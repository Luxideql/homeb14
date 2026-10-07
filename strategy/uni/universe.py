#!/usr/bin/env python3
"""Вселенные контура UNI: ядро MAIN (CORE 33) и скрининг биржи для RISK.

CORE 33 — фиксированный список ликвидных USDT-перпов (точного оригинала в спеке
нет, взят обоснованный дефолт; на старте пересекается с живым справочником, так
что несуществующие/делистнутые отсекаются). screen() строит пул для S1–S5 по
фильтрам спеки (оборот/OI/спред/возраст) и возвращает топ по ликвидности.
"""

# Кандидаты ядра MAIN (33). Реальный набор = пересечение с инструментами Trading.
CORE_CANDIDATES = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT",
    "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "TRXUSDT",
    "WIFUSDT", "ATOMUSDT", "FILUSDT", "INJUSDT", "SEIUSDT", "TIAUSDT",
    "RUNEUSDT", "AAVEUSDT", "UNIUSDT", "ETCUSDT", "HBARUSDT", "ICPUSDT",
    "WLDUSDT", "1000PEPEUSDT", "ENAUSDT",
]


# Нестандартные перпы (товар/индекс/акции) — требуют согласия или ведут себя иначе.
BLOCKLIST = {"CLUSDT", "SOXLUSDT"}


def instrument_map(client):
    """symbol -> метаданные (фильтры размера/цены, плечо, листинг, статус)."""
    out = {}
    for it in client.instruments_all(category="linear"):
        if it.get("quoteCoin") != "USDT" or it.get("contractType") != "LinearPerpetual":
            continue
        lot = it.get("lotSizeFilter", {}) or {}
        pf = it.get("priceFilter", {}) or {}
        lev = it.get("leverageFilter", {}) or {}

        def f(d, k):
            try:
                return float(d.get(k, 0) or 0)
            except (TypeError, ValueError):
                return 0.0
        out[it["symbol"]] = {
            "status": it.get("status"),
            "tickSize": f(pf, "tickSize"),
            "qtyStep": f(lot, "qtyStep"),
            "minOrderQty": f(lot, "minOrderQty"),
            "maxOrderQty": f(lot, "maxOrderQty"),
            "maxMktOrderQty": f(lot, "maxMktOrderQty"),
            "minNotional": f(lot, "minNotionalValue"),
            "maxLeverage": f(lev, "maxLeverage"),
            "launchTime": int(it.get("launchTime", "0") or 0),
            "innovation": str(it.get("innovation", "0")) == "1",
        }
    return out


def core(client, imap=None):
    """Ядро MAIN: кандидаты, которые реально торгуются (status=Trading), в том же порядке."""
    imap = imap or instrument_map(client)
    return [s for s in CORE_CANDIDATES if imap.get(s, {}).get("status") == "Trading"]


def screen(client, now_ms, imap=None, min_age_days=7, pool_size=60):
    """Пул для RISK S1–S5: скрининг всей биржи по фильтрам спеки.

    Оборот ≥ $5M (листинг < 30 дн ≥ $2M), OI ≥ $1.5M (листинг ≥ $0.5M),
    спред ≤ 0.25%, возраст ≥ 7 дн, статус Trading. Innovation — штраф к скору.
    Скор = оборот24ч (прокси ликвидности). Возвращает топ-pool_size символов.
    """
    imap = imap or instrument_map(client)
    tickers = {t["symbol"]: t for t in client.tickers("linear")}
    scored = []
    for sym, info in imap.items():
        if info["status"] != "Trading" or sym in BLOCKLIST:
            continue
        t = tickers.get(sym)
        if not t:
            continue
        age_days = (now_ms - info["launchTime"]) / 86_400_000.0 if info["launchTime"] else 999.0
        if age_days < min_age_days:
            continue

        def f(k):
            try:
                return float(t.get(k, 0) or 0)
            except (TypeError, ValueError):
                return 0.0
        turnover = f("turnover24h")
        last = f("lastPrice")
        oi_val = f("openInterest") * last
        bid, ask = f("bid1Price"), f("ask1Price")
        spread = ((ask - bid) / ((ask + bid) / 2.0) * 100.0) if (bid > 0 and ask > 0) else 99.0
        new = age_days < 30
        if turnover < (2_000_000 if new else 5_000_000):
            continue
        if oi_val < (500_000 if new else 1_500_000):
            continue
        if spread > 0.25:
            continue
        score = turnover * (0.90 if info["innovation"] else 1.0)
        scored.append((score, sym))
    scored.sort(reverse=True)
    return [s for _, s in scored[:pool_size]]


if __name__ == "__main__":
    import os
    import sys
    import time
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    import bybit_bot
    bybit_bot.load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    c = bybit_bot.Client()
    im = instrument_map(c)
    print("инструментов linear USDT-перп:", len(im))
    cr = core(c, im)
    print("CORE реально торгуется: %d/%d" % (len(cr), len(CORE_CANDIDATES)))
    missing = [s for s in CORE_CANDIDATES if s not in cr]
    if missing:
        print("  нет в Trading:", ", ".join(missing))
    now = c.server_time_ms()
    pool = screen(c, now, im)
    print("пул RISK:", len(pool), "| топ-8:", ", ".join(pool[:8]))
