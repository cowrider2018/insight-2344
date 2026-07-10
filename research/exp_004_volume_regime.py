"""EXP-004 量能 regime 濾鏡 — 研究腳本（非 production，禁止被 daily path import）

假設：D-1 量能比 r＝vol(D-1)/vol_ma5(D-6..D-2) 分層下，核心訊號「決斷夜跟 SOX」
的可靠度有系統性差異（爆量 r≥1.5 或縮量 r≤0.7 時 ≥±5pp）→ 可作 confidence 輸入。
不動方向軸，只驗可靠度分層。

用法：
  python research/exp_004_volume_regime.py            # 三視窗（近一年／早一年／合併）
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
BINS = (("縮量 r≤0.7", lambda r: r <= 0.7),
        ("正常 0.7<r<1.5", lambda r: 0.7 < r < 1.5),
        ("爆量 r≥1.5", lambda r: r >= 1.5))


def build_days() -> list[dict]:
    with xs_db.connect() as conn:
        rows = conn.execute(
            "SELECT date, close, volume FROM xs_candles WHERE symbol = ? ORDER BY date",
            (config.SYMBOL,),
        ).fetchall()
    candles = [dict(date=r["date"], close=r["close"], volume=r["volume"] or 0.0)
               for r in rows if r["close"]]
    sox_rows = [r for r in fetch_us.fetch_yahoo_daily("^SOX", "2y") if r["change_pct"] is not None]
    sox_dates = [r["date"] for r in sox_rows]
    days = []
    for i in range(6, len(candles)):
        d = candles[i]["date"]
        prev_close = candles[i - 1]["close"]
        pct = (candles[i]["close"] - prev_close) / prev_close * 100
        actual = 1 if pct > NEUTRAL_TOL else (-1 if pct < -NEUTRAL_TOL else 0)
        j = len([k for k in sox_dates if k < d])
        sox_chg = sox_rows[j - 1]["change_pct"] if j else None
        assert j == 0 or sox_dates[j - 1] < d, "look-ahead: sox"
        ma5 = sum(c["volume"] for c in candles[i - 6:i - 1]) / 5  # D-6..D-2
        r = candles[i - 1]["volume"] / ma5 if ma5 else None       # D-1 量能比，全部 < D
        days.append({"date": d, "actual": actual, "sox": sox_chg, "r": r})
    return days


def report(days: list[dict], title: str) -> None:
    dec = [s for s in days if s["sox"] is not None and abs(s["sox"]) >= DECISIVE_THR
           and s["actual"] != 0 and s["r"] is not None]
    base_hit = sum(1 for s in dec if (1 if s["sox"] > 0 else -1) == s["actual"])
    base = base_hit / len(dec) if dec else 0.0
    print(f"\n── {title}：決斷夜跟-SOX 基準 {base:.1%}（n={len(dec)}）──")
    for name, cond in BINS:
        sub = [s for s in dec if cond(s["r"])]
        if not sub:
            print(f"  {name:16s} n=0")
            continue
        hit = sum(1 for s in sub if (1 if s["sox"] > 0 else -1) == s["actual"]) / len(sub)
        p = bt._binom_two_sided_p(round(hit * len(sub)), len(sub), base)
        print(f"  {name:16s} n={len(sub):3d}  跟SOX命中 {hit:.1%}（Δ {hit - base:+.1%}, p={p:.3f}）")


def run() -> None:
    days = build_days()
    print(f"樣本 {days[0]['date']} ~ {days[-1]['date']}  n={len(days)}")
    report([s for s in days if s["date"] >= "2025-07-01"], "近一年 2025-07-01 起")
    report([s for s in days if "2024-08-01" <= s["date"] <= "2025-06-30"], "早一年 2024-08~2025-06")
    report([s for s in days if s["date"] >= "2024-08-01"], "合併兩年")


if __name__ == "__main__":
    run()
