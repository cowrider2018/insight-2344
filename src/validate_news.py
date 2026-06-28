"""獨立驗證每個消息型態的「實際次日效應」，產生已驗證極性 data/news_patterns.json。

對每個型態，蒐集隔夜窗內**出現該型態**的交易日，統計當日實際漲跌分布，與整體基準
比較並做二項檢定。只有「樣本足夠 + 顯著偏離基準」才賦予極性（+1/-1），否則 0（中性），
避免把反直覺或雜訊型態誤判成方向訊號。

用法:
    python src/validate_news.py --start 2025-07-01 --end 2026-06-23 [--min-n 8] [--alpha 0.15]
"""
from __future__ import annotations

import json
import sys
from math import comb

import backtest as bt
import config
import news_patterns
import timeline_db as tdb

MIN_DIR_N = 8       # 至少這麼多「非中性」日才考慮驗證
ALPHA = 0.15        # 二項檢定顯著水準（樣本少，放寬）


def binom_two_sided_p(k: int, n: int, p: float) -> float:
    """Binom(n,p) 下，出現「機率不高於 k」之所有結果的總機率（雙尾近似）。"""
    if n == 0:
        return 1.0
    probs = [comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    pk = probs[k]
    return min(1.0, sum(pr for pr in probs if pr <= pk + 1e-12))


def validate(feats: list[dict], min_n: int = MIN_DIR_N, alpha: float = ALPHA) -> dict:
    # 整體基準：非中性日中上漲比例
    base_up = base_dir = 0
    for f in feats:
        if f["actual"] == 1:
            base_up += 1
            base_dir += 1
        elif f["actual"] == -1:
            base_dir += 1
    base_rate = base_up / base_dir if base_dir else 0.5

    out: dict = {}
    for name, spec in news_patterns.PATTERNS.items():
        up = down = neutral = 0
        for f in feats:
            if any(name in news_patterns.match(n.get("title", "")) for n in f["news"]):
                if f["actual"] == 1:
                    up += 1
                elif f["actual"] == -1:
                    down += 1
                else:
                    neutral += 1
        n_dir = up + down
        up_rate = up / n_dir if n_dir else None
        edge = (up_rate - base_rate) if up_rate is not None else 0.0
        pval = binom_two_sided_p(up, n_dir, base_rate) if n_dir else 1.0
        validated = n_dir >= min_n and pval < alpha and abs(edge) > 1e-9
        polarity = (1 if edge > 0 else -1) if validated else 0
        out[name] = {
            "polarity": polarity, "validated": validated,
            "up": up, "down": down, "neutral": neutral, "n_dir": n_dir,
            "up_rate": round(up_rate, 4) if up_rate is not None else None,
            "base_rate": round(base_rate, 4), "edge": round(edge, 4),
            "p_value": round(pval, 4), "note": spec["note"],
        }
    return out


def write_report(patterns: dict, base_rate: float, n_days: int, path) -> None:
    lines = [
        "# 2344 消息型態獨立驗證報告",
        "",
        f"- 樣本交易日：{n_days}　整體基準上漲率（非中性日）：{base_rate:.2%}",
        f"- 採用門檻：非中性樣本 ≥ {MIN_DIR_N} 且二項檢定 p < {ALPHA}",
        "",
        "> polarity：+1 驗證偏多、-1 驗證偏空、0 樣本不足/不顯著（中性，不採用以避免誤判）。",
        "> edge = 該型態上漲率 − 基準上漲率；負值代表反直覺（利多出盡/利空出盡）。",
        "",
        "| 型態 | 說明 | n(非中性) | 上漲率 | edge | p值 | 極性 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, v in sorted(patterns.items(), key=lambda kv: (not kv[1]["validated"], kv[1]["p_value"])):
        ur = f"{v['up_rate']:.0%}" if v["up_rate"] is not None else "-"
        pol = {1: "📈+1", -1: "📉-1", 0: "—"}[v["polarity"]]
        lines.append(f"| {name} | {v['note']} | {v['n_dir']} | {ur} | {v['edge']:+.2f} "
                     f"| {v['p_value']} | {pol} |")
    lines += [
        "",
        "## 說明",
        "- 消息時間軸目前多由每日快照累積，歷史回補有限；多數型態 n 不足而暫為中性。",
        "- 隨每日 build_dataset 累積，重跑本驗證即可逐一「轉正」有統計意義的型態。",
        "- 反直覺型態（如券商調升後常賣出）一旦樣本足夠且顯著，會自動以負 edge 給予偏空極性。",
        "",
        "本報告為公開資訊回測驗證，非投資建議。",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> None:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    start = opt("--start", "2025-07-01")
    end = opt("--end", config.today_str()[:4] + "-12-31")
    tol = float(opt("--tol", str(bt.NEUTRAL_TOL)))
    min_n = int(opt("--min-n", str(MIN_DIR_N)))
    alpha = float(opt("--alpha", str(ALPHA)))

    tdb.init_db()
    with tdb.connect() as conn:
        feats, _ = bt.extract_features(conn, config.SYMBOL, start, end, tol)
    if not feats:
        print("[validate_news] 無樣本。")
        return

    patterns = validate(feats, min_n, alpha)
    base_rate = next(iter(patterns.values()))["base_rate"] if patterns else 0.5

    out = {
        "symbol": config.SYMBOL, "as_of": config.now_tpe().isoformat(),
        "window": [start, end], "n_days": len(feats),
        "base_up_rate": base_rate, "min_dir_n": min_n, "alpha": alpha,
        "patterns": patterns,
    }
    pfile = config.news_patterns_path()
    pfile.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    report = config.REPORTS_DIR / "news_patterns.md"
    write_report(patterns, base_rate, len(feats), report)

    n_val = sum(1 for v in patterns.values() if v["validated"])
    days_with_news = sum(1 for f in feats if f["news"])
    print(f"[validate_news] 樣本 {len(feats)} 日（其中 {days_with_news} 日有消息）")
    print(f"  已驗證型態 {n_val}/{len(patterns)}（其餘樣本不足或不顯著，暫為中性）")
    for name, v in patterns.items():
        if v["validated"]:
            print(f"   - {name}: 極性{v['polarity']:+d}  上漲率{v['up_rate']:.0%} "
                  f"(基準{v['base_rate']:.0%}, n={v['n_dir']}, p={v['p_value']})")
    print(f"  -> {pfile}")
    print(f"  -> {report}")


if __name__ == "__main__":
    main(sys.argv)
