"""TDCC 集保戶股權分散表（千張大戶持股比率）。第九面資料來源。

兩個來源：
- 當週（每日 build_dataset 用）：TDCC OpenData CSV `id=1-5`（全市場最新一週），篩 config.SYMBOL。
- 歷史回補：smart.tdcc.com.tw 股權分散表查詢（POST scaDate，逐週；約保留近一年週資料）。

持股分級（id=1-5，1~17）：
  1=1~999、…、12=400,001~600,000、13=600,001~800,000、14=800,001~1,000,000、
  15=1,000,001 以上（**千張大戶 ≥1000 張**）、16=差異數調整、17=合計。
分類：big=15；mid=12+13+14（≈400~1000 張中實戶）；retail=1（1~999，零股散戶）。
占比欄為「占集保庫存數比例%」（已是百分數）。

防禦式解析：抓不到回 None、記 warning、不中斷。集保憑證偶有 SKI 問題故 verify=False。
"""
from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser

import requests
import urllib3

import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OPENDATA_URL = "https://opendata.tdcc.com.tw/getOD.ashx"
SMART_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"

# 持股分級 -> 類別（以級距編號彙總）
_BIG_LEVELS = {"15"}
_MID_LEVELS = {"12", "13", "14"}
_RETAIL_LEVELS = {"1"}


def _norm_date(d) -> str | None:
    s = str(d or "").strip().replace("/", "-").replace(".", "-")
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return None


def _pct(s) -> float | None:
    try:
        return float(str(s).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _aggregate(levels: dict[str, float], data_date: str) -> dict:
    """levels: {持股分級編號: 占比%}。回傳 {data_date, big_pct, mid_pct, retail_pct}。"""
    big = sum(v for k, v in levels.items() if k in _BIG_LEVELS)
    mid = sum(v for k, v in levels.items() if k in _MID_LEVELS)
    retail = sum(v for k, v in levels.items() if k in _RETAIL_LEVELS)
    return {"data_date": data_date, "big_pct": round(big, 4),
            "mid_pct": round(mid, 4), "retail_pct": round(retail, 4)}


# ---------------- 當週：OpenData CSV ----------------

def fetch_latest_opendata(warnings: list[str]) -> dict | None:
    """OpenData CSV（全市場最新一週）篩 SYMBOL → 大戶/中實/散戶占比。"""
    try:
        r = requests.get(OPENDATA_URL, params={"id": "1-5"},
                         headers={"User-Agent": config.USER_AGENT}, timeout=60, verify=False)
        r.raise_for_status()
    except requests.RequestException as e:
        warnings.append(f"tdcc opendata 抓取失敗: {e}")
        return None
    text = r.content.decode("utf-8-sig", errors="ignore")
    levels: dict[str, float] = {}
    data_date = None
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # header
    for row in reader:
        if len(row) < 6 or row[1].strip() != config.SYMBOL:
            continue
        data_date = data_date or _norm_date(row[0])
        pct = _pct(row[5])
        if pct is not None:
            levels[row[2].strip()] = pct
    if not levels or not data_date:
        warnings.append("tdcc opendata: 找不到 SYMBOL 資料列")
        return None
    return _aggregate(levels, data_date)


# ---------------- 歷史：smart.tdcc 查詢 ----------------

class _TableParser(HTMLParser):
    """抽取所有 <tr> 的 <td>/<th> 文字（去空白）。"""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._cur: list[str] = []
        self._cell = False
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cur = []
        elif tag in ("td", "th"):
            self._cell = True
            self._buf = ""

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell:
            self._cur.append(re.sub(r"\s+", "", self._buf))
            self._cell = False
        elif tag == "tr":
            if self._cur:
                self.rows.append(self._cur)
            self._cur = []

    def handle_data(self, data):
        if self._cell:
            self._buf += data


def _parse_smart_table(html: str, data_date: str, warnings: list[str]) -> dict | None:
    """smart.tdcc 表列：序(分級編號) | 持股分級 | 人數 | 股數 | 占比%。取 cell[0]=級距、cell[4]=占比。"""
    p = _TableParser()
    p.feed(html)
    levels: dict[str, float] = {}
    for r in p.rows:
        if len(r) < 5 or not re.fullmatch(r"\d{1,2}", r[0].strip()):
            continue
        pct = _pct(r[4])
        if pct is not None:
            levels[r[0].strip()] = pct
    if not levels:
        warnings.append(f"tdcc smart: 解析不到分級資料列（{data_date}）")
        return None
    return _aggregate(levels, data_date)


_TOKEN_RE = re.compile(r'name="SYNCHRONIZER_TOKEN"\s+value="([^"]+)"')
_FIRDATE_RE = re.compile(r'name="firDate"\s+value="([^"]+)"')


def _form_state(session: requests.Session) -> tuple[str | None, str, str]:
    """GET 查詢頁（建立 session cookie），回傳 (SYNCHRONIZER_TOKEN, firDate, html)。

    查詢頁採 synchronizer-token（CSRF），須先 GET 取 token+cookie 才能 POST。
    """
    g = session.get(SMART_URL, timeout=30, verify=False)
    g.raise_for_status()
    tok = _TOKEN_RE.search(g.text)
    fir = _FIRDATE_RE.search(g.text)
    return (tok.group(1) if tok else None, fir.group(1) if fir else "", g.text)


def available_dates(warnings: list[str]) -> list[str]:
    """抓查詢頁資料日期下拉（scaDate options），回傳可用週日期（YYYYMMDD，新到舊、去重）。"""
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": config.USER_AGENT})
        _, _, html = _form_state(s)
    except requests.RequestException as e:
        warnings.append(f"tdcc 可用日期抓取失敗: {e}")
        return []
    # 只取 scaDate <select> 內的 option 值，避免誤抓頁面其他 8 位數字
    block = re.search(r'<select[^>]*name="scaDate".*?</select>', html, re.S)
    text = block.group(0) if block else html
    seen: set[str] = set()
    out: list[str] = []
    for d in re.findall(r'value="(\d{8})"', text):
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _post_query(session: requests.Session, token: str | None, fir: str,
                symbol: str, date_yyyymmdd: str) -> tuple[str, str | None]:
    """POST 一筆 (symbol, date)，回傳 (html, 新 token)。"""
    r = session.post(
        SMART_URL,
        data={"SYNCHRONIZER_TOKEN": token or "",
              "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
              "method": "submit", "firDate": fir,
              "scaDate": date_yyyymmdd, "sqlMethod": "StockNo",
              "stockNo": symbol, "stockName": ""},
        timeout=30, verify=False,
    )
    r.raise_for_status()
    nt = _TOKEN_RE.search(r.text)
    return r.text, (nt.group(1) if nt else None)


def fetch_history(date_yyyymmdd: str, warnings: list[str],
                  session: requests.Session | None = None,
                  symbol: str | None = None) -> dict | None:
    """POST 查詢頁取單一資料日（YYYYMMDD）某股票分散表（synchronizer-token 流程）。"""
    sym = symbol or config.SYMBOL
    try:
        s = session or requests.Session()
        s.headers.update({"User-Agent": config.USER_AGENT})
        token, fir, _ = _form_state(s)
        html, _ = _post_query(s, token, fir, sym, date_yyyymmdd)
    except requests.RequestException as e:
        warnings.append(f"tdcc smart 抓取失敗 {sym} {date_yyyymmdd}: {e}")
        return None
    return _parse_smart_table(html, _norm_date(date_yyyymmdd), warnings)


def fetch_big_pct_history(symbols: list[str], dates: list[str], warnings: list[str],
                          throttle: float = 0.2) -> list[dict]:
    """批次回補多檔多週大戶占比 → [{symbol, data_date, big_pct}]。

    單一 session（共用 cookie）；每檔先 GET 取 token，逐週 POST 並沿用回應中的新 token（synchronizer
    一次性，須輪替）；逐週 parse 不灌 warning（缺資料屬正常）。失敗則重取 token。dates 為 YYYYMMDD。
    """
    import time

    out: list[dict] = []
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT})
    for si, sym in enumerate(symbols, 1):
        try:
            token, fir, _ = _form_state(s)
        except requests.RequestException as e:
            warnings.append(f"tdcc form {sym}: {e}")
            continue
        got = 0
        for d in dates:
            try:
                html, nt = _post_query(s, token, fir, sym, d)
                if nt:
                    token = nt
            except requests.RequestException:
                try:
                    token, fir, _ = _form_state(s)  # 重取 token 續抓
                except requests.RequestException:
                    pass
                continue
            res = _parse_smart_table(html, _norm_date(d), [])  # 不灌 warning
            if res and res.get("big_pct") is not None:
                out.append({"symbol": sym, "data_date": res["data_date"], "big_pct": res["big_pct"]})
                got += 1
            time.sleep(throttle)
        print(f"  [tdcc-hist] {si}/{len(symbols)} {sym}: {got} 週")
    return out


def fetch_holders(date: str | None = None, warnings: list[str] | None = None) -> dict | None:
    """date=None -> OpenData 最新一週；給 YYYYMMDD/YYYY-MM-DD -> smart 歷史查詢。"""
    warnings = warnings if warnings is not None else []
    if date is None:
        return fetch_latest_opendata(warnings)
    return fetch_history(str(date).replace("-", ""), warnings)


if __name__ == "__main__":
    import sys
    w: list[str] = []
    arg = sys.argv[1] if len(sys.argv) > 1 else None  # 可選：YYYYMMDD 指定歷史週
    out = fetch_holders(arg, w)
    print(f"req={arg}  {out}")
    if arg is None:
        print("近可用週（前 6）:", available_dates(w)[:6])
    print("warnings:", w)
