#!/usr/bin/env python3
"""Клиент Bybit v5 для контура UNI (demo). Только стандартная библиотека.

Подпись v5 (HMAC-SHA256):
    sign = HMAC(secret, timestamp + api_key + recv_window + payload)
    payload = queryString (GET) либо сырое JSON-тело (POST). Заголовки X-BAPI-*.

Профиль задаётся BYBIT_PROFILE (для UNI = 'uni'); ключи берутся из окружения по
профилю. Эндпоинт — api-demo.bybit.com (BYBIT_ENV=demo). Троттлинг: приватные
запросы ≥0.34с, публичные ≥0.12с. Любой retCode ≠ 0 — исключение (не пустой список).

Fail-closed делает verify_identity(): ENV=demo, профиль, userID, не read-only —
иначе исключение, и вызывающий код завершается без единого торгового действия.
"""
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

RECV_WINDOW = "20000"   # 20с: терпим дрейф часов (после ребута ntp не сразу синхронит)
PUBLIC_MIN_INTERVAL = 0.12    # сек между публичными запросами
PRIVATE_MIN_INTERVAL = 0.34   # сек между приватными запросами
HTTP_TIMEOUT = 20
MAX_RETRIES = 3

BASE_URLS = {"demo": "https://api-demo.bybit.com", "mainnet": "https://api.bybit.com"}

# профиль -> (env-ключ, env-секрет). UNI — на demo-ключах UNI.
PROFILE_KEYS = {
    "uni": ("BYBIT_DEMO_UNI_API_KEY", "BYBIT_DEMO_UNI_API_SECRET"),
    "risk": ("BYBIT_DEMO_RISK_API_KEY", "BYBIT_DEMO_RISK_API_SECRET"),
}

# Профиль, прочитанный при импорте (для крон-обёрток, что ставят env до импорта).
PROFILE = os.environ.get("BYBIT_PROFILE", "").strip().lower()


class BybitError(Exception):
    """Любая ошибка API/сети/идентичности. ret_code < 0 — локальная/сетевая."""

    def __init__(self, ret_code, ret_msg, path=""):
        super().__init__("retCode %s on %s: %s" % (ret_code, path, ret_msg))
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        self.path = path


def load_env_file(path):
    """Подхватывает KEY=VALUE из .env в окружение (не перетирая уже заданное)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass
    global PROFILE
    PROFILE = os.environ.get("BYBIT_PROFILE", "").strip().lower()


class Client:
    """Тонкий клиент Bybit v5: подпись, троттлинг, подъём ошибок, повторы сети."""

    def __init__(self):
        self.env = os.environ.get("BYBIT_ENV", "").strip().lower()
        self.profile = os.environ.get("BYBIT_PROFILE", "").strip().lower()
        if self.env not in BASE_URLS:
            raise BybitError(-1, "BYBIT_ENV должен быть demo|mainnet (сейчас %r)" % self.env)
        self.base = BASE_URLS[self.env]
        km = PROFILE_KEYS.get(self.profile)
        if not km:
            raise BybitError(-1, "неизвестный BYBIT_PROFILE %r" % self.profile)
        self.key = os.environ.get(km[0], "").strip()
        self.secret = os.environ.get(km[1], "").strip()
        if not self.key or not self.secret:
            raise BybitError(-1, "нет ключа/секрета для профиля %s" % self.profile)
        self._lock = threading.Lock()
        self._last_public = 0.0
        self._last_private = 0.0

    # ── низкий уровень ───────────────────────────────────────────────────────

    def _throttle(self, private):
        with self._lock:
            gap = PRIVATE_MIN_INTERVAL if private else PUBLIC_MIN_INTERVAL
            last = self._last_private if private else self._last_public
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            now = time.monotonic()
            if private:
                self._last_private = now
            else:
                self._last_public = now

    def _do(self, method, path, params, private):
        if method == "GET":
            query = urllib.parse.urlencode(sorted((params or {}).items()))
            url = self.base + path + (("?" + query) if query else "")
            data = None
            payload = query
        else:
            payload = json.dumps(params or {})
            url = self.base + path
            data = payload.encode()
        headers = {"Content-Type": "application/json"}
        if private:
            ts = str(int(time.time() * 1000))
            sign = hmac.new(
                self.secret.encode(), (ts + self.key + RECV_WINDOW + payload).encode(),
                hashlib.sha256,
            ).hexdigest()
            headers.update({
                "X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": RECV_WINDOW, "X-BAPI-SIGN": sign,
            })
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise BybitError(exc.code, exc.read().decode("utf-8", "replace")[:300], path)
        if body.get("retCode") != 0:
            raise BybitError(body.get("retCode"), body.get("retMsg"), path)
        return body.get("result", {}) or {}

    def request(self, method, path, params=None, private=False):
        last = None
        for attempt in range(MAX_RETRIES):
            self._throttle(private)
            try:
                return self._do(method, path, params, private)
            except BybitError as exc:
                # сетевые/временные (-1) повторяем, ответы API с кодом — нет
                if exc.ret_code != -1 or attempt == MAX_RETRIES - 1:
                    raise
                last = exc
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last = BybitError(-1, "network: %s" % exc, path)
                if attempt == MAX_RETRIES - 1:
                    raise last
            time.sleep(0.5 * (attempt + 1))
        raise last

    def get(self, path, params=None, private=False):
        return self.request("GET", path, params, private)

    def post(self, path, params=None):
        return self.request("POST", path, params, private=True)

    # ── публичные данные ─────────────────────────────────────────────────────

    def server_time_ms(self):
        res = self.get("/v5/market/time")
        return int(res.get("timeNano", "0")) // 1_000_000

    def kline(self, symbol, interval, limit=200, category="linear"):
        res = self.get("/v5/market/kline", {
            "category": category, "symbol": symbol, "interval": str(interval), "limit": limit,
        })
        return res.get("list", [])   # [ [start, open, high, low, close, volume, turnover], ... ] новые сверху

    def tickers(self, category="linear", symbol=None):
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol
        return self.get("/v5/market/tickers", params).get("list", [])

    def instruments_info(self, category="linear", symbol=None, status=None, limit=1000, cursor=None):
        params = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return self.get("/v5/market/instruments-info", params)

    def instruments_all(self, category="linear", status=None):
        """Полный справочник с прокруткой по курсору."""
        out, cursor = [], None
        while True:
            res = self.instruments_info(category=category, status=status, limit=1000, cursor=cursor)
            out.extend(res.get("list", []))
            cursor = res.get("nextPageCursor")
            if not cursor:
                return out

    def orderbook(self, symbol, limit=1, category="linear"):
        return self.get("/v5/market/orderbook", {"category": category, "symbol": symbol, "limit": limit})

    def recent_trades(self, symbol, limit=60, category="linear"):
        return self.get("/v5/market/recent-trade", {
            "category": category, "symbol": symbol, "limit": limit,
        }).get("list", [])

    def funding_history(self, symbol, limit=1, category="linear"):
        return self.get("/v5/market/funding/history", {
            "category": category, "symbol": symbol, "limit": limit,
        }).get("list", [])

    def open_interest(self, symbol, interval_time="5min", limit=1, category="linear"):
        return self.get("/v5/market/open-interest", {
            "category": category, "symbol": symbol, "intervalTime": interval_time, "limit": limit,
        }).get("list", [])

    # ── приватные: счёт и позиции ────────────────────────────────────────────

    def query_api(self):
        return self.get("/v5/user/query-api", private=True)

    def wallet_balance(self, account_type="UNIFIED"):
        return self.get("/v5/account/wallet-balance", {"accountType": account_type}, private=True)

    def account_info(self):
        return self.get("/v5/account/info", private=True)

    def positions(self, category="linear", settle_coin="USDT", symbol=None):
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = settle_coin
        return self.get("/v5/position/list", params, private=True).get("list", [])

    def open_orders(self, category="linear", settle_coin="USDT", symbol=None):
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = settle_coin
        return self.get("/v5/order/realtime", params, private=True).get("list", [])

    def executions(self, category="linear", symbol=None, order_id=None, limit=50):
        params = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if order_id:
            params["orderId"] = order_id
        return self.get("/v5/execution/list", params, private=True).get("list", [])

    def closed_pnl(self, category="linear", symbol=None, limit=50):
        params = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        return self.get("/v5/position/closed-pnl", params, private=True).get("list", [])

    # ── приватные: настройка и ордера ────────────────────────────────────────

    def set_margin_mode(self, mode):
        """mode: ISOLATED_MARGIN | REGULAR_MARGIN | PORTFOLIO_MARGIN."""
        return self.post("/v5/account/set-margin-mode", {"setMarginMode": mode})

    def switch_position_mode(self, mode, category="linear", coin="USDT", symbol=None):
        """mode: 0 — one-way, 3 — hedge."""
        params = {"category": category, "mode": mode}
        if symbol:
            params["symbol"] = symbol
        else:
            params["coin"] = coin
        return self.post("/v5/position/switch-mode", params)

    def set_leverage(self, symbol, leverage, category="linear"):
        return self.post("/v5/position/set-leverage", {
            "category": category, "symbol": symbol,
            "buyLeverage": str(leverage), "sellLeverage": str(leverage),
        })

    def place_order(self, symbol, side, order_type, qty, category="linear", **extra):
        params = {
            "category": category, "symbol": symbol, "side": side,
            "orderType": order_type, "qty": str(qty),
        }
        params.update({k: v for k, v in extra.items() if v is not None})
        return self.post("/v5/order/create", params)

    def cancel_order(self, symbol, order_id=None, order_link_id=None, category="linear"):
        params = {"category": category, "symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        if order_link_id:
            params["orderLinkId"] = order_link_id
        return self.post("/v5/order/cancel", params)

    def trading_stop(self, symbol, category="linear", position_idx=0, **extra):
        """SL/TP/трейлинг: stopLoss, takeProfit, trailingStop, activePrice,
        tpslMode, slTriggerBy/tpTriggerBy и т.д. — через extra."""
        params = {"category": category, "symbol": symbol, "positionIdx": position_idx}
        params.update({k: v for k, v in extra.items() if v is not None})
        return self.post("/v5/position/trading-stop", params)


def verify_identity(client, expected_uid, require_demo=True):
    """Fail-closed. Возвращает dict query-api или бросает BybitError.

    Проверяет: ENV=demo (если require_demo), профиль с ключами, userID совпадает,
    ключ не read-only. Любое несовпадение — исключение; вызывающий завершается.
    """
    if require_demo and client.env != "demo":
        raise BybitError(-2, "ENV=%r, ожидался demo" % client.env, "verify")
    if client.profile != "uni":
        raise BybitError(-2, "PROFILE=%r, ожидался uni" % client.profile, "verify")
    info = client.query_api()
    uid = str(info.get("userID", ""))
    if str(expected_uid) and uid != str(expected_uid):
        raise BybitError(-2, "userID=%s, ожидался %s" % (uid, expected_uid), "verify")
    if int(info.get("readOnly", 1)) != 0:
        raise BybitError(-2, "ключ read-only — торговать нельзя", "verify")
    return info


if __name__ == "__main__":
    # Самопроверка: BYBIT_PROFILE=uni python3 bybit_bot.py  (читает ../uni/.env)
    here = os.path.dirname(os.path.abspath(__file__))
    load_env_file(os.path.join(here, "..", "uni", ".env"))
    c = Client()
    print("env=%s profile=%s base=%s" % (c.env, c.profile, c.base))
    print("server_time_ms:", c.server_time_ms())
    tk = c.tickers(symbol="BTCUSDT")
    print("BTCUSDT last:", tk[0]["lastPrice"] if tk else "нет")
    info = verify_identity(c, os.environ.get("UNI_EXPECT_USERID", ""))
    print("identity OK: userID=%s readOnly=%s" % (info.get("userID"), info.get("readOnly")))
    inst = c.instruments_all()
    print("инструментов linear (Trading):", len(inst))
    kl = c.kline("BTCUSDT", "60", limit=3)
    print("свечей 1h получено:", len(kl))
    w = c.wallet_balance()
    acc = (w.get("list") or [{}])[0]
    print("equity:", acc.get("totalEquity"), "avail:", acc.get("totalAvailableBalance"))
