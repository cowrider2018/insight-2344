"""行為式分點分類：用歷史統計每個券商分點的「次日效應」，產生已驗證極性。

對每個分點，蒐集它「淨買/淨賣」的交易日，看其淨額方向 sign(net) 與**次日**實際漲跌是否同向，
與擲硬幣(0.5) 做二項檢定。只有「樣本足夠 + 顯著」才賦予極性：
  polarity +1 = 聰明錢（其淨買後次日常漲，跟單）
  polarity -1 = 隔日沖/反指標（其淨買後次日常跌，反向）
  polarity  0 = 樣本不足/不顯著（中性，不採用）
取代 broker_tags 的人工種子名單。

用法:
    python src/validate_branches.py --start 2025-07-01 --end 2026-06-23 [--min-n 12] [--alpha 0.15]
"""
from __future__ import annotations

import json
import sys

import backtest as bt
import config
import timeline_db as tdb

# 全市場有數百個分點，多重檢定下小樣本極易出假陽性（α=0.15、500+ 分點 → 期望數十個純運氣
# 「顯著」）。故門檻刻意保守：要求大樣本 + 嚴格 α，只留少數可能為真的常駐分點。
MIN_N = 40         # 至少這麼多「該分點有表態且次日非中性」的日子才驗證（擋小樣本噪音）
ALPHA = 0.02       # 嚴格顯著水準（對多重檢定的粗略保護）


def validate(feats: list[dict], min_n: int = MIN_N, alpha: float = ALPHA) -> dict:
    """回傳 {branch: {polarity, hit_rate, n, p_value, validated}}。

    feats 須含 f["branch"]（D-1 分點列）與 f["actual"]（D 方向，+1/-1/0）。
    """
    agg: dict[str, list[int]] = {}  # branch -> [hit, n]
    for f in feats:
        if f["actual"] == 0 or not f.get("branch"):
            continue
        for r in f["branch"]:
            net = r.get("net_lots") or 0
            if not net:
                continue
            sgn = 1 if net > 0 else -1
            d = agg.setdefault(r["branch"], [0, 0])
            d[1] += 1
            if sgn == f["actual"]:
                d[0] += 1

    out: dict = {}
    for br, (hit, n) in agg.items():
        hr = hit / n if n else 0.0
        pval = bt._binom_two_sided_p(hit, n, 0.5) if n else 1.0
        validated = n >= min_n and pval < alpha and abs(hr - 0.5) > 1e-9
        polarity = (1 if hr > 0.5 else -1) if validated else 0
        out[br] = {"polarity": polarity, "validated": validated, "hit": hit, "n": n,
                   "hit_rate": round(hr, 4), "p_value": round(pval, 4)}
    return out


def polarity_map(validated: dict) -> dict:
    """精簡成 scoring 用的 {branch: {"polarity": int}}（只留已驗證者）。"""
    return {br: {"polarity": v["polarity"]} for br, v in validated.items() if v["polarity"]}


def write_report(validated: dict, n_days: int, path) -> None:
    items = sorted(validated.items(), key=lambda kv: (not kv[1]["validated"], kv[1]["p_value"]))
    smart = sum(1 for v in validated.values() if v["polarity"] == 1)
    fade = sum(1 for v in validated.values() if v["polarity"] == -1)
    lines = [
        "# 2344 券商分點行為驗證報告（次日效應）",
        "",
        f"- 樣本交易日：{n_days}　已驗證分點：聰明錢(+1) {smart}　隔日沖/反指標(-1) {fade}",
        f"- 門檻：該分點有表態且次日非中性 ≥ {MIN_N} 日，二項檢定 p < {ALPHA}",
        "",
        "> polarity +1：其淨買後次日常漲（跟單）；-1：其淨買後次日常跌（隔日沖/反指標）；0：不顯著。",
        "",
        "| 分點 | 次日同向率 | n | p值 | 極性 |",
        "|---|---|---|---|---|",
    ]
    for br, v in items[:60]:
        pol = {1: "📈+1 跟單", -1: "📉-1 反指標", 0: "—"}[v["polarity"]]
        lines.append(f"| {br} | {v['hit_rate']:.0%} | {v['n']} | {v['p_value']} | {pol} |")
    lines += ["", "本報告為公開資訊回測驗證，非投資建議。"]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> None:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    start = opt("--start", "2025-07-01")
    end = opt("--end", config.today_str()[:4] + "-12-31")
    tol = float(opt("--tol", str(bt.NEUTRAL_TOL)))
    min_n = int(opt("--min-n", str(MIN_N)))
    alpha = float(opt("--alpha", str(ALPHA)))

    tdb.init_db()
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, start, end, tol)
    if not feats:
        print("[validate_branches] 無樣本。")
        return

    validated = validate(feats, min_n, alpha)
    out = {
        "symbol": config.SYMBOL, "as_of": config.now_tpe().isoformat(),
        "window": [start, end], "n_days": len(feats),
        "min_n": min_n, "alpha": alpha,
        "branches": polarity_map(validated),     # scoring 載入用（只留已驗證）
        "detail": validated,                      # 完整統計（供檢視）
    }
    pfile = config.branch_polarity_path()
    pfile.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    report = config.SYMBOL_REPORTS_DIR / "branch_polarity.md"
    write_report(validated, len(feats), report)

    n_val = sum(1 for v in validated.values() if v["polarity"])
    print(f"[validate_branches] 樣本 {len(feats)} 日，分點 {len(validated)} 個，"
          f"已驗證 {n_val}（聰明錢 {sum(1 for v in validated.values() if v['polarity']==1)}、"
          f"反指標 {sum(1 for v in validated.values() if v['polarity']==-1)}）")
    print(f"  -> {pfile}")
    print(f"  -> {report}")


if __name__ == "__main__":
    main(sys.argv)
