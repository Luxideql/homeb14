#!/usr/bin/env python3
"""Слой исполнения UNI: настройка счёта, вход, стоп −$150, трейлинг, закрытие.

Использует клиент Bybit (bybit_bot) и чистую математику (money). Здесь — только
обёртки над ордерами; РЕШЕНИЕ входить/выходить принимает uni_cycle. orderLinkId
UNI: вход «uni<ts><COIN>», S6 «uni6_<T>_<COIN>_<ms>», закрытие «unicl<ts><COIN>».
"""
import time

import money
from money import taker_rate


class TradeError(Exception):
    pass


def ensure_account(client):
    """Изолированная маржа на уровне счёта + one-way. Возвращает заметки."""
    notes = []
    try:
        client.set_margin_mode("ISOLATED_MARGIN")
        notes.append("маржа ISOLATED: ок")
    except Exception as exc:
        notes.append("маржа ISOLATED: " + str(getattr(exc, "ret_msg", exc)))
    try:
        client.switch_position_mode(0, coin="USDT")
        notes.append("one-way: ок")
    except Exception as exc:
        notes.append("one-way: " + str(getattr(exc, "ret_msg", exc)))
    return notes


def ensure_leverage(client, symbol, lev=10):
    try:
        client.set_leverage(symbol, lev)
    except Exception as exc:
        if getattr(exc, "ret_code", None) != 110043:   # 110043 = leverage not modified
            raise


def wait_position(client, symbol, side=None, tries=8, pause=1.0):
    """Ждёт появления позиции после входа (avgPrice/size приходят в позиции сразу)."""
    for _ in range(tries):
        p = position(client, symbol)
        if p and (side is None or p.get("side") == side):
            return p
        time.sleep(pause)
    return None


def fee_of(client, symbol, order_id, tries=5, pause=2.0):
    """Фактическая комиссия ордера из execution/list (с задержкой). None — не пришло."""
    for _ in range(tries):
        execs = client.executions(symbol=symbol, order_id=order_id)
        if execs:
            return sum(float(e.get("execFee", 0) or 0) for e in execs)
        time.sleep(pause)
    return None


def entry_fee(client, symbol, order_id, value, innovation=False):
    """Комиссия входа: факт из execution, иначе расчёт value·тейкер (на demo совпадает)."""
    f = fee_of(client, symbol, order_id, tries=3, pause=1.5)
    return f if f is not None else value * taker_rate(innovation)


def enter(client, symbol, side, inst, ref_price, notional=money.NOTIONAL,
          order_type="Market", limit_price=None, post_only=False, order_link_id=None, qty=None):
    """Открывает/добавляет позицию. Возвращает (order_id, qty).

    qty задан (TREND, риск-сайзинг) → берём его (кратно шагу); иначе считаем от notional.
    """
    max_lev = inst.get("maxLeverage", 0)
    if max_lev and max_lev < money.LEVERAGE:
        raise TradeError("maxLeverage %s < %d" % (max_lev, money.LEVERAGE))
    ensure_leverage(client, symbol, int(min(money.LEVERAGE, max_lev)) if max_lev else money.LEVERAGE)
    if qty is not None:
        step = inst.get("qtyStep", 0)
        if step and step > 0:
            import math
            qty = round(math.floor(qty / step) * step, 12)
    else:
        qty = money.qty_for(ref_price, inst.get("qtyStep", 0), inst.get("minOrderQty", 0),
                            inst.get("minNotional", 0), notional)
    if not qty or qty <= 0:
        raise TradeError("qty не вычислен (минимумы/шаг) для %s @ %s" % (symbol, ref_price))
    max_oq = inst.get("maxOrderQty", 0)
    if max_oq and qty > max_oq:
        raise TradeError("qty %s > maxOrderQty %s (%s: подозрительная цена/контракт)" % (qty, max_oq, symbol))
    extra = {}
    if order_type == "Limit":
        extra["price"] = _fmt(limit_price, inst.get("tickSize", 0))
    if post_only:
        extra["timeInForce"] = "PostOnly"
    if order_link_id:
        extra["orderLinkId"] = order_link_id
    res = client.place_order(symbol, side, order_type, _fmt_qty(qty, inst.get("qtyStep", 0)), **extra)
    return res.get("orderId"), qty


def set_loss_stop(client, symbol, side, entry, qty, fee_in, inst, entries=1):
    """Биржевой стоп убытка −150·entries (Full, LastPrice). Возвращает цену стопа."""
    rate = taker_rate(inst.get("innovation", False))
    sp = money.loss_stop_price(side, entry, qty, fee_in, rate, entries, inst.get("tickSize", 0))
    if sp is None or sp <= 0:
        raise TradeError("не вычислил цену стопа −$%d" % (money.LOSS_PER_ENTRY * entries))
    client.trading_stop(symbol, stopLoss=_fmt(sp, inst.get("tickSize", 0)),
                        tpslMode="Full", slTriggerBy="LastPrice", positionIdx=0)
    return sp


def set_trailing(client, symbol, side, entry, qty, fee_in, inst, cur_net=0.0):
    """Биржевой трейлинг: дистанция 17/qty, активация +$30 (или сразу, если net≥30)."""
    rate = taker_rate(inst.get("innovation", False))
    dist, active = money.trailing_params(side, entry, qty, fee_in, rate, cur_net, inst.get("tickSize", 0))
    if not dist or dist <= 0:
        raise TradeError("не вычислил дистанцию трейлинга")
    kw = {"trailingStop": _fmt(dist, inst.get("tickSize", 0)), "tpslMode": "Full", "positionIdx": 0}
    if active is not None:
        kw["activePrice"] = _fmt(active, inst.get("tickSize", 0))
    client.trading_stop(symbol, **kw)
    return dist, active


def set_take_profit(client, symbol, side, entry, qty, fee_in, inst):
    """Тейк +$100 (режим ENABLE_TP100)."""
    rate = taker_rate(inst.get("innovation", False))
    tp = money.take_profit_price(side, entry, qty, fee_in, rate, inst.get("tickSize", 0))
    client.trading_stop(symbol, takeProfit=_fmt(tp, inst.get("tickSize", 0)),
                        tpslMode="Full", tpTriggerBy="LastPrice", positionIdx=0)
    return tp


def close(client, symbol, side, qty, inst, order_link_id=None):
    """Закрытие по рынку (reduceOnly)."""
    opp = "Sell" if side == "Buy" else "Buy"
    extra = {"reduceOnly": True}
    if order_link_id:
        extra["orderLinkId"] = order_link_id
    return client.place_order(symbol, opp, "Market", _fmt_qty(qty, inst.get("qtyStep", 0)), **extra)


def position(client, symbol):
    for p in client.positions(symbol=symbol):
        if abs(float(p.get("size", 0) or 0)) > 0:
            return p
    return None


def _fmt(value, tick):
    """Строка цены с числом знаков по tickSize."""
    if value is None:
        return None
    dec = _decimals(tick)
    return ("%." + str(dec) + "f") % value


def _fmt_qty(qty, step):
    dec = _decimals(step)
    return ("%." + str(dec) + "f") % qty


def _decimals(step):
    s = ("%.12f" % float(step or 0)).rstrip("0")
    if "." in s:
        return len(s.split(".", 1)[1])
    return 0


def _selftest(client, symbol, notional):
    """Проверка пайплайна реальным demo-ордером малого размера. В конце закрывает."""
    import universe
    imap = universe.instrument_map(client)
    inst = imap.get(symbol)
    if not inst:
        print("нет инструмента", symbol)
        return
    tk = client.tickers(symbol=symbol)
    ref = float(tk[0]["lastPrice"])
    print("symbol=%s ref=%s tick=%s step=%s maxLev=%s" % (
        symbol, ref, inst["tickSize"], inst["qtyStep"], inst["maxLeverage"]))
    print("настройка счёта:", "; ".join(ensure_account(client)))

    oid, qty = enter(client, symbol, "Buy", inst, ref, notional=notional,
                     order_link_id="unitest%d" % int(time.time()))
    print("вход: orderId=%s qty=%s" % (oid, qty))
    try:
        pos = wait_position(client, symbol, "Buy")
        if not pos:
            print("позиция не появилась — проверь вручную"); return
        entry = float(pos["avgPrice"])
        psize = abs(float(pos["size"]))
        fee_in = entry_fee(client, symbol, oid, entry * psize, inst.get("innovation", False))
        print("позиция: avg=%.6f size=%s fee_in=%.6f" % (entry, psize, fee_in))
        # стоп −2% (на малом размере −$150 недостижим — проверяем сам вызов trading-stop)
        sp = money.round_tick(entry * 0.98, inst["tickSize"], "down")
        client.trading_stop(symbol, stopLoss=_fmt(sp, inst["tickSize"]),
                            tpslMode="Full", slTriggerBy="LastPrice", positionIdx=0)
        print("stopLoss поставлен @ %s" % _fmt(sp, inst["tickSize"]))
        # трейлинг (дистанция ~1% цены)
        dist = money.round_tick(entry * 0.01, inst["tickSize"], "up")
        client.trading_stop(symbol, trailingStop=_fmt(dist, inst["tickSize"]),
                            tpslMode="Full", positionIdx=0)
        print("trailingStop поставлен, дистанция %s" % _fmt(dist, inst["tickSize"]))
        pos = position(client, symbol)
        if pos:
            print("стопы в позиции: stopLoss=%s trailingStop=%s mark=%s" % (
                pos.get("stopLoss"), pos.get("trailingStop"), pos.get("markPrice")))
    finally:
        pos = position(client, symbol)
        if pos:
            r = close(client, symbol, pos.get("side"), abs(float(pos["size"])), inst,
                      order_link_id="unicltest%d" % int(time.time()))
            print("закрытие: orderId=%s" % r.get("orderId"))
            time.sleep(2)
            print("позиция после закрытия:", "закрыта" if position(client, symbol) is None else "ЕЩЁ ОТКРЫТА")
        else:
            print("позиции нет — закрывать нечего")


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    import bybit_bot
    bybit_bot.load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    if len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        sym = sys.argv[2] if len(sys.argv) > 2 else "ADAUSDT"
        notional = float(sys.argv[3]) if len(sys.argv) > 3 else 100.0
        _selftest(bybit_bot.Client(), sym, notional)
    else:
        print("использование: python3 trader.py selftest <SYMBOL> <notional$>")
