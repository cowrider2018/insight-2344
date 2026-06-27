"""當日早盤「被殺/噴出」風險機率（6:00 盤前用）。

使用情境：每天 06:00（台股 09:00 開盤前），昨晚美股（隔夜）已知，要研判**今天早盤會不會被殺**。
故以**歷史條件機率**估計今日：
  - 開盤跳空 open_gap =（今開 − 昨收）/ 昨收：早盤被殺=大幅下跳空
  - 全日 day_move =（今收 − 昨收）/ 昨收
**依昨晚費半 SOX（已知、記憶體族群當日開盤主導）方向分層**，給出：
  P(開盤下殺≥θ)=P(open_gap≤−θ)、P(開高≥θ)、P(全日跌≥θ)、P(全日漲≥θ)，θ∈{2,3,5}%，
  以及該情境的平均開盤跳空 / 平均全日漲跌（方向傾向）。波動 regime 為輔助脈絡。

純函式、低耦合；資料取自 timeline_db（candles 的 open/close 與 us_market 隔夜）。
"""
from __future__ import annotations

import config
import timeline_db as tdb

DEFAULT_THRESHOLDS = (2.0, 3.0, 5.0)
VOL_WINDOW = 10
US_KEY = "sox"                  # 隔夜方向條件（費半；記憶體族群開盤主導）

# 昨晚費半 signed 漲跌% 分層
_OV_BUCKETS = [("大跌", None, -2.0), ("跌", -2.0, -0.7), ("平", -0.7, 0.7),
               ("漲", 0.7, 2.0), ("大漲", 2.0, None)]


def _bucket(pct: float | None) -> str | None:
    if pct is None:
        return None
    for name, lo, hi in _OV_BUCKETS:
        if (lo is None or pct >= lo) and (hi is None or pct < hi):
            return name
    return None


def _std(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def _series(candles: list[dict]) -> list[dict]:
    """每日 {date, day_move, open_gap}（相對前一日收盤，%）。"""
    out, prev = [], None
    for c in candles:
        op, cl = c.get("open"), c.get("close")
        if prev:
            out.append({"date": c["date"],
                        "day_move": (cl - prev) / prev * 100.0 if cl else None,
                        "open_gap": (op - prev) / prev * 100.0 if op else None})
        if cl:
            prev = cl
    return out


def _probs(rows: list[dict], thresholds) -> dict:
    """rows 為一組日樣本，回傳開盤跳空與全日的方向機率與平均。"""
    gaps = [r["open_gap"] for r in rows if r["open_gap"] is not None]
    days = [r["day_move"] for r in rows if r["day_move"] is not None]
    out = {"n": len(rows),
           "avg_open_gap": round(sum(gaps) / len(gaps), 3) if gaps else None,
           "avg_day_move": round(sum(days) / len(days), 3) if days else None}
    for th in thresholds:
        out[th] = {
            "open_down": round(sum(1 for g in gaps if g <= -th) / len(gaps), 4) if gaps else 0.0,
            "open_up": round(sum(1 for g in gaps if g >= th) / len(gaps), 4) if gaps else 0.0,
            "day_down": round(sum(1 for d in days if d <= -th) / len(days), 4) if days else 0.0,
            "day_up": round(sum(1 for d in days if d >= th) / len(days), 4) if days else 0.0,
        }
    return out


def _build_samples(conn, symbol: str) -> list[dict]:
    """每日樣本附當日隔夜費半 signed 漲跌與近 VOL_WINDOW 波動。"""
    ser = _series(tdb.candles_upto(conn, symbol))
    moves = [s["day_move"] for s in ser]
    samples = []
    for i, s in enumerate(ser):
        us = tdb.us_asof(conn, US_KEY, s["date"])      # 該交易日盤前可得的隔夜費半
        ov = us["change_pct"] if us and us.get("change_pct") is not None else None
        prior = [m for m in moves[max(0, i - VOL_WINDOW):i] if m is not None]
        samples.append({**s, "overnight": ov, "vol_prev": _std(prior)})
    return samples


def conditional_by_overnight(samples: list[dict], thresholds) -> dict:
    """依昨晚費半方向分層的今日開盤/全日機率表。"""
    out = {}
    for name, _, _ in _OV_BUCKETS:
        rows = [s for s in samples if _bucket(s["overnight"]) == name]
        out[name] = _probs(rows, thresholds)
    out["全部"] = _probs(samples, thresholds)
    return out


def estimate(overnight_pct: float | None = None, thresholds=DEFAULT_THRESHOLDS, conn=None) -> dict:
    """今日盤前（6:00）被殺/噴出風險。overnight_pct=昨晚費半漲跌%（不給則取 DB 最新隔夜）。"""
    def _do(conn):
        samples = _build_samples(conn, config.SYMBOL)
        if len(samples) < 30:
            return {"error": "樣本不足，請先回補日 K（candles）"}
        table = conditional_by_overnight(samples, thresholds)
        if overnight_pct is None:
            us = tdb.us_asof(conn, US_KEY, "9999-12-31")   # 最新一筆隔夜
            ov = us["change_pct"] if us and us.get("change_pct") is not None else None
        else:
            ov = overnight_pct
        bk = _bucket(ov)
        cur_vol = _std([s["day_move"] for s in samples[-VOL_WINDOW:] if s["day_move"] is not None])
        # 決斷度 → 該情境同日方向歷史命中率（朝 67% 的可達路徑）
        acc = accuracy(conn=conn)
        absov = abs(ov) if ov is not None else 0.0
        thr = 2.0 if absov >= 2.0 else (1.0 if absov >= 1.0 else 0.0)
        conviction = "決斷" if thr == 2.0 else ("中度" if thr == 1.0 else "平淡")
        tier = acc.get(f"昨晚費半|≥{thr}%", {})
        # 部位旗標：決斷夜(|SOX|≥1%, OOS ~68~71%)可重押；平淡夜(~53%)保守
        if thr >= 2.0:
            stance, stance_reason = "重押", "決斷夜(|SOX|≥2%)：跟隔夜方向，OOS/跨年同日勝率 ~70%"
        elif thr >= 1.0:
            stance, stance_reason = "重押", "決斷夜(|SOX|≥1%)：跟隔夜方向，OOS 同日勝率 ~68%"
        else:
            stance, stance_reason = "保守", "平淡夜(|SOX|<1%)：十面 OOS ~53%≈擲幣，宜小量或只看風險"
        return {
            "symbol": config.SYMBOL,
            "as_of_date": samples[-1]["date"],
            "thresholds": list(thresholds),
            "overnight_sox_pct": ov,
            "overnight_bucket": bk,
            "current_vol_10d": round(cur_vol, 3),
            "today_prob": table.get(bk) if bk else table["全部"],   # 採昨晚情境（被殺機率主依據）
            "conviction": conviction,                                # 決斷/中度/平淡（由 |昨晚費半| 分層）
            "stance": stance,                                        # 重押 / 保守（今日該不該下重手）
            "stance_reason": stance_reason,
            "dir_winrate": tier,                                     # 該決斷度的同日方向歷史命中（開盤/全日 + 涵蓋）
            "by_overnight": table,
            "n_samples": len(samples),
            "note": "open_down=開盤下殺機率(被殺)、day_down=全日收黑機率；conviction 高(決斷)時同日方向命中率較高(見 dir_winrate)",
        }

    if conn is not None:
        return _do(conn)
    with tdb.connect() as c:
        return _do(c)


def accuracy(neutral: float = 1.0, conn=None) -> dict:
    """以昨晚費半 signed 方向為預測子，量測同日「開盤」與「全日」方向命中率與涵蓋率。

    方向性命中：只計實際 |變動| ≥ neutral 的日子（過濾中性日）。依「決斷度」分層
    （|昨晚費半| ≥ 0/1/2%）——決斷度高的日子，同日方向更易命中（朝 67% 的現實路徑）。
    """
    def _do(conn):
        samples = [s for s in _build_samples(conn, config.SYMBOL) if s["overnight"] is not None]

        def wr(use: str, thr_pred: float):
            n = hit = 0
            for s in samples:
                if abs(s["overnight"]) < thr_pred:
                    continue
                actual = s["day_move"] if use == "day" else s["open_gap"]
                if actual is None or abs(actual) < neutral:
                    continue
                n += 1
                hit += (s["overnight"] > 0) == (actual > 0)
            return {"win": round(hit / n, 4) if n else 0.0, "n": n,
                    "cov": round(n / len(samples), 3) if samples else 0.0}

        out = {"total_days": len(samples), "neutral_band": neutral}
        for thr in (0.0, 1.0, 2.0):
            out[f"昨晚費半|≥{thr}%"] = {"開盤方向": wr("open", thr), "全日方向": wr("day", thr)}
        return out

    if conn is not None:
        return _do(conn)
    with tdb.connect() as c:
        return _do(c)


if __name__ == "__main__":
    import json
    import sys
    if "--accuracy" in sys.argv:
        print(json.dumps(accuracy(), ensure_ascii=False, indent=2))
    else:
        ov = float(sys.argv[1]) if len(sys.argv) > 1 else None   # 可帶昨晚費半漲跌%
        print(json.dumps(estimate(ov), ensure_ascii=False, indent=2))
