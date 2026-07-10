# 實驗帳本（/self-explore）

> 新訊號 / 新資料源 / regime 濾鏡 / 報告改善 一律走 `/self-explore` 循環驗證。
> 流程與關卡見 `.claude/skills/self-explore/SKILL.md`；單筆紀錄在 `docs/experiments/EXP-NNN-*.md`。

## 基準快照（凍結比較基準，2026-07-10 記錄）

| 指標 | 數值 | 出處 |
|---|---|---|
| 全模型 win_rate | **59.02%**（directional 60.89%、coverage 92.21%、n=244，2025-07-01 起） | `data/2344/weights.json`（as_of 2026-07-01） |
| 採用權重 | technical 0.2 / micron 0.3 / sox 0.2 / holders 0.3（chips・fundamental・intraday・futures 被護欄歸 0） | 同上 |
| 信心分層 | 高 63.85%（n=130）／中 43.75%（n=48）／低 dir 70.21%（n=66） | 同上 |
| 決斷夜（\|SOX\|≥1%） | 同日全日 ~71–72%、開盤 ~90–94% | `src/daily_decision.py` |
| risk_off veto | 全年 64%→65%（僅 3 誤觸日）；回檔期(2026-06-23~07-08) 60%→80% | `src/risk_off.py --validate` |
| 偏多基準 | ~47% | `src/oos_check.py` |

新實驗的 ACCEPT 門檻以此為比較基準；重算 weights.json 後如有變動，於此更新並註記日期。

## Backlog（依 資料可得性 × 合理性 × 與 SOX 正交性 排序）

| Rank | EXP | 假設（一句） | 資料 | 狀態 |
|---|---|---|---|---|
| 1 | EXP-001 | 韓國記憶體同業籃（海力士 000660.KS＋三星 005930.KS 相對 ^KS11 的 D-1 強弱）＝記憶體族群專屬資金流，領先 2344 次日方向（正對 2026-06 海力士 IPO 輪動教訓；韓股 14:30 TST 收盤，06:00 已知） | free-fetch（重用 `fetch_us.fetch_yahoo_daily`） | REJECT |
| 2 | EXP-002 | 台股大盤 breadth（xs.db 前 300 檔上漲家數比 D-1／近 3 日）惡化 → 2344 次日弱勢（補 risk_off 的 market_ret） | already-local（xs.db 2 年） | REJECT |
| 3 | EXP-003 | USDTWD 台幣急貶（D-1 與 5 日趨勢）＝外資撤出前兆 → 偏空 regime | free-fetch（Yahoo TWD=X） | REJECT |
| 4 | EXP-004 | 量能 regime 濾鏡：爆量/縮量日（vol vs vol_ma5 分層）改變既有訊號可靠度 → confidence/overlay，不動方向軸 | already-local（candles 240d） | pending |
| 5 | EXP-005 | 融資餘額 5 日急增至極端（散戶槓桿追高）＝反指標 | already-local（chips 表 MI_MARGN） | pending |
| 6 | EXP-006 | 波動 regime（^VIX＋SOX realized vol）高波動下「決斷夜跟隔夜」勝率衰減 → 部位調整 overlay | free-fetch＋local | pending |
| 7 | EXP-007 | 週期效應：day-of-week（n≈48 可驗）／月底月初（n≈12 只觀察）／營收公布週 系統性偏差 | already-local | pending |
| 8 | EXP-008 | 連漲/連跌 ≥3 日後反轉機率偏高（關卡④重點對象：streak 常為 SOX run 影子） | already-local | pending |
| 9 | EXP-009 | MU 財報行事曆事件標註（每年 ~4 事件、n 太小 → 僅走「報告改善」路徑，質性風險提示） | free-fetch（公開財報日） | pending |
| 10 | EXP-010 | 決斷夜且預期跳空極大時「開盤吃掉行情」→ 全日收盤方向 edge 衰減？（只測收盤方向條件化，嚴禁重測盤中差價） | already-local | pending |
| 11 | EXP-011 | DRAM 現貨價趨勢＝華邦基本面驅動（免費源需先驗證；大概率 BLOCKED-DATA） | hard | pending |
| 12 | EXP-012 | 06:00 前美期貨（NQ=F）漂移補充隔夜訊號（無歷史 06:00 快照可回補 → 需前瞻蒐集 ≥3 個月） | hard | DEFERRED |

## 已完成

| EXP | 判定 | 關鍵數字 | 紀錄 |
|---|---|---|---|
| EXP-001 | REJECT | rel_1d 全窗 58.4%(p=0.018) 但平淡夜 52.1%＝SOX 影子；rel_3d OOS −3.6pp；overlay 方向反轉（+9.1pp） | [EXP-001](docs/experiments/EXP-001-kr-memory-basket.md) |
| EXP-002 | REJECT | b1/b3 p≥0.14 無增量；overlay b3≤0.35 近一年 −25pp(n=8) 但早一年 +1pp＝regime 假象 | [EXP-002](docs/experiments/EXP-002-market-breadth.md) |
| EXP-003 | REJECT | twd_5d 單獨過①–④（2y 56.6%, p=0.016）但整合測試任何權重全模型 −2.4pp 起跳＝訊息重疊只稀釋 | [EXP-003](docs/experiments/EXP-003-usdtwd.md) |

## 已否決、不再重測（移植自 TODOS.md 實測結論；除非「重啟條件」滿足）

| 假設 | 判定依據 | 重啟條件 |
|---|---|---|
| 盤中開盤後方向／開盤跳空差價機械規則 | 扣當沖成本後 ~損益兩平、次開盤 ~50%（commit b9928b9 移除） | 無（結構性無 edge） |
| 外資現貨流 → 次日預測 | corr(外資[d-1], 大盤[d])=0.05（同日 0.51 僅解釋不預測） | 無 |
| 台指期夜盤外資淨買賣 | corr −0.02、sign 命中 51%（501 天） | 無 |
| 橫斷面第 4 因子（TDCC 大戶週變化） | 擴池去集中度後 IC 0.030 vs 0.031，判小樣本假陽性 | TDCC 累積 ≥2 年再驗 |
| 平淡夜救援訊號（十面綜合替代） | OOS 僅 ~46–53%（≈擲幣） | 累積 ≥60 個平淡夜新樣本 |
| 分點行為／法人持續性（5 日累積 57%） | 拆 regime 後平淡夜 49–52% → SOX beta 影子 | 無 |
| 台指期 OI 作為評分維度 | 單面 44% 不顯著，護欄已歸 0 | 無 |
