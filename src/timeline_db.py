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

CREATE TABLE IF NOT EXISTS candles_1min (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,                    -- 交易日 YYYY-MM-DD
    time   TEXT NOT NULL,                    -- HH:MM
    open   REAL, high REAL, low REAL, close REAL,
    volume INTEGER,
    PRIMARY KEY (symbol, date, time)
);
CREATE INDEX IF NOT EXISTS idx_1min_date ON candles_1min(symbol, date);

CREATE TABLE IF NOT EXISTS broker_branches (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,                 -- 資料日(D-1 盤後) YYYY-MM-DD
    branch    TEXT NOT NULL,                 -- 券商分點名稱
    buy_lots  REAL,
    sell_lots REAL,
    net_lots  REAL,                          -- 買進-賣出（張），正=買超、負=賣超
    PRIMARY KEY (symbol, date, branch)
);
CREATE INDEX IF NOT EXISTS idx_branch_date ON broker_branches(symbol, date);

CREATE TABLE IF NOT EXISTS branch_wf (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,                    -- 交易日 d；score 由 <=d 資料算、用於預測 d+1
    score  REAL,                             -- walk-forward 主力分點行為分數 [-1,1]
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS tdcc_holders (
    symbol     TEXT NOT NULL,
    data_date  TEXT NOT NULL,                -- 集保資料日(週結算) YYYY-MM-DD
    avail_date TEXT NOT NULL,                -- 公布可得日 = data_date + lag（無 look-ahead 用此比較）
    big_pct    REAL,                         -- 千張大戶(≥1,000,001股)占比%
    mid_pct    REAL,                         -- 400,001~1,000,000股(中實戶)占比%
    retail_pct REAL,                         -- 1~999股(零股散戶)占比%
    PRIMARY KEY (symbol, data_date)
);
CREATE INDEX IF NOT EXISTS idx_tdcc_avail ON tdcc_holders(symbol, avail_date);
"""

# 集保分散表公布 lag（資料日為週結算、隔週才公布）；保守取 +7 天避免 look-ahead
TDCC_LAG_DAYS = 7


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


def _add_days(d_iso: str, days: int) -> str:
    """YYYY-MM-DD + days -> YYYY-MM-DD。"""
    from datetime import date as _date, timedelta
    y, m, dd = (int(x) for x in d_iso.split("-"))
    return (_date(y, m, dd) + timedelta(days=days)).isoformat()


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


def upsert_intraday(conn: sqlite3.Connection, symbol: str, date: str, bars: Iterable[dict]) -> int:
    """寫入單一交易日的 1 分 K bars（冪等）。bars 須含 time/open/high/low/close/volume。"""
    dd = _norm_date(date)
    if not dd:
        return 0
    n = 0
    for b in bars:
        if not b.get("time"):
            continue
        conn.execute(
            """INSERT OR REPLACE INTO candles_1min
               (symbol, date, time, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?,?)""",
            (symbol, dd, b["time"], b.get("open"), b.get("high"),
             b.get("low"), b.get("close"), b.get("volume")),
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


def upsert_branches(conn: sqlite3.Connection, symbol: str, date: str, rows: Iterable[dict]) -> int:
    """寫入單一交易日的券商分點買賣超（冪等）。rows 須含 branch/buy_lots/sell_lots/net_lots。"""
    dd = _norm_date(date)
    if not dd:
        return 0
    n = 0
    for r in rows:
        br = (r.get("branch") or "").strip()
        if not br:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO broker_branches
               (symbol, date, branch, buy_lots, sell_lots, net_lots)
               VALUES (?,?,?,?,?,?)""",
            (symbol, dd, br, r.get("buy_lots"), r.get("sell_lots"), r.get("net_lots")),
        )
        n += 1
    return n


def upsert_branch_wf(conn: sqlite3.Connection, symbol: str, rows: Iterable[dict]) -> int:
    """寫入 walk-forward 主力分點日分數（冪等）。rows 須含 date/score。"""
    n = 0
    for r in rows:
        dd = _norm_date(r.get("date"))
        if not dd:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO branch_wf (symbol, date, score) VALUES (?,?,?)",
            (symbol, dd, r.get("score")),
        )
        n += 1
    return n


def upsert_tdcc(conn: sqlite3.Connection, symbol: str, data_date: str | None,
                big_pct, mid_pct, retail_pct, lag_days: int = TDCC_LAG_DAYS) -> None:
    """寫入單一週集保大戶持股（冪等）。avail_date = data_date + lag（公布可得日）。"""
    dd = _norm_date(data_date)
    if not dd:
        return
    conn.execute(
        """INSERT OR REPLACE INTO tdcc_holders
           (symbol, data_date, avail_date, big_pct, mid_pct, retail_pct)
           VALUES (?,?,?,?,?,?)""",
        (symbol, dd, _add_days(dd, lag_days), big_pct, mid_pct, retail_pct),
    )


def tdcc_asof(conn: sqlite3.Connection, symbol: str, before_date: str) -> dict | None:
    """取 avail_date < before_date 的最新一筆大戶持股，附 big_chg_1w/4w 與 retail_chg_1w。

    以**公布日(avail_date)** 比較，無 look-ahead；變化量取「已公布」週序列往回第 1、4 筆計差。
    """
    rows = conn.execute(
        """SELECT data_date, avail_date, big_pct, mid_pct, retail_pct FROM tdcc_holders
           WHERE symbol = ? AND avail_date < ?
           ORDER BY data_date DESC LIMIT 5""",
        (symbol, before_date),
    ).fetchall()
    if not rows:
        return None
    cur = dict(rows[0])

    def _chg(field: str, i: int):
        if cur.get(field) is None or len(rows) <= i or rows[i][field] is None:
            return None
        return round(cur[field] - rows[i][field], 4)

    cur["big_chg_1w"] = _chg("big_pct", 1)
    cur["big_chg_4w"] = _chg("big_pct", 4)
    cur["retail_chg_1w"] = _chg("retail_pct", 1)
    return cur


def branch_wf_asof(conn: sqlite3.Connection, symbol: str, before_date: str) -> dict | None:
    """取 date < before_date 的最新 walk-forward 分點分數（盤前可得的 D-1 值）。"""
    row = conn.execute(
        """SELECT date, score FROM branch_wf WHERE symbol = ? AND date < ?
           ORDER BY date DESC LIMIT 1""",
        (symbol, before_date),
    ).fetchone()
    return dict(row) if row else None


def branches_asof(conn: sqlite3.Connection, symbol: str, before_date: str) -> list[dict] | None:
    """取 date < before_date 的最新交易日全部分點列（盤前可得的 D-1 主力進出）。

    強制 date < D，無 look-ahead。回傳分點 dict list（依淨額降序），無則 None。
    """
    row = conn.execute(
        "SELECT MAX(date) AS d FROM broker_branches WHERE symbol = ? AND date < ?",
        (symbol, before_date),
    ).fetchone()
    if not row or not row["d"]:
        return None
    rows = conn.execute(
        """SELECT date, branch, buy_lots, sell_lots, net_lots FROM broker_branches
           WHERE symbol = ? AND date = ? ORDER BY net_lots DESC""",
        (symbol, row["d"]),
    ).fetchall()
    return [dict(r) for r in rows] if rows else None


def intraday_asof(conn: sqlite3.Connection, symbol: str, before_date: str) -> list[dict] | None:
    """取 date < before_date 的最新一個交易日全部 1 分 K bars（盤前可得的 D-1 盤中）。

    強制 date < D，無 look-ahead。回傳由早到晚排序的 bars，無則 None。
    """
    row = conn.execute(
        "SELECT MAX(date) AS d FROM candles_1min WHERE symbol = ? AND date < ?",
        (symbol, before_date),
    ).fetchone()
    if not row or not row["d"]:
        return None
    rows = conn.execute(
        """SELECT date, time, open, high, low, close, volume FROM candles_1min
           WHERE symbol = ? AND date = ? ORDER BY time""",
        (symbol, row["d"]),
    ).fetchall()
    return [dict(r) for r in rows] if rows else None


def counts(conn: sqlite3.Connection) -> dict:
    out = {}
    for t in ("news", "chips", "revenue", "candles", "us_market", "candles_1min",
              "broker_branches", "branch_wf", "tdcc_holders"):
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
