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


if __name__ == "__main__":
    import json
    print(json.dumps(assess(), ensure_ascii=False, indent=2))
