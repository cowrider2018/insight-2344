"""EXP-019 決斷夜高強度籌碼分歧覆蓋（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：EXP-016/017/018 的「衝突日」定義只看正負號，混入大量近零雜訊訊號稀釋效果。
改只在籌碼三面平均訊號強度極高（|avg chip score|>=門檻）時才視為「高強度分歧」並覆蓋，
過濾雜訊後，剩餘的高確信分歧樣本是否對 SOX 有覆蓋優勢。

用法：
  python research/exp_019_high_conviction_chips.py
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
THRESHOLDS = (0.2, 0.35, 0.5)


def binom_p(hit: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 1.0
    lo, hi = min(hit, n - hit), max(hit, n - hit)
    tail = sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(hi, n + 1))
    return min(1.0, 2 * tail)


def chips_avg(scores: dict) -> float | None:
    vals = [scores[d] for d in CHIP_DIMS if scores.get(d) is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


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
        dec.append({"date": s["date"], "actual": s["actual"], "sox_side": sox_side,
                    "sox_hit": sox_side == s["actual"], "chips_avg": avg})

    n = len(dec)
    base_hit = sum(x["sox_hit"] for x in dec)
    base_rate = base_hit / n
    print(f"決斷夜基準 {base_rate:.2%}（n={n}）")

    for th in THRESHOLDS:
        conflict = [x for x in dec if x["chips_avg"] is not None
                   and abs(x["chips_avg"]) >= th
                   and (1 if x["chips_avg"] > 0 else -1) != x["sox_side"]]
        if not conflict:
            print(f"\n門檻|avg|>={th}：無高強度分歧樣本")
            continue
        sox_hit = sum(x["sox_hit"] for x in conflict)
        chip_hit = sum(1 for x in conflict
                       if (1 if x["chips_avg"] > 0 else -1) == x["actual"])
        override_hit = base_hit - sox_hit + chip_hit
        override_rate = override_hit / n
        print(f"\n門檻|avg|>={th}：高強度分歧 n={len(conflict)}　"
              f"跟SOX={sox_hit/len(conflict):.2%}　跟籌碼={chip_hit/len(conflict):.2%}"
              f"(p={binom_p(chip_hit, len(conflict)):.3f})　"
              f"覆蓋後全日={override_rate:.2%}（Δ={override_rate - base_rate:+.1%}）")


if __name__ == "__main__":
    main()
