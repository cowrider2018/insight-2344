"""EXP-008 連續同向 streak 反轉 — 研究腳本（非 production，禁止被 daily path import）

假設：2344 連漲/連跌 ≥3 日（至 D-1 止）後，D 日反轉機率顯著偏高 → 反轉訊號。
關卡④重點：streak 常是 SOX run 的影子；另比較「streak≥3 日子上跟-SOX」是否仍較強。

用法：
  python research/exp_008_streak_reversal.py
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
    streak = 0  # 至 D-1 為止的連續同向日數（+連漲／−連跌，以 close 對 close，不設中性帶）
    for i in range(1, len(candles)):
        d = candles[i]["date"]
        prev_close = candles[i - 1]["close"]
        pct = (candles[i]["close"] - prev_close) / prev_close * 100
        actual = 1 if pct > NEUTRAL_TOL else (-1 if pct < -NEUTRAL_TOL else 0)
        j = len([k for k in sox_dates if k < d])
        assert j == 0 or sox_dates[j - 1] < d, "look-ahead: sox"
        sox_chg = sox_rows[j - 1]["change_pct"] if j else None
        days.append({"date": d, "actual": actual, "sox": sox_chg, "streak": streak})
        # 更新 streak（供下一日使用；本日資訊只進下一筆 → 無 look-ahead）
        step = 1 if pct > 0 else (-1 if pct < 0 else 0)
        streak = streak + step if (step != 0 and streak * step >= 0) else step
    return days


def report(days: list[dict], title: str) -> None:
    nn = [s for s in days if s["actual"] != 0]
    base_up = sum(1 for s in nn if s["actual"] == 1) / len(nn) if nn else 0.0
    print(f"\n── {title}（非中性 n={len(nn)}，基準上漲率 {base_up:.1%}）──")
    for lbl, cond, rev_dir, base in (
        ("連漲≥3 → 押反轉(跌)", lambda s: s["streak"] >= 3, -1, 1 - base_up),
        ("連跌≥3 → 押反轉(漲)", lambda s: s["streak"] <= -3, 1, base_up),
    ):
        sub = [s for s in nn if cond(s)]
        if not sub:
            print(f"  {lbl:22s} n=0")
            continue
        hit = sum(1 for s in sub if s["actual"] == rev_dir) / len(sub)
        p = bt._binom_two_sided_p(round(hit * len(sub)), len(sub), base)
        # 同日子上「跟 SOX」對照
        wsox = [s for s in sub if s["sox"] is not None and abs(s["sox"]) >= DECISIVE_THR]
        sox_hit = (sum(1 for s in wsox if (1 if s["sox"] > 0 else -1) == s["actual"]) / len(wsox)
                   if wsox else float("nan"))
        print(f"  {lbl:22s} n={len(sub):3d}  反轉命中 {hit:.1%}（基準 {base:.1%}, Δ {hit - base:+.1%}, "
              f"p={p:.3f}）｜同日子決斷夜跟SOX {sox_hit:.1%}（n={len(wsox)}）")


if __name__ == "__main__":
    days = build_days()
    print(f"樣本 {days[0]['date']} ~ {days[-1]['date']}  n={len(days)}")
    report([s for s in days if s["date"] >= "2025-07-01"], "近一年 2025-07 起")
    report([s for s in days if "2024-08-01" <= s["date"] <= "2025-06-30"], "早一年 2024-08~2025-06")
    report([s for s in days if s["date"] >= "2024-08-01"], "合併兩年")
