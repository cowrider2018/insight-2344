"""攝取與回補：把四面點時序資料灌入 timeline_db（data/market.db）。

- ingest_dataset(dataset)：把一份 build_dataset 產物（news/chips/revenue/candles）寫入各表。
- backfill_from_json()：掃既有 data/2344_*.json 快照灌 news / chips / revenue / candles（重建時間軸起點）。
- backfill_candles()：Fugle 一次抓整年日 K 灌 candles 表。
- backfill_intraday()：由最新交易日往回抓 Fugle 1 分 K 灌 candles_1min（自動探知保留期；第七面冷啟動）。
- backfill_branches()：抓富邦 DJ 主力進出當前頁（僅最新一日）灌 broker_branches（第八面冷啟動）。
- backfill_branches_history(start, end)：以日期參數逐交易日回補 DJ 分點歷史（約近半年）。
- backfill_chips(start, end)：對 candles 既有「交易日」逐日呼叫 TWSE（date 參數），只抓一次。

用法：
    python src/ingest.py --backfill-json
    python src/ingest.py --backfill-candles
    python src/ingest.py --backfill-intraday
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
        stats = {"news": 0, "chips": 0, "revenue": 0, "candles": 0, "us": 0,
                 "intraday": 0, "branch": 0, "holders": 0, "futures": 0}
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

        # 第七面：當日 1 分 K（Fugle 只留近 30 日，須每日累積才不流失歷史）
        intraday = dataset.get("intraday") or {}
        bars = intraday.get("bars") or []
        stats["intraday"] = (tdb.upsert_intraday(conn, symbol, intraday["date"], bars)
                             if intraday.get("date") and bars else 0)

        # 第八面：主力分點（DJ 僅最新一日，須每日累積才不流失歷史）
        branch = dataset.get("branch") or {}
        brows = branch.get("rows") or []
        stats["branch"] = (tdb.upsert_branches(conn, symbol, branch["date"], brows)
                           if branch.get("date") and brows else 0)

        # 第九面：TDCC 千張大戶（集保週頻，OpenData 當週；歷史以 backfill_tdcc 回補）
        holders = dataset.get("holders") or {}
        if holders.get("data_date") and holders.get("big_pct") is not None:
            tdb.upsert_tdcc(conn, symbol, holders["data_date"], holders.get("big_pct"),
                            holders.get("mid_pct"), holders.get("retail_pct"))
            stats["holders"] = 1

        # 第十面：外資台指期未平倉（市場級，TAIFEX 每日盤後）
        futures = dataset.get("futures") or {}
        if futures.get("date") and futures.get("foreign_net_oi") is not None:
            stats["futures"] = tdb.upsert_futures_oi(conn, "tx", [futures])
        return stats

    if conn is not None:
        return _do(conn)
    with tdb.connect() as c:
        return _do(c)


def backfill_from_json() -> dict:
    tdb.init_db()
    total = {"files": 0, "news": 0, "chips": 0, "revenue": 0, "candles": 0, "us": 0}
    files = sorted(config.SYMBOL_DIR.glob(f"{config.SYMBOL}_*.json"))
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


def backfill_intraday(max_empty: int = 3) -> int:
    """由最新交易日往回抓 Fugle 1 分 K 灌 candles_1min，直到連續 max_empty 日無資料為止。

    Fugle 只保留近期（約 30+ 個交易日）盤中資料，更早回空 -> 連續數日空即停（自動探知保留期）。
    冷啟動只能取到 API 當下保留的範圍；之後靠每日 build_dataset 累積、可跨越保留期成長。
    """
    import fetch_fugle
    tdb.init_db()
    total = days = empty = 0
    with tdb.connect() as conn:
        dates = [r["date"] for r in tdb.candles_upto(conn, config.SYMBOL)]
        if not dates:
            print("[backfill_intraday] candles 表為空，請先 --backfill-candles")
            return 0
        existing = {r["date"] for r in conn.execute(
            "SELECT DISTINCT date FROM candles_1min WHERE symbol = ?", (config.SYMBOL,)).fetchall()}
        for d in reversed(dates):           # 由新到舊
            if d in existing:
                empty = 0                   # 已有資料：視為仍在保留期內
                continue
            bars = fetch_fugle.fetch_intraday_candles(d)
            if not bars:
                empty += 1
                if empty >= max_empty:
                    break                   # 連續數日空 -> 已越過 API 保留期
                continue
            empty = 0
            n = tdb.upsert_intraday(conn, config.SYMBOL, d, bars)
            total += n
            days += 1
            if days % 5 == 0:
                conn.commit()
                print(f"  ...已抓 {days} 個交易日（最新未抓到 {d}）")
    print(f"[backfill_intraday] 新增 {days} 個交易日 1 分 K，共 upsert {total} 根")
    return total


def backfill_branches() -> int:
    """抓富邦 DJ 主力進出當前頁（僅最新一交易日）灌 broker_branches。

    DJ 只提供最新一日、歷史無法回補，故此為「冷啟動先存一天」；之後靠每日 build_dataset 累積。
    """
    import fetch_dj_chips
    tdb.init_db()
    warnings: list[str] = []
    out = fetch_dj_chips.fetch_branches(warnings)
    rows = out.get("rows") or []
    if not (out.get("date") and rows):
        print(f"[backfill_branches] 無資料（{out.get('date')}）；warnings: {warnings}")
        return 0
    with tdb.connect() as conn:
        n = tdb.upsert_branches(conn, config.SYMBOL, out["date"], rows)
    print(f"[backfill_branches] {out['date']} 分點 upsert {n} 筆")
    return n


def backfill_branches_history(start: str | None = None, end: str | None = None,
                              max_empty: int = 8) -> int:
    """以日期參數逐交易日回補 DJ 主力分點歷史（DJ 約保留近半年）。

    迭代 candles 交易日（新到舊），對尚未存在者以 e=f=該日 抓取；**只在解析日==請求日且有資料**
    時寫入（防頁面在逾保留期時退回最新日造成錯置）。連續 max_empty 日無資料即停（已越過保留期）。
    """
    import time

    import fetch_dj_chips
    tdb.init_db()
    total = days = empty = 0
    warnings: list[str] = []
    with tdb.connect() as conn:
        dates = [r["date"] for r in tdb.candles_upto(conn, config.SYMBOL)]
        if not dates:
            print("[backfill_branches_history] candles 表為空，請先 --backfill-candles")
            return 0
        start = start or dates[0]
        end = end or dates[-1]
        existing = {r["date"] for r in conn.execute(
            "SELECT DISTINCT date FROM broker_branches WHERE symbol = ?", (config.SYMBOL,)).fetchall()}
        sel = [d for d in dates if start <= d <= end]
        print(f"[backfill_branches_history] 區間 {start} ~ {end}，共 {len(sel)} 交易日（逐日抓 DJ，較慢）")
        for d in reversed(sel):                       # 由新到舊
            if d in existing:
                empty = 0
                continue
            out = fetch_dj_chips.fetch_branches(d, warnings)
            rows = out.get("rows") or []
            if not rows or out.get("date") != d:      # 無資料／頁面退回非請求日 -> 跳過
                empty += 1
                if empty >= max_empty:
                    break                             # 連續無資料 -> 已越過 DJ 保留期
                continue
            empty = 0
            total += tdb.upsert_branches(conn, config.SYMBOL, d, rows)
            days += 1
            if days % 10 == 0:
                conn.commit()
                print(f"  ...已回補 {days} 個交易日（最新未存 {d}）")
            time.sleep(0.25)                          # 友善節流
    print(f"[backfill_branches_history] 回補 {days} 個交易日、共 {total} 筆分點")
    return total


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


def backfill_tdcc(start: str | None = None, end: str | None = None) -> int:
    """逐週回補 TDCC 集保大戶持股（smart.tdcc 約保留近一年週資料）。

    自查詢頁取可用週日期清單，篩 [start, end]（以資料日比較），只抓尚未存在者。
    """
    import time

    import fetch_tdcc
    tdb.init_db()
    warnings: list[str] = []
    dates = fetch_tdcc.available_dates(warnings)  # YYYYMMDD，新到舊
    if not dates:
        print(f"[backfill_tdcc] 無可用週日期；warnings: {warnings}")
        return 0

    def _iso(d: str) -> str:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"

    sel = [d for d in dates
           if (not start or _iso(d) >= start) and (not end or _iso(d) <= end)]
    total = 0
    with tdb.connect() as conn:
        existing = {r["data_date"] for r in conn.execute(
            "SELECT data_date FROM tdcc_holders WHERE symbol = ?", (config.SYMBOL,)).fetchall()}
        print(f"[backfill_tdcc] 候選 {len(sel)} 週（逐週查 smart.tdcc，較慢）")
        for d in sel:
            if _iso(d) in existing:
                continue
            row = fetch_tdcc.fetch_history(d, warnings)
            if not row or row.get("big_pct") is None:
                continue
            tdb.upsert_tdcc(conn, config.SYMBOL, row["data_date"], row.get("big_pct"),
                            row.get("mid_pct"), row.get("retail_pct"))
            total += 1
            if total % 10 == 0:
                conn.commit()
                print(f"  ...已回補 {total} 週（最新 {row['data_date']}）")
            time.sleep(0.25)  # 友善節流
    print(f"[backfill_tdcc] 回補 {total} 週大戶持股")
    if warnings:
        print(f"  warnings: {len(warnings)} 則")
    return total


def backfill_futures(start: str | None = None, end: str | None = None) -> int:
    """回補外資台指期淨未平倉（TAIFEX CSV 一次取整段；省略日期=取 candles 全區間）。"""
    import fetch_taifex
    tdb.init_db()
    warnings: list[str] = []
    with tdb.connect() as conn:
        all_dates = [r["date"] for r in tdb.candles_upto(conn, config.SYMBOL)]
        start = start or (all_dates[0] if all_dates else "2025-01-01")
        end = end or (all_dates[-1] if all_dates else config.today_str())
        rows = fetch_taifex.fetch_range(start, end, warnings)
        n = tdb.upsert_futures_oi(conn, "tx", rows)
    print(f"[backfill_futures] 區間 {start} ~ {end}，外資台指期未平倉 upsert {n} 日")
    if warnings:
        print(f"  warnings: {warnings}")
    return n


def main(argv: list[str]) -> None:
    if "--backfill-json" in argv:
        backfill_from_json()
    if "--backfill-candles" in argv:
        backfill_candles()
    if "--backfill-us" in argv:
        backfill_us()
    if "--backfill-intraday" in argv:
        backfill_intraday()
    if "--backfill-branches" in argv:
        backfill_branches()
    if "--backfill-branches-history" in argv:
        i = argv.index("--backfill-branches-history")
        start = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        end = argv[i + 2] if len(argv) > i + 2 and not argv[i + 2].startswith("--") else None
        backfill_branches_history(start, end)
    if "--backfill-chips" in argv:
        i = argv.index("--backfill-chips")
        # 可省略日期（自動取 candles 全區間），或給 <start> <end>
        start = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        end = argv[i + 2] if len(argv) > i + 2 and not argv[i + 2].startswith("--") else None
        backfill_chips(start, end)
    if "--backfill-tdcc" in argv:
        i = argv.index("--backfill-tdcc")
        start = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        end = argv[i + 2] if len(argv) > i + 2 and not argv[i + 2].startswith("--") else None
        backfill_tdcc(start, end)
    if "--backfill-futures" in argv:
        i = argv.index("--backfill-futures")
        start = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        end = argv[i + 2] if len(argv) > i + 2 and not argv[i + 2].startswith("--") else None
        backfill_futures(start, end)
    if len(argv) <= 1:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
