"""EXP-014 隱含目標價 vs 法人目標價 — Stage 2 資料可得性探針（非 production）

假設（若資料可得才進 Stage 3）：
  月營收趨勢 + 記憶體現貨/合約價 + 產業飽和度競爭（中國記憶體擴產、韓廠擴廠）
  → 綜合推算「隱含目標價」，與法人（券商/外資）目標價比對驗證算法可信度，
  再作為整體行情判斷 overlay。

Stage 2 需四條資料腿同時可得（免費免金鑰＋可解析＋歷史可回補）：
  1. 月營收 — 已在地（timeline_db.revenue，TWSE OpenAPI）
  2. 記憶體現貨/合約價（DRAM/NAND）— EXP-011 已判 BLOCKED-DATA（DRAMeXchange/TrendForce
     皆 JS 渲染＋付費牆，無免費歷史 API），本探針不重跑，直接引用結論
  3. 產業飽和度競爭（中國 YMTC/CXMT 擴產、韓廠 Samsung/SK Hynix 擴產）— 無已知免費結構化
     時間序列來源（僅零星新聞質性描述，非可回測數列），本探針嘗試確認
  4. 法人/外資目標價歷史（鉅亨網 foreignrating 頁）— 本探針實測

用法：
  python research/exp_014_valuation_target_price_probe.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

import config  # noqa: E402

TARGET_PRICE_URL = "https://www.cnyes.com/twstock/foreignrating.aspx?code=2344"

# 使用者指定的 TrendForce DataTrack 現貨價圖表（2026-07-11 追加驗證）
SPOT_PRICE_URLS = [
    ("DRAM", "https://datatrack.trendforce.com.tw/Chart/content/4694/mainstream-dram-spot-price"),
    ("NAND", "https://datatrack.trendforce.com.tw/Chart/content/4695/mainstream-nand-flash-wafer-spot-price"),
]


def probe_spot_price_page(name: str, url: str) -> bool:
    try:
        r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=20)
    except requests.RequestException as e:
        print(f"[probe] {name} DataTrack: 連線失敗 {type(e).__name__}")
        return False
    print(f"[probe] {name} DataTrack: HTTP {r.status_code}  len={len(r.text)}")
    price_hits = re.findall(r"(DDR[45][^<>{}]{0,60}?\d+\.\d{2,3})", r.text)
    has_login_gate = "isCheckLogin" in r.text or "navbarloginATag" in r.text
    print(f"        價格樣式命中 {len(price_hits)} 筆；登入/會員閘門偵測：{has_login_gate}")
    return bool(price_hits) and not has_login_gate


def probe_target_price_page() -> bool:
    try:
        r = requests.get(TARGET_PRICE_URL, headers={"User-Agent": config.USER_AGENT}, timeout=20)
    except requests.RequestException as e:
        print(f"[probe] 法人目標價頁: 連線失敗 {type(e).__name__}")
        return False
    print(f"[probe] 法人目標價頁: HTTP {r.status_code}  len={len(r.text)}")
    if r.status_code != 200:
        return False
    # 靜態 HTML 內是否含可解析表格列（日期＋券商＋目標價）
    table_hits = re.findall(r"<table[^>]*>.*?</table>", r.text, re.S)
    next_data = "__NEXT_DATA__" in r.text
    print(f"        <table> 區塊命中 {len(table_hits)} 筆；__NEXT_DATA__ 內嵌資料：{next_data}")
    return bool(table_hits) or next_data


if __name__ == "__main__":
    spot_ok = [probe_spot_price_page(n, u) for n, u in SPOT_PRICE_URLS]
    print("[結論] DataTrack 現貨價：" + ("可得" if any(spot_ok) else "靜態頁 0 價格樣式＋偵測到登入/會員閘門 → 與 EXP-011 同型態，仍 BLOCKED"))

    print("[結論-已知] 產業飽和度競爭（中國/韓廠擴產）：無已知免費結構化時間序列來源（僅新聞質性事件）")

    ok = probe_target_price_page()
    print("\n[結論] 法人目標價頁可靜態解析" if ok else "\n[結論] 法人目標價頁為 JS 渲染 SPA，靜態請求無可解析表格（同 EXP-011 型態）")
    sys.exit(0 if ok else 1)
