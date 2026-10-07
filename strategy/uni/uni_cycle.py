#!/usr/bin/env python3
"""ЕДИНЫЙ бот UNI — оркестратор (demo Bybit). Режимы: manage | entries_main | entries_risk.

Берёт сигналы MAIN/RISK/S6/S7 и ведёт позиции по единым правилам владельца
(ред. 05.10): размер $10k×10, докупки до 3, стоп убытка −$150/−$300/−$450 +
биржевой трейлинг (+$30/$17). Fail-closed: без demo/профиля/верного userID —
ни одного торгового действия. Флаги-файлы в этой папке выключают входы/режимы.

Запуск: BYBIT_PROFILE=uni python3 uni_cycle.py <manage|entries_main|entries_risk>
Диагностика без ордеров: UNI_DRY_RUN=1 UNI_FORCE_REPORT=1 ... manage
"""
import calendar
import csv
import json
import os
import socket
import sys
import time

try:
    import fcntl
except ImportError:          # не-Linux (напр. Windows для офлайн-тестов)
    fcntl = None

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "scripts"))

import bybit_bot          # noqa: E402
import money              # noqa: E402
import trader             # noqa: E402
import universe           # noqa: E402
import journal            # noqa: E402
import notify             # noqa: E402
import indicators as ind  # noqa: E402
import signals_main       # noqa: E402
import signals_risk       # noqa: E402
import signals_s6         # noqa: E402
import signals_s7         # noqa: E402
import signals_trend      # noqa: E402

STATE_PATH = os.path.join(BASE, "positions_state_uni.json")
S7_SEEN = os.path.join(BASE, "s7_seen.json")
ENTRIES_META = os.path.join(BASE, "entries_meta_uni.csv")
HEARTBEAT = os.path.join(BASE, "uni_last_seen")
GAP_ALERT_S = 300        # простой > 5 мин → алерт в Telegram при возобновлении

DRY = bool(os.environ.get("UNI_DRY_RUN"))
FORCE_REPORT = os.environ.get("UNI_FORCE_REPORT", "")

MAX_POSITIONS = 12
MAX_PER_SIDE = 8
ADD_MAX_ENTRIES = 3
ADD_COOLDOWN_MIN = 15
S6_TTL_S = 55


# ── флаги, state, локи ───────────────────────────────────────────────────────

def flag(name):
    return os.path.exists(os.path.join(BASE, name))


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            s = json.load(fh)
    except (OSError, ValueError):
        s = {}
    s.setdefault("positions", {})
    s.setdefault("pending", {})
    return s


def save_state(s):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def acquire_lock(mode):
    """Лок режима (неблокирующий): при наложении возвращает None."""
    path = os.path.join(BASE, "uni_%s.lock" % mode)
    fh = open(path, "w")
    if fcntl is None:        # без fcntl (тесты) — без реального лока
        return fh
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


# ── вспомогательное ──────────────────────────────────────────────────────────

def coin(symbol):
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def zone_of(inst):
    return "innovation" if inst.get("innovation") else "normal"


def net_at(st, price):
    rate = money.taker_rate(st.get("zone") == "innovation")
    return money.net(st["side"], st["entry_price"], st["qty"], st["fee_in"], price, rate)


def pick_source(rows, symbol, side):
    """Последний вход по символу+стороне в мете → (source, scenario, time_utc) или None."""
    found = None
    for r in rows:
        if r.get("symbol") == symbol and r.get("side") == side:
            found = (r.get("source") or "ADOPT", r.get("scenario") or "?", r.get("time_utc") or "")
    return found


def recover_meta(symbol, side):
    """Восстанавливает источник усыновлённой позиции из entries_meta_uni.csv."""
    try:
        with open(ENTRIES_META, "r", encoding="utf-8") as fh:
            return pick_source(list(csv.DictReader(fh)), symbol, side)
    except OSError:
        return None


def ts_to_ms(ts):
    """'YYYY-MM-DDTHH:MM:SS' (UTC) → мс. None при ошибке."""
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%S")) * 1000
    except (ValueError, TypeError):
        return None


def reconstruct_excursions(client, symbol, st):
    """Достоверные (max_net, min_net, mae_pct) по 1m-свечам за время удержания."""
    entry_ms = st.get("entry_ms")
    if not entry_ms:
        return None, None, None
    try:
        span = int((time.time() * 1000 - entry_ms) / 60000) + 3
        kl = client.kline(symbol, "1", min(max(span, 2), 1000))
    except Exception:
        return None, None, None
    o = ind.ohlcv(kl)
    rate = money.taker_rate(st.get("zone") == "innovation")
    return money.excursions(o["t"], o["h"], o["l"], st["side"], st["entry_price"],
                            st["qty"], st["fee_in"], rate, entry_ms - 60000)


def read_heartbeat():
    """Время прошлого тика manage (unix-сек) или 0."""
    try:
        with open(HEARTBEAT, "r", encoding="utf-8") as fh:
            return float(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0.0


def write_heartbeat(ts):
    try:
        with open(HEARTBEAT, "w", encoding="utf-8") as fh:
            fh.write("%.0f" % ts)
    except OSError:
        pass


def downtime_min(prev, now, threshold=GAP_ALERT_S):
    """Простой в минутах, если prev задан и разрыв > threshold; иначе None."""
    if prev and (now - prev) > threshold:
        return (now - prev) / 60.0
    return None


def side_counts(positions):
    buy = sum(1 for p in positions.values() if p["side"] == "Buy")
    sell = sum(1 for p in positions.values() if p["side"] == "Sell")
    return buy, sell


def can_open(state, side):
    pos = state["positions"]
    total = len(pos) + len(state["pending"])
    if total >= MAX_POSITIONS:
        return False, "лимит 12 позиций"
    buy, sell = side_counts(pos)
    if side == "Buy" and buy >= MAX_PER_SIDE:
        return False, "лимит 8 лонгов"
    if side == "Sell" and sell >= MAX_PER_SIDE:
        return False, "лимит 8 шортов"
    return True, ""


def free_available(client):
    try:
        w = client.wallet_balance()
        acc = (w.get("list") or [{}])[0]
        avail = acc.get("totalAvailableBalance")
        if avail in (None, ""):
            eq = float(acc.get("totalEquity", 0) or 0)
            im = float(acc.get("totalInitialMargin", 0) or acc.get("totalPositionIM", 0) or 0)
            return eq - im
        return float(avail)
    except Exception:
        return 0.0


def total_equity(client):
    """Полный капитал счёта (для риск-сайзинга TREND)."""
    try:
        w = client.wallet_balance()
        acc = (w.get("list") or [{}])[0]
        return float(acc.get("totalEquity", 0) or 0)
    except Exception:
        return 0.0


# ── постановка выходных ордеров ──────────────────────────────────────────────

def set_exit_orders(client, symbol, st, inst, rep, fresh=True):
    """Ставит стоп −150 (+ трейлинг или TP) согласно флагам. Один вызов trading-stop.

    TREND — отдельный ATR-режим (стоп 2.5·ATR, трейлинг 3·ATR, активация +1·ATR):
    не наследует $-логику, которая душила прибыль. Остальные источники — как прежде.
    """
    side = st["side"]
    entry, qty, fee_in = st["entry_price"], st["qty"], st["fee_in"]
    tick = inst.get("tickSize", 0)
    if st.get("source") == "TREND" and st.get("atr"):
        set_exit_orders_trend(client, symbol, st, inst, rep)
        return
    entries = st.get("add_count", 0) + 1
    rate = money.taker_rate(st.get("zone") == "innovation")
    kw = {"tpslMode": "Full", "positionIdx": 0}
    if not flag("DISABLE_LOSS_STOPS"):
        sp = money.loss_stop_price(side, entry, qty, fee_in, rate, entries, tick)
        st["loss_stop_price"] = sp
        kw["stopLoss"] = trader._fmt(sp, tick)
        kw["slTriggerBy"] = "LastPrice"
    if flag("ENABLE_TP100"):
        tp = money.take_profit_price(side, entry, qty, fee_in, rate, tick)
        kw["takeProfit"] = trader._fmt(tp, tick)
        kw["tpTriggerBy"] = "LastPrice"
    if not flag("DISABLE_TRAILING"):
        cur_net = st.get("step", 0) * 1.0  # если уже в плюсе
        dist, active = money.trailing_params(side, entry, qty, fee_in, rate,
                                              st.get("last_net", 0.0), tick)
        st["trailing_set"] = True
        st["trailing_dist"] = dist
        st["trailing_active_px"] = active
        kw["trailingStop"] = trader._fmt(dist, tick)
        if active is not None:
            kw["activePrice"] = trader._fmt(active, tick)
    if DRY:
        rep.add("  [DRY] trading-stop %s: %s" % (symbol, {k: kw[k] for k in kw if k not in ("tpslMode", "positionIdx")}))
        return
    try:
        client.trading_stop(symbol, **kw)
    except Exception as exc:
        msg = str(getattr(exc, "ret_msg", exc))
        if "not modified" not in msg and "34040" not in msg:   # «не изменилось» — не ошибка
            rep.add("⚠ стоп/трейлинг %s не выставлен: %s" % (coin(symbol), msg))


def set_exit_orders_trend(client, symbol, st, inst, rep):
    """TREND-выходы: ATR-стоп + широкий ATR-трейлинг (активация в +1·ATR). Один вызов."""
    side = st["side"]
    entry, atr = st["entry_price"], st.get("atr", 0.0)
    tick = inst.get("tickSize", 0)
    kw = {"tpslMode": "Full", "positionIdx": 0}
    if not flag("DISABLE_LOSS_STOPS"):
        sp = money.atr_stop_price(side, entry, atr, st.get("atr_stop_mult", money.TREND_STOP_ATR), tick)
        if sp and sp > 0:
            st["loss_stop_price"] = sp
            kw["stopLoss"] = trader._fmt(sp, tick)
            kw["slTriggerBy"] = "LastPrice"
    dist = money.atr_trail_distance(atr, st.get("atr_trail_mult", money.TREND_TRAIL_ATR), tick)
    if dist and dist > 0:
        st["trailing_set"] = True
        st["trailing_dist"] = dist
        kw["trailingStop"] = trader._fmt(dist, tick)
        ap = money.atr_activate_price(side, entry, atr, st.get("atr_activate_mult", money.TREND_ACTIVATE_ATR), tick)
        if ap and ap > 0:
            st["trailing_active_px"] = ap
            kw["activePrice"] = trader._fmt(ap, tick)
    if DRY:
        rep.add("  [DRY] TREND trading-stop %s: %s" % (symbol, {k: kw[k] for k in kw if k not in ("tpslMode", "positionIdx")}))
        return
    if len(kw) <= 2:          # нечего ставить (всё отключено флагами)
        return
    try:
        client.trading_stop(symbol, **kw)
    except Exception as exc:
        msg = str(getattr(exc, "ret_msg", exc))
        if "not modified" not in msg and "34040" not in msg:
            rep.add("⚠ TREND стоп/трейл %s: %s" % (coin(symbol), msg))


# ── вход и докупка ───────────────────────────────────────────────────────────

def finalize_entry(client, symbol, inst, side, source, scenario, sl_pct, signal_price,
                   order_id, state, rep, is_add=False, meta=None):
    """После исполнения: читает позицию, ставит выходные, пишет state и событие.

    meta (для TREND): {atr, atr_stop_mult, atr_trail_mult, atr_activate_mult} — копится в st.
    """
    pos = trader.wait_position(client, symbol, side)
    if not pos:
        rep.add("⚠ %s: позиция не появилась после входа" % coin(symbol))
        return
    avg = float(pos["avgPrice"])
    size = abs(float(pos["size"]))
    value = avg * size
    fee_in = trader.entry_fee(client, symbol, order_id, value, inst.get("innovation", False))
    now_ms = int(time.time() * 1000)
    positions = state["positions"]
    st = positions.get(symbol)
    if st and is_add:
        st["entry_price"] = avg
        st["qty"] = size
        st["fee_in"] = st.get("fee_in", 0.0) + fee_in
        st["add_count"] = st.get("add_count", 0) + 1
        st["scenario"] = st["scenario"] + "+" + scenario
        st["entries"].append({"ts": now_ms, "source": source, "scenario": scenario,
                              "price": avg, "qty": size, "fee": fee_in})
        st["last_entry_ts"] = now_ms
        st["step"] = 0            # стоп лесенки пересчитается от новой средней
        st["trailing_set"] = False
        rep.add("➕ ДОКУПКА %s • %s/%s • %s @ %.6f • всего qty %s • вход №%d"
                % (coin(symbol), source, scenario, "лонг" if side == "Buy" else "шорт",
                   avg, size, st["add_count"] + 1))
    else:
        st = {
            "source": source, "scenario": scenario, "side": side,
            "entry_price": avg, "qty": size, "fee_in": fee_in,
            "entries": [{"ts": now_ms, "source": source, "scenario": scenario,
                         "price": avg, "qty": size, "fee": fee_in}],
            "add_count": 0, "entry_time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "entry_ms": now_ms, "last_entry_ts": now_ms,
            "initial_stop_pct": sl_pct, "step": 0, "ratchet_steps": 0,
            "max_net": 0.0, "min_net": 0.0, "last_net": 0.0,
            "trailing_set": False, "zone": zone_of(inst),
        }
        if meta:
            for k in ("atr", "atr_stop_mult", "atr_trail_mult", "atr_activate_mult"):
                if meta.get(k) is not None:
                    st[k] = meta[k]
        size_note = ("нотионал $%.0f" % (avg * size)) if source == "TREND" else "$10000 x10"
        rep.add("\U0001F4C8 ВХОД %s • %s/%s • %s @ %.6f • qty %s • %s • стоп-ориентир %.2f%%"
                % (coin(symbol), source, scenario, "лонг" if side == "Buy" else "шорт",
                   avg, size, size_note, sl_pct))
    set_exit_orders(client, symbol, st, inst, rep, fresh=not is_add)
    slip = (avg - signal_price) / signal_price * 100.0 if signal_price else 0.0
    journal.log_entry_meta(BASE, {
        "time_utc": st["entry_time"], "coin": coin(symbol), "symbol": symbol,
        "source": source, "scenario": scenario, "side": side,
        "signal_price": "%.8f" % signal_price if signal_price else "",
        "exec_price": "%.8f" % avg, "slip_pct": "%.3f" % slip, "delay_s": "",
        "liquidity": "Taker", "fee_in": "%.6f" % fee_in,
        "note": ("add=%d" % st.get("add_count", 0)) if is_add else "",
    })


def try_enter(client, symbol, side, source, scenario, sl_pct, signal_price, inst,
              ref_price, state, rep, order_type="Market", limit_price=None, post_only=False, meta=None):
    """Общий вход с учётом лимитов, маржи, докупок и переворотов."""
    positions = state["positions"]
    if symbol in state["pending"]:
        return                       # по символу уже висит ордер S6 — второй вход не делаем
    existing = positions.get(symbol)
    is_add = False
    if existing:
        if existing["side"] != side:
            rep.add("↔ сигнал против открытой %s — пропуск (one-way, не переворачиваем)" % coin(symbol))
            return
        if source == "TREND" or existing.get("source") == "TREND":
            return                   # TREND не докупаем и в TREND не доливаем из других источников
        # докупка
        if flag("DISABLE_UNI_ADD"):
            return
        if existing.get("add_count", 0) + 1 >= ADD_MAX_ENTRIES:
            return
        since = (int(time.time() * 1000) - existing.get("last_entry_ts", 0)) / 60000.0
        if since < ADD_COOLDOWN_MIN:
            return
        is_add = True
    else:
        ok, why = can_open(state, side)
        if not ok:
            rep.add("\U0001F6AB %s пропуск: %s" % (coin(symbol), why))
            return
    max_lev = inst.get("maxLeverage", 0)
    if max_lev and max_lev < money.LEVERAGE:
        rep.add("\U0001F6AB %s пропуск: maxLeverage %s < 10" % (coin(symbol), max_lev))
        return
    if not is_add and not money.free_margin_ok(free_available(client), len(positions)):
        rep.add("\U0001F6AB %s пропуск: мало свободной маржи" % coin(symbol))
        return
    if DRY:
        rep.add("  [DRY] %s %s %s/%s %s @~%.6f" % (
            "ДОКУПКА" if is_add else "ВХОД", coin(symbol), source, scenario,
            "лонг" if side == "Buy" else "шорт", ref_price))
        return
    try:
        oid, _ = trader.enter(client, symbol, side, inst, ref_price,
                              order_type=order_type, limit_price=limit_price, post_only=post_only,
                              order_link_id="uni%d%s" % (int(time.time()), coin(symbol)),
                              qty=(meta.get("qty") if meta else None))
    except Exception as exc:
        rep.add("⚠ %s вход не прошёл: %s" % (coin(symbol), getattr(exc, "ret_msg", exc)))
        return
    if post_only:
        state["pending"][symbol] = {
            "order_id": oid, "side": side, "source": source, "scenario": scenario,
            "sl_pct": sl_pct, "signal_price": signal_price, "ts": int(time.time() * 1000),
        }
        rep.add("⚡ S6 %s %s PostOnly @ %.6f" % (scenario, coin(symbol), limit_price))
    else:
        finalize_entry(client, symbol, inst, side, source, scenario, sl_pct,
                       signal_price, oid, state, rep, is_add=is_add, meta=meta)


# ── сопровождение (manage) ───────────────────────────────────────────────────

def classify_exit(st, closed_pnl):
    entries = st.get("add_count", 0) + 1
    if closed_pnl <= -0.9 * money.MARGIN_PER * entries:
        return "liquidation"
    if closed_pnl > 0:
        return "trailing_stop" if not flag("DISABLE_TRAILING") else "ratchet_stop"
    if not flag("DISABLE_LOSS_STOPS"):
        return "loss_stop"
    return "manual"


def log_close(client, symbol, st, rep):
    """Пишет закрытую позицию в журнал по closed-pnl."""
    net = 0.0
    exit_price = 0.0
    try:
        cp = client.closed_pnl(symbol=symbol, limit=1)
        if cp:
            net = float(cp[0].get("closedPnl", 0) or 0)
            exit_price = float(cp[0].get("avgExitPrice", 0) or 0)
    except Exception:
        pass
    reason = classify_exit(st, net)
    hold_min = (int(time.time() * 1000) - st.get("entry_ms", int(time.time() * 1000))) / 60000.0
    # достоверные пик/дно net и MAE по свечам (минутный сэмпл пропускал быстрые ходы)
    mfe, min_net, maep = reconstruct_excursions(client, symbol, st)
    max_net = mfe if mfe is not None else st.get("max_net", 0)
    min_net = min_net if min_net is not None else st.get("min_net", 0)
    mae_pct = maep if maep is not None else st.get("mae_pct", 0)
    journal.log_trade(BASE, {
        "entry_time_utc": st.get("entry_time", ""), "exit_time_utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "coin": coin(symbol), "symbol": symbol, "source": st.get("source", ""),
        "scenario": st.get("scenario", ""), "side": st.get("side", ""),
        "qty": st.get("qty", ""), "entry_price": "%.8f" % st.get("entry_price", 0),
        "exit_price": "%.8f" % exit_price, "gross_usdt": "", "fee_in_actual": "%.6f" % st.get("fee_in", 0),
        "fee_out_actual": "", "fee_real_est": "", "funding_usdt": "",
        "net_usdt": "%.4f" % net, "net_real_est": "",
        "max_net_reached": "%.2f" % max_net, "min_net_reached": "%.2f" % min_net,
        "mae_pct": "%.3f" % mae_pct, "ratchet_steps": st.get("ratchet_steps", 0),
        "initial_stop_pct": "%.2f" % st.get("initial_stop_pct", 0), "exit_reason": reason,
        "zone": st.get("zone", "normal"), "hold_min": "%.1f" % hold_min,
        "note": ("докупок: %d" % st.get("add_count", 0)) if st.get("add_count") else "",
    })
    icon = "✅" if net > 0 else "\U0001F6D1"
    rep.add("%s ВЫХОД %s • %s • net $%.2f • макс.net %.2f • ступеней %d • %.0f мин"
            % (icon, coin(symbol), reason, net, max_net, st.get("ratchet_steps", 0), hold_min))


def reconcile_and_manage(client, state, imap, rep):
    live = {}
    try:
        for p in client.positions():
            if abs(float(p.get("size", 0) or 0)) > 0:
                live[p["symbol"]] = p
    except Exception as exc:
        rep.add("⚠ не прочитал позиции: %s" % getattr(exc, "ret_msg", exc))
        return
    positions = state["positions"]
    # закрытые
    for symbol in list(positions.keys()):
        if symbol not in live:
            log_close(client, symbol, positions[symbol], rep)
            del positions[symbol]
    # усыновление: восстанавливаем настоящий source/время входа из меты (после ребутов
    # свои же позиции иначе становятся «ADOPT» и ведутся не тем выходом); pending не трогаем
    for symbol, p in live.items():
        if symbol not in positions and symbol not in state["pending"]:
            inst = imap.get(symbol, {})
            side = p["side"]
            rec = recover_meta(symbol, side)
            src, scn, ets = rec if rec else ("ADOPT", "?", "")
            entry_ms = int(time.time() * 1000)
            entry_time = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            if ets:
                ems = ts_to_ms(ets)
                if ems:
                    entry_ms, entry_time = ems, ets
            st = {
                "source": src, "scenario": scn, "side": side,
                "entry_price": float(p["avgPrice"]), "qty": abs(float(p["size"])),
                "fee_in": float(p["avgPrice"]) * abs(float(p["size"])) * money.taker_rate(bool(inst.get("innovation"))),
                "entries": [], "add_count": 0, "entry_time": entry_time,
                "entry_ms": entry_ms, "last_entry_ts": entry_ms,
                "initial_stop_pct": 0.0, "step": 0, "ratchet_steps": 0,
                "max_net": 0.0, "min_net": 0.0, "last_net": 0.0, "trailing_set": False,
                "zone": zone_of(inst),
            }
            # TREND: пересчитать ATR из свежих часовых свечей, чтобы работал ATR-выход
            if src == "TREND":
                try:
                    o = ind.ohlcv(client.kline(symbol, "60", 30))
                    a = ind.atr(o["h"], o["l"], o["c"], 14)
                    if a and a > 0:
                        st["atr"] = a
                        st["atr_stop_mult"] = money.TREND_STOP_ATR
                        st["atr_trail_mult"] = money.TREND_TRAIL_ATR
                        st["atr_activate_mult"] = money.TREND_ACTIVATE_ATR
                except Exception:
                    pass
            positions[symbol] = st
            tag = ("%s/%s" % (src, scn)) if rec else "ADOPT (меты нет)"
            rep.add("ℹ усыновил %s (%s) → %s" % (coin(symbol), side, tag))
    # сопровождение живых
    for symbol, p in live.items():
        st = positions.get(symbol)
        if st is None:            # символ ещё висит в pending (гонка) — возьмём на след. тике
            continue
        inst = imap.get(symbol, {})
        mark = float(p.get("markPrice", 0) or p.get("avgPrice", 0))
        cur_net = net_at(st, mark)
        st["last_net"] = cur_net
        st["max_net"] = max(st.get("max_net", 0.0), cur_net)
        st["min_net"] = min(st.get("min_net", 0.0), cur_net)
        # mae: максимальный ход против входа, %
        adverse = ((st["entry_price"] - mark) / st["entry_price"] * 100.0) if st["side"] == "Buy" \
            else ((mark - st["entry_price"]) / st["entry_price"] * 100.0)
        st["mae_pct"] = max(st.get("mae_pct", 0.0), adverse)
        step = int(cur_net // money.LADDER_STEP) * int(money.LADDER_STEP) if cur_net >= money.LADDER_START else 0
        if step > st.get("ratchet_steps", 0) * 10:
            st["ratchet_steps"] = step // 10
        has_sl = bool(p.get("stopLoss") and p.get("stopLoss") not in ("", "0"))
        has_trail = bool(p.get("trailingStop") and p.get("trailingStop") not in ("", "0"))
        # свип стопа убытка
        if not flag("DISABLE_LOSS_STOPS") and not has_sl:
            set_exit_orders(client, symbol, st, inst, rep, fresh=False)
            continue
        # трейлинг: поставить один раз (TREND всегда ATR-трейлинг, лесенка не для него)
        if st.get("source") == "TREND":
            if not has_trail and not st.get("trailing_set"):
                set_exit_orders(client, symbol, st, inst, rep, fresh=False)
        elif not flag("DISABLE_TRAILING"):
            if not has_trail and not st.get("trailing_set"):
                set_exit_orders(client, symbol, st, inst, rep, fresh=False)
        else:
            # лесенка
            rate = money.taker_rate(st.get("zone") == "innovation")
            lad = money.ladder_stop(st["side"], st["entry_price"], st["qty"], st["fee_in"],
                                    cur_net, rate, st.get("step", 0), inst.get("tickSize", 0))
            if lad:
                st["step"] = lad["step"]
                if not DRY:
                    try:
                        client.trading_stop(symbol, stopLoss=trader._fmt(lad["stop_price"], inst.get("tickSize", 0)),
                                            tpslMode="Full", slTriggerBy="LastPrice", positionIdx=0)
                    except Exception as exc:
                        rep.add("⚠ лесенка %s: %s" % (coin(symbol), getattr(exc, "ret_msg", exc)))
                rep.add("\U0001F512 %s стоп → +$%.0f (достигнуто +$%.0f) • %.6f"
                        % (coin(symbol), lad["protected_net"], cur_net, lad["stop_price"]))


def manage_pending(client, state, imap, rep):
    """Проверяет PostOnly-ордера S6: исполнен → финализировать, устарел → отменить."""
    pend = state["pending"]
    for symbol in list(pend.keys()):
        pe = pend[symbol]
        inst = imap.get(symbol, {})
        pos = trader.position(client, symbol)
        if pos and pos.get("side") == pe["side"]:
            del pend[symbol]
            finalize_entry(client, symbol, inst, pe["side"], pe["source"], pe["scenario"],
                           pe["sl_pct"], pe["signal_price"], pe["order_id"], state, rep, is_add=False)
            continue
        age = (int(time.time() * 1000) - pe["ts"]) / 1000.0
        if age > S6_TTL_S:
            if not DRY:
                try:
                    client.cancel_order(symbol, order_id=pe["order_id"])
                except Exception:
                    pass
            del pend[symbol]


# ── входы S6 / S7 (в manage) ─────────────────────────────────────────────────

def do_s6(client, now_ms, state, imap, rep):
    if flag("DISABLE_UNI") or flag("DISABLE_UNI_S6"):
        return
    try:
        sigs = signals_s6.scan_s6(client, now_ms, imap)
    except Exception as exc:
        rep.add("⚠ скан S6: %s" % getattr(exc, "ret_msg", exc))
        return
    placed = 0
    for s in sigs:
        if placed >= 2:
            break
        symbol = s["symbol"]
        inst = imap.get(symbol, {})
        if not inst:
            continue
        try:
            ob = client.orderbook(symbol, 1)
            bid = float(ob["b"][0][0]); ask = float(ob["a"][0][0])
        except Exception:
            continue
        price = bid if s["side"] == "Buy" else ask
        try_enter(client, symbol, s["side"], "S6", "S6-" + s["trigger"], s["sl_pct"],
                  s["close"], inst, price, state, rep, order_type="Limit",
                  limit_price=price, post_only=True)
        placed += 1


def do_s7(client, now_ms, state, imap, rep):
    if flag("DISABLE_UNI") or flag("DISABLE_UNI_S7"):
        return
    try:
        cands, _queue = signals_s7.detect(client, now_ms, S7_SEEN)
    except Exception as exc:
        rep.add("⚠ детекция S7: %s" % getattr(exc, "ret_msg", exc))
        return
    seen = signals_s7.load_seen(S7_SEEN)
    for cnd in cands:
        symbol = cnd["symbol"]
        if symbol in state["positions"]:
            continue
        inst = imap.get(symbol) or {}
        try:
            tk = client.tickers(symbol=symbol)
            ref = float(tk[0]["lastPrice"]) if tk else 0.0
        except Exception:
            ref = 0.0
        if ref <= 0:
            continue
        rep.add("\U0001F195 S7 листинг %s — вход" % coin(symbol))
        try_enter(client, symbol, "Buy", "S7", "S7-LIST", 9.5, ref, inst, ref, state, rep)
        seen.add(symbol)
    signals_s7.save_seen(S7_SEEN, seen)


# ── сводка ───────────────────────────────────────────────────────────────────

def summary(client, state, rep):
    pos = state["positions"]
    lines = ["\U0001F4CA Сводка: позиций %d" % len(pos)]
    for symbol, st in pos.items():
        lines.append("  %s %s net $%.2f ступень %d" % (
            coin(symbol), "L" if st["side"] == "Buy" else "S", st.get("last_net", 0), st.get("step", 0)))
    rep.message("\n".join(lines))


# ── режимы ───────────────────────────────────────────────────────────────────

def is_primary(hostname, primary):
    """True, если хост — назначенный главный (или главный не задан — обратная совместимость).

    Защита от двойной торговли на одном demo-счёте: активен только primary-хост,
    остальные бездействуют. Главный задаётся переменной UNI_PRIMARY_HOST в .env.
    """
    return (not primary) or (hostname == primary)


def make_client():
    bybit_bot.load_env_file(os.path.join(BASE, ".env"))
    c = bybit_bot.Client()
    bybit_bot.verify_identity(c, os.environ.get("UNI_EXPECT_USERID", ""))
    return c


def reporter(now_ms):
    return notify.Reporter(os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                           os.environ.get("TELEGRAM_CHAT_ID", ""), now_ms, echo=True)


def run_manage(client, rep):
    now_ms = client.server_time_ms()
    state = load_state()
    imap = universe.instrument_map(client)
    # Алерт о простое: если прошлый тик manage был давно (ребут/пропажа света),
    # сообщаем в Telegram при возобновлении. Без ИБП сказать «в момент» нельзя.
    gap = downtime_min(read_heartbeat(), time.time())
    if gap is not None:
        rep.add("\U0001F50C homeb14 возобновил работу после простоя ~%.0f мин (ребут/свет). "
                "Открытых позиций: %d — они были под биржевыми стопами."
                % (gap, len(state.get("positions", {}))))
    # Каждая фаза в своём try: сбой одной не отменяет остальные и, главное,
    # не пропускает save_state — иначе закрытые позиции не удалятся из state
    # и залогируются повторно (баг дублей в журнале).
    for name, fn in (("manage_pending", lambda: manage_pending(client, state, imap, rep)),
                     ("reconcile", lambda: reconcile_and_manage(client, state, imap, rep)),
                     ("do_s7", lambda: do_s7(client, now_ms, state, imap, rep)),
                     ("do_s6", lambda: do_s6(client, now_ms, state, imap, rep))):
        try:
            fn()
        except Exception as exc:
            rep.add("⚠ фаза %s: %s" % (name, getattr(exc, "ret_msg", exc)))
    save_state(state)
    write_heartbeat(time.time())


def run_entries_main(client, rep):
    now_ms = client.server_time_ms()
    state = load_state()
    imap = universe.instrument_map(client)
    hour = time.gmtime(now_ms / 1000.0).tm_hour
    minute = time.gmtime(now_ms / 1000.0).tm_min
    if not (flag("DISABLE_UNI") or flag("DISABLE_UNI_MAIN")):
        tickers = {t["symbol"]: t for t in client.tickers("linear")}
        for s in signals_main.scan_main(client, now_ms, imap):
            inst = imap.get(s["symbol"], {})
            ref = float(tickers.get(s["symbol"], {}).get("lastPrice", s["close"]) or s["close"])
            try_enter(client, s["symbol"], s["side"], "MAIN", s["scenario"], s["sl_pct"],
                      s["close"], inst, ref, state, rep)
    save_state(state)
    if (hour in (0, 12) and minute < 10) or FORCE_REPORT == "daily":
        summary(client, state, rep)


def run_entries_risk(client, rep):
    now_ms = client.server_time_ms()
    state = load_state()
    imap = universe.instrument_map(client)
    if flag("DISABLE_UNI") or flag("DISABLE_UNI_RISK"):
        save_state(state)
        return
    tickers = {t["symbol"]: t for t in client.tickers("linear")}
    for s in signals_risk.scan_risk(client, now_ms, imap):
        inst = imap.get(s["symbol"], {})
        ref = float(tickers.get(s["symbol"], {}).get("lastPrice", s["close"]) or s["close"])
        try_enter(client, s["symbol"], s["side"], "RISK", s["scenario"], s["sl_pct"],
                  s["close"], inst, ref, state, rep)
    save_state(state)


def run_entries_trend(client, rep):
    now_ms = client.server_time_ms()
    state = load_state()
    imap = universe.instrument_map(client)
    if flag("DISABLE_UNI") or flag("DISABLE_UNI_TREND"):
        save_state(state)
        return
    try:
        sigs = signals_trend.scan_trend(client, now_ms, imap)
    except Exception as exc:
        rep.add("⚠ скан TREND: %s" % getattr(exc, "ret_msg", exc))
        sigs = []
    equity = total_equity(client) if sigs else 0.0
    tickers = {t["symbol"]: t for t in client.tickers("linear")} if sigs else {}
    for s in sigs:
        symbol = s["symbol"]
        if symbol in state["positions"] or symbol in state["pending"]:
            continue
        inst = imap.get(symbol, {})
        ref = float(tickers.get(symbol, {}).get("lastPrice", s["close"]) or s["close"])
        atr = s.get("atr") or 0.0
        qty = money.trend_qty(equity, ref, atr, inst.get("qtyStep", 0),
                              inst.get("minOrderQty", 0), inst.get("minNotional", 0))
        if not qty:
            rep.add("\U0001F6AB TREND %s: размер не вычислен (ATR/минимумы)" % coin(symbol))
            continue
        meta = {"qty": qty, "atr": atr,
                "atr_stop_mult": money.TREND_STOP_ATR,
                "atr_trail_mult": money.TREND_TRAIL_ATR,
                "atr_activate_mult": money.TREND_ACTIVATE_ATR}
        try_enter(client, symbol, s["side"], "TREND", s["scenario"], s["sl_pct"],
                  s["close"], inst, ref, state, rep, order_type="Market", meta=meta)
    save_state(state)


MODES = {"manage": run_manage, "entries_main": run_entries_main,
         "entries_risk": run_entries_risk, "entries_trend": run_entries_trend}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.exit("usage: uni_cycle.py <manage|entries_main|entries_trend|entries_risk>")
    mode = sys.argv[1]
    # primary-host guard: торгует только назначенный главный хост (защита от двойной
    # торговли на одном demo-счёте). Не-главный хост бездействует.
    bybit_bot.load_env_file(os.path.join(BASE, ".env"))
    primary = os.environ.get("UNI_PRIMARY_HOST", "")
    if not is_primary(socket.gethostname(), primary):
        print("⏸ %s не primary (главный=%s) — бездействую" % (socket.gethostname(), primary), flush=True)
        return
    lock = acquire_lock(mode)
    if not lock:
        print("⏳ %s пропущен (идёт предыдущий)" % mode, flush=True)
        return
    try:
        try:
            client = make_client()
        except Exception as exc:
            print("FATAL: %s" % exc, flush=True)
            sys.exit(2)
        rep = reporter(client.server_time_ms())
        if DRY:
            rep.add("[DRY_RUN] режим %s — ордера не отправляются" % mode)
        try:
            MODES[mode](client, rep)
        except Exception as exc:
            rep.add("⚠ сбой цикла %s: %s" % (mode, getattr(exc, "ret_msg", exc)))
        rep.flush()
    finally:
        lock.close()


if __name__ == "__main__":
    main()
