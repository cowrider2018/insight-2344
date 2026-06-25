"""時間軸資料庫（SQLite：data/market.db）。

累積四面的點時序資料，讓任一歷史斷點可低成本查詢、免重複爬取：
- news     消息面時間軸（帶精確 published_at 時戳）
- chips     籌碼面（每交易日三大法人/融資融券）
- revenue   月營收
- candles   日 K（Fugle 一次抓的整年，快取免重抓）

所有 upsert 皆冪等（INSERT OR REPLACE / 以主鍵去重），重跑不會重複。
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import config

DB_PATH = config.DATA_DIR / "market.db"

# 標題正規化：與 scrape_cmoney 去重邏輯一致（去空白與標點）
_NORM_RE = re.compile(r"[\s\[\]（）()，,。、：:！!？?]")


def norm_title(title: str | None) -> str:
    return _NORM_RE.sub("", title or "")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id           TEXT PRIMARY KEY,         -- 來源 url 或 article id，無則 symbol|title_norm
    symbol       TEXT NOT NULL,
    title        TEXT,
    title_norm   TEXT,
    source       TEXT,
    url          TEXT,
    published_at TEXT,                      -- ISO8601（含時區）
    bull_or_bear INTEGER,                   -- 0 未表態 1 看多 2 看空
    ingested_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_pub ON news(symbol, published_at);
CREATE INDEX IF NOT EXISTS idx_news_norm ON news(symbol, title_norm);

CREATE TABLE IF NOT EXISTS chips (
    symbol         TEXT NOT NULL,
    data_date      TEXT NOT NULL,           -- YYYYMMDD（TWSE 回傳格式）或 YYYY-MM-DD，統一存 YYYY-MM-DD
    foreign_net    REAL,
    trust_net      REAL,
    dealer_net     REAL,
    total_net      REAL,
    margin_balance INTEGER,
    margin_chg     INTEGER,
    short_balance  INTEGER,
    short_chg      INTEGER,
    PRIMARY KEY (symbol, data_date)
);

CREATE TABLE IF NOT EXISTS revenue (
    symbol     TEXT NOT NULL,
    month      TEXT NOT NULL,               -- 民國年月，如 11505
    value_kntd INTEGER,
    yoy        REAL,
    mom        REAL,
    PRIMARY KEY (symbol, month)
);

CREATE TABLE IF NOT EXISTS candles (
    symbol   TEXT NOT NULL,
    date     TEXT NOT NULL,                 -- YYYY-MM-DD
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   INTEGER,
    turnover INTEGER,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS us_market (
    symbol     TEXT NOT NULL,               -- micron / sox
    date       TEXT NOT NULL,               -- 美股交易日 YYYY-MM-DD
    close      REAL,
    change_pct REAL,
    PRIMARY KEY (symbol, date)
);
"""


@contextmanager
def connect(db_path: Path | str | None = None):
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def _norm_date(d: str | None) -> str | None:
    """把 YYYYMMDD / YYYY-MM-DD / 民國 RWD 日期統一成 YYYY-MM-DD。"""
    if not d:
        return None
    s = str(d).strip().replace("/", "-")
    if re.fullmatch(r"\d{8}", s):  # YYYYMMDD
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


# ---- upsert ----

def upsert_news(conn: sqlite3.Connection, items: Iterable[dict], symbol: str, ingested_at: str) -> int:
    n = 0
    for it in items:
        title = it.get("title")
        url = it.get("url")
        nid = url or f"{symbol}|{norm_title(title)}"
        conn.execute(
            """INSERT OR IGNORE INTO news
               (id, symbol, title, title_norm, source, url, published_at, bull_or_bear, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                nid, symbol, title, norm_title(title), it.get("source"), url,
                it.get("published_at"), it.get("bull_or_bear"), ingested_at,
            ),
        )
        n += 1
    return n


def upsert_chips(conn: sqlite3.Connection, symbol: str, data_date: str | None,
                 institutional: dict, margin: dict) -> None:
    dd = _norm_date(data_date)
    if not dd:
        return
    conn.execute(
        """INSERT OR REPLACE INTO chips
           (symbol, data_date, foreign_net, trust_net, dealer_net, total_net,
            margin_balance, margin_chg, short_balance, short_chg)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            symbol, dd,
            institutional.get("foreign_net"), institutional.get("trust_net"),
            institutional.get("dealer_net"), institutional.get("total_net"),
            margin.get("margin_balance"), margin.get("margin_chg"),
            margin.get("short_balance"), margin.get("short_chg"),
        ),
    )


def upsert_revenue(conn: sqlite3.Connection, symbol: str, rev: dict) -> None:
    if not rev or not rev.get("month"):
        return
    conn.execute(
        """INSERT OR REPLACE INTO revenue (symbol, month, value_kntd, yoy, mom)
           VALUES (?,?,?,?,?)""",
        (symbol, str(rev["month"]), rev.get("value_kntd"), rev.get("yoy"), rev.get("mom")),
    )


def upsert_candles(conn: sqlite3.Connection, symbol: str, candles: Iterable[dict]) -> int:
    n = 0
    for c in candles:
        conn.execute(
            """INSERT OR REPLACE INTO candles
               (symbol, date, open, high, low, close, volume, turnover)
               VALUES (?,?,?,?,?,?,?,?)""",
            (symbol, c["date"], c.get("open"), c.get("high"), c.get("low"),
             c.get("close"), c.get("volume"), c.get("turnover")),
        )
        n += 1
    return n


def upsert_us(conn: sqlite3.Connection, symbol_key: str, rows: Iterable[dict]) -> int:
    n = 0
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO us_market (symbol, date, close, change_pct)
               VALUES (?,?,?,?)""",
            (symbol_key, r["date"], r.get("close"), r.get("change_pct")),
        )
        n += 1
    return n


# ---- query ----

def news_in_window(conn: sqlite3.Connection, symbol: str, start_iso: str, end_iso: str) -> list[dict]:
    """published_at ∈ [start_iso, end_iso)。ISO8601 字串比較對含時區同偏移之時戳有效。"""
    rows = conn.execute(
        """SELECT title, source, url, published_at, bull_or_bear FROM news
           WHERE symbol = ? AND published_at >= ? AND published_at < ?
           ORDER BY published_at""",
        (symbol, start_iso, end_iso),
    ).fetchall()
    return [dict(r) for r in rows]


def chips_asof(conn: sqlite3.Connection, symbol: str, before_date: str) -> dict | None:
    """取 data_date < before_date（YYYY-MM-DD）的最後一筆籌碼（盤前可得的最新籌碼）。"""
    row = conn.execute(
        """SELECT * FROM chips WHERE symbol = ? AND data_date < ?
           ORDER BY data_date DESC LIMIT 1""",
        (symbol, before_date),
    ).fetchone()
    return dict(row) if row else None


def revenue_asof(conn: sqlite3.Connection, symbol: str, roc_month_max: str) -> dict | None:
    """取 month <= roc_month_max（民國年月字串，如 11505）的最新月營收。"""
    row = conn.execute(
        """SELECT * FROM revenue WHERE symbol = ? AND month <= ?
           ORDER BY month DESC LIMIT 1""",
        (symbol, roc_month_max),
    ).fetchone()
    return dict(row) if row else None


def candles_upto(conn: sqlite3.Connection, symbol: str, end_date: str | None = None) -> list[dict]:
    """由舊到新的 candle list；end_date（含）以前。"""
    if end_date:
        rows = conn.execute(
            "SELECT * FROM candles WHERE symbol = ? AND date <= ? ORDER BY date",
            (symbol, end_date),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM candles WHERE symbol = ? ORDER BY date", (symbol,)
        ).fetchall()
    return [dict(r) for r in rows]


def us_asof(conn: sqlite3.Connection, symbol_key: str, before_date: str) -> dict | None:
    """取美股 date < before_date 的最後一筆（台股 D 盤前可得的隔夜值）。"""
    row = conn.execute(
        """SELECT * FROM us_market WHERE symbol = ? AND date < ?
           ORDER BY date DESC LIMIT 1""",
        (symbol_key, before_date),
    ).fetchone()
    return dict(row) if row else None


def counts(conn: sqlite3.Connection) -> dict:
    out = {}
    for t in ("news", "chips", "revenue", "candles", "us_market"):
        out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return out


if __name__ == "__main__":
    import sys
    if "--init" in sys.argv:
        init_db()
        with connect() as c:
            print(f"[timeline_db] 初始化 {DB_PATH}")
            print("  表筆數:", counts(c))
    else:
        with connect() as c:
            print("[timeline_db] 表筆數:", counts(c))
