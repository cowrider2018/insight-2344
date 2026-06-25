"""確定性六面評分：把研判規則編碼成純函式，並將門檻/尺度抽成可調參數。

每個 score_* 回傳 [-1, +1]（正=偏多、負=偏空、0=中性/無資料）。
另提供 *_signals() 回傳各子訊號 breakdown，作為「判斷提示指標」與逐步校準依據。

參數集中於 PARAMS（可由 data/score_params.json 覆寫），讓 calibrate.py 逐步調適；
所有評分函式皆可傳入 params 以便回測/校準時試不同組合。設計成純函式，便於大規模重算。
"""
from __future__ import annotations

import json

import config
import news_patterns

DIMENSIONS = ("technical", "chips", "news", "fundamental", "micron", "sox")

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


# ---------------- 綜合 ----------------

def combine(scores: dict, weights: dict, tau: float = 0.15) -> tuple[int, float]:
    """回傳 (label, composite)。label: +1 偏多 / -1 偏空 / 0 中性。"""
    composite = sum(weights.get(k, 0.0) * scores.get(k, 0.0) for k in DIMENSIONS)
    if composite > tau:
        return 1, composite
    if composite < -tau:
        return -1, composite
    return 0, composite
