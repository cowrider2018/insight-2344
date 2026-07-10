# EXP-NNN <標題>

- 日期／狀態：YYYY-MM-DD ｜ ACCEPT / REJECT / BLOCKED-DATA / DEFERRED
- 假設：<一句可證偽敘述：機制＋方向＋預期幅度>
- 資料：來源｜費用/金鑰｜覆蓋（起迄、n 交易日）｜公布時點與 as-of 截止（`date<D` 或 `avail_date<D`）
- 方法：特徵定義｜轉換｜walk-forward 設定｜評估窗（start–end, n）

## 數字

- In-sample 命中率 xx.x%（active n=…, p=…）
- OOS 命中率 xx.x% ｜ test 偏多基準 xx.x% ｜ in-sample−OOS 差 x.xpp
- Regime 拆分：決斷夜 xx%（n=…）／平淡夜 xx%（n=…）→ 是否 SOX 影子
- 成本後（如適用）／整合後全模型 delta（如適用）

## 判定與理由

<過／敗在哪一關，一段話>

## 產出物

- research/exp_NNN_slug.py ｜ commit <hash>

## 重啟條件

<什麼變了才值得重測，如「累積 ≥60 平淡夜樣本」；無則寫「無」>
