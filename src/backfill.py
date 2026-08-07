"""歷史缺漏偵測與回補（gap backfill）。

每日管線只抓「當下最新」，任一來源當天失敗就在 DB 留下一個永久破洞。本模組負責
把破洞找出來並回補，並提供 `self_heal()` 讓每日流程自動修補近期缺漏。

各面可回補性（以資料來源本身的歷史保留能力為準）：

| 表               | 來源      | 可回補 | 方式                                        |
|------------------|-----------|--------|---------------------------------------------|
| futures_oi       | TAIFEX    | 全部   | CSV 端點可指定任意區間，一次取回整段        |
| broker_branches  | 富邦 DJ   | 約半年 | 頁面吃 e/f 日期參數，逐日抓、以回傳日把關   |
| branch_wf        | 本地推導  | 全部   | 由 broker_branches 重算 walk-forward 分數   |
| xs_candles/chips | TWSE      | 多年   | xs_ingest.backfill（skip-done 冪等）        |
| candles / chips  | Fugle/TWSE| 全部   | 每日抓整年/最新，實務上不留洞（僅偵測回報） |
| candles_1min     | Fugle     | 大致可 | 逐日可指定；較舊日期來源自行降頻（見下）    |
| revenue          | TWSE OpenAPI | 否  | 端點只出最新月，過往月份需另尋來源          |

1 分 K 的來源特性：Fugle 對較舊日期會自行降頻——實測 2025-11 只給 20 分 K、2026-01 只給
5 分 K，重抓也不會變細。故根數偏少屬來源限制、非抓取失敗，本模組只補「整日沒有」的日子。

用法:
    python src/backfill.py --check          # 只報告缺漏，不抓取
    python src/backfill.py                  # 回補近 60 交易日
    python src/backfill.py --days 120       # 回補近 120 交易日
    python src/backfill.py --full           # 回補全部歷史（DJ 仍受半年保留限制）
    python src/backfill.py --intraday       # 盤前 05:30 排程用：刷新日 K + 補 1 分 K
"""
from __future__ import annotations

import datetime
import sys

import config
import timeline_db as tdb

# 富邦 DJ 個股主力進出頁的歷史保留約半年；更早的日子請求會退回最新日（已由回傳日把關擋掉）。
DJ_RETENTION_DAYS = 170
# 每日 self-heal 的回看視窗（交易日）。夠涵蓋單次來源中斷，又不讓 6:00 的流程變慢；
# 補不到的舊日子會自然滾出視窗，不會每天無止盡重試。
SELF_HEAL_LOOKBACK = 25
# 單次 self-heal 最多重抓幾天分點（DJ 逐日一次 HTTP，避免流程被拖長）。
SELF_HEAL_MAX_BRANCH_FETCH = 8
# TAIFEX 未平倉約 15:00 公布：過此時點後今日才算「應已可得」。
TAIFEX_SETTLE_HOUR = 15


def _trading_days(conn) -> list[str]:
    """以 2344 日 K 為交易日曆基準（每日由 Fugle 抓整年，最完整）。"""
    return [r["date"] for r in conn.execute(
        "SELECT date FROM candles WHERE symbol = ? ORDER BY date", (config.SYMBOL,))]


def _missing(conn, sql: str, params: tuple, days: list[str]) -> list[str]:
    have = {r[0] for r in conn.execute(sql, params)}
    return [d for d in days if d not in have]


def _settled_cutoff(source: str) -> str:
    """該來源最後一個「資料應已公布」的日期；比它新的日子不算缺漏、不去白抓。

    兩者都是盤後才出，盤前 6:00 執行時今日本來就還沒有資料。不設限的話每天都會多打一次
    HTTP 並在 log 留下假警告。
      - taifex：約 15:00 公布當日未平倉，過該時點今日即可得。
      - dj    ：實測 17:30 仍只有 D-1（頁面隔日才帶到當日），故一律只取今日之前。
    """
    now = config.now_tpe()
    if source == "taifex" and now.hour >= TAIFEX_SETTLE_HOUR:
        return now.date().isoformat()
    return (now.date() - datetime.timedelta(days=1)).isoformat()


def _branch_wf_warmup_end(conn) -> str:
    """branch_wf 開始表態的第一個日期：第 MIN_HIST+1 個有分點資料的交易日。"""
    import branch_model
    row = conn.execute(
        "SELECT DISTINCT date FROM broker_branches WHERE symbol=? ORDER BY date LIMIT 1 OFFSET ?",
        (config.SYMBOL, branch_model.MIN_HIST),
    ).fetchone()
    return row[0] if row else "9999-12-31"


def gap_report(conn) -> dict:
    """列出各表相對交易日曆的缺漏日（不抓取）。"""
    days = _trading_days(conn)
    if not days:
        return {"error": "market.db 無日 K，無法建立交易日曆"}
    sym = (config.SYMBOL,)
    rep = {
        "trading_days": len(days), "first": days[0], "last": days[-1],
        "chips": _missing(conn, "SELECT DISTINCT data_date FROM chips WHERE symbol=?", sym, days),
        "broker_branches": _missing(
            conn, "SELECT DISTINCT date FROM broker_branches WHERE symbol=?", sym, days),
        # branch_wf 前 MIN_HIST 個分點日為模型暖身期、依設計不表態，不算缺漏。
        "branch_wf": [d for d in _missing(
            conn, "SELECT DISTINCT date FROM branch_wf WHERE symbol=?", sym, days)
            if d >= _branch_wf_warmup_end(conn)],
        "futures_oi": _missing(conn, "SELECT DISTINCT date FROM futures_oi", (), days),
        "candles_1min": _missing(
            conn, "SELECT DISTINCT date FROM candles_1min WHERE symbol=?", sym, days),
    }
    return rep


def _window(days: list[str], missing: list[str], lookback: int | None) -> list[str]:
    """把缺漏日限制在最近 lookback 個交易日內；lookback=None 表示不設限。"""
    if lookback is None:
        return missing
    cutoff = days[max(0, len(days) - lookback)]
    return [d for d in missing if d >= cutoff]


# --- 各面回補 -------------------------------------------------------------

def fill_futures(conn, days: list[str], missing: list[str], warnings: list[str]) -> int:
    """TAIFEX 外資台指期未平倉：一次 CSV 取回 [最早缺漏日, 今日] 整段後全量 upsert。"""
    if not missing:
        return 0
    import fetch_taifex
    rows = fetch_taifex.fetch_range(missing[0], config.now_tpe().date().isoformat(), warnings)
    if not rows:
        # 結束日尚無資料時 TAIFEX 整段回空，往前退到最後一個已知交易日重試。
        rows = fetch_taifex.fetch_range(missing[0], days[-1], warnings)
    if not rows:
        warnings.append(f"backfill futures: {missing[0]}~ 取不到任何資料")
        return 0
    tdb.upsert_futures_oi(conn, fetch_taifex.MARKET_KEY, rows)
    return len({r["date"] for r in rows} & set(missing))


def fill_branches(conn, missing: list[str], warnings: list[str], limit: int | None = None) -> int:
    """券商分點：DJ 逐日抓。回傳日與請求日不符即視為超出保留期，跳過不寫（避免污染）。"""
    import fetch_dj_chips
    cutoff = (config.now_tpe().date() - datetime.timedelta(days=DJ_RETENTION_DAYS)).isoformat()
    todo = [d for d in missing if d >= cutoff]
    if len(missing) > len(todo):
        warnings.append(f"backfill branches: {len(missing) - len(todo)} 日超出 DJ 保留期（<{cutoff}），無法回補")
    if limit is not None:
        todo = todo[-limit:]
    filled = 0
    for d in todo:
        got = fetch_dj_chips.fetch_branches(d, warnings)
        if got.get("date") != d or not got.get("rows"):
            warnings.append(f"backfill branches {d}: 取不到（回傳 date={got.get('date')}），可能為非交易日或已過期")
            continue
        tdb.upsert_branches(conn, config.SYMBOL, d, got["rows"])
        filled += 1
    return filled


def fill_branch_wf(conn) -> int:
    """由 broker_branches 重算 walk-forward 分點分數並覆寫（冪等，無 look-ahead）。

    分點資料一有新增（每日或回補）就必須重算，否則 branch_wf 停在舊日期、
    第八面模型分數失真。
    """
    import branch_model
    scores = branch_model.walkforward(conn)
    return tdb.upsert_branch_wf(conn, config.SYMBOL, scores)


def refresh_candles(conn, warnings: list[str]) -> str | None:
    """刷新日 K 到最新已收盤交易日，回傳最新日期。

    05:30 的盤前工作必須先做這步：前一天 06:00 的流程跑完時 D-1 還沒開盤，`candles` 最新
    只到 D-2，不先刷新就不會知道 D-1 是交易日、也就不會去補 D-1 的 1 分 K。
    """
    try:
        import fetch_fugle
        candles = fetch_fugle.fetch_candles()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"refresh_candles: {e}")
        return None
    if not candles:
        warnings.append("refresh_candles: Fugle 回空")
        return None
    tdb.upsert_candles(conn, config.SYMBOL, candles)
    return candles[-1]["date"]


def fill_intraday(conn, missing: list[str], warnings: list[str]) -> int:
    """1 分 K：逐日向 Fugle 補。抓不到的日子記 warning（超出保留期）。

    注意 Fugle 對較舊的日期會自行降頻（實測 2025-11 只給 20 分 K、2026-01 只給 5 分 K），
    回傳根數偏少屬來源特性、非抓取失敗，重抓也不會變細，故不視為缺漏。
    """
    import fetch_fugle
    filled = 0
    for d in missing:
        bars = fetch_fugle.fetch_intraday_candles(d)
        if not bars:
            warnings.append(f"backfill intraday {d}: Fugle 無資料（超出盤中保留期）")
            continue
        tdb.upsert_intraday(conn, config.SYMBOL, d, bars)
        filled += 1
    return filled


def fill_xs(days: list[str], lookback: int | None, warnings: list[str]) -> dict:
    """全市場橫斷面庫（xs.db）：skip-done 冪等，只補真正沒有的日子。"""
    try:
        import xs_ingest
        start = days[0] if lookback is None else days[max(0, len(days) - lookback)]
        return xs_ingest.backfill(start, days[-1], all_market=True)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"backfill xs: {e}")
        return {"error": str(e)}


# --- 對外入口 -------------------------------------------------------------

def self_heal(conn, warnings: list[str], lookback: int = SELF_HEAL_LOOKBACK) -> dict:
    """每日管線用：修補近期缺漏。須在 ingest 之後呼叫（當日資料已入庫）。

    有意設計成有界：只看最近 lookback 個交易日、分點最多重抓
    SELF_HEAL_MAX_BRANCH_FETCH 天，讓 6:00 的流程不會因回補而變慢。
    """
    days = _trading_days(conn)
    if not days:
        return {"error": "無交易日曆"}
    rep = gap_report(conn)
    def pending(key: str, source: str) -> list[str]:
        cutoff = _settled_cutoff(source)
        return [d for d in _window(days, rep[key], lookback) if d <= cutoff]

    out = {}
    try:
        out["futures"] = fill_futures(conn, days, pending("futures_oi", "taifex"), warnings)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"self_heal futures: {e}")
    try:
        out["branches"] = fill_branches(
            conn, pending("broker_branches", "dj"), warnings, limit=SELF_HEAL_MAX_BRANCH_FETCH)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"self_heal branches: {e}")
    # branch_wf 每天都要重算（今日新分點入庫後分數才會延伸到昨日）
    try:
        out["branch_wf"] = fill_branch_wf(conn)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"self_heal branch_wf: {e}")
    return out


def run_intraday(lookback: int | None = 30) -> dict:
    """盤前 05:30 專用：刷新日 K → 補齊 1 分 K，讓 06:00 的分析已有前一交易日完整盤中資料。

    與 06:00 的 build_dataset 是「先行 + 保險」關係：build_dataset 本身也會抓當日 1 分 K，
    但那是單次、失敗即當日永久遺失；這支先行跑，且會把近 lookback 個交易日抓漏的日子一併補回。
    """
    tdb.init_db()
    warnings: list[str] = []
    with tdb.connect() as conn:
        latest = refresh_candles(conn, warnings)
        print(f"[intraday] 日 K 已刷新至 {latest}")
        days = _trading_days(conn)
        if not days:
            print("[intraday] 無交易日曆，中止")
            return {"error": "無交易日曆"}
        rep = gap_report(conn)
        # 1 分 K 是收盤後才完整；今日（尚未開盤/收盤）不算缺漏。
        cutoff = (config.now_tpe().date() - datetime.timedelta(days=1)).isoformat()
        miss = [d for d in _window(days, rep["candles_1min"], lookback) if d <= cutoff]
        print(f"[intraday] 近 {lookback or '全部'} 交易日缺 {len(miss)} 日"
              + (f"：{', '.join(miss)}" if miss else ""))
        n = fill_intraday(conn, miss, warnings)
        print(f"[intraday] 補入 {n} 日")
    if warnings:
        print("  warnings:")
        for w in warnings:
            print("   -", w)
    return {"latest_candle": latest, "missing": len(miss), "filled": n}


def run(lookback: int | None, check_only: bool = False, with_xs: bool = True) -> dict:
    """CLI 入口：回補（或僅檢查）到最新交易日。lookback=None 表全歷史。"""
    tdb.init_db()
    warnings: list[str] = []
    with tdb.connect() as conn:
        rep = gap_report(conn)
        if rep.get("error"):
            print(f"[backfill] {rep['error']}")
            return rep
        _print_gaps(rep, lookback)
        if check_only:
            return rep

        days = _trading_days(conn)
        print("\n[backfill] 開始回補…")

        miss = [d for d in _window(days, rep["futures_oi"], lookback)
                if d <= _settled_cutoff("taifex")]
        n = fill_futures(conn, days, miss, warnings)
        print(f"  futures_oi      缺 {len(miss):3d} -> 補 {n}")

        miss = [d for d in _window(days, rep["broker_branches"], lookback)
                if d <= _settled_cutoff("dj")]
        n = fill_branches(conn, miss, warnings)
        print(f"  broker_branches 缺 {len(miss):3d} -> 補 {n}")

        n = fill_branch_wf(conn)
        print(f"  branch_wf       重算 {n} 日")

        # 1 分 K 收盤後才完整，今日不算缺漏
        miss = [d for d in _window(days, rep["candles_1min"], lookback)
                if d < config.now_tpe().date().isoformat()]
        n = fill_intraday(conn, miss, warnings)
        print(f"  candles_1min    缺 {len(miss):3d} -> 補 {n}")

    if with_xs:
        with tdb.connect() as conn:
            days = _trading_days(conn)
        st = fill_xs(days, lookback, warnings)
        print(f"  xs.db           {st}")

    with tdb.connect() as conn:
        after = gap_report(conn)
    print("\n[backfill] 回補後：")
    _print_gaps(after, lookback)
    if warnings:
        print("\n  warnings:")
        for w in warnings:
            print("   -", w)
    return after


def _print_gaps(rep: dict, lookback: int | None) -> None:
    days_note = "全歷史" if lookback is None else f"近 {lookback} 交易日"
    print(f"[backfill] 交易日曆 {rep['trading_days']} 日（{rep['first']} ~ {rep['last']}），檢查範圍：{days_note}")
    notes = {"candles_1min": "（逾 Fugle 盤中保留期者無法回補）"}
    # 尚未到公布時點的日子分開列，避免看起來像破洞。
    src = {"broker_branches": "dj", "branch_wf": "dj", "futures_oi": "taifex"}
    for k in ("chips", "broker_branches", "branch_wf", "futures_oi", "candles_1min"):
        cutoff = _settled_cutoff(src[k]) if k in src else "9999-12-31"
        m = [d for d in rep[k] if d <= cutoff]
        pend = [d for d in rep[k] if d > cutoff]
        tail = f"  最近缺漏: {', '.join(m[-5:])}{notes.get(k, '')}" if m else ""
        if pend:
            tail += f"  （{', '.join(pend)} 尚未公布，非缺漏）"
        print(f"  {k:16s} 缺 {len(m):4d}{tail}")


def main(argv: list[str]) -> None:
    lookback: int | None = 60
    if "--full" in argv:
        lookback = None
    elif "--days" in argv:
        lookback = int(argv[argv.index("--days") + 1])
    if "--intraday" in argv:          # 盤前 05:30 排程用：只刷新日 K + 補 1 分 K
        run_intraday(lookback)
        return
    run(lookback, check_only="--check" in argv, with_xs="--no-xs" not in argv)


if __name__ == "__main__":
    main(sys.argv[1:])
