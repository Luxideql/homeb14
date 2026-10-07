#!/usr/bin/env python3
"""CSV-журнал UNI: одна строка на закрытую позицию + отдельный лог входов.

trades_log_uni.csv — для анализа через месяц: net с комиссиями, пик/просадка по
пути (max_net/min_net), mae_pct (макс. ход против входа — ключ к вопросу о стопах),
ступени лесенки, справочный стоп сценария, причина выхода.
"""
import csv
import os

TRADES = "trades_log_uni.csv"
ENTRIES_META = "entries_meta_uni.csv"

TRADES_HEADER = [
    "entry_time_utc", "exit_time_utc", "coin", "symbol", "source", "scenario", "side",
    "qty", "entry_price", "exit_price", "gross_usdt", "fee_in_actual", "fee_out_actual",
    "fee_real_est", "funding_usdt", "net_usdt", "net_real_est", "max_net_reached",
    "min_net_reached", "mae_pct", "ratchet_steps", "initial_stop_pct", "exit_reason",
    "zone", "hold_min", "note",
]

META_HEADER = [
    "time_utc", "coin", "symbol", "source", "scenario", "side", "signal_price",
    "exec_price", "slip_pct", "delay_s", "liquidity", "fee_in", "note",
]


def _ensure(path, header):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(header)


def _append(path, header, row):
    _ensure(path, header)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow([row.get(k, "") for k in header])


def log_trade(base_dir, row):
    _append(os.path.join(base_dir, TRADES), TRADES_HEADER, row)


def log_entry_meta(base_dir, row):
    _append(os.path.join(base_dir, ENTRIES_META), META_HEADER, row)
