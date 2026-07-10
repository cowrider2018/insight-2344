"""EXP-011 DRAM 現貨價 — Stage 2 資料可得性探針（非 production）

假設（若資料可得才進 Stage 3）：DRAM 現貨價趨勢＝華邦基本面驅動 → 中期方向訊號。
Stage 2 需同時滿足：免費免金鑰、欄位可解析、歷史可回補 ≥200 交易日。
已知：DRAMeXchange/TrendForce 正式數據為付費；本探針驗證免費頁面可得性與歷史深度。

用法：
  python research/exp_011_dram_spot_probe.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

import config  # noqa: E402

TARGETS = [
    ("DRAMeXchange 首頁（現貨表）", "https://www.dramexchange.com/"),
    ("TrendForce 現貨頁", "https://www.trendforce.com/price/dram"),
]


def probe_one(name: str, url: str) -> bool:
    try:
        r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=20)
    except requests.RequestException as e:
        print(f"[probe] {name}: 連線失敗 {type(e).__name__}")
        return False
    print(f"[probe] {name}: HTTP {r.status_code}  len={len(r.text)}")
    if r.status_code != 200:
        return False
    # 找現貨價樣式（DDR4/DDR5 + 數字），確認是否在靜態 HTML 內
    hits = re.findall(r"(DDR[45][^<]{0,40}?\d+\.\d{2,3})", r.text)[:5]
    print(f"        價格樣式命中 {len(hits)} 筆：{hits[:3]}")
    return bool(hits)


if __name__ == "__main__":
    ok = [probe_one(n, u) for n, u in TARGETS]
    print("\n[結論] 即時值可得" if any(ok) else "\n[結論] 免費靜態頁無現貨價（JS/付費牆）")
    print("[結論] 歷史回補 ≥200 交易日：兩來源皆無免費歷史 API → Stage 2 不可能滿足")
    sys.exit(0 if any(ok) else 1)
