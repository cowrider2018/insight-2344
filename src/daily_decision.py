"""每日選邊決策（regime 切換）＋ 回測，極大化同日方向勝率且每天都有訊息。

策略：每天都選邊（多/空），信心依 regime 分級：
  - **決斷夜**（|昨晚費半| ≥ decisive_thr，約半數日）：跟隨隔夜美股方向（記憶體族群開盤受 SOX 主導），
    信心高（歷史同日全日 ~71~72%、開盤 ~90~94%）。
  - **平淡夜**（|昨晚費半| < decisive_thr）：隔夜無方向，改用**十面綜合**（technical/chips/大戶/branch…）選邊，
    信心由 |composite| 大小定（高/中/低）。
此模組同時回測：分段勝率（決斷/平淡）、合併（每日全覆蓋）勝率，並與「全跟隔夜」「全用模型」對照。
"""
from __future__ import annotations

import json
import sys

import backtest as bt
import config
import scoring
import timeline_db as tdb

DECISIVE_THR = 1.0     # |昨晚費半%| ≥ 此值 = 決斷夜
NEUTRAL = 1.0          # 實際漲跌中性帶（方向性命中只計 |move|≥此值的日子）


def _load_weights() -> tuple[dict, float]:
    f = config.DATA_DIR / "weights.json"
    if f.exists():
        w = json.loads(f.read_text(encoding="utf-8"))
        return w["weights"], w.get("neutral_threshold", 0.15)
    return {d: (1.0 if d == "technical" else 0.0) for d in scoring.DIMENSIONS}, 0.15


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def decide(scores: dict, weights: dict, overnight_pct: float | None,
           decisive_thr: float = DECISIVE_THR) -> dict:
    """回傳每日選邊：side(+1/-1)、basis(overnight/model)、confidence(高/中/低)、composite。"""
    _, comp = scoring.combine(scores, weights, tau=0.0)   # tau=0 只取 composite
    if overnight_pct is not None and abs(overnight_pct) >= decisive_thr:
        side = _sign(overnight_pct) or _sign(comp) or 1
        conf = "高" if abs(overnight_pct) >= 2.0 else "中高"
        return {"side": side, "basis": "overnight", "confidence": conf,
                "composite": round(comp, 4), "overnight_pct": overnight_pct}
    # 平淡夜：用十面綜合
    side = _sign(comp) or 1
    conf = "中" if abs(comp) >= 0.20 else "低"
    return {"side": side, "basis": "model", "confidence": conf,
            "composite": round(comp, 4), "overnight_pct": overnight_pct}


def analyze(start: str, end: str, decisive_thr: float = DECISIVE_THR,
            neutral: float = NEUTRAL) -> dict:
    weights, _ = _load_weights()
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, start, end, neutral)
        samples = bt.score_samples(feats)
        ov = {}
        for f in feats:
            us = tdb.us_asof(conn, "sox", f["date"])
            ov[f["date"]] = us["change_pct"] if us and us.get("change_pct") is not None else None

    seg = {"decisive": [0, 0], "flat": [0, 0]}     # [hit, n_directional]
    combined = [0, 0]
    always_ov = [0, 0]
    always_model = [0, 0]
    forced_total = 0                                # 每日選邊總數（全覆蓋）
    for s in samples:
        actual = s["actual"]
        o = ov.get(s["date"])
        d = decide(s["scores"], weights, o, decisive_thr)
        forced_total += 1
        # 對照：全跟隔夜 / 全用模型（皆強制選邊）
        ov_side = _sign(o) if o is not None else 0
        model_side = _sign(d["composite"]) or 1
        if actual != 0:                            # 方向性命中只計實際非中性日
            if ov_side != 0:
                always_ov[1] += 1
                always_ov[0] += (ov_side == actual)
            always_model[1] += 1
            always_model[0] += (model_side == actual)
            combined[1] += 1
            combined[0] += (d["side"] == actual)
            key = "decisive" if d["basis"] == "overnight" else "flat"
            seg[key][1] += 1
            seg[key][0] += (d["side"] == actual)

    def rate(hn):
        return {"win": round(hn[0] / hn[1], 4) if hn[1] else 0.0, "n": hn[1]}

    n_days = len(samples)
    n_dec = sum(1 for s in samples if (ov.get(s["date"]) is not None
                                       and abs(ov[s["date"]]) >= decisive_thr))
    return {
        "window": [start, end], "n_days": n_days, "decisive_thr": decisive_thr,
        "neutral_band": neutral,
        "decisive_day_share": round(n_dec / n_days, 3) if n_days else 0.0,
        "combined_every_day": rate(combined),          # 每日選邊（全覆蓋）方向勝率
        "segment_decisive": rate(seg["decisive"]),     # 決斷夜（跟隔夜）
        "segment_flat": rate(seg["flat"]),             # 平淡夜（用十面模型）
        "baseline_always_overnight": rate(always_ov),
        "baseline_always_model": rate(always_model),
    }


def _eval_rule(samples, weights, ov, decisive_thr):
    """對 samples 套用決策規則，回傳 combined/decisive/flat/always_overnight 方向勝率。"""
    seg = {"decisive": [0, 0], "flat": [0, 0]}
    comb = [0, 0]
    aov = [0, 0]
    for s in samples:
        actual = s["actual"]
        if actual == 0:
            continue
        o = ov.get(s["date"])
        d = decide(s["scores"], weights, o, decisive_thr)
        comb[1] += 1
        comb[0] += (d["side"] == actual)
        key = "decisive" if d["basis"] == "overnight" else "flat"
        seg[key][1] += 1
        seg[key][0] += (d["side"] == actual)
        ovs = _sign(o) if o is not None else 0
        if ovs != 0:
            aov[1] += 1
            aov[0] += (ovs == actual)

    def r(hn):
        return {"win": round(hn[0] / hn[1], 4) if hn[1] else 0.0, "n": hn[1]}
    return {"combined": r(comb), "decisive": r(seg["decisive"]),
            "flat": r(seg["flat"]), "always_overnight": r(aov)}


def oos(start: str, end: str, split: float = 0.7, rounds: int = 2,
        decisive_thr: float = DECISIVE_THR, neutral: float = NEUTRAL) -> dict:
    """視窗內 train/test：只在 train 校參+選權重，套決策規則到 held-out test。"""
    import calibrate as cal
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, start, end, neutral)
        ov = {}
        for f in feats:
            us = tdb.us_asof(conn, "sox", f["date"])
            ov[f["date"]] = us["change_pct"] if us and us.get("change_pct") is not None else None
    k = int(len(feats) * split)
    tr, te = feats[:k], feats[k:]
    params, _ = cal.calibrate(tr, rounds)                 # 只在 train 校參
    tr_s = bt.score_samples(tr, params)
    res = bt.optimize(tr_s)
    res, _ = bt.apply_guard(tr_s, res)
    W = bt.pick_balanced(res, 0.0)["weights"]             # 只在 train 選權重
    return {
        "split": split, "n_train": len(tr), "n_test": len(te),
        "train_window": [tr[0]["date"], tr[-1]["date"]] if tr else [],
        "test_window": [te[0]["date"], te[-1]["date"]] if te else [],
        "in_sample_train": _eval_rule(bt.score_samples(tr, params), W, ov, decisive_thr),
        "out_of_sample_test": _eval_rule(bt.score_samples(te, params), W, ov, decisive_thr),
    }


def cross_year_overnight(decisive_thr: float = DECISIVE_THR, neutral: float = NEUTRAL) -> dict:
    """跨年複核「決斷夜跟隔夜」核心：用 xs.db 2 年 2344 收盤 + 重抓 2 年 SOX，分年段算同日(全日)方向勝率。

    僅驗證 weight-free 的隔夜核心（無 open 故不含開盤跳空、無 chips）；分 2024-07~2025-06 與 2025-07~2026-06。
    """
    import fetch_us
    import xs_db
    with xs_db.connect() as c:
        rows = c.execute("SELECT date, close FROM xs_candles WHERE symbol = ? ORDER BY date",
                         (config.SYMBOL,)).fetchall()
    closes = [(r["date"], r["close"]) for r in rows if r["close"]]
    rets = [(closes[i][0], (closes[i][1] - closes[i - 1][1]) / closes[i - 1][1] * 100.0)
            for i in range(1, len(closes))]
    sox = sorted((s["date"], s["change_pct"]) for s in fetch_us.fetch_yahoo_daily("^SOX", "2y")
                 if s.get("change_pct") is not None)

    def overnight_for(twdate: str):
        v = None
        for sd, pct in sox:
            if sd < twdate:
                v = pct
            else:
                break
        return v

    segs = {"2024-07~2025-06": ("2024-07-01", "2025-06-30"),
            "2025-07~2026-06": ("2025-07-01", "2026-06-30")}
    out = {}
    for name, (s, e) in segs.items():
        dec = [0, 0]
        alld = [0, 0]
        for d, ret in rets:
            if not (s <= d <= e) or abs(ret) < neutral:
                continue
            o = overnight_for(d)
            if o is None or o == 0:
                continue
            alld[1] += 1
            alld[0] += ((o > 0) == (ret > 0))
            if abs(o) >= decisive_thr:
                dec[1] += 1
                dec[0] += ((o > 0) == (ret > 0))
        out[name] = {"decisive": {"win": round(dec[0] / dec[1], 4) if dec[1] else 0.0, "n": dec[1]},
                     "all_overnight": {"win": round(alld[0] / alld[1], 4) if alld[1] else 0.0, "n": alld[1]}}
    return out


def main(argv):
    def opt(flag, d=None):
        return argv[argv.index(flag) + 1] if flag in argv else d
    start = opt("--start", "2025-07-01")
    end = opt("--end", config.today_str()[:4] + "-12-31")
    thr = float(opt("--thr", str(DECISIVE_THR)))
    if "--cross-year" in argv:
        cy = cross_year_overnight(thr)
        print(f"[daily_decision 跨年] 決斷夜跟隔夜核心（全日方向，門檻 |SOX|>={thr}%）：")
        for name, v in cy.items():
            print(f"  {name}: 決斷夜 {v['decisive']['win']:.1%}(n={v['decisive']['n']})  "
                  f"全隔夜 {v['all_overnight']['win']:.1%}(n={v['all_overnight']['n']})")
        return
    if "--oos" in argv:
        o = oos(start, end, float(opt("--split", "0.7")), int(opt("--rounds", "2")), thr)
        print(f"[daily_decision OOS] train {o['n_train']} 日 {o['train_window']} / "
              f"test {o['n_test']} 日 {o['test_window']}  (split={o['split']})")
        for tag, key in (("in-sample(train)", "in_sample_train"), ("OUT-OF-SAMPLE(test)", "out_of_sample_test")):
            e = o[key]
            print(f"  {tag}: 合併 {e['combined']['win']:.1%}(n={e['combined']['n']})  "
                  f"決斷夜 {e['decisive']['win']:.1%}(n={e['decisive']['n']})  "
                  f"平淡夜 {e['flat']['win']:.1%}(n={e['flat']['n']})  "
                  f"全跟隔夜 {e['always_overnight']['win']:.1%}")
        return
    r = analyze(start, end, thr)
    print(f"[daily_decision] {r['window'][0]}~{r['window'][1]}  {r['n_days']} 日  "
          f"決斷夜門檻 |SOX|>={thr}%  決斷夜占 {r['decisive_day_share']:.0%}")
    print(f"  每日選邊(全覆蓋) 方向勝率 {r['combined_every_day']['win']:.1%} (n={r['combined_every_day']['n']})")
    print(f"    - 決斷夜(跟隔夜)  {r['segment_decisive']['win']:.1%} (n={r['segment_decisive']['n']})")
    print(f"    - 平淡夜(用十面)  {r['segment_flat']['win']:.1%} (n={r['segment_flat']['n']})")
    print(f"  對照 全跟隔夜 {r['baseline_always_overnight']['win']:.1%}  "
          f"全用模型 {r['baseline_always_model']['win']:.1%}")


if __name__ == "__main__":
    main(sys.argv)
