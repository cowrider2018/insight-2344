"""EXP-007 週期效應 — 研究腳本（非 production，禁止被 daily path import）

假設：2344 次日方向存在日曆系統性偏差——(a) day-of-week、(b) 月底月初（月末 2 日＋月初 3 日）、
(c) 營收公布旬（每月 1~10 日）。任一子效應需 |Δ|≥5pp、p<0.15 且跨年同向才算數。
多重檢定警告：5 weekday × 2 年 = 預期 ~1 個 p<0.15 假陽性 → 跨年一致性是硬要求。

用法：
  python research/exp_007_calendar.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import backtest as bt  # noqa: E402
import config  # noqa: E402
import xs_db  # noqa: E402

NEUTRAL_TOL = 1.0
WD = ("一", "二", "三", "四", "五")


def build_days() -> list[dict]:
    with xs_db.connect() as conn:
        rows = conn.execute(
            "SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
            (config.SYMBOL,),
        ).fetchall()
    candles = [{"date": r["date"], "close": r["close"]} for r in rows if r["close"]]
    dates = [c["date"] for c in candles]
    days = []
    for i in range(1, len(candles)):
        d = candles[i]["date"]
        prev_close = candles[i - 1]["close"]
        pct = (candles[i]["close"] - prev_close) / prev_close * 100
        actual = 1 if pct > NEUTRAL_TOL else (-1 if pct < -NEUTRAL_TOL else 0)
        dt = date.fromisoformat(d)
        month = d[:7]
        # 月底 2 日：往後看同月剩餘交易日 <2 → 但這是 look-ahead？否——月曆是先驗已知，
        # 交易日曆亦於月初公告；此處以「本月最後 2 個交易日」以實際日曆計（可事前推知）。
        rest_in_month = [x for x in dates[i:] if x[:7] == month]
        turn = len(rest_in_month) <= 2 or len([x for x in dates[:i + 1] if x[:7] == month]) <= 3
        days.append({"date": d, "actual": actual, "wd": dt.weekday(),
                     "turn": turn, "rev_window": 1 <= dt.day <= 10})
    return days


def seg_stats(seg: list[dict], cond) -> tuple[int, float, float, float]:
    nn = [s for s in seg if s["actual"] != 0]
    base = sum(1 for s in nn if s["actual"] == 1) / len(nn) if nn else 0.0
    sub = [s for s in nn if cond(s)]
    if not sub:
        return 0, 0.0, base, 1.0
    up = sum(1 for s in sub if s["actual"] == 1) / len(sub)
    p = bt._binom_two_sided_p(round(up * len(sub)), len(sub), base)
    return len(sub), up, base, p


def report(days: list[dict]) -> None:
    wins = (("近一年", [s for s in days if s["date"] >= "2025-07-01"]),
            ("早一年", [s for s in days if "2024-08-01" <= s["date"] <= "2025-06-30"]),
            ("合併", [s for s in days if s["date"] >= "2024-08-01"]))
    print("── day-of-week 上漲率 Δ vs 基準（非中性日）──")
    for w in range(5):
        row = []
        for name, seg in wins:
            n, up, base, p = seg_stats(seg, lambda s, w=w: s["wd"] == w)
            row.append(f"{name} Δ{up - base:+.1%}(n={n},p={p:.2f})")
        print(f"  週{WD[w]}： " + "  ".join(row))
    for label, key in (("月底月初（末2＋初3）", "turn"), ("營收公布旬（1~10日）", "rev_window")):
        row = []
        for name, seg in wins:
            n, up, base, p = seg_stats(seg, lambda s, k=key: s[k])
            row.append(f"{name} Δ{up - base:+.1%}(n={n},p={p:.2f})")
        print(f"── {label} ──\n  " + "  ".join(row))


if __name__ == "__main__":
    days = build_days()
    print(f"樣本 {days[0]['date']} ~ {days[-1]['date']}  n={len(days)}")
    report(days)
