"""EXP-026 平淡夜 MU 救援（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：SOX 平淡（|SOX|<1%，現行平淡夜規則用十面 composite，~53–57%）但美光大動
（|MU|≥門檻）＝記憶體株特異隔夜訊號（財報、記憶體報價新聞等 SOX 未反映的資訊），
跟 MU 方向可救平淡夜勝率。與已否決「十面綜合救援」不同：這是單一驅動的 magnitude 條件化。

資料：xs.db 2344 收盤 2 年＋timeline.db us_market（mu 2024-06-27~2026-06-26 501 筆、
micron 2025-06-25~2026-07-09 261 筆，合併去重）＋sox/soxx 同理。全部 as-of `date < D`（assert）。

門檻預先註冊：|MU| ≥ 1.5 / 2.0 / 3.0。
關卡：②顯著性（active≥8, p<0.15, hit>0.5）③70/30 時序 OOS＋分年一致性
④影子檢驗（該子集內 sign(小SOX) 與 sign(MU) 的一致率；若 MU 只是放大的小SOX 則 REJECT）。

用法：
  python research/exp_026_flat_mu_rescue.py
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
FLAT_THR = 1.0          # |SOX| < 此值 = 平淡夜（同 daily_decision.DECISIVE_THR）
MU_THRS = (1.5, 2.0, 3.0)


def binom_p(hit: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 1.0
    lo, hi = min(hit, n - hit), max(hit, n - hit)
    tail = sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(hi, n + 1))
    return min(1.0, 2 * tail)


def load_us(conn, keys: tuple[str, ...]) -> list[tuple[str, float]]:
    """合併多個 us_market symbol 鍵（如 mu＋micron），同日取後者，回傳按日排序 (date, pct)。"""
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
        sox_series = load_us(conn, ("sox", "soxx"))  # soxx ETF 補 2y 前段，近期以 sox 指數覆蓋
    with xs_db.connect() as c:
        rows = c.execute("SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
                         (config.SYMBOL,)).fetchall()
    closes = [(r["date"], r["close"]) for r in rows if r["close"]]
    print(f"2344 收盤 {len(closes)} 日（{closes[0][0]}~{closes[-1][0]}）　"
          f"MU {len(mu_series)} 筆　SOX/SOXX {len(sox_series)} 筆")

    days = []
    for i in range(1, len(closes)):
        d, close = closes[i]
        prev = closes[i - 1][1]
        pct = (close - prev) / prev * 100
        actual = 1 if pct > NEUTRAL else (-1 if pct < -NEUTRAL else 0)
        sox = asof(sox_series, d)
        mu = asof(mu_series, d)
        # look-ahead 稽核
        assert sox is None or max(sd for sd, _ in sox_series if sd < d) < d
        assert mu is None or max(sd for sd, _ in mu_series if sd < d) < d
        days.append({"date": d, "actual": actual, "sox": sox, "mu": mu})

    # 平淡夜（|SOX|<1%）且方向性有效日
    flat = [x for x in days if x["actual"] != 0 and x["sox"] is not None
            and abs(x["sox"]) < FLAT_THR and x["mu"] is not None]
    base_hit = sum(1 for x in flat if (1 if x["sox"] > 0 else -1) == x["actual"])
    print(f"\n平淡夜方向性日 n={len(flat)}　（對照）跟小SOX命中率={base_hit/len(flat):.2%}")

    def eval_subset(sub, tag):
        if not sub:
            print(f"    {tag}: n=0")
            return
        hit = sum(1 for x in sub if (1 if x["mu"] > 0 else -1) == x["actual"])
        agree = sum(1 for x in sub if (x["mu"] > 0) == (x["sox"] > 0))
        sox_hit = sum(1 for x in sub if (1 if x["sox"] > 0 else -1) == x["actual"])
        print(f"    {tag}: n={len(sub):3d}  跟MU={hit/len(sub):.2%}(p={binom_p(hit, len(sub)):.3f})  "
              f"跟小SOX={sox_hit/len(sub):.2%}  MU/SOX同號率={agree/len(sub):.0%}")

    for thr in MU_THRS:
        sub = [x for x in flat if abs(x["mu"]) >= thr]
        print(f"\n── |MU|>={thr}% ──")
        eval_subset(sub, "全窗")
        # 分年一致性
        mid = "2025-07-01"
        eval_subset([x for x in sub if x["date"] < mid], "早一年")
        eval_subset([x for x in sub if x["date"] >= mid], "近一年")
        # 70/30 時序
        k = int(len(sub) * 0.7)
        eval_subset(sub[:k], "train70")
        eval_subset(sub[k:], "test30")


if __name__ == "__main__":
    main()
