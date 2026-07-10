"""EXP-021 決斷夜籌碼流量變化覆蓋（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：EXP-016~020 用的都是籌碼面「單日水位」（level）與 SOX 比對，一致 REJECT。
改測籌碼三面分數的「日變化」（D 的分數 − D-1 的分數，即趨勢/加速度，仍全部 as-of `<D`：
兩者皆用 D 當天盤前可得的 D-1／D-2 資料算出）是否與 SOX 衝突時有獨立覆蓋力
——趨勢轉向可能比單日水位承載更多「風向轉變」資訊。

用法：
  python research/exp_021_chips_delta_decisive.py
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


def main() -> None:
    tdb.init_db()
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, START, config.today_str()[:4] + "-12-31", dd.NEUTRAL)
        samples = bt.score_samples(feats, scoring.PARAMS)
        ov = {}
        for f in feats:
            us = tdb.us_asof(conn, dd.OVERNIGHT_KEY, f["date"])
            ov[f["date"]] = us["change_pct"] if us and us.get("change_pct") is not None else None

    # 逐面計算日變化（D 樣本分數 - 前一個「有資料」樣本分數；只用序列中已出現的歷史，無 look-ahead）
    last_val = {d: None for d in CHIP_DIMS}
    rows = []
    for s in samples:
        deltas = {}
        for d in CHIP_DIMS:
            v = s["scores"].get(d)
            if v is not None and last_val[d] is not None:
                deltas[d] = v - last_val[d]
            if v is not None:
                last_val[d] = v
        rows.append({"date": s["date"], "actual": s["actual"], "deltas": deltas})

    dec = []
    for s, r in zip(samples, rows):
        if s["actual"] == 0:
            continue
        o = ov.get(s["date"])
        if o is None or abs(o) < dd.DECISIVE_THR:
            continue
        dec.append({"date": s["date"], "actual": s["actual"], "sox_side": dd._sign(o),
                    "sox_hit": dd._sign(o) == s["actual"], "deltas": r["deltas"]})

    n = len(dec)
    base_hit = sum(x["sox_hit"] for x in dec)
    base_rate = base_hit / n
    print(f"決斷夜基準 {base_rate:.2%}（n={n}）")

    for dim in CHIP_DIMS:
        conflict = [x for x in dec if x["deltas"].get(dim) not in (None, 0)
                   and (1 if x["deltas"][dim] > 0 else -1) != x["sox_side"]]
        if len(conflict) < MIN_ACTIVE:
            print(f"  {dim:10s} n={len(conflict):3d}（<{MIN_ACTIVE}，跳過）")
            continue
        sox_hit = sum(x["sox_hit"] for x in conflict)
        delta_hit = sum(1 for x in conflict
                        if (1 if x["deltas"][dim] > 0 else -1) == x["actual"])
        override_hit = base_hit - sox_hit + delta_hit
        override_rate = override_hit / n
        p = binom_p(delta_hit, len(conflict))
        flag = " <== p<0.15" if p < 0.15 and delta_hit / len(conflict) > 0.5 else ""
        print(f"  {dim:10s}趨勢 n={len(conflict):3d}  跟SOX={sox_hit/len(conflict):.2%}  "
              f"跟趨勢={delta_hit/len(conflict):.2%}(p={p:.3f})  "
              f"覆蓋後全日={override_rate:.2%}(Δ={override_rate-base_rate:+.1%}){flag}")


if __name__ == "__main__":
    main()
