"""券商分點分類：辨識「隔日沖（短線當沖/隔日倒貨）」 vs 「長線（官股/外資留倉）」。

本期先用人工種子名單（關鍵字子字串比對）；待 broker_branches 累積足夠歷史，
可改以行為統計（各分點「買進後隔日倒貨率」）精進，故 classify() 介面保持穩定。

回傳：
  "daytrade" 隔日沖傾向分點（大買常於次日倒貨 -> 對次日偏空）
  "longterm" 長線/官股/外資分點（留倉 -> 對次日偏多）
  "other"    未分類（中性，不貢獻方向）
"""
from __future__ import annotations

# 隔日沖熱門分點（台股常見短線/隔日沖聚集地，子字串比對）。可隨觀察增修。
DAYTRADE_BRANCHES = (
    "凱基-台北", "富邦-建國", "元大-土城", "群益金鼎-大安", "元大-館前",
    "凱基-松山", "凱基-信義", "台新-吉利", "國票-敦化", "元富-自由",
    "永豐金-內湖", "統一-新店", "兆豐-松德", "元大-桃興", "康和",
)

# 長線/官股/外資/造市分點（傾向留倉）。子字串比對。
LONGTERM_BRANCHES = (
    "摩根", "美林", "高盛", "瑞銀", "港商", "野村", "花旗", "美商", "新加坡商",
    "台灣銀行", "兆豐銀", "合作金庫", "第一金", "華南銀", "彰銀", "台企銀",
    "法銀", "德意志", "麥格理", "里昂", "大和", "星展",
)


def classify(branch: str | None) -> str:
    """以子字串比對分類分點。先比隔日沖具名分點，再比長線關鍵字。"""
    b = (branch or "").strip()
    if not b:
        return "other"
    for kw in DAYTRADE_BRANCHES:
        if kw and kw in b:
            return "daytrade"
    for kw in LONGTERM_BRANCHES:
        if kw and kw in b:
            return "longterm"
    return "other"
