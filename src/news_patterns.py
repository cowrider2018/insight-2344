"""消息面「特殊型態」登錄與比對 + 已驗證極性載入。

理念：不預設「目標價調升=利多」。許多型態是反直覺的（如小摩/外資調升目標價後短期常見
賣出、利多出盡）。每個型態的多空極性，必須由 validate_news.py **獨立以歷史驗證**
（足夠樣本 + 統計顯著）才採用；未驗證者極性 0（中性），避免誤判。

型態定義為「群組的 AND-of-OR」：title 需在每個群組各命中至少一個關鍵字才算符合。
"""
from __future__ import annotations

import json

import config

# name -> {"groups": [[同義詞...], ...], "note": 假設/說明}
PATTERNS: dict = {
    "broker_target_up": {
        "groups": [["小摩", "大摩", "摩根", "高盛", "美林", "花旗", "大和", "野村", "瑞銀",
                    "外資", "分析師", "券商", "投行"],
                   ["調升", "升評", "喊買", "看好", "上修", "目標價", "買進", "加碼評等", "登"]],
        "note": "直覺偏多；常見利多出盡/賣出（反直覺，須驗證）",
    },
    "broker_target_down": {
        "groups": [["小摩", "大摩", "摩根", "高盛", "美林", "花旗", "大和", "野村", "瑞銀",
                    "外資", "分析師", "券商", "投行"],
                   ["調降", "降評", "示警", "看壞", "下修", "賣出評等", "減碼", "警告"]],
        "note": "直覺偏空；可能利空出盡（須驗證）",
    },
    "limit_up": {"groups": [["漲停"]], "note": "漲停"},
    "limit_down": {"groups": [["跌停"]], "note": "跌停"},
    "memory_crash": {
        "groups": [["記憶體", "DRAM", "華邦", "南亞科", "美光", "晶片"],
                   ["大屠殺", "重挫", "崩", "暴跌", "跌停", "殺", "跳水", "災區"]],
        "note": "記憶體族群急殺",
    },
    "dram_price_up": {
        "groups": [["記憶體", "DRAM", "DDR", "HBM", "顆粒", "NAND"],
                   ["漲價", "缺貨", "供不應求", "報價", "調漲", "拉貨", "延燒"]],
        "note": "記憶體漲價/缺貨利多",
    },
    "foreign_sell": {
        "groups": [["外資"], ["賣超", "提款", "調節", "砍", "倒貨"]],
        "note": "外資賣超",
    },
    "foreign_buy": {
        "groups": [["外資"], ["買超", "加碼", "回補", "敲進"]],
        "note": "外資買超",
    },
    "earnings_beat": {
        "groups": [["財報", "營收", "獲利", "EPS", "業績"],
                   ["優於預期", "創新高", "大增", "暴增", "報捷", "亮眼", "成長"]],
        "note": "財報/營收優於預期",
    },
    "earnings_miss": {
        "groups": [["財報", "營收", "獲利", "EPS", "業績"],
                   ["不如預期", "衰退", "下滑", "虧損", "減少", "利空"]],
        "note": "財報/營收不如預期",
    },
    "us_chip_selloff": {
        "groups": [["費半", "美股", "那斯達克", "科技股", "晶片股", "AI", "費城半導體"],
                   ["重挫", "急殺", "大跌", "崩", "暴跌", "全面"]],
        "note": "隔夜美股/費半重挫",
    },
}


# 專家先驗極性（validate_news 尚未統計驗證前的備援；正=偏多、負=偏空/反向）。
# 值為 signed edge：sign=方向、|值|=強度（0~0.5，強度 = min(|edge|*2,1)，0.5→滿格 1.0）。
# 反直覺型態（券商/外資調升目標價常見利多出盡＋倒貨）給「強反向」——此為策略核心先驗。
# 已統計驗證的型態（news_patterns.json validated=true）之極性優先於此先驗（見 scoring）。
PRIOR_EDGE: dict[str, float] = {
    "broker_target_up":   -0.50,   # 旗艦反向：調升目標價常倒貨/利多出盡 → 強偏空
    "broker_target_down": -0.15,   # 直覺偏空（弱先驗）
    "limit_up":           +0.15,
    "limit_down":         -0.30,
    "memory_crash":       -0.50,   # 記憶體急殺 → 強偏空
    "dram_price_up":      +0.30,
    "foreign_sell":       -0.30,   # 外資賣超/倒貨 → 偏空
    "foreign_buy":        +0.30,
    "earnings_beat":      +0.30,
    "earnings_miss":      -0.35,
    "us_chip_selloff":    -0.40,   # 隔夜費半/美股重挫 → 偏空
}


def match(title: str | None) -> list[str]:
    """回傳 title 命中的所有型態名稱。"""
    t = title or ""
    out = []
    for name, spec in PATTERNS.items():
        if all(any(kw in t for kw in group) for group in spec["groups"]):
            out.append(name)
    return out


def load_validated() -> dict:
    """載入 data/news_patterns.json（validate_news.py 產生）。無則回空 dict。"""
    f = config.news_patterns_path()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data.get("patterns", {}) if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


if __name__ == "__main__":
    tests = [
        "美光還在漲！南亞科意外跌停、華邦電暴跌6％「大摩驚爆預期已達極限」",
        "DRAM缺貨漲價延燒到DDR2 預估這兩家受惠",
        "外資買超684億元 加碼台積電1.43萬張 大砍群創、華邦電均逾10萬張",
        "費半急殺近8%，AI科技股全面重挫",
    ]
    for t in tests:
        print(match(t), "<-", t[:30])
