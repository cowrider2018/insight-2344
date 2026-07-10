"""EXP-013 SK海力士美股上市資金移動假設 — 研究腳本（非 production，禁止被 daily path import）

假設：2026-06 底~07 元大／大摩（台灣摩根士丹利）為預備認購 SK 海力士美股上市，
提前大賣 2344 籌碼 -> 該賣壓可作為次日偏空的領先訊號。

本腳本只做 Stage 2 資料可得性 + 描述性診斷：
  1) 分點淨口數表 broker_branches 是否已含元大/大摩分點、覆蓋窗多長；
  2) 近期(2026-06~07)兩造淨口數是否明顯偏離全樣本常態（判斷「猜想」本身是否
     連描述性都站得住腳），若連描述性都不成立就不必進入 walk-forward。

用法：
  python research/exp_013_ipo_capital_flow.py
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import timeline_db as tdb  # noqa: E402

MS_BRANCH = "台灣摩根士丹利"
RECENT_START = "2026-06-01"


def load_flows(conn) -> tuple[dict[str, float], dict[str, float]]:
    rows = conn.execute(
        "SELECT date, branch, net_lots FROM broker_branches WHERE symbol = ? ORDER BY date",
        (config.SYMBOL,),
    ).fetchall()
    yuanta: dict[str, float] = defaultdict(float)
    ms: dict[str, float] = defaultdict(float)
    for r in rows:
        net = r["net_lots"] or 0
        if r["branch"].startswith("元大"):
            yuanta[r["date"]] += net
        if r["branch"] == MS_BRANCH:
            ms[r["date"]] += net
    return dict(yuanta), dict(ms)


def report(name: str, by_date: dict[str, float]) -> None:
    vals = list(by_date.values())
    mean, sd = statistics.mean(vals), statistics.pstdev(vals)
    recent = {d: v for d, v in by_date.items() if d >= RECENT_START}
    r_vals = list(recent.values())
    neg_days = sum(1 for v in r_vals if v < 0)
    print(f"\n── {name} ──  全樣本 n={len(vals)}  mean={mean:.0f}  stdev={sd:.0f}")
    print(f"  近期({RECENT_START}起) n={len(r_vals)}  賣超日 {neg_days}/{len(r_vals)}"
          f"  賣超日均值={statistics.mean([v for v in r_vals if v < 0]) if neg_days else 0:.0f}")
    extreme = [(d, v) for d, v in recent.items() if abs(v) > sd]
    print(f"  超過 1 stdev 的近期日數：{len(extreme)}（正負混雜＝{sum(1 for _, v in extreme if v > 0)}正/"
          f"{sum(1 for _, v in extreme if v < 0)}負）")


if __name__ == "__main__":
    tdb.init_db()
    with tdb.connect() as conn:
        yuanta, ms = load_flows(conn)
    print(f"broker_branches 覆蓋：{min(list(yuanta) + list(ms))} ~ {max(list(yuanta) + list(ms))}")
    report("元大全分點合計", yuanta)
    report(MS_BRANCH, ms)
    print("\n結論參見 docs/experiments/EXP-013-ipo-capital-flow.md")
