"""EXP-017 決斷夜單一籌碼子面覆蓋（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：EXP-016 三面多數決在衝突日反而是反指標（35.82%）。改分別測 chips/branch/holders
單一子面（僅用 <D 已知資料）：與 SOX 衝突時，是否有某一面單獨命中率優於 SOX（64.18%），
可作局部覆蓋來源，提升決斷夜全日命中率。

用法：
  python research/exp_017_single_dim_decisive.py
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
                    "sox_hit": sox_side == s["actual"], "scores": s["scores"]})

    n = len(dec)
    base_hit = sum(x["sox_hit"] for x in dec)
    base_rate = base_hit / n
    print(f"決斷夜基準（純跟SOX）：{base_rate:.2%}（n={n}）")

    for dim in CHIP_DIMS:
        conflict = [x for x in dec if x["scores"].get(dim) and
                   (1 if x["scores"][dim] > 0 else -1) != x["sox_side"]]
        if not conflict:
            print(f"\n[{dim}] 無衝突樣本")
            continue
        sox_hit = sum(x["sox_hit"] for x in conflict)
        sox_rate = sox_hit / len(conflict)
        dim_hit = sum(1 for x in conflict
                     if (1 if x["scores"][dim] > 0 else -1) == x["actual"])
        dim_rate = dim_hit / len(conflict)
        p_dim = binom_p(dim_hit, len(conflict))
        override_hit = base_hit - sox_hit + dim_hit
        override_rate = override_hit / n
        print(f"\n[{dim}] 衝突日 n={len(conflict)}　跟SOX={sox_rate:.2%}　跟{dim}={dim_rate:.2%}(p={p_dim:.3f})"
              f"　覆蓋後全日={override_rate:.2%}（Δ={override_rate - base_rate:+.1%}）")


if __name__ == "__main__":
    main()
