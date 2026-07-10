"""EXP-023 決斶夜嚴格一致籌碼共識覆蓋（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：EXP-016 的多數決允許 2:1（一面缺資料或反向也算共識），可能混入弱樣本。改要求
chips/branch/holders 三面「全部有資料且方向完全一致」才算籌碼共識，樣本數雖降低但
確信度應更高，是否對決斶夜衝突有覆蓋力。

用法：
  python research/exp_023_unanimous_chips_decisive.py
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


def binom_p(hit: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 1.0
    lo, hi = min(hit, n - hit), max(hit, n - hit)
    tail = sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(hi, n + 1))
    return min(1.0, 2 * tail)


def unanimous_side(scores: dict) -> int:
    vals = [scores.get(d) for d in CHIP_DIMS]
    if any(v is None or v == 0 for v in vals):
        return 0
    signs = [1 if v > 0 else -1 for v in vals]
    return signs[0] if len(set(signs)) == 1 else 0


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
        sox_side = dd._sign(o)
        dec.append({"date": s["date"], "actual": s["actual"], "sox_side": sox_side,
                    "sox_hit": sox_side == s["actual"], "chips_side": unanimous_side(s["scores"])})

    n = len(dec)
    base_hit = sum(x["sox_hit"] for x in dec)
    base_rate = base_hit / n
    unanimous_n = sum(1 for x in dec if x["chips_side"] != 0)
    print(f"決斶夜基準 {base_rate:.2%}（n={n}）　三面皆有資料且一致樣本 n={unanimous_n}")

    conflict = [x for x in dec if x["chips_side"] != 0 and x["chips_side"] != x["sox_side"]]
    confirm = [x for x in dec if x["chips_side"] != 0 and x["chips_side"] == x["sox_side"]]
    print(f"嚴格一致：衝突 n={len(conflict)}　同向 n={len(confirm)}")

    if len(conflict) < 8:
        print(f"\n衝突樣本 n={len(conflict)} < 8，未達關卡②最低樣本數，REJECT。")
        return

    sox_hit = sum(x["sox_hit"] for x in conflict)
    chip_hit = sum(1 for x in conflict if x["chips_side"] == x["actual"])
    override_hit = base_hit - sox_hit + chip_hit
    override_rate = override_hit / n
    print(f"\n衝突日跟SOX={sox_hit/len(conflict):.2%}　跟籌碼={chip_hit/len(conflict):.2%}"
          f"(p={binom_p(chip_hit, len(conflict)):.3f})")
    print(f"覆蓋後全日={override_rate:.2%}（Δ={override_rate-base_rate:+.1%}）")

    if confirm:
        c_hit = sum(x["sox_hit"] for x in confirm)
        print(f"\n（附帶）同向日跟SOX命中率={c_hit/len(confirm):.2%}（n={len(confirm)}）")


if __name__ == "__main__":
    main()
