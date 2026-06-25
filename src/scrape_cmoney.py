"""CMoney 消息面：以 Playwright 渲染論壇/新聞分頁，攔截其內部 JSON API。

關鍵端點（無需登入即回 JSON）：
- /api/mach/api/Article/GetChannelsArticleByWeight  -> 文章/新聞清單（含精確 createTime 毫秒、來源 URL、commodityTags）
- /api/mach/api/Channel/ArticlesCount/Today          -> 今日該股文章數（情緒參考）

新聞發布時間取自文章 createTime（epoch 毫秒），故均為「嚴格確認」(confirmed=True)。
只保留 commodityTags 含 2344 的文章。任何失敗都回 partial，不中斷整體流程。
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

import config

# bullOrBear: 0=未表態 1=看多 2=看空
_BULL, _BEAR = 1, 2


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return None


def _epoch_ms_to_iso(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=config.TZ).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def scrape(warnings: list[str]) -> dict:
    result = {"news": [], "forum_sentiment": {"posts_24h": None, "bullish": 0, "bearish": 0},
              "status": "partial"}
    scrape_dt = config.now_tpe()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        warnings.append("cmoney: 未安裝 playwright（pip install playwright; playwright install chromium）")
        return result

    articles: list = []
    today_count = {"v": None}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(user_agent=config.USER_AGENT, locale="zh-TW").new_page()

            def on_response(resp):
                try:
                    if "json" not in resp.headers.get("content-type", ""):
                        return
                    if "GetChannelsArticleByWeight" in resp.url:
                        j = resp.json()
                        if isinstance(j, list):
                            articles.extend(j)
                    elif "ArticlesCount/Today" in resp.url:
                        j = resp.json()
                        if isinstance(j, dict) and "count" in j:
                            today_count["v"] = j["count"]
                except Exception:
                    pass

            page.on("response", on_response)
            page.goto(f"{config.CMONEY_FORUM_URL}?tab=news", wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(4000)
            for _ in range(3):  # 捲動載入更多文章
                try:
                    page.mouse.wheel(0, 6000)
                    page.wait_for_timeout(2000)
                except Exception:
                    break
            browser.close()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"cmoney 渲染失敗: {e}")
        return result

    result["forum_sentiment"]["posts_24h"] = today_count["v"]

    import re

    seen_ids = set()
    seen_titles = set()
    news = []
    bull = bear = 0
    for art in articles:
        aid = art.get("id")
        content = art.get("content") or {}
        title = (content.get("title") or "").strip()
        tags = content.get("commodityTags") or []
        tag = next((t for t in tags if str(t.get("key")) == config.SYMBOL), None)
        norm = re.sub(r"[\s\[\]（）()，,。、：:！!？?]", "", title)
        if not title or tag is None or aid in seen_ids or norm in seen_titles:
            continue
        seen_ids.add(aid)
        seen_titles.add(norm)

        if tag.get("bullOrBear") == _BULL:
            bull += 1
        elif tag.get("bullOrBear") == _BEAR:
            bear += 1

        iso = _epoch_ms_to_iso(art.get("createTime"))
        if iso is None:
            continue
        src_url = next((m.get("url") for m in content.get("multiMedia", [])
                        if m.get("mediaType") == "source"), None)
        url = src_url or f"https://www.cmoney.tw/forum/article/{aid}"
        age = round((scrape_dt - datetime.fromisoformat(iso)).total_seconds() / 3600, 1)
        news.append({
            "title": title,
            "source": _domain(src_url) or "cmoney論壇",
            "url": url,
            "published_at": iso,
            "confirmed": True,            # createTime 為精確毫秒時戳
            "age_hours": age,
            "bull_or_bear": tag.get("bullOrBear"),
        })

    news.sort(key=lambda n: n["published_at"], reverse=True)
    result["news"] = news[:40]
    result["forum_sentiment"]["bullish"] = bull
    result["forum_sentiment"]["bearish"] = bear
    if news:
        result["status"] = "ok"
    else:
        warnings.append("cmoney: 未擷取到 2344 相關文章（API 結構可能調整）")
    return result


if __name__ == "__main__":
    import json
    w: list[str] = []
    r = scrape(w)
    print("status:", r["status"], "| news:", len(r["news"]), "| sentiment:", r["forum_sentiment"])
    print(json.dumps(r["news"][:6], ensure_ascii=False, indent=2))
    print("warnings:", w)
