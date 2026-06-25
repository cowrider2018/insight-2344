# 2344 權重調適流程（Tuning Playbook）

> 這份文件讓**任何一個新對話的 Claude** 都能正確地對 2344 六面模型做「調適」（重新優化權重與評分參數）。
> 使用者只要說「**依 TUNING.md 對 2344 重跑調適**」「**重新優化權重**」「**tune / 調適**」，就照本文件執行。

專案根目錄：`c:\Users\johnyou\Desktop\make-money`（以下指令在此根目錄下執行）。

---

## 0. 一句話總結
六面（技術 / 籌碼 / 消息 / 基本 / 美光 / 費半）各自評分 → 加權合成方向 → 與當日實際漲跌（中性帶 ±1%）比對 →
**walk-forward 回測**網格搜尋權重、**座標上升**校準評分參數、**獨立驗證**消息型態極性 →
產出**平衡且校準後**的權重 `data/weights.json`，供每日技能 `/cmoney-2344-daily` 使用。

目標是**樣本外**命中率，現實天花板約 **55–62%**；看到 ≥80% 先當成 bug（look-ahead / 過擬合）去抓，不要當成功。

---

## 1. 系統組成（冷啟動先讀懂這些）

| 檔案 | 角色 |
|---|---|
| `src/timeline_db.py` | SQLite 時間軸 DB `data/market.db`：`news/chips/revenue/candles/us_market` 五表 + 查詢（`*_asof` 皆 `< D`，無 look-ahead） |
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
期望：`candles` ≈ 240、`chips` ≈ 230+、`us_market` 兩個代號各 ≈ 250、`news` 隨每日累積。缺則先回補：
```powershell
python src\ingest.py --backfill-candles      # Fugle 整年日K（需 .env 的 FUGLE_MARKETDATA_API_KEY）
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
2. **平衡 vs 純最佳**：採用「命中率距最佳 ≤2pp 內最均衡」者（避免退化壓在 2-3 面）。
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

- **無 look-ahead**：技術只用 D-1 收盤、籌碼 `data_date < D`、消息 `published_at < D 09:00`、美股 `date < D`；`backtest.extract_features` 內以 `assert` 強制，**不可拿掉**。
- **不要只報 in-sample 最佳**：一律附步驟 C 的樣本外結果。
- **平衡優先於退化**：`weights.json` 採平衡權重；純最高命中率只放 `raw_best` 對照。
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
