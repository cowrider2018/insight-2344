"""EXP-023 OOS 覆核（三面嚴格一致籌碼覆蓋）— 研究腳本（非 production，禁止被 daily path import）

EXP-023 全樣本（n=140，衝突34/同向34）顯示衝突日跟SOX 47.06% vs 跟籌碼52.94%（p=0.864，
不顯著）、同向日跟SOX 79.41%（n=34）。此腳本依 skill 關卡③標準做 70/30 時序切分，
檢驗這兩個子集的效應在 in-sample／OOS 是否一致（in-sample 已知不顯著，此處驗證是否
在切分後更雜訊化或恰好走勢相反，避免僅憑全樣本數字誤判）。

用法：
  python research/exp_023_unanimous_chips_oos.py
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
SPLIT = 0.7


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


def report(label: str, rows: list[dict]) -> None:
    n = len(rows)
    if n == 0:
        print(f"  [{label}] n=0")
        return
    base_hit = sum(x["sox_hit"] for x in rows)
    base_rate = base_hit / n
    conflict = [x for x in rows if x["chips_side"] != 0 and x["chips_side"] != x["sox_side"]]
    confirm = [x for x in rows if x["chips_side"] != 0 and x["chips_side"] == x["sox_side"]]
    print(f"  [{label}] 決斷夜 n={n}  跟SOX基準={base_rate:.2%}  衝突n={len(conflict)}  同向n={len(confirm)}")
    if conflict:
        sh = sum(x["sox_hit"] for x in conflict)
        ch = sum(1 for x in conflict if x["chips_side"] == x["actual"])
        print(f"      衝突日：跟SOX={sh/len(conflict):.2%}  跟籌碼={ch/len(conflict):.2%}"
              f"(p={binom_p(ch, len(conflict)):.3f})")
    if confirm:
        sh2 = sum(x["sox_hit"] for x in confirm)
        print(f"      同向日：跟SOX={sh2/len(confirm):.2%}(p={binom_p(sh2, len(confirm)):.3f})")


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

    print(f"決斷夜全樣本 n={len(dec)}（{dec[0]['date']}~{dec[-1]['date']}）\n")
    report("全樣本", dec)

    k = int(len(dec) * SPLIT)
    train, test = dec[:k], dec[k:]
    print(f"\n70/30 時序切分：train n={len(train)}（{train[0]['date']}~{train[-1]['date']}）  "
          f"test n={len(test)}（{test[0]['date']}~{test[-1]['date']}）\n")
    report("train(in-sample)", train)
    report("test(OOS)", test)


if __name__ == "__main__":
    main()
