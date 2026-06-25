# 2344 權重調適流程（Tuning Playbook）

> 這份文件讓**任何一個新對話的 Claude** 都能正確地對 2344 六面模型做「調適」（重新優化權重與評分參數）。
> 使用者只要說「**依 TUNING.md 對 2344 重跑調適**」「**重新優化權重**」「**tune / 調適**」，就照本文件執行。

專案根目錄：`c:\Users\johnyou\Desktop\make-money`（以下指令在此根目錄下執行）。

---

## 0. 一句話總結
八面（技術 / 籌碼 / 消息 / 基本 / 美光 / 費半 / **日內 1 分 K** / **主力分點**）各自評分 → 加權合成方向 → 與當日實際漲跌（中性帶 ±1%）比對 →
**walk-forward 回測**網格搜尋權重、**座標上升**校準評分參數、**獨立驗證**消息型態極性 →
產出**校準後**的權重 `data/weights.json`，供每日技能 `/cmoney-2344-daily` 使用。

> **第八面（主力分點）**：由前一日(D-1)富邦 DJ 主力進出頁取券商分點買賣超，算主力淨額/集中度/**行為式聰明錢(smart)**/隔日沖名單/長線名單子訊號。
> DJ 頁支援日期參數（`?a=2344&e=DATE&f=DATE`）→ 可**回補近半年以上歷史**（`--backfill-branches-history`），回測即有完整覆蓋；之後每日續抓最新一日累積。
> **行為式分類 v2（walk-forward，取代 v1 全窗 polarity）**：`branch_model.py` 對每個交易日 d **只用 < d 歷史**估每券商 edge（淨買方向與次日同向率），經 **empirical-Bayes 收縮**成加權分數 wedge（小樣本→0），再**跨券商聚合**成日訊號寫入 `branch_wf` 表；smart 子訊號取此分數。**無 look-ahead by construction**，OOS 不需特別覆寫。每券商行為檔（多 horizon 命中率/wedge/跟單反指標標記）見 `data/branch_profiles.json`。
> v1（`validate_branches.py` 全窗二項極性）已被取代——曾證實過擬合（in-sample 63.86% 但 OOS 54%、526 分點多重檢定噪音）。

> **第七面（日內 1 分 K）**：由前一日(D-1)的 1 分 K 萃取盤中走勢細節（VWAP 偏離 / 尾盤動能 / 日內趨勢 / 收盤位置 / 量能分布）。
> Fugle 1 分 K 只保留近期交易日（實測約百餘交易日，依方案而定），**更早歷史無法回補**，故冷啟動覆蓋率受 API 保留期限制，須靠每日 `build_dataset` 累積、跨越保留期成長（同消息面）。
> **公平機制**：`scoring.combine()` 改為**覆蓋率正規化**（只對「當天有資料的面」加權並重新正規化，不同覆蓋率的日子 composite 尺度一致）；並以**顯著性護欄**（二項檢定 vs 0.5）在第七面小樣本不顯著時把其權重強制歸 0，避免運氣虛灌命中率。最終 240 筆仍一起比較。

目標是**樣本外**命中率，現實天花板約 **55–62%**；看到 ≥80% 先當成 bug（look-ahead / 過擬合）去抓，不要當成功。

---

## 1. 系統組成（冷啟動先讀懂這些）

| 檔案 | 角色 |
|---|---|
| `src/timeline_db.py` | SQLite 時間軸 DB `data/market.db`：`news/chips/revenue/candles/us_market/candles_1min/broker_branches` 七表 + 查詢（`*_asof` 皆 `< D`，無 look-ahead） |
| `src/fetch_dj_chips.py` | 富邦 DJ 主力進出（券商分點買賣超）抓取與解析（支援日期參數回補歷史）；`src/broker_tags.py` 隔日沖/長線種子名單 |
| `src/branch_model.py` | **主力分點 v2（walk-forward）**：逐日 expanding-window 收縮 edge + 跨券商聚合 → `branch_wf` 表 + `data/branch_profiles.json`（每券商加權分數/客製行為檔）。`validate_branches.py` 為 v1（全窗，已被取代） |
| `src/ingest.py` | 攝取每日 dataset + 回補（`--backfill-json/-candles/-us/-chips`，chips 省略日期=自動全區間） |
| `src/fetch_*.py` | `fetch_fugle`（K/技術/基本）、`fetch_twse`（籌碼/月營收）、`scrape_cmoney`（消息）、`fetch_us`（美光MU/費半SOX，Yahoo；美光另以 CNBC 補盤後） |
| `src/scoring.py` | 六面確定性評分（純函式）。門檻/尺度集中於 `PARAMS`，可由 `data/score_params.json` 覆寫；`technical_signals()` 回子訊號 breakdown |
| `src/backtest.py` | `extract_features`（抽 as-of 原始輸入，只算一次）→ `score_samples`（套參數評分）→ `optimize`（權重網格）→ `pick_balanced` + `signal_diagnostics` |
| `src/calibrate.py` | 座標上升校準 `PARAMS`（粗網格加速），收斂後完整網格重算，寫 `score_params.json` + `weights.json` + 報告 |
| `src/news_patterns.py` | 11 種消息型態（AND-of-OR 關鍵字）+ 已驗證極性載入 |
| `src/validate_news.py` | **逐型態獨立**驗證次日效應（二項檢定），只有顯著者才給極性，寫 `data/news_patterns.json` |

**產出檔（都在 `data/` 與 `reports/`，已被 .gitignore）**
- `data/weights.json`：實際採用的**平衡權重** + `neutral_threshold` + `raw_best`（純最高命中率對照）+ coverage。
- `data/score_params.json`：校準後的評分參數。
- `data/news_patterns.json`：各消息型態的已驗證極性（`polarity`/`edge`）。
- `reports/backtest_<start>_<end>.md`：含最佳/平衡權重、**判斷提示指標**（逐指標方向命中率）、單面基準、排行、限制。
- `reports/news_patterns.md`：消息型態驗證表。

---

## 2. 調適前置：確認資料齊備
```powershell
python src\timeline_db.py            # 印各表筆數
```
期望：`candles` ≈ 240、`chips` ≈ 230+、`us_market` 兩個代號各 ≈ 250、`candles_1min` 約百餘交易日（受 API 保留期限制，每日累積成長）、`broker_branches` 每日 +30 筆（DJ 僅最新一日，靠累積）、`news` 隨每日累積。缺則先回補：
```powershell
python src\ingest.py --backfill-candles      # Fugle 整年日K（需 .env 的 FUGLE_MARKETDATA_API_KEY）
python src\ingest.py --backfill-intraday     # Fugle 近期 1 分 K（第七面冷啟動；自動探知保留期，更早無法回補）
python src\ingest.py --backfill-branches              # 富邦 DJ 主力分點最新一日（每日累積）
python src\ingest.py --backfill-branches-history      # 以日期參數回補 DJ 分點歷史（約近半年，第八面回測即有覆蓋）
python src\ingest.py --backfill-us           # 美光/費半（Yahoo，免金鑰）
python src\ingest.py --backfill-chips        # 歷史籌碼（TWSE 逐日，較慢；省略日期=自動全區間）
python src\ingest.py --backfill-json         # 自既有 data\2344_*.json 快照灌消息等
```

---

## 3. 完整調適流程（核心，依序執行）

### 步驟 A — 校準評分參數 + 重算平衡權重（主指令）
```powershell
python src\calibrate.py --start 2025-07-01 --end 2026-06-23 --rounds 2
```
- 座標上升調 `score_params.json` 內門檻/尺度（候選值見 `calibrate.py` 的 `CANDIDATES`）。
- 收斂後以**完整權重網格** + **平衡選擇**重算，寫 `weights.json` 與回測報告。
- 終端會印：起始→收斂命中率軌跡、純最佳 vs 平衡(採用) 權重。

> 只想用目前參數重算權重、不重新校參：`python src\backtest.py --start ... --end ...`

### 步驟 B — 獨立驗證消息型態極性
```powershell
python src\validate_news.py --start 2025-07-01 --end 2026-06-23
```
- 每個型態各自統計「出現該型態的隔夜窗 → 當日漲跌」，與基準比 + 二項檢定。
- **只有樣本足夠（非中性 ≥8）且顯著（p<0.15）才給極性**；反直覺型態（如券商調升後賣出）會以負 edge 自動偏空。未驗證=中性，避免誤判。
- 註：目前歷史消息覆蓋很低（多數型態 n 不足），多會維持中性；隨每日快照累積後重跑才會逐一「轉正」。

### 步驟 C — 樣本外驗證（**必做的誠實檢查**）
只在 train 上校參數+選權重，套到 held-out test，確認沒過擬合：
```powershell
python src\oos_check.py --start 2025-07-01 --end 2026-06-23
```
**判讀**：OOS 命中率須**明顯高於 test 偏多基準**（約 47%），且不應遠低於 in-sample；腳本末會印 `[OK]/[WARN]/[FAIL]` 判定。OOS≈in-sample 或更高 = 沒過擬合（理想）。

---

## 4. 判讀回測報告 `reports/backtest_*.md`

1. **基準線**：`基準(always 偏多)命中率`。模型要顯著高於此才有意義。
2. **採用方案**：命中率第一優先——採最高命中率方案（`balance_tol` 預設 0），多個並列最佳時才取較均衡者；`raw_best` 為對照。
3. **判斷提示指標表**（逐指標方向命中率）：
   - **<50% = 反指標**（例：曾測出「乖離 40.6%」在趨勢盤是反指標）→ 應降權、反向、或移除。
   - 高且 active 多 = 可靠（例：美光/費半 ~62%、RSI/均線 ~58%）。
   - `量價` active=0 = 訊號幾乎不觸發 → 邏輯需重寫或無用。
4. **涵蓋率**：某面 coverage 相對 n_days 偏低（尤其消息），其權重信心不足，報告與技能都會標註。

---

## 5. 調適決策邏輯（看完診斷後，要改什麼）

- **某子訊號 <50%（反指標）**：在 `calibrate.py` 的 `CANDIDATES` 加入其權重候選含 0 與低值（如 `"w_bias":[0.0,0.05,0.1]`），讓校準自動降權；或在 `scoring.technical_signals` 反向其符號。
- **某面 coverage 太低**：不要硬給權重；先補資料（消息只能等每日累積）。
- **新增可調參數**：在 `scoring.DEFAULT_PARAMS` 加預設值並於評分函式使用 → 在 `calibrate.CANDIDATES` 加候選清單。
- **新增消息型態**：在 `news_patterns.PATTERNS` 加「群組 AND-of-OR」定義，跑 `validate_news.py`；**極性由驗證決定，勿手寫直覺多空**。
- **新增第 7、8 特徵**：仿美光/費半——`fetch_*` 抓 → timeline_db 加表/`*_asof` 查詢（須 `< D`）→ `scoring.DIMENSIONS` 加維度 + `score_*` → `backtest.extract_features/score_samples` 接入。權重網格與報告會自動泛化到 N 維。

---

## 6. 鐵則（不可違反，否則數字是騙自己的）

- **無 look-ahead**：技術只用 D-1 收盤、籌碼 `data_date < D`、消息 `published_at < D 09:00`、美股 `date < D`、日內 1 分 K `date < D`（取 D-1 整日盤中）；`backtest.extract_features` 內以 `assert` 強制，**不可拿掉**。
- **第七面公平**：`combine()` 用覆蓋率正規化、護欄令小樣本不顯著時 intraday 權重=0；冷啟動覆蓋率約 30 日，**頭條 240 命中率不應因加入第七面而異常跳升**（>80% 視為 look-ahead/過擬合）。
- **不要只報 in-sample 最佳**：一律附步驟 C 的樣本外結果。
- **命中率第一優先**：`weights.json` 採最高命中率方案（`balance_tol` 預設 0），僅在多個並列最高命中率時取較均衡者作次選排序；`raw_best` 為對照。若要刻意以少量命中率換穩健，傳 `--balance-tol 0.02`。
- **消息極性只信驗證**：未驗證型態一律中性。
- **命中率 ≠ 報酬率**：本系統不含成本/滑價，只用於權重相對比較與方向研判。
- **可達天花板 ~55–62%**：追 90% 必是 look-ahead 或過擬合。

---

## 7. 一鍵對應（雙擊 .bat）
- `setup.bat`：建環境 + DB 初始化 + 回補 json/candles/us
- `backfill_history.bat`：回補歷史籌碼 → 回測 → 驗證消息型態（= 步驟 A 的回測部分 + 步驟 B）
- `calibrate.bat`：校準參數 + 重算平衡權重（= 步驟 A）
- `run_once.bat` / `schedule_create.bat` / `schedule_delete.bat`：每日流程的立即執行 / 建立 / 刪除排程

---

## 8. 參考基準（最近一次調適結果，供下次比較是否退步）
- 區間 2025-07-01 ~ 2026-06-23、238 交易日、中性帶 ±1%、偏多基準 ≈47%。
- 校準把可達命中率自 57.1% 提升至 **59.7%**（粗網格；調整 `us_scale 3→4、kd_div 8→12、rsi_div 30→20`）。
- 完整網格：純最佳 **59.66%**（技術0.4/美光0.4/費半0.2）、平衡採用 **57.98%**（技術0.3/消息0.1/基本0.1/美光0.3/費半0.2）。
- 樣本外（train 校準、test 驗證）：曾達 **OOS 61.1%**（基準 47.2%），OOS≥in-sample → 未過擬合。
- 強訊號：美光/費半 ~62%、RSI ~59%、均線 ~58%；反指標：乖離 ~41%；消息/基本樣本過少暫不可信。

> 下次調適後若 OOS 明顯低於上列、或平衡命中率掉破基準，視為退步，需回頭檢查資料缺漏或參數/型態改動。

---

本流程為公開資訊回測與研判，非投資建議，據此操作風險自負。
