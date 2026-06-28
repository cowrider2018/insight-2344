"""主力分點 v2：每券商行為加權模型（walk-forward + 收縮 + 跨券商聚合）。

對每個交易日 d，**只用 < d 的歷史**估每券商 edge（其淨買/賣方向與次日漲跌同向的比例），
經 empirical-Bayes 收縮成「加權分數 wedge」（小樣本 → 0），再以 wedge 對當日各券商方向加權
聚合成日訊號 branch_wf[d] ∈ [-1,1]，用於預測 d+1。無 look-ahead by construction。

另產出每券商「客製化行為檔」（多 horizon 命中率、wedge、跟單/反指標標記）供質性情報。

用法:
    python src/branch_model.py [--min-obs 20] [--K 25] [--min-hist 40]
"""
from __future__ import annotations

import json
import sys

import config
import timeline_db as tdb

NEUTRAL_TOL = 1.0     # 次日漲跌中性帶 ±%
MIN_OBS = 20          # 一券商至少這麼多「有表態且次日非中性」歷史才給 wedge
K = 25.0              # 收縮常數（越大越保守、小樣本越趨 0）
MIN_HIST = 40         # 暖身：分點歷史前這麼多交易日不產生分數
HORIZONS = (1, 3, 5)  # profile 用的未來 horizon（隔日 / 數日）


def _dir(pct: float, tol: float = NEUTRAL_TOL) -> int:
    return 1 if pct > tol else (-1 if pct < -tol else 0)


def _load(conn):
    candles = tdb.candles_upto(conn, config.SYMBOL)            # 由舊到新
    dates = [c["date"] for c in candles]
    close = [c["close"] for c in candles]
    # 各 horizon 的未來方向：dir_h[H][i] = 第 i 日往後 H 日累積漲跌方向
    dir_h = {H: {} for H in HORIZONS}
    for H in HORIZONS:
        for i in range(len(dates) - H):
            c0 = close[i]
            if c0:
                dir_h[H][dates[i]] = _dir((close[i + H] - c0) / c0 * 100)
    rows = conn.execute(
        "SELECT date, branch, net_lots FROM broker_branches WHERE symbol = ? ORDER BY date",
        (config.SYMBOL,),
    ).fetchall()
    by_date: dict[str, list[tuple[str, float]]] = {}
    by_branch: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        net = r["net_lots"] or 0
        if not net:
            continue
        by_date.setdefault(r["date"], []).append((r["branch"], net))
        by_branch.setdefault(r["branch"], []).append((r["date"], 1 if net > 0 else -1))
    return dates, dir_h, by_date, by_branch


def _wedge(hits: int, n: int, k: float) -> float:
    """收縮後加權分數 ∈ (-0.5, 0.5)；n 小 -> 趨 0。"""
    return (hits - 0.5 * n) / (n + k) if n else 0.0


def walkforward(conn, min_obs: int = MIN_OBS, k: float = K, min_hist: int = MIN_HIST) -> list[dict]:
    """逐日 expanding-window 收縮 edge -> 每日跨券商聚合分數（預測次日）。"""
    dates, dir_h, by_date, by_branch = _load(conn)
    nday = dir_h[1]
    branch_dates = sorted(by_date.keys())
    out: list[dict] = []
    for pos, d in enumerate(branch_dates):
        if pos < min_hist:                      # 暖身期不表態
            continue
        num = den = 0.0
        contrib = 0
        for branch, net in by_date[d]:
            hits = n = 0
            for t, sgn in by_branch[branch]:    # 已按日期排序
                if t >= d:                      # 只用 < d 的歷史（無 look-ahead）
                    break
                y = nday.get(t)
                if not y:
                    continue
                n += 1
                hits += (sgn == y)
            if n < min_obs:
                continue
            w = _wedge(hits, n, k)
            s = 1 if net > 0 else -1
            num += w * s
            den += abs(w)
            contrib += 1
        score = (num / den) if den > 0 else 0.0
        out.append({"date": d, "score": round(max(-1.0, min(1.0, score)), 4), "contrib": contrib})
    return out


def profiles(conn, min_obs: int = MIN_OBS, k: float = K) -> dict:
    """每券商客製化行為檔（全樣本，僅供情報）：多 horizon 命中率、wedge、標記。"""
    dates, dir_h, by_date, by_branch = _load(conn)
    prof: dict = {}
    for branch, acts in by_branch.items():
        rec = {"n": 0, "hit": {H: 0 for H in HORIZONS}, "obs": {H: 0 for H in HORIZONS}}
        for t, sgn in acts:
            for H in HORIZONS:
                y = dir_h[H].get(t)
                if not y:
                    continue
                rec["obs"][H] += 1
                rec["hit"][H] += (sgn == y)
        n1 = rec["obs"][1]
        if n1 < min_obs:
            continue
        hr = {H: round(rec["hit"][H] / rec["obs"][H], 4) if rec["obs"][H] else None for H in HORIZONS}
        w1 = _wedge(rec["hit"][1], n1, k)
        label = "follow(跟單)" if w1 > 0.05 else ("fade(反指標/隔日沖)" if w1 < -0.05 else "neutral")
        prof[branch] = {"n": n1, "hit_rate": hr, "wedge": round(w1, 4), "label": label,
                        "appearances": len(acts)}
    return prof


def write_report(prof: dict, n_scores: int, path) -> None:
    items = sorted(prof.items(), key=lambda kv: -abs(kv[1]["wedge"]))
    follow = sum(1 for v in prof.values() if v["wedge"] > 0.05)
    fade = sum(1 for v in prof.values() if v["wedge"] < -0.05)
    lines = [
        "# 2344 主力分點行為檔（walk-forward 模型）",
        "",
        f"- 有分數交易日：{n_scores}　建檔券商（樣本≥{MIN_OBS}）：{len(prof)}"
        f"　跟單傾向：{follow}　反指標/隔日沖：{fade}",
        "- wedge：收縮後加權分數（>0 跟單、<0 反指標）；hit@H：其淨買方向與未來 H 日同向率。",
        "- 注意：本表為**全樣本**情報，可能含多重檢定運氣；訊號實際用 walk-forward 聚合分數。",
        "",
        "| 券商分點 | wedge | hit@1 | hit@3 | hit@5 | n | 出現 | 標記 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for br, v in items[:40]:
        h = v["hit_rate"]
        def pct(x):
            return f"{x:.0%}" if x is not None else "-"
        lines.append(f"| {br} | {v['wedge']:+.3f} | {pct(h[1])} | {pct(h[3])} | {pct(h[5])} "
                     f"| {v['n']} | {v['appearances']} | {v['label']} |")
    lines += ["", "本報告為公開資訊回測驗證，非投資建議。"]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> None:
    def opt(flag, default):
        return argv[argv.index(flag) + 1] if flag in argv else default

    min_obs = int(opt("--min-obs", str(MIN_OBS)))
    k = float(opt("--K", str(K)))
    min_hist = int(opt("--min-hist", str(MIN_HIST)))

    tdb.init_db()
    with tdb.connect() as conn:
        scores = walkforward(conn, min_obs, k, min_hist)
        n = tdb.upsert_branch_wf(conn, config.SYMBOL, scores)
        prof = profiles(conn, min_obs, k)

    out = {
        "symbol": config.SYMBOL, "as_of": config.now_tpe().isoformat(),
        "min_obs": min_obs, "K": k, "min_hist": min_hist,
        "n_scores": len(scores), "branches": prof,
    }
    pfile = config.branch_profiles_path()
    pfile.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    report = config.SYMBOL_REPORTS_DIR / "branch_profiles.md"
    write_report(prof, len(scores), report)

    nz = [s["score"] for s in scores if s["score"]]
    mean_abs = round(sum(abs(x) for x in nz) / len(nz), 3) if nz else 0.0
    print(f"[branch_model] walk-forward 分數 {n} 日（暖身後），建檔券商 {len(prof)} 個")
    print(f"  非零分數日 {len(nz)}，平均 |score| {mean_abs}")
    print(f"  -> branch_wf 表 + {pfile}")
    print(f"  -> {report}")


if __name__ == "__main__":
    main(sys.argv)
