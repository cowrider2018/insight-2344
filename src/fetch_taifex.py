"""TAIFEX 三大法人－區分各期貨契約：外資台指期淨未平倉口數。第十面資料來源。

市場級 regime 訊號：外資在臺股期貨(TXF)的「多空未平倉口數淨額」反映外資對大盤方向的部位，
影響整個盤含 2344（記憶體族群高 beta）。每日盤後(約 15:00)公布。

以 CSV 下載端點一次取整段區間（big5 / MS950 編碼），欄位以**表頭名稱**定位（fallback 索引 13）。
防禦式解析：抓不到回空/None、記 warning、不中斷。
"""
from __future__ import annotations

import csv
import io
import re

import requests
import urllib3

import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOWNLOAD_URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
COMMODITY = "TXF"                  # 臺股期貨
MARKET_KEY = "tx"                  # timeline_db futures_oi 的 market 鍵
_NET_OI_HEADER = "未平倉口數淨額"   # 「多空未平倉口數淨額」（口）
_NET_OI_IDX_FALLBACK = 13
_FOREIGN_KW = "外資"               # 身份別「外資及陸資」


def _to_int(s) -> int | None:
    try:
        return int(float(str(s).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _norm_date(s) -> str | None:
    s = str(s or "").strip().replace("/", "-")
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


def _parse_csv(text: str) -> list[dict]:
    """解析 TAIFEX CSV → [{date, foreign_net_oi}]（只取外資×臺股期貨列），由舊到新。"""
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return []
    hdr = rows[0]
    oi_idx = next((i for i, h in enumerate(hdr) if _NET_OI_HEADER in h), _NET_OI_IDX_FALLBACK)
    out: list[dict] = []
    for r in rows[1:]:
        if len(r) <= oi_idx or len(r) < 3 or _FOREIGN_KW not in r[2]:
            continue
        d = _norm_date(r[0])
        net = _to_int(r[oi_idx])
        if d and net is not None:
            out.append({"date": d, "foreign_net_oi": net})
    out.sort(key=lambda x: x["date"])
    return out


def fetch_range(start: str, end: str, warnings: list[str]) -> list[dict]:
    """抓 [start, end]（YYYY-MM-DD）外資台指期淨未平倉，一次 CSV 取回整段。"""
    try:
        r = requests.post(
            DOWNLOAD_URL,
            data={"queryStartDate": start.replace("-", "/"),
                  "queryEndDate": end.replace("-", "/"), "commodityId": COMMODITY},
            headers={"User-Agent": config.USER_AGENT}, timeout=30, verify=False,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        warnings.append(f"taifex 抓取失敗 {start}~{end}: {e}")
        return []
    return _parse_csv(r.content.decode("big5", errors="ignore"))


def fetch_oi(date: str | None = None, warnings: list[str] | None = None) -> dict | None:
    """date=None -> 取近 10 日最新一筆（盤前可得的 D-1）；給 YYYY-MM-DD -> 該日。"""
    import datetime
    warnings = warnings if warnings is not None else []
    if date is None:
        today = config.now_tpe().date()
        start = (today - datetime.timedelta(days=10)).isoformat()
        rows = fetch_range(start, today.isoformat(), warnings)
        return rows[-1] if rows else None
    rows = fetch_range(date, date, warnings)
    return rows[-1] if rows else None


if __name__ == "__main__":
    import sys
    w: list[str] = []
    if len(sys.argv) > 2:
        out = fetch_range(sys.argv[1], sys.argv[2], w)
        print(f"range {sys.argv[1]}~{sys.argv[2]}: {len(out)} 日")
        for r in out[-3:]:
            print("  ", r)
    else:
        print("latest:", fetch_oi(None, w))
    print("warnings:", w)
