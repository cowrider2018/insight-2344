"""每股最佳隔夜驅動選擇（泛用化關鍵）：不再寫死費半 SOX，而是對一籃美股代理
測與該股次日的相關性，挑最匹配者作為該股的「隔夜驅動」。

理由：不同台股有不同的隔夜主導——記憶體跟美光、IC 測試/晶圓代工跟台積 ADR、廣義半導體跟費半。
驅動序列存入 us_market（鍵=驅動代號），swing_risk / daily_decision 以該鍵取隔夜值。
以相關性（全樣本、連續、較穩）選擇，避免小樣本勝率的選擇偏誤。
"""
from __future__ import annotations

import timeline_db as tdb

# 候選：us_market 鍵 -> Yahoo 代號（皆美股、隔夜時段，台股開盤前可得）
CANDIDATES = {
    "sox": "^SOX", "smh": "SMH", "soxx": "SOXX", "mu": "MU",
    "tsm": "TSM", "nvda": "NVDA", "amd": "AMD",
}


def backfill_driver(key: str, yahoo: str, range_: str = "2y") -> int:
    """抓某驅動的美股日線存入 us_market（鍵=key）。冪等。"""
    import fetch_us
    rows = fetch_us.fetch_yahoo_daily(yahoo, range_)
    with tdb.connect() as conn:
        n = tdb.upsert_us(conn, key, rows)
    return n


def _stock_moves(conn, symbol: str) -> dict:
    cr = [(r["date"], r["close"]) for r in
          conn.execute("SELECT date, close FROM candles WHERE symbol=? ORDER BY date", (symbol,))
          if r["close"]]
    return {cr[i][0]: (cr[i][1] - cr[i - 1][1]) / cr[i - 1][1] * 100.0 for i in range(1, len(cr))}


def _corr(xs, ys):
    n = len(xs)
    if n < 20:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sx = sum((a - mx) ** 2 for a in xs) ** 0.5
    sy = sum((b - my) ** 2 for b in ys) ** 0.5
    return sxy / (sx * sy) if sx and sy else None


def select_best(symbol: str, candidates: dict | None = None,
                ensure_backfill: bool = True) -> dict:
    """回傳該股最佳隔夜驅動：{"best": key, "corr": r, "table": [{key,corr,n}...]}。

    以 corr(隔夜驅動[盤前可得], 該股次日漲跌) 選最高正相關者。
    """
    cands = candidates or CANDIDATES
    if ensure_backfill:
        for key, yahoo in cands.items():
            with tdb.connect() as conn:
                has = conn.execute("SELECT 1 FROM us_market WHERE symbol=? LIMIT 1", (key,)).fetchone()
            if not has:
                try:
                    backfill_driver(key, yahoo)
                except Exception:  # noqa: BLE001
                    pass

    with tdb.connect() as conn:
        moves = _stock_moves(conn, symbol)
        table = []
        for key in cands:
            rows = conn.execute(
                "SELECT date, change_pct FROM us_market WHERE symbol=? AND change_pct IS NOT NULL "
                "ORDER BY date", (key,)).fetchall()
            if not rows:
                continue
            sd = [r["date"] for r in rows]
            sm = {r["date"]: r["change_pct"] for r in rows}

            def ov(d, sd=sd, sm=sm):
                v = None
                for x in sd:
                    if x < d:
                        v = sm[x]
                    else:
                        break
                return v

            pr = [(ov(d), moves[d]) for d in moves if ov(d) is not None]
            r = _corr([a for a, _ in pr], [b for _, b in pr])
            if r is not None:
                table.append({"key": key, "corr": round(r, 4), "n": len(pr)})
    table.sort(key=lambda x: -x["corr"])
    best = table[0]["key"] if table else "sox"
    return {"best": best, "corr": table[0]["corr"] if table else None, "table": table}


if __name__ == "__main__":
    import sys
    import config
    sym = sys.argv[1] if len(sys.argv) > 1 else config.SYMBOL
    r = select_best(sym)
    print(f"{sym} 最佳隔夜驅動: {r['best']} (corr {r['corr']})")
    for t in r["table"]:
        print(f"  {t['key']:<6} corr {t['corr']:+.3f} (n={t['n']})")
