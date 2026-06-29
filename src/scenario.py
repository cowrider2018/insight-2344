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
