# EXP-014 隱含目標價 vs 法人目標價 驗證

- 日期／狀態：2026-07-11 ｜ **BLOCKED-DATA**
- 假設：月營收趨勢＋記憶體現貨/合約價＋產業飽和度競爭（中國記憶體 CXMT/YMTC 擴產、韓廠 Samsung/SK Hynix 擴廠）綜合推算「隱含目標價」與達成可期望度，與法人目標價比對驗證算法可信度，作為整體行情判斷 overlay。
- 資料：四條必要腿逐一探針（見下）
- 方法：Stage 2 資料可得性探針（`research/exp_014_valuation_target_price_probe.py`）

## 數字（四條腿逐一檢驗）

| 腿 | 來源 | 結果 |
|---|---|---|
| 月營收趨勢 | `timeline_db.revenue`（TWSE OpenAPI） | ✅ already-local；但已是既有 `score_fundamental` 輸入，現行權重護欄已歸 0（基準快照） |
| 記憶體現貨/合約價（DRAM/NAND） | DRAMeXchange／TrendForce | ❌ 沿用 [EXP-011](EXP-011-dram-spot.md) 結論；追加實測使用者指定之 `datatrack.trendforce.com.tw` DRAM(4694)/NAND(4695) 圖表頁：靜態 HTML 0 筆價格樣式、頁面為 Vue+Plotly 動態渲染，且偵測到 `isCheckLogin` cookie 機制＋登入/註冊連結（會員閘門）；需帳號登入才可能繼續探查，經使用者確認維持 BLOCKED-DATA |
| 產業飽和度競爭（中國擴產/韓廠擴廠） | SEMI／TrendForce／DIGITIMES Research 產能報告；備選方案「消息面認定＋可信度轉化強度」 | ❌ 結構化數列無免費來源（WebSearch 確認僅零星質性報導，如 CXMT 月產能目標、新廠時程）。備選的新聞質性方案亦查證受阻：本地 `news` 表僅保留 ~2.5 週歷史（169 筆，2026-06-24~07-10），遠低於回測所需 490 個交易日（技術面資料範圍）；且此類重大產能消息年度發生頻率極低、分散不定期，未來即使補齊歷史語料，能通過關卡②（active≥8）樣本數也需長期累積。經使用者確認判 BLOCKED-DATA／DEFERRED |
| 法人/外資目標價歷史 | 鉅亨網 `foreignrating.aspx?code=2344` | ✅ 探針證實：靜態 HTML `<table>` 可解析，欄位含日期／券商(Factset)／評等升降／財測EPS／目標價／現價，回溯至 2020 年（多年歷史），免金鑰 |

## 判定與理由

四條必要資料腿中 2 條（記憶體現貨/合約價、產業飽和度競爭）為假設機制的核心驅動因子，且皆無免費可回測來源（前者沿用 EXP-011 既有結論；後者本輪新探針確認僅有不定期新聞質性報導，無結構化數列）。依鐵則③「需付費/新金鑰 → 直接結案」，即使另外 2 條腿（月營收、法人目標價歷史）確認可免費取得，整體「隱含目標價」模型仍無法建構，不進 Stage 3。

附帶發現：月營收已是現有 `score_fundamental` 輸入且已被權重護欄歸零；法人目標價的「調升/調降事件」方向性反應已由 `news_patterns.py` 的 `broker_target_up`（反向：利多出盡）覆蓋，故即使日後補齊 2 條缺口腿重啟本實驗，仍須留意與既有維度的訊息重疊（比照 EXP-003 usdtwd 整合稀釋的前例）。

## 產出物

- research/exp_014_valuation_target_price_probe.py ｜ commit 見本檔 commit

## 重啟條件

出現免費且可回補 ≥200 交易日歷史的（1）DRAM/NAND 現貨或合約價來源（如使用者持有 TrendForce DataTrack 帳號且確認為免費非付費層級、頁面登入後仍可回補足夠歷史），**且**（2）中國/韓廠記憶體產能或稼動率結構化時間序列來源，**或**（2'）本地 `news` 表歷史語料累積 ≥1 年（供「消息面可信度→強度」質性方案回測驗證），兩者（或 1＋2'）皆備才重啟；屆時需額外檢查與既有 fundamental／news broker_target_up 維度的訊息重疊度。
