#!/usr/bin/env python3
"""Офлайн-тесты RISK-сигналов: проверяем срабатывание S3 и S2 и приоритет/лень OI."""
import sys

import signals_risk as sr

tests = 0
fails = 0


def check(name, cond):
    global tests, fails
    tests += 1
    if not cond:
        fails += 1
        print("FAIL:", name)


def kl(closes, up=0.2, dn=0.2):
    rows = [[str(i), str(c), str(c + up), str(c - dn), str(c), "100", "0"] for i, c in enumerate(closes)]
    return list(reversed(rows))


DAY = 86_400_000
NOW = 1_000_000_000_000

# 1h растущий ряд (фон вверх, mom6>0, последний максимум)
c1_up = [100.0 + 0.5 * i for i in range(50)]
k1_up = kl(c1_up)
k15_flat = kl([100.0] * 30)
k5_flat = kl([100.0] * 15)


def oi_raise(it, lb):
    raise AssertionError("oi_fn не должен вызываться для S3")


# S3: возраст 20 дн, фон вверх, mom6>0, фандинг 0 → S2 пропущен, OI не трогается
sig = sr.evaluate("T", k15_flat, k1_up, k5_flat, funding=0.0, age_days=20.0, oi_fn=oi_raise)
check("S3 срабатывает (листинг-континуэйшн)", sig is not None and sig["scenario"] == "S3")
check("S3 — лонг", sig is not None and sig["side"] == "Buy")
check("S3 без вызова OI (лень)", sig is not None)   # oi_raise не бросил → ок

# S2: фандинг −0.6%, OI24=+15%, пробой 12ч максимума → приоритетнее S3
def oi_s2(it, lb):
    return 15.0 if (it == "1h" and lb == 24) else None

sig2 = sr.evaluate("T", k15_flat, k1_up, k5_flat, funding=-0.006, age_days=20.0, oi_fn=oi_s2)
check("S2 срабатывает (фандинг-сквиз)", sig2 is not None and sig2["scenario"] == "S2")
check("S2 приоритетнее S3", sig2 is not None and sig2["scenario"] == "S2")

# Нет сигнала: нисходящий фон, фандинг 0, возраст большой
c1_dn = [150.0 - 0.5 * i for i in range(50)]
check("даунтренд/старый листинг → None",
      sr.evaluate("T", k15_flat, kl(c1_dn), k5_flat, 0.0, 300.0, lambda a, b: None) is None)

# Мало истории → None
check("мало истории → None",
      sr.evaluate("T", kl([100.0] * 10), kl([100.0] * 10), kl([100.0] * 5), 0.0, 20.0, lambda a, b: None) is None)

print("ИТОГО: %d проверок, провалено %d" % (tests, fails))
sys.exit(1 if fails else 0)
