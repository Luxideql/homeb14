#!/usr/bin/env python3
"""Офлайн-тесты S7: фильтр кандидатов, инициализация виденных, очередь."""
import sys

import signals_s7 as s7

tests = 0
fails = 0


def check(name, cond):
    global tests, fails
    tests += 1
    if not cond:
        fails += 1
        print("FAIL:", name)


NOW = 1_700_000_000_000
MIN = 60_000


def inst(sym, launch_ms, ctype="LinearPerpetual", settle="USDT", status="Trading"):
    return {"symbol": sym, "contractType": ctype, "settleCoin": settle,
            "launchTime": str(launch_ms), "status": status}


instruments = [
    inst("NEWUSDT", NOW - 2 * MIN),            # листинг 2 мин назад — кандидат
    inst("SOONUSDT", NOW + 30_000),            # старт через 30 с — кандидат
    inst("OLDUSDT", NOW - 100 * 24 * 3600_000),  # старый — не кандидат
    inst("FARUSDT", NOW + 5 * MIN),            # старт через 5 мин — только очередь
    inst("BTC-25DEC26", NOW - 2 * MIN),        # датированный фьючерс (есть «-») — прочь
    inst("COINUSDC", NOW - 2 * MIN, settle="USDC"),  # не USDT — прочь
    inst("SEENUSDT", NOW - 2 * MIN),           # в seen — прочь
]
seen = {"SEENUSDT"}

cands = s7.filter_candidates(instruments, NOW, seen)
names = {c["symbol"] for c in cands}
check("кандидаты = NEW и SOON", names == {"NEWUSDT", "SOONUSDT"})
check("старый не кандидат", "OLDUSDT" not in names)
check("далёкий (5 мин) не кандидат", "FARUSDT" not in names)
check("датированный фьючерс отсеян", "BTC-25DEC26" not in names)
check("не-USDT отсеян", "COINUSDC" not in names)
check("уже виденный отсеян", "SEENUSDT" not in names)

est = s7.established_seen(instruments, NOW)
check("established: только старый", est == {"OLDUSDT"})

q = s7.upcoming_queue(instruments, NOW)
qnames = [x["symbol"] for x in q]
check("очередь = будущие по времени", qnames == ["SOONUSDT", "FARUSDT"])

print("ИТОГО: %d проверок, провалено %d" % (tests, fails))
sys.exit(1 if fails else 0)
