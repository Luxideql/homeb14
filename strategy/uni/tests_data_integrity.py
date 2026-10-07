#!/usr/bin/env python3
"""Тесты честности данных: реконструкция экскурсий (money.excursions) и
восстановление источника при усыновлении (uni_cycle.pick_source / ts_to_ms)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import money
import uni_cycle as u

PASS = 0
FAIL = 0


def check(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("  ПРОВАЛ:", name)


def eq3(t, a, b, c):
    return t == (a, b, c)


# ── money.excursions ─────────────────────────────────────────────────────────
T = [1000, 2000, 3000]
H = [101, 105, 102]
L = [99, 98, 100]
# лонг: пик по high (105→+50), дно по low (98→−20), MAE (100-98)/100=2%
check(eq3(money.excursions(T, H, L, "Buy", 100, 10, 0.0, 0.0), 50.0, -20.0, 2.0), "excursions лонг")
# шорт: пик по low (98→+20), дно по high (105→−50), MAE (105-100)/100=5%
check(eq3(money.excursions(T, H, L, "Sell", 100, 10, 0.0, 0.0), 20.0, -50.0, 5.0), "excursions шорт")
# отсечка по времени входа: только последний бар (time 3000)
check(eq3(money.excursions(T, H, L, "Buy", 100, 10, 0.0, 0.0, cutoff_ms=2500), 20.0, 0.0, 0.0), "excursions cutoff")
# все бары до входа → пусто
check(money.excursions(T, H, L, "Buy", 100, 10, 0.0, 0.0, cutoff_ms=9999) == (None, None, None), "excursions всё отсечено")
# комиссии учитываются: 1 бар flat@110, fee_in 1, rate 0.001 → net=100-1-1.1=97.9
check(money.excursions([1000], [110], [110], "Buy", 100, 10, 1.0, 0.001)[0] == 97.9, "excursions с комиссиями")
# защита от мусора
check(money.excursions(T, H, L, "Buy", 0, 10, 0.0, 0.0) == (None, None, None), "excursions entry=0")
check(money.excursions(T, H, L, "Buy", 100, 0, 0.0, 0.0) == (None, None, None), "excursions qty=0")

# ── uni_cycle.pick_source ────────────────────────────────────────────────────
ROWS = [
    {"symbol": "SEIUSDT", "side": "Sell", "source": "TREND", "scenario": "DON", "time_utc": "2026-10-07T01:00:00"},
    {"symbol": "SEIUSDT", "side": "Sell", "source": "MAIN", "scenario": "B", "time_utc": "2026-10-07T02:00:00"},
    {"symbol": "ADAUSDT", "side": "Buy", "source": "RISK", "scenario": "S3", "time_utc": "2026-10-07T03:00:00"},
    {"symbol": "XRPUSDT", "side": "Buy", "scenario": "", "time_utc": ""},  # без source
]
check(u.pick_source(ROWS, "SEIUSDT", "Sell") == ("MAIN", "B", "2026-10-07T02:00:00"), "pick_source берёт последний матч")
check(u.pick_source(ROWS, "ADAUSDT", "Buy") == ("RISK", "S3", "2026-10-07T03:00:00"), "pick_source RISK")
check(u.pick_source(ROWS, "SEIUSDT", "Buy") is None, "pick_source сторона не совпала → None")
check(u.pick_source(ROWS, "DOGEUSDT", "Buy") is None, "pick_source нет монеты → None")
check(u.pick_source(ROWS, "XRPUSDT", "Buy") == ("ADOPT", "?", ""), "pick_source пустой source → дефолт")

# ── uni_cycle.ts_to_ms ───────────────────────────────────────────────────────
check(u.ts_to_ms("1970-01-01T00:00:00") == 0, "ts_to_ms эпоха = 0")
check(u.ts_to_ms("2026-10-07T00:00:00") == 1791331200000, "ts_to_ms конкретная дата")
check(u.ts_to_ms("bad") is None, "ts_to_ms мусор → None")
check(u.ts_to_ms("") is None, "ts_to_ms пусто → None")

# ── uni_cycle.downtime_min (алерт о простое) ─────────────────────────────────
check(u.downtime_min(0, 1000) is None, "downtime нет прошлого тика → None")
check(u.downtime_min(1000, 1000 + 100, 300) is None, "downtime разрыв 100с < 300 → None")
check(u.downtime_min(1000, 1000 + 600, 300) == 10.0, "downtime разрыв 600с → 10 мин")

# ── uni_cycle.is_primary (guard от двойной торговли) ─────────────────────────
check(u.is_primary("pi4home", "pi4home") is True, "is_primary хост совпал → активен")
check(u.is_primary("homeb14", "pi4home") is False, "is_primary не совпал → бездействие")
check(u.is_primary("homeb14", "") is True, "is_primary главный не задан → обратная совместимость")

print("тесты: %d проверок, провалено %d" % (PASS + FAIL, FAIL))
sys.exit(1 if FAIL else 0)
