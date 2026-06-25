"""由日 K（OHLCV）計算標準技術指標。輸入為由舊到新排序的 candle list。

每個 candle 須含 date, open, high, low, close, volume 欄位（float/int）。
所有函式皆為純函式，便於測試與重用。
"""
from __future__ import annotations

from typing import Sequence


def _round(x, n=2):
    return None if x is None else round(float(x), n)


def sma(values: Sequence[float], period: int):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def moving_averages(closes: Sequence[float]) -> dict:
    out = {}
    for p in (5, 10, 20, 60, 120, 240):
        out[f"ma{p}"] = _round(sma(closes, p))
    return out


def rsi(closes: Sequence[float], period: int):
    """Wilder's RSI。"""
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return _round(100 - 100 / (1 + rs))


def stochastic_kd(candles, period=9):
    """台股常用 9 日 KD（RSV 平滑因子 1/3）。"""
    if len(candles) < period:
        return {"k": None, "d": None}
    k = d = 50.0
    for i in range(period - 1, len(candles)):
        window = candles[i - period + 1 : i + 1]
        high = max(c["high"] for c in window)
        low = min(c["low"] for c in window)
        close = candles[i]["close"]
        rsv = 50.0 if high == low else (close - low) / (high - low) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
    return {"k": _round(k), "d": _round(d)}


def _ema(values: Sequence[float], period: int):
    if not values:
        return []
    mult = 2 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append((v - ema[-1]) * mult + ema[-1])
    return ema


def macd(closes: Sequence[float], fast=12, slow=26, signal=9) -> dict:
    if len(closes) < slow + signal:
        return {"dif": None, "macd": None, "osc": None}
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = _ema(dif, signal)
    osc = dif[-1] - signal_line[-1]
    return {"dif": _round(dif[-1], 3), "macd": _round(signal_line[-1], 3), "osc": _round(osc, 3)}


def bias(closes: Sequence[float], period=20):
    ma = sma(closes, period)
    if ma is None or ma == 0:
        return None
    return _round((closes[-1] - ma) / ma * 100)


def compute_all(candles: list[dict]) -> dict:
    """candles：由舊到新。回傳標準化 technical 區塊。"""
    closes = [c["close"] for c in candles]
    vols = [c["volume"] for c in candles]
    return {
        "ma": moving_averages(closes),
        "kd": stochastic_kd(candles),
        "rsi": {"rsi5": rsi(closes, 5), "rsi10": rsi(closes, 10)},
        "macd": macd(closes),
        "bias20": bias(closes, 20),
        "vol_ma5": _round(sma(vols, 5), 0),
        "latest_volume": vols[-1] if vols else None,
        "candles_60d": candles[-60:],
    }
