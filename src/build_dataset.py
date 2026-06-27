"""彙整 Fugle / TWSE / CMoney 三來源 -> 標準化 JSON（data/2344_YYYYMMDD.json）。

決定性流程：只負責抓取與標準化欄位，不做分析。任一來源失敗都記錄於 source_status。
"""
from __future__ import annotations

import json

import config
import fetch_dj_chips
import fetch_fugle
import fetch_taifex
import fetch_tdcc
import fetch_twse
import fetch_us
import scrape_cmoney
import swing_risk


def build() -> dict:
    warnings: list[str] = []
    status = {"fugle": "ok", "twse": "ok", "cmoney": "ok", "warnings": warnings}

    # --- Fugle：行情/技術/基本 ---
    try:
        fg = fetch_fugle.build(warnings)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"fugle 例外: {e}")
        fg = {"quote": {}, "technical": {}, "fundamental": {}, "trading_date": None, "name": config.NAME}
    if not fg.get("quote"):
        status["fugle"] = "partial"

    trading_date = fg.get("trading_date")

    # --- TWSE：籌碼 + 月營收 ---
    try:
        chips = fetch_twse.build(warnings, trading_date)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"twse 例外: {e}")
        chips = {"institutional": {}, "margin": {}, "data_date": None}
    if not chips.get("institutional", {}).get("total_net") and not chips.get("margin", {}).get("margin_balance"):
        status["twse"] = "partial"

    try:
        rev = fetch_twse.fetch_monthly_revenue(warnings)
        if rev:
            fg["fundamental"]["monthly_revenue"] = rev
    except Exception as e:  # noqa: BLE001
        warnings.append(f"twse 月營收例外: {e}")

    # --- CMoney：消息面 ---
    try:
        cm = scrape_cmoney.scrape(warnings)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"cmoney 例外: {e}")
        cm = {"news": [], "forum_sentiment": {}, "status": "partial"}
    status["cmoney"] = cm.get("status", "partial")

    # --- 隔夜美股：美光 / 費半（外生特徵）---
    try:
        overnight = fetch_us.build_overnight(warnings)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"us 例外: {e}")
        overnight = {}
    status["us"] = "ok" if overnight else "partial"

    # --- 主力分點（富邦 DJ，第八面）---
    try:
        branch = fetch_dj_chips.fetch_branches(warnings)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"dj 例外: {e}")
        branch = {"date": None, "rows": []}
    status["dj"] = "ok" if branch.get("rows") else "partial"

    # --- TDCC 千張大戶（集保分散表，第九面）---
    try:
        holders = fetch_tdcc.fetch_holders(warnings=warnings)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"tdcc 例外: {e}")
        holders = None
    status["tdcc"] = "ok" if holders else "partial"

    # --- 外資台指期未平倉（TAIFEX，第十面，市場級）---
    try:
        futures = fetch_taifex.fetch_oi(warnings=warnings)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"taifex 例外: {e}")
        futures = None
    status["taifex"] = "ok" if futures else "partial"

    # --- 隔日衝/早盤被殺風險（盤前 6:00 用昨晚費半條件估今日開盤/全日機率）---
    try:
        sox_pct = (overnight.get("sox") or {}).get("change_pct")
        swing = swing_risk.estimate(overnight_pct=sox_pct)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"swing_risk 例外: {e}")
        swing = None

    dataset = {
        "symbol": config.SYMBOL,
        "name": fg.get("name", config.NAME),
        "as_of": config.now_tpe().isoformat(),
        "trading_date": trading_date,
        "quote": fg.get("quote", {}),
        "technical": fg.get("technical", {}),
        "fundamental": fg.get("fundamental", {}),
        "chips": chips,
        "news": cm.get("news", []),
        "forum_sentiment": cm.get("forum_sentiment", {}),
        "overnight": overnight,
        "intraday": fg.get("intraday"),       # 第七面：當日 1 分 K（供累積）
        "branch": branch,                     # 第八面：主力分點（供累積）
        "holders": holders or {},             # 第九面：TDCC 千張大戶（當週，供累積）
        "futures": futures or {},             # 第十面：外資台指期未平倉（D-1，供累積）
        "swing_risk": swing or {},            # 盤前：今日早盤被殺/噴出機率（昨晚費半條件）
        "source_status": status,
    }
    return dataset


def main():
    dataset = build()
    out = config.data_path()
    out.write_text(json.dumps(dataset, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    s = dataset["source_status"]
    print(f"[build_dataset] 寫入 {out}")
    print(f"  fugle={s['fugle']} twse={s['twse']} cmoney={s['cmoney']} us={s.get('us')} "
          f"dj={s.get('dj')} tdcc={s.get('tdcc')} taifex={s.get('taifex')} news={len(dataset['news'])} "
          f"branch={len(dataset.get('branch', {}).get('rows', []))} "
          f"holders={'y' if dataset.get('holders') else 'n'} "
          f"futures={'y' if dataset.get('futures') else 'n'} trading_date={dataset['trading_date']}")
    sw = dataset.get("swing_risk") or {}
    if sw and not sw.get("error") and sw.get("today_prob"):
        tp = sw["today_prob"]
        print(f"  早盤被殺風險: 昨晚SOX {sw.get('overnight_sox_pct')}% -> {sw.get('overnight_bucket')}情境"
              f"  開盤殺>=2% {tp[2.0]['open_down']:.0%}  全日收黑>=2% {tp[2.0]['day_down']:.0%}"
              f"  開高>=2% {tp[2.0]['open_up']:.0%}")
    if s["warnings"]:
        print("  warnings:")
        for w in s["warnings"]:
            print("   -", w)

    # 累積至時間軸 DB（消息/籌碼/營收/K 線），供回測查詢、節省日後重抓成本
    try:
        import ingest
        import timeline_db
        timeline_db.init_db()
        st = ingest.ingest_dataset(dataset)
        print(f"  timeline_db: news+{st['news']} chips+{st['chips']} "
              f"revenue+{st['revenue']} candles+{st['candles']} us+{st.get('us', 0)} "
              f"intraday+{st.get('intraday', 0)} branch+{st.get('branch', 0)} "
              f"holders+{st.get('holders', 0)} futures+{st.get('futures', 0)}")
    except Exception as e:  # noqa: BLE001
        print(f"  timeline_db 攝取略過: {e}")
    return out


if __name__ == "__main__":
    main()
