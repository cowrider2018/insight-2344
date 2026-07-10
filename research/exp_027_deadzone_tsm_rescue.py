"""EXP-027 平淡夜死區 TSM 救援（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：EXP-026 發現平淡夜 composite 實質主驅動是 micron，真死區＝「SOX 平（|SOX|<1%）
且 MU 平（|MU|<3%）」。死區內若台積電 ADR 大動（|TSM|≥門檻）＝台股特異通道訊號
（TSMC 財報、台灣宏觀、地緣），跟 TSM 方向可救死區勝率。TSM 不在現行十面體系內，
若有訊號必然非冗餘（EXP-026 教訓：先驗證增量非冗餘）。

門檻預先註冊：|TSM| ≥ 1.5 / 2.0 / 3.0。
資料：xs.db 2344 收盤 2 年＋us_market tsm 501 筆＋mu/micron、sox/soxx 合併；as-of `date < D`。

用法：
  python research/exp_027_deadzone_tsm_rescue.py
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
TSM_THRS = (1.5, 2.0, 3.0)


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


def asof(series: list[tuple[str, float]], d: str) -> float | None:
    v = None
    for sd, pct in series:
        if sd < d:
            v = pct
        else:
            break
    return v


def main() -> None:
    tdb.init_db()
    with tdb.connect() as conn:
        mu_series = load_us(conn, ("mu", "micron"))
        sox_series = load_us(conn, ("sox", "soxx"))
        tsm_series = load_us(conn, ("tsm",))
    with xs_db.connect() as c:
        rows = c.execute("SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
                         (config.SYMBOL,)).fetchall()
    closes = [(r["date"], r["close"]) for r in rows if r["close"]]
    print(f"2344 {len(closes)} 日　TSM {len(tsm_series)} 筆（{tsm_series[0][0]}~{tsm_series[-1][0]}）")

    dead = []
    for i in range(1, len(closes)):
        d, close = closes[i]
        prev = closes[i - 1][1]
        pct = (close - prev) / prev * 100
        actual = 1 if pct > NEUTRAL else (-1 if pct < -NEUTRAL else 0)
        if actual == 0:
            continue
        sox, mu, tsm = asof(sox_series, d), asof(mu_series, d), asof(tsm_series, d)
        if sox is None or mu is None or tsm is None:
            continue
        if abs(sox) >= FLAT_SOX or abs(mu) >= FLAT_MU:
            continue
        dead.append({"date": d, "actual": actual, "sox": sox, "mu": mu, "tsm": tsm})

    # 死區基準：跟小SOX／跟小MU 的命中率（對照）
    n = len(dead)
    sox_hit = sum(1 for x in dead if (1 if x["sox"] > 0 else -1) == x["actual"])
    mu_hit = sum(1 for x in dead if x["mu"] != 0 and (1 if x["mu"] > 0 else -1) == x["actual"])
    print(f"\n死區（|SOX|<1 且 |MU|<3）方向性日 n={n}")
    print(f"對照：跟小SOX={sox_hit/n:.2%}　跟小MU={mu_hit/n:.2%}")

    def eval_subset(sub, tag):
        if not sub:
            print(f"    {tag}: n=0")
            return
        hit = sum(1 for x in sub if (1 if x["tsm"] > 0 else -1) == x["actual"])
        print(f"    {tag}: n={len(sub):3d}  跟TSM={hit/len(sub):.2%}(p={binom_p(hit, len(sub)):.3f})")

    for thr in TSM_THRS:
        sub = [x for x in dead if abs(x["tsm"]) >= thr]
        print(f"\n── |TSM|>={thr}% ──")
        eval_subset(sub, "全窗")
        mid = "2025-07-01"
        eval_subset([x for x in sub if x["date"] < mid], "早一年")
        eval_subset([x for x in sub if x["date"] >= mid], "近一年")
        k = int(len(sub) * 0.7)
        eval_subset(sub[:k], "train70")
        eval_subset(sub[k:], "test30")


if __name__ == "__main__":
    main()
