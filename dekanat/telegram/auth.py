"""Проверка подписи Telegram: данные Mini App (initData) и виджет входа. Без обращений к сети — только HMAC-SHA256 с токеном бота.

Подпись подтверждает, что данные выданы самим Telegram для этого бота: подделать их, не зная токен, нельзя.
Срок жизни ограничен (auth_date), чтобы украденная строка не работала вечно.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import parse_qsl


class TelegramAuthError(ValueError):
    """Подпись неверна, данные устарели или имеют неожиданный вид."""


def _check_string(pairs: dict[str, str]) -> str:
    return "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))


def _profile(user: dict) -> dict:
    try:
        tg_id = int(user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramAuthError("В данных Telegram нет идентификатора пользователя") from exc
    if tg_id <= 0 or user.get("is_bot"):
        raise TelegramAuthError("Вход разрешён только обычным пользователям Telegram")
    clean = lambda v, n: (str(v).strip()[:n] or None) if v not in (None, "") else None  # noqa: E731
    return {"id": tg_id, "username": clean(user.get("username"), 64), "first_name": clean(user.get("first_name"), 64),
            "last_name": clean(user.get("last_name"), 64), "language": clean(user.get("language_code"), 8)}


def verify_webapp_init_data(init_data: str, bot_token: str, max_age_seconds: float = 86400, now: Optional[float] = None) -> dict:
    """Данные, которые Telegram передаёт Mini App (Telegram.WebApp.initData). Возвращает профиль пользователя.

    Ключ — HMAC-SHA256(«WebAppData», токен бота); подпись — HMAC-SHA256(ключ, строка из полей по алфавиту без hash).
    """
    if not init_data or not bot_token or len(init_data) > 8192:
        raise TelegramAuthError("Нет данных Telegram")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    got = pairs.pop("hash", "")
    if not got:
        raise TelegramAuthError("В данных Telegram нет подписи")
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    want = hmac.new(secret, _check_string(pairs).encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got.lower()):
        raise TelegramAuthError("Подпись Telegram не подошла")
    _fresh(pairs.get("auth_date"), max_age_seconds, now)
    try:
        user = json.loads(pairs.get("user", ""))
    except ValueError as exc:
        raise TelegramAuthError("В данных Telegram нет пользователя") from exc
    if not isinstance(user, dict):
        raise TelegramAuthError("В данных Telegram нет пользователя")
    return _profile(user)


def verify_login_widget(data: dict, bot_token: str, max_age_seconds: float = 86400, now: Optional[float] = None) -> dict:
    """Данные виджета «Войти через Telegram» (id, first_name, …, auth_date, hash). Ключ — SHA256(токен бота)."""
    pairs = {str(k): str(v) for k, v in (data or {}).items() if v is not None}
    got = pairs.pop("hash", "")
    if not got or not bot_token:
        raise TelegramAuthError("Нет подписи Telegram")
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    want = hmac.new(secret, _check_string(pairs).encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got.lower()):
        raise TelegramAuthError("Подпись Telegram не подошла")
    _fresh(pairs.get("auth_date"), max_age_seconds, now)
    return _profile({"id": pairs.get("id"), "username": pairs.get("username"), "first_name": pairs.get("first_name"),
                     "last_name": pairs.get("last_name"), "language_code": pairs.get("language_code")})


def _fresh(auth_date: Optional[str], max_age: float, now: Optional[float]) -> None:
    try:
        issued = float(auth_date or "")
    except ValueError as exc:
        raise TelegramAuthError("В данных Telegram нет времени выдачи") from exc
    current = now if now is not None else time.time()
    if issued > current + 300:
        raise TelegramAuthError("Время выдачи данных Telegram в будущем")
    if current - issued > max_age:
        raise TelegramAuthError("Данные Telegram устарели: откройте приложение заново")
