"""EXP-003 USDTWD 匯率 — 研究腳本（非 production，禁止被 daily path import）

假設：台幣急貶（USDTWD 上升，D-1 與 5 日趨勢）＝外資撤出台股前兆 → 2344 次日偏弱；
台幣升值 → 偏多。提供獨立於 SOX 的資金流 regime 訊號。
Yahoo TWD=X 日線以美東標日：D-1 匯率 bar 於 D 日 05:00 TST 收 → 06:00 已知，as-of `date < D`。

用法：
  python research/exp_003_usdtwd.py --start 2025-07-01
  python research/exp_003_usdtwd.py --start 2024-08-01 --end 2025-06-30   # 早一年（跨 regime）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import json  # noqa: E402

import backtest as bt  # noqa: E402
import config  # noqa: E402
import fetch_us  # noqa: E402
import scoring  # noqa: E402
import timeline_db as tdb  # noqa: E402
import xs_db  # noqa: E402

NEUTRAL_TOL = 1.0
DECISIVE_THR = 1.0
TWD_SCALE = 1.0  # twd_5d(%) → [-1,1]，貶值為負分（偏空）


def build_days(start: str, end: str) -> list[dict]:
    """2344 收盤取自 xs.db（488 日，跨 regime）；SOX 與 TWD=X 取 Yahoo 2y，as-of `date < D`。"""
    with xs_db.connect() as conn:
        rows = conn.execute(
            "SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
            (config.SYMBOL,),
        ).fetchall()
    candles = [{"date": r["date"], "close": r["close"]} for r in rows if r["close"]]

    sox_rows = [r for r in fetch_us.fetch_yahoo_daily("^SOX", "2y") if r["change_pct"] is not None]
    twd_rows = [r for r in fetch_us.fetch_yahoo_daily("TWD=X", "2y") if r["change_pct"] is not None]
    sox_dates = [r["date"] for r in sox_rows]
    twd_dates = [r["date"] for r in twd_rows]

    days = []
    for i in range(1, len(candles)):
        d = candles[i]["date"]
        if not (start <= d <= end):
            continue
        prev_close = candles[i - 1]["close"]
        pct = (candles[i]["close"] - prev_close) / prev_close * 100
        actual = 1 if pct > NEUTRAL_TOL else (-1 if pct < -NEUTRAL_TOL else 0)

        j = len([k for k in sox_dates if k < d])
        sox_chg = sox_rows[j - 1]["change_pct"] if j else None
        assert j == 0 or sox_dates[j - 1] < d, "look-ahead: sox"

        k = len([x for x in twd_dates if x < d])
        assert k == 0 or twd_dates[k - 1] < d, "look-ahead: twd"
        twd_1d = twd_rows[k - 1]["change_pct"] if k else None
        twd_5d = (sum(r["change_pct"] for r in twd_rows[max(0, k - 5):k]) if k >= 5 else None)

        days.append({"date": d, "actual": actual, "pct": pct, "sox": sox_chg,
                     "twd_1d": twd_1d, "twd_5d": twd_5d})
    return days


def _hit(days: list[dict], key: str) -> dict:
    """訊號＝sign(−twd)：台幣貶（twd>0）→ 偏空；升 → 偏多。"""
    active = hit = long_hit = 0
    for s in days:
        v = s[key]
        if v is None or v == 0 or s["actual"] == 0:
            continue
        active += 1
        if (-1 if v > 0 else 1) == s["actual"]:
            hit += 1
        if s["actual"] == 1:
            long_hit += 1
    rate = hit / active if active else 0.0
    p = bt._binom_two_sided_p(hit, active, 0.5) if active else 1.0
    return {"active": active, "hit_rate": rate, "p": p,
            "long_base": long_hit / active if active else 0.0}


def run(start: str, end: str) -> None:
    days = build_days(start, end)
    n = len(days)
    cov = sum(1 for s in days if s["twd_5d"] is not None)
    print(f"視窗 {start}~{end}  n={n}  TWD 覆蓋 {cov}/{n}")

    cut = int(n * 0.7)
    for key in ("twd_1d", "twd_5d"):
        print(f"\n── 訊號 {key}（貶→空 / 升→多）──")
        for name, seg in (("全窗", days), ("in-sample(70%)", days[:cut]), ("OOS(30%)", days[cut:])):
            st = _hit(seg, key)
            print(f"  {name:14s} 命中 {st['hit_rate']:.1%} (active={st['active']}, p={st['p']:.3f}, "
                  f"偏多基準 {st['long_base']:.1%})")
        dec = [s for s in days if s["sox"] is not None and abs(s["sox"]) >= DECISIVE_THR]
        flat = [s for s in days if s["sox"] is not None and abs(s["sox"]) < DECISIVE_THR]
        sd, sf = _hit(dec, key), _hit(flat, key)
        print(f"  決斷夜        命中 {sd['hit_rate']:.1%} (active={sd['active']})  "
              f"平淡夜 命中 {sf['hit_rate']:.1%} (active={sf['active']})  ← SOX 影子檢驗")

    # Overlay 診斷：SOX>0 但台幣 5 日急貶 → 上漲率變化
    pool = [s for s in days if s["sox"] is not None and s["actual"] != 0 and s["twd_5d"] is not None]
    up_pool = [s for s in pool if s["sox"] > 0]
    base_up = sum(1 for s in up_pool if s["actual"] == 1) / len(up_pool) if up_pool else 0.0
    print(f"\nOverlay 診斷（SOX>0 且 actual≠0，n={len(up_pool)}，上漲率 {base_up:.1%}）：")
    for thr in (0.5, 1.0, 1.5):
        sub = [s for s in up_pool if s["twd_5d"] >= thr]
        up = sum(1 for s in sub if s["actual"] == 1) / len(sub) if sub else 0.0
        print(f"  twd_5d ≥ +{thr:.1f}%（急貶）：n={len(sub)}  上漲率 {up:.1%}（Δ {up - base_up:+.1%}）")
    dn_pool = [s for s in pool if s["sox"] < 0]
    base_dn = sum(1 for s in dn_pool if s["actual"] == -1) / len(dn_pool) if dn_pool else 0.0
    print(f"Overlay 診斷（SOX<0 且 actual≠0，n={len(dn_pool)}，下跌率 {base_dn:.1%}）：")
    for thr in (-0.5, -1.0, -1.5):
        sub = [s for s in dn_pool if s["twd_5d"] <= thr]
        dn = sum(1 for s in sub if s["actual"] == -1) / len(sub) if sub else 0.0
        print(f"  twd_5d ≤ {thr:.1f}%（急升）：n={len(sub)}  下跌率 {dn:.1%}（Δ {dn - base_dn:+.1%}）")


def integrate(start: str, end: str) -> None:
    """邊際整合測試（不動 production 檔案）：production 樣本（market.db＋校準參數＋採用權重）
    上，凍結十面相對權重、混入 twd 維度權重 wt，比較全模型 win_rate / OOS 變化。
    scoring.DIMENSIONS 只在本行程內延伸，combine() 覆蓋率正規化自動涵蓋缺值日。"""
    with tdb.connect() as conn:
        samples, _ = bt.build_samples(conn, config.SYMBOL, start, end, NEUTRAL_TOL)
    twd_rows = [r for r in fetch_us.fetch_yahoo_daily("TWD=X", "2y") if r["change_pct"] is not None]
    twd_dates = [r["date"] for r in twd_rows]
    scoring.DIMENSIONS = tuple(scoring.DIMENSIONS) + ("twd",)  # in-process only
    for s in samples:
        k = len([x for x in twd_dates if x < s["date"]])
        assert k == 0 or twd_dates[k - 1] < s["date"], "look-ahead: twd"
        twd5 = sum(r["change_pct"] for r in twd_rows[max(0, k - 5):k]) if k >= 5 else None
        s["scores"]["twd"] = scoring.clamp(-twd5 / TWD_SCALE) if twd5 is not None else None

    wj = json.loads(config.weights_path().read_text(encoding="utf-8"))
    base_w, tau = wj["weights"], wj.get("neutral_threshold", 0.05)
    cut = int(len(samples) * 0.7)

    def ev(seg, wt):
        w = {d: v * (1 - wt) for d, v in base_w.items()}
        w["twd"] = wt
        return bt.evaluate(seg, w, tau)

    print(f"整合測試視窗 {start}~{end}  n={len(samples)}  tau={tau}  （production 樣本）")
    print(f"{'twd權重':>8} {'全窗':>8} {'in-sample':>10} {'OOS':>8}")
    for wt in (0.0, 0.05, 0.1, 0.2, 0.3):
        f_, i_, o_ = ev(samples, wt), ev(samples[:cut], wt), ev(samples[cut:], wt)
        print(f"{wt:>8.2f} {f_['win_rate']:>8.2%} {i_['win_rate']:>10.2%} {o_['win_rate']:>8.2%}")


if __name__ == "__main__":
    args = sys.argv[1:]
    def opt(flag, default):
        return args[args.index(flag) + 1] if flag in args else default
    if "--integrate" in args:
        integrate(opt("--start", "2025-07-01"), opt("--end", "2099-12-31"))
    else:
        run(opt("--start", "2025-07-01"), opt("--end", "2099-12-31"))
