"""EXP-015 預測命中動能（推理流程 overlay）— 研究腳本（非 production，禁止被 daily path import）

假設：模型近期預測命中動能（近 5 個「有表態」交易日的方向命中率，僅用 D 之前已知的
predictions/actuals，無 look-ahead）可預測次日命中率，可作為信心/conviction 的 overlay
（連續命中後升高信心／連續未命中後降級觀望）。關卡④：此效應須獨立於決斷夜（|SOX|>=1%）
regime——若熱/冷動能只是「決斷夜聚集」的影子，則 REJECT。

用法：
  python research/exp_015_prediction_momentum.py
"""
from __future__ import annotations

import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import backtest as bt  # noqa: E402
import config  # noqa: E402
import scoring  # noqa: E402
import timeline_db as tdb  # noqa: E402

START = "2025-07-01"
WINDOW = 5          # rolling 命中動能視窗（近 N 個「有表態」交易日）
HOT_TH = 0.8         # 動能 >= 此值 = 熱
COLD_TH = 0.2        # 動能 <= 此值 = 冷


def binom_p(hit: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 1.0
    lo, hi = min(hit, n - hit), max(hit, n - hit)
    tail = sum(comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k)) for k in range(hi, n + 1))
    return min(1.0, 2 * tail)


def main() -> None:
    tdb.init_db()
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, START, config.today_str()[:4] + "-12-31", bt.NEUTRAL_TOL)
        weights_path = config.ROOT / "data" / config.SYMBOL / "weights.json"
        import json
        wcfg = json.loads(weights_path.read_text(encoding="utf-8"))
        weights, tau = wcfg["weights"], wcfg["raw_best"]["tau"]
        samples = bt.score_samples(feats, scoring.PARAMS)

    # --- 逐日以 production 權重/tau 產生 (pred, actual, sox_abs)；全部只用 < D 資料 ---
    rows = []
    for f, s in zip(feats, samples):
        pred, _ = scoring.combine(s["scores"], weights, tau)
        sox = f.get("sox")
        rows.append({
            "date": s["date"], "pred": pred, "actual": s["actual"],
            "sox_abs": abs(sox["change_pct"]) if sox and sox.get("change_pct") is not None else None,
        })

    directional = [r for r in rows if r["pred"] != 0]
    print(f"總天數={len(rows)}　有表態天數={len(directional)}")

    # --- look-ahead 稽核：動能只用嚴格早於 D 的 directional 歷史 ---
    hist: list[int] = []  # 1=命中 0=未命中，依時間序累積（僅 directional 天）
    bucketed = {"hot": [], "mid": [], "cold": []}
    for r in directional:
        if len(hist) >= WINDOW:
            momentum = sum(hist[-WINDOW:]) / WINDOW
            hit = 1 if r["pred"] == r["actual"] else 0
            bucket = "hot" if momentum >= HOT_TH else ("cold" if momentum <= COLD_TH else "mid")
            bucketed[bucket].append(r | {"momentum": momentum, "hit": hit})
        hist.append(1 if r["pred"] == r["actual"] else 0)

    base_n = sum(len(v) for v in bucketed.values())
    base_hit = sum(sum(x["hit"] for x in v) for v in bucketed.values())
    base_rate = base_hit / base_n if base_n else 0.0
    print(f"\n基準（有動能可查天數）方向命中率 {base_rate:.2%}（n={base_n}）")

    for name in ("hot", "cold", "mid"):
        v = bucketed[name]
        n = len(v)
        hit = sum(x["hit"] for x in v)
        rate = hit / n if n else 0.0
        p = binom_p(hit, n)
        print(f"  {name:5s} n={n:3d}  命中率={rate:.2%}  Δvs基準={rate - base_rate:+.1%}  p={p:.3f}")

    # --- 關卡④ SOX 影子檢驗：決斷夜 vs 平淡夜 分層下 hot/cold 是否仍有效 ---
    print("\n── 關卡④ SOX 影子分層 ──")
    for regime_name, cond in (("決斷夜(|SOX|>=1%)", lambda a: a is not None and a >= 1.0),
                               ("平淡夜(|SOX|<1%)", lambda a: a is not None and a < 1.0)):
        print(f" {regime_name}:")
        for name in ("hot", "cold"):
            v = [x for x in bucketed[name] if cond(x["sox_abs"])]
            n = len(v)
            if n == 0:
                print(f"   {name:5s} n=0")
                continue
            hit = sum(x["hit"] for x in v)
            rate = hit / n
            p = binom_p(hit, n)
            print(f"   {name:5s} n={n:3d}  命中率={rate:.2%}  p={p:.3f}")

    # --- 決斷夜比例是否隨動能 bucket 系統性偏移（熱/冷是否只是決斷夜聚集的影子）---
    print("\n── 決斷夜佔比（各 bucket）──")
    for name in ("hot", "cold", "mid"):
        v = bucketed[name]
        dec = [x for x in v if x["sox_abs"] is not None and x["sox_abs"] >= 1.0]
        frac = len(dec) / len(v) if v else 0.0
        print(f"  {name:5s} 決斷夜佔比={frac:.1%}（n={len(v)}）")


if __name__ == "__main__":
    main()
