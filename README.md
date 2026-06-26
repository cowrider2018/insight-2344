# 2344 華邦電 每日盤前走勢分析

每天上午 06:00 自動抓取**十面**標準化資料（技術 / 基本 / 籌碼 / 消息 / 美光 / 費半 / 日內 1 分 K / 主力分點 / TDCC 千張大戶 / 外資台指期未平倉），由 Claude 依回測最佳權重做當日盤前走勢研判，產出報告並 email。

## 分工
- **標準化抓取（決定性腳本）**：`src/build_dataset.py` → `data/2344_YYYYMMDD.json`
- **時間軸累積**：`src/timeline_db.py`（SQLite `data/market.db`）+ `src/ingest.py`，`build_dataset.py` 每日自動攝取
- **回測 / 權重優化**：`src/scoring.py` + `src/backtest.py` → `data/weights.json`（六面權重 + 中性門檻）
- **參數校準**：`src/calibrate.py` → `data/score_params.json`；**消息型態驗證**：`src/validate_news.py` → `data/news_patterns.json`
- **分析（Claude）**：技能 `/cmoney-2344-daily` 讀 JSON + weights → `reports/2344_YYYYMMDD.md`
- **交付**：`src/send_email.py` 寄報告給 `MAIL_TO`
- **排程**：`src/run_daily.ps1`（依序跑抓取→分析→寄信）由 Windows 工作排程器於 06:00 觸發

## 資料來源（十面）
| 面向 | 來源 | 單位/備註 |
|---|---|---|
| 技術面 | Fugle Marketdata API（historical/candles → 本地算 MA/KD/RSI/MACD/乖離/量能） | 需 API 金鑰 |
| 基本面 | Fugle stats（PE/PB/殖利率/52週）＋ TWSE 月營收 t187ap05_L | 營收單位：仟元 |
| 籌碼面 | TWSE RWD：fund/T86（三大法人）、marginTrading/MI_MARGN（融資融券） | 單位：張；免金鑰 |
| 消息面 | CMoney 論壇/新聞內部 JSON API（Playwright 攔截） | 發布時間取自 createTime（精確毫秒），嚴格確認 |
| 美光 (MU) | Yahoo Finance chart API | 隔夜收盤漲跌%；免金鑰 |
| 費半 (SOX) | Yahoo Finance chart API（^SOX） | 隔夜收盤漲跌%；免金鑰 |
| 日內 1 分 K | Fugle Marketdata API（intraday/candles，前一日盤中） | 僅保留近期，須每日累積 |
| 主力分點 | 富邦 DJ 個股主力進出（券商分點買賣超） | 單位：張；僅最新一日，須每日累積 |
| TDCC 千張大戶 | 集保戶股權分散表：OpenData CSV（當週）＋ smart.tdcc 查詢（歷史逐週） | 週頻、占比%；公布有 lag |
| 外資台指期 | TAIFEX 三大法人－區分各期貨契約 CSV（臺股期貨 TXF） | 外資多空未平倉口數淨額；盤後公布、免金鑰 |

> 記憶體族群與美光/費半高度正相關，隔夜美股常主導 2344 當日方向，為重要外生特徵。

## 設定 `.env`（複製 `.env.example`）
```
FUGLE_MARKETDATA_API_KEY=<你的富果金鑰>
MAIL_TO=<你的 Email>
```
寄信採 Gmail API + OAuth：需 `credentials.json`（Google Cloud Console 下載）。`setup.bat` 最後一步會開瀏覽器要求登入授權，完成後自動產生 `token.json`，之後排程即可無頭寄信。

> 為何要在 setup 授權：若沒有 `token.json`，每日排程的寄信步驟會落入互動式 OAuth 流程、開瀏覽器等人授權，在無人值守的 06:00 排程會卡住不動。先在 setup 完成一次授權即可避免。

---

# 操作方式

每個流程都提供「**一鍵 .bat**」與「**等價手動指令**」兩種方式，**擇一即可**。先設好 `.env`（與 Gmail 憑證）再開始。下面兩節為同一套流程的兩種入口，步驟編號互相對應。

## 路徑 A — 一鍵操作（.bat，雙擊即可）

依生命週期順序：

| 步驟 | 檔案 | 用途 | 等價手動（見路徑 B） |
|---|---|---|---|
| 1 | `setup.bat` | **建置環境**：建立 `.venv` + 安裝套件 + Playwright + 初始化時間軸 DB + 回補消息/K線/美股 + Gmail 授權（開瀏覽器登入一次） | B-1 |
| 2（選用） | `backfill_history.bat` | 回補歷史籌碼 → 回測 → 消息型態驗證 → 樣本外複核（產生 `data\weights.json`、`data\news_patterns.json`） | B-2 |
| 2'（選用） | `calibrate.bat` | 校準評分參數 + 重算平衡權重 → 樣本外複核（產生 `data\score_params.json`、`data\weights.json`） | B-2' |
| 3 | `run_once.bat` | **立即執行一次**每日流程（抓資料 → Claude 分析 → 寄信） | B-3 |
| 4 | `schedule_create.bat` | **建立**每日 06:00 排程 | B-4 |
| — | `schedule_delete.bat` | **刪除**排程 | B-4 |

> 排程相關 .bat 若提示權限不足，請以系統管理員身分執行。`backfill_history.bat` / `calibrate.bat` 未帶參數時，預設視窗為 `--start 2025-07-01`（與 TUNING.md 基準一致），可自行覆寫，例如 `calibrate.bat --start 2025-07-01 --end 2026-06-23 --rounds 2`。

## 路徑 B — 手動指令（PowerShell，等價於各 .bat）

### B-1 建置環境（＝ `setup.bat`）
```powershell
python -m venv .venv                                 # 建立虛擬環境（套件不裝到全域 / WindowsApps）
.\.venv\Scripts\Activate.ps1                         # 啟用 .venv（之後 B-2 / B-2' / B-3 指令都在此環境下執行）
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python src\timeline_db.py --init
python src\ingest.py --backfill-json
python src\ingest.py --backfill-candles
python src\ingest.py --backfill-us
python -c "import sys; sys.path.insert(0,'src'); import send_email; send_email._gmail_service()"  # Gmail 授權，開瀏覽器登入一次 -> token.json
```
> 所有腳本都以專案 `.venv` 執行：`setup.bat` / `backfill_history.bat` / `calibrate.bat` 會自動使用 `.venv\Scripts\python.exe`，`src\run_daily.ps1`（排程）亦優先採用 `.venv`，找不到才退回 PATH。手動執行請先 `Activate.ps1` 啟用。

### B-2 回補歷史 + 重算權重（＝ `backfill_history.bat`，以「當前已校準參數」重算）
```powershell
python src\ingest.py --backfill-chips                            # 回補歷史籌碼（逐日抓 TWSE 較慢；省略日期=自動取全區間）
python src\ingest.py --backfill-tdcc                             # 第九面 TDCC 千張大戶（smart.tdcc 逐週，約近一年）
python src\ingest.py --backfill-futures                          # 第十面 外資台指期未平倉（TAIFEX 一次取整段）
python src\backtest.py --start 2025-07-01 [--end 2026-06-23]     # 回測 -> data\weights.json + reports\backtest_*.md
python src\validate_news.py --start 2025-07-01                   # 驗證消息型態極性 -> data\news_patterns.json
python src\oos_check.py --start 2025-07-01                       # 樣本外複核（OOS 應 >= baseline ~47%）
```

### B-2' 逐步校準評分參數（＝ `calibrate.bat`，會「改寫」參數再重算）
```powershell
python src\calibrate.py --start 2025-07-01 --rounds 2   # 座標上升校準 score_params.json，再全網格 -> weights.json（平衡+校準）
python src\oos_check.py  --start 2025-07-01 --rounds 2  # 樣本外複核
```
> B-2 與 B-2' 的差別：B-2 **沿用**現有 `score_params.json` 只重算權重；B-2' **重新校準**評分參數本身。完整調適流程與判讀準則見 **[TUNING.md](TUNING.md)**。

### B-3 執行一次每日流程（＝ `run_once.bat`）
逐步驗證每個環節：
```powershell
python src\build_dataset.py                          # 產生當日標準化 JSON（並自動攝取進 market.db）
# 在 Claude Code 內：/cmoney-2344-daily             # 讀 JSON + weights -> reports\2344_YYYYMMDD.md
python src\send_email.py reports\2344_YYYYMMDD.md    # 寄信測試
```
或直接跑排程用的包裝腳本（與 `run_once.bat` 完全相同）：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File src\run_daily.ps1
```

### B-4 每日 06:00 排程（＝ `schedule_create.bat` / `schedule_delete.bat`）
```powershell
schtasks /Create /TN "CMoney_2344_Daily" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\johnyou\Desktop\make-money\src\run_daily.ps1" /SC DAILY /ST 06:00 /F
schtasks /Run    /TN "CMoney_2344_Daily"      # 立即觸發一次測試（或執行 run_once.bat）
schtasks /Delete /TN "CMoney_2344_Daily" /F   # 刪除（或執行 schedule_delete.bat）
```
排程需求：`claude` CLI 已安裝並登入（供 `run_daily.ps1` 無頭呼叫）。執行日誌見 `logs/`。

---

# 策略說明：回測與權重優化（動態調整六面權重）

把六面研判編碼為確定性評分（`src/scoring.py`），以「每個交易日為資訊截止斷點、**不偷看當日之後**」做 walk-forward 回測，網格搜尋命中率最高的六面權重，寫入 `data/weights.json` 供每日技能讀取。時間軸 DB 讓歷史斷點可低成本查詢、免重複爬取。對應的執行指令見路徑 A 步驟 2/2' 或路徑 B 的 B-2/B-2'。

- **預測目標**：`sign(當日收盤 − 前日收盤)`，實際漲跌**中性帶 ±1%**（三類：偏多/中性/偏空）。
- **無 look-ahead**：技術只用 D-1 收盤、籌碼 `data_date < D`、消息 `< D 09:00`、美股 `date < D`，皆於回測中以 assert 強制。
- **平衡權重**：`weights.json` 採「命中率距最佳 ≤2pp 內最均衡」的方案（避免退化壓在 2-3 面），`raw_best` 另存純最高命中率方案。
- **判斷提示指標**：報告含逐指標（六面 + 技術子訊號）方向命中率，看哪些指標有預測力、哪些是雜訊。
- **逐步校準**：`calibrate.py` 對 `score_params.json` 內門檻/尺度做座標上升調適（規則為手寫啟發式，非訓練模型，需校準）。
- **消息型態獨立驗證**：`validate_news.py` 對每個消息型態（券商調升目標價、記憶體漲價、外資買賣超…）**獨立以歷史驗證**次日效應，只有樣本足夠且顯著者才賦予極性。**反直覺型態**（如券商調升後常「利多出盡」賣出）一旦驗證為負 edge，會自動以偏空計分；未驗證者中性，避免誤判。型態極性存 `data/news_patterns.json`，由 `scoring.score_news` 優先採用。
- 消息面無法回補，只能隨每日快照累積；其權重與型態極性待累積足夠後重跑才漸具意義（報告會標註各面涵蓋率與信心）。
- 命中率非報酬率，僅供權重相對比較；建議以樣本外（train/test 切分）複核：`python src\oos_check.py`。

> **完整調適流程見 [TUNING.md](TUNING.md)** —— 內含系統組成、逐步指令、判讀準則、調適決策邏輯與鐵則，
> 讓任何新對話都能執行「重跑調適 / 重新優化權重」。

---

# 選項 D：橫斷面多股籌碼排序（獨立子系統，MVP）

單股每日方向先天丟失「跨上百檔排序」的籌碼 alpha。此為一個**與單股 production 完全分離**的
評估子系統（自有 `data/xs.db`，不動 market.db），以**跨股 IC / 分位報酬 / 多空回測**衡量
籌碼因子有效性（而非單股命中率）。資料源用 TWSE 全市場單次端點（MI_INDEX 收盤、T86 三大法人）。

```powershell
# 全市場大樣本（一次抓全市場、存所有普通股；不增加抓取成本）
python src\xs_ingest.py --backfill 2025-07-01 2026-06-24 --all      # -> data\xs.db（約 20 萬列/表）
python src\xs_backtest.py --start 2025-07-01 --top 300 --cost 0.45 --holds 1,5,10,20 --subperiods 4  # IC/成本/分期 -> reports\xs_backtest_*.md
# 小而精策展清單（~25 檔）
python src\xs_ingest.py --backfill 2026-01-01 2026-06-24
python src\xs_backtest.py --start 2026-01-01 --window 5 --q 5
```

- **訊號**：三大法人淨額 / 成交量（跨股籌碼流入強度），訊號日 D 盤後可得、預測 **D→D+1**（無 look-ahead）。
- **指標**：平均 IC（Spearman）、IC_IR、IC>0 比例、分位平均報酬、多空（top−bottom）累積報酬。
- **股票池**：`src/universe.py` 策展 ~25 檔；或 `--all` 全市場普通股（4 位數、排除 ETF/權證），回測以 `--top N` 每日取流動性前 N 檔。
- **大樣本實測（全市場、每日前 300 檔、2025-07~2026-06、237 日）**：平均 IC 0.013、IC>0 占 **58.7%** 日、
  多空毛累積 **+31.4%**、分位呈高訊號→高報酬。**方向一致為正**（與小樣本 MVP 的負值相反），支持「跨股籌碼 alpha 在規模下浮現」。
- **關卡一（成本/週轉，成本 0.45%/單位週轉）**：每日換倉淨 **−5.2%**（成本吃光），但**持有 5/10/20 日**淨 **+13.8%/+35.7%/+19.4%**
  → 降週轉後可扛成本（最佳約持有 10 日）。
- **關卡二（分期穩健，4 等分）**：單因子前三段多空毛 +21%/+13%/+14%、IC 0.028→0.006，**最近一季（2026-03~06）轉負 −16%**
  → 單因子有衰減/regime 疑慮。
- **多因子複合（`--composite`：法人流 5 日＋法人流 20 日＋外資流 5 日，跨股 z-score 等權）**：IC 0.017、多空毛累積
  **+56%**、**4 段全為非負（+22%/+14%/+13%/+0.3%）**、成本後各持有期淨 **+11%~+17%**（每日換倉也轉正 +14%）。
  → 加入較慢/結構性因子**明顯改善穩定度**（最近一季由 −16% 修復為持平），但 IC 仍弱（<0.03）、近期僅持平；
  屬「更穩健但尚非可部署 alpha」。真正的 TDCC 大戶週變化因全市場歷史不可廉價回補，留待聚焦 ~50 檔再驗。

> 06:00 台股未開盤，採前一交易日收盤定數＋隔夜美股/消息，產出盤前走勢研判。本專案為資訊彙整與分析，非投資建議，據此操作風險自負。
