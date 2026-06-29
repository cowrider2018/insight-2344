"""個股策略建置（Step 1）：對一支股票，把專案所有方法全跑一遍，挑出最高勝率方案，
寫成該股專屬 data/<symbol>/strategy.json，供排程分析（Step 2）使用。

用法（先 set STOCK_SYMBOL=XXXX，預設 2344）：
    python src/strategy_builder.py --backfill          # 回補該股所有資料（首次必跑，較慢）
    python src/strategy_builder.py --calibrate         # 校準十面權重（寫 weights/score_params）
    python src/strategy_builder.py --build [--quick]    # 跑全部方法、挑最佳、寫 strategy.json
    python src/strategy_builder.py --full               # 上面三步一次做完

評估的方法電池（全部沿用既有模組，依 symbol 自動切換）：
  十面評分+顯著性護欄(backtest)、信心分層(confidence)、隔夜決斷度勝率(swing_risk)、
  每日選邊 regime 切換(daily_decision.analyze/oos)、跨年複核、外資背離、平淡夜逐訊號。
挑選邏輯：以 OOS 為準，找該股「同日方向」最高且穩健的可部署規則，並判定該股屬
  『隔夜美股 beta 主導』或『籌碼/個股 alpha 可用』型，據此定每日決策規則。
"""
from __future__ import annotations

import json
import sys

import backtest as bt
import config
import daily_decision as dd
import scoring
import swing_risk
import timeline_db as tdb


# ---------------- 資料回補 ----------------

def backfill_all() -> None:
    """回補當前 symbol 的所有面資料（沿用既有 ingest；皆以 config.SYMBOL 運作）。"""
    import ingest
    print(f"[builder] 回補 {config.SYMBOL} {config.NAME} 全面資料 ...")
    ingest.backfill_candles()                       # 日 K（Fugle，需金鑰）
    ingest.backfill_us()                            # 美光/費半（共用）
    ingest.backfill_chips()                         # 三大法人/融資券（TWSE 逐日）
    ingest.backfill_tdcc()                          # TDCC 千張大戶（逐週）
    ingest.backfill_futures()                       # 外資台指期（共用）
    ingest.backfill_branches()                      # 主力分點（DJ 最新一日）
    try:
        ingest.backfill_intraday()                  # 日內 1 分 K（近期）
    except Exception as e:  # noqa: BLE001
        print(f"  intraday 略過: {e}")
    # 主力分點 walk-forward 行為分數
    try:
        import branch_model
        branch_model.main([])
    except Exception as e:  # noqa: BLE001
        print(f"  branch_model 略過: {e}")


def _window():
    with tdb.connect() as conn:
        ds = [r["date"] for r in tdb.candles_upto(conn, config.SYMBOL)]
    if not ds:
        return None, None
    return ds[0], ds[-1]


# ---------------- 方法電池評估 ----------------

def evaluate(start: str, end: str, quick: bool = False) -> dict:
    """跑全部方法，回傳各方案勝率與診斷（資料須先回補/校準）。"""
    out: dict = {"window": [start, end]}
    with tdb.connect() as conn:
        samples, coverage = bt.build_samples(conn, config.SYMBOL, start, end, bt.NEUTRAL_TOL)
    if len(samples) < 30:
        return {"error": f"{config.SYMBOL} 樣本不足（{len(samples)}），請先 --backfill"}
    out["n_days"] = len(samples)
    out["coverage"] = coverage

    # 1) 十面評分 + 顯著性護欄 + 採用權重 + 信心分層
    results = bt.optimize(samples)
    results, guard = bt.apply_guard(samples, results)
    best = bt.pick_balanced(results, 0.0)
    out["model"] = {
        "weights": best["weights"], "tau": best["tau"],
        "win_rate_full_stance": best["win_rate"],
        "directional_hit_rate": best["directional_hit_rate"],
        "significant_faces": [d for d in scoring.DIMENSIONS
                              if guard["eligibility"][d]["significant"]],
        "dim_significance": {d: {"significant": g["significant"], "hit_rate": g["hit_rate"],
                                 "active": g["active"]}
                             for d, g in guard["eligibility"].items()},
    }
    ct = bt.confidence_diagnostics(samples, best["weights"], best["tau"])
    out["confidence_tiers"] = ct

    # 2) 每日選邊 regime 切換（in-sample）
    out["daily_decision_insample"] = dd.analyze(start, end)

    # 3) 隔夜決斷度同日方向勝率（開盤/全日）
    out["overnight_accuracy"] = swing_risk.accuracy()

    # 3.5) 門檻×視窗(開盤/全日)掃描：每股最佳決斷門檻與交易視窗（含基準率校正＋OOS）
    out["threshold_horizon"] = swing_risk.scan_threshold_horizon()

    # 4) 外資背離（東買西賣）測試（tuple key 轉可序列化字串）
    try:
        dv = dd.divergence_analysis()
        lab = {(1, 1): "US漲+外資買", (1, -1): "US漲+外資賣(東買西賣)",
               (-1, 1): "US跌+外資買", (-1, -1): "US跌+外資賣"}
        out["divergence"] = {
            lab.get(k, str(k)): {
                "n": v[0],
                "up_rate": round(v[1] / v[0], 4) if v[0] else 0.0,
                "follow_overnight": round(v[2] / v[0], 4) if v[0] else 0.0,
                "follow_foreign": round(v[3] / v[0], 4) if v[0] else 0.0,
            } for k, v in dv.items()}
    except Exception as e:  # noqa: BLE001
        out["divergence_error"] = str(e)

    # 5) 平淡夜逐訊號（找該股平淡夜是否有可用訊號）
    try:
        out["flat_night_signals"] = dd.flat_night_diagnostics(start, end)["signals"][:6]
    except Exception as e:  # noqa: BLE001
        out["flat_night_error"] = str(e)

    # 6) OOS（train/test）——誠實裁決（慢，--quick 可略）
    if not quick:
        out["oos"] = dd.oos(start, end)

    return out


# ---------------- 挑選最佳策略 ----------------

def choose(ev: dict) -> dict:
    """依評估結果挑該股可部署規則並分型。以 OOS 為準（無 OOS 退回 in-sample）。"""
    oos = ev.get("oos")
    if oos:
        dec = oos["test_composite"]["decisive"]["win"]
        flat = oos["test_composite"]["flat"]["win"]
        comb = oos["test_composite"]["combined"]["win"]
        basis = "OOS"
    else:
        di = ev["daily_decision_insample"]
        dec, flat, comb = di["segment_decisive"]["win"], di["segment_flat"]["win"], di["combined_every_day"]["win"]
        basis = "in-sample"

    sig = set(ev["model"]["significant_faces"])
    chip_faces = {"chips", "branch", "holders", "futures"}
    # 判定該股是否有「籌碼/個股 alpha」：平淡夜（無隔夜時）明顯優於擲幣，且有顯著籌碼面
    chip_useful = flat >= 0.55 and bool(sig & chip_faces)
    stock_type = "chip_alpha_available" if chip_useful else "overnight_beta_dominated"

    flat_action = ("平淡夜可用籌碼訊號選邊（該股籌碼面有 edge）"
                   if chip_useful else "平淡夜保守/縮量（無獨立 edge，~擲幣）")
    drv = ev.get("overnight_driver", {}).get("best", "sox")
    th = ev.get("threshold_horizon", {}).get("recommend", {})
    rec_thr = th.get("thr", dd.DECISIVE_THR)
    horizon = th.get("horizon", "day")
    win_window = "全日" if horizon == "day" else "開盤跳空"
    return {
        "type": stock_type,
        "basis": basis,
        "decisive_thr": rec_thr,
        "overnight_driver": drv,
        "trade_window": horizon,                # day=全日 / open=賺開盤跳空
        "window_winrate_oos": th.get("win_test"),
        "open_vs_day_oos": {"open": th.get("open_win_test"), "day": th.get("day_win_test")},
        "open_asymmetry": {"up_driver_open_up": th.get("open_up_rate"),
                           "down_driver_open_down": th.get("open_down_rate"),
                           "base_open_up": ev.get("threshold_horizon", {}).get("base_open_up_rate")},
        "rule": (f"決斷夜（|{drv}|≥{rec_thr}%）跟驅動方向、重押，交易視窗＝**{win_window}**"
                 f"（OOS {th.get('win_test',0):.0%}）；" + flat_action),
        "expected_winrate": {"combined": round(comb, 4), "decisive_night": round(dec, 4),
                             "flat_night": round(flat, 4)},
        "rationale": (f"決斷夜 {dec:.0%}、平淡夜 {flat:.0%}、合併 {comb:.0%}（{basis}）；"
                      f"顯著面={sorted(sig)}；"
                      + ("籌碼面有 edge、平淡夜可用。" if chip_useful
                         else "籌碼面無獨立 edge、由隔夜美股主導。")),
    }


def build(start: str | None = None, end: str | None = None, quick: bool = False) -> dict:
    s, e = _window()
    start, end = start or s, end or e
    if not start:
        print("[builder] 無日 K，請先 --backfill")
        return {}
    # 泛用化：選該股最佳隔夜驅動並套用到 regime 閘門與 swing_risk（不再寫死費半）
    import overnight_driver
    drv = overnight_driver.select_best(config.SYMBOL)
    swing_risk.US_KEY = drv["best"]
    dd.OVERNIGHT_KEY = drv["best"]
    print(f"[builder] {config.SYMBOL} 最佳隔夜驅動：{drv['best']}（corr {drv['corr']}）")
    print(f"[builder] 評估 {config.SYMBOL} {config.NAME}  {start}~{end} ...")
    ev = evaluate(start, end, quick)
    if not ev.get("error"):
        ev["overnight_driver"] = drv
    if ev.get("error"):
        print("[builder]", ev["error"])
        return ev
    ev["symbol"] = config.SYMBOL
    ev["name"] = config.NAME
    ev["built_at"] = config.now_tpe().isoformat()
    ev["chosen_strategy"] = choose(ev)
    config.strategy_path().write_text(json.dumps(ev, ensure_ascii=False, indent=2,
                                                 default=str), encoding="utf-8")
    cs = ev["chosen_strategy"]
    print(f"[builder] {config.SYMBOL} {config.NAME} 策略已建立 -> {config.strategy_path()}")
    print(f"  類型：{cs['type']}")
    print(f"  規則：{cs['rule']}")
    print(f"  預期勝率（{cs['basis']}）：合併 {cs['expected_winrate']['combined']:.0%}"
          f"／決斷夜 {cs['expected_winrate']['decisive_night']:.0%}"
          f"／平淡夜 {cs['expected_winrate']['flat_night']:.0%}")
    print(f"  顯著面：{ev['model']['significant_faces']}")
    return ev


def main(argv: list[str]) -> None:
    if "--full" in argv or "--backfill" in argv:
        backfill_all()
    if "--full" in argv or "--calibrate" in argv:
        import calibrate
        s, e = _window()
        if s:
            calibrate.main(["--start", s, "--end", e, "--rounds", "2"])
    if "--full" in argv or "--build" in argv or not any(a.startswith("--") for a in argv[1:]):
        build(quick="--quick" in argv)


if __name__ == "__main__":
    main(sys.argv)
