"""EXP-028 平淡夜死區 SOX 累積漂移救援（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：死區（|SOX|<1% 且 |MU|<3%）的定義只看「昨晚單夜」，漏接慢速趨勢——近 3 個美股
交易日 SOX 累積漂移（每晚都 <1% 但 |合計|≥門檻）＝未被單夜規則捕捉的緩升/緩跌趨勢，
死區內跟累積方向可救勝率。與 EXP-008（2344 自身 streak）不同：這是隔夜驅動端的多日累積。

門檻預先註冊：|SOX 3日累積| ≥ 1.0 / 1.5 / 2.0。
資料：xs.db 2344 收盤 2 年＋us_market sox/soxx 合併、mu/micron 合併；as-of `date < D`。

用法：
  python research/exp_028_deadzone_sox_drift.py
"""
from __future__ import annotations

import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import timeline_db as tdb  # noqa: E402
import xs_db  # noqa: E402

NEUTRAL = 1.0
FLAT_SOX = 1.0
FLAT_MU = 3.0
DRIFT_THRS = (1.0, 1.5, 2.0)


def binom_p(hit: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 1.0
    lo, hi = min(hit, n - hit), max(hit, n - hit)
    tail = sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(hi, n + 1))
    return min(1.0, 2 * tail)


def load_us(conn, keys: tuple[str, ...]) -> list[tuple[str, float]]:
    merged: dict[str, float] = {}
    for k in keys:
        for d, pct in conn.execute(
                "SELECT date, change_pct FROM us_market WHERE symbol = ? ORDER BY date", (k,)):
            if pct is not None:
                merged[d] = pct
    return sorted(merged.items())


def last_n_before(series: list[tuple[str, float]], d: str, n: int) -> list[float]:
    """取 date < d 的最後 n 筆 change_pct（由舊到新）。"""
    vals = [pct for sd, pct in series if sd < d]
    return vals[-n:] if len(vals) >= n else []


def main() -> None:
    tdb.init_db()
    with tdb.connect() as conn:
        mu_series = load_us(conn, ("mu", "micron"))
        sox_series = load_us(conn, ("sox", "soxx"))
    with xs_db.connect() as c:
        rows = c.execute("SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
                         (config.SYMBOL,)).fetchall()
    closes = [(r["date"], r["close"]) for r in rows if r["close"]]

    dead = []
    for i in range(1, len(closes)):
        d, close = closes[i]
        prev = closes[i - 1][1]
        pct = (close - prev) / prev * 100
        actual = 1 if pct > NEUTRAL else (-1 if pct < -NEUTRAL else 0)
        if actual == 0:
            continue
        sox3 = last_n_before(sox_series, d, 3)
        mu1 = last_n_before(mu_series, d, 1)
        if len(sox3) < 3 or not mu1:
            continue
        sox_last = sox3[-1]
        if abs(sox_last) >= FLAT_SOX or abs(mu1[0]) >= FLAT_MU:
            continue
        dead.append({"date": d, "actual": actual, "drift": sum(sox3),
                     "all_flat": all(abs(v) < FLAT_SOX for v in sox3)})

    n = len(dead)
    print(f"死區方向性日 n={n}")

    def eval_subset(sub, tag):
        if not sub:
            print(f"    {tag}: n=0")
            return
        hit = sum(1 for x in sub if (1 if x["drift"] > 0 else -1) == x["actual"])
        print(f"    {tag}: n={len(sub):3d}  跟漂移={hit/len(sub):.2%}(p={binom_p(hit, len(sub)):.3f})")

    for thr in DRIFT_THRS:
        sub = [x for x in dead if abs(x["drift"]) >= thr]
        print(f"\n── |SOX 3日累積|>={thr}% ──")
        eval_subset(sub, "全窗")
        mid = "2025-07-01"
        eval_subset([x for x in sub if x["date"] < mid], "早一年")
        eval_subset([x for x in sub if x["date"] >= mid], "近一年")
        k = int(len(sub) * 0.7)
        eval_subset(sub[:k], "train70")
        eval_subset(sub[k:], "test30")
        # 純漂移子集：3 晚每晚都平（排除「前兩晚有大動、只是昨晚平」的混合情境）
        pure = [x for x in sub if x["all_flat"]]
        eval_subset(pure, "純漂移(3晚皆平)")


if __name__ == "__main__":
    main()
