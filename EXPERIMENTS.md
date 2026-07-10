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
| 4 | EXP-004 | 量能 regime 濾鏡：爆量/縮量日（vol vs vol_ma5 分層）改變既有訊號可靠度 → confidence/overlay，不動方向軸 | already-local（candles 240d） | REJECT |
| 5 | EXP-005 | 融資餘額 5 日急增至極端（散戶槓桿追高）＝反指標 | already-local（chips 表 MI_MARGN） | REJECT |
| 6 | EXP-006 | 波動 regime（^VIX＋SOX realized vol）高波動下「決斷夜跟隔夜」勝率衰減 → 部位調整 overlay | free-fetch＋local | REJECT |
| 7 | EXP-007 | 週期效應：day-of-week（n≈48 可驗）／月底月初（n≈12 只觀察）／營收公布週 系統性偏差 | already-local | REJECT |
| 8 | EXP-008 | 連漲/連跌 ≥3 日後反轉機率偏高（關卡④重點對象：streak 常為 SOX run 影子） | already-local | REJECT |
| 9 | EXP-009 | MU 財報行事曆事件標註（每年 ~4 事件、n 太小 → 僅走「報告改善」路徑，質性風險提示） | free-fetch（公開財報日） | BLOCKED-DATA |
| 10 | EXP-010 | 決斷夜且預期跳空極大時「開盤吃掉行情」→ 全日收盤方向 edge 衰減？（只測收盤方向條件化，嚴禁重測盤中差價） | already-local | REJECT |
| 11 | EXP-011 | DRAM 現貨價趨勢＝華邦基本面驅動（免費源需先驗證；大概率 BLOCKED-DATA） | hard | BLOCKED-DATA |
| 12 | EXP-012 | 06:00 前美期貨（NQ=F）漂移補充隔夜訊號（無歷史 06:00 快照可回補 → 需前瞻蒐集 ≥3 個月） | hard | DEFERRED |
| 13 | EXP-013 | SK海力士美股上市前元大／大摩調度資金大賣 2344 → 次日偏空領先訊號 | already-local（broker_branches） | REJECT |
| 14 | EXP-014 | 月營收趨勢＋記憶體現貨/合約價＋產業飽和度競爭（中國記憶體、韓廠擴產）綜合推算「隱含目標價」，與法人目標價比對驗證算法可信度，再作整體行情判斷 overlay | free-fetch（多來源） | BLOCKED-DATA |
| 15 | EXP-015 | 推理流程優化（非新資料）：模型近5個有表態交易日的命中動能（rolling correctness，僅用<D歷史）可預測次日命中率，作為信心/conviction overlay；須獨立於決斷夜SOX規模（非影子） | already-local（backtest 預測序列） | REJECT |
| 16 | EXP-016 | 決斷夜籌碼推理overlay：籌碼三面(chips/branch/holders)方向共識與SOX方向衝突日，改以籌碼共識覆蓋跟隔夜規則，可將決斷夜全日命中率由70.7%推升 | already-local（daily_decision 決斷夜子集） | REJECT |
| 17 | EXP-017 | 決斷夜籌碼推理overlay（單面）：分別測 chips/branch/holders 各自在與SOX衝突日的方向命中率，找出是否有單面優於SOX可局部覆蓋 | already-local（daily_decision 決斷夜子集） | REJECT |
| 18 | EXP-018 | 決斷夜籌碼推理overlay（弱磁點分層）：|SOX|處於1-2%弱決斷帶時跟SOX命中率較低，此帶內籌碼共識是否具備局部覆蓋優勢（強磁點|SOX|>=2%不動） | already-local（daily_decision 決斷夜子集） | REJECT |
| 19 | EXP-019 | 決斷夜籌碼推理overlay（高強度門檻）：只在籌碼三面平均訊號強度極高(>=0.5)時才視為有效衝突並覆蓋，過濾雜訊分歧樣本 | already-local（daily_decision 決斷夜子集） | REJECT |
| 20 | EXP-020 | 決斷夜籌碼推理overlay（子訊號級）：分點(net/conc/smart/daytrade/longterm)與大戶(chg1w/chg4w/retail)各子訊號在衝突日是否有獨立覆蓋力（避免面聚合稀釋） | already-local（daily_decision 決斷夜子集） | REJECT |
| 21 | EXP-021 | 決斷夜籌碼推理overlay（流量變化）：籌碼三面分數日變化(D-1 vs D-2 delta，趨勢/加速度)而非單日水位，作為衝突判斷來源 | already-local（daily_decision 決斷夜子集） | REJECT |
| 22 | EXP-022 | 決斷夜籌碼推理overlay（chips內部子成分）：拆三大法人淨額(inst)與融資變化(margin)兩子成分，個別測是否有獨立覆蓋力 | already-local（daily_decision 決斷夜子集） | REJECT |
| 23 | EXP-023 | 決斷夜籌碼推理overlay（嚴格一致）：三面須全部有資料且方向完全一致才算籌碼共識（排除多數決2:1弱樣本），測是否較穩健 | already-local（daily_decision 決斷夜子集） | REJECT |
| 24 | EXP-024 | 決斷夜籌碼推理overlay（2D交互）：SOX磁點強度(弱/強)×籌碼訊號強度(弱/強)網格掃描，找是否有特定象限SOX命中率顯著跌破50% | already-local（daily_decision 決斷夜子集） | REJECT |
| 25 | EXP-025 | 決斷夜籌碼推理overlay（5日滾動趨勢）：籌碼三面5日滾動平均（機構週度部位累積，僅用<D歷史）與SOX衝突時是否有覆蓋力 | already-local（daily_decision 決斷夜子集） | REJECT |
| 26 | EXP-026 | 平淡夜MU救援：SOX平淡(|SOX|<1%)但美光大動(|MU|>=門檻)＝記憶體株特異隔夜訊號，跟MU方向可救平淡夜勝率(56.9%→?)；與已否決十面綜合救援不同（單驅動條件化） | already-local（us_market mu 2y＋xs.db） | REJECT |
| 27 | EXP-027 | 平淡夜死區TSM救援：SOX平且MU平(|SOX|<1,|MU|<3)的真死區內，台積電ADR大動(|TSM|>=門檻)＝台股特異通道訊號，跟TSM方向 | already-local（us_market tsm 2y＋xs.db） | REJECT |
| 28 | EXP-028 | 平淡夜死區SOX累積漂移：死區內近3日SOX累積(每晚都<1%但合計|>=1.5%)＝慢速趨勢未被單夜規則捕捉，跟累積方向 | already-local（us_market sox/soxx 2y＋xs.db） | REJECT |
| 29 | EXP-029 | 橫斷面相對強弱（框架重構）：目標改為 2344 vs 記憶體同業籃(2408/2337/4967/2451/3006)次日相對報酬；SOX共同因子被消除後，個股特異訊號(相對動能/相對法人流)應在此目標下有效 | already-local（xs.db 2y） | REJECT |

## 已完成

| EXP | 判定 | 關鍵數字 | 紀錄 |
|---|---|---|---|
| EXP-001 | REJECT | rel_1d 全窗 58.4%(p=0.018) 但平淡夜 52.1%＝SOX 影子；rel_3d OOS −3.6pp；overlay 方向反轉（+9.1pp） | [EXP-001](docs/experiments/EXP-001-kr-memory-basket.md) |
| EXP-002 | REJECT | b1/b3 p≥0.14 無增量；overlay b3≤0.35 近一年 −25pp(n=8) 但早一年 +1pp＝regime 假象 | [EXP-002](docs/experiments/EXP-002-market-breadth.md) |
| EXP-003 | REJECT | twd_5d 單獨過①–④（2y 56.6%, p=0.016）但整合測試任何權重全模型 −2.4pp 起跳＝訊息重疊只稀釋 | [EXP-003](docs/experiments/EXP-003-usdtwd.md) |
| EXP-004 | REJECT | 量能分層 Δ≤4.1pp、p≥0.53；爆量效果跨年符號翻轉；不改變跟-SOX 可靠度 | [EXP-004](docs/experiments/EXP-004-volume-regime.md) |
| EXP-005 | REJECT | sign(−m5) 49.0% 擲幣；極端箱 p≥0.36；m5≥+8% 反而偏漲（−8pp 反向） | [EXP-005](docs/experiments/EXP-005-margin-extreme.md) |
| EXP-006 | REJECT | 波動分箱合併 |Δ|≤3.6pp、p≥0.55、單年翻轉；附帶驗證跟-SOX 對波動全域穩健 | [EXP-006](docs/experiments/EXP-006-vol-regime.md) |
| EXP-007 | REJECT | 週一效應兩年各 p<0.05 但方向相反＝假陽性示範；月底月初 p=0.22 不過 | [EXP-007](docs/experiments/EXP-007-calendar.md) |
| EXP-008 | REJECT | 反轉六格全負（合併 −6.3/−4.6pp）＝輕微續勢；同日子跟-SOX 75% 支配 | [EXP-008](docs/experiments/EXP-008-streak-reversal.md) |
| EXP-009 | BLOCKED-DATA | Yahoo quoteSummary 401（需 crumb）；且 8 次財報反應日 \|漲跌\| 3.21%≈基準 3.13% 無放大 | [EXP-009](docs/experiments/EXP-009-mu-earnings.md) |
| EXP-010 | REJECT | 假設反向：\|SOX\|≥3% 跟-SOX 更可靠（兩年一致 +7.7pp）；2–3% 箱 −8.2pp 但 p=0.197 不過 | [EXP-010](docs/experiments/EXP-010-gap-conditioning.md) |
| EXP-011 | BLOCKED-DATA | 免費靜態頁無現貨價（JS/付費牆）；無免費歷史 API，回測不可能 | [EXP-011](docs/experiments/EXP-011-dram-spot.md) |
| EXP-013 | REJECT | 大摩台分點近期賣超日 72%(p=0.017) 真實但元大僅 40% 不同步；機制＝branch_wf 已測、權重0；催化劑n=1無法通過關卡② | [EXP-013](docs/experiments/EXP-013-ipo-capital-flow.md) |
| EXP-014 | BLOCKED-DATA | 記憶體現貨價(沿用EXP-011)與中韓產能飽和度皆無免費結構化歷史；法人目標價頁(cnyes)雖可解析但核心驅動腿缺2/4 | [EXP-014](docs/experiments/EXP-014-valuation-target-price.md) |
| EXP-015 | REJECT | hot動能64.71%(p=0.009,+4.1pp)但拆層後決斷夜72.41% vs 平淡夜48.15%(≤53%)＝SOX影子，非獨立推理流程改進 | [EXP-015](docs/experiments/EXP-015-prediction-momentum.md) |
| EXP-016 | REJECT | 決斷夜基準70.71%；衝突日改跟籌碼共識僅35.82%(反指標)；覆蓋後全日57.14%(−13.6pp)；同向日76.71%為附帶發現 | [EXP-016](docs/experiments/EXP-016-chips-consensus-decisive.md) |
| EXP-017 | REJECT | 三面單獨測試一致：衝突日跟SOX 62-65% vs 跟籌碼35-37%（互補鏡像）；確認非多數決手法瑕疵，機制性不成立 | [EXP-017](docs/experiments/EXP-017-single-dim-decisive.md) |
| EXP-018 | REJECT | 弱磁點(1-2%)68.97% vs 強磁點71.95%差距小；弱磁點衝突日籌碼仍鏡像互補(33.33%)，覆蓋後63.57%(−7.1pp) | [EXP-018](docs/experiments/EXP-018-weak-sox-band-chips.md) |
| EXP-019 | REJECT | 三門檻掃描非單調(−6.4/+0.7/−2.1pp)；唯一轉正的0.35門檻p=1.000無顯著性，屬多重檢定雜訊 | [EXP-019](docs/experiments/EXP-019-high-conviction-chips.md) |
| EXP-020 | REJECT | 8個子訊號(分點5+大戶3)全數劣化(−9.3~−23.6pp)，含曾入選FLAT_W的daytrade/chg1w，五種粒度一致確認無獨立資訊 | [EXP-020](docs/experiments/EXP-020-subsignal-decisive.md) |
| EXP-021 | REJECT | 趨勢版更劣(−6.4~−18.6pp)；meta發現：二元覆蓋框架下SOX衝突子集命中率從未穩健跌破50%，結構性難成立 | [EXP-021](docs/experiments/EXP-021-chips-delta-decisive.md) |
| EXP-022 | REJECT | inst/margin子成分皆劣化；margin最差(−26.4pp)，衝突日跟SOX反而72.29%＞基準；七輪窮盡拆解一致REJECT | [EXP-022](docs/experiments/EXP-022-chips-subparts-decisive.md) |
| EXP-023 | REJECT | 嚴格一致下衝突日SOX首次跌破50%(47.06%)，覆蓋後+1.4pp但p=0.864無顯著性，屬多重比較巧合 | [EXP-023](docs/experiments/EXP-023-unanimous-chips-decisive.md) |
| EXP-024 | REJECT | 2x2網格四象限僅強SOX×弱籌碼顯著(83.33%,p=0.008，反而支持跟SOX)；唯一<50%象限p=0.815無顯著性 | [EXP-024](docs/experiments/EXP-024-2d-grid-decisive.md) |
| EXP-025 | REJECT | 5日滾動趨勢版仍鏡像互補(−12.1~−16.4pp)；連續第10輪REJECT，達使用者停止條件 | [EXP-025](docs/experiments/EXP-025-5d-trend-decisive.md) |
| EXP-026 | REJECT | 訊號真實(\|MU\|≥3%平淡夜75.86%,p=0.008,非SOX影子)但與composite冗餘：22天中19天已同向，僅3天改向，全日+0.47pp<+1pp門檻 | [EXP-026](docs/experiments/EXP-026-flat-mu-rescue.md) |
| EXP-027 | REJECT | 死區(n=97)內TSM全門檻擲幣(48-57%,p≥0.62)且分年方向翻轉；TSM個股題材傳導不到利基型記憶體 | [EXP-027](docs/experiments/EXP-027-deadzone-tsm-rescue.md) |
| EXP-028 | REJECT | SOX 3日累積漂移全門檻51-54%(p≥0.60)、早一年≤50%；多日緩趨勢已被台股逐日消化，死區無殘餘alpha | [EXP-028](docs/experiments/EXP-028-deadzone-sox-drift.md) |
| EXP-029 | REJECT | 相對目標下四預註冊訊號全擲幣(50.9-52.9%,p≥0.27,n=391大樣本)；個股特異訊號非被SOX蓋住，是日頻本就不存在 | [EXP-029](docs/experiments/EXP-029-xs-relative-strength.md) |

## 已否決、不再重測（移植自 TODOS.md 實測結論；除非「重啟條件」滿足）

| 假設 | 判定依據 | 重啟條件 |
|---|---|---|
| 盤中開盤後方向／開盤跳空差價機械規則 | 扣當沖成本後 ~損益兩平、次開盤 ~50%（commit b9928b9 移除） | 無（結構性無 edge） |
| 外資現貨流 → 次日預測 | corr(外資[d-1], 大盤[d])=0.05（同日 0.51 僅解釋不預測） | 無 |
| 台指期夜盤外資淨買賣 | corr −0.02、sign 命中 51%（501 天） | 無 |
| 橫斷面第 4 因子（TDCC 大戶週變化） | 擴池去集中度後 IC 0.030 vs 0.031，判小樣本假陽性 | TDCC 累積 ≥2 年再驗 |
| 平淡夜救援訊號（十面綜合替代） | OOS 僅 ~46–53%（≈擲幣） | 累積 ≥60 個平淡夜新樣本 |
| 分點行為／法人持續性（5 日累積 57%） | 拆 regime 後平淡夜 49–52% → SOX beta 影子 | 無 |
| 模型自身近期命中動能（rolling correctness 作 conviction overlay） | hot 64.71% 拆層後平淡夜 48.15%（≤53%）→ SOX beta 影子（EXP-015） | 無 |
| 決斷夜籌碼面（chips/branch/holders）覆蓋跟SOX規則（任何粒度：聚合/單面/子訊號/子成分/水位/delta/5日趨勢/嚴格一致/2D交互） | 10輪（EXP-016~025）一致：衝突子集 SOX 命中率結構性不會穩健跌破50%，籌碼側必為鏡像互補、無獨立方向資訊 | 無（機制性證據充分，需全新籌碼資料源才可能重啟） |
| 平淡夜死區（\|SOX\|<1 且 \|MU\|<3，約23%方向性日）隔夜端救援（TSM單夜、SOX多日累積漂移） | EXP-027/028：全門檻擲幣或不顯著、分年翻轉；多日趨勢已被逐日消化 | 無（隔夜端已窮盡；死區若要救只剩非隔夜資訊，但十面綜合救援亦已否決） |
| 橫斷面相對強弱目標（2344 vs 同業籃）下的日頻個股特異訊號（相對動能/相對法人流） | EXP-029：n=391 大樣本全擲幣（p≥0.27），SOX 中性化無法讓已否決訊號復活 | 無（除非引入全新日頻資料源：借券、隔日沖標記等） |
| 台指期 OI 作為評分維度 | 單面 44% 不顯著，護欄已歸 0 | 無 |
