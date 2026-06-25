"""樣本外驗證：只用 train 校準參數+選平衡權重，套到 held-out test，檢查是否過擬合。

OOS 命中率須明顯高於 test 偏多基準，且不應遠低於 in-sample。
OOS ≈ in-sample 或更高 = 沒過擬合（理想）。

用法:
    python src/oos_check.py --start 2025-07-01 --end 2026-06-23 [--split 0.7] [--rounds 2]
"""
from __future__ import annotations

import collections
import sys

import backtest as bt
import calibrate as cal
import config
import scoring
import timeline_db as tdb


def main(argv: list[str]) -> None:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    start = opt("--start", "2025-07-01")
    end = opt("--end", config.today_str()[:4] + "-12-31")
    tol = float(opt("--tol", str(bt.NEUTRAL_TOL)))
    split = float(opt("--split", "0.7"))
    rounds = int(opt("--rounds", "2"))

    tdb.init_db()
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, start, end, tol)
    if len(feats) < 20:
        print("[oos_check] 樣本太少，無法切分驗證。")
        return

    k = int(len(feats) * split)
    tr, te = feats[:k], feats[k:]
    # 註：第八面 smart 用 branch_model 的 walk-forward 分數（branch_wf 表，僅用 ≤d 資料），
    # 本身無 look-ahead，故 OOS 不需特別覆寫。

    params, _ = cal.calibrate(tr, rounds)             # 只在 train 校參
    tr_samples = bt.score_samples(tr, params)
    res = bt.optimize(tr_samples)                     # 只在 train 選權重
    res, _ = bt.apply_guard(tr_samples, res)          # 同採用模型：顯著性護欄（只看 train）
    bal = bt.pick_balanced(res, 0.0)                  # 命中率第一優先（同採用模型）

    ins = bt.evaluate(bt.score_samples(tr, params), bal["weights"], bal["tau"])
    oos = bt.evaluate(bt.score_samples(te, params), bal["weights"], bal["tau"])
    d = collections.Counter(s["actual"] for s in te)
    base = d[1] / len(te) if te else 0.0

    w = bal["weights"]
    print(f"[oos_check] train {len(tr)} 日 / test {len(te)} 日（split={split}）")
    print("  權重(train) " + "/".join(f"{bt._DIM_ZH[dim]}{w[dim]:.1f}"
                                      for dim in scoring.DIMENSIONS) + f"  tau={bal['tau']}")
    print(f"  in-sample(train) 命中率 {ins['win_rate']:.2%}")
    print(f"  OUT-OF-SAMPLE(test) 命中率 {oos['win_rate']:.2%}  方向命中 {oos['directional_hit_rate']:.2%}")
    print(f"  test 偏多基準 {base:.2%}")
    verdict = ("[OK] 未過擬合（OOS >= in-sample）" if oos["win_rate"] >= ins["win_rate"] - 0.02
               else "[WARN] OOS 低於 in-sample，留意過擬合")
    if oos["win_rate"] <= base + 0.01:
        verdict = "[FAIL] OOS 未顯著高於基準，模型無有效邊際"
    print("  判定：" + verdict)


if __name__ == "__main__":
    main(sys.argv)
