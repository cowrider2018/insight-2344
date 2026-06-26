"""橫斷面多股籌碼排序回測（選項 D，MVP）。

評估「跨股以籌碼訊號排名」是否有 alpha——以 cross-sectional **IC（資訊係數）**、
**分位數報酬** 與 **多空（top−bottom 分位）** 衡量，而非單股命中率。

無 look-ahead：訊號 sig(D) 由三大法人淨額（D 盤後公布）算，預測 **D→D+1** 報酬。
輸出 reports/xs_backtest_<start>_<end>.md。

用法：
    python src/xs_backtest.py [--start 2026-01-01] [--end 2026-06-24] [--window 5] [--q 5] [--top 300]
    --top N：每日只取成交量前 N 檔形成流動性橫斷面（0=全部，全市場模式建議 200~300）。
"""
from __future__ import annotations

import sys

import config
import xs_db
import xs_signals as xs


def _mean(v: list[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def _std(v: list[float]) -> float:
    if len(v) < 2:
        return 0.0
    m = _mean(v)
    return (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5


def run(start: str | None, end: str | None, window: int, q: int, top: int = 0) -> dict:
    with xs_db.connect() as conn:
        closes, flows, vols, dates = xs_db.load_panel(conn)
    if len(dates) < 5:
        return {"error": "xs.db 樣本不足，請先 python src/xs_ingest.py --backfill"}
    if start:
        dates = [d for d in dates if d >= start]
    if end:
        dates = [d for d in dates if d <= end]
    sig = xs.smoothed_flow(flows, dates, window)
    syms_all = list(sig.keys())

    ics: list[float] = []
    ls_rets: list[float] = []          # 多空（top−bottom 分位）每日報酬
    qmeans: dict[int, list[float]] = {g: [] for g in range(q)}
    xs_sizes: list[int] = []           # 每日橫斷面檔數
    n_eval = 0
    for i in range(len(dates) - 1):
        d, dn = dates[i], dates[i + 1]
        assert d < dn, "look-ahead: 前向日序不單調"
        # 當日有訊號、且當日與次日皆有收盤的股票
        rows = []
        for s in syms_all:
            sv = sig.get(s, {}).get(d)
            c0 = closes.get(s, {}).get(d)
            c1 = closes.get(s, {}).get(dn)
            if sv is None or not c0 or c1 is None:
                continue
            rows.append((s, sv, c1 / c0 - 1.0, vols.get(s, {}).get(d) or 0.0))
        # 每日流動性篩選：取成交量前 top 檔（0=不篩）
        if top and len(rows) > top:
            rows = sorted(rows, key=lambda r: r[3], reverse=True)[:top]
        if len(rows) < 5:
            continue
        n_eval += 1
        xs_sizes.append(len(rows))
        ic = xs.spearman([r[1] for r in rows], [r[2] for r in rows])
        if ic is not None:
            ics.append(ic)
        groups = xs.quantile_groups([(r[0], r[1]) for r in rows], q)
        ret_by_sym = {r[0]: r[2] for r in rows}
        for g, syms in groups.items():
            if syms:
                qmeans[g].append(_mean([ret_by_sym[s] for s in syms]))
        top_g = [ret_by_sym[s] for s in groups[q - 1]]
        bot = [ret_by_sym[s] for s in groups[0]]
        if top_g and bot:
            ls_rets.append(_mean(top_g) - _mean(bot))

    mean_ic = _mean(ics)
    ic_ir = mean_ic / _std(ics) if _std(ics) else 0.0
    pos_ic = sum(1 for x in ics if x > 0) / len(ics) if ics else 0.0
    ls_mean = _mean(ls_rets)
    ls_cum = 1.0
    for r in ls_rets:
        ls_cum *= (1.0 + r)
    return {
        "n_days": n_eval, "universe": len(syms_all), "top": top,
        "avg_xs_size": round(_mean([float(x) for x in xs_sizes]), 1),
        "window": window, "q": q,
        "mean_ic": mean_ic, "ic_ir": ic_ir, "pos_ic_ratio": pos_ic, "n_ic": len(ics),
        "ls_mean_daily": ls_mean, "ls_cumulative": ls_cum - 1.0,
        "quantile_mean_daily": {g: _mean(v) for g, v in qmeans.items()},
        "range": [dates[0], dates[-1]] if dates else [None, None],
    }


def write_report(res: dict, path) -> None:
    if res.get("error"):
        path.write_text(res["error"], encoding="utf-8")
        return
    q = res["q"]
    lines = [
        f"# 橫斷面多股籌碼排序回測（{res['range'][0]} ~ {res['range'][1]}）",
        "",
        f"- 股票池：**{res['universe']}** 檔（每日流動性前 {res['top'] or '全部'} 檔、平均橫斷面 "
        f"{res['avg_xs_size']} 檔）　評估交易日：**{res['n_days']}**　訊號平滑：{res['window']} 日　分位數：{q}",
        "- 訊號：三大法人淨額 / 成交量（跨股籌碼流入強度），預測次日報酬（無 look-ahead）。",
        "",
        "## 資訊係數（IC，Spearman）",
        f"- 平均 IC：**{res['mean_ic']:.4f}**　IC_IR（平均/標準差）：**{res['ic_ir']:.3f}**　"
        f"IC>0 比例：{res['pos_ic_ratio']:.2%}（n={res['n_ic']}）",
        "> 經驗參考：|平均 IC|≥0.03、IC_IR≥0.3 即視為有實質跨股預測力（單因子）。",
        "",
        "## 多空組合（top − bottom 分位，等權、每日換倉、無成本假設）",
        f"- 平均每日報酬：**{res['ls_mean_daily']:.4%}**　區間累積：**{res['ls_cumulative']:.2%}**",
        "",
        "## 各分位平均每日報酬（0=最低訊號 → 高=最高訊號）",
        "| 分位 | 平均每日報酬 |",
        "|---|---|",
    ]
    for g in range(q):
        lines.append(f"| Q{g} | {res['quantile_mean_daily'].get(g, 0):.4%} |")
    lines += [
        "",
        "## 注意與限制",
        "- 評估指標為橫斷面 IC / 分位報酬 / 多空，**非單股命中率**；正 IC 表示高籌碼流入股次日相對較強。",
        "- 無交易成本/滑價/流動性假設，多空為理論等權每日換倉，僅供因子有效性評估。",
        "- 股票池與訊號為 MVP；可擴大股票池、加入 TDCC 大戶週變化等多因子做更完整評估。",
        "",
        "本報告為公開資訊回測，非投資建議，據此操作風險自負。",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> None:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    start = opt("--start")
    end = opt("--end")
    window = int(opt("--window", "5"))
    q = int(opt("--q", "5"))
    top = int(opt("--top", "0"))

    res = run(start, end, window, q, top)
    if res.get("error"):
        print("[xs_backtest]", res["error"])
        return
    path = config.REPORTS_DIR / f"xs_backtest_{res['range'][0]}_{res['range'][1]}.md"
    write_report(res, path)
    print(f"[xs_backtest] 股票池 {res['universe']} 檔、評估 {res['n_days']} 日")
    print(f"  平均 IC {res['mean_ic']:.4f}　IC_IR {res['ic_ir']:.3f}　IC>0 {res['pos_ic_ratio']:.2%}")
    print(f"  多空 平均日報酬 {res['ls_mean_daily']:.4%}　累積 {res['ls_cumulative']:.2%}")
    print(f"  -> {path}")


if __name__ == "__main__":
    main(sys.argv)
