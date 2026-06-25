"""富邦 DJ「個股主力進出」（券商分點買賣超）。第八面資料來源。

頁面為 Big5 靜態 HTML，每列 10 欄：
  左半(0-4) 買超分點：券商 | 買進 | 賣出 | 買賣超 | 佔比%
  右半(5-9) 賣超分點：券商 | 買進 | 賣出 | 買賣超 | 佔比%
net_lots 一律以「買進-賣出」計（買超側為正、賣超側為負），單位：張。

防禦式解析：憑證 SKI 問題須 verify=False；表結構/欄位抓不到回空 list、記 warning、不中斷。
DJ 頁僅提供最新一個交易日（D-1 盤後公布），歷史無法回補 -> 靠每日累積。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

import requests
import urllib3

import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")


class _TableParser(HTMLParser):
    """抽取所有 <tr> 的 <td> 文字（去空白）。"""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._cur: list[str] = []
        self._cell = False
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cur = []
        elif tag == "td":
            self._cell = True
            self._buf = ""

    def handle_endtag(self, tag):
        if tag == "td" and self._cell:
            self._cur.append(re.sub(r"\s+", "", self._buf))
            self._cell = False
        elif tag == "tr":
            if self._cur:
                self.rows.append(self._cur)
            self._cur = []

    def handle_data(self, data):
        if self._cell:
            self._buf += data


def _num(s: str) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _norm_date(text: str) -> str | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def parse(html: str, warnings: list[str] | None = None) -> dict:
    """解析 DJ zco HTML -> {"date": YYYY-MM-DD|None, "rows": [{branch,buy_lots,sell_lots,net_lots}]}。"""
    warnings = warnings if warnings is not None else []
    p = _TableParser()
    p.feed(html)
    date = _norm_date(html)
    rows: list[dict] = []
    seen: set[str] = set()

    def add(branch, buy, sell):
        b = (branch or "").strip()
        buy_l, sell_l = _num(buy), _num(sell)
        if not b or buy_l is None or sell_l is None:
            return
        if b in seen:
            return
        seen.add(b)
        rows.append({"branch": b, "buy_lots": buy_l, "sell_lots": sell_l,
                     "net_lots": round(buy_l - sell_l, 1)})

    for r in p.rows:
        # 資料列：10 欄且左右兩半的佔比欄含 '%'
        if len(r) >= 10 and "%" in r[4] and "%" in r[9]:
            add(r[0], r[1], r[2])   # 買超側
            add(r[5], r[6], r[7])   # 賣超側
    if not rows:
        warnings.append("dj: 解析不到分點資料列（頁面結構可能調整）")
    return {"date": date, "rows": rows}


def fetch_branches(date: str | None = None, warnings: list[str] | None = None) -> dict:
    """抓取並解析 DJ 主力進出頁。回傳 {"date","rows"}；失敗回空 rows。

    date 為 None -> 最新一交易日；給 YYYY-MM-DD -> 指定歷史日（DJ 約保留近半年，
    逾期或非交易日回空 rows；out-of-range 時頁面可能退回最新日，呼叫端須以 date 比對把關）。
    """
    warnings = warnings if warnings is not None else []
    params = {"a": config.SYMBOL, "e": date, "f": date} if date else None
    try:
        r = requests.get(
            config.DJ_CHIPS_URL,
            params=params,
            headers={"User-Agent": config.USER_AGENT,
                     "Referer": "https://fubon-ebrokerdj.fbs.com.tw/"},
            timeout=20,
            verify=False,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        warnings.append(f"dj: 抓取失敗 {e}")
        return {"date": None, "rows": []}
    html = r.content.decode("big5", errors="ignore")
    return parse(html, warnings)


if __name__ == "__main__":
    import sys
    w: list[str] = []
    date = sys.argv[1] if len(sys.argv) > 1 else None   # 可選：YYYY-MM-DD 指定歷史日
    out = fetch_branches(date, w)
    print(f"req={date}  date={out['date']}  分點數={len(out['rows'])}")
    for row in out["rows"][:6]:
        print("  ", row)
    print("warnings:", w)
