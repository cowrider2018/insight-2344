"""記憶體族群輪動偵測 + 外資抽離「下檔保護 veto」（方向軸的 risk-off 修正）。

問題：核心「決斷夜跟隔夜費半 SOX」預設「2344＝記憶體＝費半 beta」。當資金**專屬地**輪出
記憶體族群（如海力士 IPO 吸走記憶體資金）時，這條連動被打斷——費半隔夜漲、2344 仍被殺，
跟 SOX 反向送死。2026-06-22→07-08 回檔即此型：全市場(前300)持平 +0.4%、記憶體籃 −22%、
2344 −26%，外資對記憶體籃倒 −37.8 萬張——是**族群輪動、非總體 risk-off**。

本模組兩層（皆 as-of D-1、無 look-ahead）：
  ① 原因層 regime 診斷（為何該不信 SOX）：記憶體同業籃相對全市場的近 k 日相對強弱
     ＋族群外資流。分類 memory_rotation / broad_risk_off / normal（解釋用）。
  ② 快觸發 veto（當下翻向）：2344 自身外資近 3 日累積賣超 ≤ VETO_LOTS（張）時，
     否決核心的「跟 SOX 做多」、翻偏空（不對稱：只擋做多、不擋做空＝下檔保護）。

實證（walk-forward，2025-07-01~2026-07-08，見 --validate）：方向命中率 全年 64%→65%
（幾乎無損、全年僅 3 誤觸日），回檔期(06-23~07-08) 60%→80%。回檔時資金最先重砸 2344 本身，
故單檔外資流比族群籃聚合更快更準（族群相對強弱較平滑、轉折落後）——單檔當觸發、族群當診斷。

侷限（需人工複核）：僅單一輪動事件、回檔方向日 n=10，門檻為示意非定論；急殺後反彈日會誤觸
（如 2026-06-25 +7%）。定位為**防禦性下檔保護 overlay**，非對稱、非全天候 alpha。
"""
from __future__ import annotations

import config
import timeline_db as tdb
import universe
import xs_db

# 記憶體/模組同業籃（DRAM/Flash/控制IC/模組）——族群輪動的觀測籃
MEMORY_BASKET = ["2344", "2408", "2337", "8299", "3260", "4967", "2451", "3006"]
VETO_LOTS = -60000     # 2344 外資近 K 日累積賣超門檻（張）；≤ 此值 → veto 做多
K = 3                  # 外資累積與相對強弱視窗（交易日）
MARKET_TOPN = 300      # 全市場報酬用當日成交量前 N 檔（濾除雞蛋水餃股噪音）
ROTATION_FOREIGN_LOTS = -120000   # 族群外資近 K 日累積賣超（診斷 memory_rotation 用）


def _sgn(x) -> int:
    if x is None:
        return 0
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _foreign_sum_asof(conn, symbol: str, d: str, k: int = K) -> float | None:
    """< d 的最近 k 個交易日 2344 外資買賣超累積（張）。data_date < d 保證無 look-ahead。"""
    rows = conn.execute(
        "SELECT foreign_net FROM chips WHERE symbol=? AND data_date<? AND foreign_net IS NOT NULL "
        "ORDER BY data_date DESC LIMIT ?", (symbol, d, k)).fetchall()
    return sum(r["foreign_net"] for r in rows) if len(rows) >= k else None


def _xs_panel(xs_conn):
    """由 xs.db 取 {closes, vols, fnet, dates}（全市場，用於族群相對強弱與族群外資流）。"""
    closes: dict[str, dict[str, float]] = {}
    vols: dict[str, dict[str, float]] = {}
    for r in xs_conn.execute("SELECT symbol, date, close, volume FROM xs_candles"):
        if r["close"] is not None:
            closes.setdefault(r["symbol"], {})[r["date"]] = r["close"]
            vols.setdefault(r["date"], {})[r["symbol"]] = r["volume"]
    fnet: dict[str, dict[str, float]] = {}
    for r in xs_conn.execute("SELECT symbol, date, foreign_net FROM xs_chips"):
        if r["foreign_net"] is not None:
            fnet.setdefault(r["date"], {})[r["symbol"]] = r["foreign_net"]
    dates = sorted({d for s in closes.values() for d in s})
    return {"closes": closes, "vols": vols, "fnet": fnet, "dates": dates}


def _ret(panel, sym: str, d: str, prev: str) -> float | None:
    cd = panel["closes"].get(sym, {})
    a, b = cd.get(prev), cd.get(d)
    return (b - a) / a * 100.0 if (a and b) else None


def _basket_ret(panel, syms, d: str, prev: str) -> float | None:
    rs = [_ret(panel, s, d, prev) for s in syms]
    rs = [x for x in rs if x is not None]
    return sum(rs) / len(rs) if rs else None


def _market_ret(panel, d: str, prev: str, topn: int = MARKET_TOPN) -> float | None:
    vv = panel["vols"].get(d, {})
    top = sorted((s for s in vv if vv[s]), key=lambda s: -vv[s])[:topn]
    rs = [_ret(panel, s, d, prev) for s in top]
    rs = [x for x in rs if x is not None]
    return sum(rs) / len(rs) if rs else None


def _rotation_context(panel, d: str, k: int = K) -> dict:
    """< d 近 k 交易日：記憶體籃 vs 全市場累積報酬、族群外資累積 → 診斷 regime。"""
    prior = [x for x in panel["dates"] if x < d]
    if len(prior) < k + 1:
        return {"regime": "unknown", "mem_rel_strength": None,
                "basket_foreign": None, "market_ret": None, "memory_ret": None}
    seq = prior[-(k + 1):]
    mem_c = mkt_c = 0.0
    for i in range(1, len(seq)):
        mr = _basket_ret(panel, MEMORY_BASKET, seq[i], seq[i - 1])
        kr = _market_ret(panel, seq[i], seq[i - 1])
        if mr is not None:
            mem_c += mr
        if kr is not None:
            mkt_c += kr
    bf = sum(sum(v for s in MEMORY_BASKET if (v := panel["fnet"].get(x, {}).get(s)) is not None)
             for x in prior[-k:])
    rel = mem_c - mkt_c
    # 分類（診斷用）：以「記憶體相對全市場落後幅度」為主：
    #  - 全市場明顯下跌且記憶體未特別落後 → 總體 risk-off（跟 SOX 做空本就對，veto 非必要）。
    #  - 記憶體明顯落後全市場（大盤未崩，或族群外資重賣）→ 記憶體族群輪出（SOX 連動失效，veto 對症）。
    if mkt_c <= -3.0 and rel > -2.0:
        regime = "broad_risk_off"
    elif rel <= -2.0 and (mkt_c > -3.0 or bf <= ROTATION_FOREIGN_LOTS):
        regime = "memory_rotation"
    else:
        regime = "normal"
    return {"regime": regime, "mem_rel_strength": round(rel, 2),
            "basket_foreign": round(bf), "market_ret": round(mkt_c, 2),
            "memory_ret": round(mem_c, 2)}


def assess(as_of_date: str | None = None, symbol: str | None = None) -> dict:
    """今日盤前 risk-off 狀態：veto 旗標（會翻向）＋ 族群輪動診斷（解釋）。

    as_of_date 省略＝取 2344 chips 最新日的次一交易日情境（即『今日』盤前，用 < 最新日 的資料）。
    回傳 veto_long（True＝重賣超、應否決做多翻偏空）與 regime 診斷；失敗回 {"error": ...}。
    """
    symbol = symbol or config.SYMBOL
    out = {"error": None, "as_of": None, "data_through": None, "foreign_sum_k": None,
           "veto_lots": VETO_LOTS, "k": K, "veto_long": False, "regime": None,
           "context": {}, "reason": ""}
    data_through = None
    try:
        with tdb.connect() as conn:
            # as-of 邊界 d：只用 data_date < d 的資料（無 look-ahead）
            if as_of_date is None:
                row = conn.execute(
                    "SELECT MAX(data_date) m FROM chips WHERE symbol=?", (symbol,)).fetchone()
                data_through = row["m"] if row else None
                if not data_through:
                    return {**out, "error": "chips 無資料（請先 build_dataset / backfill-chips）"}
                # 次一交易日盤前：取 candles 中 > 最新資料日的最小日；無（今日尚未收盤）則用哨兵
                nxt = conn.execute(
                    "SELECT MIN(date) n FROM candles WHERE symbol=? AND date>?",
                    (symbol, data_through)).fetchone()
                d = (nxt and nxt["n"]) or "9999-12-31"
            else:
                d = as_of_date
            fsum = _foreign_sum_asof(conn, symbol, d, K)
    except Exception as e:  # noqa: BLE001
        return {**out, "error": f"market.db 讀取失敗：{e}"}

    ctx = {}
    try:
        with xs_db.connect() as xc:
            ctx = _rotation_context(_xs_panel(xc), d, K)
    except Exception as e:  # noqa: BLE001
        ctx = {"regime": "unknown", "error": f"xs.db 讀取失敗：{e}"}

    veto = fsum is not None and fsum <= VETO_LOTS
    regime = ctx.get("regime")
    if veto:
        why = {"memory_rotation": "記憶體族群輪出（大盤未跌、資金專屬撤離記憶體）",
               "broad_risk_off": "總體 risk-off（全市場同步走弱）",
               "normal": "外資單檔重賣（族群診斷未達輪動門檻）"}.get(regime, "外資單檔重賣")
        reason = (f"2344 外資近 {K} 日累積 {fsum:+.0f} 張 ≤ {VETO_LOTS} → 否決『跟 SOX 做多』、"
                  f"翻偏空（下檔保護）；情境：{why}")
    else:
        reason = (f"2344 外資近 {K} 日累積 {fsum:+.0f} 張 > {VETO_LOTS} → 不觸發（抽離壓力未達門檻）"
                  if fsum is not None else "外資資料不足，veto 不觸發")
    as_of_label = "次一交易日盤前" if d == "9999-12-31" else d
    out.update({"as_of": as_of_label, "data_through": data_through,
                "foreign_sum_k": None if fsum is None else round(fsum),
                "veto_long": bool(veto), "regime": regime, "context": ctx, "reason": reason})
    return out


def apply_to_side(core_side: int, state: dict) -> tuple[int, str]:
    """把 risk-off veto 套到核心方向：核心做多(+1) 且 veto_long → 翻空(-1)；否則原樣。

    不對稱：只擋做多（下檔保護），不動做空/中性。回傳 (調整後方向, 說明)。
    """
    if state and state.get("veto_long") and core_side == 1:
        return -1, "下檔保護 veto：核心跟 SOX 做多，但外資重抽離 → 翻偏空"
    return core_side, ""


# ---------- walk-forward 驗證（複核回檔期修正效果） ----------

def validate(start: str = "2025-07-01", end: str = "2026-07-08",
             pullback_start: str = "2026-06-23", neutral: float = 1.0) -> dict:
    """核心 vs 核心+veto 的方向命中率（全期 & 回檔期），無 look-ahead。"""
    import backtest as bt

    def core_side(f) -> int:
        sox = f["sox"]["change_pct"] if f["sox"] else None
        if sox is not None and abs(sox) >= 1.0:
            return _sgn(sox)
        ma = f["technical"].get("ma") or {}
        return -1 if (f["prev_close"] and ma.get("ma20") and f["prev_close"] < ma["ma20"]) else 1

    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, start, end, neutral)
        fs = {f["date"]: _foreign_sum_asof(conn, config.SYMBOL, f["date"], K) for f in feats}

    def veto_side(f) -> int:
        b = core_side(f)
        v = fs.get(f["date"])
        return -1 if (b == 1 and v is not None and v <= VETO_LOTS) else b

    def hit(fn, sub):
        h = n = 0
        for f in sub:
            if f["actual"] == 0:
                continue
            s = fn(f)
            if s == 0:
                continue
            n += 1
            h += (s == f["actual"])
        return {"win": round(h / n, 4) if n else 0.0, "n": n}

    recent = [f for f in feats if f["date"] >= pullback_start]
    fp = sum(1 for f in feats if f["actual"] == 1 and core_side(f) == 1
             and (fs.get(f["date"]) or 0) <= VETO_LOTS)
    trig = sum(1 for f in feats if core_side(f) == 1 and (fs.get(f["date"]) or 0) <= VETO_LOTS)
    return {
        "window": [start, end], "pullback": [pullback_start, end],
        "veto_lots": VETO_LOTS, "k": K, "n_trigger_days": trig, "n_false_positive_up_days": fp,
        "full": {"core": hit(core_side, feats), "veto": hit(veto_side, feats)},
        "pullback_seg": {"core": hit(core_side, recent), "veto": hit(veto_side, recent)},
    }


def main(argv: list[str]) -> None:
    import json
    if "--validate" in argv:
        def opt(flag, d):
            return argv[argv.index(flag) + 1] if flag in argv else d
        r = validate(opt("--start", "2025-07-01"), opt("--end", "2026-07-08"),
                     opt("--pullback", "2026-06-23"))
        print(f"[risk_off 驗證] 視窗 {r['window'][0]}~{r['window'][1]}  "
              f"veto 門檻 {r['veto_lots']}張/近{r['k']}日  觸發 {r['n_trigger_days']}日/年  "
              f"漲日誤觸 {r['n_false_positive_up_days']}日")
        f, p = r["full"], r["pullback_seg"]
        print(f"  全期  ：核心 {f['core']['win']:.0%}(n={f['core']['n']}) → "
              f"+veto {f['veto']['win']:.0%}(n={f['veto']['n']})")
        print(f"  回檔期：核心 {p['core']['win']:.0%}(n={p['core']['n']}) → "
              f"+veto {p['veto']['win']:.0%}(n={p['veto']['n']})  [{r['pullback'][0]}~{r['pullback'][1]}]")
    else:
        print(json.dumps(assess(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    main(sys.argv)
