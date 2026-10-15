"""Фоновая работа бота: получение сообщений из Telegram, их обработка и отправка уведомлений из очереди.

По умолчанию — долгий опрос (long polling): серверу не нужен ни публичный адрес, ни открытый порт, он сам ходит в Telegram.
Режим webhook включается настройкой `telegram.mode: webhook` (нужен https-адрес `telegram.public_url`).
Всё выключено, пока `telegram.enabled: false` или не задан токен: сайт тогда работает как раньше.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import httpx

from .api import TelegramAPI, TelegramError
from .bot import TelegramBot
from .notify import Notifier

log = logging.getLogger("dekanat.telegram")

OFFSET_KEY = "telegram_update_offset"


def webhook_path_secret(token: str, secret: Optional[str]) -> str:
    """Часть адреса webhook, по которой Telegram находит наш обработчик: выводится из токена, сам токен в адресе не светится."""
    return hashlib.sha256(f"webhook|{secret or ''}|{token}".encode("utf-8")).hexdigest()[:32]


class TelegramService:
    def __init__(self, settings, storage, svc, core, accounts, notifier: Notifier, ocr_slots, locks):
        self.settings, self.storage, self.notifier = settings, storage, notifier
        self.token = str(settings.get("telegram.bot_token") or "")
        self.enabled = bool(settings.get("telegram.enabled", False)) and bool(self.token)
        self.mode = str(settings.get("telegram.mode", "polling")).lower()
        self.public_url = str(settings.get("telegram.public_url") or "").rstrip("/")
        self.webhook_secret = str(settings.get("telegram.webhook_secret") or "") or None
        self.api: Optional[TelegramAPI] = None
        self.bot: Optional[TelegramBot] = None
        self.username = str(settings.get("telegram.bot_username") or "")
        self.error = ""
        self.last_poll = 0.0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._pool: Optional[ThreadPoolExecutor] = None
        if self.enabled:
            try:
                client = httpx.Client(timeout=40.0)
                self.api = TelegramAPI(self.token, str(settings.get("telegram.api_url") or ""), client=client)
                self.bot = TelegramBot(self.api, svc, storage, core, accounts, notifier, settings, ocr_slots, locks)
            except ValueError as exc:
                log.error("Telegram: %s", exc)
                self.enabled = False

    # ------------------------------------------------------------------ состояние для /api/health и сайта
    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    @property
    def mini_app(self) -> bool:
        return bool(self.enabled and self.public_url.startswith("https://") and self.settings.get("telegram.mini_app", True))

    def status(self) -> dict:
        return {"enabled": self.enabled, "mode": self.mode if self.enabled else None, "running": self.running, "bot": self.username or None,
                "mini_app": self.mini_app, "error": self.error or None,
                "last_poll_seconds_ago": round(time.time() - self.last_poll, 1) if self.last_poll else None}

    @property
    def webhook_path(self) -> str:
        return f"/api/telegram/webhook/{webhook_path_secret(self.token, self.webhook_secret)}"

    # ------------------------------------------------------------------ запуск и остановка
    def start(self) -> None:
        if not self.enabled or self.running:
            return
        self._stop.clear()
        self._pool = ThreadPoolExecutor(max_workers=int(self.settings.get("telegram.workers", 4)), thread_name_prefix="tg-update")
        t = threading.Thread(target=self._boot, name="tg-boot", daemon=True)
        t.start()
        self._threads = [t]

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=3)
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
        self._threads = []

    def _boot(self) -> None:
        """Проверка токена и выбор режима. Работает в своём потоке, чтобы медленный Telegram не задерживал запуск сайта."""
        assert self.api and self.bot
        while not self._stop.is_set():
            try:
                me = self.api.get_me()
                self.username = self.username or str(me.get("username") or "")
                self.bot.username = self.username
                self.error = ""
                break
            except TelegramError as exc:
                self.error = "Telegram недоступен или токен неверный" if exc.code in (0, 401) else exc.description
                log.warning("Telegram: не удалось проверить токен (%s). Повтор через минуту.", exc.description)
                if self._stop.wait(60):
                    return
        if self._stop.is_set():
            return
        self.bot.setup()
        outbox = threading.Thread(target=self._outbox_loop, name="tg-outbox", daemon=True)
        outbox.start()
        self._threads.append(outbox)
        if self.mode == "webhook":
            self._setup_webhook()
            return
        try:
            self.api.delete_webhook()  # long polling не работает, пока у бота стоит webhook
        except TelegramError as exc:
            log.warning("Telegram: не удалось снять webhook: %s", exc.description)
        log.info("Telegram-бот @%s запущен (долгий опрос)", self.username)
        self._poll_loop()

    def _setup_webhook(self) -> None:
        assert self.api
        if not self.public_url.startswith("https://"):
            self.error = "для webhook нужен https-адрес telegram.public_url"
            log.error("Telegram: %s", self.error)
            return
        secret = hashlib.sha256(f"hook-secret|{self.webhook_secret or ''}|{self.token}".encode("utf-8")).hexdigest()[:48]
        try:
            self.api.set_webhook(self.public_url + self.webhook_path, secret)
            log.info("Telegram-бот @%s запущен (webhook)", self.username)
        except TelegramError as exc:
            self.error = exc.description
            log.error("Telegram: webhook не установлен: %s", exc.description)

    def webhook_header_ok(self, header: Optional[str]) -> bool:
        secret = hashlib.sha256(f"hook-secret|{self.webhook_secret or ''}|{self.token}".encode("utf-8")).hexdigest()[:48]
        return bool(header) and hmac.compare_digest(str(header), secret)

    # ------------------------------------------------------------------ получение сообщений
    def _poll_loop(self) -> None:
        assert self.api
        offset = self._load_offset()
        failures = 0
        while not self._stop.is_set():
            try:
                updates = self.api.get_updates(offset, timeout=25)
                failures = 0
                self.error = ""
                self.last_poll = time.time()
            except TelegramError as exc:
                failures += 1
                self.error = exc.description
                if exc.code == 409:
                    log.error("Telegram: этот бот уже опрашивается другим процессом (или у него включён webhook).")
                wait = min(60, 2 ** min(failures, 6))
                if self._stop.wait(wait):
                    return
                continue
            for upd in updates:
                offset = max(offset or 0, int(upd["update_id"]) + 1)
                self.dispatch(upd)
            if updates:
                self._save_offset(offset)

    def dispatch(self, update: dict) -> None:
        """Обработка обновления в пуле потоков: сообщение одного человека не задерживает остальных."""
        if self._pool is not None and self.bot is not None:
            try:
                self._pool.submit(self.bot.handle_update, update)
            except RuntimeError:
                pass  # пул остановлен

    def _load_offset(self) -> Optional[int]:
        raw = self.storage.db.meta_get(OFFSET_KEY)
        return int(raw) if raw and raw.isdigit() else None

    def _save_offset(self, offset: Optional[int]) -> None:
        if offset is not None:
            self.storage.db.meta_set(OFFSET_KEY, str(offset))

    # ------------------------------------------------------------------ очередь уведомлений
    def _outbox_loop(self) -> None:
        assert self.api
        tick = 0
        while not self._stop.wait(2.0):
            try:
                self.notifier.dispatch_due(self.api)
                tick += 1
                if tick % 150 == 0:  # раз в ~5 минут чистим старое
                    self.storage.notifications.purge_old()
            except Exception:
                log.exception("Сбой при отправке уведомлений")
