"""Walk-forward 回測與四面權重網格優化。

對視窗內每個交易日 D 作為「資訊截止斷點」：
  - 僅用 D 之前可得資訊重建四面（無 look-ahead）
  - 盤前研判 D 當日方向，與 D 實際漲跌（含中性帶 ±1%）比對
網格搜尋四面權重 + 中性帶門檻 tau，找命中率最高方案 ->
  data/weights.json + reports/backtest_<start>_<end>.md

用法:
    python src/backtest.py --start 2025-09-01 --end 2026-06-23 [--tol 1.0]
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from math import comb

import config
import confidence
import indicators
import scoring
import timeline_db as tdb

NEUTRAL_TOL = 1.0          # 實際漲跌中性帶 ±%
TAU_GRID = [0.05, 0.10, 0.15, 0.20, 0.25]
WEIGHT_STEP = 10           # 0.1 解析度（compositions of 10）
NEWS_FLOOR = 0.1           # 消息面總權重下限：回測選出的權重不含消息面時強制保底並重正規化


# ---------- 工具 ----------

def label_from_pct(pct: float, tol: float) -> int:
    if pct > tol:
        return 1
    if pct < -tol:
        return -1
    return 0


def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def latest_revenue_roc(d: date) -> str | None:
    """D 當下可安全取得的最新月營收（民國年月字串）。

    月營收約於次月 10 號公布，故營收月 RM 須其公布日(RM 次月 10 號) <= D。
    """
    for back in range(1, 4):
        rm_y, rm_m = d.year, d.month - back
        while rm_m <= 0:
            rm_m += 12
            rm_y -= 1
        py, pm = rm_y, rm_m + 1
        if pm > 12:
            pm -= 12
            py += 1
        if date(py, pm, 10) <= d:
            return f"{rm_y - 1911:03d}{rm_m:02d}"
    return None


def weight_grid(step: int = WEIGHT_STEP, dims: tuple = scoring.DIMENSIONS):
    """各維度權重在單純形上的所有組合（和=1，解析度 1/step）。"""
    n = len(dims)

    def rec(idx: int, remaining: int, acc: list[int]):
        if idx == n - 1:
            yield acc + [remaining]
            return
        for v in range(remaining + 1):
            yield from rec(idx + 1, remaining - v, acc + [v])

    for combo in rec(0, step, []):
        yield {d: combo[i] / step for i, d in enumerate(dims)}


# ---------- 建立每日樣本（與權重無關，只算一次） ----------

def extract_features(conn, symbol: str, start: str, end: str, tol: float) -> tuple[list[dict], dict]:
    """抽取每日的「原始 as-of 輸入」（與評分參數無關，只需算一次）。

    回傳每日 dict：technical/prev_close/chips/ref_vol_lots/news/rev/mu/sox/actual/change_pct。
    校準時可重複以不同參數對同一批 features 評分，省去重複查 DB。
    """
    candles = tdb.candles_upto(conn, symbol)  # 由舊到新
    feats: list[dict] = []
    coverage = {"chips": 0, "news": 0, "fundamental": 0, "micron": 0, "sox": 0,
                "intraday": 0, "branch": 0, "holders": 0, "futures": 0}

    for i in range(1, len(candles)):
        D = candles[i]
        d_date = D["date"]
        if not (start <= d_date <= end):
            continue
        prev = candles[i - 1]
        prev_close = prev["close"]
        if not prev_close:
            continue

        # --- look-ahead 防護：as-of 只到 D-1 ---
        as_of = candles[:i]
        assert as_of[-1]["date"] < d_date, "look-ahead: 技術面切片含 D 當日"

        technical = indicators.compute_all(as_of)
        ref_vol_lots = (sum(c["volume"] for c in as_of[-5:]) / min(5, len(as_of))) / 1000.0

        chips = tdb.chips_asof(conn, symbol, d_date)  # data_date < D

        start_iso = f"{prev['date']}T13:30:00+08:00"
        end_iso = f"{d_date}T09:00:00+08:00"
        news = tdb.news_in_window(conn, symbol, start_iso, end_iso)
        assert all(n["published_at"] < end_iso for n in news), "look-ahead: 消息超過 D 開盤"

        roc = latest_revenue_roc(_parse_date(d_date))
        rev = tdb.revenue_asof(conn, symbol, roc) if roc else None

        mu = tdb.us_asof(conn, "micron", d_date)
        sox = tdb.us_asof(conn, "sox", d_date)
        assert mu is None or mu["date"] < d_date, "look-ahead: 美光隔夜超過 D"
        assert sox is None or sox["date"] < d_date, "look-ahead: 費半隔夜超過 D"

        intraday = tdb.intraday_asof(conn, symbol, d_date)  # D-1（或更早）的 1 分 K
        assert intraday is None or intraday[0]["date"] < d_date, "look-ahead: 日內 1 分 K 超過 D"

        branch = tdb.branches_asof(conn, symbol, d_date)    # D-1（或更早）的主力分點
        assert branch is None or branch[0]["date"] < d_date, "look-ahead: 主力分點超過 D"
        branch_wf = tdb.branch_wf_asof(conn, symbol, d_date)  # D-1（或更早）walk-forward 分數
        assert branch_wf is None or branch_wf["date"] < d_date, "look-ahead: 分點 wf 超過 D"

        holders = tdb.tdcc_asof(conn, symbol, d_date)  # 公布日 < D 的最新集保大戶持股
        assert holders is None or holders["avail_date"] < d_date, "look-ahead: 集保大戶公布日超過 D"

        futures = tdb.futures_oi_asof(conn, "tx", d_date)  # 盤後公布，取 date < D 的 D-1
        assert futures is None or futures["date"] < d_date, "look-ahead: 外資台指期未平倉超過 D"

        for key, present in (("chips", chips), ("news", news), ("fundamental", rev),
                             ("micron", mu), ("sox", sox), ("intraday", intraday),
                             ("branch", branch), ("holders", holders), ("futures", futures)):
            if present:
                coverage[key] += 1

        pct = (D["close"] - prev_close) / prev_close * 100
        feats.append({
            "date": d_date,
            "technical": technical, "prev_close": prev_close,
            "chips": chips, "ref_vol_lots": ref_vol_lots,
            "news": news, "rev": rev, "mu": mu, "sox": sox, "intraday": intraday,
            "branch": branch, "branch_wf": branch_wf, "holders": holders, "futures": futures,
            "change_pct": round(pct, 2),
            "actual": label_from_pct(pct, tol),
        })
    return feats, coverage


def score_samples(feats: list[dict], params: dict | None = None) -> list[dict]:
    """以參數對 features 評分，回傳含 scores（六面）與 subsignals（技術子訊號）的樣本。"""
    p = params or scoring.PARAMS
    samples = []
    for f in feats:
        sub = scoring.technical_signals(f["technical"], f["prev_close"], p)
        id_sub = scoring.intraday_signals(f["intraday"], p) if f.get("intraday") else {}
        wf = f.get("branch_wf")
        wf_score = wf.get("score") if wf else None
        br_sub = scoring.branch_signals(f["branch"], p, wf_score) if f.get("branch") else {}
        hd_sub = scoring.holders_signals(f["holders"], p) if f.get("holders") else {}
        # 「無資料」一律以 None 表示（combine 會跳過並重新正規化權重 -> 公平）
        scores = {
            "technical": scoring.score_technical(f["technical"], f["prev_close"], p),
            "chips": scoring.score_chips(f["chips"], f["ref_vol_lots"], p) if f["chips"] else None,
            "news": scoring.score_news(f["news"], p) if f["news"] else None,
            "fundamental": scoring.score_fundamental(f["rev"], p) if f["rev"] else None,
            "micron": scoring.score_us(f["mu"], p) if f["mu"] else None,
            "sox": scoring.score_us(f["sox"], p) if f["sox"] else None,
            "intraday": scoring.score_intraday(f["intraday"], p) if f.get("intraday") else None,
            "branch": scoring.score_branch(f["branch"], p, wf_score) if f.get("branch") else None,
            "holders": scoring.score_holders(f["holders"], p) if f.get("holders") else None,
            "futures": scoring.score_futures(f["futures"], p) if f.get("futures") else None,
        }
        samples.append({"date": f["date"], "scores": scores, "subsignals": sub,
                        "id_subsignals": id_sub, "branch_subsignals": br_sub,
                        "hd_subsignals": hd_sub,
                        "change_pct": f["change_pct"], "actual": f["actual"]})
    return samples


def build_samples(conn, symbol: str, start: str, end: str, tol: float,
                  params: dict | None = None) -> tuple[list[dict], dict]:
    feats, coverage = extract_features(conn, symbol, start, end, tol)
    return score_samples(feats, params), coverage


# ---------- 評估 ----------

def evaluate(samples: list[dict], weights: dict, tau: float) -> dict:
    n = len(samples)
    hit = 0
    dir_pred = dir_hit = 0  # 方向性：預測非中性的命中
    for s in samples:
        pred, _ = scoring.combine(s["scores"], weights, tau)
        if pred == s["actual"]:
            hit += 1
        if pred != 0:
            dir_pred += 1
            if pred == s["actual"]:
                dir_hit += 1
    return {
        "weights": weights,
        "tau": tau,
        "win_rate": round(hit / n, 4) if n else 0.0,
        "directional_hit_rate": round(dir_hit / dir_pred, 4) if dir_pred else 0.0,
        "directional_coverage": round(dir_pred / n, 4) if n else 0.0,
    }


def _balance(weights: dict) -> float:
    """權重均衡度（變異數越小越均衡），作為並列時的次要偏好。"""
    vals = list(weights.values())
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def optimize(samples: list[dict], step: int = WEIGHT_STEP, tau_grid: list | None = None) -> list[dict]:
    results = []
    for w in weight_grid(step):
        for tau in (tau_grid or TAU_GRID):
            results.append(evaluate(samples, w, tau))
    # 主排序：命中率↓；次：方向涵蓋率↑（願意表態）；再次：權重越均衡↑
    results.sort(key=lambda r: (-r["win_rate"], -r["directional_coverage"], _balance(r["weights"])))
    return results


def pick_balanced(results: list[dict], tol: float = 0.0) -> dict:
    """命中率第一優先：在命中率距最佳 tol 以內（預設 0=僅同為最高命中率）的方案中，
    選最均衡（變異數最小）者作為次選排序。

    tol=0 時等於「採最高命中率，僅在多個並列最佳時取較均衡者」；
    若刻意要以少量命中率換穩健，可傳入 tol>0（如 0.02）。
    """
    if not results:
        return {}
    best_wr = results[0]["win_rate"]
    near = [r for r in results if r["win_rate"] >= best_wr - tol]
    return min(near, key=lambda r: (_balance(r["weights"]), -r["win_rate"]))


def restrict_news_floor(results: list[dict], floor: float = NEWS_FLOOR) -> list[dict]:
    """新策略權重建置法：只保留消息面總權重 >= floor 的方案（總和仍=1），再交由護欄/挑選。

    理念：消息面為策略指定必納面（樣本雖少但實戰關鍵），直接以「約束」在 news≥floor 的
    權重搭配中挑最高命中率者來決策——而非事後強塞、也不加額外否決層，讓消息面的貢獻可被
    回測直接衡量。消息面內部各型態的方向強度由 scoring 依（已驗證 edge 優先／否則專家先驗）
    加權成單一 [-1,1] 分數，再乘上此總權重（非切割權重）。floor<=0 可停用此約束。
    """
    if floor <= 0:
        return results
    kept = [r for r in results if r["weights"].get("news", 0.0) >= floor - 1e-9]
    return kept or results


def _binom_two_sided_p(k: int, n: int, p: float) -> float:
    """Binom(n,p) 下「機率不高於 k 結果」之總機率（雙尾近似）；與 validate_news 同法。"""
    if n == 0:
        return 1.0
    probs = [comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    pk = probs[k]
    return min(1.0, sum(pr for pr in probs if pr <= pk + 1e-12))


def dim_significant(samples: list[dict], dim: str, min_n: int = 8, alpha: float = 0.15) -> dict:
    """第七面顯著性護欄：覆蓋的「非中性日」中方向命中是否顯著優於擲硬幣(0.5)。

    樣本不足（active < min_n）或不顯著（二項 p≥alpha 或命中率≤50%）-> significant=False，
    呼叫端據此把該面權重強制歸 0，避免小樣本（如 30 日）運氣虛灌命中率。
    """
    active = hit = 0
    for s in samples:
        v = s["scores"].get(dim)
        if v is None or v == 0 or s["actual"] == 0:
            continue
        active += 1
        if (1 if v > 0 else -1) == s["actual"]:
            hit += 1
    hit_rate = hit / active if active else 0.0
    pval = _binom_two_sided_p(hit, active, 0.5) if active else 1.0
    significant = active >= min_n and pval < alpha and hit_rate > 0.5
    return {"significant": significant, "active": active, "hit": hit,
            "hit_rate": round(hit_rate, 4), "p_value": round(pval, 4)}


def eligible_dims(samples: list[dict], min_n: int = 8, alpha: float = 0.15) -> dict:
    """逐面顯著性。技術面為基準面恆視為合格；其餘面須通過二項檢定才可獲得權重。"""
    return {d: dim_significant(samples, d, min_n, alpha) for d in scoring.DIMENSIONS}


def apply_guard(samples: list[dict], results: list[dict],
                min_n: int = 8, alpha: float = 0.15) -> tuple[list[dict], dict]:
    """顯著性護欄（公平核心）：覆蓋率正規化後，低覆蓋/不顯著的面不可獲得「免費權重」。

    對每個非技術面做二項檢定（vs 0.5），不顯著者（含覆蓋率過低 -> active<min_n）一律
    強制權重=0：過濾權重網格只保留這些面權重皆為 0 的方案，再交由 pick_balanced 選平衡解。
    這同時擋住第七面 30 日小樣本與消息/基本面低覆蓋的運氣灌水，確保 240 筆公平比較。
    """
    elig = eligible_dims(samples, min_n, alpha)
    # 技術面為基準面恆合格；消息面為策略指定必納面（下限 news_floor，不受顯著性歸零）故亦豁免。
    blocked = [d for d in scoring.DIMENSIONS
               if d not in ("technical", "news") and not elig[d]["significant"]]
    if blocked:
        kept = [r for r in results
                if all(r["weights"].get(d, 0.0) == 0.0 for d in blocked)]
        results = kept or results
    return results, {"eligibility": elig, "blocked": blocked}


def confidence_diagnostics(samples: list[dict], weights: dict, tau: float,
                           conf_params: dict | None = None) -> dict:
    """選項 B：各信心層（high/mid/low）的命中率、方向命中率與涵蓋率（全表態，誠實揭露）。

    全表態下總涵蓋率恆為 100%（每天都有標的方向＋信心等級）；本表用來看「高信心子集」
    是否確實命中率較高，並揭露其占比（涵蓋率），避免靠降涵蓋率作弊。
    """
    tiers = {lv: {"hit": 0, "n": 0, "dir_pred": 0, "dir_hit": 0}
             for lv in ("high", "mid", "low")}
    for s in samples:
        pred, comp = scoring.combine(s["scores"], weights, tau)
        a = confidence.assess(s["scores"], weights, comp, conf_params)
        t = tiers[a["level"]]
        t["n"] += 1
        if pred == s["actual"]:
            t["hit"] += 1
        if pred != 0:
            t["dir_pred"] += 1
            if pred == s["actual"]:
                t["dir_hit"] += 1
    n = len(samples)
    out = {}
    for lv, t in tiers.items():
        out[lv] = {
            "win_rate": round(t["hit"] / t["n"], 4) if t["n"] else 0.0,
            "dir_hit_rate": round(t["dir_hit"] / t["dir_pred"], 4) if t["dir_pred"] else 0.0,
            "n": t["n"],
            "coverage": round(t["n"] / n, 4) if n else 0.0,
        }
    return out


def signal_diagnostics(samples: list[dict]) -> list[dict]:
    """逐指標「判斷提示」：每個面與每個技術子訊號的方向命中率與作用涵蓋率。

    定義：在該訊號 sign≠0 且當日實際非中性的日子中，sign 與實際同向的比例。
    用來看哪些指標真的有預測力、哪些是雜訊，作為逐步校準依據。
    """
    rows = []
    # 七面 + 技術子訊號 + 日內子訊號
    keys = [("dim", d) for d in scoring.DIMENSIONS] + \
           [("sub", s) for s in ("ma", "kd", "rsi", "macd", "bias", "volprice")] + \
           [("idsub", s) for s in ("vwap", "pos", "tail", "trend", "volconc")] + \
           [("brsub", s) for s in ("net", "conc", "smart", "daytrade", "longterm")] + \
           [("hdsub", s) for s in ("chg1w", "chg4w", "retail")]
    for kind, name in keys:
        active = hit = 0
        for s in samples:
            if kind == "dim":
                val = s["scores"].get(name)
            elif kind == "sub":
                val = s["subsignals"].get(name)
            elif kind == "idsub":
                val = s.get("id_subsignals", {}).get(name)
            elif kind == "brsub":
                val = s.get("branch_subsignals", {}).get(name)
            else:
                val = s.get("hd_subsignals", {}).get(name)
            if val is None:
                continue
            sgn = 1 if val > 0 else (-1 if val < 0 else 0)
            if sgn == 0 or s["actual"] == 0:
                continue
            active += 1
            if sgn == s["actual"]:
                hit += 1
        rows.append({
            "kind": kind, "name": name,
            "hit_rate": round(hit / active, 4) if active else 0.0,
            "active": active,
        })
    rows.sort(key=lambda r: -r["hit_rate"])
    return rows


# ---------- 報告 ----------

def baseline_single_dim(samples: list[dict], tau: float = 0.15) -> dict:
    out = {}
    for dim in scoring.DIMENSIONS:
        w = {k: (1.0 if k == dim else 0.0) for k in scoring.DIMENSIONS}
        out[dim] = evaluate(samples, w, tau)["win_rate"]
    return out


_DIM_ZH = {"technical": "技術", "chips": "籌碼", "news": "消息", "fundamental": "基本",
           "micron": "美光", "sox": "費半", "intraday": "日內", "branch": "分點",
           "holders": "大戶", "futures": "台期"}


_SUB_ZH = {"ma": "均線", "kd": "KD", "rsi": "RSI", "macd": "MACD", "bias": "乖離", "volprice": "量價"}


_IDSUB_ZH = {"vwap": "VWAP偏離", "pos": "收盤位置", "tail": "尾盤動能",
             "trend": "日內趨勢", "volconc": "量能分布"}


_BRSUB_ZH = {"net": "主力淨額", "conc": "集中度", "smart": "聰明錢(行為)",
             "daytrade": "隔日沖(名單)", "longterm": "長線(名單)"}


_HDSUB_ZH = {"chg1w": "大戶週變", "chg4w": "大戶月變", "retail": "散戶背離"}


def _wstr(weights: dict) -> str:
    return "｜".join(f"{_DIM_ZH[d]} {weights[d]:.1f}" for d in scoring.DIMENSIONS)


def write_report(samples, coverage, results, start, end, tol, path, balanced=None,
                 diagnostics=None, confidence_tiers=None):
    n = len(samples)
    dims = scoring.DIMENSIONS
    dist = {1: 0, 0: 0, -1: 0}
    for s in samples:
        dist[s["actual"]] += 1
    best = results[0]
    singles = baseline_single_dim(samples)

    cov_str = "　".join(f"{_DIM_ZH[d]} {coverage.get(d, n)}/{n}"
                        for d in dims if d != "technical")

    lines = [
        f"# 2344 六面權重回測報告（{start} ~ {end}）",
        "",
        f"- 樣本交易日數：**{n}**　中性帶：±{tol}%",
        f"- 實際分布：偏多 {dist[1]}　中性 {dist[0]}　偏空 {dist[-1]}",
        f"- 資料涵蓋率：{cov_str}",
        "",
        "> 基準（always 偏多）命中率 = 偏多占比 = "
        f"{round(dist[1] / n, 4) if n else 0}；模型須顯著高於此與單面基準才有意義。",
        "",
        "## 最佳權重方案（純最高命中率）",
        f"- 權重：{_wstr(best['weights'])}",
        f"- 中性帶門檻 tau：{best['tau']}",
        f"- **命中率：{best['win_rate']:.2%}**　方向命中率：{best['directional_hit_rate']:.2%}"
        f"　方向涵蓋率：{best['directional_coverage']:.2%}",
    ]

    if balanced:
        lines += [
            "",
            "## 採用權重方案（命中率第一優先，同分時取最均衡，**實際採用**）",
            f"- 權重：{_wstr(balanced['weights'])}",
            f"- 中性帶門檻 tau：{balanced['tau']}",
            f"- **命中率：{balanced['win_rate']:.2%}**　方向命中率：{balanced['directional_hit_rate']:.2%}"
            f"　方向涵蓋率：{balanced['directional_coverage']:.2%}",
            "- 命中率第一優先（balance_tol 預設 0）：採最高命中率方案，僅在多個並列最佳時取較均衡者。",
        ]

    if diagnostics:
        lines += [
            "",
            "## 判斷提示指標（逐指標方向命中率，作為校準依據）",
            "> 每個訊號單獨看（sign≠0 且當日非中性的日子），方向猜對的比例；active=作用天數。",
            "| 類別 | 指標 | 方向命中率 | active |",
            "|---|---|---|---|",
        ]
        for r in diagnostics:
            label = (_DIM_ZH.get(r["name"]) or _SUB_ZH.get(r["name"])
                     or _IDSUB_ZH.get(r["name"]) or _BRSUB_ZH.get(r["name"])
                     or _HDSUB_ZH.get(r["name"], r["name"]))
            kind = {"dim": "面", "sub": "技術子訊號", "idsub": "日內子訊號",
                    "brsub": "分點子訊號", "hdsub": "大戶子訊號"}[r["kind"]]
            lines.append(f"| {kind} | {label} | {r['hit_rate']:.2%} | {r['active']} |")

    if confidence_tiers:
        lines += [
            "",
            "## 信心分層命中率（選項 B：全表態＋標信心等級，誠實揭露）",
            "> 每天仍全表態（涵蓋率合計 100%），另標信心等級；看「高信心子集」是否命中率較高。",
            "| 信心等級 | 命中率 | 方向命中率 | 樣本數 | 占比(涵蓋率) |",
            "|---|---|---|---|---|",
        ]
        for lv in ("high", "mid", "low"):
            t = confidence_tiers.get(lv, {})
            lines.append(f"| {confidence.LEVEL_ZH[lv]} | {t.get('win_rate', 0):.2%} | "
                         f"{t.get('dir_hit_rate', 0):.2%} | {t.get('n', 0)} | {t.get('coverage', 0):.2%} |")

    lines += ["", "## 單面基準命中率（weight=1 單押該面，tau=0.15）", "| 面向 | 命中率 |", "|---|---|"]
    for dim, wr in singles.items():
        lines.append(f"| {_DIM_ZH.get(dim, dim)} | {wr:.2%} |")

    header = "| " + " | ".join(_DIM_ZH[d] for d in dims) + " | tau | 命中率 | 方向命中 | 方向涵蓋 |"
    sep = "|" + "---|" * (len(dims) + 4)
    lines += ["", "## 權重命中率排行（前 15）", "", header, sep]
    for r in results[:15]:
        w = r["weights"]
        cells = " | ".join(f"{w[d]:.1f}" for d in dims)
        lines.append(f"| {cells} | {r['tau']} | {r['win_rate']:.2%} "
                     f"| {r['directional_hit_rate']:.2%} | {r['directional_coverage']:.2%} |")

    lines += [
        "",
        "## 注意與限制",
        f"- 消息面僅 {coverage['news']}/{n} 日有資料；若偏低，消息權重的優化結果信心不足，"
        "須待每日快照累積後重跑。",
        f"- 第七面（日內 1 分 K）僅 {coverage.get('intraday', 0)}/{n} 日有資料（Fugle 1 分 K 僅保留近期"
        "交易日，更早歷史無法回補，須每日累積成長）；未達顯著門檻時權重被護欄強制歸 0，"
        "coverage 與逐面顯著性（dim_significance）見 data/weights.json。",
        f"- 第八面（主力分點）僅 {coverage.get('branch', 0)}/{n} 日有資料（富邦 DJ 僅提供最新一日，"
        "歷史無法回補，須每日累積）；隔日沖/長線分類為人工種子名單，待累積後以行為統計精進。",
        f"- 第九面（TDCC 千張大戶）{coverage.get('holders', 0)}/{n} 日有資料（集保週頻、公布有 lag，"
        "以公布日 avail_date<D 比較）；週變化緩、單股訊號可能弱，未達顯著門檻時權重被護欄歸 0。",
        f"- 第十面（外資台指期未平倉）{coverage.get('futures', 0)}/{n} 日有資料（盤後公布，date<D 取 D-1）；"
        "市場級 regime 訊號，與美光/費半（市場 beta）可能相關，單股增益有限但可能穩定。",
        "- 基本面（月營收）變動緩慢且舊月難回補，貢獻有限。",
        "- 隔夜美光/費半為強外生訊號，但與大盤高度相關，須留意過擬合；建議以樣本外驗證複核。",
        "- 本回測無交易成本/滑價假設，命中率非報酬率，僅供權重相對比較。",
        "",
        "本報告為公開資訊回測，非投資建議，據此操作風險自負。",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> None:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    start = opt("--start", "2025-01-01")
    end = opt("--end", config.today_str()[:4] + "-12-31")
    tol = float(opt("--tol", str(NEUTRAL_TOL)))
    balance_tol = float(opt("--balance-tol", "0.0"))  # 命中率第一優先：預設 0=不為平衡犧牲命中率
    news_floor = float(opt("--news-floor", str(NEWS_FLOOR)))  # 消息面總權重下限（0 可停用）

    tdb.init_db()
    with tdb.connect() as conn:
        samples, coverage = build_samples(conn, config.SYMBOL, start, end, tol)

    if not samples:
        print("[backtest] 無樣本：請先 ingest --backfill-candles（與 --backfill-chips）。")
        return

    results = optimize(samples)
    results = restrict_news_floor(results, news_floor)  # 約束：只在 news≥floor 的搭配中選（消息面必納）
    results, guard = apply_guard(samples, results)   # 顯著性護欄（技術/消息面豁免）
    best = results[0]
    balanced = pick_balanced(results, balance_tol)   # 實際採用：news≥floor 中最高命中、同分取均衡
    diagnostics = signal_diagnostics(samples)
    conf_tiers = confidence_diagnostics(samples, balanced["weights"], balanced["tau"])

    out = {
        "symbol": config.SYMBOL,
        "weights": balanced["weights"],              # 採平衡權重
        "neutral_threshold": balanced["tau"],
        "as_of": config.now_tpe().isoformat(),
        "win_rate": balanced["win_rate"],
        "directional_hit_rate": balanced["directional_hit_rate"],
        "directional_coverage": balanced["directional_coverage"],
        "n_days": len(samples),
        "window": [start, end],
        "actual_neutral_tol_pct": tol,
        "balance_tol": balance_tol,
        "news_floor": news_floor,           # 消息面總權重下限（約束選出的方案 news≥此值）
        "raw_best": {"weights": best["weights"], "tau": best["tau"], "win_rate": best["win_rate"]},
        "coverage": coverage,
        "blocked_dims": guard["blocked"],
        "intraday_significant": guard["eligibility"]["intraday"]["significant"],
        "intraday_active": guard["eligibility"]["intraday"]["active"],
        "intraday_hit_rate": guard["eligibility"]["intraday"]["hit_rate"],
        "dim_significance": {d: {"significant": g["significant"], "active": g["active"],
                                 "hit_rate": g["hit_rate"]}
                             for d, g in guard["eligibility"].items()},
        "confidence": {"thresholds": {k: confidence.DEFAULT_CONF_PARAMS[k]
                                      for k in ("conf_hi", "conf_mid", "conf_mag_full",
                                                "w_conf_mag", "w_conf_agree", "w_conf_chip")},
                       "tiers": conf_tiers},
        "score_params_file": str(config.score_params_path()),
    }
    weights_path = config.weights_path()
    weights_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = config.SYMBOL_REPORTS_DIR / f"backtest_{start}_{end}.md"
    write_report(samples, coverage, results, start, end, tol, report_path, balanced,
                 diagnostics, conf_tiers)

    print(f"[backtest] 樣本 {len(samples)} 日")
    print(f"  純最佳 命中率 {best['win_rate']:.2%}  權重 "
          + "/".join(f"{_DIM_ZH[d]}{best['weights'][d]:.1f}" for d in scoring.DIMENSIONS)
          + f"  tau={best['tau']}")
    print(f"  平衡(採用) 命中率 {balanced['win_rate']:.2%}  權重 "
          + "/".join(f"{_DIM_ZH[d]}{balanced['weights'][d]:.1f}" for d in scoring.DIMENSIONS)
          + f"  tau={balanced['tau']}")
    print(f"  消息面約束：於 news≥{news_floor} 的權重搭配中選最佳（採用 news={balanced['weights']['news']:.1f}）")
    print(f"  -> {weights_path}")
    print(f"  -> {report_path}")


if __name__ == "__main__":
    main(sys.argv)
