#!/usr/bin/env python3
"""TREND — трендследование (Donchian-пробой + фильтр тренда + выравнивание по BTC).

Отдельный источник контура UNI с ПРАВИЛЬНЫМ риск/профит: редкие входы по пробою
канала на часовых свечах, вход только по тренду (EMA50) и в сторону BTC, ATR-стоп
и ШИРОКИЙ ATR-трейлинг (даём прибыли бежать). Это противоположность S6 (который
фиксировал профит на $17 и сливал на −$150).

Вход (на закрытой 1h-свече):
  LONG  — close > верх Donchian(20) предыдущих баров, close > EMA50 и EMA50 растёт,
          BTC не в даунтренде.
  SHORT — зеркально, BTC не в аптренде.
Размер и выход (ATR) считает money/uni_cycle: стоп 2.5·ATR, трейлинг 3·ATR,
активация в +1·ATR. Сигнал несёт atr для расчёта размера от риска % equity.
"""
import indicators as ind
import money

DONCHIAN_PERIOD = 20
EMA_PERIOD = 50
ATR_PERIOD = 14
STOP_MIN, STOP_MAX = 0.3, 15.0   # границы справочного initial_stop_pct


def _clamp(p):
    return max(STOP_MIN, min(STOP_MAX, p))


def evaluate_trend(k1h, btc_dir, donchian_period=DONCHIAN_PERIOD,
                   ema_period=EMA_PERIOD, atr_period=ATR_PERIOD,
                   stop_mult=money.TREND_STOP_ATR):
    """Чистая логика TREND по часовым свечам. btc_dir: +1 аптренд / −1 даунтренд / 0.

    Возвращает dict сигнала или None. k1h — свечи Bybit (новые сверху).
    """
    o = ind.ohlcv(k1h)
    c, h, l = o["c"], o["h"], o["l"]
    need = max(ema_period, donchian_period + 1, atr_period + 1) + 1
    if len(c) < need:
        return None
    price = c[-1]
    if price <= 0:
        return None

    ema_now = ind.ema(c, ema_period)
    ema_prev = ind.ema(c[:-1], ema_period)
    if ema_now is None or ema_prev is None:
        return None
    du, dl = ind.donchian_prev(h, l, donchian_period)   # канал ДО последнего бара
    if du is None or dl is None:
        return None
    atr_v = ind.atr(h, l, c, atr_period)
    if not atr_v or atr_v <= 0:
        return None

    up = price > ema_now and ema_now > ema_prev
    down = price < ema_now and ema_now < ema_prev
    sl_pct = _clamp(stop_mult * atr_v / price * 100.0)

    # LONG: пробой верха канала вверх, тренд вверх, BTC не против
    if price > du and up and btc_dir >= 0:
        return {"scenario": "DON", "side": "Buy", "sl_pct": sl_pct,
                "score": (price - du) / atr_v, "close": price, "atr": atr_v}
    # SHORT: пробой низа канала вниз, тренд вниз, BTC не против
    if price < dl and down and btc_dir <= 0:
        return {"scenario": "DON", "side": "Sell", "sl_pct": sl_pct,
                "score": (dl - price) / atr_v, "close": price, "atr": atr_v}
    return None


def _btc_dir(client, ema_period=EMA_PERIOD):
    """Направление BTC по 1h-EMA50 на ЗАКРЫТОЙ свече: +1 / −1 / 0."""
    try:
        k = client.kline("BTCUSDT", "60", ema_period + 7)
        if len(k) > 1:
            k = k[1:]                 # отбросить текущий (формирующийся) бар
        o = ind.ohlcv(k)
        c = o["c"]
        e = ind.ema(c, ema_period)
        if e is None:
            return 0
        if c[-1] > e:
            return 1
        if c[-1] < e:
            return -1
        return 0
    except Exception:
        return 0


def scan_trend(client, now_ms, imap=None, top=3):
    """Скан ядра (CORE) по тренду. Возвращает топ по силе пробоя (в ATR)."""
    import universe
    imap = imap or universe.instrument_map(client)
    syms = universe.core(client, imap)
    btc = _btc_dir(client)
    limit = max(EMA_PERIOD, DONCHIAN_PERIOD + 1, ATR_PERIOD + 1) + 7
    out = []
    for sym in syms:
        try:
            k1h = client.kline(sym, "60", limit)
        except Exception:
            continue
        if len(k1h) > 1:
            k1h = k1h[1:]             # вход по ЗАКРЫТОЙ свече — отбрасываем формирующийся бар
        sig = evaluate_trend(k1h, btc)
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
    print("BTC направление (1h EMA50):", _btc_dir(c))
    sigs = scan_trend(c, now)
    print("TREND сигналов (топ-3):", len(sigs))
    for s in sigs:
        print("  %s %s %s | стоп %.2f%% | сила %.2f ATR | ATR %.6f"
              % (s["symbol"], s["scenario"], s["side"], s["sl_pct"], s["score"], s["atr"]))
    if not sigs:
        print("  (сейчас пробоев по тренду нет — это норма)")
