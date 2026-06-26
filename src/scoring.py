"""確定性六面評分：把研判規則編碼成純函式，並將門檻/尺度抽成可調參數。

每個 score_* 回傳 [-1, +1]（正=偏多、負=偏空、0=中性/無資料）。
另提供 *_signals() 回傳各子訊號 breakdown，作為「判斷提示指標」與逐步校準依據。

參數集中於 PARAMS（可由 data/score_params.json 覆寫），讓 calibrate.py 逐步調適；
所有評分函式皆可傳入 params 以便回測/校準時試不同組合。設計成純函式，便於大規模重算。
"""
from __future__ import annotations

import json

import broker_tags
import config
import news_patterns

DIMENSIONS = ("technical", "chips", "news", "fundamental", "micron", "sox", "intraday",
              "branch", "holders", "futures")

# 可調參數（門檻、尺度、子訊號權重）。calibrate.py 會逐步調整這些。
DEFAULT_PARAMS: dict = {
    # 技術面子訊號尺度
    "kd_div": 8.0,            # (k-d)/kd_div 飽和
    "rsi_div": 30.0,          # (rsi-50)/rsi_div
    "macd_price_frac": 0.01,  # osc/(macd_price_frac*price)
    "bias_div": 20.0,         # -bias20/bias_div（均值回歸）
    # 技術面子訊號權重（會被正規化）
    "w_ma": 0.30, "w_kd": 0.20, "w_rsi": 0.15, "w_macd": 0.20,
    "w_bias": 0.05, "w_volprice": 0.10,
    "volprice_div": 1.0,      # (latest_vol/vol_ma5 - 1)/volprice_div 量價放大倍率
    # 籌碼面
    "chips_inst_frac": 0.15,    # total_net/(frac*日均量張)
    "chips_fixed_scale": 30000.0,
    "chips_inst_w": 0.8, "chips_margin_w": 0.2,
    # 消息面
    "news_smoothing": 3.0,
    # 基本面
    "fund_yoy_div": 100.0, "fund_mom_div": 20.0,
    # 隔夜美股
    "us_scale": 3.0,
    # 第七面：日內 1 分 K 子訊號尺度（飽和分母）
    "id_vwap_div": 0.01,      # (close-vwap)/vwap / id_vwap_div
    "id_tail_div": 0.01,      # (close-收盤前30分價)/價 / id_tail_div（尾盤動能）
    "id_trend_div": 0.02,     # (close-open)/open / id_trend_div（日內趨勢）
    "id_volconc_div": 0.3,    # (尾盤量佔比-0.5)/id_volconc_div（量能分布）
    # 第七面子訊號權重（會被正規化）
    "w_id_vwap": 0.25, "w_id_pos": 0.20, "w_id_tail": 0.25,
    "w_id_trend": 0.20, "w_id_volconc": 0.10,
    # 第八面：主力分點尺度（張）與子權重
    "branch_net_div": 20000.0,    # 主力買賣超合計 / div
    "branch_grp_div": 8000.0,     # 隔日沖/長線分點群淨額 / div（種子名單，弱）
    "branch_smart_div": 12000.0,  # 行為式 polarity 加權淨額 / div
    "w_br_net": 0.25, "w_br_conc": 0.20, "w_br_smart": 0.45,
    "w_br_daytrade": 0.05, "w_br_longterm": 0.05,
    # 第九面：TDCC 千張大戶持股比率（占比為百分點，週/月變化以百分點計）
    "holder_chg1w_div": 0.5,    # 大戶比率週變化(pp)/div 飽和
    "holder_chg4w_div": 1.0,    # 大戶比率月變化(pp)/div
    "holder_retail_div": 1.0,   # 散戶比率週變化(pp)/div（上升=分散=偏空，取負）
    "w_hd_chg1w": 0.5, "w_hd_chg4w": 0.4, "w_hd_retail": 0.1,
    # 第十面：外資台指期未平倉（市場級 regime，口）
    "fut_oi_div": 40000.0,      # 外資淨未平倉口數 / div 飽和
    "fut_oi_chg_div": 15000.0,  # 日變化 / div
    "w_fut_level": 0.7, "w_fut_chg": 0.3,
}


def load_params() -> dict:
    """回傳 DEFAULT_PARAMS 疊加 data/score_params.json（若存在）。"""
    p = dict(DEFAULT_PARAMS)
    f = config.DATA_DIR / "score_params.json"
    if f.exists():
        try:
            p.update(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return p


PARAMS = load_params()


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _safe(v, default=None):
    return v if isinstance(v, (int, float)) else default


def _wavg(parts: list[tuple[float, float]]) -> float:
    """parts: (sub_score, weight)。回傳加權平均並 clamp。"""
    parts = [(s, w) for s, w in parts if w > 0]
    if not parts:
        return 0.0
    wsum = sum(w for _, w in parts)
    return clamp(sum(s * w for s, w in parts) / wsum) if wsum else 0.0


# ---------------- 技術面 ----------------

def technical_signals(technical: dict, prev_close: float | None, params: dict | None = None) -> dict:
    """回傳各技術子訊號 [-1,1]：ma / kd / rsi / macd / bias / volprice。"""
    p = params or PARAMS
    out: dict = {}
    if not technical or not prev_close:
        return out
    ma = technical.get("ma") or {}

    ma5, ma20, ma60 = _safe(ma.get("ma5")), _safe(ma.get("ma20")), _safe(ma.get("ma60"))
    checks = []
    for m in (ma5, ma20, ma60):
        if m is not None:
            checks.append(1.0 if prev_close > m else 0.0)
    if ma5 is not None and ma20 is not None:
        checks.append(1.0 if ma5 > ma20 else 0.0)
    if ma20 is not None and ma60 is not None:
        checks.append(1.0 if ma20 > ma60 else 0.0)
    if checks:
        out["ma"] = 2 * (sum(checks) / len(checks)) - 1

    kd = technical.get("kd") or {}
    k, d = _safe(kd.get("k")), _safe(kd.get("d"))
    if k is not None and d is not None:
        out["kd"] = clamp((k - d) / p["kd_div"])

    rsi = technical.get("rsi") or {}
    r10 = _safe(rsi.get("rsi10")) or _safe(rsi.get("rsi5"))
    if r10 is not None:
        out["rsi"] = clamp((r10 - 50) / p["rsi_div"])

    macd = technical.get("macd") or {}
    osc = _safe(macd.get("osc"))
    if osc is not None:
        out["macd"] = clamp(osc / (p["macd_price_frac"] * prev_close))

    bias20 = _safe(technical.get("bias20"))
    if bias20 is not None:
        out["bias"] = clamp(-bias20 / p["bias_div"])

    vol_ma5 = _safe(technical.get("vol_ma5"))
    latest_vol = _safe(technical.get("latest_volume"))
    candles = technical.get("candles_60d") or []
    if vol_ma5 and latest_vol and candles:
        last_chg = _safe(candles[-1].get("change"), 0.0) or 0.0
        dir_sign = 1.0 if last_chg > 0 else (-1.0 if last_chg < 0 else 0.0)
        out["volprice"] = clamp((latest_vol / vol_ma5 - 1.0) / p["volprice_div"]) * dir_sign

    return out


def score_technical(technical: dict, prev_close: float | None, params: dict | None = None) -> float:
    p = params or PARAMS
    sig = technical_signals(technical, prev_close, p)
    return _wavg([
        (sig.get("ma", 0.0), p["w_ma"]),
        (sig.get("kd", 0.0), p["w_kd"]),
        (sig.get("rsi", 0.0), p["w_rsi"]),
        (sig.get("macd", 0.0), p["w_macd"]),
        (sig.get("bias", 0.0), p["w_bias"]),
        (sig.get("volprice", 0.0), p["w_volprice"]),
    ]) if sig else 0.0


# ---------------- 籌碼面 ----------------

def score_chips(chips: dict, ref_volume_lots: float | None = None, params: dict | None = None) -> float:
    p = params or PARAMS
    if not chips:
        return 0.0
    total_net = _safe(chips.get("total_net"))
    parts: list[tuple[float, float]] = []
    if total_net is not None:
        if ref_volume_lots and ref_volume_lots > 0:
            inst = clamp(total_net / (p["chips_inst_frac"] * ref_volume_lots))
        else:
            inst = clamp(total_net / p["chips_fixed_scale"])
        parts.append((inst, p["chips_inst_w"]))
    margin_chg = _safe(chips.get("margin_chg"))
    margin_balance = _safe(chips.get("margin_balance"))
    if margin_chg is not None and margin_balance:
        parts.append((clamp(-margin_chg / (0.05 * abs(margin_balance) + 1e-9)), p["chips_margin_w"]))
    return _wavg(parts)


# ---------------- 消息面 ----------------

_BEAR_KW = ["重挫", "跌停", "大屠殺", "暴跌", "摜破", "跳水", "崩", "利空", "翻黑",
            "苦主", "重災", "保衛戰", "急殺", "慘", "殺", "賣超", "下殺", "翻車", "曇花"]
_BULL_KW = ["漲停", "飆", "大漲", "翻紅", "利多", "創新高", "新高", "買超", "加碼", "狂買",
            "暴漲", "噴", "兆元", "報捷", "漲價", "缺貨", "受惠", "豪賭", "開趴", "狂歡"]


def _kw_polarity(title: str) -> int:
    t = title or ""
    bull = sum(1 for kw in _BULL_KW if kw in t)
    bear = sum(1 for kw in _BEAR_KW if kw in t)
    return 1 if bull > bear else (-1 if bear > bull else 0)


# 已獨立驗證的消息型態極性（由 validate_news.py 產生）；空則退回 tag/關鍵字
NEWS_PATTERNS: dict = news_patterns.load_validated()


def _load_branch_polarity() -> dict:
    """載入 data/branch_polarity.json 的已驗證分點極性（由 validate_branches.py 產生）。

    回傳 {branch: {"polarity": +1/-1/0}}；不存在則空（smart 子訊號不作用）。
    """
    f = config.DATA_DIR / "branch_polarity.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data.get("branches", {}) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


# 行為式分點極性（聰明錢 +1 / 隔日沖反指標 -1）；oos_check 會以 train 期覆寫以防洩漏
BRANCH_POLARITY: dict = _load_branch_polarity()


def _score_news_validated(news_list: list[dict], p: dict) -> float | None:
    """以「已驗證型態極性」評分；無任何已驗證型態命中則回 None（交回退邏輯）。"""
    if not NEWS_PATTERNS:
        return None
    s = 0.0
    matched = 0
    for it in news_list:
        for name in news_patterns.match(it.get("title", "")):
            v = NEWS_PATTERNS.get(name)
            if v and v.get("polarity"):
                # 以 edge 幅度轉換為強度（反直覺型態 edge 為負，polarity 已含方向）
                s += v["polarity"] * min(abs(v.get("edge", 0.0)) * 2.0, 1.0)
                matched += 1
    if matched == 0:
        return None
    return clamp(s / (matched + p["news_smoothing"]))


def score_news(news_list: list[dict], params: dict | None = None) -> float:
    p = params or PARAMS
    if not news_list:
        return 0.0
    # 1) 優先用獨立驗證過的型態極性（含反直覺：利多出盡等）
    v = _score_news_validated(news_list, p)
    if v is not None:
        return v
    # 2) 退回 CMoney 自身多空標記 + 關鍵字（皆未經驗證，故僅作備援）
    bull = bear = 0
    for n in news_list:
        bob = n.get("bull_or_bear")
        if bob == 1:
            bull += 1
        elif bob == 2:
            bear += 1
        else:
            pol = _kw_polarity(n.get("title", ""))
            bull += pol > 0
            bear += pol < 0
    if bull == 0 and bear == 0:
        return 0.0
    return clamp((bull - bear) / (bull + bear + p["news_smoothing"]))


# ---------------- 基本面 ----------------

def score_fundamental(fundamental_or_revenue: dict | None, params: dict | None = None) -> float:
    p = params or PARAMS
    if not fundamental_or_revenue:
        return 0.0
    src = fundamental_or_revenue
    rev = src.get("monthly_revenue") if "monthly_revenue" in src else src
    if not rev:
        return 0.0
    yoy = _safe(rev.get("yoy"))
    mom = _safe(rev.get("mom"))
    parts = []
    if yoy is not None:
        parts.append(clamp(yoy / p["fund_yoy_div"]) * 0.5)
    if mom is not None:
        parts.append(clamp(mom / p["fund_mom_div"]) * 0.5)
    return clamp(sum(parts)) if parts else 0.0


# ---------------- 隔夜美股（美光 / 費半）----------------

def score_us(us_row: dict | None, params: dict | None = None) -> float:
    p = params or PARAMS
    if not us_row:
        return 0.0
    pct = _safe(us_row.get("change_pct"))
    if pct is None:
        return 0.0
    return clamp(pct / p["us_scale"])


# ---------------- 第七面：日內 1 分 K（前一日走勢細節）----------------

def intraday_signals(bars: list[dict] | None, params: dict | None = None) -> dict:
    """由 D-1 的 1 分 K bars 萃取日內子訊號 [-1,1]：vwap / pos / tail / trend / volconc。

    bars：單一交易日由早到晚的 1 分 bars（time/open/high/low/close/volume）。
    各子訊號皆為純函式，缺資料則略過該項（不影響其他）。
    """
    p = params or PARAMS
    out: dict = {}
    if not bars or len(bars) < 2:
        return out

    opens = [_safe(b.get("open")) for b in bars]
    highs = [_safe(b.get("high")) for b in bars]
    lows = [_safe(b.get("low")) for b in bars]
    closes = [_safe(b.get("close")) for b in bars]
    vols = [_safe(b.get("volume"), 0.0) or 0.0 for b in bars]

    day_open = next((o for o in opens if o is not None), None)
    close = next((c for c in reversed(closes) if c is not None), None)
    if day_open is None or close is None:
        return out
    day_high = max((h for h in highs if h is not None), default=None)
    day_low = min((lo for lo in lows if lo is not None), default=None)

    # VWAP 偏離：收盤相對日內成交量加權均價
    num = den = 0.0
    for h, lo, c, v in zip(highs, lows, closes, vols):
        if None in (h, lo, c) or v <= 0:
            continue
        num += (h + lo + c) / 3.0 * v
        den += v
    if den > 0:
        vwap = num / den
        if vwap:
            out["vwap"] = clamp((close - vwap) / vwap / p["id_vwap_div"])

    # 收盤在日內高低區間的位置（收高=偏多）
    if day_high is not None and day_low is not None and day_high > day_low:
        out["pos"] = clamp((close - day_low) / (day_high - day_low) * 2 - 1)

    # 尾盤動能：收盤相對「收盤前約 30 分鐘」價
    ref_idx = max(0, len(closes) - 31)
    ref_close = closes[ref_idx]
    if ref_close:
        out["tail"] = clamp((close - ref_close) / ref_close / p["id_tail_div"])

    # 日內趨勢：開盤 -> 收盤
    if day_open:
        out["trend"] = clamp((close - day_open) / day_open / p["id_trend_div"])

    # 量能分布：尾盤（後半段）量佔比偏離 0.5，乘尾盤方向
    total_vol = sum(vols)
    if total_vol > 0:
        mid = len(vols) // 2
        late_share = sum(vols[mid:]) / total_vol
        dir_sign = 1.0 if close > day_open else (-1.0 if close < day_open else 0.0)
        out["volconc"] = clamp((late_share - 0.5) / p["id_volconc_div"]) * dir_sign

    return out


def score_intraday(bars: list[dict] | None, params: dict | None = None) -> float:
    p = params or PARAMS
    sig = intraday_signals(bars, p)
    return _wavg([
        (sig.get("vwap", 0.0), p["w_id_vwap"]),
        (sig.get("pos", 0.0), p["w_id_pos"]),
        (sig.get("tail", 0.0), p["w_id_tail"]),
        (sig.get("trend", 0.0), p["w_id_trend"]),
        (sig.get("volconc", 0.0), p["w_id_volconc"]),
    ]) if sig else 0.0


# ---------------- 第八面：主力分點（券商分點 / 隔日沖辨識）----------------

def branch_signals(rows: list[dict] | None, params: dict | None = None,
                   wf_score: float | None = None) -> dict:
    """由 D-1 券商分點買賣超萃取子訊號 [-1,1]：net / conc / smart / daytrade / longterm。

    rows：單日分點列（branch / buy_lots / sell_lots / net_lots，net=買-賣，張）。
    wf_score：branch_model 的 walk-forward 行為分數（優先作為 smart）；None 時退回全窗 polarity。
    """
    p = params or PARAMS
    out: dict = {}
    if not rows:
        return out

    nets = [_safe(r.get("net_lots"), 0.0) or 0.0 for r in rows]
    total_net = sum(nets)
    abs_sum = sum(abs(x) for x in nets)

    # 主力買賣超合計（前 N 大分點淨額和）
    out["net"] = clamp(total_net / p["branch_net_div"])

    # 方向集中度：買賣方淨額一致度（正=買方主導）
    if abs_sum > 0:
        out["conc"] = clamp(total_net / abs_sum)

    # 隔日沖分點群淨額：大買 -> 次日常倒貨，取負號（偏空）
    dt_net = sum(n for n, r in zip(nets, rows)
                 if broker_tags.classify(r.get("branch")) == "daytrade")
    out["daytrade"] = clamp(-dt_net / p["branch_grp_div"])

    # 長線/官股/外資分點群淨額：留倉 -> 偏多
    lt_net = sum(n for n, r in zip(nets, rows)
                 if broker_tags.classify(r.get("branch")) == "longterm")
    out["longterm"] = clamp(lt_net / p["branch_grp_div"])

    # 行為式聰明錢/隔日沖（smart）：用 branch_model 的 walk-forward 行為分數（無 look-ahead）。
    # 無分數（暖身期）則不產生 smart 子訊號（該日分點面僅由 net/conc 構成）。
    if wf_score is not None:
        out["smart"] = clamp(wf_score)

    return out


def score_branch(rows: list[dict] | None, params: dict | None = None,
                 wf_score: float | None = None) -> float:
    p = params or PARAMS
    sig = branch_signals(rows, p, wf_score)
    return _wavg([
        (sig.get("net", 0.0), p["w_br_net"]),
        (sig.get("conc", 0.0), p["w_br_conc"]),
        (sig.get("smart", 0.0), p["w_br_smart"]),
        (sig.get("daytrade", 0.0), p["w_br_daytrade"]),
        (sig.get("longterm", 0.0), p["w_br_longterm"]),
    ]) if sig else 0.0


# ---------------- 第九面：TDCC 千張大戶持股比率 ----------------

def holders_signals(row: dict | None, params: dict | None = None) -> dict:
    """由集保大戶持股比率變化萃取子訊號 [-1,1]：chg1w / chg4w / retail。

    row：tdcc_asof 回傳（big_pct + big_chg_1w/4w + retail_chg_1w，皆百分點）。
    大戶比率上升（吃貨）= 偏多；散戶比率上升（籌碼分散）= 偏空（取負）。
    """
    p = params or PARAMS
    out: dict = {}
    if not row:
        return out
    c1 = _safe(row.get("big_chg_1w"))
    if c1 is not None:
        out["chg1w"] = clamp(c1 / p["holder_chg1w_div"])
    c4 = _safe(row.get("big_chg_4w"))
    if c4 is not None:
        out["chg4w"] = clamp(c4 / p["holder_chg4w_div"])
    rc = _safe(row.get("retail_chg_1w"))
    if rc is not None:
        out["retail"] = clamp(-rc / p["holder_retail_div"])
    return out


def score_holders(row: dict | None, params: dict | None = None) -> float:
    p = params or PARAMS
    sig = holders_signals(row, p)
    return _wavg([
        (sig.get("chg1w", 0.0), p["w_hd_chg1w"]),
        (sig.get("chg4w", 0.0), p["w_hd_chg4w"]),
        (sig.get("retail", 0.0), p["w_hd_retail"]),
    ]) if sig else 0.0


# ---------------- 第十面：外資台指期未平倉（市場級 regime）----------------

def score_futures(row: dict | None, params: dict | None = None) -> float:
    """外資台指期淨未平倉口數（與日變化）線性正規化。正=外資淨多單 -> 偏多。

    row：futures_oi_asof 回傳（foreign_net_oi + oi_chg）。
    """
    p = params or PARAMS
    if not row:
        return 0.0
    parts: list[tuple[float, float]] = []
    lvl = _safe(row.get("foreign_net_oi"))
    if lvl is not None:
        parts.append((clamp(lvl / p["fut_oi_div"]), p["w_fut_level"]))
    chg = _safe(row.get("oi_chg"))
    if chg is not None:
        parts.append((clamp(chg / p["fut_oi_chg_div"]), p["w_fut_chg"]))
    return _wavg(parts)


# ---------------- 綜合 ----------------

def combine(scores: dict, weights: dict, tau: float = 0.15) -> tuple[int, float]:
    """回傳 (label, composite)。label: +1 偏多 / -1 偏空 / 0 中性。

    覆蓋率正規化：只對「當天有資料的面」（score 非 None）加權，並以其權重和重新正規化，
    讓不同覆蓋率的交易日 composite 尺度一致（公平）。當天有資料的面權重全為 0 -> 中性。
    """
    active = [(weights.get(k, 0.0), scores[k]) for k in DIMENSIONS if scores.get(k) is not None]
    wsum = sum(w for w, _ in active)
    if wsum <= 0:
        return 0, 0.0
    composite = sum(w * s for w, s in active) / wsum
    if composite > tau:
        return 1, composite
    if composite < -tau:
        return -1, composite
    return 0, composite
