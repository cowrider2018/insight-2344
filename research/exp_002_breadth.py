"""EXP-002 台股大盤 breadth — 研究腳本（非 production，禁止被 daily path import）

假設：全市場（每日成交量前 300 檔）上漲家數比（breadth）於 D-1／近 3 日惡化，
代表資金參與度收縮，2344 次日偏弱 —— 提供「獨立於 SOX」的市場內部訊號。

用法：
  python research/exp_002_breadth.py            # 回測（預設 2025-07-01 起）
  python research/exp_002_breadth.py --start 2024-08-01   # 拉長至近 2 年（跨 regime）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import backtest as bt  # noqa: E402
import config  # noqa: E402
import fetch_us  # noqa: E402
import xs_db  # noqa: E402

NEUTRAL_TOL = 1.0
DECISIVE_THR = 1.0
TOP_N = 300


def load_breadth() -> dict[str, float]:
    """{date: 上漲家數比}（每日成交量前 TOP_N 檔、以各檔前一 xs 日收盤算漲跌）。"""
    with xs_db.connect() as conn:
        rows = conn.execute(
            "SELECT symbol, date, close, volume FROM xs_candles ORDER BY symbol, date"
        ).fetchall()
    # 每檔依日期序算日報酬
    ret: dict[str, dict[str, float]] = {}   # date -> {symbol: ret}
    vol: dict[str, dict[str, float]] = {}   # date -> {symbol: volume}
    prev_sym, prev_close = None, None
    for r in rows:
        s, d, c, v = r["symbol"], r["date"], r["close"], r["volume"]
        if c is None:
            prev_sym, prev_close = s, None
            continue
        if s == prev_sym and prev_close:
            ret.setdefault(d, {})[s] = (c - prev_close) / prev_close
        vol.setdefault(d, {})[s] = v or 0.0
        prev_sym, prev_close = s, c
    breadth: dict[str, float] = {}
    for d, rmap in ret.items():
        top = sorted(rmap, key=lambda s: vol[d].get(s, 0.0), reverse=True)[:TOP_N]
        if len(top) >= 100:
            breadth[d] = sum(1 for s in top if rmap[s] > 0) / len(top)
    return breadth


def build_days(breadth: dict[str, float], start: str, end: str) -> list[dict]:
    """交易日曆與 2344 收盤取自 xs.db（488 日 > market.db 的 ~250 日，跨 regime）；
    SOX 直抓 Yahoo 2 年（免金鑰），as-of `date < D`。"""
    bdates = sorted(breadth)
    with xs_db.connect() as conn:
        rows = conn.execute(
            "SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
            (config.SYMBOL,),
        ).fetchall()
    candles = [{"date": r["date"], "close": r["close"]} for r in rows if r["close"]]
    sox_rows = [r for r in fetch_us.fetch_yahoo_daily("^SOX", "2y") if r["change_pct"] is not None]
    sox_dates = [r["date"] for r in sox_rows]
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
        past = [k for k in bdates if k < d]
        assert not past or past[-1] < d, "look-ahead: breadth"
        b1 = breadth[past[-1]] if past else None
        b3 = (sum(breadth[k] for k in past[-3:]) / len(past[-3:])) if past else None
        days.append({"date": d, "actual": actual, "pct": pct, "sox": sox_chg,
                     "b1": b1, "b3": b3})
    return days


def _sig(v: float | None) -> int | None:
    if v is None:
        return None
    return 1 if v > 0.5 else (-1 if v < 0.5 else 0)


def _hit(days: list[dict], key: str) -> dict:
    active = hit = long_hit = 0
    for s in days:
        v = _sig(s[key])
        if v is None or v == 0 or s["actual"] == 0:
            continue
        active += 1
        if v == s["actual"]:
            hit += 1
        if s["actual"] == 1:
            long_hit += 1
    rate = hit / active if active else 0.0
    p = bt._binom_two_sided_p(hit, active, 0.5) if active else 1.0
    return {"active": active, "hit_rate": rate, "p": p,
            "long_base": long_hit / active if active else 0.0}


def run(start: str, end: str) -> None:
    breadth = load_breadth()
    days = build_days(breadth, start, end)
    n = len(days)
    cov = sum(1 for s in days if s["b1"] is not None)
    print(f"視窗 {start}~{end}  n={n}  breadth 覆蓋 {cov}/{n}  TOP_N={TOP_N}")

    cut = int(n * 0.7)
    for key in ("b1", "b3"):
        print(f"\n── 訊號 {key}（>0.5 偏多 / <0.5 偏空）──")
        for name, seg in (("全窗", days), ("in-sample(70%)", days[:cut]), ("OOS(30%)", days[cut:])):
            st = _hit(seg, key)
            print(f"  {name:14s} 命中 {st['hit_rate']:.1%} (active={st['active']}, p={st['p']:.3f}, "
                  f"偏多基準 {st['long_base']:.1%})")
        dec = [s for s in days if s["sox"] is not None and abs(s["sox"]) >= DECISIVE_THR]
        flat = [s for s in days if s["sox"] is not None and abs(s["sox"]) < DECISIVE_THR]
        sd, sf = _hit(dec, key), _hit(flat, key)
        print(f"  決斷夜        命中 {sd['hit_rate']:.1%} (active={sd['active']})  "
              f"平淡夜 命中 {sf['hit_rate']:.1%} (active={sf['active']})  ← SOX 影子檢驗")

    # Overlay 診斷：SOX>0 但 breadth 弱 → 上漲率變化；SOX<0 但 breadth 強 → 下跌緩解
    pool = [s for s in days if s["sox"] is not None and s["actual"] != 0 and s["b3"] is not None]
    up_pool = [s for s in pool if s["sox"] > 0]
    base_up = sum(1 for s in up_pool if s["actual"] == 1) / len(up_pool) if up_pool else 0.0
    print(f"\nOverlay 診斷（SOX>0 且 actual≠0，n={len(up_pool)}，上漲率 {base_up:.1%}）：")
    for thr in (0.45, 0.40, 0.35):
        sub = [s for s in up_pool if s["b3"] <= thr]
        up = sum(1 for s in sub if s["actual"] == 1) / len(sub) if sub else 0.0
        print(f"  b3 ≤ {thr:.2f}：n={len(sub)}  上漲率 {up:.1%}（Δ {up - base_up:+.1%}）")
    dn_pool = [s for s in pool if s["sox"] < 0]
    base_dn = sum(1 for s in dn_pool if s["actual"] == -1) / len(dn_pool) if dn_pool else 0.0
    print(f"Overlay 診斷（SOX<0 且 actual≠0，n={len(dn_pool)}，下跌率 {base_dn:.1%}）：")
    for thr in (0.55, 0.60, 0.65):
        sub = [s for s in dn_pool if s["b3"] >= thr]
        dn = sum(1 for s in sub if s["actual"] == -1) / len(sub) if sub else 0.0
        print(f"  b3 ≥ {thr:.2f}：n={len(sub)}  下跌率 {dn:.1%}（Δ {dn - base_dn:+.1%}）")


if __name__ == "__main__":
    args = sys.argv[1:]
    def opt(flag, default):
        return args[args.index(flag) + 1] if flag in args else default
    run(opt("--start", "2025-07-01"), opt("--end", "2099-12-31"))
