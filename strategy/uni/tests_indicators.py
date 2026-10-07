#!/usr/bin/env python3
"""Офлайн-тесты индикаторов. Запуск: python3 tests_indicators.py — «провалено 0»."""
import sys

import indicators as ind

tests = 0
fails = 0


def check(name, cond):
    global tests, fails
    tests += 1
    if not cond:
        fails += 1
        print("FAIL:", name)


def approx(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


# SMA / EMA
check("sma базовый", approx(ind.sma([1, 2, 3, 4, 5], 3), 4.0))
check("sma мало данных", ind.sma([1, 2], 3) is None)
check("ema константа", approx(ind.ema([5.0] * 30, 10), 5.0))
check("ema 1..10 p3", approx(ind.ema([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 3), 9.0))

# ATR: highs=11, lows=9, closes=10 → TR=2 всегда → ATR=2
h = [11.0] * 30
lo = [9.0] * 30
c = [10.0] * 30
check("atr константный TR", approx(ind.atr(h, lo, c, 14), 2.0))

# RSI крайние случаи
up = list(range(1, 31))
down = list(range(30, 0, -1))
check("rsi только рост = 100", approx(ind.rsi(up, 14), 100.0))
check("rsi только падение = 0", approx(ind.rsi(down, 14), 0.0))
check("rsi в диапазоне 0..100", 0.0 <= (ind.rsi([1, 2, 1, 3, 2, 4, 3, 5, 4, 6, 5, 7, 6, 8, 7, 9], 14) or 0) <= 100.0)

# Supertrend: явный тренд
rh = [10 + i for i in range(40)]
rl = [9 + i for i in range(40)]
rc = [9.5 + i for i in range(40)]
d_up, _ = ind.supertrend(rh, rl, rc, 10, 4.0)
check("supertrend рост = dir 1", d_up[-1] == 1)
fh = [50 - i for i in range(40)]
fl2 = [49 - i for i in range(40)]
fc = [49.5 - i for i in range(40)]
d_dn, _ = ind.supertrend(fh, fl2, fc, 10, 4.0)
check("supertrend падение = dir -1", d_dn[-1] == -1)

# Donchian
dh = list(range(1, 21))
dl = list(range(0, 20))
up_ch, lo_ch = ind.donchian(dh, dl, 20)
check("donchian верх", approx(up_ch, 20.0))
check("donchian низ", approx(lo_ch, 0.0))
pu, pl = ind.donchian_prev(list(range(1, 22)), list(range(0, 21)), 20)
check("donchian_prev верх без последнего", approx(pu, 20.0))

# Bollinger: константа → σ=0
bb = ind.bollinger([10.0] * 20, 20, 2.0)
check("bollinger mid", approx(bb["mid"], 10.0))
check("bollinger σ=0 на константе", approx(bb["sd"], 0.0) and approx(bb["bandwidth"], 0.0))

# rVOL
check("rvol x2", approx(ind.rvol([10.0] * 20 + [20.0], 20), 2.0))
check("rvol мало данных", ind.rvol([10.0] * 10, 20) is None)

# VWAP: равные объёмы → средняя типичная цена
vh = [11.0, 11.0, 11.0]
vl = [9.0, 9.0, 9.0]
vc = [10.0, 10.0, 10.0]
vv = [1.0, 1.0, 1.0]
check("vwap равные объёмы", approx(ind.vwap(vh, vl, vc, vv, 3), 10.0))

# pct_change
check("pct_change +10%", approx(ind.pct_change([100.0, 110.0], 1), 10.0))
check("pct_change -50%", approx(ind.pct_change([100.0, 50.0], 1), -50.0))

# кроссоверы
check("crossed_above да", ind.crossed_above([39.0, 41.0], 40.0) is True)
check("crossed_above нет (уже выше)", ind.crossed_above([41.0, 42.0], 40.0) is False)
check("crossed_below да", ind.crossed_below([61.0, 59.0], 60.0) is True)

# ohlcv разворачивает порядок (Bybit даёт новые сверху)
kl = [["3", "0", "0", "0", "30", "0", "0"],
      ["2", "0", "0", "0", "20", "0", "0"],
      ["1", "0", "0", "0", "10", "0", "0"]]
o = ind.ohlcv(kl)
check("ohlcv хронологический порядок", o["c"] == [10.0, 20.0, 30.0] and o["t"] == [1, 2, 3])

print("ИТОГО: %d проверок, провалено %d" % (tests, fails))
sys.exit(1 if fails else 0)
