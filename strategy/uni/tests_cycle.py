#!/usr/bin/env python3
"""Офлайн-тесты helper'ов оркестратора: лимиты, классификация выхода, net, coin."""
import sys

import uni_cycle as uc
import money

tests = 0
fails = 0


def check(name, cond):
    global tests, fails
    tests += 1
    if not cond:
        fails += 1
        print("FAIL:", name)


# coin
check("coin BTCUSDT", uc.coin("BTCUSDT") == "BTC")
check("coin 1000PEPEUSDT", uc.coin("1000PEPEUSDT") == "1000PEPE")

# side_counts / can_open
st8b3s = {"positions": {("B%d" % i): {"side": "Buy"} for i in range(8)}, "pending": {}}
for i in range(3):
    st8b3s["positions"]["S%d" % i] = {"side": "Sell"}
b, s = uc.side_counts(st8b3s["positions"])
check("side_counts 8/3", b == 8 and s == 3)
check("can_open Buy при 8 лонгах → нет", uc.can_open(st8b3s, "Buy")[0] is False)
check("can_open Sell при 3 шортах → да", uc.can_open(st8b3s, "Sell")[0] is True)

full = {"positions": {("X%d" % i): {"side": "Buy" if i % 2 else "Sell"} for i in range(12)}, "pending": {}}
check("can_open при 12 позициях → нет", uc.can_open(full, "Sell")[0] is False)

near = {"positions": {("X%d" % i): {"side": "Sell"} for i in range(11)}, "pending": {"P": {}}}
check("can_open при 11+1 pending = 12 → нет", uc.can_open(near, "Buy")[0] is False)

# classify_exit (флагов нет → трейлинг активен, стопы активны)
check("выход net>0 → trailing_stop", uc.classify_exit({"add_count": 0}, 50.0) == "trailing_stop")
check("выход net≈−маржа → liquidation", uc.classify_exit({"add_count": 0}, -1000.0) == "liquidation")
check("выход net<0 малый → loss_stop", uc.classify_exit({"add_count": 0}, -50.0) == "loss_stop")
check("выход −1000 после 2 докупок → ещё loss_stop", uc.classify_exit({"add_count": 2}, -1000.0) == "loss_stop")

# net_at
st = {"side": "Buy", "entry_price": 100.0, "qty": 10.0, "fee_in": 1.0, "zone": "normal"}
check("net_at = money.net", abs(uc.net_at(st, 110.0) - money.net("Buy", 100.0, 10.0, 1.0, 110.0, money.TAKER_NORMAL)) < 1e-9)

print("ИТОГО: %d проверок, провалено %d" % (tests, fails))
sys.exit(1 if fails else 0)
