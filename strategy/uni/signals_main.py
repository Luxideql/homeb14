#!/usr/bin/env python3
"""MAIN-сигналы A/B/C/D (контур R5h). Реконструкция из спеки, не оригинал.

Вход: закрытые свечи 1h и 4h + тикер. На выходе — первый сработавший сценарий по
монете (порядок A→B→C→D), затем топ-3 по скору. Жёсткие фильтры: спред ≤ 0.20%,
достаточная история; BTC-режим по EMA50(4h) для A; гейт шортов (импульс 5д ≤ −12%
и фандинг > 0 → шорты запрещены); стоп сценария вне 0.3–12% → сигнал отброшен.

Определения импульсов/трендов — обоснованные (оригинальный код недоступен):
импульс 5д = изм. 1h-закрытий за 120 баров; импульс 4h = изм. за 1 бар 4h;
импульс «5д по 4h» = изм. 4h-закрытий за 30 баров; тренд 4h вверх = close4h > EMA50(4h).
"""
import time

import indicators as ind
import universe

SPREAD_MAX = 0.20     # %
ST_BUFFER = 0.002     # 0.2% цены — буфер стопа Supertrend (сценарий A)
STOP_MIN, STOP_MAX = 0.3, 12.0   # допустимый стоп сценария, %


def _spread_pct(t):
    try:
        bid = float(t.get("bid1Price", 0) or 0)
        ask = float(t.get("ask1Price", 0) or 0)
    except (TypeError, ValueError):
        return 99.0
    return ((ask - bid) / ((ask + bid) / 2.0) * 100.0) if bid > 0 and ask > 0 else 99.0


def _sl_pct(close, stop_price):
    return abs(close - stop_price) / close * 100.0 if close else 999.0


def scan_symbol(sym, k1h, k4h, ticker, btc_up, now_hour):
    """Возвращает dict сигнала (scenario/side/sl_pct/score/close) или None."""
    o1 = ind.ohlcv(k1h)
    o4 = ind.ohlcv(k4h)
    if len(o1["c"]) < 200 or len(o4["c"]) < 31:
        return None
    if _spread_pct(ticker) > SPREAD_MAX:
        return None
    c1, close = o1["c"], o1["c"][-1]
    ema200 = ind.ema(c1, 200)
    ema50_4h = ind.ema(o4["c"], 50)
    if ema200 is None or ema50_4h is None or close <= 0:
        return None
    atr1h = ind.atr(o1["h"], o1["l"], o1["c"], 14) or 0.0
    atr4h = ind.atr(o4["h"], o4["l"], o4["c"], 14) or 0.0
    atr1h_pct = atr1h / close * 100.0
    atr4h_pct = atr4h / o4["c"][-1] * 100.0
    mom5d = ind.pct_change(c1, 120) or 0.0
    mom4h = ind.pct_change(o4["c"], 1) or 0.0
    mom5d_4h = ind.pct_change(o4["c"], 30) or 0.0
    trend4h_up = o4["c"][-1] > ema50_4h
    sma20 = ind.sma(c1, 20)
    try:
        funding = float(ticker.get("fundingRate", 0) or 0)
    except (TypeError, ValueError):
        funding = 0.0
    shorts_forbidden = (mom5d <= -12.0 and funding > 0)

    def ok(p):
        return STOP_MIN <= p <= STOP_MAX

    # A — разворот Supertrend(10,4.0) на 1h
    d1, line = ind.supertrend(o1["h"], o1["l"], o1["c"], 10, 4.0)
    if d1[-1] is not None and d1[-2] is not None and d1[-1] != d1[-2] and atr1h_pct < 2.0:
        if d1[-1] == 1 and close > ema200 and mom5d > 0 and btc_up:
            p = _sl_pct(close, line[-1] * (1 - ST_BUFFER))
            if ok(p):
                return {"scenario": "A", "side": "Buy", "sl_pct": p, "score": abs(mom5d), "close": close}
        if d1[-1] == -1 and close < ema200 and mom5d < 0 and not btc_up and not shorts_forbidden:
            p = _sl_pct(close, line[-1] * (1 + ST_BUFFER))
            if ok(p):
                return {"scenario": "A", "side": "Sell", "sl_pct": p, "score": abs(mom5d), "close": close}

    # B — импульс 5д по 4h, только первый час после закрытия 4h-свечи
    if now_hour % 4 == 0 and abs(mom5d_4h) > 15.0 and atr4h_pct < 4.0:
        side = "Buy" if mom5d_4h > 0 else "Sell"
        aligned = (side == "Buy" and mom4h > 0) or (side == "Sell" and mom4h < 0)
        if aligned and not (side == "Sell" and shorts_forbidden):
            p = 2.5 * atr4h / close * 100.0
            if ok(p):
                return {"scenario": "B", "side": side, "sl_pct": p, "score": abs(mom5d_4h), "close": close}

    # C — откат по тренду 1h (RSI пересёк 40 снизу / 60 сверху)
    if atr1h_pct < 2.5:
        rsi_s = ind.rsi_series(c1, 14)
        if trend4h_up and mom4h > 0 and ind.crossed_above(rsi_s, 40):
            p = 3.0 * atr1h / close * 100.0
            if ok(p):
                return {"scenario": "C", "side": "Buy", "sl_pct": p, "score": 0.5, "close": close}
        if not trend4h_up and mom4h < 0 and ind.crossed_below(rsi_s, 60) and not shorts_forbidden:
            p = 3.0 * atr1h / close * 100.0
            if ok(p):
                return {"scenario": "C", "side": "Sell", "sl_pct": p, "score": 0.5, "close": close}

    # D — отклонение от SMA20(1h) ≥ 6% против тренда 4h, вход по тренду
    if sma20:
        dev = (close - sma20) / sma20 * 100.0
        if abs(dev) >= 6.0 and abs(mom5d) >= 5.0:
            if dev < 0 and trend4h_up:
                p = 3.0 * atr1h / close * 100.0
                if ok(p):
                    return {"scenario": "D", "side": "Buy", "sl_pct": p, "score": abs(dev), "close": close}
            if dev > 0 and not trend4h_up and not shorts_forbidden:
                p = 3.0 * atr1h / close * 100.0
                if ok(p):
                    return {"scenario": "D", "side": "Sell", "sl_pct": p, "score": abs(dev), "close": close}
    return None


def scan_main(client, now_ms, imap=None, top=3):
    """Сканирует ядро 33, возвращает топ-N сигналов по скору."""
    now_hour = time.gmtime(now_ms / 1000.0).tm_hour
    tickers = {t["symbol"]: t for t in client.tickers("linear")}
    btc4 = ind.ohlcv(client.kline("BTCUSDT", "240", 60))
    btc_up = btc4["c"][-1] > (ind.ema(btc4["c"], 50) or btc4["c"][-1])
    out = []
    for sym in universe.core(client, imap):
        t = tickers.get(sym)
        if not t:
            continue
        try:
            k1 = client.kline(sym, "60", 200)
            k4 = client.kline(sym, "240", 60)
        except Exception:
            continue
        sig = scan_symbol(sym, k1, k4, t, btc_up, now_hour)
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
    print("час UTC:", time.gmtime(now / 1000.0).tm_hour)
    sigs = scan_main(c, now)
    print("MAIN сигналов (топ-3):", len(sigs))
    for s in sigs:
        print("  %s %s %s | стоп %.2f%% | скор %.2f | close %s"
              % (s["symbol"], s["scenario"], s["side"], s["sl_pct"], s["score"], s["close"]))
    if not sigs:
        print("  (сейчас ни один сценарий не сработал — это норма)")
