"""EXP-016 決斷夜籌碼共識覆蓋（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：決斷夜（|昨晚SOX|>=1%，現行規則單純 sign(SOX) 決定全日方向，同日全日命中率基準 70.7%）
中，當「籌碼三面」（chips/branch/holders，僅用 <D 已知資料）方向共識與 SOX 方向衝突時，
改以籌碼共識覆蓋跟隔夜規則，可提升決斷夜全日命中率。

用法：
  python research/exp_016_chips_consensus_decisive.py
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


def chips_consensus(scores: dict) -> int:
    """三面（皆 <D 已知）簡單多數決；平手或無資料回傳 0（無共識）。"""
    signs = [1 if scores[d] > 0 else -1 for d in CHIP_DIMS if scores.get(d)]
    if not signs:
        return 0
    pos, neg = signs.count(1), signs.count(-1)
    if pos == neg:
        return 0
    return 1 if pos > neg else -1


def main() -> None:
    tdb.init_db()
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, START, config.today_str()[:4] + "-12-31", dd.NEUTRAL)
        samples = bt.score_samples(feats, scoring.PARAMS)
        ov = {}
        for f in feats:
            us = tdb.us_asof(conn, dd.OVERNIGHT_KEY, f["date"])
            ov[f["date"]] = us["change_pct"] if us and us.get("change_pct") is not None else None

    dec = []  # 決斷夜、方向性有效日
    for s in samples:
        if s["actual"] == 0:
            continue
        o = ov.get(s["date"])
        if o is None or abs(o) < dd.DECISIVE_THR:
            continue
        sox_side = dd._sign(o)
        chips_side = chips_consensus(s["scores"])
        dec.append({"date": s["date"], "actual": s["actual"], "sox_side": sox_side,
                    "chips_side": chips_side,
                    "sox_hit": sox_side == s["actual"],
                    "conflict": chips_side != 0 and chips_side != sox_side})

    n = len(dec)
    base_hit = sum(x["sox_hit"] for x in dec)
    base_rate = base_hit / n if n else 0.0
    print(f"決斷夜基準（純跟SOX）：{base_rate:.2%}（n={n}, hit={base_hit}）")

    conflict = [x for x in dec if x["conflict"]]
    confirm = [x for x in dec if x["chips_side"] != 0 and not x["conflict"]]
    no_chip = [x for x in dec if x["chips_side"] == 0]
    print(f"\n分層：衝突(籌碼≠SOX) n={len(conflict)}　同向(籌碼=SOX) n={len(confirm)}　無籌碼共識 n={len(no_chip)}")

    for name, sub in (("衝突日", conflict), ("同向日", confirm), ("無共識日", no_chip)):
        if not sub:
            print(f"  {name:8s} n=0")
            continue
        sox_hit = sum(x["sox_hit"] for x in sub)
        sox_rate = sox_hit / len(sub)
        p = binom_p(sox_hit, len(sub))
        print(f"  {name:8s} n={len(sub):3d}  跟SOX命中率={sox_rate:.2%}  p={p:.3f}")

    if conflict:
        chip_hit = sum(1 for x in conflict if x["chips_side"] == x["actual"])
        chip_rate = chip_hit / len(conflict)
        p_chip = binom_p(chip_hit, len(conflict))
        print(f"\n衝突日改跟籌碼共識命中率={chip_rate:.2%}（n={len(conflict)}, p={p_chip:.3f}）"
              f"　vs 原跟SOX={sum(x['sox_hit'] for x in conflict)/len(conflict):.2%}")

        # --- 覆蓋規則後的決斷夜整體命中率 ---
        override_hit = base_hit - sum(x["sox_hit"] for x in conflict) + chip_hit
        override_rate = override_hit / n
        print(f"\n覆蓋規則後決斷夜全日命中率：{override_rate:.2%}（原 {base_rate:.2%}，"
              f"Δ={override_rate - base_rate:+.1%}）")
    else:
        print("\n無衝突日樣本，無法評估覆蓋規則。")


if __name__ == "__main__":
    main()
