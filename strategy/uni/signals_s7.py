#!/usr/bin/env python3
"""S7 — детекция листингов с первой минуты. Реконструкция из спеки.

Каждый тик: свежий справочник linear (Trading + PreLaunch). Кандидат —
LinearPerpetual, settleCoin USDT, без «-» в символе (датированные фьючерсы прочь),
launchTime ∈ [now − WINDOW, now + 60с], символ не в s7_seen. При первом запуске все
контракты с launchTime < now − WINDOW помечаются виденными (чтобы не скупать всё).

Здесь только ДЕТЕКЦИЯ и файл виденных. Сам вход (рыночный ордер, плечо min(10,maxLev),
до 3 ордеров, ожидание до launchTime) — в слое исполнения uni_cycle.
"""
import json
import os

WINDOW_MIN = 10
WINDOW_MS = WINDOW_MIN * 60 * 1000
AHEAD_MS = 60 * 1000       # ловим и те, что стартуют в ближайшие 60 с


def is_perp_usdt(it):
    return (it.get("contractType") == "LinearPerpetual"
            and it.get("settleCoin") == "USDT"
            and "-" not in str(it.get("symbol", "")))


def _launch(it):
    try:
        return int(it.get("launchTime", "0") or 0)
    except (TypeError, ValueError):
        return 0


def filter_candidates(instruments, now_ms, seen, window_ms=WINDOW_MS):
    """Кандидаты на вход S7 (чистая функция)."""
    out = []
    for it in instruments:
        if not is_perp_usdt(it):
            continue
        sym = it["symbol"]
        if sym in seen:
            continue
        lt = _launch(it)
        if lt <= 0:
            continue
        if now_ms - window_ms <= lt <= now_ms + AHEAD_MS:
            out.append({"symbol": sym, "launchTime": lt, "status": it.get("status")})
    out.sort(key=lambda x: x["launchTime"])
    return out


def established_seen(instruments, now_ms, window_ms=WINDOW_MS):
    """Символы, уже давно торгующиеся (launchTime < now − WINDOW) — пометить виденными."""
    seen = set()
    for it in instruments:
        if not is_perp_usdt(it):
            continue
        lt = _launch(it)
        if lt and lt < now_ms - window_ms:
            seen.add(it["symbol"])
    return seen


def upcoming_queue(instruments, now_ms, limit=5):
    """Очередь листингов: USDT-перпы с launchTime в будущем (для сводки)."""
    future = [(_launch(it), it["symbol"]) for it in instruments
              if is_perp_usdt(it) and _launch(it) > now_ms]
    future.sort()
    return [{"symbol": s, "launchTime": lt} for lt, s in future[:limit]]


def load_seen(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError):
        return set()


def save_seen(path, seen):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sorted(seen), fh)
    os.replace(tmp, path)


def all_instruments(client):
    """Полный справочник: Trading + PreLaunch, без дублей."""
    out = {}
    for it in client.instruments_all(category="linear"):
        out[it["symbol"]] = it
    try:
        pre = client.instruments_info(category="linear", status="PreLaunch").get("list", [])
        for it in pre:
            out[it["symbol"]] = it
    except Exception:
        pass
    return list(out.values())


def detect(client, now_ms, seen_path):
    """Возвращает (кандидаты, очередь). Инициализирует seen при первом запуске."""
    instruments = all_instruments(client)
    first_run = not os.path.exists(seen_path)
    seen = load_seen(seen_path)
    if first_run:
        seen = established_seen(instruments, now_ms)
        save_seen(seen_path, seen)
    cands = filter_candidates(instruments, now_ms, seen)
    queue = upcoming_queue(instruments, now_ms)
    return cands, queue


if __name__ == "__main__":
    import sys
    import time
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    import bybit_bot
    here = os.path.dirname(os.path.abspath(__file__))
    bybit_bot.load_env_file(os.path.join(here, ".env"))
    c = bybit_bot.Client()
    now = c.server_time_ms()
    seen_path = os.path.join(here, "s7_seen.json")
    cands, queue = detect(c, now, seen_path)
    print("виденных в s7_seen.json:", len(load_seen(seen_path)))
    print("кандидатов на вход сейчас:", len(cands))
    for x in cands:
        print("  ", x["symbol"], "launch", time.strftime("%H:%M:%S UTC", time.gmtime(x["launchTime"] / 1000)))
    print("очередь листингов:", len(queue))
    for x in queue:
        print("  ", x["symbol"], "->", time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(x["launchTime"] / 1000)))
