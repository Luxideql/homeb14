#!/usr/bin/env python3
"""Офлайн-тесты источника TREND: риск-сайзинг, ATR-выходы, логика пробоя."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import money
import signals_trend as tr

PASS = 0
FAIL = 0


def check(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("  ПРОВАЛ:", name)


def approx(a, b, eps=1e-6):
    return a is not None and abs(a - b) <= eps


def klines(closes, spread=0.5, vol=1000.0):
    """Список закрытий (хронологически) → свечи Bybit [start,o,h,l,c,v,turn], новые сверху."""
    rows = []
    start = 1_700_000_000_000
    for i, c in enumerate(closes):
        rows.append([start + i * 3_600_000, c, c + spread, c - spread, c, vol, c * vol])
    return list(reversed(rows))


# ── размер от риска ───────────────────────────────────────────────────────────
# equity 100k, risk 0.5% = $500; stop_dist = 2.5·ATR
check(approx(money.trend_qty(100000, 100.0, 2.0, 1.0), 100.0), "trend_qty базовый (500/5=100)")
# потолок нотионала: atr мал → риск дал бы 400 (нотионал 40k), режем до 20k → qty 200
check(approx(money.trend_qty(100000, 100.0, 0.5, 1.0), 200.0), "trend_qty потолок нотионала")
check(money.trend_qty(100000, 100.0, 2.0, 1.0, min_qty=200.0) is None, "trend_qty отказ по min_qty")
check(money.trend_qty(100000, 100.0, 0.0, 1.0) is None, "trend_qty atr=0 → None")
check(money.trend_qty(0, 100.0, 2.0, 1.0) is None, "trend_qty equity=0 → None")

# ── ATR-выходы (tick=0 → без округления, проверяем чистую математику) ───────────
check(approx(money.atr_stop_price("Buy", 100.0, 2.0, 2.5, 0.0), 95.0), "atr_stop лонг 100-5")
check(approx(money.atr_stop_price("Sell", 100.0, 2.0, 2.5, 0.0), 105.0), "atr_stop шорт 100+5")
check(approx(money.atr_trail_distance(2.0, 3.0, 0.0), 6.0), "atr_trail дистанция 3·ATR")
check(approx(money.atr_activate_price("Buy", 100.0, 2.0, 1.0, 0.0), 102.0), "atr_activate лонг +1·ATR")
check(approx(money.atr_activate_price("Sell", 100.0, 2.0, 1.0, 0.0), 98.0), "atr_activate шорт −1·ATR")
check(money.atr_stop_price("Buy", 100.0, 0.0, 2.5, 0.0) is None, "atr_stop atr=0 → None")

# ── логика пробоя ───────────────────────────────────────────────────────────────
up = klines([100.0 + i for i in range(60)])          # строгий аптренд
down = klines([160.0 - i for i in range(60)])         # строгий даунтренд
flat = klines([100.0 + (0.05 if i % 2 else -0.05) for i in range(60)])  # флэт

s_long = tr.evaluate_trend(up, btc_dir=1)
check(s_long is not None and s_long["side"] == "Buy", "лонг на пробое вверх в аптренде (BTC up)")
check(s_long is not None and s_long["atr"] > 0 and s_long["scenario"] == "DON", "лонг несёт ATR и сценарий DON")

s_short = tr.evaluate_trend(down, btc_dir=-1)
check(s_short is not None and s_short["side"] == "Sell", "шорт на пробое вниз в даунтренде (BTC down)")

check(tr.evaluate_trend(up, btc_dir=-1) is None, "лонг заблокирован: BTC против (down)")
check(tr.evaluate_trend(down, btc_dir=1) is None, "шорт заблокирован: BTC против (up)")
check(tr.evaluate_trend(flat, btc_dir=0) is None, "флэт без пробоя → None")
check(tr.evaluate_trend(klines([100.0] * 10), btc_dir=1) is None, "мало данных → None")

print("тесты: %d проверок, провалено %d" % (PASS + FAIL, FAIL))
sys.exit(1 if FAIL else 0)
