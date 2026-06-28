"""每日盤前決策卡（Step 2，排程分析）：讀該股 strategy.json + 今日 swing_risk，
依該股專屬規則輸出固定格式決策卡（重押/保守＋多空＋早盤被殺機率＋預期勝率）。

通用：以 config.SYMBOL（env STOCK_SYMBOL）為當前標的；strategy.json 由 strategy_builder 產生。
用法：python src/daily_report.py   （建議先跑 build_dataset 抓當日資料）
"""
from __future__ import annotations

import json

import config
import swing_risk


def _load_strategy() -> dict | None:
    f = config.strategy_path()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


_DRIVER_ZH = {"sox": "費半", "smh": "半導體ETF", "soxx": "費半ETF", "mu": "美光",
              "tsm": "台積ADR", "nvda": "NVDA", "amd": "AMD"}


def generate(date_str: str | None = None) -> str:
    strat = _load_strategy()
    drv = (strat or {}).get("overnight_driver", {}).get("best", "sox")
    swing_risk.US_KEY = drv                          # 套用該股最佳隔夜驅動
    dname = _DRIVER_ZH.get(drv, drv.upper())
    sw = swing_risk.estimate()                      # 今日盤前（DB 最新隔夜驅動）
    sym, name = config.SYMBOL, config.NAME
    date = date_str or config.today_str()
    dstr = f"{date[:4]}-{date[4:6]}-{date[6:]}"

    if sw.get("error"):
        card = f"# {sym} {name} 盤前決策卡（{dstr}）\n\n無法產生：{sw['error']}（請先回補日 K / build_dataset）。\n"
        config.report_path(date).write_text(card, encoding="utf-8")
        return card

    ov = sw.get("overnight_sox_pct")
    stance = sw.get("stance", "保守")
    tp = sw.get("today_prob", {})
    dw = sw.get("dir_winrate", {})
    # 方向：決斷夜跟隔夜；平淡夜低信心（依 strategy 類型，beta 股保守）
    side = "偏多" if (ov or 0) > 0 else ("偏空" if (ov or 0) < 0 else "中性")
    decisive = stance == "重押"

    cs = (strat or {}).get("chosen_strategy", {})
    ew = cs.get("expected_winrate", {})
    stype = cs.get("type", "（尚未建立策略，請先 strategy_builder --build）")

    def pr(th, k):
        return tp.get(str(th), tp.get(th, {})).get(k)

    lines = [
        f"# {sym} {name} 盤前決策卡（{dstr}）",
        "",
        "## ⚡ 一行決策",
        (f"【{stance}】{side}・{'高信心' if decisive else '低信心(保守)'}"
         f" ─ 昨晚{dname} {ov:+.2f}%（{sw.get('overnight_bucket')}/{sw.get('conviction')}夜）。"),
        (f"預期同日勝率：決斷夜全日 {dw.get('全日方向',{}).get('win',0):.0%}／開盤 {dw.get('開盤方向',{}).get('win',0):.0%}"
         if decisive else
         f"平淡夜屬低信心情境（該股 {('籌碼可參考' if stype=='chip_alpha_available' else '~擲幣，宜縮量')}）。"),
        "",
        "## 風險儀表板",
        f"- 部位旗標：**{stance}**　方向：**{side}**　波動(10日)：{sw.get('current_vol_10d')}",
        f"- 早盤被殺機率(open_down)：≥2% {pr(2.0,'open_down') or 0:.0%}　≥3% {pr(3.0,'open_down') or 0:.0%}　≥5% {pr(5.0,'open_down') or 0:.0%}",
        f"- 開高機率(open_up)：≥2% {pr(2.0,'open_up') or 0:.0%}　全日收黑：≥2% {pr(2.0,'day_down') or 0:.0%}",
        f"- 平均開盤跳空 {tp.get('avg_open_gap')}%　平均全日 {tp.get('avg_day_move')}%",
        "",
        "## 策略依據",
        f"- 該股類型：{stype}",
        f"- 規則：{cs.get('rule','(未建立)')}",
        (f"- 回測勝率（{cs.get('basis','-')}）：合併 {ew.get('combined',0):.0%}／決斷夜 {ew.get('decisive_night',0):.0%}"
         f"／平淡夜 {ew.get('flat_night',0):.0%}" if ew else "- （尚無 strategy.json，請先 strategy_builder --build）"),
        "",
        "本卡為公開資訊彙整分析，非投資建議，據此操作風險自負。",
    ]
    card = "\n".join(lines)
    config.report_path(date).write_text(card, encoding="utf-8")
    return card


if __name__ == "__main__":
    print(generate())
    print("->", config.report_path())
