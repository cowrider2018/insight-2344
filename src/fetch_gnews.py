"""歷史消息面回補：Google News RSS + `after:`/`before:` 日期運算子。

Google 網頁搜尋（SERP，tbs=cdr 日期後綴）會被反爬 CAPTCHA 當場擋下；改用
news.google.com/rss/search 加 `after:YYYY-MM-DD before:YYYY-MM-DD` 搜尋運算子
（回傳 XML、不觸發 CAPTCHA），逐短視窗抓「華邦電」歷史新聞，標準化為與 scrape_cmoney
相同 schema（title/source/url/published_at/confirmed），灌入 timeline_db.news。

用途：讓 validate_news.py 能以**真實歷史**統計驗證各消息型態的次日效應極性
（取代 scoring 的專家先驗 PRIOR_EDGE），並讓 backtest 的消息面獲得真實覆蓋率。

用法:
    python src/fetch_gnews.py --backfill 2025-01-01 2026-06-30 [--query 華邦電] [--win 2]
"""
from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

import config
import timeline_db as tdb

RSS = "https://news.google.com/rss/search"
WINDOW_DAYS = 2            # 每次查詢的日期視窗（Google News RSS 每查上限約 100 則，短視窗防截斷）
CAP_WARN = 95             # 單視窗回傳逼近上限 -> 可能截斷，發警告


def _pubdate_to_tpe_iso(s: str | None) -> str | None:
    """RFC822 GMT 時戳 -> 台北時區 ISO8601（與 news_in_window 的 +08:00 視窗字串可比較）。"""
    try:
        return parsedate_to_datetime(s).astimezone(config.TZ).isoformat()
    except (TypeError, ValueError):
        return None


def fetch_window(query: str, after: str, before: str, warnings: list[str]) -> list[dict]:
    """抓單一日期視窗 [after, before) 的新聞，回標準化 news dict 清單。"""
    q = f"{query} after:{after} before:{before}"
    url = f"{RSS}?q={quote(q)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    try:
        r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError) as e:
        warnings.append(f"gnews {after}~{before} 失敗: {e}")
        return []
    items = root.findall(".//item")
    if len(items) >= CAP_WARN:
        warnings.append(f"gnews {after}~{before} 回 {len(items)} 則（逼近上限，可能截斷；建議縮小 --win）")
    out = []
    for it in items:
        title = (it.findtext("title") or "").strip()
        iso = _pubdate_to_tpe_iso(it.findtext("pubDate"))
        if not title or iso is None:
            continue
        src_el = it.find("{*}source")
        out.append({
            "title": title,
            "source": (src_el.text if src_el is not None else None) or "google_news",
            "url": it.findtext("link"),
            "published_at": iso,
            "confirmed": True,          # pubDate 為精確時戳
            "age_hours": None,          # 歷史回補不計 age（新聞窗以 published_at 判斷）
            "bull_or_bear": None,       # 極性交由 news_patterns 型態比對（非 CMoney 標記）
        })
    return out


def _windows(start: str, end: str, win: int):
    cur = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while cur <= last:
        nxt = min(cur + timedelta(days=win), last + timedelta(days=1))
        yield cur.isoformat(), nxt.isoformat()
        cur = nxt


def backfill(start: str, end: str, query: str = "華邦電",
             win: int = WINDOW_DAYS, sleep: float = 0.8) -> dict:
    """逐視窗回補 [start, end] 的歷史消息面到 timeline_db.news（norm_title 去重、冪等 upsert）。"""
    tdb.init_db()
    warnings: list[str] = []
    wins = list(_windows(start, end, win))
    print(f"[gnews] 回補 {start}~{end}，共 {len(wins)} 個 {win} 日視窗（Google News RSS，逐視窗抓）")
    seen: set[str] = set()
    total_items = ingested = 0
    ingested_at = config.now_tpe().isoformat()
    with tdb.connect() as conn:
        for k, (a, b) in enumerate(wins, 1):
            items = fetch_window(query, a, b, warnings)
            fresh = []
            for it in items:
                key = tdb.norm_title(it["title"])
                if key and key not in seen:
                    seen.add(key)
                    fresh.append(it)
            total_items += len(items)
            ingested += tdb.upsert_news(conn, fresh, config.SYMBOL, ingested_at)
            if k % 20 == 0:
                conn.commit()
                print(f"  ...{k}/{len(wins)}（{a}）抓{total_items} 入庫{ingested}")
            time.sleep(sleep)
    print(f"[gnews] 完成：抓 {total_items} 則、去重入庫 {ingested} 則（{start}~{end}）")
    if warnings:
        print(f"  warnings: {len(warnings)} 則（截斷/連線；前3：{warnings[:3]}）")
    return {"fetched": total_items, "ingested": ingested, "warnings": len(warnings)}


def main(argv: list[str]) -> None:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    if "--backfill" in argv:
        i = argv.index("--backfill")
        start = argv[i + 1]
        end = argv[i + 2]
        backfill(start, end, opt("--query", "華邦電"), int(opt("--win", str(WINDOW_DAYS))))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
