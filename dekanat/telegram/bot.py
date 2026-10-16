"""Telegram-бот «Онлайн-деканата»: то же, что сайт, в переписке. Текстовое управление (команды, обычные сообщения, кнопки) и кнопка Mini App.

Бот работает поверх того же ядра, что и сайт (`AssistantCore`): анкета, карточка документа и готовые файлы лежат в «сессии данных»
аккаунта, поэтому начатое в боте продолжается на сайте и наоборот. Документы распознаются локально (Tesseract), ответы — по правилам и Положению;
в Telegram уходят только сообщения этой переписки. Снимки не сохраняются: запоминается только идентификатор файла Telegram, чтобы
повторить вопрос по тому же документу.

Здесь — ядро: сборка, разбор обновлений, отправка сообщений. Темы вынесены в примеси: `commands` (команды), `documents` (файл и вопросы),
`forms` (анкета и обращение), `callbacks` (кнопки).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from ..accounts import Accounts
from ..assistant import AssistantCore, ServiceError
from .api import TelegramAPI, TelegramError, profile_of
from .callbacks import CallbacksMixin
from .commands import CommandsMixin
from .documents import FILE_EXT, MAX_TG_FILE, DocumentsMixin
from .forms import FormsMixin
from .notify import Notifier
from .views import COMMANDS, MENU, esc, split_message

log = logging.getLogger("dekanat.telegram")

GENERIC_ERROR = "Не получилось выполнить запрос. Попробуйте ещё раз чуть позже."


class RateLimiter:
    """Не больше `limit` событий за `window` секунд от одного ключа: бот не должен становиться способом перегрузить распознавание."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[Any, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: Any) -> bool:
        now = time.time()
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(recent) >= self.limit:
                self._hits[key] = recent
                return False
            self._hits[key] = recent + [now]
            if len(self._hits) > 5000:
                self._hits.clear()
            return True


class TelegramBot(CommandsMixin, DocumentsMixin, FormsMixin, CallbacksMixin):
    def __init__(self, api: TelegramAPI, svc, storage, core: AssistantCore, accounts: Accounts, notifier: Notifier, settings, ocr_slots, locks):
        self.api, self.svc, self.storage, self.core, self.accounts, self.notifier = api, svc, storage, core, accounts, notifier
        self.settings, self.ocr_slots, self.locks = settings, ocr_slots, locks
        self.username = str(settings.get("telegram.bot_username") or "")
        self.public_url = str(settings.get("telegram.public_url") or "").rstrip("/")
        self.max_bytes = min(MAX_TG_FILE, int(settings.get("server.max_upload_mb", 25)) * 1024 * 1024)
        self.limiter = RateLimiter(int(settings.get("telegram.rate_per_minute", 20)), 60)
        self.file_limiter = RateLimiter(int(settings.get("telegram.files_per_hour", 12)), 3600)
        self._user_locks: dict[int, threading.Lock] = {}
        self._guard = threading.Lock()
        self._files: "OrderedDict[str, bytes]" = OrderedDict()  # последние присланные файлы (в памяти, не на диске): повторный вопрос не качает файл заново

    # ------------------------------------------------------------------ настройка
    @property
    def webapp_url(self) -> Optional[str]:
        """Адрес Mini App. Telegram принимает только https: без публичного адреса кнопки приложения нет."""
        return f"{self.public_url}/?tg=1" if self.public_url.startswith("https://") else None

    def setup(self) -> None:
        """Команды, описание и кнопка меню бота. Ошибки не критичны: бот работает и без этого."""
        steps = [
            lambda: self.api.set_my_commands([{"command": c, "description": d} for c, d in COMMANDS]),
            lambda: self.api.set_my_description("Цифровой секретарь деканата: проверит справку по фото, ответит по Положению со ссылкой на пункт, поможет составить заявление.",
                                                "Секретарь деканата: справки, Положение, заявления"),
            lambda: self.api.set_chat_menu_button(self.webapp_url),
        ]
        for step in steps:
            try:
                step()
            except TelegramError as exc:
                log.warning("Настройка бота: %s", exc.description)

    # ------------------------------------------------------------------ вход обновлений
    def handle_update(self, update: dict) -> None:
        try:
            if isinstance(update.get("message"), dict):
                self._message(update["message"])
            elif isinstance(update.get("callback_query"), dict):
                self._callback(update["callback_query"])
        except TelegramError as exc:
            log.warning("Telegram: %s", exc)
        except Exception:
            log.exception("Ошибка при обработке обновления Telegram")
            frm = (update.get("message") or (update.get("callback_query") or {})).get("from") or {}
            if frm.get("id"):
                try:
                    self.api.send_message(frm["id"], GENERIC_ERROR)
                except TelegramError:
                    pass

    def _lock(self, tg_id: int) -> threading.Lock:
        with self._guard:
            if len(self._user_locks) > 5000:
                self._user_locks.clear()
            return self._user_locks.setdefault(tg_id, threading.Lock())

    # ------------------------------------------------------------------ сообщения
    def _message(self, msg: dict) -> None:
        chat, frm = msg.get("chat") or {}, msg.get("from") or {}
        if chat.get("type") != "private" or frm.get("is_bot") or not frm.get("id"):
            return  # бот работает только в личной переписке
        with self._lock(int(frm["id"])):
            if not self.limiter.hit(frm["id"]):
                self.api.send_message(frm["id"], "Слишком много сообщений подряд. Подождите минуту и продолжим.")
                return
            user, created = self.accounts.upsert_tg(profile_of(frm))
            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                cmd, _, arg = text.partition(" ")
                return self._command(user, cmd.split("@")[0].lower(), arg.strip(), created)
            photo = msg.get("photo")
            doc = msg.get("document")
            if photo:
                best = max(photo, key=lambda p: (p.get("file_size") or 0, p.get("width", 0) * p.get("height", 0)))
                return self._file(user, best["file_id"], "photo.jpg", best.get("file_size") or 0, msg.get("caption") or "")
            if doc:
                name = doc.get("file_name") or "document"
                mime = (doc.get("mime_type") or "").lower()
                if not (name.lower().endswith(FILE_EXT) or mime in ("image/jpeg", "image/png", "application/pdf")):
                    self._send(user, "Такой файл я не читаю. Пришлите фото (JPG, PNG) или PDF с документом.")
                    return
                return self._file(user, doc["file_id"], name, doc.get("file_size") or 0, msg.get("caption") or "")
            if text:
                return self._text(user, text)
            self._send(user, "Пока я понимаю текст, фото и PDF. Пришлите документ или напишите вопрос.")

    def _command(self, u: dict, cmd: str, arg: str, created: bool = False) -> None:
        if cmd == "/start":
            if arg.startswith("login_"):
                return self._login_request(u, arg[len("login_"):])
            return self._hello(u)
        handlers = {"/help": self._hello, "/menu": self._hello, "/check": self._cmd_check, "/ask": self._cmd_ask, "/form": self._cmd_form, "/anketa": self._cmd_form,
                    "/tickets": self._cmd_tickets, "/settings": self._cmd_settings, "/app": self._cmd_app, "/forget": self._cmd_forget, "/privacy": self._cmd_privacy,
                    "/cancel": self._cmd_cancel}
        if cmd == "/status":
            return self._cmd_status(u, arg)
        fn = handlers.get(cmd)
        if fn is None:
            self._send(u, "Такой команды я не знаю. Список — /help или меню под полем ввода.")
            return
        fn(u)

    def _text(self, u: dict, text: str) -> None:
        action = MENU.get(text)
        if action:
            return {"check": self._cmd_check, "ask": self._cmd_ask, "form": self._cmd_form, "tickets": self._cmd_tickets, "settings": self._cmd_settings}[action](u)
        state = self.storage.users.bot_state(u["id"])
        if state.get("await") == "status":
            state.pop("await", None)
            self.storage.users.save_bot_state(u["id"], state)
            return self._cmd_status(u, text)
        self._typing(u)
        sid = self.accounts.ensure_data_session(u)
        try:
            res = self.core.message(sid, text, defer=True)
        except ServiceError as exc:
            self._send(u, esc(exc.detail))
            return
        if res["reply"]["kind"] != "passthrough":
            return self._dialog(u, sid, res)
        self._question(u, sid, text)

    # ------------------------------------------------------------------ отправка сообщений
    def _typing(self, u: dict, action: str = "typing") -> None:
        try:
            self.api.send_chat_action(u["tg_id"], action)
        except TelegramError:
            pass

    def _send(self, u: dict, text: str, markup: Optional[dict] = None) -> Optional[dict]:
        """Сообщение (длинное — несколькими); кнопки — под последним."""
        parts = split_message(text) or ["…"]
        last = None
        for i, part in enumerate(parts):
            last = self.api.send_message(u["tg_id"], part, markup if i == len(parts) - 1 else None)
        return last

    def _edit_or_send(self, u: dict, sent: Optional[dict], text: str, markup: Optional[dict] = None) -> None:
        """Заменяет сообщение «Читаю документ…» результатом; не получилось (или результат длинный) — отправляет новыми сообщениями."""
        parts = split_message(text) or ["…"]
        if sent and len(parts) == 1:
            try:
                self.api.edit_message_text(u["tg_id"], sent["message_id"], parts[0], markup)
                return
            except TelegramError:
                pass
        if sent:
            try:
                self.api.delete_message(u["tg_id"], sent["message_id"])
            except TelegramError:
                pass
        self._send(u, text, markup)
