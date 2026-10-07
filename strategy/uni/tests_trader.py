#!/usr/bin/env python3
"""Офлайн-тесты форматирования trader (цена/количество по шагу)."""
import sys

import trader as tr

tests = 0
fails = 0


def check(name, cond):
    global tests, fails
    tests += 1
    if not cond:
        fails += 1
        print("FAIL:", name)


check("_decimals 0.0001 = 4", tr._decimals(0.0001) == 4)
check("_decimals 1.0 = 0", tr._decimals(1.0) == 0)
check("_decimals 0.5 = 1", tr._decimals(0.5) == 1)
check("_decimals 0 = 0", tr._decimals(0) == 0)
check("_fmt цены по тику", tr._fmt(0.2689, 0.0001) == "0.2689")
check("_fmt None → None", tr._fmt(None, 0.0001) is None)
check("_fmt_qty целое по шагу 1", tr._fmt_qty(371.0, 1.0) == "371")
check("_fmt_qty дробное по шагу 0.001", tr._fmt_qty(1.5, 0.001) == "1.500")

print("ИТОГО: %d проверок, провалено %d" % (tests, fails))
sys.exit(1 if fails else 0)
