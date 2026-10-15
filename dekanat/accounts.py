"""Необязательные аккаунты: вход через Telegram, общие данные для сайта и бота.

Как это устроено:
* Без входа всё работает как раньше: анонимная сессия в cookie, данные удаляются через сутки.
* Со входом у человека есть «сессия данных» (kind='account'): в ней анкета, карточка документа и готовые файлы. Её же использует бот
  для того же Telegram-идентификатора, поэтому анкета, начатая в боте, продолжается на сайте и наоборот.
* Вход на сайте — отдельная cookie-сессия (user_id), её можно закрыть, не трогая данные. При входе cookie заменяется новой
  (защита от подмены сессии), а данные анонимной сессии переходят в аккаунт, если там своих ещё нет.
* Вход через бота требует подтверждения в Telegram: ссылку с чужого запроса нельзя «подсунуть» человеку так, чтобы он вошёл молча.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

log = logging.getLogger("dekanat.accounts")


class AccountError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def describe_device(user_agent: str, ip: str = "") -> str:
    """Короткая подпись устройства для окна подтверждения в Telegram: «Chrome, Windows · 203.0.*.*». Полный адрес не показывается и не хранится."""
    ua = user_agent or ""
    browser = next((name for pat, name in ((r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"YaBrowser", "Яндекс Браузер"), (r"Firefox/", "Firefox"),
                                           (r"Chrome/|CriOS", "Chrome"), (r"Safari/", "Safari")) if re.search(pat, ua)), "браузер")
    system = next((name for pat, name in ((r"Windows", "Windows"), (r"Android", "Android"), (r"iPhone|iPad|iOS", "iOS"), (r"Mac OS X|Macintosh", "macOS"),
                                           (r"Linux", "Linux")) if re.search(pat, ua)), "")
    masked = ""
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip or ""):
        masked = ".".join(ip.split(".")[:2]) + ".*.*"
    elif ip and ":" in ip:
        masked = ":".join(ip.split(":")[:2]) + ":*"
    return ", ".join(x for x in (browser, system) if x) + (f" · {masked}" if masked else "")


def display_name(user: dict) -> str:
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x).strip()
    return name or (f"@{user['username']}" if user.get("username") else f"Пользователь {user['id']}")


def public_user(user: dict) -> dict:
    """То, что видит сам человек на сайте (и ничего лишнего: ни Telegram-идентификатора, ни служебных полей)."""
    return {"id": user["id"], "name": display_name(user), "username": user.get("username"), "notify": bool(user.get("notify")),
            "created_at": user.get("created_at"), "linked_web": bool(user.get("web_linked_at"))}


class Accounts:
    def __init__(self, storage, settings):
        self.storage = storage
        self.settings = settings

    # ------------------------------------------------------------------ сроки
    @property
    def anon_ttl(self) -> float:
        return self.storage.session_ttl

    @property
    def user_ttl(self) -> float:
        return self.storage.account_session_ttl

    @property
    def data_ttl(self) -> float:
        return self.storage.account_data_ttl

    @property
    def login_ttl(self) -> float:
        return float(self.settings.get("telegram.login_ttl_seconds", 300))

    # ------------------------------------------------------------------ сессия сайта -> аккаунт -> данные
    def touch_cookie(self, cookie: str) -> bool:
        """Продлевает cookie-сессию: анонимную на сутки, вошедшую — на accounts.session_days."""
        return self.storage.sessions.touch(cookie, self.anon_ttl, self.user_ttl)

    def user_of_cookie(self, cookie: str) -> Optional[dict]:
        row = self.storage.sessions.get(cookie) if cookie else None
        if not row or row["user_id"] is None:
            return None
        return self.storage.users.get(int(row["user_id"]))

    def ensure_data_session(self, user: dict) -> str:
        """Сессия с данными аккаунта (общая для сайта и бота). Живёт, пока человек пользуется сервисом; истекла — создаётся новая пустая."""
        sid = user.get("data_sid")
        if sid and self.storage.sessions.touch(sid, self.data_ttl):
            return sid
        sid = self.storage.sessions.create(self.data_ttl, kind="account")
        self.storage.users.set_data_sid(user["id"], sid)
        return sid

    def data_sid(self, cookie: str) -> str:
        """Какой сессией читать и писать данные: у вошедшего — данными аккаунта, у остальных — самой cookie-сессией."""
        user = self.user_of_cookie(cookie)
        return self.ensure_data_session(user) if user else cookie

    def data_sid_for_tg(self, tg_id: int) -> Optional[str]:
        user = self.storage.users.get_by_tg(tg_id)
        return self.ensure_data_session(user) if user else None

    def user_by_data_sid(self, sid: str) -> Optional[dict]:
        return self.storage.users.by_data_sid(sid)

    # ------------------------------------------------------------------ аккаунт из Telegram
    def upsert_tg(self, profile: dict) -> tuple[dict, bool]:
        """Аккаунт по профилю из Telegram (бот или Mini App). Для бота это неявная регистрация: человек сам написал боту."""
        return self.storage.users.upsert_tg(profile["id"], profile.get("username"), profile.get("first_name"), profile.get("last_name"), profile.get("language"))

    def login_session(self, profile: dict, cookie: str) -> tuple[dict, str, dict]:
        """Вход на сайте. Возвращает (аккаунт, новая cookie-сессия, что перенесено из анонимной сессии).
        Старая cookie-сессия закрывается: идентификатор, известный до входа, после входа не работает."""
        user, _ = self.upsert_tg(profile)
        data_sid = self.ensure_data_session(user)
        old = self.storage.sessions.get(cookie) if cookie else None
        moved: dict = {"dialog": False, "document": False, "files": 0, "ticket": None}
        if old and old["user_id"] == user["id"]:
            return user, cookie, moved  # уже вошёл этим же аккаунтом
        if old and old["user_id"] is None:
            moved["ticket"] = self._claim_ticket(cookie, user["id"])  # обращение, отправленное из этого браузера до входа, становится обращением аккаунта
            moved.update(self.storage.sessions.move_data(cookie, data_sid))  # данные анонимной сессии не пропадают
        new_sid = self.storage.sessions.create(self.user_ttl, user_id=user["id"])
        if old:
            self._remove(self.storage.sessions.delete(cookie))
        self.storage.users.mark_web_linked(user["id"])
        log.info("Вход на сайте через Telegram: аккаунт %s (перенесено: %s)", user["id"], moved)
        return user, new_sid, moved

    def _claim_ticket(self, cookie: str, user_id: int) -> Optional[int]:
        """Обращение из анкеты этой анонимной сессии привязывается к аккаунту: ответ деканата придёт в Telegram и без ввода номера и кода.
        Доказательство владения — сама сессия: номер обращения лежит в её анкете. Чужое (уже привязанное) обращение не отбирается."""
        state = self.storage.sessions.load_state(cookie) or {}
        ticket_id = state.get("ticket_id")
        if isinstance(ticket_id, int) and self.storage.tickets.attach_user(ticket_id, user_id):
            return ticket_id
        return None

    def logout(self, cookie: str) -> None:
        """Выход с этого устройства: cookie-сессия закрывается, данные аккаунта остаются."""
        row = self.storage.sessions.get(cookie) if cookie else None
        if row and row["user_id"] is not None:
            self._remove(self.storage.sessions.delete(cookie))

    def delete_account(self, user_id: int) -> dict:
        return self.storage.delete_account(user_id)

    # ------------------------------------------------------------------ вход через бота
    def start_login(self, cookie: str, device: str) -> str:
        if self.storage.logins.pending_count(cookie) >= 5:
            raise AccountError(429, "Слишком много запросов входа подряд. Подождите несколько минут.")
        return self.storage.logins.create(cookie, self.login_ttl, device)

    def poll_login(self, token: str, cookie: str) -> tuple[str, Optional[dict]]:
        """(статус, профиль). Статус: pending | confirmed | denied | expired. Запрос отдаётся только той сессии, что его создала."""
        req = self.storage.logins.get(token)
        if not req or req["session_id"] != cookie:
            return "expired", None
        if req["state"] == "denied":
            return "denied", None
        if req["state"] == "confirmed":
            profile = dict(req["profile"] or {})
            profile.setdefault("id", req["tg_id"])
            if not self.storage.logins.consume(req["hash"]):
                return "expired", None  # вход выдаётся один раз
            return "confirmed", profile
        if req["state"] == "pending" and req["expires_at"] > time.time():
            return "pending", None
        return "expired", None

    def answer_login(self, prefix: str, profile: dict, approve: bool) -> str:
        """Ответ человека в Telegram на запрос входа. Возвращает: ok | denied | expired."""
        req = self.storage.logins.find_by_prefix(prefix)
        if not req or req["state"] != "pending":
            return "expired"
        if not approve:
            self.storage.logins.deny(req["hash"])
            return "denied"
        return "ok" if self.storage.logins.confirm(req["hash"], profile["id"], profile) else "expired"

    def describe_login(self, prefix: str) -> Optional[dict]:
        req = self.storage.logins.find_by_prefix(prefix)
        return req if req and req["state"] == "pending" else None

    # ------------------------------------------------------------------ обращения
    def link_ticket(self, user: dict, ticket_id: int, code: str) -> dict:
        """Привязывает ранее отправленное обращение к аккаунту по номеру и коду: ответы деканата будут приходить в Telegram."""
        row = self.storage.tickets.check_code(ticket_id, code)
        if row is None:
            raise AccountError(404, "Обращение с таким номером и кодом не найдено. Проверьте номер и код.")
        owner = self.storage.tickets.owner_of(ticket_id)
        if owner is not None and owner != user["id"]:
            raise AccountError(409, "Это обращение уже привязано к другому аккаунту.")
        self.storage.tickets.attach_user(ticket_id, user["id"])
        return row

    @staticmethod
    def _remove(paths: list[str]) -> None:
        from pathlib import Path

        for p in paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
