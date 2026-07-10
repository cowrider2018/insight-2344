"""EXP-006 波動 regime — 研究腳本（非 production，禁止被 daily path import）

假設：高波動 regime（VIX 高檔或 SOX 10 日已實現波動高）下，「決斷夜跟 SOX」
同日勝率衰減 ≥5pp → 高波動決斷夜應降 conviction（重押→保守）的 overlay。
門檻預先註冊：VIX <17／17–25／≥25；SOX rv10 <1.5／1.5–3／≥3（%）。

用法：
  python research/exp_006_vol_regime.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import backtest as bt  # noqa: E402
import config  # noqa: E402
import fetch_us  # noqa: E402
import xs_db  # noqa: E402

NEUTRAL_TOL = 1.0
DECISIVE_THR = 1.0
VIX_BINS = (("VIX<17", lambda v: v < 17), ("17≤VIX<25", lambda v: 17 <= v < 25),
            ("VIX≥25", lambda v: v >= 25))
RV_BINS = (("rv10<1.5", lambda v: v < 1.5), ("1.5≤rv10<3", lambda v: 1.5 <= v < 3),
           ("rv10≥3", lambda v: v >= 3))


def build_days() -> list[dict]:
    with xs_db.connect() as conn:
        rows = conn.execute(
            "SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
            (config.SYMBOL,),
        ).fetchall()
    candles = [{"date": r["date"], "close": r["close"]} for r in rows if r["close"]]
    sox_rows = [r for r in fetch_us.fetch_yahoo_daily("^SOX", "2y") if r["change_pct"] is not None]
    vix_raw = fetch_us.fetch_yahoo_daily("^VIX", "2y")
    vix_rows = [r for r in vix_raw if r["close"] is not None]
    sox_dates = [r["date"] for r in sox_rows]
    vix_dates = [r["date"] for r in vix_rows]
    days = []
    for i in range(1, len(candles)):
        d = candles[i]["date"]
        prev_close = candles[i - 1]["close"]
        pct = (candles[i]["close"] - prev_close) / prev_close * 100
        actual = 1 if pct > NEUTRAL_TOL else (-1 if pct < -NEUTRAL_TOL else 0)
        j = len([k for k in sox_dates if k < d])
        assert j == 0 or sox_dates[j - 1] < d, "look-ahead: sox"
        sox_chg = sox_rows[j - 1]["change_pct"] if j else None
        rv10 = None
        if j >= 10:
            win = [r["change_pct"] for r in sox_rows[j - 10:j]]
            m = sum(win) / 10
            rv10 = (sum((x - m) ** 2 for x in win) / 10) ** 0.5
        k = len([x for x in vix_dates if x < d])
        assert k == 0 or vix_dates[k - 1] < d, "look-ahead: vix"
        vix = vix_rows[k - 1]["close"] if k else None
        days.append({"date": d, "actual": actual, "sox": sox_chg, "rv10": rv10, "vix": vix})
    return days


def report(days: list[dict], key: str, bins, title: str) -> None:
    dec = [s for s in days if s["sox"] is not None and abs(s["sox"]) >= DECISIVE_THR
           and s["actual"] != 0 and s[key] is not None]
    base = (sum(1 for s in dec if (1 if s["sox"] > 0 else -1) == s["actual"]) / len(dec)
            if dec else 0.0)
    print(f"\n── {title}：決斷夜跟-SOX 基準 {base:.1%}（n={len(dec)}）──")
    for name, cond in bins:
        sub = [s for s in dec if cond(s[key])]
        if not sub:
            print(f"  {name:12s} n=0")
            continue
        hit = sum(1 for s in sub if (1 if s["sox"] > 0 else -1) == s["actual"]) / len(sub)
        p = bt._binom_two_sided_p(round(hit * len(sub)), len(sub), base)
        print(f"  {name:12s} n={len(sub):3d}  跟SOX命中 {hit:.1%}（Δ {hit - base:+.1%}, p={p:.3f}）")


def run() -> None:
    days = build_days()
    print(f"樣本 {days[0]['date']} ~ {days[-1]['date']}  n={len(days)}")
    for lbl, lo, hi in (("近一年 2025-07 起", "2025-07-01", "2099-12-31"),
                        ("早一年 2024-08~2025-06", "2024-08-01", "2025-06-30"),
                        ("合併兩年", "2024-08-01", "2099-12-31")):
        seg = [s for s in days if lo <= s["date"] <= hi]
        report(seg, "vix", VIX_BINS, f"{lbl}（VIX 分箱）")
        report(seg, "rv10", RV_BINS, f"{lbl}（SOX rv10 分箱）")


if __name__ == "__main__":
    run()
