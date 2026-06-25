"""攝取與回補：把四面點時序資料灌入 timeline_db（data/market.db）。

- ingest_dataset(dataset)：把一份 build_dataset 產物（news/chips/revenue/candles）寫入各表。
- backfill_from_json()：掃既有 data/2344_*.json 快照灌 news / chips / revenue / candles（重建時間軸起點）。
- backfill_candles()：Fugle 一次抓整年日 K 灌 candles 表。
- backfill_chips(start, end)：對 candles 既有「交易日」逐日呼叫 TWSE（date 參數），只抓一次。

用法：
    python src/ingest.py --backfill-json
    python src/ingest.py --backfill-candles
    python src/ingest.py --backfill-chips 2025-06-24 2026-06-24
"""
from __future__ import annotations

import json
import sys

import config
import timeline_db as tdb


def ingest_dataset(dataset: dict, conn=None) -> dict:
    """把單一 dataset 寫入 DB。可傳入既有 conn 以共用交易。"""
    symbol = dataset.get("symbol", config.SYMBOL)
    ingested_at = config.now_tpe().isoformat()

    def _do(conn):
        stats = {"news": 0, "chips": 0, "revenue": 0, "candles": 0}
        stats["news"] = tdb.upsert_news(conn, dataset.get("news", []) or [], symbol, ingested_at)

        chips = dataset.get("chips") or {}
        inst = chips.get("institutional") or {}
        margin = chips.get("margin") or {}
        if chips.get("data_date") and (any(v is not None for v in inst.values())
                                       or any(v is not None for v in margin.values())):
            tdb.upsert_chips(conn, symbol, chips.get("data_date"), inst, margin)
            stats["chips"] = 1

        rev = (dataset.get("fundamental") or {}).get("monthly_revenue")
        if rev:
            tdb.upsert_revenue(conn, symbol, rev)
            stats["revenue"] = 1

        candles = (dataset.get("technical") or {}).get("candles_60d") or []
        stats["candles"] = tdb.upsert_candles(conn, symbol, candles)

        overnight = dataset.get("overnight") or {}
        stats["us"] = 0
        for key, row in overnight.items():
            if row:
                stats["us"] += tdb.upsert_us(conn, key, [row])
        return stats

    if conn is not None:
        return _do(conn)
    with tdb.connect() as c:
        return _do(c)


def backfill_from_json() -> dict:
    tdb.init_db()
    total = {"files": 0, "news": 0, "chips": 0, "revenue": 0, "candles": 0, "us": 0}
    files = sorted(config.DATA_DIR.glob(f"{config.SYMBOL}_*.json"))
    with tdb.connect() as conn:
        for f in files:
            try:
                ds = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"  跳過 {f.name}: {e}")
                continue
            s = ingest_dataset(ds, conn)
            total["files"] += 1
            for k in ("news", "chips", "revenue", "candles", "us"):
                total[k] += s.get(k, 0)
    print(f"[backfill_from_json] {total['files']} 檔 -> news+{total['news']} "
          f"chips+{total['chips']} revenue+{total['revenue']} candles+{total['candles']} us+{total['us']}")
    return total


def backfill_us(range_: str = "1y") -> int:
    """Yahoo 一次抓 MU / SOX 近一年日線灌 us_market 表。"""
    import fetch_us
    tdb.init_db()
    total = 0
    with tdb.connect() as conn:
        for key in fetch_us.US_SYMBOLS:
            try:
                rows = fetch_us.fetch_yahoo_daily(fetch_us.US_SYMBOLS[key], range_)
                n = tdb.upsert_us(conn, key, rows)
                total += n
                print(f"[backfill_us] {key}: upsert {n}（{rows[0]['date'] if rows else '-'} ~ "
                      f"{rows[-1]['date'] if rows else '-'}）")
            except Exception as e:  # noqa: BLE001
                print(f"[backfill_us] {key} 失敗: {e}")
    return total


def backfill_candles() -> int:
    import fetch_fugle
    tdb.init_db()
    candles = fetch_fugle.fetch_candles()
    with tdb.connect() as conn:
        n = tdb.upsert_candles(conn, config.SYMBOL, candles)
    print(f"[backfill_candles] candles upsert {n}（{candles[0]['date'] if candles else '-'} ~ "
          f"{candles[-1]['date'] if candles else '-'}）")
    return n


def backfill_chips(start: str | None = None, end: str | None = None) -> int:
    """對 candles 表中 [start, end] 的交易日逐日抓 TWSE 籌碼（date 參數），只抓尚未存在者。

    start/end 省略時自動取 candles 表的最早/最新交易日（方便一鍵回補）。
    """
    import fetch_twse
    tdb.init_db()
    warnings: list[str] = []
    fetched = 0
    with tdb.connect() as conn:
        all_dates = [r["date"] for r in tdb.candles_upto(conn, config.SYMBOL)]
        if not all_dates:
            print("[backfill_chips] candles 表為空，請先 --backfill-candles")
            return 0
        start = start or all_dates[0]
        end = end or all_dates[-1]
        dates = [d for d in all_dates if start <= d <= end]
        if not dates:
            print("[backfill_chips] candles 表無此區間交易日，請先 --backfill-candles")
            return 0
        print(f"[backfill_chips] 區間 {start} ~ {end}，共 {len(dates)} 交易日（逐日抓 TWSE，較慢）")
        existing = {r["data_date"] for r in conn.execute(
            "SELECT data_date FROM chips WHERE symbol = ?", (config.SYMBOL,)).fetchall()}
        for d in dates:
            if d in existing:
                continue
            ymd = d.replace("-", "")  # TWSE date 參數要 YYYYMMDD
            inst = fetch_twse.fetch_institutional(warnings, ymd)
            margin = fetch_twse.fetch_margin(warnings, ymd)
            if inst.get("total_net") is None and margin.get("margin_balance") is None:
                continue  # 該日無資料（假日/未更新）
            tdb.upsert_chips(conn, config.SYMBOL, d, inst, margin)
            fetched += 1
            if fetched % 20 == 0:
                conn.commit()
                print(f"  ...已抓 {fetched} 日（最新 {d}）")
    print(f"[backfill_chips] 新增 {fetched} 個交易日籌碼")
    if warnings:
        print(f"  warnings: {len(warnings)} 則（多為假日/尚未公布，可忽略）")
    return fetched


def main(argv: list[str]) -> None:
    if "--backfill-json" in argv:
        backfill_from_json()
    if "--backfill-candles" in argv:
        backfill_candles()
    if "--backfill-us" in argv:
        backfill_us()
    if "--backfill-chips" in argv:
        i = argv.index("--backfill-chips")
        # 可省略日期（自動取 candles 全區間），或給 <start> <end>
        start = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        end = argv[i + 2] if len(argv) > i + 2 and not argv[i + 2].startswith("--") else None
        backfill_chips(start, end)
    if len(argv) <= 1:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
