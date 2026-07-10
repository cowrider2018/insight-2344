"""EXP-029 橫斷面相對強弱（框架重構）— 研究腳本（非 production，禁止被 daily path import）

假設：把預測目標從「2344 絕對方向」換成「2344 vs 記憶體同業籃次日相對報酬」後，
SOX 共同因子被相減消除，先前在絕對方向下全滅的個股特異訊號（相對動能、相對法人流）
應在此目標下復活。

同業籃（等權）：2408 南亞科、2337 旺宏、4967 十銓、2451 創見、3006 晶豪科。
目標：rel(D) = ret_2344(D) − mean(ret_peers(D))；中性帶 ±0.5%（預先註冊；相對波動較小）。

訊號（全部 as-of < D，預先註冊）：
  1. rel_mom1：D-1 相對報酬符號（相對動能續勢）
  2. rel_mom3：D-3..D-1 相對報酬合計符號
  3. frel：D-1 外資買賣超/成交量（2344 − 同業平均）符號（相對外資流）
  4. trel：D-1 三大法人買賣超/成交量（2344 − 同業平均）符號

關卡：②顯著性 ③70/30 OOS＋分年 ④SOX影子（決斷夜/平淡夜分層——相對目標理論上
SOX 中性，若 edge 只在決斷夜＝殘餘 beta 未消乾淨）。另附平均 |rel| 供成本評估。

用法：
  python research/exp_029_xs_relative_strength.py
"""
from __future__ import annotations

import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import timeline_db as tdb  # noqa: E402
import xs_db  # noqa: E402

SYM = "2344"
PEERS = ("2408", "2337", "4967", "2451", "3006")
REL_NEUTRAL = 0.5
FLAT_SOX = 1.0


def binom_p(hit: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 1.0
    lo, hi = min(hit, n - hit), max(hit, n - hit)
    tail = sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(hi, n + 1))
    return min(1.0, 2 * tail)


def load_stock(c, sym: str) -> dict[str, dict]:
    """回傳 {date: {close, volume, foreign_net, total_net}}（按日）。"""
    out: dict[str, dict] = {}
    for r in c.execute("SELECT date, close, volume FROM xs_candles WHERE symbol=? ORDER BY date", (sym,)):
        if r["close"]:
            out[r["date"]] = {"close": r["close"], "volume": r["volume"]}
    for r in c.execute("SELECT date, foreign_net, total_net FROM xs_chips WHERE symbol=?", (sym,)):
        if r["date"] in out:
            out[r["date"]]["foreign_net"] = r["foreign_net"]
            out[r["date"]]["total_net"] = r["total_net"]
    return out


def main() -> None:
    tdb.init_db()
    with xs_db.connect() as c:
        data = {s: load_stock(c, s) for s in (SYM,) + PEERS}
    with tdb.connect() as conn:
        sox_rows = conn.execute(
            "SELECT date, change_pct FROM us_market WHERE symbol IN ('sox','soxx') ORDER BY date").fetchall()
    sox_map: dict[str, float] = {}
    for r in sox_rows:
        if r["change_pct"] is not None:
            sox_map[r["date"]] = r["change_pct"]  # sox 較晚寫入，同日覆蓋 soxx
    sox_dates = sorted(sox_map)

    dates = sorted(data[SYM])
    # 逐日計算各股報酬與相對序列
    rets: dict[str, dict[str, float]] = {s: {} for s in data}
    for s, dd_ in data.items():
        ds = sorted(dd_)
        for i in range(1, len(ds)):
            p0_, p1_ = dd_[ds[i - 1]]["close"], dd_[ds[i]]["close"]
            rets[s][ds[i]] = (p1_ - p0_) / p0_ * 100

    rel: dict[str, float] = {}
    for d in dates:
        if d not in rets[SYM]:
            continue
        peer_r = [rets[p][d] for p in PEERS if d in rets[p]]
        if len(peer_r) < 3:
            continue
        rel[d] = rets[SYM][d] - sum(peer_r) / len(peer_r)

    rel_dates = sorted(rel)
    print(f"相對報酬序列 n={len(rel_dates)}　平均|rel|={sum(abs(rel[d]) for d in rel_dates)/len(rel_dates):.2f}%　"
          f"|rel|>{REL_NEUTRAL}% 占比={sum(1 for d in rel_dates if abs(rel[d]) > REL_NEUTRAL)/len(rel_dates):.0%}")

    def flow_rel(d_prev: str, key: str) -> float | None:
        """D-1 的（2344 − 同業平均）法人流/成交量。"""
        me = data[SYM].get(d_prev)
        if not me or me.get(key) is None or not me.get("volume"):
            return None
        mine = me[key] / me["volume"]
        pv = []
        for p in PEERS:
            r = data[p].get(d_prev)
            if r and r.get(key) is not None and r.get("volume"):
                pv.append(r[key] / r["volume"])
        if len(pv) < 3:
            return None
        return mine - sum(pv) / len(pv)

    samples = []
    for i, d in enumerate(rel_dates):
        if i < 3:
            continue
        target = 1 if rel[d] > REL_NEUTRAL else (-1 if rel[d] < -REL_NEUTRAL else 0)
        if target == 0:
            continue
        prev = rel_dates[i - 1]
        assert prev < d, "look-ahead: rel_mom"
        sox_prior = [sd for sd in sox_dates if sd < d]
        sox = sox_map[sox_prior[-1]] if sox_prior else None
        samples.append({
            "date": d, "target": target,
            "rel_mom1": rel[prev],
            "rel_mom3": sum(rel[rel_dates[j]] for j in range(i - 3, i)),
            "frel": flow_rel(prev, "foreign_net"),
            "trel": flow_rel(prev, "total_net"),
            "sox": sox,
        })
    print(f"方向性樣本（|rel|>{REL_NEUTRAL}%）n={len(samples)}")

    def eval_sig(sub, key, tag):
        act = [(1 if x[key] > 0 else -1, x["target"]) for x in sub
               if x.get(key) is not None and x[key] != 0]
        if len(act) < 8:
            print(f"    {tag}: n={len(act)}（<8 跳過）")
            return
        hit = sum(1 for s_, t_ in act if s_ == t_)
        print(f"    {tag}: n={len(act):3d}  命中={hit/len(act):.2%}(p={binom_p(hit, len(act)):.3f})")

    mid = "2025-07-01"
    for key in ("rel_mom1", "rel_mom3", "frel", "trel"):
        print(f"\n── {key} ──")
        eval_sig(samples, key, "全窗")
        eval_sig([x for x in samples if x["date"] < mid], key, "早一年")
        eval_sig([x for x in samples if x["date"] >= mid], key, "近一年")
        k = int(len(samples) * 0.7)
        eval_sig(samples[:k], key, "train70")
        eval_sig(samples[k:], key, "test30")
        # 關卡④：決斷夜/平淡夜分層（相對目標應 SOX 中性）
        eval_sig([x for x in samples if x["sox"] is not None and abs(x["sox"]) >= FLAT_SOX], key, "決斷夜")
        eval_sig([x for x in samples if x["sox"] is not None and abs(x["sox"]) < FLAT_SOX], key, "平淡夜")


if __name__ == "__main__":
    main()
