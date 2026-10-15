"""Вход администратора: cookie-сессия (токен хранится в БД только в виде хэша), блокировка после неудачных попыток."""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import HTTPException, Request, Response

from .security import LoginThrottle, burn_time, check_password_policy, hash_password, verify_password

log = logging.getLogger("dekanat.admin")

COOKIE_ADMIN = "dekanat_admin"
_USERNAME = re.compile(r"[A-Za-z0-9_.@-]{3,64}")


class AdminAuth:
    def __init__(self, storage, settings):
        self.storage = storage
        g = settings.get
        self.protect_edit = bool(g("admin.protect_edit", True))
        self.session_seconds = float(g("admin.session_hours", 8)) * 3600
        self.min_length = int(g("admin.min_password_length", 10))
        self.throttle = LoginThrottle(int(g("admin.max_login_failures", 0)), float(g("admin.lockout_minutes", 0)) * 60)
        self._secure = str(g("server.cookie_secure", "auto")).strip().lower()

    # ------------------------------------------------------------------ текущий администратор
    def current(self, request: Request) -> Optional[dict]:
        return self.storage.admins.get_session(request.cookies.get(COOKIE_ADMIN, ""))

    def require(self, request: Request) -> dict:
        """Зависимость FastAPI: без действующей сессии администратора — 401. Проверка выполняется на сервере для каждого запроса."""
        admin = self.current(request)
        if admin is None:
            raise HTTPException(401, "Требуется вход администратора")
        return admin

    def actor_for_edit(self, request: Request) -> str:
        """Правки Положения и типов документов: нужен вход администратора (admin.protect_edit: true) либо, в режиме совместимости, локальный запуск."""
        if not self.protect_edit:
            return "local"
        admin = self.current(request)
        if admin is None:
            if self.storage.admins.count() == 0:
                raise HTTPException(401, "Администратор ещё не создан. Создайте его командой: python -m dekanat create-admin")
            raise HTTPException(401, "Требуется вход администратора: откройте страницу /admin")
        return admin["username"]

    def can_edit(self, request: Request) -> bool:
        return self.protect_edit is False or self.current(request) is not None

    # ------------------------------------------------------------------ cookie
    def _use_secure(self, request: Request) -> bool:
        if self._secure in ("true", "1", "yes", "on"):
            return True
        if self._secure in ("false", "0", "no", "off"):
            return False
        return request.url.scheme == "https"

    def set_cookie(self, response: Response, request: Request, token: str) -> None:
        response.set_cookie(COOKIE_ADMIN, token, max_age=int(self.session_seconds), httponly=True, samesite="strict", secure=self._use_secure(request), path="/")

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(COOKIE_ADMIN, path="/")

    # ------------------------------------------------------------------ вход
    def login(self, request: Request, username: str, password: str) -> tuple[str, dict]:
        username = (username or "").strip()
        ip = request.client.host if request.client else "?"
        keys = (f"u:{username.lower()}", f"ip:{ip}")
        left = self.throttle.remaining(*keys)
        if left > 0:
            raise HTTPException(429, f"Слишком много неудачных попыток входа. Повторите через {int(left // 60) + 1} мин.")
        row = self.storage.admins.get_by_username(username) if username else None
        if row is not None and not row["disabled"] and verify_password(password or "", row["pw_hash"]):
            self.throttle.success(*keys)
            self.storage.admins.touch_login(row["id"])
            token = self.storage.admins.create_session(row["id"], self.session_seconds)
            self.storage.audit.log(row["username"], "admin.login", "admin", str(row["id"]))
            return token, {"username": row["username"], "role": row["role"]}
        if row is None:
            burn_time(password or "")  # время ответа не выдаёт, есть ли такой логин
        self.throttle.failure(*keys)
        self.storage.audit.log(username[:64] or "?", "admin.login_failed", "admin", "", {"ip": ip})
        log.warning("Неудачный вход администратора (логин «%s»)", username[:64])
        raise HTTPException(401, "Неверный логин или пароль")

    def logout(self, request: Request) -> None:
        token = request.cookies.get(COOKIE_ADMIN, "")
        admin = self.storage.admins.get_session(token)
        if token:
            self.storage.admins.delete_session(token)
        if admin:
            self.storage.audit.log(admin["username"], "admin.logout", "admin", str(admin["id"]))

    # ------------------------------------------------------------------ учётные записи
    def create_admin(self, username: str, password: str) -> int:
        username = (username or "").strip()
        if not _USERNAME.fullmatch(username):
            raise ValueError("Логин: 3–64 символа, латинские буквы, цифры и «_ . @ -»")
        error = check_password_policy(password, username, self.min_length)
        if error:
            raise ValueError(error)
        admin_id = self.storage.admins.create(username, hash_password(password))
        self.storage.audit.log("system", "admin.create", "admin", str(admin_id), {"username": username})
        return admin_id

    def change_password(self, admin: dict, current: str, new: str, keep_token: str = "") -> None:
        row = self.storage.admins.get_by_username(admin["username"])
        if row is None or not verify_password(current or "", row["pw_hash"]):
            raise HTTPException(400, "Текущий пароль указан неверно")
        error = check_password_policy(new, admin["username"], self.min_length)
        if error:
            raise HTTPException(400, error)
        self.storage.admins.set_password(row["id"], hash_password(new))
        self.storage.admins.delete_sessions_of(row["id"], except_token=keep_token or None)  # остальные входы завершаются
        self.storage.audit.log(admin["username"], "admin.password_change", "admin", str(row["id"]))
