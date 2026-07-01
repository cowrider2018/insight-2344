"""每日盤前決策卡（Step 2，排程分析）：讀該股 strategy.json + 今日 swing_risk，
依該股專屬規則輸出固定格式決策卡（重押/保守＋多空＋早盤被殺機率＋預期勝率）。

通用：以 config.SYMBOL（env STOCK_SYMBOL）為當前標的；strategy.json 由 strategy_builder 產生。
用法：python src/daily_report.py   （建議先跑 build_dataset 抓當日資料）
"""
from __future__ import annotations

import json

import config
import indicators
import reversal_risk
import scenario
import scoring
import swing_risk
import timeline_db as tdb
import xs_score


def _position(decisive: bool, risk_level: str) -> str:
    """部位軸：方向信心(決斷夜) × 轉空風險等級 → 部位大小（只降不翻方向）。

    重押僅在『決斷夜 且 轉空風險安全』時允許；風險升高即逐級降部位、極度危險則避開。
    """
    if risk_level == "極度危險":
        return "避開/極輕"
    if risk_level == "偏高":
        return "標準" if decisive else "觀望"
    if risk_level == "留意":
        return "標準" if decisive else "輕"
    return "可重押" if decisive else "輕/標準"   # 安全


def _sgn(x):
    return 1 if (x or 0) > 0 else (-1 if (x or 0) < 0 else 0)


def _contradiction(driver_sign: int) -> tuple[int, list[str]]:
    """今日(盤前 as-of) {均線, RSI, 分點} 與隔夜驅動方向矛盾的數目（重壓濾網用）。

    OOS 顯示：決斷夜若 ≥2 個非隔夜指標與驅動反向，跟驅動較易失準（68%→76% 但少做~40%日），
    故據此把『重押』降為『標準』（只調部位、不改方向；n 偏小、屬中等可信）。
    """
    with tdb.connect() as conn:
        candles = tdb.candles_upto(conn, config.SYMBOL)
        if not candles:
            return 0, []
        tsig = scoring.technical_signals(indicators.compute_all(candles), candles[-1]["close"])
        brows = tdb.branches_asof(conn, config.SYMBOL, "9999-12-31")
        wf = tdb.branch_wf_asof(conn, config.SYMBOL, "9999-12-31")
        br = _sgn(scoring.score_branch(brows, wf_score=(wf or {}).get("score"))) if brows else 0
    sigs = {"均線": _sgn(tsig.get("ma")), "RSI": _sgn(tsig.get("rsi")), "分點": br}
    against = [k for k, v in sigs.items() if v != 0 and v != driver_sign]
    return len(against), against


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
    tp = sw.get("today_prob", {})
    dw = sw.get("dir_winrate", {})
    # 方向軸：決斷夜跟隔夜方向、信心高；平淡夜低信心
    side = "偏多" if (ov or 0) > 0 else ("偏空" if (ov or 0) < 0 else "中性")
    decisive = sw.get("stance", "保守") == "重押"

    # 部位/風險軸（獨立於方向）：轉空風險指數決定部位大小；重押僅在風險安全時允許
    risk = reversal_risk.assess()
    rlevel = "安全" if risk.get("error") else (risk.get("level") or "安全")
    position = _position(decisive, rlevel)
    rpct = "" if risk.get("error") else f"({risk['percentile']:.0%})"
    if risk.get("error"):
        risk_detail = "（轉空風險指數無資料）"
    else:
        risk_detail = (f"（{risk['percentile']:.0%}百分位・外資連賣{risk.get('sell_streak',0)}日"
                       f"｜相對弱{risk['components']['相對弱勢z']:+.1f}/賣壓{risk['components']['外資賣壓z']:+.1f}"
                       f"/連賣{risk['components']['外資連賣z']:+.1f}）")
    # 技術/分點與隔夜背離（補充註記，不另設降級層；部位一律由風險軸決定）
    nc, against = _contradiction(_sgn(ov)) if decisive else (0, [])
    div_note = f"　技術/分點背離 {nc} 項（{'、'.join(against)}）" if nc >= 2 else ""

    cs = (strat or {}).get("chosen_strategy", {})
    ew = cs.get("expected_winrate", {})
    stype = cs.get("type", "（尚未建立策略，請先 strategy_builder --build）")

    def pr(th, k):
        return tp.get(str(th), tp.get(th, {})).get(k)

    lines = [
        f"# {sym} {name} 盤前決策卡（{dstr}）",
        "",
        "## ⚡ 一行決策（方向 × 部位風險 兩軸）",
        (f"- 方向【{side}】{'高信心(決斷夜)' if decisive else '低信心(平淡夜)'}"
         f" ─ 昨晚{dname} {ov:+.2f}%（{sw.get('overnight_bucket')}/{sw.get('conviction')}夜）"),
        (f"- 部位【{position}】 ← 轉空風險 **{rlevel}**{risk_detail}{div_note}"),
        (f"預期同日勝率：決斷夜全日 {dw.get('全日方向',{}).get('win',0):.0%}／開盤 {dw.get('開盤方向',{}).get('win',0):.0%}"
         + ("　但轉空風險已升高、以部位軸為準、勿重押" if rlevel in ("偏高", "極度危險") else "")
         if decisive else
         f"平淡夜屬低信心情境（該股 {('籌碼可參考' if stype=='chip_alpha_available' else '~擲幣，宜縮量')}）。"),
        "",
        "## 風險儀表板",
        f"- 部位：**{position}**　方向：**{side}**　轉空風險：**{rlevel}**{rpct}"
        f"　波動(10日)：{sw.get('current_vol_10d')}",
        f"- 早盤被殺機率(open_down)：≥2% {pr(2.0,'open_down') or 0:.0%}　≥3% {pr(3.0,'open_down') or 0:.0%}　≥5% {pr(5.0,'open_down') or 0:.0%}",
        f"- 開高機率(open_up)：≥2% {pr(2.0,'open_up') or 0:.0%}　全日收黑：≥2% {pr(2.0,'day_down') or 0:.0%}",
        f"- 平均開盤跳空 {tp.get('avg_open_gap')}%　平均全日 {tp.get('avg_day_move')}%",
        "",
        "## 策略依據",
        f"- 該股類型：{stype}　隔夜驅動：{cs.get('overnight_driver', drv)}（{dname}）",
        f"- 規則：{cs.get('rule','(未建立)')}",
        (f"- 交易視窗：**{'賺開盤跳空' if cs.get('trade_window')=='open' else '全日'}**"
         f"（OOS {cs.get('window_winrate_oos') or 0:.0%}）"
         + (f"；開盤不對稱：上驅動開高 {cs['open_asymmetry'].get('up_driver_open_up') or 0:.0%}／"
            f"下驅動開低 {cs['open_asymmetry'].get('down_driver_open_down') or 0:.0%}"
            f"（基準開高 {cs['open_asymmetry'].get('base_open_up') or 0:.0%}，故做多側較強、做空側較弱）"
            if cs.get('trade_window') == 'open' and cs.get('open_asymmetry') else "")),
        (f"- 回測勝率（{cs.get('basis','-')}）：合併 {ew.get('combined',0):.0%}／決斷夜 {ew.get('decisive_night',0):.0%}"
         f"／平淡夜 {ew.get('flat_night',0):.0%}" if ew else "- （尚無 strategy.json，請先 strategy_builder --build）"),
    ]

    # 今日劇本機率（描述性條件分布；昨晚驅動所屬情境）
    pb = scenario.playbook(ov)
    if not pb.get("error"):
        o, rg, cl_, pa = pb["open"], pb["range"], pb["close"], pb["paths"]
        lines += [
            "",
            f"## 今日劇本機率（{pb['bucket']}情境・{pb['scope']}・n={pb['n']}）",
            f"- 開盤：平均跳空 {o['avg_gap']}%｜開高≥2% {o['up']['ge2.0']:.0%}｜開低≥2% {o['down']['ge2.0']:.0%}",
            f"- 盤中震盪：平均振幅 {rg['avg']}%｜振幅≥3% {rg['prob']['ge3.0']:.0%}｜≥5% {rg['prob']['ge5.0']:.0%}"
            f"｜開盤後平均上影 {rg['avg_hi_ext']}%／下殺 {rg['avg_lo_ext']}%",
            f"- 收盤：收紅 {cl_['up_vs_prev']:.0%}｜收在開盤之上 {cl_['above_open']:.0%}｜收在日高半部 {cl_['upper_half']:.0%}",
            f"- 路徑：開高走高 {pa['開高走高']:.0%}｜開高走低 {pa['開高走低']:.0%}｜"
            f"開低走高 {pa['開低走高']:.0%}｜開低走低 {pa['開低走低']:.0%}",
        ]
        ip = scenario.intraday_path(ov)
        if ip.get("n") and ip.get("enough"):
            lines.append(
                f"- 盤中時序(1分K,近月,n={ip['n']})：開盤後30分均 {ip['early30_avg']:+.2f}%"
                f"｜低點均第{ip['low_min_avg']}分／高點第{ip['high_min_avg']}分"
                f"｜先觸低再觸高 {ip['low_before_high_pct']:.0%}"
                + (f"｜開低後收>開 {ip['gap_down_recover_pct']:.0%}" if ip.get("gap_down_recover_pct") is not None else "")
                + " ＝跳空後盤中偏震盪、勿追開盤瞬間")
        elif ip.get("n"):
            lines.append(f"- 盤中時序(1分K)：此情境僅 n={ip['n']} 天（1分K保留期短），樣本不足、暫不提供時序")

    # 橫斷面籌碼分數（2344 籌碼流入強度的跨股相對位置；無 look-ahead，as-of 昨日）
    xc = xs_score.cross_section_score()
    if not xc.get("error"):
        def _pl(name, p):
            d = xc.get("pools", {}).get(p, {})
            if d.get("error"):
                return f"- {name}：樣本不足"
            ic = (f"｜近期IC≈{d['recent_ic']:+.3f}[{d.get('state')}](n={d['ic_n']})"
                  if d.get("recent_ic") is not None else "")
            return (f"- {name}(n={d['n']})：z {d['z']:+.2f}｜百分位 {d['pct']:.0%}"
                    f"｜第 {d['quintile']}/5 分位（5＝流入最強）｜名次 {d['rank']}{ic}")
        # regime 閘門：跨年驗證此因子 regime-dependent（2024H2 曾翻負）→ 反向時明示勿用
        states = [xc.get("pools", {}).get(p, {}).get("state") for p in ("peer", "market")]
        if all(s == "反向" for s in states):
            gate = "- ⚠ **閘門：兩池近期 IC 皆為負（訊號當前失效/反噬）→ 今日此分數勿用於研判。**"
        elif "反向" in states:
            bad = "同儕池" if states[0] == "反向" else "全市場"
            gate = f"- ⚠ 閘門：**{bad}近期 IC 為負（訊號當前失效）**，該池分數勿用；另一池方為當前有效邊際。"
        elif any(s == "有效" for s in states):
            gate = "- 閘門：近期 IC 為正且達門檻＝訊號當前有效；高分股次日相對偏強。此為**相對位置**非單股勝率。"
        else:
            gate = "- 閘門：近期 IC 偏弱/近零＝當前邊際有限，分數僅供參考。此為**相對位置**非單股勝率。"
        lines += [
            "",
            f"## 橫斷面籌碼分數（跨股相對強度・as-of {xc['as_of']}）",
            _pl("同儕池", "peer"),
            _pl("全市場", "market"),
            gate,
        ]
    else:
        lines += ["", f"## 橫斷面籌碼分數", f"- 資料不足：{xc['error']}"]

    lines += ["", "本卡為公開資訊彙整分析，非投資建議，據此操作風險自負。"]
    card = "\n".join(lines)
    config.report_path(date).write_text(card, encoding="utf-8")
    return card


if __name__ == "__main__":
    print(generate())
    print("->", config.report_path())
