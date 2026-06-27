"""橫斷面多股籌碼排序回測（選項 D）。

評估「跨股以籌碼訊號排名」是否有 alpha——以 cross-sectional **IC（資訊係數）**、
**分位數報酬**、**多空（top−bottom）** 衡量，並做**交易成本／週轉（持有期）敏感度**與
**分期穩健**檢驗（決定弱訊號是否真能交易、是否為單一 regime 運氣）。

無 look-ahead：訊號 sig(D) 由三大法人淨額（D 盤後公布）算，預測 **D→D+1** 報酬。
輸出 reports/xs_backtest_<start>_<end>.md。

用法：
    python src/xs_backtest.py [--start ..] [--end ..] [--window 5] [--q 5] [--top 300]
                              [--cost 0.45] [--holds 1,5,10,20] [--subperiods 4] [--composite]
    --top N：每日只取成交量前 N 檔形成流動性橫斷面（0=全部，全市場建議 200~300）。
    --cost：每單位「名單週轉」的來回成本%（預設 0.45，約台股稅費折後 + 借券概估）。
    --holds：持有期（每幾日換倉）清單，用來看降週轉後淨報酬。
    --composite：多因子複合（法人流 5 日 + 法人流 20 日 + 外資流 5 日，每日跨股 z-score 等權）。
    --tdcc：在複合再加 **TDCC 大戶週變化**（限有 TDCC 歷史的聚焦池）。
    --composite --tdcc-pool：3 因子但限同一聚焦池（與 --tdcc 公平對照）。
                 預設僅單因子（法人流 window 日）。
"""
from __future__ import annotations

import sys

import config
import universe
import xs_db
import xs_signals as xs


def _mean(v: list[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def _std(v: list[float]) -> float:
    if len(v) < 2:
        return 0.0
    m = _mean(v)
    return (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5


def _prep(start, end):
    """載入 xs.db 面板一次，回傳 (dates, flows, fflows, tdcc_series, closes, vols)；不足回 None。"""
    with xs_db.connect() as conn:
        closes, flows, vols, dates = xs_db.load_panel(conn)
        fflows = xs_db.load_foreign_flows(conn)
        tdcc_series = xs_db.load_tdcc_series(conn)
    if start:
        dates = [d for d in dates if d >= start]
    if end:
        dates = [d for d in dates if d <= end]
    if len(dates) < 10:
        return None
    return dates, flows, fflows, tdcc_series, closes, vols


def _restrict(sig, keep):
    return {s: v for s, v in sig.items() if s in keep}


def build_signal(dates, flows, fflows, tdcc_series, window, mode):
    """訊號建構。
    single             -> 法人流 window 日
    composite          -> 法人流5+法人流20+外資流5（全市場）
    composite_tdccpool -> 同上但限有 TDCC 歷史的聚焦池（與 tdcc 公平對照）
    tdcc               -> 法人流5+法人流20+外資流5+**大戶週變化**（限聚焦池）
    """
    f5 = xs.smoothed_flow(flows, dates, 5)
    f20 = xs.smoothed_flow(flows, dates, 20)
    ff5 = xs.smoothed_flow(fflows, dates, 5)
    if mode in ("tdcc", "composite_tdccpool"):
        if mode == "tdcc":
            tchg = xs.tdcc_change_factor(tdcc_series, dates)
            keep = set(tchg)  # 4 因子：限有大戶週變化的股票
            facs = [_restrict(f5, keep), _restrict(f20, keep), _restrict(ff5, keep), tchg]
            return xs.composite(facs, dates), "複合4(法人流5+20+外資流5+大戶週變化)｜聚焦池"
        keep = set(universe.SYMBOLS)  # 3 因子：限策展焦點池（與 TDCC 資料是否在窗內無關）
        facs = [_restrict(f5, keep), _restrict(f20, keep), _restrict(ff5, keep)]
        return xs.composite(facs, dates), "複合3(法人流5+20+外資流5)｜聚焦池"
    if mode == "composite":
        return xs.composite([f5, f20, ff5], dates), "複合3(法人流5+20+外資流5)"
    return xs.smoothed_flow(flows, dates, window), f"單因子(法人流{window}日)"


def _form_baskets(d, sig, closes, vols, q, top):
    """以 signal(d) 在流動性前 top 檔中分位，回傳 (top 分位 set, bottom 分位 set)。"""
    rows = []
    for s in sig:
        sv = sig.get(s, {}).get(d)
        c0 = closes.get(s, {}).get(d)
        if sv is None or not c0:
            continue
        rows.append((s, sv, vols.get(s, {}).get(d) or 0.0))
    if top and len(rows) > top:
        rows = sorted(rows, key=lambda r: r[2], reverse=True)[:top]
    if len(rows) < 5:
        return None, None
    g = xs.quantile_groups([(r[0], r[1]) for r in rows], q)
    return set(g[q - 1]), set(g[0])


def _leg_ret(names, closes, d, dn):
    rs = []
    for s in names:
        c0 = closes.get(s, {}).get(d)
        c1 = closes.get(s, {}).get(dn)
        if c0 and c1 is not None:
            rs.append(c1 / c0 - 1.0)
    return _mean(rs) if rs else 0.0


def run(dates, sig, closes, vols, q, top):
    """每日換倉的 IC / 分位 / 多空（毛）。回傳 (stats, daily)；daily 供分期分析。"""
    syms_all = list(sig.keys())
    daily = []                          # 每日 {date, ic, ls}
    qmeans = {g: [] for g in range(q)}
    xs_sizes = []
    for i in range(len(dates) - 1):
        d, dn = dates[i], dates[i + 1]
        assert d < dn, "look-ahead: 前向日序不單調"
        rows = []
        for s in syms_all:
            sv = sig.get(s, {}).get(d)
            c0 = closes.get(s, {}).get(d)
            c1 = closes.get(s, {}).get(dn)
            if sv is None or not c0 or c1 is None:
                continue
            rows.append((s, sv, c1 / c0 - 1.0, vols.get(s, {}).get(d) or 0.0))
        if top and len(rows) > top:
            rows = sorted(rows, key=lambda r: r[3], reverse=True)[:top]
        if len(rows) < 5:
            continue
        xs_sizes.append(len(rows))
        ic = xs.spearman([r[1] for r in rows], [r[2] for r in rows])
        groups = xs.quantile_groups([(r[0], r[1]) for r in rows], q)
        ret_by_sym = {r[0]: r[2] for r in rows}
        for g, syms in groups.items():
            if syms:
                qmeans[g].append(_mean([ret_by_sym[s] for s in syms]))
        ls = _mean([ret_by_sym[s] for s in groups[q - 1]]) - _mean([ret_by_sym[s] for s in groups[0]])
        daily.append({"date": d, "ic": ic, "ls": ls})

    ics = [x["ic"] for x in daily if x["ic"] is not None]
    ls_rets = [x["ls"] for x in daily]
    ls_cum = 1.0
    for r in ls_rets:
        ls_cum *= (1.0 + r)
    stats = {
        "n_days": len(daily), "universe": len(syms_all), "top": top,
        "avg_xs_size": round(_mean([float(x) for x in xs_sizes]), 1), "q": q,
        "mean_ic": _mean(ics), "ic_ir": _mean(ics) / _std(ics) if _std(ics) else 0.0,
        "pos_ic_ratio": sum(1 for x in ics if x > 0) / len(ics) if ics else 0.0, "n_ic": len(ics),
        "ls_mean_daily": _mean(ls_rets), "ls_cumulative": ls_cum - 1.0,
        "quantile_mean_daily": {g: _mean(v) for g, v in qmeans.items()},
        "range": [dates[0], dates[-1]],
    }
    return stats, daily


def cost_sweep(dates, sig, closes, vols, q, top, holds, cost):
    """不同持有期（每幾日換倉）下的毛/淨累積與週轉。cost=每單位名單週轉的來回成本%。"""
    out = []
    for hold in holds:
        L = S = None
        gross = net = 1.0
        tos = []
        ndays = 0
        for i in range(len(dates) - 1):
            d, dn = dates[i], dates[i + 1]
            cost_today = 0.0
            if i % hold == 0:
                nl, ns = _form_baskets(d, sig, closes, vols, q, top)
                if nl is not None:
                    to = 1.0 if L is None else (len(nl - L) + len(ns - S)) / (len(nl) + len(ns))
                    tos.append(to)
                    cost_today = to * cost / 100.0
                    L, S = nl, ns
            if L is None:
                continue
            ls = _leg_ret(L, closes, d, dn) - _leg_ret(S, closes, d, dn)
            gross *= (1.0 + ls)
            net *= (1.0 + ls - cost_today)
            ndays += 1
        ann = net ** (252.0 / ndays) - 1.0 if ndays else 0.0
        out.append({"hold": hold, "gross_cum": gross - 1.0, "net_cum": net - 1.0,
                    "ann_net": ann, "avg_turnover": _mean(tos), "n_rebal": len(tos)})
    return out


def subperiods(daily, k):
    """把評估日等分 k 段，各段平均 IC 與多空毛累積（看是否單一 regime 運氣）。"""
    n = len(daily)
    out = []
    for j in range(k):
        chunk = daily[j * n // k:(j + 1) * n // k]
        if not chunk:
            continue
        ics = [x["ic"] for x in chunk if x["ic"] is not None]
        cum = 1.0
        for x in chunk:
            cum *= (1.0 + x["ls"])
        out.append({"label": f"{chunk[0]['date']}~{chunk[-1]['date']}", "n": len(chunk),
                    "mean_ic": _mean(ics), "ls_cum": cum - 1.0})
    return out


def write_report(res, daily, costs, subs, cost, signal_label, path) -> None:
    q = res["q"]
    lines = [
        f"# 橫斷面多股籌碼排序回測（{res['range'][0]} ~ {res['range'][1]}）",
        "",
        f"- 股票池：**{res['universe']}** 檔（每日流動性前 {res['top'] or '全部'} 檔、平均橫斷面 "
        f"{res['avg_xs_size']} 檔）　評估交易日：**{res['n_days']}**　分位數：{q}",
        f"- 訊號：**{signal_label}**（跨股籌碼流入強度），預測次日報酬（無 look-ahead）。",
        "",
        "## 資訊係數（IC，Spearman）",
        f"- 平均 IC：**{res['mean_ic']:.4f}**　IC_IR：**{res['ic_ir']:.3f}**　"
        f"IC>0 比例：{res['pos_ic_ratio']:.2%}（n={res['n_ic']}）",
        "> 經驗參考：|平均 IC|≥0.03、IC_IR≥0.3 才算有實質跨股預測力（單因子）。",
        "",
        "## 多空（top−bottom，等權、每日換倉、**毛**、無成本）",
        f"- 平均每日：**{res['ls_mean_daily']:.4%}**　區間累積：**{res['ls_cumulative']:.2%}**",
        "",
        "## 各分位平均每日報酬（0=最低訊號 → 高=最高訊號）",
        "| 分位 | 平均每日報酬 |", "|---|---|",
    ]
    for g in range(q):
        lines.append(f"| Q{g} | {res['quantile_mean_daily'].get(g, 0):.4%} |")

    lines += [
        "",
        f"## 交易成本與週轉敏感度（成本假設：每單位名單週轉來回 {cost:.2f}%）",
        "> **關卡一**：弱訊號能否扛成本。每日換倉週轉極高、成本最重；拉長持有期（降週轉）才有機會轉正。",
        "| 持有期(日) | 換倉次數 | 平均週轉 | 毛累積 | **淨累積** | 年化淨 |",
        "|---|---|---|---|---|---|",
    ]
    for c in costs:
        lines.append(f"| {c['hold']} | {c['n_rebal']} | {c['avg_turnover']:.2%} | "
                     f"{c['gross_cum']:.2%} | **{c['net_cum']:.2%}** | {c['ann_net']:.2%} |")

    lines += [
        "",
        "## 分期穩健（等分區間的平均 IC 與多空毛累積）",
        "> **關卡二**：edge 是否集中在某段（regime 運氣）。各段方向一致才可信。",
        "| 期間 | 日數 | 平均 IC | 多空毛累積 |", "|---|---|---|---|",
    ]
    for s in subs:
        lines.append(f"| {s['label']} | {s['n']} | {s['mean_ic']:.4f} | {s['ls_cum']:.2%} |")

    lines += [
        "",
        "## 注意與限制",
        "- 評估指標為橫斷面 IC / 分位 / 多空，**非單股命中率**；正 IC=高籌碼流入股次日相對較強。",
        "- 成本模型為簡化估計（每單位名單週轉一個來回固定成本），未含滑價、借券限制、衝擊成本與當沖/平盤限制；"
        "**淨累積為負或接近 0 即代表此單因子在該成本下不可交易**。",
        "- 多空為理論等權，放空在台股有借券成本與限制；保守應另看**純做多 top 分位 vs 大盤**。",
        "- 單因子且區間有限；可加 TDCC 大戶週變化等多因子、延長區間以強化結論。",
        "",
        "本報告為公開資訊回測，非投資建議，據此操作風險自負。",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> None:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    start, end = opt("--start"), opt("--end")
    window, q, top = int(opt("--window", "5")), int(opt("--q", "5")), int(opt("--top", "0"))
    cost = float(opt("--cost", "0.45"))
    holds = [int(x) for x in opt("--holds", "1,5,10,20").split(",")]
    k = int(opt("--subperiods", "4"))
    if "--tdcc" in argv:
        mode = "tdcc"
    elif "--composite" in argv and "--tdcc-pool" in argv:
        mode = "composite_tdccpool"
    elif "--composite" in argv:
        mode = "composite"
    else:
        mode = "single"

    prep = _prep(start, end)
    if prep is None:
        print("[xs_backtest] xs.db 樣本不足，請先 python src/xs_ingest.py --backfill")
        return
    dates, flows, fflows, tdcc_series, closes, vols = prep
    sig, signal_label = build_signal(dates, flows, fflows, tdcc_series, window, mode)
    res, daily = run(dates, sig, closes, vols, q, top)
    costs = cost_sweep(dates, sig, closes, vols, q, top, holds, cost)
    subs = subperiods(daily, k)

    path = config.REPORTS_DIR / f"xs_backtest_{res['range'][0]}_{res['range'][1]}.md"
    write_report(res, daily, costs, subs, cost, signal_label, path)
    print(f"[xs_backtest] {signal_label}　股票池 {res['universe']} 檔、評估 {res['n_days']} 日、每日前 {top or '全部'} 檔")
    print(f"  平均 IC {res['mean_ic']:.4f}　IC_IR {res['ic_ir']:.3f}　IC>0 {res['pos_ic_ratio']:.2%}")
    print(f"  多空毛 平均日 {res['ls_mean_daily']:.4%}　累積 {res['ls_cumulative']:.2%}")
    print(f"  成本{cost:.2f}% 下淨累積：" + "　".join(
        f"持{c['hold']}日 {c['net_cum']:.1%}(週轉{c['avg_turnover']:.0%})" for c in costs))
    print("  分期毛累積：" + "　".join(f"{s['label'][:7]}… IC{s['mean_ic']:.3f}/{s['ls_cum']:.0%}"
                                       for s in subs))
    print(f"  -> {path}")


if __name__ == "__main__":
    main(sys.argv)
