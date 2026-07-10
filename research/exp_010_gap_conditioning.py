"""EXP-010 決斷夜跳空條件化 — 研究腳本（非 production，禁止被 daily path import）

假設：決斷夜且 |SOX| 極大（預期跳空極大）時「開盤吃掉行情」→ 全日收盤方向跟-SOX 的
edge 衰減 ≥5pp → 極端決斷夜應降 conviction。只測全日收盤方向條件化；嚴禁重測盤中差價
（已否決清單）。分箱預先註冊：1≤|SOX|<2、2≤|SOX|<3、|SOX|≥3。

用法：
  python research/exp_010_gap_conditioning.py
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
BINS = (("1≤|SOX|<2", lambda a: 1 <= a < 2), ("2≤|SOX|<3", lambda a: 2 <= a < 3),
        ("|SOX|≥3", lambda a: a >= 3))


def build_days() -> list[dict]:
    with xs_db.connect() as conn:
        rows = conn.execute(
            "SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
            (config.SYMBOL,),
        ).fetchall()
    candles = [{"date": r["date"], "close": r["close"]} for r in rows if r["close"]]
    sox_rows = [r for r in fetch_us.fetch_yahoo_daily("^SOX", "2y") if r["change_pct"] is not None]
    sox_dates = [r["date"] for r in sox_rows]
    days = []
    for i in range(1, len(candles)):
        d = candles[i]["date"]
        prev_close = candles[i - 1]["close"]
        pct = (candles[i]["close"] - prev_close) / prev_close * 100
        actual = 1 if pct > NEUTRAL_TOL else (-1 if pct < -NEUTRAL_TOL else 0)
        j = len([k for k in sox_dates if k < d])
        assert j == 0 or sox_dates[j - 1] < d, "look-ahead: sox"
        days.append({"date": d, "actual": actual,
                     "sox": sox_rows[j - 1]["change_pct"] if j else None})
    return days


def report(days: list[dict], title: str) -> None:
    dec = [s for s in days if s["sox"] is not None and abs(s["sox"]) >= 1 and s["actual"] != 0]
    base = (sum(1 for s in dec if (1 if s["sox"] > 0 else -1) == s["actual"]) / len(dec)
            if dec else 0.0)
    print(f"\n── {title}：決斷夜跟-SOX 基準 {base:.1%}（n={len(dec)}）──")
    for name, cond in BINS:
        sub = [s for s in dec if cond(abs(s["sox"]))]
        if not sub:
            print(f"  {name:12s} n=0")
            continue
        hit = sum(1 for s in sub if (1 if s["sox"] > 0 else -1) == s["actual"]) / len(sub)
        p = bt._binom_two_sided_p(round(hit * len(sub)), len(sub), base)
        print(f"  {name:12s} n={len(sub):3d}  跟SOX命中 {hit:.1%}（Δ {hit - base:+.1%}, p={p:.3f}）")


if __name__ == "__main__":
    days = build_days()
    print(f"樣本 {days[0]['date']} ~ {days[-1]['date']}  n={len(days)}")
    report([s for s in days if s["date"] >= "2025-07-01"], "近一年 2025-07 起")
    report([s for s in days if "2024-08-01" <= s["date"] <= "2025-06-30"], "早一年 2024-08~2025-06")
    report([s for s in days if s["date"] >= "2024-08-01"], "合併兩年")
