"""橫斷面多股排序（選項 D）的股票池。

兩種模式：
- 預設策展清單 UNIVERSE：記憶體/半導體/IC 設計/封測/伺服器等同產業可比股（小而精）。
- 全市場模式（`is_common_stock`）：4 位數普通股（排除 ETF 00xx、權證等），供大樣本跨股 IC。
  因 TWSE MI_INDEX/T86 一次回傳全市場，全市場模式不增加抓取成本，只多存資料列。
回測再以每日成交量取「流動性前 N 檔」形成乾淨橫斷面。純資料，無耦合。
"""
from __future__ import annotations

import re

# 普通股：4 位數且不以 0 開頭（排除 00xx ETF、5-6 位權證/受益證券）
_COMMON_RE = re.compile(r"^[1-9]\d{3}$")


def is_common_stock(symbol: str) -> bool:
    return bool(_COMMON_RE.match(str(symbol).strip()))

# {代號: 名稱}（名稱僅供報告閱讀，計算只用代號）
UNIVERSE: dict[str, str] = {
    "2344": "華邦電", "2408": "南亞科", "3260": "威剛", "4967": "十銓", "8299": "群聯",
    "2337": "旺宏", "2330": "台積電", "2303": "聯電", "2454": "聯發科", "3034": "聯詠",
    "2379": "瑞昱", "3711": "日月光投控", "3443": "創意", "3035": "智原", "3037": "欣興",
    "3017": "奇鋐", "2308": "台達電", "2382": "廣達", "2376": "技嘉", "2357": "華碩",
    "3231": "緯創", "2356": "英業達", "6669": "緯穎", "3661": "世芯-KY", "2451": "創見",
}

SYMBOLS: list[str] = list(UNIVERSE.keys())


def name(symbol: str) -> str:
    return UNIVERSE.get(symbol, symbol)
