"""逐步校準評分參數（coordinate-ascent），把規則調適到最高命中率。

理由：六面評分是手寫啟發式規則（非訓練出的金融模型），門檻/尺度未必最佳。
本工具對 data/score_params.json 內各參數做座標上升：固定其他、逐一試候選值、留最佳，
反覆數輪收斂。校準時用「粗網格」評分以加速；收斂後寫回參數並以「完整網格 + 平衡權重」
重跑回測，產生 data/weights.json 與報告。

用法:
    python src/calibrate.py --start 2025-07-01 --end 2026-06-23 [--rounds 2]
"""
from __future__ import annotations

import json
import sys

import backtest as bt
import config
import scoring
import timeline_db as tdb

# 校準用粗網格（加速）；收斂後最終仍以完整網格重算
COARSE_STEP = 5
COARSE_TAU = [0.05, 0.15, 0.25]

# 每個參數的候選值（座標上升逐一嘗試）
CANDIDATES = {
    "us_scale": [2.0, 2.5, 3.0, 4.0, 5.0],
    "kd_div": [5.0, 8.0, 12.0],
    "rsi_div": [20.0, 30.0, 40.0],
    "macd_price_frac": [0.005, 0.01, 0.02],
    "bias_div": [10.0, 20.0, 40.0],
    "news_smoothing": [1.0, 3.0, 5.0],
    "chips_inst_frac": [0.10, 0.15, 0.25],
    "w_ma": [0.20, 0.30, 0.40],
    "w_kd": [0.10, 0.20, 0.30],
    "w_macd": [0.10, 0.20, 0.30],
    "w_bias": [0.05, 0.10, 0.20],       # 乖離（均值回歸）權重
    "w_volprice": [0.05, 0.10, 0.20],   # 量價權重
    "volprice_div": [0.5, 1.0, 2.0],    # 量價放大倍率（量增/量縮對方向的敏感度）
    # 第七面：日內 1 分 K 子訊號尺度與權重（含 0 讓校準自動取捨）
    "id_vwap_div": [0.005, 0.01, 0.02],
    "id_tail_div": [0.005, 0.01, 0.02],
    "id_trend_div": [0.01, 0.02, 0.04],
    "id_volconc_div": [0.2, 0.3, 0.5],
    "w_id_vwap": [0.0, 0.25, 0.40],
    "w_id_pos": [0.0, 0.20, 0.40],
    "w_id_tail": [0.0, 0.25, 0.40],
    "w_id_trend": [0.0, 0.20, 0.40],
    "w_id_volconc": [0.0, 0.10, 0.20],
    # 第八面：主力分點
    "branch_net_div": [10000.0, 20000.0, 40000.0],
    "branch_grp_div": [4000.0, 8000.0, 16000.0],
    "branch_smart_div": [6000.0, 12000.0, 24000.0],
    "w_br_net": [0.0, 0.25, 0.50],
    "w_br_conc": [0.0, 0.20, 0.40],
    "w_br_smart": [0.0, 0.45, 0.70],
    "w_br_daytrade": [0.0, 0.05, 0.20],
    "w_br_longterm": [0.0, 0.05, 0.20],
}


def objective(feats: list[dict], params: dict) -> float:
    """以該參數評分後，粗網格能達到的最高命中率（params 使訊號越準，上限越高）。

    套用第七面護欄：不顯著時不讓 intraday 權重灌水，校準才不會追逐 30 日小樣本雜訊。
    """
    samples = bt.score_samples(feats, params)
    res = bt.optimize(samples, step=COARSE_STEP, tau_grid=COARSE_TAU)
    res, _ = bt.apply_guard(samples, res)
    return res[0]["win_rate"]


def calibrate(feats: list[dict], rounds: int = 2) -> tuple[dict, list[str]]:
    params = dict(scoring.DEFAULT_PARAMS)
    base = objective(feats, params)
    trail = [f"起始命中率 {base:.2%}"]
    cur = base
    for rd in range(1, rounds + 1):
        for key, cands in CANDIDATES.items():
            best_v, best_obj = params[key], cur
            for v in cands:
                if v == params[key]:
                    continue
                trial = dict(params)
                trial[key] = v
                obj = objective(feats, trial)
                if obj > best_obj + 1e-9:
                    best_obj, best_v = obj, v
            if best_v != params[key]:
                trail.append(f"R{rd} {key}: {params[key]} -> {best_v}  命中率 {cur:.2%} -> {best_obj:.2%}")
                params[key] = best_v
                cur = best_obj
        trail.append(f"== 第 {rd} 輪結束，命中率 {cur:.2%} ==")
    trail.append(f"校準完成：{base:.2%} -> {cur:.2%}（粗網格）")
    return params, trail


def main(argv: list[str]) -> None:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    start = opt("--start", "2025-07-01")
    end = opt("--end", config.today_str()[:4] + "-12-31")
    tol = float(opt("--tol", str(bt.NEUTRAL_TOL)))
    rounds = int(opt("--rounds", "2"))
    balance_tol = float(opt("--balance-tol", "0.0"))  # 命中率第一優先（同前）

    tdb.init_db()
    with tdb.connect() as conn:
        feats, coverage = bt.extract_features(conn, config.SYMBOL, start, end, tol)
    if not feats:
        print("[calibrate] 無樣本：請先回補 candles/chips/us。")
        return

    print(f"[calibrate] 樣本 {len(feats)} 日，開始座標上升（{rounds} 輪）...")
    params, trail = calibrate(feats, rounds)
    for line in trail:
        print("  " + line)

    # 寫回參數
    pfile = config.DATA_DIR / "score_params.json"
    pfile.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    scoring.PARAMS = params  # 使後續以調適後參數評分

    # 以調適後參數 + 完整網格 + 平衡權重重算
    samples = bt.score_samples(feats, params)
    results = bt.optimize(samples)
    results, guard = bt.apply_guard(samples, results)   # 顯著性護欄（含第七面）
    best = results[0]
    balanced = bt.pick_balanced(results, balance_tol)
    diagnostics = bt.signal_diagnostics(samples)

    out = {
        "symbol": config.SYMBOL,
        "weights": balanced["weights"],
        "neutral_threshold": balanced["tau"],
        "as_of": config.now_tpe().isoformat(),
        "win_rate": balanced["win_rate"],
        "directional_hit_rate": balanced["directional_hit_rate"],
        "directional_coverage": balanced["directional_coverage"],
        "n_days": len(samples),
        "window": [start, end],
        "actual_neutral_tol_pct": tol,
        "balance_tol": balance_tol,
        "raw_best": {"weights": best["weights"], "tau": best["tau"], "win_rate": best["win_rate"]},
        "coverage": coverage,
        "blocked_dims": guard["blocked"],
        "intraday_significant": guard["eligibility"]["intraday"]["significant"],
        "intraday_active": guard["eligibility"]["intraday"]["active"],
        "intraday_hit_rate": guard["eligibility"]["intraday"]["hit_rate"],
        "dim_significance": {d: {"significant": g["significant"], "active": g["active"],
                                 "hit_rate": g["hit_rate"]}
                             for d, g in guard["eligibility"].items()},
        "calibrated": True,
        "score_params_file": "data/score_params.json",
    }
    (config.DATA_DIR / "weights.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = config.REPORTS_DIR / f"backtest_{start}_{end}.md"
    bt.write_report(samples, coverage, results, start, end, tol, report_path, balanced, diagnostics)

    print(f"[calibrate] 完整網格結果：純最佳 {best['win_rate']:.2%}，平衡(採用) {balanced['win_rate']:.2%}")
    print("  平衡權重 " + "/".join(f"{bt._DIM_ZH[d]}{balanced['weights'][d]:.1f}"
                                  for d in scoring.DIMENSIONS) + f"  tau={balanced['tau']}")
    print(f"  -> {pfile}")
    print(f"  -> {config.DATA_DIR / 'weights.json'}")
    print(f"  -> {report_path}")


if __name__ == "__main__":
    main(sys.argv)
