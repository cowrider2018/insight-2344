"""EXP-025 決斷夜籌碼5日滾動趨勢覆蓋（推理流程 overlay，本系列最後一輪）— 研究腳本
（非 production，禁止被 daily path import）

假設：EXP-016(單日水位)、EXP-021(單日delta) 皆 REJECT。改用機構週度部位累積訊號——
籌碼三面過去 5 個「有資料」交易日的滾動平均（僅用 <D 已知歷史），平滑單日雜訊，
是否對決斶夜衝突有覆蓋力。

用法：
  python research/exp_025_5d_trend_decisive.py
"""
from __future__ import annotations

import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import backtest as bt  # noqa: E402
import config  # noqa: E402
import daily_decision as dd  # noqa: E402
import scoring  # noqa: E402
import timeline_db as tdb  # noqa: E402

START = "2025-07-01"
CHIP_DIMS = ("chips", "branch", "holders")
WINDOW = 5
MIN_ACTIVE = 8


def binom_p(hit: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 1.0
    lo, hi = min(hit, n - hit), max(hit, n - hit)
    tail = sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(hi, n + 1))
    return min(1.0, 2 * tail)


def main() -> None:
    tdb.init_db()
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, START, config.today_str()[:4] + "-12-31", dd.NEUTRAL)
        samples = bt.score_samples(feats, scoring.PARAMS)
        ov = {}
        for f in feats:
            us = tdb.us_asof(conn, dd.OVERNIGHT_KEY, f["date"])
            ov[f["date"]] = us["change_pct"] if us and us.get("change_pct") is not None else None

    # 逐面累積歷史（僅用嚴格早於當日的樣本），算 5 日滾動平均
    hist = {d: [] for d in CHIP_DIMS}
    rows = []
    for s in samples:
        trend = {}
        for d in CHIP_DIMS:
            if len(hist[d]) >= WINDOW:
                trend[d] = sum(hist[d][-WINDOW:]) / WINDOW
            v = s["scores"].get(d)
            if v is not None:
                hist[d].append(v)
        rows.append({"date": s["date"], "actual": s["actual"], "trend": trend})

    dec = []
    for s, r in zip(samples, rows):
        if s["actual"] == 0:
            continue
        o = ov.get(s["date"])
        if o is None or abs(o) < dd.DECISIVE_THR:
            continue
        dec.append({"actual": s["actual"], "sox_side": dd._sign(o),
                    "sox_hit": dd._sign(o) == s["actual"], "trend": r["trend"]})

    n = len(dec)
    base_hit = sum(x["sox_hit"] for x in dec)
    base_rate = base_hit / n
    print(f"決斶夜基準 {base_rate:.2%}（n={n}）")

    for dim in CHIP_DIMS:
        conflict = [x for x in dec if x["trend"].get(dim) not in (None, 0)
                   and (1 if x["trend"][dim] > 0 else -1) != x["sox_side"]]
        if len(conflict) < MIN_ACTIVE:
            print(f"  {dim:10s} n={len(conflict):3d}（<{MIN_ACTIVE}，跳過）")
            continue
        sox_hit = sum(x["sox_hit"] for x in conflict)
        trend_hit = sum(1 for x in conflict
                        if (1 if x["trend"][dim] > 0 else -1) == x["actual"])
        override_hit = base_hit - sox_hit + trend_hit
        override_rate = override_hit / n
        p = binom_p(trend_hit, len(conflict))
        flag = " <== p<0.15" if p < 0.15 and trend_hit / len(conflict) > 0.5 else ""
        print(f"  {dim:10s}5日趨勢 n={len(conflict):3d}  跟SOX={sox_hit/len(conflict):.2%}  "
              f"跟趨勢={trend_hit/len(conflict):.2%}(p={p:.3f})  "
              f"覆蓋後全日={override_rate:.2%}(Δ={override_rate-base_rate:+.1%}){flag}")


if __name__ == "__main__":
    main()
