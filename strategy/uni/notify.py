#!/usr/bin/env python3
"""Telegram-отчёты UNI. Сообщения с подписью-шапкой, чтобы отличать от обычного бота.

stdout тоже дублируется (для cron-лога/диагностики). Без событий — тишина (ничего
не шлётся). Накопленные за цикл события уходят одним сообщением.
"""
import json
import time
import urllib.parse
import urllib.request


def _ts(now_ms=None):
    t = time.gmtime((now_ms / 1000.0) if now_ms else time.time())
    return time.strftime("%d.%m %H:%M", t)


class Reporter:
    def __init__(self, token, chat_id, now_ms=None, echo=True):
        self.token = token
        self.chat_id = chat_id
        self.now_ms = now_ms
        self.echo = echo
        self.events = []

    def add(self, line):
        """Добавить событие в очередь цикла."""
        if line:
            self.events.append(line)
            if self.echo:
                print(line, flush=True)

    def _post(self, text):
        if not self.token or not self.chat_id:
            return
        try:
            data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode()
            urllib.request.urlopen(
                "https://api.telegram.org/bot%s/sendMessage" % self.token, data=data, timeout=15)
        except Exception as exc:
            print("telegram error: %s" % exc, flush=True)

    def flush(self):
        """Отправить накопленные события одним сообщением. Пусто — тишина."""
        if not self.events:
            return
        header = "\U0001F9E9 ЕДИНЫЙ • %s UTC • ДЕМО-3 • UNI v1" % _ts(self.now_ms)
        text = header + "\n\n" + "\n".join(self.events)
        # Telegram лимит 4096 — режем с запасом
        self._post(text[:4000])
        self.events = []

    def message(self, text):
        """Отправить произвольный текст с шапкой немедленно (сводка)."""
        header = "\U0001F9E9 ЕДИНЫЙ • %s UTC • ДЕМО-3 • UNI v1" % _ts(self.now_ms)
        full = header + "\n\n" + text
        if self.echo:
            print(full, flush=True)
        self._post(full[:4000])
