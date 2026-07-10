"""EXP-001 韓國記憶體同業籃 — 研究腳本（非 production，禁止被 daily path import）

假設：海力士(000660.KS)＋三星(005930.KS) 等權報酬相對 KOSPI(^KS11) 的 D-1 強弱
（記憶體族群專屬資金流）對 2344 次日方向有「獨立於 SOX」的預測力。
韓股 15:30 KST（14:30 TST）收盤，D 日 06:00 已知 → as-of 一律 kr_date < D。

用法：
  python research/exp_001_kr_memory.py --probe            # Stage 2 資料可得性探針
  python research/exp_001_kr_memory.py                    # 回測（預設 2025-07-01 起）
  python research/exp_001_kr_memory.py --refresh          # 忽略快取重抓
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

import backtest as bt  # noqa: E402  （只用 _binom_two_sided_p，不改任何東西）
import config  # noqa: E402
import fetch_us  # noqa: E402
import timeline_db as tdb  # noqa: E402

SYMS = {"hynix": "000660.KS", "samsung": "005930.KS", "kospi": "^KS11"}
CACHE = config.DATA_DIR / "exp001_kr_cache.json"  # data/ 已 gitignore
NEUTRAL_TOL = 1.0
DECISIVE_THR = 1.0
STALE_DAYS = 4  # KR 最新收盤距 D 超過此日曆天數 → 視為資料過舊，當日不出訊號


def fetch_kr_daily(yahoo_sym: str, range_: str = "2y") -> list[dict]:
    """同 fetch_us.fetch_yahoo_daily，但以交易所時區（meta）標日期，避免 KR 標成美東日期。"""
    r = requests.get(
        fetch_us.YAHOO.format(sym=yahoo_sym),
        params={"range": range_, "interval": "1d"},
        headers={"User-Agent": config.USER_AGENT},
        timeout=20,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    tz = ZoneInfo(res["meta"].get("exchangeTimezoneName", "Asia/Seoul"))
    ts = res.get("timestamp") or []
    closes = res["indicators"]["quote"][0].get("close") or []
    rows, prev = [], None
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=tz).date().isoformat()
        chg = round((c - prev) / prev * 100, 4) if prev else None
        rows.append({"date": d, "close": round(float(c), 2), "change_pct": chg})
        prev = c
    return rows


def load_kr(refresh: bool = False) -> dict[str, dict[str, float]]:
    """回傳 {key: {date: change_pct}}；三檔皆有值的日期才可用。"""
    if CACHE.exists() and not refresh:
        raw = json.loads(CACHE.read_text(encoding="utf-8"))
    else:
        raw = {k: fetch_kr_daily(s) for k, s in SYMS.items()}
        CACHE.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return {k: {r["date"]: r["change_pct"] for r in rows if r["change_pct"] is not None}
            for k, rows in raw.items()}


def probe() -> None:
    for key, sym in SYMS.items():
        rows = fetch_kr_daily(sym)
        ok = [r for r in rows if r["change_pct"] is not None]
        print(f"[probe] {key:8s} {sym:10s} n={len(ok)}  {ok[0]['date']} ~ {ok[-1]['date']}  "
              f"末筆 close={ok[-1]['close']} chg={ok[-1]['change_pct']}%")
    print("[probe] 公布時點：KR 收盤 14:30 TST（D-1）→ 06:00 可得，as-of 用 kr_date < D")


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs) ** 0.5
    vy = sum((b - my) ** 2 for b in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else float("nan")


def _hit_stats(days: list[dict], sig_key: str) -> dict:
    """dim_significant 同口徑：訊號非 0 且 actual 非 0 的日子。"""
    active = hit = long_hit = 0
    for s in days:
        v = s[sig_key]
        if v is None or v == 0 or s["actual"] == 0:
            continue
        active += 1
        if (1 if v > 0 else -1) == s["actual"]:
            hit += 1
        if s["actual"] == 1:
            long_hit += 1
    rate = hit / active if active else 0.0
    p = bt._binom_two_sided_p(hit, active, 0.5) if active else 1.0
    return {"active": active, "hit_rate": rate, "p": p,
            "long_base": long_hit / active if active else 0.0}


def build_days(kr: dict, start: str, end: str) -> list[dict]:
    common = sorted(set(kr["hynix"]) & set(kr["samsung"]) & set(kr["kospi"]))
    with tdb.connect() as conn:
        candles = tdb.candles_upto(conn, config.SYMBOL)
        days = []
        for i in range(1, len(candles)):
            d = candles[i]["date"]
            if not (start <= d <= end):
                continue
            prev_close = candles[i - 1]["close"]
            if not prev_close:
                continue
            pct = (candles[i]["close"] - prev_close) / prev_close * 100
            actual = 1 if pct > NEUTRAL_TOL else (-1 if pct < -NEUTRAL_TOL else 0)

            sox = tdb.us_asof(conn, "sox", d)
            assert sox is None or sox["date"] < d, "look-ahead: sox"
            sox_chg = sox["change_pct"] if sox else None

            kdates = [k for k in common if k < d]
            assert not kdates or kdates[-1] < d, "look-ahead: kr"
            rel_1d = rel_3d = abs_1d = None
            if kdates:
                last = kdates[-1]
                gap = (date.fromisoformat(d) - date.fromisoformat(last)).days
                if gap <= STALE_DAYS:
                    def rel(k):  # 記憶體雙雄等權 − 大盤
                        return (kr["hynix"][k] + kr["samsung"][k]) / 2 - kr["kospi"][k]
                    rel_1d = rel(last)
                    abs_1d = (kr["hynix"][last] + kr["samsung"][last]) / 2
                    rel_3d = sum(rel(k) for k in kdates[-3:])
            days.append({"date": d, "actual": actual, "pct": pct, "sox": sox_chg,
                         "rel_1d": rel_1d, "rel_3d": rel_3d, "abs_1d": abs_1d})
    return days


def run(start: str, end: str, refresh: bool) -> None:
    kr = load_kr(refresh)
    days = build_days(kr, start, end)
    n = len(days)
    cov = sum(1 for s in days if s["rel_1d"] is not None)
    print(f"視窗 {start}~{end}  台股交易日 n={n}  KR 覆蓋 {cov}/{n}")

    cut = int(n * 0.7)
    segs = {"全窗": days, "in-sample(70%)": days[:cut], "OOS(30%)": days[cut:]}
    for sig in ("rel_1d", "rel_3d", "abs_1d"):
        print(f"\n── 訊號 {sig}（sign → 次日方向）──")
        for name, seg in segs.items():
            st = _hit_stats(seg, sig)
            print(f"  {name:14s} 命中 {st['hit_rate']:.1%} (active={st['active']}, p={st['p']:.3f}, "
                  f"偏多基準 {st['long_base']:.1%})")
        dec = [s for s in days if s["sox"] is not None and abs(s["sox"]) >= DECISIVE_THR]
        flat = [s for s in days if s["sox"] is not None and abs(s["sox"]) < DECISIVE_THR]
        sd, sf = _hit_stats(dec, sig), _hit_stats(flat, sig)
        print(f"  決斷夜        命中 {sd['hit_rate']:.1%} (active={sd['active']})  "
              f"平淡夜 命中 {sf['hit_rate']:.1%} (active={sf['active']})  ← SOX 影子檢驗")

    # 相關性診斷
    pair = [(s["rel_1d"], s["pct"]) for s in days if s["rel_1d"] is not None]
    pair_sox = [(s["rel_1d"], s["sox"]) for s in days
                if s["rel_1d"] is not None and s["sox"] is not None]
    print(f"\ncorr(rel_1d[D-1], 2344[D]) = {_pearson([a for a, _ in pair], [b for _, b in pair]):+.3f}"
          f"   corr(rel_1d, 隔夜SOX) = {_pearson([a for a, _ in pair_sox], [b for _, b in pair_sox]):+.3f}")

    # Overlay 診斷：SOX 偏多（基線會跟多）但 KR 記憶體相對弱 → 2344 上漲率是否驟降
    base_pool = [s for s in days if s["sox"] is not None and s["sox"] > 0 and s["actual"] != 0
                 and s["rel_3d"] is not None]
    base_up = sum(1 for s in base_pool if s["actual"] == 1) / len(base_pool) if base_pool else 0.0
    print(f"\nOverlay 診斷（SOX>0 且 actual≠0，n={len(base_pool)}，上漲率 {base_up:.1%}）：")
    for thr in (-1.0, -2.0, -3.0):
        sub = [s for s in base_pool if s["rel_3d"] <= thr]
        if sub:
            up = sum(1 for s in sub if s["actual"] == 1) / len(sub)
            print(f"  rel_3d ≤ {thr:+.0f}%：n={len(sub)}  上漲率 {up:.1%}（Δ {up - base_up:+.1%}）")
        else:
            print(f"  rel_3d ≤ {thr:+.0f}%：n=0")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--probe" in args:
        probe()
        sys.exit(0)
    def opt(flag, default):
        return args[args.index(flag) + 1] if flag in args else default
    run(opt("--start", "2025-07-01"), opt("--end", "2099-12-31"), "--refresh" in args)
