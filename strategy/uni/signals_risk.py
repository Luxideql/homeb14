#!/usr/bin/env python3
"""RISK-сигналы S1–S5 (контур RISK v1.1). Реконструкция из спеки, не оригинал.

Только лонг. Пул строит universe.screen (топ-60). По монете проверяются сценарии в
порядке приоритета S2 > S3 > S1 > S4 > S5 — первый сработавший, затем топ-4 по скору.

Данные: 15m (Donchian20, BB, rVOL), 1h (EMA50 фон, 12ч максимум), 5m (бар, rVOL),
OI-история и фандинг. OI запрашивается лениво — только когда прочие условия сценария
уже прошли. Стопы — ATR-based (оригинальные формулы RISK недоступны), в текущем
режиме это лишь справочный initial_stop_pct (реальный выход — стоп −$150 / трейлинг).
"""
import indicators as ind
import universe

STOP_MIN, STOP_MAX = 0.3, 15.0


def _clamp(p):
    return max(STOP_MIN, min(STOP_MAX, p))


def _oi_change(client, symbol, interval_time, lookback_idx):
    """Изменение OI в % между сейчас и lookback_idx баров назад. None при нехватке."""
    try:
        lst = client.open_interest(symbol, interval_time, limit=lookback_idx + 1)
    except Exception:
        return None
    if len(lst) <= lookback_idx:
        return None
    try:
        now_oi = float(lst[0]["openInterest"])
        old_oi = float(lst[lookback_idx]["openInterest"])
    except (KeyError, ValueError, TypeError):
        return None
    return ((now_oi / old_oi - 1.0) * 100.0) if old_oi > 0 else None


def evaluate(sym, k15, k1h, k5, funding, age_days, oi_fn):
    """Чистая логика S1–S5. oi_fn(interval_time, lookback) -> % или None (ленивый OI)."""
    o15, o1, o5 = ind.ohlcv(k15), ind.ohlcv(k1h), ind.ohlcv(k5)
    if len(o15["c"]) < 25 or len(o1["c"]) < 50 or len(o5["c"]) < 10:
        return None
    c15, c1, c5 = o15["c"], o1["c"], o5["c"]
    price = c15[-1]
    if price <= 0:
        return None
    ema50_1h = ind.ema(c1, 50)
    bg_up = ema50_1h is not None and c1[-1] > ema50_1h
    atr15 = ind.atr(o15["h"], o15["l"], o15["c"], 14) or 0.0
    atr1h = ind.atr(o1["h"], o1["l"], o1["c"], 14) or 0.0
    atr5 = ind.atr(o5["h"], o5["l"], o5["c"], 14) or 0.0
    rvol15 = ind.rvol(o15["v"], 20) or 0.0
    rvol5 = ind.rvol(o5["v"], 5) or 0.0

    # S2 — фандинг-сквиз (приоритет 1)
    if funding <= -0.005:
        oi24 = oi_fn("1h", 24)
        if oi24 is not None and oi24 >= 10.0 and len(o1["h"]) >= 13:
            high12 = max(o1["h"][-13:-1])
            if c1[-1] > high12:
                return {"scenario": "S2", "side": "Buy",
                        "sl_pct": _clamp(2.0 * atr1h / price * 100.0),
                        "score": abs(funding) * 1000.0 + oi24, "close": price}

    # S3 — продолжение после листинга (приоритет 2)
    if 7 <= age_days <= 45 and bg_up:
        mom6 = ind.pct_change(c1, 6) or 0.0
        if mom6 > 0:
            return {"scenario": "S3", "side": "Buy",
                    "sl_pct": _clamp(2.0 * atr1h / price * 100.0),
                    "score": mom6, "close": price}

    # S1 — памп-моментум (приоритет 3)
    du, _dl = ind.donchian_prev(o15["h"], o15["l"], 20)
    if du and c15[-1] > du and bg_up and rvol15 >= 2.0:
        oi15 = oi_fn("5min", 3)
        if oi15 is not None and oi15 > 0:
            return {"scenario": "S1", "side": "Buy",
                    "sl_pct": _clamp(1.5 * atr15 / price * 100.0),
                    "score": rvol15, "close": price}

    # S4 — short-squeeze по падению OI (приоритет 4)
    bar5 = ind.pct_change(c5, 1) or 0.0
    if bar5 >= 4.0 and rvol5 >= 3.0:
        oi15 = oi_fn("5min", 3)
        if oi15 is not None and oi15 <= -3.0:
            return {"scenario": "S4", "side": "Buy",
                    "sl_pct": _clamp(1.5 * atr5 / price * 100.0),
                    "score": rvol5, "close": price}

    # S5 — пробой сжатия Боллинджера на 15m (приоритет 5)
    if len(c15) >= 30:
        bb_now = ind.bollinger(c15, 20, 2.0)
        bb_prev = ind.bollinger(c15[:-1], 20, 2.0)
        bb_old = ind.bollinger(c15[:-10], 20, 2.0)
        if bb_now and bb_prev and bb_old:
            squeeze = bb_now["bandwidth"] < bb_old["bandwidth"] * 0.8
            breakout = c15[-1] > bb_now["upper"] and c15[-2] <= bb_prev["upper"]
            if squeeze and breakout and bg_up:
                return {"scenario": "S5", "side": "Buy",
                        "sl_pct": _clamp(1.5 * atr15 / price * 100.0),
                        "score": (c15[-1] - bb_now["upper"]) / bb_now["upper"] * 100.0,
                        "close": price}
    return None


def scan_symbol_risk(client, sym, info, ticker, now_ms):
    try:
        k15 = client.kline(sym, "15", 50)
        k1h = client.kline(sym, "60", 50)
        k5 = client.kline(sym, "5", 30)
    except Exception:
        return None
    try:
        funding = float(ticker.get("fundingRate", 0) or 0)
    except (TypeError, ValueError):
        funding = 0.0
    launch = info.get("launchTime", 0) or 0
    age_days = (now_ms - launch) / 86_400_000.0 if launch else 999.0

    def oi_fn(interval_time, lookback):
        return _oi_change(client, sym, interval_time, lookback)

    return evaluate(sym, k15, k1h, k5, funding, age_days, oi_fn)


def scan_risk(client, now_ms, imap=None, top=4, pool_limit=None):
    """Скрининг пула и S1–S5. pool_limit — ограничить число монет (для тестов)."""
    imap = imap or universe.instrument_map(client)
    pool = universe.screen(client, now_ms, imap)
    if pool_limit:
        pool = pool[:pool_limit]
    tickers = {t["symbol"]: t for t in client.tickers("linear")}
    out = []
    for sym in pool:
        t = tickers.get(sym)
        if not t:
            continue
        sig = scan_symbol_risk(client, sym, imap.get(sym, {}), t, now_ms)
        if sig:
            sig["symbol"] = sym
            out.append(sig)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:top]


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    import bybit_bot
    bybit_bot.load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    c = bybit_bot.Client()
    now = c.server_time_ms()
    sigs = scan_risk(c, now, pool_limit=12)
    print("RISK сигналов (топ-4, пул урезан до 12 для скорости):", len(sigs))
    for s in sigs:
        print("  %s %s %s | стоп %.2f%% | скор %.2f" % (s["symbol"], s["scenario"], s["side"], s["sl_pct"], s["score"]))
    if not sigs:
        print("  (сейчас ни один сценарий не сработал — это норма)")
