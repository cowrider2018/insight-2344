"""每日劇本機率：依昨晚隔夜驅動分層，給當日「開高低 / 盤中震盪 / 收高低 / 路徑」的歷史條件機率。

描述性（非預測 edge）：把歷史同情境日的開盤跳空、日內振幅、收盤位置、四象限路徑做頻率統計，
讓極短線者看「今天最可能怎麼走、震盪多大、收高收低機率」。資料取日 K（開高低收）＋ us_market 驅動。
驅動鍵用 swing_risk.US_KEY（由 strategy 的最佳驅動覆寫）。
"""
from __future__ import annotations

import config
import swing_risk
import timeline_db as tdb

GAP_THS = (1.0, 2.0, 3.0)        # 開盤跳空門檻%
RANGE_THS = (3.0, 5.0, 7.0)      # 日內振幅門檻%


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _rows(conn, symbol: str) -> list[dict]:
    cs = tdb.candles_upto(conn, symbol)
    out, prev = [], None
    for c in cs:
        o, h, l, cl = c.get("open"), c.get("high"), c.get("low"), c.get("close")
        if prev and None not in (o, h, l, cl) and o:
            out.append({
                "date": c["date"],
                "gap": (o - prev) / prev * 100,                 # 開盤跳空（vs 昨收）
                "rng": (h - l) / prev * 100,                    # 日內振幅
                "c2o": (cl - o) / o * 100,                      # 開→收
                "day": (cl - prev) / prev * 100,                # 全日（vs 昨收）
                "pos": (cl - l) / (h - l) if h > l else 0.5,    # 收盤在日高低區間位置 0=低 1=高
                "hi_ext": (h - o) / o * 100,                    # 開盤後上影空間
                "lo_ext": (o - l) / o * 100,                    # 開盤後下殺空間
            })
        if cl:
            prev = cl
    for r in out:
        us = tdb.us_asof(conn, swing_risk.US_KEY, r["date"])
        r["ov"] = us["change_pct"] if us and us.get("change_pct") is not None else None
    return out


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    gap = [r["gap"] for r in rows]
    rng = [r["rng"] for r in rows]
    out = {
        "n": n,
        "open": {"avg_gap": round(_mean(gap), 2),
                 "up": {f"ge{t}": round(_mean(g >= t for g in gap), 3) for t in GAP_THS},
                 "down": {f"ge{t}": round(_mean(g <= -t for g in gap), 3) for t in GAP_THS}},
        "range": {"avg": round(_mean(rng), 2),
                  "prob": {f"ge{t}": round(_mean(r >= t for r in rng), 3) for t in RANGE_THS},
                  "avg_hi_ext": round(_mean(r["hi_ext"] for r in rows), 2),
                  "avg_lo_ext": round(_mean(r["lo_ext"] for r in rows), 2)},
        "close": {"up_vs_prev": round(_mean(r["day"] > 0 for r in rows), 3),
                  "above_open": round(_mean(r["c2o"] > 0 for r in rows), 3),
                  "upper_half": round(_mean(r["pos"] >= 0.5 for r in rows), 3),
                  "avg_c2o": round(_mean(r["c2o"] for r in rows), 2)},
        # 四象限路徑：開盤跳空方向 × 開→收方向
        "paths": {
            "開高走高": round(_mean(r["gap"] > 0 and r["c2o"] > 0 for r in rows), 3),
            "開高走低": round(_mean(r["gap"] > 0 and r["c2o"] <= 0 for r in rows), 3),
            "開低走高": round(_mean(r["gap"] <= 0 and r["c2o"] > 0 for r in rows), 3),
            "開低走低": round(_mean(r["gap"] <= 0 and r["c2o"] <= 0 for r in rows), 3),
        },
    }
    return out


def intraday_path(overnight_pct: float | None = None, min_n: int = 15, conn=None) -> dict:
    """用 1 分 K 補「盤中時序」：當前驅動情境下，開盤後 30 分走勢、低/高點時點、是否先殺後拉。

    1 分 K 僅近數月（Fugle 保留期），分情境後樣本小 -> 回傳 n，呼叫端據 n 決定是否顯示（小樣本標註）。
    """
    def _do(conn):
        rows = conn.execute(
            "SELECT date, time, open, close, high, low FROM candles_1min "
            "WHERE symbol=? ORDER BY date, time", (config.SYMBOL,)).fetchall()
        if not rows:
            return {"n": 0}
        from collections import defaultdict
        days = defaultdict(list)
        for r in rows:
            days[r["date"]].append(r)
        dc = {r["date"]: r["close"] for r in
              conn.execute("SELECT date, close FROM candles WHERE symbol=?", (config.SYMBOL,))}
        dk = sorted(dc)
        import bisect
        ov = overnight_pct
        if ov is None:
            us = tdb.us_asof(conn, swing_risk.US_KEY, "9999-12-31")
            ov = us["change_pct"] if us and us.get("change_pct") is not None else None
        bucket = swing_risk._bucket(ov)
        e30, lowm, highm, lowfirst, recover, gapn = [], [], [], 0, 0, 0
        for d, b in days.items():
            if len(b) < 60:
                continue
            i = bisect.bisect_left(dk, d)
            pc = dc[dk[i - 1]] if i > 0 else None
            us = tdb.us_asof(conn, swing_risk.US_KEY, d)
            if not pc or not us or swing_risk._bucket(us.get("change_pct")) != bucket:
                continue
            op = b[0]["open"]
            li = min(range(len(b)), key=lambda k: b[k]["low"])
            hi = max(range(len(b)), key=lambda k: b[k]["high"])
            e30.append((b[min(29, len(b) - 1)]["close"] - op) / op * 100)
            lowm.append(li)
            highm.append(hi)
            lowfirst += (li < hi)
            if (op - pc) / pc < 0:
                gapn += 1
                recover += (b[-1]["close"] > op)
        n = len(e30)
        if n == 0:
            return {"n": 0, "bucket": bucket}
        return {"n": n, "bucket": bucket, "driver": swing_risk.US_KEY,
                "early30_avg": round(sum(e30) / n, 2),
                "low_min_avg": round(sum(lowm) / n), "high_min_avg": round(sum(highm) / n),
                "low_before_high_pct": round(lowfirst / n, 3),
                "gap_down_recover_pct": round(recover / gapn, 3) if gapn else None,
                "enough": n >= min_n}

    if conn is not None:
        return _do(conn)
    with tdb.connect() as c:
        return _do(c)


def playbook(overnight_pct: float | None = None, conn=None) -> dict:
    """回傳今日劇本：採昨晚驅動所屬情境（不足則用全部）的歷史條件機率。"""
    def _do(conn):
        rows = _rows(conn, config.SYMBOL)
        rows = [r for r in rows if r["ov"] is not None]
        if len(rows) < 30:
            return {"error": "日 K 樣本不足"}
        ov = overnight_pct
        if ov is None:
            us = tdb.us_asof(conn, swing_risk.US_KEY, "9999-12-31")
            ov = us["change_pct"] if us and us.get("change_pct") is not None else None
        bucket = swing_risk._bucket(ov)
        same = [r for r in rows if swing_risk._bucket(r["ov"]) == bucket] if bucket else []
        use = same if len(same) >= 15 else rows         # 情境樣本太少則退回全部
        st = _stats(use)
        st.update({"driver": swing_risk.US_KEY, "overnight_pct": ov, "bucket": bucket,
                   "scope": "情境" if use is same else "全部(情境樣本不足)",
                   "all": _stats(rows)})
        return st

    if conn is not None:
        return _do(conn)
    with tdb.connect() as c:
        return _do(c)


if __name__ == "__main__":
    import json
    import sys
    ov = float(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(playbook(ov), ensure_ascii=False, indent=2))
