"""橫斷面子系統回補（選項 D）：逐交易日抓 TWSE 全市場 → 篩股票池 → 灌 data/xs.db。

資料源（皆免金鑰、全市場單次端點，成本低）：
- 全市場日 K 收盤：TWSE RWD MI_INDEX（type=ALLBUT0999），每日一次取全部上市股收盤/成交量。
- 全市場三大法人：TWSE RWD T86（selectType=ALL），每日一次取全部上市股買賣超。
- 全市場 TDCC 大戶（當週快照）：TDCC OpenData CSV id=1-5（一次取全市場最新週）。
交易日清單沿用 market.db 的 2344 日 K（只讀，低耦合）。

用法：
    python src/xs_ingest.py --backfill 2026-01-01 2026-06-24
    python src/xs_ingest.py --backfill            # 預設近約一季
    python src/xs_ingest.py --backfill 2025-07-01 2026-06-24 --all   # 全市場普通股（大樣本 IC）
"""
from __future__ import annotations

import sys
import time

import config
import fetch_twse
import timeline_db as tdb
import universe
import xs_db


def _shares_to_lots(s) -> float | None:
    v = fetch_twse._to_int(s)
    return None if v is None else round(v / 1000, 1)


def _col_idx(fields: list, *needles, exclude=()) -> int | None:
    for i, f in enumerate(fields):
        fs = str(f)
        if all(n in fs for n in needles) and not any(x in fs for x in exclude):
            return i
    return None


def _keep(sym: str, want: set[str] | None) -> bool:
    """want 為 None -> 全市場普通股；否則只留 want 清單內。"""
    return universe.is_common_stock(sym) if want is None else (sym in want)


def _cell(row: list, idx: int | None):
    """安全取欄（全市場資料偶有短列/彙總列，須防 IndexError）。"""
    return row[idx] if idx is not None and idx < len(row) else None


def fetch_chips_allstock(date_ymd: str, want: set[str] | None, warnings: list[str]) -> list[dict]:
    """T86 全市場三大法人 → 篩 want 股票池，回傳 [{symbol,date,foreign_net,total_net}]（張）。"""
    try:
        js = fetch_twse._get_json(f"{config.TWSE_RWD}/fund/T86",
                                  {"selectType": "ALL", "response": "json", "date": date_ymd})
    except (ValueError, Exception) as e:  # noqa: BLE001
        warnings.append(f"xs T86 {date_ymd} 失敗: {e}")
        return []
    fields = js.get("fields") or []
    fi = _col_idx(fields, "外資", "買賣超股數", "不含外資自營商")
    ti = _col_idx(fields, "三大法人買賣超股數")
    d = date_ymd[:4] + "-" + date_ymd[4:6] + "-" + date_ymd[6:]
    out = []
    for r in js.get("data") or []:
        if not r:
            continue
        sym = str(r[0]).strip()
        if not _keep(sym, want):
            continue
        out.append({"symbol": sym, "date": d,
                    "foreign_net": _shares_to_lots(_cell(r, fi)),
                    "total_net": _shares_to_lots(_cell(r, ti))})
    return out


def fetch_closes_allstock(date_ymd: str, want: set[str] | None, warnings: list[str]) -> list[dict]:
    """MI_INDEX 全市場收盤 → 篩 want，回傳 [{symbol,date,close,volume(張)}]。"""
    try:
        js = fetch_twse._get_json(f"{config.TWSE_RWD}/afterTrading/MI_INDEX",
                                  {"date": date_ymd, "type": "ALLBUT0999", "response": "json"})
    except (ValueError, Exception) as e:  # noqa: BLE001
        warnings.append(f"xs MI_INDEX {date_ymd} 失敗: {e}")
        return []
    d = date_ymd[:4] + "-" + date_ymd[4:6] + "-" + date_ymd[6:]
    for t in js.get("tables") or []:
        fields = t.get("fields") or []
        ci = _col_idx(fields, "收盤價")
        vi = _col_idx(fields, "成交股數")
        si = _col_idx(fields, "證券代號")
        if ci is None or si is None:
            continue
        out = []
        for r in t.get("data") or []:
            if not r or si >= len(r):
                continue
            sym = str(r[si]).strip()
            if not _keep(sym, want):
                continue
            out.append({"symbol": sym, "date": d,
                        "close": fetch_twse._flt(_cell(r, ci)),
                        "volume": _shares_to_lots(_cell(r, vi))})
        return out
    warnings.append(f"xs MI_INDEX {date_ymd}: 找不到收盤行情表")
    return []


def fetch_tdcc_allstock(want: set[str] | None, warnings: list[str]) -> list[dict]:
    """TDCC OpenData CSV（全市場最新週）→ 篩 want，回傳 [{symbol,data_date,big_pct}]。"""
    import csv
    import io

    import fetch_tdcc
    try:
        import requests
        r = requests.get(fetch_tdcc.OPENDATA_URL, params={"id": "1-5"},
                         headers={"User-Agent": config.USER_AGENT}, timeout=60, verify=False)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"xs tdcc opendata 失敗: {e}")
        return []
    by_sym: dict[str, dict] = {}
    dd: dict[str, str] = {}
    reader = csv.reader(io.StringIO(r.content.decode("utf-8-sig", errors="ignore")))
    next(reader, None)
    for row in reader:
        if len(row) < 6:
            continue
        sym = row[1].strip()
        if not _keep(sym, want):
            continue
        pct = fetch_tdcc._pct(row[5])
        if pct is not None:
            by_sym.setdefault(sym, {})[row[2].strip()] = pct
            dd.setdefault(sym, fetch_tdcc._norm_date(row[0]))
    out = []
    for sym, levels in by_sym.items():
        agg = fetch_tdcc._aggregate(levels, dd.get(sym))
        out.append({"symbol": sym, "data_date": agg["data_date"], "big_pct": agg["big_pct"]})
    return out


def backfill(start: str | None = None, end: str | None = None,
             all_market: bool = False) -> dict:
    want = None if all_market else set(universe.SYMBOLS)
    xs_db.init_db()
    warnings: list[str] = []
    # 交易日清單沿用 market.db 的 2344 日 K（只讀）
    with tdb.connect() as mc:
        all_dates = [r["date"] for r in tdb.candles_upto(mc, config.SYMBOL)]
    if not all_dates:
        print("[xs_ingest] market.db 無 2344 日 K，請先在主系統 --backfill-candles")
        return {}
    start = start or all_dates[max(0, len(all_dates) - 65)]  # 預設近約一季
    end = end or all_dates[-1]
    sel = [d for d in all_dates if start <= d <= end]
    pool = "全市場普通股" if want is None else f"{len(want)} 檔"
    print(f"[xs_ingest] 股票池 {pool}，交易日 {len(sel)}（{start}~{end}），逐日抓 TWSE 全市場（較慢）")

    tot = {"candles": 0, "chips": 0, "tdcc": 0, "skip": 0}
    with xs_db.connect() as conn:
        # 已同時有 candles 與 chips 的交易日 -> 跳過（重跑可續抓）
        done = ({r[0] for r in conn.execute("SELECT DISTINCT date FROM xs_candles")}
                & {r[0] for r in conn.execute("SELECT DISTINCT date FROM xs_chips")})
        for k, d in enumerate(sel, 1):
            if d in done:
                tot["skip"] += 1
                continue
            ymd = d.replace("-", "")
            tot["candles"] += xs_db.upsert_candles(conn, fetch_closes_allstock(ymd, want, warnings))
            tot["chips"] += xs_db.upsert_chips(conn, fetch_chips_allstock(ymd, want, warnings))
            if k % 10 == 0:
                conn.commit()
                print(f"  ...{k}/{len(sel)}（{d}）candles+{tot['candles']} chips+{tot['chips']} skip{tot['skip']}")
            time.sleep(0.3)
        tot["tdcc"] = xs_db.upsert_tdcc(conn, fetch_tdcc_allstock(want, warnings))
    print(f"[xs_ingest] 完成：candles {tot['candles']}、chips {tot['chips']}、tdcc {tot['tdcc']}")
    if warnings:
        print(f"  warnings: {len(warnings)} 則（多為假日/尚未更新）")
    return tot


def refresh_daily(window_days: int = 60) -> dict:
    """每日管線用：把 xs.db 補到最新交易日（全市場，供 peer + market 兩池橫斷面分數）。

    薄包裝 backfill(all_market=True)：交易日清單沿用 market.db 的 2344 日 K，回補最近
    window_days 個交易日。**skip-done 冪等**——首次跑自動 bootstrap ~一季（供近期 IC 樣本），
    之後每日只抓最新 1 天（其餘已存跳過，約 2 次全市場 HTTP + 1 次 TDCC CSV）。
    """
    with tdb.connect() as mc:
        all_dates = [r["date"] for r in tdb.candles_upto(mc, config.SYMBOL)]
    if not all_dates:
        return {"error": "market.db 無 2344 日 K"}
    start = all_dates[max(0, len(all_dates) - window_days)]
    return backfill(start, all_dates[-1], all_market=True)


def _weekdays(start: str, end: str):
    from datetime import date, timedelta
    cur = date(*(int(x) for x in start.split("-")))
    last = date(*(int(x) for x in end.split("-")))
    while cur <= last:
        if cur.weekday() < 5:
            yield cur.isoformat()
        cur += timedelta(days=1)


def backfill_range(start: str, end: str, all_market: bool = True) -> dict:
    """以「週一~週五」逐日回補任意區間（不依賴 market.db 1 年 scaffold；非交易日 MI_INDEX 回空即跳過）。

    供把 chip+foreign 3 因子往更早拉（TWSE 多年；TDCC/Fugle 受 1 年保留期限不適用）。
    """
    want = None if all_market else set(universe.SYMBOLS)
    xs_db.init_db()
    warnings: list[str] = []
    days = list(_weekdays(start, end))
    tot = {"candles": 0, "chips": 0, "skip": 0, "holiday": 0}
    with xs_db.connect() as conn:
        done = ({r[0] for r in conn.execute("SELECT DISTINCT date FROM xs_candles")}
                & {r[0] for r in conn.execute("SELECT DISTINCT date FROM xs_chips")})
        print(f"[xs-range] {start}~{end} 共 {len(days)} 個平日（逐日抓 TWSE 全市場、跳過非交易日）")
        for k, d in enumerate(days, 1):
            if d in done:
                tot["skip"] += 1
                continue
            ymd = d.replace("-", "")
            cl = fetch_closes_allstock(ymd, want, warnings)
            if not cl:                       # 非交易日/未公布 -> 不再打 T86
                tot["holiday"] += 1
                continue
            tot["candles"] += xs_db.upsert_candles(conn, cl)
            tot["chips"] += xs_db.upsert_chips(conn, fetch_chips_allstock(ymd, want, warnings))
            if k % 10 == 0:
                conn.commit()
                print(f"  ...{k}/{len(days)}（{d}）candles+{tot['candles']} chips+{tot['chips']} "
                      f"skip{tot['skip']} 非交易{tot['holiday']}")
            time.sleep(0.3)
    print(f"[xs-range] 完成：candles {tot['candles']}、chips {tot['chips']}、已存跳過 {tot['skip']}")
    return tot


def backfill_tdcc_history(start: str | None = None, end: str | None = None) -> int:
    """聚焦池（universe.SYMBOLS）逐檔逐週回補 TDCC 大戶占比歷史 → xs.db.xs_tdcc。

    全市場逐檔歷史不可廉價回補，故只對策展 ~50 檔做（真大戶週變化因子用）。
    """
    import fetch_tdcc
    xs_db.init_db()
    warnings: list[str] = []
    weeks = fetch_tdcc.available_dates(warnings)  # YYYYMMDD 新到舊
    if not weeks:
        print(f"[tdcc-hist] 無可用週；warnings: {warnings}")
        return 0

    def _iso(d):
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"

    weeks = [w for w in weeks if (not start or _iso(w) >= start) and (not end or _iso(w) <= end)]
    # 跳過已有 >=2 週歷史的股票（可續抓擴池新增者）
    with xs_db.connect() as conn:
        done = {r[0] for r in conn.execute(
            "SELECT symbol FROM xs_tdcc GROUP BY symbol HAVING COUNT(*) >= 2")}
    syms = [s for s in universe.SYMBOLS if s not in done]
    if not syms:
        print(f"[tdcc-hist] 聚焦池 {len(universe.SYMBOLS)} 檔皆已有歷史，無需回補")
        return 0
    print(f"[tdcc-hist] 新增 {len(syms)}/{len(universe.SYMBOLS)} 檔 × {len(weeks)} 週，逐檔逐週查 smart.tdcc（較慢）")
    rows = fetch_tdcc.fetch_big_pct_history(syms, weeks, warnings)
    with xs_db.connect() as conn:
        n = xs_db.upsert_tdcc(conn, rows)
    print(f"[tdcc-hist] upsert {n} 筆（{len(syms)} 檔大戶週序列）")
    if warnings:
        print(f"  warnings: {len(warnings)} 則")
    return n


def main(argv: list[str]) -> None:
    if "--backfill-range" in argv:
        i = argv.index("--backfill-range")
        backfill_range(argv[i + 1], argv[i + 2], all_market="--all" in argv or True)
    elif "--backfill-tdcc-hist" in argv:
        i = argv.index("--backfill-tdcc-hist")
        start = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        end = argv[i + 2] if len(argv) > i + 2 and not argv[i + 2].startswith("--") else None
        backfill_tdcc_history(start, end)
    elif "--backfill" in argv:
        i = argv.index("--backfill")
        start = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else None
        end = argv[i + 2] if len(argv) > i + 2 and not argv[i + 2].startswith("--") else None
        backfill(start, end, all_market="--all" in argv)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
