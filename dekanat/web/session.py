"""Cookie-сессии посетителей сайта: анонимные и вошедшие через Telegram. Все данные привязаны к «сессии данных» (у аккаунта она общая с ботом)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, Response

log = logging.getLogger("dekanat.session")

COOKIE_SID = "dekanat_sid"


@dataclass
class Visitor:
    cookie: str  # идентификатор cookie-сессии этого браузера
    sid: str  # сессия, в которой лежат данные: у вошедшего — данные аккаунта (общие с ботом), у остальных — сама cookie-сессия
    status: str  # ok | new | expired
    user: Optional[dict] = None


class WebSessions:
    def __init__(self, storage, accounts, settings, cookie_secure):
        self.storage = storage
        self.accounts = accounts
        self.settings = settings
        self.cookie_secure = cookie_secure
        self._created: dict[str, list[float]] = {}  # адрес -> время создания последних сессий (защита от массового создания)
        self._last_purge = 0.0

    def maybe_purge(self) -> None:
        if time.time() - self._last_purge > 600:
            self._last_purge = time.time()
            try:
                self.storage.purge()
            except Exception:  # чистка не должна мешать работе
                log.exception("Не удалось очистить просроченные данные")

    def throttle_new_sessions(self, request: Request) -> None:
        limit = int(self.settings.get("server.max_new_sessions_per_minute", 60))
        ip = request.client.host if request.client else "?"
        now = time.time()
        recent = [t for t in self._created.get(ip, []) if now - t < 60]
        if limit and len(recent) >= limit:
            self._created[ip] = recent
            raise HTTPException(429, "Слишком много новых сессий с вашего адреса. Подождите минуту и повторите.")
        self._created[ip] = recent + [now]
        if len(self._created) > 5000:
            self._created.clear()

    def set_cookie(self, response: Response, request: Request, sid: str, signed_in: bool = False) -> None:
        ttl = self.accounts.user_ttl if signed_in else self.accounts.anon_ttl
        response.set_cookie(COOKIE_SID, sid, max_age=int(ttl), httponly=True, samesite="lax", secure=self.cookie_secure(request), path="/")

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(COOKIE_SID, path="/")

    def ensure(self, request: Request, response: Response) -> Visitor:
        """Сессия посетителя; пустая или просроченная заменяется новой анонимной."""
        self.maybe_purge()  # сроки хранения соблюдаются и на сервере, где новых сессий давно нет
        cookie = request.cookies.get(COOKIE_SID, "")
        if cookie and self.accounts.touch_cookie(cookie):
            user = self.accounts.user_of_cookie(cookie)
            return Visitor(cookie, self.accounts.data_sid(cookie), "ok", user)
        self.throttle_new_sessions(request)
        sid = self.storage.sessions.create(self.accounts.anon_ttl)
        self.set_cookie(response, request, sid)
        return Visitor(sid, sid, "expired" if cookie else "new", None)

    def require(self, request: Request) -> Visitor:
        self.maybe_purge()
        cookie = request.cookies.get(COOKIE_SID, "")
        if not cookie or not self.accounts.touch_cookie(cookie):
            raise HTTPException(410, "Сессия истекла или не найдена: данные удалены. Обновите страницу и начните заново.")
        user = self.accounts.user_of_cookie(cookie)
        return Visitor(cookie, self.accounts.data_sid(cookie), "ok", user)

    def peek(self, request: Request) -> str:
        """Сессия данных, если она есть (ничего не создаёт): для проверок со стола, где без сессии всё работает как прежде."""
        cookie = request.cookies.get(COOKIE_SID, "")
        if cookie and self.accounts.touch_cookie(cookie):
            return self.accounts.data_sid(cookie)
        return ""
