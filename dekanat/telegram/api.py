"""Клиент Telegram Bot API: тонкая обёртка над httpx. Токен бота нигде не печатается и не попадает в тексты ошибок."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger("dekanat.telegram")

DEFAULT_URL = "https://api.telegram.org"
MAX_DOWNLOAD = 20 * 1024 * 1024  # потолок Bot API на скачивание файлов


class TelegramError(Exception):
    """Ответ Bot API с ошибкой или нет связи. code — код Bot API (0 — сеть), retry_after — сколько ждать при flood control."""

    def __init__(self, code: int, description: str, retry_after: float = 0.0):
        super().__init__(f"Telegram {code}: {description}")
        self.code, self.description, self.retry_after = code, description, retry_after

    @property
    def blocked(self) -> bool:
        """Пользователь заблокировал бота или чат недоступен: писать туда больше нет смысла."""
        d = self.description.lower()
        return self.code in (401, 403) or (self.code == 400 and ("chat not found" in d or "user is deactivated" in d))


class TelegramAPI:
    def __init__(self, token: str, base_url: str = DEFAULT_URL, client: Optional[httpx.Client] = None, timeout: float = 35.0):
        if not token:
            raise ValueError("Не задан токен бота")
        self._token = token
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def __repr__(self) -> str:  # токен не должен случайно попасть в лог или трассировку
        return f"TelegramAPI({self.base_url}, token=***)"

    # ------------------------------------------------------------------ вызов метода
    def call(self, method: str, params: Optional[dict] = None, files: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
        url = f"{self.base_url}/bot{self._token}/{method}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}  # пустые необязательные поля Telegram получать не должен
        data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in clean.items()}
        try:
            resp = self._client.post(url, data=data, files=files, timeout=timeout) if files else self._client.post(url, json=clean, timeout=timeout)
        except httpx.HTTPError as exc:
            raise TelegramError(0, f"нет связи с Telegram ({type(exc).__name__})") from None  # без исходного исключения: в его тексте есть адрес с токеном
        try:
            body = resp.json()
        except ValueError:
            raise TelegramError(resp.status_code, "Telegram вернул не JSON") from None
        if not body.get("ok"):
            raise TelegramError(int(body.get("error_code") or resp.status_code), str(body.get("description") or "ошибка"),
                                float((body.get("parameters") or {}).get("retry_after") or 0))
        return body.get("result")

    # ------------------------------------------------------------------ методы, которые нужны боту
    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: Optional[int], timeout: int = 25, allowed: Optional[list] = None) -> list:
        return self.call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": allowed or ["message", "callback_query"]}, timeout=timeout + 15) or []

    def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None, parse_mode: Optional[str] = "HTML") -> dict:
        return self.call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "reply_markup": reply_markup, "disable_web_page_preview": True})

    def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None, parse_mode: Optional[str] = "HTML") -> dict:
        return self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode,
                                            "reply_markup": reply_markup if reply_markup is not None else {"inline_keyboard": []}, "disable_web_page_preview": True})

    def edit_reply_markup(self, chat_id: int, message_id: int, reply_markup: Optional[dict] = None) -> Any:
        return self.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup or {"inline_keyboard": []}})

    def delete_message(self, chat_id: int, message_id: int) -> Any:
        return self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def answer_callback(self, callback_id: str, text: str = "", alert: bool = False) -> Any:
        return self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190] or None, "show_alert": alert or None})

    def send_chat_action(self, chat_id: int, action: str = "typing") -> Any:
        return self.call("sendChatAction", {"chat_id": chat_id, "action": action})

    def send_photo(self, chat_id: int, data: bytes, caption: str = "", filename: str = "photo.jpg", reply_markup: Optional[dict] = None) -> dict:
        return self.call("sendPhoto", {"chat_id": chat_id, "caption": caption or None, "parse_mode": "HTML", "reply_markup": reply_markup}, files={"photo": (filename, data)}, timeout=60)

    def send_document(self, chat_id: int, data: bytes, filename: str, caption: str = "", reply_markup: Optional[dict] = None) -> dict:
        return self.call("sendDocument", {"chat_id": chat_id, "caption": caption or None, "parse_mode": "HTML", "reply_markup": reply_markup},
                         files={"document": (filename, data)}, timeout=60)

    def get_file(self, file_id: str) -> dict:
        return self.call("getFile", {"file_id": file_id})

    def download_file(self, file_id: str, max_bytes: int = MAX_DOWNLOAD) -> bytes:
        """Файл, присланный пользователю боту (фото, PDF). Размер ограничен: больше лимита — отказ, а не загрузка в память."""
        info = self.get_file(file_id)
        if int(info.get("file_size") or 0) > max_bytes:
            raise TelegramError(413, "файл слишком большой")
        path = info.get("file_path")
        if not path:
            raise TelegramError(400, "Telegram не отдал файл")
        url = f"{self.base_url}/file/bot{self._token}/{path}"
        try:
            with self._client.stream("GET", url, timeout=60) as resp:
                if resp.status_code != 200:
                    raise TelegramError(resp.status_code, "не удалось скачать файл")
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise TelegramError(413, "файл слишком большой")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise TelegramError(0, f"нет связи с Telegram ({type(exc).__name__})") from None
        return b"".join(chunks)

    def set_my_commands(self, commands: list[dict]) -> Any:
        return self.call("setMyCommands", {"commands": commands})

    def set_chat_menu_button(self, web_app_url: Optional[str] = None, text: str = "Приложение") -> Any:
        button = {"type": "web_app", "text": text, "web_app": {"url": web_app_url}} if web_app_url else {"type": "commands"}
        return self.call("setChatMenuButton", {"menu_button": button})

    def set_my_description(self, description: str, short: str = "") -> None:
        self.call("setMyDescription", {"description": description})
        if short:
            self.call("setMyShortDescription", {"short_description": short})

    def set_webhook(self, url: str, secret: str) -> Any:
        return self.call("setWebhook", {"url": url, "secret_token": secret, "allowed_updates": ["message", "callback_query"], "drop_pending_updates": False})

    def delete_webhook(self) -> Any:
        return self.call("deleteWebhook", {"drop_pending_updates": False})


def profile_of(frm: dict) -> dict:
    """Профиль пользователя из поля `from` обновления Telegram: ровно то, что хранит аккаунт."""
    return {"id": int(frm["id"]), "username": frm.get("username"), "first_name": frm.get("first_name"), "last_name": frm.get("last_name"), "language": frm.get("language_code")}
