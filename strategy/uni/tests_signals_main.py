#!/usr/bin/env python3
"""Офлайн-тесты MAIN-сигналов: проверяем, что логика входа реально срабатывает."""
import sys

import signals_main as sm

tests = 0
fails = 0


def check(name, cond):
    global tests, fails
    tests += 1
    if not cond:
        fails += 1
        print("FAIL:", name)


def kl(closes, hi_off=0.2, lo_off=0.2):
    """Строит свечи Bybit (новые сверху) из списка закрытий."""
    rows = []
    for i, c in enumerate(closes):
        rows.append([str(i), str(c), str(c + hi_off), str(c - lo_off), str(c), "100", "0"])
    return list(reversed(rows))


FAKE_TICKER = {"bid1Price": "100", "ask1Price": "100.05", "fundingRate": "0.0001"}

# --- Сценарий D (лонг): растущий тренд, но последний бар провалился > 6% под SMA20 ---
c1 = [100.0 + 0.3 * i for i in range(199)]   # плавный рост 100→159.4
c1.append(c1[-1] * 0.90)                      # резкий провал ~10% → dev < −6%
k1h = kl(c1)
c4 = [100.0 + 0.5 * i for i in range(60)]     # 4h тренд вверх (нужно ≥50 для EMA50)
k4h = kl(c4)
sig = sm.scan_symbol("TESTUSDT", k1h, k4h, FAKE_TICKER, btc_up=True, now_hour=1)
check("D срабатывает на провале под SMA в аптренде", sig is not None and sig["scenario"] == "D")
check("D — это лонг", sig is not None and sig["side"] == "Buy")
check("D стоп в диапазоне 0.3–12%", sig is not None and 0.3 <= sig["sl_pct"] <= 12.0)

# --- Нет сигнала: ровный флэт ---
flat = [100.0] * 200
k1f = kl(flat)
k4f = kl([100.0] * 60)
check("флэт → нет сигнала", sm.scan_symbol("X", k1f, k4f, FAKE_TICKER, True, 1) is None)

# --- Мало истории → None ---
check("мало истории → None", sm.scan_symbol("X", kl([100.0] * 50), kl([100.0] * 10),
                                            FAKE_TICKER, True, 1) is None)

# --- Широкий спред отсекается ---
wide = {"bid1Price": "100", "ask1Price": "101", "fundingRate": "0"}   # спред ~1%
check("широкий спред → None", sm.scan_symbol("X", k1h, k4h, wide, True, 1) is None)

print("ИТОГО: %d проверок, провалено %d" % (tests, fails))
sys.exit(1 if fails else 0)
