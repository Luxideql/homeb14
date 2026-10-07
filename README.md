# UNI — торговый бот (Bybit demo, фьючерсы USDT-перпы)

Детерминированный бот на Python 3 (только стандартная библиотека, **без LLM** в контуре).
Форвард-тест на демо-счёте Bybit: бессрочные фьючерсы USDT (linear). Источники входа
(MAIN A–D, RISK S1–S5, S6 T1–T3, S7-листинги, TREND) ведутся по единым правилам размера
и выхода (стоп убытка + биржевой трейлинг).

## Структура
```
strategy/
  scripts/bybit_bot.py      клиент Bybit v5 (подпись HMAC, demo-эндпоинт, троттлинг)
  uni/
    indicators.py           EMA/SMA/ATR/RSI/Supertrend/Donchian/BB/rVOL/VWAP
    universe.py             вселенные: ядро MAIN + скрининг биржи
    signals_main.py         MAIN (A–D)
    signals_risk.py         RISK (S1–S5)
    signals_s6.py           S6 (T1–T3, быстрый скальп — отключаемый)
    signals_s7.py           S7 (новые листинги)
    signals_trend.py        TREND (Donchian-пробой + EMA + BTC, ATR-выход)
    money.py                размер позиции, net с комиссиями, стопы, трейлинг
    trader.py               исполнение: вход / стоп / трейлинг / закрытие
    journal.py              журнал сделок (CSV)
    notify.py               Telegram-отчёты
    uni_cycle.py            оркестратор: manage | entries_main | entries_trend | entries_risk
    uni_dash.py             веб-дашборд статистики (порт 8097)
    tests_*.py              офлайн-тесты
    .env.example            шаблон ключей (реальный .env — НЕ в git)
    README.md / HANDOFF.md  детали и управление
systemd/uni-dash.service    служба дашборда
```

## Запуск
Ключи — в `strategy/uni/.env` (не в git, см. `.env.example`). Бот крутится по cron:
`manage` ежеминутно, `entries_main`/`entries_trend` ежечасно, `entries_risk` каждые 15 мин.
Офлайн-тесты: `python3 strategy/uni/tests_uni_offline.py`. Подробно — `strategy/uni/README.md`.

## Честно
Форвард-тест, не гарантия доходности; ценность — данные для калибровки стопов.
Демо ≠ реал (слиппедж, funding, ликвидность). Плечо усиливает и прибыль, и просадку.
