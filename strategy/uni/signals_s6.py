#!/usr/bin/env python3
"""S6-сигналы T1/T2/T3 (контур быстрого бота RISK v1.2). Реконструкция из спеки.

12 сверхликвидных монет, закрытые 1m-свечи + стакан L1 + лента сделок. Триггеры
(приоритет T1 > T2 > T3), лонг и шорт. Перед входом: спред ≤ 0.04%, подушка L1 ≥ $5000.

- T1 — пробой 5-мин диапазона с объёмом ≥ 2.5× и подтверждением OI.
- T2 — фейд: отклонение от VWAP15 ≥ 0.4% и ≥ 1.5·ATR14 + давление стакана ≥ 1.8×.
- T3 — имбаланс L1 ≥ 2.5 (≤ 0.4) + лента 10 с ≥ 1.6× (≥ 5 сделок, ≥ $5k).

Стоп S6 в текущем режиме — справочные 0.35% (реальный выход: стоп −$150 / трейлинг).
"""
import indicators as ind

SPREAD_MAX = 0.04        # %
CUSHION_MIN = 5000.0     # $ ликвидности на лучшем уровне
VOL_MULT = 2.5           # T1: объём ≥ 2.5×
VWAP_DEV = 0.4           # T2: отклонение ≥ 0.4%
ATR_MULT = 1.5           # T2: и ≥ 1.5·ATR
OB_PRESSURE = 1.8        # T2: давление стакана ≥ 1.8×
IMB_LONG = 2.5           # T3: имбаланс для лонга
IMB_SHORT = 0.4          # T3: имбаланс для шорта
TAPE_RATIO = 1.6         # T3: перевес ленты ≥ 1.6×
TAPE_MIN_TRADES = 5
TAPE_MIN_VALUE = 5000.0
TAPE_WINDOW_MS = 10_000
S6_STOP_PCT = 0.35       # справочный стоп


def _l1(ob):
    """Из стакана: (bid_px, bid_sz, ask_px, ask_sz) или None."""
    b = ob.get("b") or []
    a = ob.get("a") or []
    if not b or not a:
        return None
    try:
        return float(b[0][0]), float(b[0][1]), float(a[0][0]), float(a[0][1])
    except (IndexError, ValueError, TypeError):
        return None


def _gates(l1):
    """Спред ≤ 0.04% и подушка L1 ≥ $5000 с обеих сторон."""
    bpx, bsz, apx, asz = l1
    if bpx <= 0 or apx <= 0:
        return False
    spread = (apx - bpx) / ((apx + bpx) / 2.0) * 100.0
    if spread > SPREAD_MAX:
        return False
    return bpx * bsz >= CUSHION_MIN and apx * asz >= CUSHION_MIN


def _tape(trades, now_ms):
    """Лента за 10 с: (buy_vol, sell_vol, count, value$)."""
    bv = sv = val = 0.0
    cnt = 0
    for tr in trades:
        try:
            ts = int(tr.get("time", 0))
            sz = float(tr.get("size", 0))
            px = float(tr.get("price", 0))
        except (TypeError, ValueError):
            continue
        if now_ms - ts > TAPE_WINDOW_MS:
            continue
        cnt += 1
        val += sz * px
        if tr.get("side") == "Buy":
            bv += sz
        else:
            sv += sz
    return bv, sv, cnt, val


def evaluate_s6(sym, k1m, ob, trades, now_ms, oi_fn):
    """Чистая логика S6. oi_fn(interval_time, lookback) -> % или None."""
    o = ind.ohlcv(k1m)
    if len(o["c"]) < 21:
        return None
    l1 = _l1(ob)
    if not l1 or not _gates(l1):
        return None
    bpx, bsz, apx, asz = l1
    c = o["c"]
    close = c[-1]
    if close <= 0:
        return None
    atr = ind.atr(o["h"], o["l"], o["c"], 14) or 0.0
    atr_pct = atr / close * 100.0
    vwap15 = ind.vwap(o["h"], o["l"], o["c"], o["v"], 15)
    rvol = ind.rvol(o["v"], 20) or 0.0
    imb = (bsz / asz) if asz > 0 else 999.0

    # T1 — пробой 5-мин диапазона + объём + OI
    hi5 = max(o["h"][-6:-1])
    lo5 = min(o["l"][-6:-1])
    if rvol >= VOL_MULT:
        if close > hi5 and (oi_fn("5min", 1) or 0) > 0:
            return {"trigger": "T1", "side": "Buy", "sl_pct": S6_STOP_PCT, "score": rvol, "close": close}
        if close < lo5 and (oi_fn("5min", 1) or 0) > 0:
            return {"trigger": "T1", "side": "Sell", "sl_pct": S6_STOP_PCT, "score": rvol, "close": close}

    # T2 — фейд от VWAP15 + давление стакана
    if vwap15:
        dev = (close - vwap15) / vwap15 * 100.0
        if dev >= VWAP_DEV and dev >= ATR_MULT * atr_pct and asz > 0 and (asz / bsz) >= OB_PRESSURE:
            return {"trigger": "T2", "side": "Sell", "sl_pct": S6_STOP_PCT, "score": abs(dev), "close": close}
        if dev <= -VWAP_DEV and dev <= -ATR_MULT * atr_pct and bsz > 0 and (bsz / asz) >= OB_PRESSURE:
            return {"trigger": "T2", "side": "Buy", "sl_pct": S6_STOP_PCT, "score": abs(dev), "close": close}

    # T3 — имбаланс L1 + лента
    bv, sv, cnt, val = _tape(trades, now_ms)
    if cnt >= TAPE_MIN_TRADES and val >= TAPE_MIN_VALUE:
        if imb >= IMB_LONG and sv > 0 and (bv / sv) >= TAPE_RATIO:
            return {"trigger": "T3", "side": "Buy", "sl_pct": S6_STOP_PCT, "score": bv / sv, "close": close}
        if imb <= IMB_SHORT and bv > 0 and (sv / bv) >= TAPE_RATIO:
            return {"trigger": "T3", "side": "Sell", "sl_pct": S6_STOP_PCT, "score": sv / bv, "close": close}
    return None


def s6_universe(client, imap=None, size=12):
    """12 сверхликвидных: топ по обороту среди торгуемых USDT-перпов."""
    import universe
    imap = imap or universe.instrument_map(client)
    rows = []
    for t in client.tickers("linear"):
        sym = t.get("symbol")
        if imap.get(sym, {}).get("status") != "Trading" or sym in universe.BLOCKLIST:
            continue
        try:
            rows.append((float(t.get("turnover24h", 0) or 0), sym))
        except (TypeError, ValueError):
            continue
    rows.sort(reverse=True)
    return [s for _, s in rows[:size]]


def scan_s6(client, now_ms, imap=None, top=2):
    syms = s6_universe(client, imap)
    out = []
    for sym in syms:
        try:
            k1m = client.kline(sym, "1", 30)
            ob = client.orderbook(sym, 1)
            trades = client.recent_trades(sym, 60)
        except Exception:
            continue

        def oi_fn(it, lb, _s=sym):
            try:
                lst = client.open_interest(_s, it, limit=lb + 1)
                if len(lst) <= lb:
                    return None
                a, b = float(lst[0]["openInterest"]), float(lst[lb]["openInterest"])
                return ((a / b - 1.0) * 100.0) if b > 0 else None
            except Exception:
                return None

        sig = evaluate_s6(sym, k1m, ob, trades, now_ms, oi_fn)
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
    print("S6 вселенная (12):", ", ".join(s6_universe(c)))
    sigs = scan_s6(c, now)
    print("S6 сигналов (≤2):", len(sigs))
    for s in sigs:
        print("  %s %s %s | скор %.2f" % (s["symbol"], s["trigger"], s["side"], s["score"]))
    if not sigs:
        print("  (сейчас ни один триггер не сработал — это норма)")
