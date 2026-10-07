#!/usr/bin/env python3
"""Чистые индикаторы для контура UNI. Только стандартная библиотека, без состояния.

Все функции принимают ХРОНОЛОГИЧЕСКИЕ списки (старое → новое) и возвращают
значение для последнего (нового) бара либо серию той же длины с None там, где
данных не хватило. Свечи Bybit приходят новыми сверху — приводить через ohlcv().

Формулы: EMA (SMA-сид), ATR и RSI — по Уайлдеру (сглаживание 1/period),
Supertrend(10,4.0), Donchian, Bollinger (популяционная σ), rVOL, VWAP.
"""


def ohlcv(klines):
    """Bybit kline [start,o,h,l,c,v,turnover] (новые сверху) → словарь хроно-списков."""
    k = list(reversed(klines))
    return {
        "t": [int(x[0]) for x in k],
        "o": [float(x[1]) for x in k],
        "h": [float(x[2]) for x in k],
        "l": [float(x[3]) for x in k],
        "c": [float(x[4]) for x in k],
        "v": [float(x[5]) for x in k],
        "turnover": [float(x[6]) for x in k],
    }


# ── скользящие средние ───────────────────────────────────────────────────────

def sma(values, period):
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def sma_series(values, period):
    out = []
    for i in range(len(values)):
        out.append(sum(values[i + 1 - period:i + 1]) / period if i + 1 >= period else None)
    return out


def ema_series(values, period):
    n = len(values)
    if period <= 0 or n < period:
        return [None] * n
    k = 2.0 / (period + 1)
    out = [None] * (period - 1)
    prev = sum(values[:period]) / period     # сид — SMA первых period
    out.append(prev)
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def ema(values, period):
    s = ema_series(values, period)
    return s[-1] if s else None


# ── истинный диапазон, ATR, RSI (Уайлдер) ────────────────────────────────────

def true_ranges(highs, lows, closes):
    trs = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            trs.append(max(highs[i] - lows[i],
                           abs(highs[i] - closes[i - 1]),
                           abs(lows[i] - closes[i - 1])))
    return trs


def atr_series(highs, lows, closes, period=14):
    tr = true_ranges(highs, lows, closes)
    n = len(tr)
    out = [None] * n
    if n < period:
        return out
    prev = sum(tr[:period]) / period         # сид — SMA первых period TR
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def atr(highs, lows, closes, period=14):
    s = atr_series(highs, lows, closes, period)
    return s[-1] if s else None


def rsi_series(closes, period=14):
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        gains[i] = ch if ch > 0 else 0.0
        losses[i] = -ch if ch < 0 else 0.0
    ag = sum(gains[1:period + 1]) / period
    al = sum(losses[1:period + 1]) / period

    def val(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    out[period] = val(ag, al)
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i] = val(ag, al)
    return out


def rsi(closes, period=14):
    s = rsi_series(closes, period)
    return s[-1] if s else None


# ── Supertrend ───────────────────────────────────────────────────────────────

def supertrend(highs, lows, closes, period=10, mult=4.0):
    """Возвращает (dir_series, line_series): dir 1 — вверх, -1 — вниз."""
    n = len(closes)
    atrs = atr_series(highs, lows, closes, period)
    dir_ = [None] * n
    line = [None] * n
    fu = [None] * n   # финальная верхняя полоса
    fl = [None] * n   # финальная нижняя полоса
    started = False
    for i in range(n):
        if atrs[i] is None:
            continue
        hl2 = (highs[i] + lows[i]) / 2.0
        bu = hl2 + mult * atrs[i]
        bl = hl2 - mult * atrs[i]
        if not started:
            fu[i], fl[i] = bu, bl
            dir_[i], line[i] = 1, bl
            started = True
            continue
        pfu = fu[i - 1] if fu[i - 1] is not None else bu
        pfl = fl[i - 1] if fl[i - 1] is not None else bl
        fu[i] = bu if (bu < pfu or closes[i - 1] > pfu) else pfu
        fl[i] = bl if (bl > pfl or closes[i - 1] < pfl) else pfl
        if dir_[i - 1] == 1:
            dir_[i] = -1 if closes[i] < fl[i] else 1
        else:
            dir_[i] = 1 if closes[i] > fu[i] else -1
        line[i] = fl[i] if dir_[i] == 1 else fu[i]
    return dir_, line


# ── Donchian, Bollinger, rVOL, VWAP ──────────────────────────────────────────

def donchian(highs, lows, period=20):
    """Верх/низ канала за последние period баров (включая текущий)."""
    if len(highs) < period:
        return None, None
    return max(highs[-period:]), min(lows[-period:])


def donchian_prev(highs, lows, period=20):
    """Верх/низ за period баров ДО последнего — для пробоя на закрытом баре."""
    if len(highs) < period + 1:
        return None, None
    return max(highs[-period - 1:-1]), min(lows[-period - 1:-1])


def bollinger(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period   # популяционная дисперсия
    sd = var ** 0.5
    return {
        "mid": mid, "upper": mid + mult * sd, "lower": mid - mult * sd,
        "sd": sd, "bandwidth": (2 * mult * sd / mid) if mid else 0.0,
    }


def rvol(volumes, period):
    """Текущий объём / средний за предыдущие period баров (без текущего)."""
    if len(volumes) < period + 1:
        return None
    avg = sum(volumes[-period - 1:-1]) / period
    return (volumes[-1] / avg) if avg else None


def vwap(highs, lows, closes, volumes, period):
    """Скользящий VWAP по типичной цене (H+L+C)/3 за последние period баров."""
    if len(closes) < period:
        return None
    hs, ls, cs, vs = highs[-period:], lows[-period:], closes[-period:], volumes[-period:]
    num = sum(((hs[i] + ls[i] + cs[i]) / 3.0) * vs[i] for i in range(period))
    den = sum(vs)
    return (num / den) if den else None


# ── прочее ───────────────────────────────────────────────────────────────────

def pct_change(values, n):
    """Изменение в % за n баров: (last / last_n - 1) * 100."""
    if len(values) <= n or values[-1 - n] == 0:
        return None
    return (values[-1] / values[-1 - n] - 1.0) * 100.0


def crossed_above(series, level):
    """Последний бар пересёк level снизу вверх."""
    if len(series) < 2 or series[-1] is None or series[-2] is None:
        return False
    return series[-2] < level <= series[-1]


def crossed_below(series, level):
    if len(series) < 2 or series[-1] is None or series[-2] is None:
        return False
    return series[-2] > level >= series[-1]
