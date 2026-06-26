"""橫斷面多股子系統的獨立資料庫（SQLite：data/xs.db）。

與單股 production 的 market.db **完全分離**（低耦合）：多 symbol 的日 K 收盤、三大法人淨額、
TDCC 大戶占比。供 xs_signals / xs_backtest 做跨股排序與 IC / 多空回測。

所有 upsert 皆冪等（INSERT OR REPLACE）。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import config

DB_PATH = config.DATA_DIR / "xs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS xs_candles (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,                    -- YYYY-MM-DD
    close  REAL,
    volume REAL,                             -- 成交張數
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS xs_chips (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,               -- 交易日 YYYY-MM-DD（盤後公布）
    foreign_net REAL,                        -- 外資買賣超（張）
    total_net   REAL,                        -- 三大法人買賣超（張）
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS xs_tdcc (
    symbol    TEXT NOT NULL,
    data_date TEXT NOT NULL,                 -- 集保資料日（週）YYYY-MM-DD
    big_pct   REAL,                          -- 千張大戶占比%
    PRIMARY KEY (symbol, data_date)
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


def upsert_candles(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    n = 0
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO xs_candles (symbol, date, close, volume) VALUES (?,?,?,?)",
            (r["symbol"], r["date"], r.get("close"), r.get("volume")),
        )
        n += 1
    return n


def upsert_chips(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    n = 0
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO xs_chips (symbol, date, foreign_net, total_net) VALUES (?,?,?,?)",
            (r["symbol"], r["date"], r.get("foreign_net"), r.get("total_net")),
        )
        n += 1
    return n


def upsert_tdcc(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    n = 0
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO xs_tdcc (symbol, data_date, big_pct) VALUES (?,?,?)",
            (r["symbol"], r["data_date"], r.get("big_pct")),
        )
        n += 1
    return n


def load_panel(conn: sqlite3.Connection) -> tuple[dict, dict, dict, list[str]]:
    """回傳 (closes, flows, vols, dates)。

    closes[symbol][date] = 收盤價；
    flows[symbol][date]  = 三大法人淨額/成交量（張/張，跨股可比的籌碼流入強度）；
    vols[symbol][date]   = 成交張數（供回測做每日流動性篩選）；
    dates = 有任一收盤的交易日（由舊到新）。
    """
    closes: dict[str, dict[str, float]] = {}
    vols: dict[str, dict[str, float]] = {}
    date_set: set[str] = set()
    for r in conn.execute("SELECT symbol, date, close, volume FROM xs_candles"):
        if r["close"] is None:
            continue
        closes.setdefault(r["symbol"], {})[r["date"]] = r["close"]
        vols.setdefault(r["symbol"], {})[r["date"]] = r["volume"]
        date_set.add(r["date"])

    flows: dict[str, dict[str, float]] = {}
    for r in conn.execute("SELECT symbol, date, total_net FROM xs_chips"):
        net = r["total_net"]
        v = vols.get(r["symbol"], {}).get(r["date"])
        if net is None or not v:
            continue
        flows.setdefault(r["symbol"], {})[r["date"]] = net / v
    return closes, flows, vols, sorted(date_set)


def load_foreign_flows(conn: sqlite3.Connection) -> dict:
    """fflows[symbol][date] = 外資買賣超/成交量（跨股可比的外資流入強度，第二因子用）。"""
    vols: dict[str, dict[str, float]] = {}
    for r in conn.execute("SELECT symbol, date, volume FROM xs_candles"):
        if r["volume"]:
            vols.setdefault(r["symbol"], {})[r["date"]] = r["volume"]
    ff: dict[str, dict[str, float]] = {}
    for r in conn.execute("SELECT symbol, date, foreign_net FROM xs_chips"):
        v = vols.get(r["symbol"], {}).get(r["date"])
        if r["foreign_net"] is None or not v:
            continue
        ff.setdefault(r["symbol"], {})[r["date"]] = r["foreign_net"] / v
    return ff


def load_tdcc_series(conn: sqlite3.Connection) -> dict:
    """回傳 {symbol: [(data_date, big_pct), ...]}（依週日期升冪），供大戶週變化因子。"""
    per: dict[str, list] = {}
    for r in conn.execute(
        "SELECT symbol, data_date, big_pct FROM xs_tdcc WHERE big_pct IS NOT NULL "
        "ORDER BY symbol, data_date"
    ):
        per.setdefault(r["symbol"], []).append((r["data_date"], r["big_pct"]))
    return per


def counts(conn: sqlite3.Connection) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("xs_candles", "xs_chips", "xs_tdcc")}


if __name__ == "__main__":
    init_db()
    with connect() as c:
        print("[xs_db] 表筆數:", counts(c))
