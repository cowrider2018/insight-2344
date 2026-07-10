"""EXP-020 決斷夜分點/大戶子訊號覆蓋（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：EXP-016~019 用的是聚合後的 chips/branch/holders 面分數，可能被面內子訊號互相抵銷稀釋。
改直接測分點面子訊號（net/conc/smart/daytrade/longterm）與大戶面子訊號（chg1w/chg4w/retail）
各自在決斶夜與 SOX 衝突時的方向命中率，找出是否有子訊號級的獨立覆蓋來源
（daytrade/chg1w 曾在平淡夜診斷中入選 FLAT_W，值得在決斶夜衝突子集覆核）。

用法：
  python research/exp_020_subsignal_decisive.py
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

    dec = []
    for s in samples:
        if s["actual"] == 0:
            continue
        o = ov.get(s["date"])
        if o is None or abs(o) < dd.DECISIVE_THR:
            continue
        dec.append({"date": s["date"], "actual": s["actual"], "sox_side": dd._sign(o),
                    "sox_hit": dd._sign(o) == s["actual"],
                    "br": s.get("branch_subsignals", {}), "hd": s.get("hd_subsignals", {})})

    n = len(dec)
    base_hit = sum(x["sox_hit"] for x in dec)
    base_rate = base_hit / n
    print(f"決斷夜基準 {base_rate:.2%}（n={n}）")

    subsignals = [("分點:net", "br", "net"), ("分點:conc", "br", "conc"),
                  ("分點:smart", "br", "smart"), ("分點:daytrade", "br", "daytrade"),
                  ("分點:longterm", "br", "longterm"),
                  ("大戶:chg1w", "hd", "chg1w"), ("大戶:chg4w", "hd", "chg4w"),
                  ("大戶:retail", "hd", "retail")]

    for label, grp, key in subsignals:
        conflict = [x for x in dec if x[grp].get(key) is not None and x[grp][key] != 0
                   and (1 if x[grp][key] > 0 else -1) != x["sox_side"]]
        if len(conflict) < MIN_ACTIVE:
            print(f"  {label:14s} n={len(conflict):3d}（<{MIN_ACTIVE}，跳過）")
            continue
        sox_hit = sum(x["sox_hit"] for x in conflict)
        sub_hit = sum(1 for x in conflict
                      if (1 if x[grp][key] > 0 else -1) == x["actual"])
        override_hit = base_hit - sox_hit + sub_hit
        override_rate = override_hit / n
        p = binom_p(sub_hit, len(conflict))
        flag = " <== p<0.15" if p < 0.15 and sub_hit / len(conflict) > 0.5 else ""
        print(f"  {label:14s} n={len(conflict):3d}  跟SOX={sox_hit/len(conflict):.2%}  "
              f"跟子訊號={sub_hit/len(conflict):.2%}(p={p:.3f})  "
              f"覆蓋後全日={override_rate:.2%}(Δ={override_rate-base_rate:+.1%}){flag}")


if __name__ == "__main__":
    main()
