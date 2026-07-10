"""EXP-022 決斷夜籌碼子成分（法人淨額/融資變化）覆蓋（推理流程 overlay）— 研究腳本
（非 production，禁止被 daily path import）

假設：EXP-016~021 都測聚合後的 chips 面分數，chips 本身由「三大法人淨額(inst)」與
「融資餘額變化(margin)」兩子成分加權而成（score_chips），可能互相抵銷。改拆解兩子成分，
個別在決斶夜與 SOX 衝突時是否有獨立覆蓋力。

用法：
  python research/exp_022_chips_subparts_decisive.py
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


def chips_parts(chips: dict | None, ref_vol_lots: float | None, p: dict) -> dict:
    """重現 scoring.score_chips 的兩子成分（inst / margin），拆開回傳。"""
    out = {}
    if not chips:
        return out
    total_net = chips.get("total_net")
    if total_net is not None:
        if ref_vol_lots and ref_vol_lots > 0:
            out["inst"] = scoring.clamp(total_net / (p["chips_inst_frac"] * ref_vol_lots))
        else:
            out["inst"] = scoring.clamp(total_net / p["chips_fixed_scale"])
    margin_chg = chips.get("margin_chg")
    margin_balance = chips.get("margin_balance")
    if margin_chg is not None and margin_balance:
        out["margin"] = scoring.clamp(-margin_chg / (0.05 * abs(margin_balance) + 1e-9))
    return out


def main() -> None:
    tdb.init_db()
    p = scoring.PARAMS
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, START, config.today_str()[:4] + "-12-31", dd.NEUTRAL)
        samples = bt.score_samples(feats, p)
        ov = {}
        for f in feats:
            us = tdb.us_asof(conn, dd.OVERNIGHT_KEY, f["date"])
            ov[f["date"]] = us["change_pct"] if us and us.get("change_pct") is not None else None

    dec = []
    for f, s in zip(feats, samples):
        if s["actual"] == 0:
            continue
        o = ov.get(f["date"])
        if o is None or abs(o) < dd.DECISIVE_THR:
            continue
        parts = chips_parts(f["chips"], f["ref_vol_lots"], p)
        dec.append({"date": f["date"], "actual": s["actual"], "sox_side": dd._sign(o),
                    "sox_hit": dd._sign(o) == s["actual"], "parts": parts})

    n = len(dec)
    base_hit = sum(x["sox_hit"] for x in dec)
    base_rate = base_hit / n
    print(f"決斷夜基準 {base_rate:.2%}（n={n}）")

    for key in ("inst", "margin"):
        conflict = [x for x in dec if x["parts"].get(key) not in (None, 0)
                   and (1 if x["parts"][key] > 0 else -1) != x["sox_side"]]
        if len(conflict) < MIN_ACTIVE:
            print(f"  {key:8s} n={len(conflict):3d}（<{MIN_ACTIVE}，跳過）")
            continue
        sox_hit = sum(x["sox_hit"] for x in conflict)
        part_hit = sum(1 for x in conflict
                       if (1 if x["parts"][key] > 0 else -1) == x["actual"])
        override_hit = base_hit - sox_hit + part_hit
        override_rate = override_hit / n
        pv = binom_p(part_hit, len(conflict))
        flag = " <== p<0.15" if pv < 0.15 and part_hit / len(conflict) > 0.5 else ""
        print(f"  {key:8s} n={len(conflict):3d}  跟SOX={sox_hit/len(conflict):.2%}  "
              f"跟子成分={part_hit/len(conflict):.2%}(p={pv:.3f})  "
              f"覆蓋後全日={override_rate:.2%}(Δ={override_rate-base_rate:+.1%}){flag}")


if __name__ == "__main__":
    main()
