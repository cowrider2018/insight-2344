"""橫斷面訊號與統計工具（選項 D）。純函式，無外部相依（不用 numpy/scipy）。

提供：跨股 rank / z-score、Spearman 等級相關（IC 用）、分位分組、移動平均平滑。
預設籌碼訊號為「三大法人淨額 / 成交量」（xs_db 已算成 flows），可在此再做多日平滑。
"""
from __future__ import annotations


def _avg_ranks(vals: list[float]) -> list[float]:
    """平均等級（處理 ties）。最小值 rank=1。"""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / (sxx * syy) ** 0.5


def spearman(x: list[float], y: list[float]) -> float | None:
    """Spearman 等級相關係數（資訊係數 IC 用）。"""
    if len(x) != len(y) or len(x) < 3:
        return None
    return _pearson(_avg_ranks(x), _avg_ranks(y))


def quantile_groups(pairs: list[tuple[str, float]], q: int = 5) -> dict[int, list[str]]:
    """依訊號值由低到高分成 q 組，回傳 {組索引(0=最低): [symbols]}。"""
    s = sorted(pairs, key=lambda p: p[1])
    n = len(s)
    out: dict[int, list[str]] = {g: [] for g in range(q)}
    for idx, (sym, _) in enumerate(s):
        g = min(q - 1, idx * q // n)
        out[g].append(sym)
    return out


def zscore_map(values: dict[str, float]) -> dict[str, float]:
    """對一組（同一天跨股）值做 z-score；樣本不足或無變異則全 0。"""
    vals = list(values.values())
    n = len(vals)
    if n < 3:
        return {k: 0.0 for k in values}
    m = sum(vals) / n
    sd = (sum((x - m) ** 2 for x in vals) / n) ** 0.5
    if sd <= 0:
        return {k: 0.0 for k in values}
    return {k: (v - m) / sd for k, v in values.items()}


def composite(factor_sigs: list[dict], dates: list[str]) -> dict:
    """多因子複合：每日對「各因子皆有值」的股票各自跨股 z-score，等權平均成複合分數。

    factor_sigs：[{symbol:{date:val}}, ...]。回傳 comp[symbol][date]。無 look-ahead（逐日獨立）。
    """
    if not factor_sigs:
        return {}
    out: dict[str, dict[str, float]] = {}
    for d in dates:
        present = [{s for s in f if d in f[s]} for f in factor_sigs]
        common = set.intersection(*present) if len(present) > 1 else (present[0] if present else set())
        if len(common) < 5:
            continue
        zmaps = [zscore_map({s: f[s][d] for s in common}) for f in factor_sigs]
        for s in common:
            out.setdefault(s, {})[d] = sum(z[s] for z in zmaps) / len(zmaps)
    return out


def _add_days(d_iso: str, days: int) -> str:
    from datetime import date, timedelta
    y, m, dd = (int(x) for x in d_iso.split("-"))
    return (date(y, m, dd) + timedelta(days=days)).isoformat()


def tdcc_change_factor(series: dict, trading_dates: list[str], lag_days: int = 7) -> dict:
    """TDCC 千張大戶週變化因子（結構性、慢）。

    series：{symbol: [(data_date, big_pct)...升冪]}。對每個交易日取「公布日(avail=data_date+lag)<該日」
    的最新與前一週 big_pct 之差（百分點）。**以公布日比較，無 look-ahead**。回傳 {symbol:{date:chg}}。
    """
    out: dict[str, dict[str, float]] = {}
    for sym, ser in series.items():
        if len(ser) < 2:
            continue
        avail = [(_add_days(dd, lag_days), bp) for dd, bp in ser]  # 依 data_date 升冪 -> avail 亦升冪
        for td in trading_dates:
            elig = [bp for ad, bp in avail if ad < td]
            if len(elig) >= 2:
                out.setdefault(sym, {})[td] = elig[-1] - elig[-2]
    return out


def smoothed_flow(flows: dict, dates: list[str], window: int = 5) -> dict:
    """對每檔股票的 flow 序列做 window 日移動平均（含當日，僅用過去資料，無 look-ahead）。

    回傳 sig[symbol][date] = 近 window 日 flow 均值（不足 window 日則以現有日數平均）。
    """
    sig: dict[str, dict[str, float]] = {}
    for sym, series in flows.items():
        ds = [d for d in dates if d in series]
        for i, d in enumerate(ds):
            lo = max(0, i - window + 1)
            vals = [series[ds[k]] for k in range(lo, i + 1)]
            sig.setdefault(sym, {})[d] = sum(vals) / len(vals)
    return sig
