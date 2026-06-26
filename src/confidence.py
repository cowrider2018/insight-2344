"""信心評估（選項 B：全表態＋標信心等級）。加法式、不改 combine 核心，低耦合。

每天仍給方向（沿用 scoring.combine 的 label/composite），另**附信心等級**（高/中/低），
不降低涵蓋率（不做「觀望」）。信心 conf∈[0,1] 由三項綜合：
  1) |composite| 離中性距離（主驅動；對齊「|composite|≥0.4 時命中率較高」之觀察）
  2) 訊號一致性：有資料且非中性的面中，與 composite 同號者之**權重**比例（0.5=隨機）
  3) 籌碼信心：籌碼族（chips/branch/holders）分數平均強度（高集中=高信心）
等級由門檻 conf_hi/conf_mid 決定（可調）。回測以 confidence_diagnostics 誠實揭露各層命中率與涵蓋率。
"""
from __future__ import annotations

import scoring

DEFAULT_CONF_PARAMS: dict = {
    "conf_mag_full": 0.4,      # |composite| 達此值 -> 距離項=1
    "w_conf_mag": 0.5, "w_conf_agree": 0.3, "w_conf_chip": 0.2,
    "conf_hi": 0.6, "conf_mid": 0.35,
    "chip_dims": ("chips", "branch", "holders"),
}

LEVEL_ZH = {"high": "高", "mid": "中", "low": "低"}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def assess(scores: dict, weights: dict, composite: float,
           params: dict | None = None) -> dict:
    """回傳 {conf, level, mag_term, agree_term, chip_term, agree_frac}。"""
    p = {**DEFAULT_CONF_PARAMS, **(params or {})}
    mag = abs(composite)
    mag_term = _clamp01(mag / p["conf_mag_full"]) if p["conf_mag_full"] > 0 else 0.0

    csign = 1 if composite > 0 else (-1 if composite < 0 else 0)
    agree_w = total_w = 0.0
    for d in scoring.DIMENSIONS:
        s, w = scores.get(d), weights.get(d, 0.0)
        if s is None or s == 0 or w <= 0:
            continue
        total_w += w
        if (1 if s > 0 else -1) == csign:
            agree_w += w
    agree_frac = (agree_w / total_w) if total_w else 0.0
    agree_term = _clamp01((agree_frac - 0.5) / 0.5)  # 0.5=隨機 -> 0；全一致 -> 1

    chip_vals = [abs(scores[d]) for d in p["chip_dims"] if scores.get(d) is not None]
    chip_term = _clamp01(sum(chip_vals) / len(chip_vals)) if chip_vals else 0.0

    conf = _clamp01(p["w_conf_mag"] * mag_term + p["w_conf_agree"] * agree_term
                    + p["w_conf_chip"] * chip_term)
    level = "high" if conf >= p["conf_hi"] else ("mid" if conf >= p["conf_mid"] else "low")
    return {"conf": round(conf, 4), "level": level,
            "mag_term": round(mag_term, 4), "agree_term": round(agree_term, 4),
            "chip_term": round(chip_term, 4), "agree_frac": round(agree_frac, 4)}
