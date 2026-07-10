"""EXP-018 決斷夜弱磁點分層籌碼覆蓋（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：EXP-017 顯示衝突日整體跟籌碼是 SOX 的鏡像互補、無獨立資訊。但 EXP-010 已知
|SOX|>=3% 決斷夜特別可靠，反向推論 1<=|SOX|<2%（弱磁點）跟SOX命中率應較低——
此弱磁點窄帶內，籌碼三面共識是否對「跟SOX」有局部覆蓋優勢（強磁點 |SOX|>=2% 維持現行
跟隔夜規則不動，只測窄帶內的覆蓋可能）。

用法：
  python research/exp_018_weak_sox_band_chips.py
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
    signs = [1 if scores[d] > 0 else -1 for d in CHIP_DIMS if scores.get(d)]
    if not signs:
        return 0
    pos, neg = signs.count(1), signs.count(-1)
    return 0 if pos == neg else (1 if pos > neg else -1)


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
        dec.append({"date": s["date"], "actual": s["actual"], "sox_abs": abs(o),
                    "sox_side": sox_side, "sox_hit": sox_side == s["actual"],
                    "chips_side": chips_consensus(s["scores"])})

    weak = [x for x in dec if 1.0 <= x["sox_abs"] < 2.0]
    strong = [x for x in dec if x["sox_abs"] >= 2.0]
    print(f"決斶夜基準 n={len(dec)}　弱磁點[1,2) n={len(weak)}　強磁點[2,+) n={len(strong)}")

    for name, sub in (("弱磁點", weak), ("強磁點", strong)):
        hit = sum(x["sox_hit"] for x in sub)
        rate = hit / len(sub) if sub else 0.0
        print(f"  {name} 跟SOX命中率={rate:.2%}（n={len(sub)}, p={binom_p(hit, len(sub)):.3f}）")

    conflict = [x for x in weak if x["chips_side"] != 0 and x["chips_side"] != x["sox_side"]]
    if not conflict:
        print("\n弱磁點內無籌碼衝突樣本，無法評估覆蓋。")
        return
    sox_hit = sum(x["sox_hit"] for x in conflict)
    chip_hit = sum(1 for x in conflict if x["chips_side"] == x["actual"])
    print(f"\n弱磁點內衝突日 n={len(conflict)}　跟SOX={sox_hit/len(conflict):.2%}　"
          f"跟籌碼={chip_hit/len(conflict):.2%}（p={binom_p(chip_hit, len(conflict)):.3f}）")

    base_hit = sum(x["sox_hit"] for x in dec)
    override_hit = base_hit - sox_hit + chip_hit
    override_rate = override_hit / len(dec)
    base_rate = base_hit / len(dec)
    print(f"\n僅弱磁點衝突日覆蓋後 決斷夜全日命中率={override_rate:.2%}"
          f"（原 {base_rate:.2%}，Δ={override_rate - base_rate:+.1%}）")


if __name__ == "__main__":
    main()
