#!/usr/bin/env python3
"""Офлайн-тесты денег/стопов: сверка с примером BNB и round-trip цена↔net."""
import sys

import money as m

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


R = m.TAKER_NORMAL   # 0.00055

# --- Пример BNB (лонг): entry 612.50, qty 16.3 ---
entry, qty = 612.50, 16.3
fee_in = entry * qty * R   # 5.49106...

# TP +100 ≈ 619.3125 (спека)
tp = m.price_for_net("Buy", entry, qty, fee_in, 100.0, R)
check("BNB TP +100 ≈ 619.31", approx(tp, 619.3125, 0.02))
check("net в точке TP = +100", approx(m.net("Buy", entry, qty, fee_in, tp, R), 100.0, 1e-6))
# цена 614.40 → net ≈ +20 (спека)
check("BNB net@614.40 ≈ +20", approx(m.net("Buy", entry, qty, fee_in, 614.40, R), 20.0, 0.1))

# --- Стоп убытка −$150 (round-trip) ---
sp = m.loss_stop_price("Buy", entry, qty, fee_in, R, entries=1, tick=0.0)
check("стоп −150: net=−150", approx(m.net("Buy", entry, qty, fee_in, sp, R), -150.0, 1e-6))
check("стоп ниже входа (лонг)", sp < entry)
sp3 = m.loss_stop_price("Buy", entry, qty, fee_in, R, entries=3, tick=0.0)
check("стоп после 2 докупок: net=−450", approx(m.net("Buy", entry, qty, fee_in, sp3, R), -450.0, 1e-6))

# --- Трейлинг ---
dist, active = m.trailing_params("Buy", entry, qty, fee_in, R, cur_net=0.0, tick=0.0)
check("дистанция = 17/qty", approx(dist, 17.0 / qty, 1e-9))
check("активация: net=+30", approx(m.net("Buy", entry, qty, fee_in, active, R), 30.0, 1e-6))
dist2, active2 = m.trailing_params("Buy", entry, qty, fee_in, R, cur_net=40.0, tick=0.0)
check("уже в плюсе → активен сразу (active=None)", active2 is None and approx(dist2, 17.0 / qty, 1e-9))

# --- Лесенка ---
lad = m.ladder_stop("Buy", entry, qty, fee_in, cur_net=47.0, rate_out=R, prev_step=0, tick=0.0)
check("лесенка net 47 → ступень 40", lad and approx(lad["step"], 40.0))
check("лесенка защищает +20", lad and approx(lad["protected_net"], 20.0))
check("цена стопа лесенки: net=+23 (цель+буфер $3)",
      lad and approx(m.net("Buy", entry, qty, fee_in, lad["stop_price"], R), 23.0, 1e-6))
check("лесенка net 25 (<30) → None", m.ladder_stop("Buy", entry, qty, fee_in, 25.0, R, 0) is None)
check("ратчет: ступень не ниже prev → None", m.ladder_stop("Buy", entry, qty, fee_in, 45.0, R, 40) is None)
check("ратчет: новая ступень 50 > 40 → ок", (m.ladder_stop("Buy", entry, qty, fee_in, 55.0, R, 40) or {}).get("step") == 50.0)

# --- Шорт симметрия (round-trip) ---
spn = m.price_for_net("Sell", entry, qty, fee_in, 50.0, R)
check("шорт round-trip net=+50", approx(m.net("Sell", entry, qty, fee_in, spn, R), 50.0, 1e-6))
sls = m.loss_stop_price("Sell", entry, qty, fee_in, R, entries=1, tick=0.0)
check("шорт стоп выше входа", sls > entry and approx(m.net("Sell", entry, qty, fee_in, sls, R), -150.0, 1e-6))

# --- Размер ---
check("qty_for кратно шагу и ≤ нотионала", approx(m.qty_for(100.0, 0.001), 100.0) and 100.0 * 100.0 <= 10000.0 + 1e-6)
check("qty_for ниже minQty → None", m.qty_for(100.0, 0.001, min_qty=200.0) is None)
check("qty_for цена 0 → None", m.qty_for(0.0, 0.001) is None)

# --- Маржа ---
check("маржа 1100 на 0 входов — ок", m.free_margin_ok(1100.0, 0) is True)
check("маржа 1000 — мало", m.free_margin_ok(1000.0, 0) is False)
check("маржа 2200 на 1 вход — ок (IM 2000·1.1)", m.free_margin_ok(2200.0, 1) is True)
check("маржа 2100 на 1 вход — мало", m.free_margin_ok(2100.0, 1) is False)

print("ИТОГО: %d проверок, провалено %d" % (tests, fails))
sys.exit(1 if fails else 0)
