"""EXP-005 融資餘額極端值 — 研究腳本（非 production，禁止被 daily path import）

假設：融資餘額 5 日急增至極端（散戶槓桿追高）＝反指標 → 2344 次日偏空；
急減（斷頭/去槓桿）→ 偏多。與被護欄歸零的 chips 綜合面不同切法（只看散戶槓桿極端）。
限制：margin 僅 market.db 近一年（251 日），無跨 regime 視窗，結論僅適用單一 regime。

用法：
  python research/exp_005_margin_extreme.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import backtest as bt  # noqa: E402
import config  # noqa: E402
import timeline_db as tdb  # noqa: E402

NEUTRAL_TOL = 1.0
DECISIVE_THR = 1.0


def build_days() -> list[dict]:
    with tdb.connect() as conn:
        candles = tdb.candles_upto(conn, config.SYMBOL)
        margins = conn.execute(
            "SELECT data_date, margin_balance, margin_chg FROM chips "
            "WHERE symbol = ? AND margin_balance IS NOT NULL ORDER BY data_date",
            (config.SYMBOL,),
        ).fetchall()
        mdates = [m["data_date"] for m in margins]
        days = []
        for i in range(1, len(candles)):
            d = candles[i]["date"]
            prev_close = candles[i - 1]["close"]
            if not prev_close:
                continue
            pct = (candles[i]["close"] - prev_close) / prev_close * 100
            actual = 1 if pct > NEUTRAL_TOL else (-1 if pct < -NEUTRAL_TOL else 0)
            sox = tdb.us_asof(conn, "sox", d)
            assert sox is None or sox["date"] < d, "look-ahead: sox"
            k = len([x for x in mdates if x < d])
            assert k == 0 or mdates[k - 1] < d, "look-ahead: margin"
            m5 = None
            if k >= 6:
                bal_then = margins[k - 6]["margin_balance"]
                chg5 = sum(m["margin_chg"] or 0 for m in margins[k - 5:k])
                m5 = chg5 / bal_then * 100 if bal_then else None  # 5 日融資增幅 %
            days.append({"date": d, "actual": actual, "sox": sox["change_pct"] if sox else None,
                         "m5": m5})
    return [s for s in days if s["m5"] is not None]


def run() -> None:
    days = build_days()
    print(f"樣本 {days[0]['date']} ~ {days[-1]['date']}  n={len(days)}")
    nn = [s for s in days if s["actual"] != 0]
    base_up = sum(1 for s in nn if s["actual"] == 1) / len(nn)
    base_dn = 1 - base_up
    print(f"非中性日 n={len(nn)}  上漲率 {base_up:.1%}／下跌率 {base_dn:.1%}")

    print("\n── 融資 5 日急增（反指標 → 押次日跌）──")
    for thr in (3.0, 5.0, 8.0):
        sub = [s for s in nn if s["m5"] >= thr]
        if not sub:
            print(f"  m5 ≥ +{thr:.0f}%：n=0")
            continue
        dn = sum(1 for s in sub if s["actual"] == -1) / len(sub)
        p = bt._binom_two_sided_p(round(dn * len(sub)), len(sub), base_dn)
        print(f"  m5 ≥ +{thr:.0f}%：n={len(sub):3d}  次日下跌率 {dn:.1%}（Δ {dn - base_dn:+.1%}, p={p:.3f}）")

    print("── 融資 5 日急減（去槓桿 → 押次日漲）──")
    for thr in (-3.0, -5.0, -8.0):
        sub = [s for s in nn if s["m5"] <= thr]
        if not sub:
            print(f"  m5 ≤ {thr:.0f}%：n=0")
            continue
        up = sum(1 for s in sub if s["actual"] == 1) / len(sub)
        p = bt._binom_two_sided_p(round(up * len(sub)), len(sub), base_up)
        print(f"  m5 ≤ {thr:.0f}%：n={len(sub):3d}  次日上漲率 {up:.1%}（Δ {up - base_up:+.1%}, p={p:.3f}）")

    # 連續訊號整體檢：sign(−m5)（增→空、減→多），70/30 OOS＋決斷/平淡拆層
    def hit(seg):
        a = h = 0
        for s in seg:
            if s["m5"] == 0 or s["actual"] == 0:
                continue
            a += 1
            if (-1 if s["m5"] > 0 else 1) == s["actual"]:
                h += 1
        return (h / a if a else 0.0), a

    cut = int(len(days) * 0.7)
    print("\n── 連續訊號 sign(−m5) ──")
    for name, seg in (("全窗", days), ("in-sample(70%)", days[:cut]), ("OOS(30%)", days[cut:])):
        r, a = hit(seg)
        p = bt._binom_two_sided_p(round(r * a), a, 0.5) if a else 1.0
        print(f"  {name:14s} 命中 {r:.1%} (active={a}, p={p:.3f})")
    dec = [s for s in days if s["sox"] is not None and abs(s["sox"]) >= DECISIVE_THR]
    flat = [s for s in days if s["sox"] is not None and abs(s["sox"]) < DECISIVE_THR]
    (rd, ad), (rf, af) = hit(dec), hit(flat)
    print(f"  決斷夜        命中 {rd:.1%} (active={ad})  平淡夜 命中 {rf:.1%} (active={af})")


if __name__ == "__main__":
    run()
