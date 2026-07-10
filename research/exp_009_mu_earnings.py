"""EXP-009 MU 財報行事曆 — 研究腳本（非 production，禁止被 daily path import）

路徑：「報告改善」（質性標註，不入加權、不做統計 edge 主張——每年僅 ~4 事件，n 永遠不足）。
探針：免金鑰取得美光下次財報日（Yahoo quoteSummary calendarEvents，備援 earnings 模組）。
歷史脈絡：以近兩年財報日檢視 2344 次日 |漲跌| 是否放大（純描述、非 edge）。

用法：
  python research/exp_009_mu_earnings.py --probe    # 只測「下次財報日」可得性
  python research/exp_009_mu_earnings.py            # 探針＋歷史脈絡統計
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

import config  # noqa: E402
import xs_db  # noqa: E402

QS = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/MU"
      "?modules=calendarEvents%2Cearnings")
NEUTRAL_TOL = 1.0

# 近兩年 MU 財報公布日（美東盤後；來源：Micron IR 新聞稿，寫死供歷史脈絡用）
PAST_EARNINGS = ["2024-09-25", "2024-12-18", "2025-03-20", "2025-06-25",
                 "2025-09-23", "2025-12-17", "2026-03-19", "2026-06-24"]


def probe() -> dict | None:
    r = requests.get(QS, headers={"User-Agent": config.USER_AGENT}, timeout=20)
    print(f"[probe] HTTP {r.status_code}")
    if r.status_code != 200:
        return None
    res = r.json()["quoteSummary"]["result"][0]
    ev = res.get("calendarEvents", {}).get("earnings", {})
    dates = [datetime.fromtimestamp(x["raw"], tz=timezone.utc).date().isoformat()
             for x in ev.get("earningsDate", []) if isinstance(x, dict) and "raw" in x]
    print(f"[probe] 下次財報日窗：{dates}")
    return {"next": dates}


def context_stats() -> None:
    with xs_db.connect() as conn:
        rows = conn.execute(
            "SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
            (config.SYMBOL,),
        ).fetchall()
    candles = [{"date": r["date"], "close": r["close"]} for r in rows if r["close"]]
    dates = [c["date"] for c in candles]
    moves = {}
    for i in range(1, len(candles)):
        moves[candles[i]["date"]] = abs(
            (candles[i]["close"] - candles[i - 1]["close"]) / candles[i - 1]["close"] * 100)
    avg_all = sum(moves.values()) / len(moves)
    # 財報反應日＝財報日（美東盤後）之後的第一個台股交易日再下一日？
    # 美東 D 盤後公布 → 台北 D+1 清晨已知 → 台股「當日」即反應日：取第一個 > 財報日 的台股交易日
    hits = []
    for e in PAST_EARNINGS:
        nxt = next((d for d in dates if d > e), None)
        if nxt and nxt in moves:
            hits.append((e, nxt, moves[nxt]))
    print(f"全樣本平均 |漲跌| = {avg_all:.2f}%（n={len(moves)}）")
    print("MU 財報反應日（台股第一個交易日）：")
    for e, nxt, m in hits:
        print(f"  財報 {e} → 反應日 {nxt}  |漲跌| {m:.2f}%")
    if hits:
        avg_e = sum(m for _, _, m in hits) / len(hits)
        print(f"反應日平均 |漲跌| = {avg_e:.2f}% vs 全樣本 {avg_all:.2f}%（n={len(hits)}，純描述非 edge）")


if __name__ == "__main__":
    out = probe()
    if "--probe" not in sys.argv[1:]:
        context_stats()
    sys.exit(0 if out else 1)
