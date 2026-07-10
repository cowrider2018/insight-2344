"""EXP-030 橫斷面相對強弱・中期尺度（框架重構延伸）— 研究腳本（非 production，禁止被 daily path import）

假設：日頻相對強弱無訊號（EXP-029 高信度否定），但中期尺度（未來 5/10 交易日的相對報酬）
由較慢的資金輪動驅動，慢變數應有預測力：
  1. rel_mom20：過去 20 日累積相對報酬（相對強弱趨勢持續）
  2. flow_rel10：過去 10 日累積（外資買賣超/成交量，2344 − 同業平均）
  3. tdcc_rel：最新一期集保大戶比率週變化（2344 − 同業平均；data_date < D，週六公布故 <D 安全）

統計設計：**不重疊區塊**——5 日 horizon 每 5 個交易日取樣一次（10 日同理），
避免重疊多日報酬的自相關灌水 p 值。目標中性帶：|5日相對報酬| > 1%（10日 > 1.5%，預註冊）。

若成立：作為日報「相對強弱週期」質性說明軸（當日仍照常給絕對方向），需另過使用者確認才整合。

用法：
  python research/exp_030_xs_medium_horizon.py
"""
from __future__ import annotations

import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import xs_db  # noqa: E402

SYM = "2344"
PEERS = ("2408", "2337", "4967", "2451", "3006")
HORIZONS = ((5, 1.0), (10, 1.5))  # (未來N日, 中性帶%)


def binom_p(hit: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 1.0
    lo, hi = min(hit, n - hit), max(hit, n - hit)
    tail = sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(hi, n + 1))
    return min(1.0, 2 * tail)


def load_stock(c, sym: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in c.execute("SELECT date, close, volume FROM xs_candles WHERE symbol=? ORDER BY date", (sym,)):
        if r["close"]:
            out[r["date"]] = {"close": r["close"], "volume": r["volume"]}
    for r in c.execute("SELECT date, foreign_net FROM xs_chips WHERE symbol=?", (sym,)):
        if r["date"] in out:
            out[r["date"]]["foreign_net"] = r["foreign_net"]
    return out


def load_tdcc(c, sym: str) -> list[tuple[str, float]]:
    return [(r["data_date"], r["big_pct"]) for r in c.execute(
        "SELECT data_date, big_pct FROM xs_tdcc WHERE symbol=? ORDER BY data_date", (sym,))
        if r["big_pct"] is not None]


def main() -> None:
    with xs_db.connect() as c:
        data = {s: load_stock(c, s) for s in (SYM,) + PEERS}
        tdcc = {s: load_tdcc(c, s) for s in (SYM,) + PEERS}

    # 日相對報酬序列
    rets: dict[str, dict[str, float]] = {s: {} for s in data}
    for s, dd_ in data.items():
        ds = sorted(dd_)
        for i in range(1, len(ds)):
            rets[s][ds[i]] = (dd_[ds[i]]["close"] - dd_[ds[i - 1]]["close"]) / dd_[ds[i - 1]]["close"] * 100
    dates = sorted(d for d in rets[SYM])
    rel = {}
    for d in dates:
        pr = [rets[p][d] for p in PEERS if d in rets[p]]
        if len(pr) >= 3:
            rel[d] = rets[SYM][d] - sum(pr) / len(pr)
    rel_dates = sorted(rel)

    def flow_rel_1d(d: str) -> float | None:
        me = data[SYM].get(d)
        if not me or me.get("foreign_net") is None or not me.get("volume"):
            return None
        mine = me["foreign_net"] / me["volume"]
        pv = [data[p][d]["foreign_net"] / data[p][d]["volume"] for p in PEERS
              if d in data[p] and data[p][d].get("foreign_net") is not None and data[p][d].get("volume")]
        if len(pv) < 3:
            return None
        return mine - sum(pv) / len(pv)

    def tdcc_rel_asof(d: str) -> float | None:
        """最新一期（data_date < D）大戶比率週變化（2344 − 同業平均）。"""
        def chg(sym):
            hist = [(dd_, v) for dd_, v in tdcc[sym] if dd_ < d]
            if len(hist) < 2:
                return None
            return hist[-1][1] - hist[-2][1]
        mine = chg(SYM)
        if mine is None:
            return None
        pv = [x for x in (chg(p) for p in PEERS) if x is not None]
        if len(pv) < 3:
            return None
        return mine - sum(pv) / len(pv)

    for horizon, band in HORIZONS:
        print(f"\n════ 未來{horizon}日相對報酬（中性帶±{band}%，不重疊每{horizon}日取樣）════")
        samples = []
        i = 25  # 暖身（20日動能）
        while i + horizon <= len(rel_dates):
            d = rel_dates[i]
            fwd = sum(rel[rel_dates[j]] for j in range(i, i + horizon))       # D..D+h-1（含當日）
            past20 = sum(rel[rel_dates[j]] for j in range(i - 20, i))          # < D
            f10 = [flow_rel_1d(rel_dates[j]) for j in range(i - 10, i)]        # < D
            f10v = [x for x in f10 if x is not None]
            target = 1 if fwd > band else (-1 if fwd < -band else 0)
            samples.append({
                "date": d, "target": target,
                "rel_mom20": past20,
                "flow_rel10": sum(f10v) if len(f10v) >= 7 else None,
                "tdcc_rel": tdcc_rel_asof(d),
            })
            i += horizon
        directional = [x for x in samples if x["target"] != 0]
        print(f"取樣 {len(samples)} 塊，方向性 {len(directional)} 塊")

        def eval_sig(sub, key, tag):
            act = [(1 if x[key] > 0 else -1, x["target"]) for x in sub
                   if x.get(key) is not None and x[key] != 0]
            if len(act) < 8:
                print(f"    {tag}: n={len(act)}（<8 跳過）")
                return
            hit = sum(1 for s_, t_ in act if s_ == t_)
            print(f"    {tag}: n={len(act):3d}  命中={hit/len(act):.2%}(p={binom_p(hit, len(act)):.3f})")

        mid = "2025-07-01"
        for key in ("rel_mom20", "flow_rel10", "tdcc_rel"):
            print(f"  ── {key} ──")
            eval_sig(directional, key, "全窗")
            eval_sig([x for x in directional if x["date"] < mid], key, "早一年")
            eval_sig([x for x in directional if x["date"] >= mid], key, "近一年")
            k = int(len(directional) * 0.7)
            eval_sig(directional[:k], key, "train70")
            eval_sig(directional[k:], key, "test30")


if __name__ == "__main__":
    main()
