"""EXP-024 決斷夜 SOX磁點×籌碼強度 2D 交互覆蓋（推理流程 overlay）— 研究腳本
（非 production，禁止被 daily path import）

假設：EXP-018（SOX磁點分層）、EXP-019（籌碼強度門檻）單軸皆 REJECT，但交互作用
（例如「弱SOX磁點 且 強籌碼分歧」象限）可能存在單軸掃描看不到的局部訊號。
2x2 網格：SOX磁點 弱[1,2)/強[2,+) × 籌碼共識強度(|avg|) 弱[0,0.3)/強[0.3,+)，
只在「籌碼與SOX方向衝突」子集內分四象限，逐一檢驗跟SOX命中率是否顯著跌破50%。

用法：
  python research/exp_024_2d_grid_decisive.py
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
MIN_ACTIVE = 8


def binom_p(hit: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 1.0
    lo, hi = min(hit, n - hit), max(hit, n - hit)
    tail = sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(hi, n + 1))
    return min(1.0, 2 * tail)


def chips_avg(scores: dict) -> float | None:
    vals = [scores[d] for d in CHIP_DIMS if scores.get(d) is not None]
    return sum(vals) / len(vals) if vals else None


def main() -> None:
    tdb.init_db()
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, START, config.today_str()[:4] + "-12-31", dd.NEUTRAL)
        samples = bt.score_samples(feats, scoring.PARAMS)
        ov = {}
        for f in feats:
            us = tdb.us_asof(conn, dd.OVERNIGHT_KEY, f["date"])
            ov[f["date"]] = us["change_pct"] if us and us.get("change_pct") is not None else None

    dec = []
    for s in samples:
        if s["actual"] == 0:
            continue
        o = ov.get(s["date"])
        if o is None or abs(o) < dd.DECISIVE_THR:
            continue
        avg = chips_avg(s["scores"])
        sox_side = dd._sign(o)
        dec.append({"actual": s["actual"], "sox_abs": abs(o), "sox_side": sox_side,
                    "sox_hit": sox_side == s["actual"], "chips_avg": avg})

    conflict = [x for x in dec if x["chips_avg"] is not None and x["chips_avg"] != 0
               and (1 if x["chips_avg"] > 0 else -1) != x["sox_side"]]
    base_hit = sum(x["sox_hit"] for x in dec)
    base_rate = base_hit / len(dec)
    print(f"決斷夜基準 {base_rate:.2%}（n={len(dec)}）　全部衝突樣本 n={len(conflict)}")

    sox_bins = (("弱SOX[1,2)", lambda a: 1.0 <= a < 2.0), ("強SOX[2,+)", lambda a: a >= 2.0))
    chip_bins = (("弱籌碼[0,.3)", lambda a: abs(a) < 0.3), ("強籌碼[.3,+)", lambda a: abs(a) >= 0.3))

    for sname, scond in sox_bins:
        for cname, ccond in chip_bins:
            cell = [x for x in conflict if scond(x["sox_abs"]) and ccond(x["chips_avg"])]
            if len(cell) < MIN_ACTIVE:
                print(f"  [{sname} x {cname}] n={len(cell):3d}（<{MIN_ACTIVE}，跳過）")
                continue
            hit = sum(x["sox_hit"] for x in cell)
            rate = hit / len(cell)
            p = binom_p(hit, len(cell))
            flag = " <== SOX<50% 且 p<0.15" if rate < 0.5 and p < 0.15 else ""
            print(f"  [{sname} x {cname}] n={len(cell):3d}  跟SOX={rate:.2%}  p={p:.3f}{flag}")


if __name__ == "__main__":
    main()
