"""TWSE 籌碼面與月營收（皆免金鑰公開資料）。

- 三大法人買賣超：RWD /fund/T86（OpenAPI 版已停用，回傳 HTML）。
- 個股融資融券：RWD /marginTrading/MI_MARGN（融資/融券欄位同名，需以「出現次序」區分）。
- 月營收：OpenAPI /opendata/t187ap05_L。
TWSE 回應須強制 UTF-8 解碼，否則中文欄名變亂碼。失敗則回 partial 並記 warning。
"""
from __future__ import annotations

import requests

import config

_HEADERS = {"User-Agent": config.USER_AGENT}


def _get_json(url: str, params: dict | None = None, retries: int = 3):
    """TWSE 偶有節流，回空資料/非 OK；重試數次。"""
    import time

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params or {}, headers=_HEADERS, timeout=20)
            r.raise_for_status()
            r.encoding = "utf-8"  # TWSE 未在 header 正確標示，強制 UTF-8
            js = r.json()
            stat = js.get("stat") if isinstance(js, dict) else None
            has_rows = (not isinstance(js, dict)) or bool(js.get("data")) or bool(js.get("tables"))
            if stat in (None, "OK") and has_rows:
                return js
            last_err = f"stat={stat} 或無資料"
        except (requests.RequestException, ValueError) as e:
            last_err = e
        time.sleep(1.5 * (attempt + 1))
    raise ValueError(f"TWSE 重試 {retries} 次仍失敗: {last_err}")


def _to_int(s) -> int | None:
    if s is None:
        return None
    try:
        return int(str(s).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def _flt(s):
    if s is None:
        return None
    try:
        return round(float(str(s).replace(",", "").strip()), 2)
    except ValueError:
        return None


def _lots(shares) -> float | None:
    """股數 -> 張。"""
    v = _to_int(shares)
    return None if v is None else round(v / 1000, 1)


def fetch_institutional(warnings: list[str], date_str: str | None = None) -> dict:
    """T86：三大法人個股買賣超（股數→張）。"""
    out = {"foreign_net": None, "trust_net": None, "dealer_net": None,
           "total_net": None, "data_date": None}
    params = {"selectType": "ALL", "response": "json"}
    if date_str:
        params["date"] = date_str
    try:
        js = _get_json(f"{config.TWSE_RWD}/fund/T86", params)
    except (requests.RequestException, ValueError) as e:
        warnings.append(f"twse T86 失敗: {e}")
        return out

    out["data_date"] = js.get("date")
    fields = js.get("fields") or []
    data = js.get("data") or []
    row = next((r for r in data if r and str(r[0]).strip() == config.SYMBOL), None)
    if not row:
        warnings.append("twse T86: 找不到 2344（可能尚未更新）")
        return out

    def col(*needles, exclude=()):
        for i, f in enumerate(fields):
            fs = str(f)
            if all(n in fs for n in needles) and not any(x in fs for x in exclude):
                return row[i] if i < len(row) else None
        return None

    # 外資買賣超（不含自營）、投信買賣超、自營商買賣超（合計）、三大法人合計
    out["foreign_net"] = _lots(col("買賣超股數", "不含外資自營商"))
    out["trust_net"] = _lots(col("投信", "買賣超股數"))
    out["dealer_net"] = _lots(col("自營商買賣超股數", exclude=("自行", "避險", "外資")))
    out["total_net"] = _lots(col("三大法人買賣超股數"))
    return out


def fetch_margin(warnings: list[str], date_str: str | None = None) -> dict:
    """MI_MARGN：個股融資/融券今日餘額與較前日增減（單位：張）。"""
    out = {"margin_balance": None, "margin_chg": None,
           "short_balance": None, "short_chg": None, "data_date": None}
    params = {"selectType": "ALL", "response": "json"}
    if date_str:
        params["date"] = date_str
    try:
        js = _get_json(f"{config.TWSE_RWD}/marginTrading/MI_MARGN", params)
    except (requests.RequestException, ValueError) as e:
        warnings.append(f"twse MI_MARGN 失敗: {e}")
        return out

    out["data_date"] = js.get("date")
    for tbl in (js.get("tables") or [js]):
        fields = tbl.get("fields") or []
        data = tbl.get("data") or []
        if not fields or "代號" not in str(fields[0]):
            continue
        row = next((r for r in data if r and str(r[0]).strip() == config.SYMBOL), None)
        if not row:
            continue
        # 融資/融券欄名同名（今日餘額/前日餘額各出現兩次）：第一組=融資、第二組=融券
        today_idx = [i for i, f in enumerate(fields) if str(f) == "今日餘額"]
        prev_idx = [i for i, f in enumerate(fields) if str(f) == "前日餘額"]
        if len(today_idx) >= 2 and len(prev_idx) >= 2:
            m_today, m_prev = _to_int(row[today_idx[0]]), _to_int(row[prev_idx[0]])
            s_today, s_prev = _to_int(row[today_idx[1]]), _to_int(row[prev_idx[1]])
            out["margin_balance"] = m_today
            out["margin_chg"] = (m_today - m_prev) if None not in (m_today, m_prev) else None
            out["short_balance"] = s_today
            out["short_chg"] = (s_today - s_prev) if None not in (s_today, s_prev) else None
        break
    if out["margin_balance"] is None:
        warnings.append("twse MI_MARGN: 找不到 2344 個股融資融券（可能尚未更新）")
    return out


def fetch_monthly_revenue(warnings: list[str]) -> dict | None:
    """t187ap05_L：上市公司月營收彙總（最新月）。當月營收單位：仟元。"""
    try:
        rows = _get_json(f"{config.TWSE_OPENAPI}/opendata/t187ap05_L")
    except (requests.RequestException, ValueError) as e:
        warnings.append(f"twse 月營收失敗: {e}")
        return None
    row = next((x for x in rows if str(x.get("公司代號", "")).strip() == config.SYMBOL), None)
    if not row:
        warnings.append("twse 月營收: 找不到 2344")
        return None

    def find(*needles, exclude=()):
        for k, v in row.items():
            if all(n in k for n in needles) and not any(x in k for x in exclude):
                return v
        return None

    return {
        "month": row.get("資料年月"),  # 民國年月，如 11505
        "value_kntd": _to_int(find("當月營收", exclude=("去年", "累計"))),  # 仟元
        "yoy": _flt(find("去年同月增減")),
        "mom": _flt(find("上月比較增減")),
    }


def build(warnings: list[str], trading_date: str | None = None) -> dict:
    # 不指定日期，讓 TWSE 回傳最新已公布交易日（三大法人/融資融券通常於盤後傍晚才更新，
    # 6am 執行時最新可得即為前一交易日；強制帶當日反而會在尚未公布時取不到資料）。
    inst = fetch_institutional(warnings)
    margin = fetch_margin(warnings)
    return {
        "institutional": {k: inst[k] for k in ("foreign_net", "trust_net", "dealer_net", "total_net")},
        "margin": {k: margin[k] for k in ("margin_balance", "margin_chg", "short_balance", "short_chg")},
        "data_date": inst.get("data_date") or margin.get("data_date"),
    }


if __name__ == "__main__":
    import json
    w: list[str] = []
    print(json.dumps(build(w, "2026-06-23"), ensure_ascii=False, indent=2))
    print("revenue:", json.dumps(fetch_monthly_revenue(w), ensure_ascii=False))
    print("warnings:", w)
