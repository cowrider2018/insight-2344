"""Fugle Marketdata API：行情（quote）、技術面（candles→indicators）、基本面（stats）。

防禦性解析：任何欄位缺漏都回 None 並記錄 warning，不中斷整體流程。
"""
from __future__ import annotations

from datetime import timedelta

import requests

import config
import indicators


def _get(path: str, params: dict | None = None) -> dict:
    if not config.FUGLE_API_KEY:
        raise RuntimeError("FUGLE_MARKETDATA_API_KEY 未設定")
    url = f"{config.FUGLE_BASE}/{path}"
    r = requests.get(
        url,
        params=params or {},
        headers={"X-API-KEY": config.FUGLE_API_KEY, "User-Agent": config.USER_AGENT},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def fetch_candles(days: int = 360) -> list[dict]:
    """近 N 個日曆日的日 K，回傳由舊到新排序的標準 candle list。

    注意：Fugle 限制查詢區間須小於一年，故 days < 365（360 約含 245 個交易日，足夠 MA240）。
    """
    to_d = config.now_tpe().date()
    from_d = to_d - timedelta(days=days)
    js = _get(
        f"historical/candles/{config.SYMBOL}",
        {
            "from": from_d.isoformat(),
            "to": to_d.isoformat(),
            "fields": "open,high,low,close,volume,turnover,change",
        },
    )
    rows = js.get("data", []) or []
    candles = []
    for row in rows:
        try:
            candles.append(
                {
                    "date": row["date"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row.get("volume") or 0),
                    "turnover": int(row.get("turnover") or 0),
                    "change": float(row.get("change") or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    candles.sort(key=lambda c: c["date"])  # 確保由舊到新
    return candles


def fetch_stats() -> dict:
    """historical/stats：52 週高低、估值等（欄位視方案而定，缺則 None）。"""
    try:
        return _get(f"historical/stats/{config.SYMBOL}")
    except requests.RequestException:
        return {}


def fetch_quote() -> dict:
    """intraday/quote：盤前可能無當日資料，僅作估值欄位補充。"""
    try:
        return _get(f"intraday/quote/{config.SYMBOL}")
    except requests.RequestException:
        return {}


def build(warnings: list[str]) -> dict:
    """回傳 (quote, technical, fundamental) 三區塊與 trading_date。"""
    out = {"quote": {}, "technical": {}, "fundamental": {}, "trading_date": None,
           "name": config.NAME}

    candles = fetch_candles()
    if not candles:
        warnings.append("fugle: 無歷史 K 線資料")
        return out

    last = candles[-1]
    prev_close = candles[-2]["close"] if len(candles) >= 2 else None
    change = round(last["close"] - prev_close, 2) if prev_close else last.get("change")
    change_pct = round(change / prev_close * 100, 2) if (prev_close and change is not None) else None

    out["trading_date"] = last["date"]
    out["name"] = config.NAME
    out["quote"] = {
        "prev_close": prev_close,
        "open": last["open"],
        "high": last["high"],
        "low": last["low"],
        "close": last["close"],
        "change": change,
        "change_pct": change_pct,
        "volume_shares": last["volume"],            # Fugle volume 單位為股
        "volume_lots": round(last["volume"] / 1000),  # 股 -> 張
        "turnover": last["turnover"],
    }

    out["technical"] = indicators.compute_all(candles)

    stats = fetch_stats()
    quote = fetch_quote().get("quote", {})
    # 估值欄位在不同方案/端點命名不一，逐一嘗試
    def pick(*keys, src=None):
        for src_dict in ([src] if src is not None else [stats, quote]):
            if not isinstance(src_dict, dict):
                continue
            for k in keys:
                v = src_dict.get(k)
                if v not in (None, "", 0):
                    return v
        return None

    out["fundamental"] = {
        "pe": pick("peRatio", "pe"),
        "pb": pick("pbRatio", "priceToBook", "pb"),
        "dividend_yield": pick("dividendYield", "yield"),
        "market_cap": pick("marketCap"),
        "week52_high": pick("week52High", "highPrice52Weeks"),
        "week52_low": pick("week52Low", "lowPrice52Weeks"),
        "monthly_revenue": None,  # 由 TWSE 補
    }
    if not any(out["fundamental"].values()):
        warnings.append("fugle: 估值/基本面欄位多數缺漏（方案權限或端點調整）")
    return out


if __name__ == "__main__":
    import json
    w: list[str] = []
    print(json.dumps(build(w), ensure_ascii=False, indent=2, default=str))
    print("warnings:", w)
