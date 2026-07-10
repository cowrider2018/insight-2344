# EXP-011 DRAM 現貨價

- 日期／狀態：2026-07-11 ｜ **BLOCKED-DATA**
- 假設（未進 Stage 3）：DRAM 現貨價趨勢＝華邦基本面驅動 → 中期方向訊號。
- 資料：DRAMeXchange 首頁與 TrendForce 現貨頁探針——HTTP 200 但靜態 HTML 無任何價格樣式（JS 渲染＋付費牆）；正式數據（DRAMeXchange/TrendForce）為付費訂閱
- 方法：Stage 2 探針（免費可得性＋歷史深度）

## 數字

- 兩來源價格樣式命中 0 筆（頁面 190KB 全為前端框架）
- 歷史回補 ≥200 交易日：**無任何免費歷史 API**（即使即時值可爬，回測仍不可能）

## 判定與理由

BLOCKED-DATA（Stage 2 雙重不滿足：即時值不可免費取得、歷史深度為零）。依鐵則③（需付費/新金鑰 → 直接結案），不進 Stage 3。

## 產出物

- research/exp_011_dram_spot_probe.py ｜ commit 見本檔 commit

## 重啟條件

- 出現免費且含 ≥200 日歷史的 DRAM 現貨價來源（如公開 API 或可穩定解析的日更頁面）時重啟。
