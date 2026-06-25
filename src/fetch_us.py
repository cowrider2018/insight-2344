"""隔夜美股外生特徵：美光 (MU) 與費城半導體指數 (SOX)。

記憶體族群（含華邦電 2344）的當日開盤/方向，受隔夜美股—尤其費半 SOX 與美光 MU—
主導程度，往往大於自身技術面。故將兩者「隔夜漲跌 %」作為第 5、6 個特徵。

來源：Yahoo Finance chart JSON（免金鑰）。日線時戳以美東時區換算成「美股交易日」，
回傳由舊到新、含 change_pct（當日 close vs 前一交易日 close）。

【盤後/延長交易】Yahoo 日線只含正常盤收盤，**抓不到美股盤後（after-hours）**。
然美光財報常於盤後公布，盤後巨幅跳動才是「當晚 → 台股今日開盤」真正的隔夜訊號。
故另以 CNBC 報價 API（免金鑰、含 ExtendedMktQuote）補抓盤後價，計算
effective_change_pct（前一日收盤 → 最新盤後價）作為今日開盤的主導外生訊號。
歷史回測仍用正常盤 change_pct（盤後無歷史資料，且權重以正常盤校準）。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import config

_ET = ZoneInfo("America/New_York")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
CNBC = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"

# 內部維度名 -> Yahoo 代號
US_SYMBOLS = {"micron": "MU", "sox": "^SOX"}
# 內部維度名 -> CNBC 代號（用於補抓盤後價；指數無盤後，僅個股有意義）
CNBC_SYMBOLS = {"micron": "MU"}


def _num(s) -> float | None:
    """把 CNBC 的格式化字串（如 '1,213.96'、'+15.78%'、'+165.45'）轉成 float。"""
    if s is None:
        return None
    t = str(s).replace(",", "").replace("%", "").replace("+", "").strip()
    if t in ("", "UNCH", "N/A"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def fetch_cnbc_extended(cnbc_sym: str) -> dict | None:
    """抓 CNBC 報價（含盤後）。回傳含正常盤與盤後價、市場狀態的字典；失敗回 None。"""
    r = requests.get(
        CNBC,
        params={
            "symbols": cnbc_sym, "requestMethod": "itv", "noform": 1,
            "partnerId": 2, "fund": 1, "exthrs": 1, "output": "json", "events": 1,
        },
        headers={"User-Agent": config.USER_AGENT},
        timeout=20,
    )
    r.raise_for_status()
    quotes = r.json().get("FormattedQuoteResult", {}).get("FormattedQuote") or []
    if not quotes:
        return None
    q = quotes[0]
    prev = _num(q.get("previous_day_closing"))
    reg_last = _num(q.get("last"))
    out = {
        "status": q.get("curmktstatus"),           # REG_MKT / POST_MKT / PRE_MKT / CLOSED
        "regular_last": reg_last,
        "regular_change_pct": _num(q.get("change_pct")),
        "previous_close": prev,
    }
    ext = q.get("ExtendedMktQuote") or {}
    ah_last = _num(ext.get("last"))
    if ah_last is not None:
        out["after_hours"] = {
            "type": ext.get("type"),                # POST_MKT / PRE_MKT
            "price": ah_last,
            "change": _num(ext.get("change")),
            "change_pct": _num(ext.get("change_pct")),   # 相對正常盤收盤
            "as_of": ext.get("last_timedate") or ext.get("last_time"),
            "volume": ext.get("volume_alt") or ext.get("volume"),
            "source": "CNBC",
        }
        # 前一日收盤 -> 最新盤後價：今日台股開盤真正面對的隔夜漲跌%
        if prev:
            out["effective_change_pct"] = round((ah_last - prev) / prev * 100, 2)
    return out


def fetch_yahoo_daily(yahoo_sym: str, range_: str = "1y") -> list[dict]:
    """回傳 [{date(YYYY-MM-DD 美股交易日), close, change_pct}]，由舊到新。"""
    r = requests.get(
        YAHOO.format(sym=yahoo_sym),
        params={"range": range_, "interval": "1d"},
        headers={"User-Agent": config.USER_AGENT},
        timeout=20,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res.get("timestamp") or []
    closes = res["indicators"]["quote"][0].get("close") or []

    rows: list[dict] = []
    prev = None
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=_ET).date().isoformat()
        chg = round((c - prev) / prev * 100, 2) if prev else None
        rows.append({"date": d, "close": round(float(c), 2), "change_pct": chg})
        prev = c
    return rows


def fetch_overnight(symbol_key: str, range_: str = "5d") -> dict | None:
    """取某代號最新一筆（盤前的隔夜值）。symbol_key ∈ US_SYMBOLS。"""
    rows = fetch_yahoo_daily(US_SYMBOLS[symbol_key], range_)
    return rows[-1] if rows else None


def build_overnight(warnings: list[str]) -> dict:
    """回傳 {micron: {...}, sox: {...}} 供每日 dataset 引用。任一失敗記 warning，不中斷。

    個股（美光）另以 CNBC 補抓盤後價，於該維度加上 after_hours / effective_change_pct。
    頂層 change_pct 仍為正常盤（供 ingest 累積與回測一致）。
    """
    out: dict = {}
    for key in US_SYMBOLS:
        try:
            row = fetch_overnight(key)
            if row:
                out[key] = row
            else:
                warnings.append(f"us {key}: 無資料")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"us {key} 失敗: {e}")

    # 盤後補抓（僅個股；指數無盤後）。失敗不影響正常盤資料。
    for key, cnbc_sym in CNBC_SYMBOLS.items():
        try:
            ext = fetch_cnbc_extended(cnbc_sym)
            if not ext:
                continue
            row = out.setdefault(key, {})
            row["market_status"] = ext.get("status")
            ah = ext.get("after_hours")
            if ah:
                row["after_hours"] = ah
                if ext.get("effective_change_pct") is not None:
                    row["effective_change_pct"] = ext["effective_change_pct"]
        except Exception as e:  # noqa: BLE001
            warnings.append(f"us {key} 盤後(CNBC) 失敗: {e}")
    return out


if __name__ == "__main__":
    import json
    w: list[str] = []
    print(json.dumps(build_overnight(w), ensure_ascii=False, indent=2))
    print("warnings:", w)
