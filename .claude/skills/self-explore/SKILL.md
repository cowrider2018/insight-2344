---
name: self-explore
description: 對 2344 盤前系統執行「自我探索」實驗循環：從 EXPERIMENTS.md 待驗清單挑一個假設 → 驗證資料可得性 → walk-forward 回測 → ACCEPT/REJECT 寫入實驗帳本並 commit。當使用者說「自我探索」「探索新訊號」「跑一輪實驗」「self-explore」時使用。
---

# self-explore — 自我探索實驗循環

目的：以可重複、可稽核的流程尋找系統改進（新評分維度 / 新資料源 / regime 濾鏡 overlay / 報告改善）。
原則：**精簡輸出、值得信賴（誠實統計）、可參考（每輪留紀錄）、可獲利（edge 須扛成本）**。

所有路徑相對於專案根目錄。互動輸出一律繁體中文。

## 鐵則（不可違反）

1. **Reject-by-default**：預設 REJECT；只有全部關卡通過才 ACCEPT；任何不確定＝REJECT。
2. **絕不弱化既有已驗證邏輯**：不得修改 `src/scoring.py` 既有函式行為、`src/risk_off.py` 門檻、`src/swing_risk.py` 規則；`data/<SYM>/weights.json` 只能由 `src/backtest.py` / `src/calibrate.py` 重算產生，禁止手改。
3. **資料只用免費、免金鑰來源**（既有 FUGLE key 可續用）；需要新 API key 或付費 → 直接判 `BLOCKED-DATA` 結案。
4. 沿用 `TUNING.md` §6 鐵則：無 look-ahead（as-of `< D`）、OOS 必附、命中率 ≥80%＝bug 先抓錯、命中率≠報酬率、樣本外天花板 ~55–62%。
5. **每輪一個實驗、一個 atomic conventional commit**（REJECT / BLOCKED-DATA 也要留紀錄與 commit）；僅本地 commit、絕不 push、絕不加任何 AI 署名。
6. **聊天輸出精簡**：每實驗結案輸出 ≤10 行（判定＋關鍵數字＋帳本路徑），細節寫進 EXP 檔。
7. 開跑前先讀 `EXPERIMENTS.md` 的「已否決、不再重測」清單；與其重疊的假設直接跳過（除非其「重啟條件」已滿足）。
8. 研究程式碼一律放 `research/`，**禁止被 daily path**（`build_dataset.py` / `scoring.py` / 每日技能）import。

## 觸發方式

- `/self-explore` — 跑 backlog 最高順位 `pending`
- `/self-explore EXP-003` — 指定實驗
- `/self-explore 3` — 連跑 3 輪
- `/self-explore 新想法：<描述>` — 先登記進 backlog（給 EXP-id、資料標籤、排序）再跑

## Stage 1 — 探索方法

1. 讀 `EXPERIMENTS.md`：基準快照、backlog、否決清單。
2. 取最高順位 `pending`（或使用者指定）。
3. 把假設寫成**一句可證偽敘述**：機制＋方向＋所需資料＋預期幅度。
4. 在 backlog 將該項標為 `RUNNING`。

## Stage 2 — 資料可得性

- `already-local`：`python src/timeline_db.py` 查各表筆數（xs 面查 `data/xs.db`）；日頻訊號覆蓋 <100 交易日 → 降級假設或判 `BLOCKED-DATA`。
- `free-fetch`：先寫小探針（放 `research/`）驗證三件事：
  1. 欄位齊全、可穩定解析；
  2. 歷史可回補 ≥200 交易日（供回測）；
  3. **公布時點**明確 → 決定 as-of 截止：即時資料 `date < D`，有公布延遲者用 `avail_date < D`（仿 TDCC）。
- 任一不滿足 → 立即結案 `BLOCKED-DATA`（照樣寫帳本＋commit；這是最便宜的合法結局）。

## Stage 3 — 實作＋回測

寫 `research/exp_NNN_<slug>.py`：

- `sys.path.insert(0, "src")` 重用 `timeline_db` / `backtest` / `scoring` / `config`。
- 特徵抽取一律 as-of `< D`，以 `assert` 強制（仿 `backtest.extract_features`）。
- 評估與現有系統同口徑：中性帶 ±1%、目標 = sign(D 收盤 − D-1 收盤)、需估參數時走 walk-forward（對每個 d 只用 `< d` 歷史，仿 `branch_model.py`）。

**五道硬關卡**：

| # | 關卡 | 判準（沿用現有程式碼標準） |
|---|---|---|
| ① | Look-ahead 稽核 | 全部查詢 `< D`（lag 資料用 `avail_date < D`）；命中率 ≥80% 直接視為 bug 回頭抓 |
| ② | 顯著性 | 非中性 active ≥ 8、雙尾二項 p < 0.15、hit_rate > 0.5（同 `backtest.dim_significant`） |
| ③ | OOS | 70/30 時序切分；OOS ≥ in-sample − 2pp，且 OOS > test 偏多基準 + 1pp（同 `oos_check.py`） |
| ④ | SOX 影子檢驗 | 依決斷夜（\|昨晚SOX\| ≥ 1%）／平淡夜分層各算命中率；edge 只存在決斷夜且平淡夜 ≤53% → 判 SOX beta 影子 → REJECT（前例：法人持續性 57% 拆層後平淡夜 49–52%） |
| ⑤ | 成本存活 | 涉進出頻率：扣 0.45%/單位週轉後仍為正；純方向研判訊號：以「整合後全模型 OOS 提升 ≥ +1pp」為實質門檻 |

## Stage 4 — 判定

依假設類型套 ACCEPT 標準（其餘一律 REJECT）：

- **新評分維度**：過關卡 ①–④，且全網格重跑後 win_rate 提升 ≥ +1pp、`oos_check.py` 判 `[OK]` 且不低於整合前基準。
- **Overlay（regime / veto，仿 risk_off）**：walk-forward 全年命中率變化 ≥ −1pp（近乎無損）、目標情境提升 ≥ +5pp 且受影響日 n ≥ 8、必附誤觸日數。
- **報告改善（非統計）**：資料可得、不入加權、只做質性呈現並明寫「無統計 edge 主張」。

終局四選一：`ACCEPT` / `REJECT` / `BLOCKED-DATA` / `DEFERRED`（資料需前瞻蒐集後才可驗）。每種終局都要：

1. 寫 `docs/experiments/EXP-NNN-<slug>.md`（依 `docs/experiments/TEMPLATE.md`）。
2. 更新 `EXPERIMENTS.md`（backlog 狀態＋已完成表加一行）。
3. Commit（格式）：`test(exp-001): korean-memory-basket — REJECT (OOS 53.1% vs base 47.2%)`。

## ACCEPT 之後（不得自動執行）

**停下**，輸出整合計畫與完整數字，**等使用者確認**後才動 production：

- **新維度路徑**：`src/fetch_<source>.py` → `timeline_db.py`（新表＋`<name>_asof` 皆 `< D`）→ `ingest.py` → `build_dataset.py` → `scoring.py`（`DIMENSIONS`＋`score_<name>`）→ `calibrate.py` CANDIDATES → `backtest.py`（`extract_features`＋assert）→ 重算 `weights.json` → `oos_check.py` 必 `[OK]` → `.claude/skills/cmoney-2344-daily/SKILL.md` 加段落 → README / TUNING 資料源表。
- **Overlay 路徑**：`src/<name>.py`（`assess()` as-of D-1＋`--validate` walk-forward CLI，仿 `risk_off.py`）→ `build_dataset.py` 掛載 → cmoney-2344-daily 判讀規則。
- 整合後必重跑 `backtest.py`＋`oos_check.py`；OOS 較整合前退步 >1pp → **revert 整合、降為 REJECT** 並記錄數字。

## 停止條件

預設 1 輪；`/self-explore N` 最多 N 輪；使用者要求連續跑則跑到喊停。ACCEPT 發生 → 本輪結束即停（等整合確認）。
