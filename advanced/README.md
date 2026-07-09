# 進階建置工具（advanced/）

一般使用者**不需要**動這個資料夾。專案根目錄的四支 `.bat` 已涵蓋日常生命週期：

| 檔案 | 用途 |
|---|---|
| `setup.bat` | 一條龍完整建置（環境 + 資料回補 + 回測產生權重 + Gmail 授權） |
| `run_once.bat` | 立即執行一次每日流程 |
| `schedule_create.bat` | 建立每日 06:00 排程 |
| `schedule_delete.bat` | 刪除排程 |

本資料夾收錄**進階/選用**工具，供需要重新調適參數、重算權重，或把框架套到其他個股的使用者。
所有工具皆會自動切回專案根目錄、以 `.venv\Scripts\python.exe` 執行；**請從專案根目錄呼叫**（例如 `advanced\calibrate.bat`），或直接雙擊。

| 檔案 | 用途 | 等價手動 |
|---|---|---|
| `backfill_history.bat` | 以**當前已校準參數**回補歷史籌碼 → 回測 → 消息驗證 → 樣本外複核，重算 `data\weights.json`、`data\news_patterns.json`。`setup.bat` 已做過一次；此工具用於**日後刷新**。 | README 路徑 B-2 |
| `calibrate.bat` | **重新校準**評分參數（座標上升）→ `data\score_params.json`，再全網格重算 `data\weights.json` → 樣本外複核。改的是評分參數本身，非只重算權重。 | README 路徑 B-2' |
| `build_stock.bat <symbol> [name]` | 通用框架 Step 1：對任一上市股跑完整策略建置（回補 → 校準 → 跑方法電池挑最佳）→ `data\<symbol>\strategy.json`。 | 見 README「通用個股策略框架」 |
| `daily_stock.bat <symbol>` | 通用框架 Step 2：抓當日資料 → 產出決策卡 → `reports\<symbol>\`。 | 見 README「通用個股策略框架」 |

> `backfill_history.bat` / `calibrate.bat` 未帶參數時，預設視窗為 `--start 2025-07-01`（與 [TUNING.md](../TUNING.md) 基準一致），可自行覆寫，例如：
> `advanced\calibrate.bat --start 2025-07-01 --end 2026-06-23 --rounds 2`

完整調適流程、判讀準則與鐵則見專案根目錄的 **[TUNING.md](../TUNING.md)**。
