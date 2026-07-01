"""盤前橫斷面籌碼分數：2344 今日的籌碼流入強度在跨股中的相對位置。

純讀 data/xs.db，重用既有橫斷面工具（xs_db / xs_signals），算出最新交易日
（= 昨日盤後公布的籌碼鮮流）2344 在兩個池中的 z-score／百分位／分位：
  - peer  ：universe.SYMBOLS 同產業同儕池（記憶體/半導體/電子）。
  - market：當日成交量前 top 檔的全市場流動性橫斷面。

**無 look-ahead**：sig(D) 由 D 盤後資料算，盤前報告取 xs.db 最新日 D（昨日）即為最新可用快照。
回傳供 daily_report 決策卡與 build_dataset JSON（skill 完整報告）共用。
"""
from __future__ import annotations

import config
import universe
import xs_db
import xs_signals as xs

QUANTILES = 5          # 5 分位（5＝籌碼流入最強）
DEFAULT_TOP = 300      # 全市場池每日流動性前 N 檔
IC_LOOKBACK = 60       # 近期 IC 取最後 N 個評估日（~一季，當 regime 閘門用，較 40 日穩）


def _ic_state(ic: float | None) -> str:
    """近期 IC → regime 閘門標籤。跨年驗證顯示此籌碼因子 regime-dependent（2024H2 曾翻負），
    故每日以近期 IC 判斷訊號當前是否有效：反向時分數不可用於研判。"""
    if ic is None:
        return "無資料"
    if ic >= 0.03:      # 達「實質跨股預測力」經驗門檻
        return "有效"
    if ic >= 0.01:
        return "偏弱"
    if ic > -0.01:
        return "中性"   # ≈0，近乎無邊際
    return "反向"        # 明顯負：訊號當前失效/反噬，分數勿用


def _scope_members(sig: dict, vols: dict, date: str, pool: str, top: int) -> dict:
    """回傳該池在 date 當日「有訊號值」的成員 {symbol: signal_value}。

    peer  ：限 universe.SYMBOLS。
    market：全市場，若超過 top 檔則取當日成交量前 top（流動性橫斷面）。
    """
    if pool == "peer":
        cand = {s: sig[s][date] for s in universe.SYMBOLS
                if s in sig and date in sig[s]}
    else:
        cand = {s: v[date] for s, v in sig.items() if date in v}
        if top and len(cand) > top:
            ranked = sorted(cand, key=lambda s: vols.get(s, {}).get(date) or 0.0,
                            reverse=True)[:top]
            cand = {s: cand[s] for s in ranked}
    return cand


def _position(members: dict, symbol: str) -> dict | None:
    """2344 在該池當日的相對位置：z-score、百分位、分位（5＝最高流入）、名次。"""
    if symbol not in members or len(members) < 5:
        return None
    z = xs.zscore_map(members).get(symbol, 0.0)
    v = members[symbol]
    n = len(members)
    below = sum(1 for x in members.values() if x < v)
    groups = xs.quantile_groups(list(members.items()), QUANTILES)
    quintile = next((g + 1 for g, syms in groups.items() if symbol in syms), None)
    return {
        "n": n,
        "z": round(z, 3),
        "pct": round(below / (n - 1), 3) if n > 1 else 0.5,   # 0=最低 1=最高
        "quintile": quintile,
        "rank": n - below,                                    # 1=流入最強
    }


def _recent_ic(dates: list[str], sig: dict, closes: dict, vols: dict,
               pool: str, top: int, lookback: int) -> tuple[float | None, int]:
    """近期跨股 IC（Spearman, sig(D)→次日報酬），取最後 lookback 個評估日的平均。

    正 IC＝高分股次日相對偏強，作為分數可信度的客觀佐證。重用 xs_signals.spearman。
    """
    ics: list[float] = []
    for i in range(len(dates) - 1):
        d, dn = dates[i], dates[i + 1]
        members = _scope_members(sig, vols, d, pool, top)
        rows = [(members[s], closes[s][dn] / closes[s][d] - 1.0)
                for s in members
                if closes.get(s, {}).get(d) and closes.get(s, {}).get(dn) is not None]
        if len(rows) < 5:
            continue
        ic = xs.spearman([r[0] for r in rows], [r[1] for r in rows])
        if ic is not None:
            ics.append(ic)
    ics = ics[-lookback:]
    if not ics:
        return None, 0
    return round(sum(ics) / len(ics), 4), len(ics)


def cross_section_score(symbol: str | None = None, top: int = DEFAULT_TOP) -> dict:
    """2344 最新交易日的橫斷面籌碼分數（peer + market 兩池）。

    失敗/樣本不足一律回 {"error": ...}，呼叫端優雅降級為「橫斷面資料不足」。
    """
    symbol = symbol or config.SYMBOL
    out = {"error": None, "as_of": None, "signal_label": None, "pools": {}}
    try:
        with xs_db.connect() as conn:
            closes, flows, vols, dates = xs_db.load_panel(conn)
            fflows = xs_db.load_foreign_flows(conn)
    except Exception as e:  # noqa: BLE001
        return {**out, "error": f"xs.db 讀取失敗：{e}"}
    if len(dates) < 10:
        return {**out, "error": "xs.db 樣本不足（請先 python src/xs_ingest.py --backfill --all）"}

    # 1 日鮮流複合（法人流1日 + 外資流1日，等權跨股 z）：實測次日 IC≈0.046、IC_IR≈0.50，
    # 遠勝平滑/多日複合（5/20 日把過期籌碼摻入會稀釋 edge）。全市場一次算好，兩池各自池內標準化。
    t1 = xs.smoothed_flow(flows, dates, 1)
    ff1 = xs.smoothed_flow(fflows, dates, 1)
    sig = xs.composite([t1, ff1], dates)
    label = "雙鮮流(法人1日+外資1日)"
    out["signal_label"] = label

    # 最新「2344 有訊號」的交易日（無 look-ahead：此即昨日盤後快照）
    sdates = [d for d in dates if symbol in sig and d in sig[symbol]]
    if not sdates:
        return {**out, "error": f"{symbol} 無橫斷面籌碼訊號（成交量/法人資料缺）"}
    d0 = sdates[-1]
    out["as_of"] = d0

    for pool in ("peer", "market"):
        members = _scope_members(sig, vols, d0, pool, top)
        pos = _position(members, symbol)
        if pos is None:
            out["pools"][pool] = {"error": "樣本不足"}
            continue
        ic, ic_n = _recent_ic(dates, sig, closes, vols, pool, top, IC_LOOKBACK)
        out["pools"][pool] = {**pos, "recent_ic": ic, "ic_n": ic_n, "state": _ic_state(ic)}

    if all(p.get("error") for p in out["pools"].values()):
        return {**out, "error": "兩池皆樣本不足"}
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(cross_section_score(), ensure_ascii=False, indent=2))
