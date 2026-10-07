#!/usr/bin/env python3
"""Деньги и стопы контура UNI: размер, net с комиссиями, стоп −$150, трейлинг, лесенка.

Чистые функции (без сети/состояния), чтобы математику можно было выверить тестами.
net(price) = pnl(price) − fee_in − fee_out(price), fee_out = price·qty·rate_out.
Формулы цены по целевому net (вывод из уравнения net):
  лонг:  price = (target + fee_in + entry·qty) / (qty·(1 − rate_out))
  шорт:  price = (entry·qty − target − fee_in) / (qty·(1 + rate_out))

Режим выхода (ред. 05.10): стоп убытка −$150 на вход (−$300/−$450 после докупок) +
биржевой трейлинг (активация +$30 net, дистанция $17 net). Лесенка — запасной режим.
"""
import math

NOTIONAL = 10000.0        # $ на один вход
LEVERAGE = 10
MARGIN_PER = NOTIONAL / LEVERAGE   # $1000 маржи на вход
LOSS_PER_ENTRY = 150.0    # стоп убытка, $ чистыми на вход
TP_NET = 100.0            # тейк (режим ENABLE_TP100)

TRAIL_ACTIVATE_NET = 30.0
TRAIL_DISTANCE_NET = 17.0

LADDER_START = 30.0       # net, с которого включается лесенка
LADDER_STEP = 10.0
LADDER_LAG = 20.0         # отставание стопа от достигнутой ступени
SLIP_BUFFER = 3.0         # буфер на проскальзывание, $ чистыми

# TREND (трендследование): размер от риска % equity, ATR-стоп и широкий ATR-трейлинг
TREND_RISK_PCT = 0.005        # доля equity под риск (до стопа) на один вход
TREND_MAX_NOTIONAL = 20000.0  # потолок нотионала на вход (ограничивает размер)
TREND_STOP_ATR = 2.5          # начальный стоп = 2.5·ATR от входа
TREND_TRAIL_ATR = 3.0         # дистанция трейлинга = 3·ATR (даём прибыли бежать)
TREND_ACTIVATE_ATR = 1.0      # трейлинг активируется при +1·ATR прибыли

# Ставки комиссии по зоне
TAKER_NORMAL, MAKER_NORMAL = 0.00055, 0.0002
TAKER_INNO, MAKER_INNO = 0.0011, 0.0004


def taker_rate(innovation=False):
    return TAKER_INNO if innovation else TAKER_NORMAL


def maker_rate(innovation=False):
    return MAKER_INNO if innovation else MAKER_NORMAL


def round_tick(price, tick, mode="nearest"):
    if not tick or tick <= 0:
        return price
    n = price / tick
    if mode == "up":
        return math.ceil(n) * tick
    if mode == "down":
        return math.floor(n) * tick
    return round(n) * tick


def qty_for(price, qty_step, min_qty=0.0, min_notional=0.0, notional=NOTIONAL):
    """Количество под целевой нотионал, кратное шагу. None — не влезает по минимумам."""
    if price <= 0 or qty_step <= 0:
        return None
    qty = math.floor((notional / price) / qty_step) * qty_step
    # аккуратно к шагу (уберём хвост float)
    qty = round(qty, 12)
    if qty <= 0 or (min_qty and qty < min_qty):
        return None
    if min_notional and qty * price < min_notional:
        return None
    return qty


def fee(value, rate):
    return value * rate


def pnl(side, entry, qty, price):
    return (price - entry) * qty if side == "Buy" else (entry - price) * qty


def net(side, entry, qty, fee_in, price, rate_out):
    """Чистый результат при цене price: pnl − комиссия входа − комиссия выхода."""
    return pnl(side, entry, qty, price) - fee_in - price * qty * rate_out


def price_for_net(side, entry, qty, fee_in, target_net, rate_out):
    """Цена, при которой net == target_net."""
    if qty <= 0:
        return None
    if side == "Buy":
        return (target_net + fee_in + entry * qty) / (qty * (1.0 - rate_out))
    return (entry * qty - target_net - fee_in) / (qty * (1.0 + rate_out))


def loss_stop_price(side, entry, qty, fee_in, rate_out, entries=1, tick=0.0):
    """Цена стопа убытка: net == −(150·entries). Округление от входа (не туже)."""
    target = -LOSS_PER_ENTRY * max(1, entries)
    p = price_for_net(side, entry, qty, fee_in, target, rate_out)
    if p is None:
        return None
    return round_tick(p, tick, "down" if side == "Buy" else "up")


def take_profit_price(side, entry, qty, fee_in, rate_out, tick=0.0):
    """Цена тейка +$100 (режим ENABLE_TP100). Округление в сторону прибыли."""
    p = price_for_net(side, entry, qty, fee_in, TP_NET, rate_out)
    if p is None:
        return None
    return round_tick(p, tick, "up" if side == "Buy" else "down")


def trailing_params(side, entry, qty, fee_in, rate_out, cur_net=0.0, tick=0.0):
    """Параметры биржевого трейлинга.

    Возвращает (distance_price, active_price): дистанция = $17 net / qty (к тику вверх);
    активация на цене net=+$30 (если net ещё < +30), иначе active_price=None (активен сразу).
    """
    dist = round_tick(TRAIL_DISTANCE_NET / qty, tick, "up") if qty > 0 else None
    active = None
    if cur_net < TRAIL_ACTIVATE_NET:
        ap = price_for_net(side, entry, qty, fee_in, TRAIL_ACTIVATE_NET, rate_out)
        active = round_tick(ap, tick, "up" if side == "Buy" else "down") if ap is not None else None
    return dist, active


def ladder_stop(side, entry, qty, fee_in, cur_net, rate_out, prev_step=0, tick=0.0):
    """Лесенка (запасной режим). Возвращает dict или None, если двигать стоп рано.

    net ≥ 30 → защищаем +10; ≥ 40 → +20 … (ступень step=floor(net/10)·10, защита step−20),
    цена ставится на буфер +$3 выше цели. Только вперёд (step должен превысить prev_step).
    """
    if cur_net < LADDER_START:
        return None
    step = math.floor(cur_net / LADDER_STEP) * LADDER_STEP
    if step <= prev_step:
        return None
    protected = step - LADDER_LAG
    p = price_for_net(side, entry, qty, fee_in, protected + SLIP_BUFFER, rate_out)
    if p is None:
        return None
    stop_price = round_tick(p, tick, "up" if side == "Buy" else "down")
    return {"step": step, "protected_net": protected, "stop_price": stop_price}


def free_margin_ok(available, open_entries=0):
    """Свободная маржа ≥ max($1100, 1.1·IM). IM = $1000 на каждый открытый вход + новый."""
    im = MARGIN_PER * (open_entries + 1)
    return available >= max(1100.0, 1.1 * im)


# ── TREND: размер от риска и ATR-выходы ──────────────────────────────────────

def trend_qty(equity, entry, atr, qty_step, min_qty=0.0, min_notional=0.0,
              risk_pct=TREND_RISK_PCT, stop_atr=TREND_STOP_ATR,
              max_notional=TREND_MAX_NOTIONAL):
    """Размер тренд-входа: qty = (equity·risk%) / (stop_atr·ATR), с потолком нотионала.

    Риск на сделку = расстояние до стопа (stop_atr·ATR) × qty ≈ equity·risk%.
    Кратно шагу, не ниже минимумов, не выше max_notional. None — не влезает.
    """
    if equity <= 0 or entry <= 0 or atr <= 0 or qty_step <= 0:
        return None
    stop_dist = stop_atr * atr
    if stop_dist <= 0:
        return None
    qty = (equity * risk_pct) / stop_dist
    cap_qty = max_notional / entry          # потолок нотионала
    if qty > cap_qty:
        qty = cap_qty
    qty = math.floor(qty / qty_step) * qty_step
    qty = round(qty, 12)
    if qty <= 0 or (min_qty and qty < min_qty):
        return None
    if min_notional and qty * entry < min_notional:
        return None
    return qty


def atr_stop_price(side, entry, atr, mult=TREND_STOP_ATR, tick=0.0):
    """Цена ATR-стопа: entry ∓ mult·ATR (округление не туже входа)."""
    if atr is None or atr <= 0:
        return None
    if side == "Buy":
        return round_tick(entry - mult * atr, tick, "down")
    return round_tick(entry + mult * atr, tick, "up")


def atr_trail_distance(atr, mult=TREND_TRAIL_ATR, tick=0.0):
    """Дистанция трейлинга в цене = mult·ATR (к тику вверх, не меньше тика)."""
    if atr is None or atr <= 0:
        return None
    d = round_tick(mult * atr, tick, "up")
    if tick and d < tick:
        d = tick
    return d


def atr_activate_price(side, entry, atr, mult=TREND_ACTIVATE_ATR, tick=0.0):
    """Цена активации трейлинга: entry ± mult·ATR в сторону прибыли."""
    if atr is None or atr <= 0:
        return None
    if side == "Buy":
        return round_tick(entry + mult * atr, tick, "up")
    return round_tick(entry - mult * atr, tick, "down")


# ── реконструкция экскурсий (достоверные max_net/min_net/MAE по свечам) ───────

def excursions(times, highs, lows, side, entry, qty, fee_in, rate_out, cutoff_ms=0):
    """MFE/MAE за удержание по барам: (max_net, min_net, mae_pct) или (None,None,None).

    Для лонга благоприятная цена = high, неблагоприятная = low; для шорта наоборот.
    Это честный пик/дно net вместо минутного сэмпла (который пропускал быстрые ходы).
    cutoff_ms — игнорировать бары раньше (время входа минус запас).
    """
    if entry <= 0 or qty <= 0:
        return None, None, None
    best = worst = None
    mae_pct = 0.0
    n = min(len(times), len(highs), len(lows))
    for i in range(n):
        if cutoff_ms and times[i] < cutoff_ms:
            continue
        hi, lo = highs[i], lows[i]
        if side == "Buy":
            fav, adv = hi, lo
            adv_pct = (entry - adv) / entry * 100.0
        else:
            fav, adv = lo, hi
            adv_pct = (adv - entry) / entry * 100.0
        nf = net(side, entry, qty, fee_in, fav, rate_out)
        na = net(side, entry, qty, fee_in, adv, rate_out)
        best = nf if best is None else max(best, nf)
        worst = na if worst is None else min(worst, na)
        if adv_pct > mae_pct:
            mae_pct = adv_pct
    if best is None:
        return None, None, None
    return round(best, 2), round(worst, 2), round(mae_pct, 3)
