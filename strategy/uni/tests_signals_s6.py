#!/usr/bin/env python3
"""Офлайн-тесты S6: T1 (пробой) и T3 (имбаланс+лента), гейты спреда/подушки."""
import sys

import signals_s6 as s6

tests = 0
fails = 0


def check(name, cond):
    global tests, fails
    tests += 1
    if not cond:
        fails += 1
        print("FAIL:", name)


def kl(closes, highs=None, lows=None, vols=None):
    n = len(closes)
    highs = highs or [c + 0.1 for c in closes]
    lows = lows or [c - 0.1 for c in closes]
    vols = vols or [100.0] * n
    rows = [[str(i), str(closes[i]), str(highs[i]), str(lows[i]), str(closes[i]), str(vols[i]), "0"]
            for i in range(n)]
    return list(reversed(rows))


NOW = 1_700_000_000_000
OB_OK = {"b": [["100.00", "1000"]], "a": [["100.02", "1000"]]}       # спред ~0.02%, подушка ок
OB_WIDE = {"b": [["100.00", "1000"]], "a": [["101.00", "1000"]]}     # спред ~1%
OI_POS = lambda it, lb: 5.0
OI_NONE = lambda it, lb: None

# T1 лонг: пробой 5-мин диапазона + объём ×3 + OI растёт
c_break = [100.0] * 24 + [101.0]
h_break = [100.1] * 24 + [101.1]
l_break = [99.9] * 24 + [100.9]
v_break = [100.0] * 24 + [300.0]
k_t1 = kl(c_break, h_break, l_break, v_break)
sig = s6.evaluate_s6("T", k_t1, OB_OK, [], NOW, OI_POS)
check("T1 срабатывает на пробое", sig is not None and sig["trigger"] == "T1")
check("T1 — лонг", sig is not None and sig["side"] == "Buy")

# Широкий спред отсекается даже при пробое
check("широкий спред → None", s6.evaluate_s6("T", k_t1, OB_WIDE, [], NOW, OI_POS) is None)

# T1 без подтверждения OI → не T1 (и ничего больше)
check("нет OI → None", s6.evaluate_s6("T", k_t1, OB_OK, [], NOW, OI_NONE) is None)

# T3: имбаланс L1 3.0 + лента покупок 5:1
ob_t3 = {"b": [["100.00", "3000"]], "a": [["100.02", "1000"]]}
trades = ([{"time": NOW, "size": "10", "price": "100", "side": "Buy"}] * 5 +
          [{"time": NOW, "size": "10", "price": "100", "side": "Sell"}] * 1)
sig3 = s6.evaluate_s6("T", kl([100.0] * 25), ob_t3, trades, NOW, OI_NONE)
check("T3 срабатывает (имбаланс+лента)", sig3 is not None and sig3["trigger"] == "T3")
check("T3 — лонг", sig3 is not None and sig3["side"] == "Buy")

# Флэт без ленты → None
check("флэт → None", s6.evaluate_s6("T", kl([100.0] * 25), OB_OK, [], NOW, OI_NONE) is None)

# Мало истории → None
check("мало истории → None", s6.evaluate_s6("T", kl([100.0] * 10), OB_OK, [], NOW, OI_NONE) is None)

# Старая лента (вне окна 10 с) не считается
old = [{"time": NOW - 60_000, "size": "10", "price": "100", "side": "Buy"}] * 6
check("старая лента → None", s6.evaluate_s6("T", kl([100.0] * 25), ob_t3, old, NOW, OI_NONE) is None)

print("ИТОГО: %d проверок, провалено %d" % (tests, fails))
sys.exit(1 if fails else 0)
