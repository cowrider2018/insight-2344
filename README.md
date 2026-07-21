# 2344 華邦電 每日盤前走勢分析

每天上午 06:00 自動抓取**十面**標準化資料（技術 / 基本 / 籌碼 / 消息 / 美光 / 費半 / 日內 1 分 K / 主力分點 / TDCC 千張大戶 / 外資台指期未平倉），由 Claude 依回測最佳權重做當日盤前走勢研判，產出報告並 email。

---

# 🏭 通用個股策略框架（兩步驟）

本專案已通用化：任一上市股都能跑。以環境變數 `STOCK_SYMBOL` 切換標的（預設 2344），每股獨立狀態收於
`data/<symbol>/`、報告於 `reports/<symbol>/`；`market.db` / `xs.db` 共用（已含 symbol 欄）。2344 為內建範例。

**Step 1 — 策略建置（`strategy_builder.py`）**：對一支股票把所有方法跑一遍，以 **OOS** 挑最高勝率的可部署規則、
判定該股屬「隔夜美股 beta 主導」或「籌碼/個股 alpha 可用」型，寫成專屬 `data/<symbol>/strategy.json`。
```powershell
advanced\build_stock.bat 2330 台積電   # = STOCK_SYMBOL=2330 python src\strategy_builder.py --full
# 或分步：--backfill（回補全部資料）→ --calibrate（校準十面權重）→ --build（跑方法電池、寫 strategy.json）
```
方法電池：十面評分＋顯著性護欄、信心分層、隔夜決斷度勝率（swing_risk）、每日選邊 regime 切換＋OOS／跨年複核、
外資背離（東買西賣）、平淡夜逐訊號。挑選以 OOS 為準（避免過擬合）。

**Step 2 — 排程分析（`daily_report.py`）**：每日盤前讀該股 `strategy.json` ＋ 今日資料，輸出固定格式**決策卡**
（重押/保守 ＋ 多空 ＋ 早盤被殺機率 ＋ 預期勝率）。
```powershell
advanced\daily_stock.bat 2344        # = STOCK_SYMBOL=2344 python src\build_dataset.py + daily_report.py
```

> 範例（2344，自動分型）：`overnight_beta_dominated`；決斷夜跟隔夜美股、重押（OOS 同日 ~67–72%），平淡夜保守（~54%）。
> 不同股票若籌碼面有獨立 edge（平淡夜 OOS≥55%），會被分型為 `chip_alpha_available`、平淡夜改用籌碼訊號選邊。

---

## 分工
- **標準化抓取（決定性腳本）**：`src/build_dataset.py` → `data/2344_YYYYMMDD.json`
- **時間軸累積**：`src/timeline_db.py`（SQLite `data/market.db`）+ `src/ingest.py`，`build_dataset.py` 每日自動攝取
- **極短線盤前風險**：`src/swing_risk.py` → 以**昨晚費半方向**條件化的今日**早盤被殺/噴出機率**（開盤跳空 ≤−θ / ≥θ、全日收黑/紅）＋**決斷度與同日方向歷史勝率**，供 06:00 隔日沖判「今天會不會被殺、該不該重押」；併入 `data/2344_*.json` 的 `swing_risk`
- **risk-off 下檔保護 veto（方向軸修正）**：`src/risk_off.py` → 偵測**記憶體族群輪出**（記憶體同業籃相對全市場弱勢＋族群外資賣，as-of 昨日、讀 xs.db；診斷 `memory_rotation`/`broad_risk_off`/`normal`）＋**2344 外資近 3 日累積賣超 ≤ −60,000 張**時，否決核心「跟費半做多」翻偏空（不對稱、只擋做多）。併入 `data/2344_*.json` 的 `risk_off`，由 `daily_report.py`／技能套進方向軸。
  - 由來：2026-06-22→07-08 回檔（2344 −24%）核心「跟費半」崩到 ~50%——費半隔夜漲、資金卻專屬撤離記憶體（海力士 IPO），2344 脫鉤照跌。因果拆解：**全市場(前300)持平 +0.4%、記憶體籃 −22%、2344 −26%**＝族群輪動非總體 risk-off。
  - 實證（walk-forward 2025-07~2026-07，`python src/risk_off.py --validate`）：方向命中率 **全年 64%→65%**（幾乎無損、全年僅 3 誤觸日、9 觸發日）、**回檔期 60%→80%**。**防禦性 overlay、非全天候 alpha（回檔方向日 n=10，門檻示意）；與轉空風險部位軸(`reversal_risk.py`)互補——後者縮量、此翻向。**
  - 勝率實證（2344, 241 日，方向性、中性帶±1%）：十面模型全表態同日收盤 **~60%（天花板）**；**改以隔夜美股決斷度選日**——
    昨晚費半 **|≥1%| 同日全日命中 71%、開盤 90%**；**|≥2% 決斷夜 全日 72%、開盤 94%**（各約覆蓋半數/三成交易日）。
    → **67% 目標靠「只在決斷夜重押、平淡夜降信心」達成**（`swing_risk.py --accuracy` 可複算）。
- **每日選邊決策（`src/daily_decision.py`）**：每天一定選一邊＋信心分級——決斷夜跟隔夜(信心高)、平淡夜用十面 composite(信心低)。
  - full-window(近一年, in-sample)：合併 68.0%、決斷夜 71.2%、平淡夜 62.0%。
  - **OOS 複核（train/test 切分，`--oos`）**：合併 **65.1%**（in-sample 65.0% → 不過擬合）、決斷夜 **68.0%**、**平淡夜 53.8%（≈擲幣）**、全跟隔夜 64.5%。
  - **跨年複核（`--cross-year`）**：決斷夜跟隔夜 **2024 年 70.7% / 2025 年 71.0%**（兩年一致，結構性穩定）；全隔夜 ~62~63%。
  - **誠實結論**：每日選邊 OOS **~65%（>60%，未達 67%）**；真正可靠的邊際是**決斷夜跟隔夜 ~68~71%（跨年穩定、weight-free）**；
    **平淡夜十面 ~53% 幾無邊際**。**67% 目標靠決斷夜重押、平淡夜保守**達成。
  - **部位旗標**：`swing_risk.stance`＝**重押**(決斷夜)/**保守**(平淡夜)，每日盤前一眼判部位大小。
  - **救平淡夜（嘗試，結論：未過 OOS）**：平淡夜逐訊號實測，大戶週變化 62.7%、分點隔日沖 62.3%（樣本內最佳）；
    組成平淡夜專屬訊號樣本內把平淡夜拉到 67.6%、合併 70%，**但 OOS 測試段（僅 13 平淡夜日）46%、未撐住**（選擇偏誤＋樣本過小）。
    → 預設**不啟用**（`daily_decision.decide(flat_signal=False)`），平淡夜維持保守；待累積更多平淡夜資料再驗。
- **回測 / 權重優化**：`src/scoring.py` + `src/backtest.py` → `data/weights.json`（六面權重 + 中性門檻）
- **參數校準**：`src/calibrate.py` → `data/score_params.json`；**消息型態驗證**：`src/validate_news.py` → `data/news_patterns.json`
- **分析（Claude）**：技能 `/cmoney-2344-daily` 讀 JSON + weights → `reports/2344_YYYYMMDD.md`
  ＋白話新手版 `reports/2344_YYYYMMDD_simple.md`
- **交付**：`src/send_email.py` 每天寄**兩封獨立的信**——完整版 → `MAIL_TO`；
  白話版（`--simple`，固定版型、無術語、含各面向多空與權重表）→ `MAIL_TO_SIMPLE`
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
MAIL_TO=<你的 Email>                  # 完整版報告收件名單
MAIL_TO_SIMPLE=<白話版 Email>         # 白話新手版收件名單（可多人逗號分隔）；留空則沿用 MAIL_TO
```
寄信採 Gmail API + OAuth：需 `credentials.json`（Google Cloud Console 下載）。`setup.bat` 會開瀏覽器要求登入授權（安排在長時間回測前，趁你在場完成），產生 `token.json`，之後排程即可無頭寄信。

> 為何要在 setup 授權：若沒有 `token.json`，每日排程的寄信步驟會落入互動式 OAuth 流程、開瀏覽器等人授權，在無人值守的 06:00 排程會卡住不動。先在 setup 完成一次授權即可避免。

---

# 操作方式

每個流程都提供「**一鍵 .bat**」與「**等價手動指令**」兩種方式，**擇一即可**。先設好 `.env`（與 Gmail 憑證）再開始。下面兩節為同一套流程的兩種入口，步驟編號互相對應。

## 路徑 A — 一鍵操作（.bat，雙擊即可）

專案根目錄僅四支 .bat，涵蓋日常生命週期；進階/選用工具收於 `advanced\`（見下）。依生命週期順序：

| 步驟 | 檔案 | 用途 | 等價手動（見路徑 B） |
|---|---|---|---|
| 1 | `setup.bat` | **一條龍完整建置**：建立 `.venv` + 安裝套件 + Playwright + 初始化時間軸 DB + 回補消息/K線/美股/日內/分點 + Gmail 授權（開瀏覽器登入一次）+ 回補歷史籌碼 + 回測產生 `data\weights.json` + 消息型態驗證。跑完系統即可上線。 | B-1、B-2 |
| 2 | `run_once.bat` | **立即執行一次**每日流程（抓資料 → Claude 分析 → 寄信） | B-3 |
| 3 | `schedule_create.bat` | **建立**每日 06:00 排程 | B-4 |
| — | `schedule_delete.bat` | **刪除**排程 | B-4 |

> 排程相關 .bat 若提示權限不足，請以系統管理員身分執行。

### 進階工具（`advanced\`，選用）
一般使用者不需碰。供重新調適參數、刷新權重，或把框架套到其他個股者；請從專案根目錄呼叫（或雙擊）。

| 檔案 | 用途 | 等價手動 |
|---|---|---|
| `advanced\backfill_history.bat` | 以當前參數回補歷史籌碼 → 回測 → 消息驗證 → OOS 複核，**刷新** `data\weights.json`、`data\news_patterns.json`（`setup.bat` 已做過一次） | B-2 |
| `advanced\calibrate.bat` | **重新校準**評分參數 + 重算平衡權重 → OOS 複核（`data\score_params.json`、`data\weights.json`） | B-2' |
| `advanced\build_stock.bat <symbol> [name]` | 通用框架 Step 1：對任一上市股跑完整策略建置 → `data\<symbol>\strategy.json` | 見上方「通用個股策略框架」 |
| `advanced\daily_stock.bat <symbol>` | 通用框架 Step 2：抓當日資料 → 產出決策卡 → `reports\<symbol>\` | 見上方「通用個股策略框架」 |

> `advanced\backfill_history.bat` / `advanced\calibrate.bat` 未帶參數時，預設視窗為 `--start 2025-07-01`（與 TUNING.md 基準一致），可自行覆寫，例如 `advanced\calibrate.bat --start 2025-07-01 --end 2026-06-23 --rounds 2`。詳見 `advanced\README.md`。

## 路徑 B — 手動指令（PowerShell，等價於各 .bat）

### B-1 一條龍建置（＝ `setup.bat`，＝下列環境步驟 + B-2 回測）
```powershell
python -m venv .venv                                 # 建立虛擬環境（套件不裝到全域 / WindowsApps）
.\.venv\Scripts\Activate.ps1                         # 啟用 .venv（之後 B-2 / B-2' / B-3 指令都在此環境下執行）
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python src\timeline_db.py --init
python src\ingest.py --backfill-json
python src\ingest.py --backfill-candles
python src\ingest.py --backfill-intraday
python src\ingest.py --backfill-branches
python src\ingest.py --backfill-us
python -c "import sys; sys.path.insert(0,'src'); import send_email; send_email._gmail_service()"  # Gmail 授權，開瀏覽器登入一次 -> token.json
# 接著 setup.bat 續跑 B-2 的建置步驟（歷史籌碼回補 → branch_model → 回測產生 weights → 消息驗證）：
python src\ingest.py --backfill-chips
python src\branch_model.py
python src\backtest.py --start 2025-07-01
python src\validate_news.py --start 2025-07-01
```
> 所有腳本都以專案 `.venv` 執行：`setup.bat` / `advanced\backfill_history.bat` / `advanced\calibrate.bat` 會自動使用 `.venv\Scripts\python.exe`，`src\run_daily.ps1`（排程）亦優先採用 `.venv`，找不到才退回 PATH。手動執行請先 `Activate.ps1` 啟用。

### B-2 回補歷史 + 重算權重（＝ `advanced\backfill_history.bat`，以「當前已校準參數」重算）
```powershell
python src\ingest.py --backfill-chips                            # 回補歷史籌碼（逐日抓 TWSE 較慢；省略日期=自動取全區間）
python src\ingest.py --backfill-tdcc                             # 第九面 TDCC 千張大戶（smart.tdcc 逐週，約近一年）
python src\ingest.py --backfill-futures                          # 第十面 外資台指期未平倉（TAIFEX 一次取整段）
python src\backtest.py --start 2025-07-01 [--end 2026-06-23]     # 回測 -> data\weights.json + reports\backtest_*.md
python src\validate_news.py --start 2025-07-01                   # 驗證消息型態極性 -> data\news_patterns.json
python src\oos_check.py --start 2025-07-01                       # 樣本外複核（OOS 應 >= baseline ~47%）
```

### B-2' 逐步校準評分參數（＝ `advanced\calibrate.bat`，會「改寫」參數再重算）
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
python src\send_email.py reports\2344_YYYYMMDD.md            # 完整版寄信測試 -> MAIL_TO
python src\send_email.py --simple reports\2344_YYYYMMDD_simple.md   # 白話版寄信測試 -> MAIL_TO_SIMPLE
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
  → 加入較慢/結構性因子**明顯改善穩定度**（最近一季由 −16% 修復為持平），但全市場 IC 仍弱（<0.03）。
- **聚焦池（~50 檔半導體同儕，`--tdcc-pool` / `--tdcc`）— 訊號明顯更強且加入真大戶因子有效**：同質同業的橫斷面排序更乾淨。
  - 3 因子（同池）：IC **0.030**、IC_IR 0.15、多空毛 **+102%**、4 段全正（+18/+22/+23/+14%）。
  - **4 因子（＋TDCC 大戶週變化）**：IC **0.036（過 0.03 門檻）**、IC_IR 0.19、IC>0 57%、多空毛 **+112%**、
    分位單調（Q4 0.62%/日 vs Q0 0.27%）、**4 段全正含最近季（+37/+18/+17/+12%）**、成本後各持有期淨 **+35%~+78%**。
  - 註：50 檔窄池時大戶因子曾顯現邊際，但**去集中度後消失**（見下）。
- **穩健性複核（擴池 + 跨 regime）— 重要修正**：
  - **去集中度**（焦點池擴至 ~105 檔、每分位 ~21）：3 因子仍穩（IC 0.031、IC_IR 0.19、多空毛 +102%、4 段全正）→ **核心籌碼 alpha 真實、非小樣本雜訊**；
    但 **4 因子（+大戶）不再優於 3 因子**（IC 0.030 vs 0.031、毛 +75% vs +102%）→ 50 檔上看到的大戶邊際**未通過去集中度檢驗**，判為小樣本假陽性。
  - **跨 regime（拉長至 2 年，2024-07~2026-06；TDCC 受 1 年保留期限故僅 chip+foreign 3 因子可拉）**：**edge 強烈 regime 依賴**——
    近一年(2025-07~2026-06)多空毛 **+102%**、IC 0.025~0.036；早一年(2024-07~2025-06，真 OOS)多空毛僅 **+14%**、IC **0.002~0.011**（每日換倉淨 −7%）。2 年平均 IC 0.019（<0.03）。
  - **修正後結論**：跨股籌碼 alpha **真實但不穩定**——在多頭/高分散度 regime 顯著、2024 弱市幾近消失；**大戶週變化非可靠加值因子**。屬「機制驗證完成、有條件有效，非全天候可部署 alpha」。
- **純做多可部署性（`--long-only`；基準＝個股等權整池，無 ETF）**：
  - 基準（無腦等權買整池）本身 +117%~+140%（半導體大多頭，**多數為 beta**）。
  - top 分位選股**僅在拉長持有（~20 日/月換倉）才贏過等權整池**：超額 近一年 3 因子 +14%、2 年 +39%、4 因子 +54%；
    短持有（1~5 日）多為**負超額**（成本吃光）。
  - → 先前 +100% 多空績效**主要來自放空後段**，純做多拿不到。**做為純做多個股策略：選股超額有限且不穩、報酬主要是 beta**；
    最佳形態為「月換倉、做多最高籌碼訊號分位」，超額為正但小。**非顯著可部署 alpha；建議僅作選股傾向參考。**

> 06:00 台股未開盤，採前一交易日收盤定數＋隔夜美股/消息，產出盤前走勢研判。本專案為資訊彙整與分析，非投資建議，據此操作風險自負。
