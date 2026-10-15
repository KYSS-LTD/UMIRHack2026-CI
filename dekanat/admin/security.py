"""Пароли и защита входа администратора. Только стандартная библиотека (scrypt), без универсальных паролей и без секретов в коде."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import time
from collections import defaultdict
from typing import Optional

_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32
_MAXMEM = 64 * 1024 * 1024

_COMMON = {
    "password", "password1", "qwerty", "qwertyuiop", "1234567890", "12345678", "123456789", "0123456789", "admin", "administrator",
    "adminadmin", "letmein", "iloveyou", "qwerty123", "пароль", "пароль123", "йцукен", "йцукенгшщз",
}


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, want = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        got = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(want), maxmem=_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


# хэш для сравнения, когда пользователя нет: время ответа не выдаёт, существует ли логин
_DUMMY = hash_password("dummy-password-for-timing")


def burn_time(password: str) -> None:
    verify_password(password, _DUMMY)


def check_password_policy(password: str, username: str = "", min_length: int = 10) -> Optional[str]:
    """Возвращает текст ошибки или None, если пароль подходит."""
    if len(password) < min_length:
        return f"Пароль должен быть не короче {min_length} символов"
    if len(password) > 256:
        return "Пароль слишком длинный"
    low = password.lower()
    if low in _COMMON or (username and low == username.lower()):
        return "Слишком простой пароль"
    if password.isdigit() or len(set(password)) < 4:
        return "Слишком простой пароль: добавьте разные буквы и цифры"
    return None


class LoginThrottle:
    """Необязательная блокировка после серии неудачных входов (по логину и по адресу клиента). Хранится в памяти процесса.

    По умолчанию выключена (`max_failures <= 0` или `lockout_seconds <= 0`): неверный пароль просто отклоняется, без ожидания.
    Включать её стоит там, где сервис доступен из интернета.
    """

    def __init__(self, max_failures: int = 0, lockout_seconds: float = 0.0):
        self.max = max(0, max_failures)
        self.lockout = max(0.0, lockout_seconds)
        self._fail: dict[str, list[float]] = defaultdict(list)
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.max > 0 and self.lockout > 0

    def remaining(self, *keys: str) -> float:
        """Сколько секунд ещё действует блокировка (0 — вход разрешён)."""
        if not self.enabled:
            return 0.0
        now = time.monotonic()
        with self._lock:
            left = [self._until[k] - now for k in keys if k in self._until and self._until[k] > now]
            for k in keys:
                if k in self._until and self._until[k] <= now:
                    del self._until[k]
                    self._fail.pop(k, None)
        return max(left, default=0.0)

    def failure(self, *keys: str) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        with self._lock:
            for k in keys:
                recent = [t for t in self._fail[k] if now - t < self.lockout] + [now]
                self._fail[k] = recent
                if len(recent) >= self.max:
                    self._until[k] = now + self.lockout

    def success(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._fail.pop(k, None)
                self._until.pop(k, None)
