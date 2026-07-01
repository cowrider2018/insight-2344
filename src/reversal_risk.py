"""轉空風險指數（第二軸：風險/部位，與方向軸獨立）。

問題：舊方案「決斷夜→重押」只看隔夜 beta 決定部位，在多頭紅利下命中率虛高、一遇轉折
（個股面共振偏空但隔夜偏多）即重押爆倉。此模組把「該不該重押」獨立成一個可衡量的風險軸。

指數＝三個獨立個股面空方訊號的**共振**（皆讀 data/xs.db、無 look-ahead，as-of 昨日）：
  ① 相對弱勢：2344 雙鮮流在同儕的跨股 z（負＝落後）。
  ② 外資賣壓：近 5 日外資流（負＝賣超）。
  ③ 外資連賣：連續外資淨賣天數（越長越危險）。
三者各自對歷史 z 標準化後相加＝空方共振分數，取其歷史百分位分級。

實證（2025-07~2026-06，482 日）：最空五分位 Q5 是唯一前瞻報酬為負的桶（次日 −0.32%、
5 日 −0.64%，其餘桶 +2~6%）——單一訊號各 ~53% 很弱，但**共振有單調方向梯度**，且這是
唯一連多頭資料都驗證得出效果的下檔保護。故定位為**部位/風險軸**（只降部位、不翻方向）。
"""
from __future__ import annotations

import config
import universe
import xs_db
import xs_signals as xs

# 百分位分級門檻與對應部位建議（高＝危險）
LEVELS = [
    (0.90, "極度危險", "避開/極輕（前 10% 空方共振，歷史前瞻報酬轉負）"),
    (0.80, "偏高",     "降一級部位（前 20% 空方共振）"),
    (0.60, "留意",     "略減、勿追高"),
    (0.00, "安全",     "無明顯轉空共振"),
]


def _zscore(m: dict) -> dict:
    v = list(m.values())
    if not v:
        return {}
    mu = sum(v) / len(v)
    sd = (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5 or 1.0
    return {k: (x - mu) / sd for k, x in m.items()}


def _level(pct: float) -> tuple[str, str]:
    for thr, name, advice in LEVELS:
        if pct >= thr:
            return name, advice
    return "安全", LEVELS[-1][2]


def assess(symbol: str | None = None) -> dict:
    """回傳今日（xs.db 最新交易日）轉空風險指數。失敗/樣本不足回 {"error": ...}。"""
    symbol = symbol or config.SYMBOL
    out = {"error": None, "as_of": None, "score": None, "percentile": None,
           "level": None, "advice": None, "components": {}, "sell_streak": 0}
    try:
        with xs_db.connect() as conn:
            closes, flows, vols, dates = xs_db.load_panel(conn)
            ff = xs_db.load_foreign_flows(conn)
            rows = conn.execute(
                "SELECT date, foreign_net FROM xs_chips WHERE symbol=? ORDER BY date",
                (symbol,)).fetchall()
    except Exception as e:  # noqa: BLE001
        return {**out, "error": f"xs.db 讀取失敗：{e}"}
    fnet = {r["date"]: r["foreign_net"] for r in rows if r["foreign_net"] is not None}
    D = [d for d in dates if d in closes.get(symbol, {})]
    if len(D) < 20:
        return {**out, "error": "xs.db 樣本不足（請先 python src/xs_ingest.py --backfill --all）"}

    # ① 相對弱勢：2344 雙鮮流(法人1+外資1)在同儕的 z
    comp = xs.composite([xs.smoothed_flow(flows, D, 1), xs.smoothed_flow(ff, D, 1)], D)
    xz = {}
    for d in D:
        mem = {s: comp[s][d] for s in universe.SYMBOLS if s in comp and d in comp[s]}
        if symbol in mem and len(mem) >= 5:
            xz[d] = xs.zscore_map(mem)[symbol]
    # ② 外資 5 日流（負＝賣超）
    ff5 = xs.smoothed_flow(ff, D, 5).get(symbol, {})

    # ③ 外資連賣天數（結尾連續 foreign_net<0）
    def _sellstreak(i: int) -> int:
        n = 0
        for j in range(i, -1, -1):
            x = fnet.get(D[j])
            if x is None or x >= 0:
                break
            n += 1
        return n

    rel = {d: -xz[d] for d in xz}                                   # 高＝相對弱
    sell = {d: -(ff5[d]) for d in D if d in ff5}                    # 高＝外資賣
    stk = {D[i]: _sellstreak(i) for i in range(len(D))}            # 高＝連賣久
    zr, zs_, zk = _zscore(rel), _zscore(sell), _zscore(stk)
    common = [d for d in D if d in zr and d in zs_ and d in zk]
    if len(common) < 20:
        return {**out, "error": "轉空共振樣本不足"}
    B = {d: zr[d] + zs_[d] + zk[d] for d in common}                # 空方共振（高＝空）

    d0 = common[-1]
    today = B[d0]
    vals = sorted(B.values())
    pct = sum(1 for x in vals if x < today) / len(vals)
    level, advice = _level(pct)
    out.update({
        "as_of": d0, "score": round(today, 2), "percentile": round(pct, 3),
        "level": level, "advice": advice, "sell_streak": stk[d0],
        "components": {"相對弱勢z": round(zr[d0], 2), "外資賣壓z": round(zs_[d0], 2),
                       "外資連賣z": round(zk[d0], 2)},
    })
    return out


def _components(symbol: str):
    """回傳 (D, closes, 三個原始分量 dict rel/sell/stk)；供 assess 與 backtest 共用邏輯。"""
    with xs_db.connect() as conn:
        closes, flows, vols, dates = xs_db.load_panel(conn)
        ff = xs_db.load_foreign_flows(conn)
        rows = conn.execute(
            "SELECT date, foreign_net FROM xs_chips WHERE symbol=? ORDER BY date",
            (symbol,)).fetchall()
    fnet = {r["date"]: r["foreign_net"] for r in rows if r["foreign_net"] is not None}
    D = [d for d in dates if d in closes.get(symbol, {})]
    comp = xs.composite([xs.smoothed_flow(flows, D, 1), xs.smoothed_flow(ff, D, 1)], D)
    rel = {}
    for d in D:
        mem = {s: comp[s][d] for s in universe.SYMBOLS if s in comp and d in comp[s]}
        if symbol in mem and len(mem) >= 5:
            rel[d] = -xs.zscore_map(mem)[symbol]
    ff5 = xs.smoothed_flow(ff, D, 5).get(symbol, {})
    sell = {d: -(ff5[d]) for d in D if d in ff5}
    stk = {}
    for i, d in enumerate(D):
        n = 0
        for j in range(i, -1, -1):
            x = fnet.get(D[j])
            if x is None or x >= 0:
                break
            n += 1
        stk[d] = n
    return D, closes, rel, sell, stk


def backtest(symbol: str | None = None, warmup: int = 60, horizons=(1, 5)) -> dict:
    """walk-forward 回測：每日僅以『過去』資料標準化三分量並相加成風險分數 B，評估其對前瞻
    報酬的預測（無 look-ahead）。高 B＝空方共振強；若有效，B 與前瞻報酬應**負相關**、
    高分位桶前瞻報酬偏低。回傳 IC 與分位桶統計。
    """
    symbol = symbol or config.SYMBOL
    D, closes, rel, sell, stk = _components(symbol)
    order = [d for d in D if d in rel and d in sell and d in stk]
    # 各分量以『擴張窗（僅過去）』標準化：維護 running mean/var
    def running_z(series):
        z = {}
        n = s = ss = 0.0
        for d in order:
            if n >= warmup:
                mu = s / n
                var = max(ss / n - mu * mu, 1e-12)
                z[d] = (series[d] - mu) / var ** 0.5
            x = series[d]
            n += 1
            s += x
            ss += x * x
        return z
    zr, zs_, zk = running_z(rel), running_z(sell), running_z(stk)
    B = {d: zr[d] + zs_[d] + zk[d] for d in order if d in zr and d in zs_ and d in zk}
    idx = {d: i for i, d in enumerate(D)}

    def fwd(d, h):
        i = idx[d]
        if i + h >= len(D):
            return None
        a, b = closes[symbol].get(D[i]), closes[symbol].get(D[i + h])
        return (b / a - 1) if (a and b) else None

    evald = [d for d in B]
    out = {"symbol": symbol, "warmup": warmup, "n_eval": len(evald),
           "range": [evald[0], evald[-1]] if evald else None, "horizons": {}}
    for h in horizons:
        pairs = [(B[d], fwd(d, h)) for d in evald if fwd(d, h) is not None]
        bs = [p[0] for p in pairs]
        rs = [p[1] for p in pairs]
        ic = xs.spearman(bs, rs)
        # 五分位（依 B 由低到高）
        srt = sorted(pairs, key=lambda p: p[0])
        q = 5
        buckets = []
        for g in range(q):
            seg = srt[g * len(srt) // q:(g + 1) * len(srt) // q]
            rr = [p[1] for p in seg]
            buckets.append({"mean": sum(rr) / len(rr) if rr else 0.0,
                            "down_rate": sum(1 for x in rr if x < 0) / len(rr) if rr else 0.0,
                            "n": len(rr)})
        # 極端層：B 前 10%（極度危險近似）
        k = max(1, len(srt) // 10)
        top = [p[1] for p in srt[-k:]]
        out["horizons"][h] = {
            "ic": round(ic, 4) if ic is not None else None,
            "buckets": buckets,
            "extreme_top10pct": {"mean": round(sum(top) / len(top), 4), "n": len(top),
                                 "down_rate": round(sum(1 for x in top if x < 0) / len(top), 3)},
        }
    return out


def _print_backtest(res: dict) -> None:
    print(f"[reversal_risk backtest] {res['symbol']}  評估 {res['n_eval']} 日"
          f"（{res['range'][0]}~{res['range'][1]}，warmup {res['warmup']}，walk-forward 無 look-ahead）")
    for h, r in res["horizons"].items():
        print(f"\n== 持有 {h} 日 ==  IC(B vs 前瞻報酬,應為負)={r['ic']}")
        print(f"{'B五分位(低→高空方)':<18}{'前瞻均%':>9}{'下跌率':>8}{'n':>5}")
        for g, b in enumerate(r["buckets"]):
            lab = ["Q1最買", "Q2", "Q3", "Q4", "Q5最空"][g]
            print(f"{lab:<18}{b['mean']*100:>9.2f}{b['down_rate']*100:>7.0f}%{b['n']:>5}")
        e = r["extreme_top10pct"]
        print(f"  極端(B前10%≈極度危險): 前瞻均 {e['mean']*100:.2f}%  下跌率 {e['down_rate']:.0%}  n={e['n']}")


if __name__ == "__main__":
    import sys
    if "--backtest" in sys.argv:
        _print_backtest(backtest())
    else:
        import json
        print(json.dumps(assess(), ensure_ascii=False, indent=2))
