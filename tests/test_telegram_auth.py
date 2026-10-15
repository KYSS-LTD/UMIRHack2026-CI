"""Проверка подписи Telegram: данные Mini App и виджет входа. Подпись считается тем же способом, что и в документации Telegram."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from dekanat.telegram.auth import TelegramAuthError, verify_login_widget, verify_webapp_init_data

TOKEN = "123456:TEST-TOKEN-NOT-REAL"


def sign_webapp(user: dict, token: str = TOKEN, auth_date=None, extra=None) -> str:
    fields = {"auth_date": str(int(auth_date if auth_date is not None else time.time())), "query_id": "AAH-test", "user": json.dumps(user, ensure_ascii=False, separators=(",", ":"))}
    fields.update(extra or {})
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def sign_widget(data: dict, token: str = TOKEN) -> dict:
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    data = dict(data)
    data["hash"] = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


USER = {"id": 4242, "first_name": "Анна", "last_name": "Петрова", "username": "anna_p", "language_code": "ru"}


def test_webapp_valid_returns_profile():
    p = verify_webapp_init_data(sign_webapp(USER), TOKEN)
    assert p == {"id": 4242, "username": "anna_p", "first_name": "Анна", "last_name": "Петрова", "language": "ru"}


def test_webapp_wrong_token_or_tampered_rejected():
    good = sign_webapp(USER)
    with pytest.raises(TelegramAuthError):
        verify_webapp_init_data(good, "999:OTHER")
    tampered = good.replace("4242", "4243")
    with pytest.raises(TelegramAuthError):
        verify_webapp_init_data(tampered, TOKEN)


def test_webapp_missing_hash_and_garbage_rejected():
    for bad in ("", "user=%7B%7D", "a=b&hash=", "x" * 9000):
        with pytest.raises(TelegramAuthError):
            verify_webapp_init_data(bad, TOKEN)


def test_webapp_expired_and_future_rejected():
    with pytest.raises(TelegramAuthError, match="устарели"):
        verify_webapp_init_data(sign_webapp(USER, auth_date=time.time() - 3 * 86400), TOKEN, max_age_seconds=86400)
    with pytest.raises(TelegramAuthError, match="будущем"):
        verify_webapp_init_data(sign_webapp(USER, auth_date=time.time() + 3600), TOKEN)


def test_webapp_bots_and_bad_ids_rejected():
    for u in ({"id": 5, "is_bot": True}, {"id": -3}, {"id": "abc"}, {"first_name": "x"}):
        with pytest.raises(TelegramAuthError):
            verify_webapp_init_data(sign_webapp(u), TOKEN)


def test_webapp_profile_is_trimmed():
    p = verify_webapp_init_data(sign_webapp({"id": 7, "first_name": "Я" * 300, "username": ""}), TOKEN)
    assert len(p["first_name"]) == 64 and p["username"] is None


def test_widget_valid_and_tampered():
    data = sign_widget({"id": "99", "first_name": "Олег", "username": "oleg", "auth_date": str(int(time.time()))})
    assert verify_login_widget(data, TOKEN)["id"] == 99
    data["id"] = "100"
    with pytest.raises(TelegramAuthError):
        verify_login_widget(data, TOKEN)


def test_widget_and_webapp_keys_are_not_interchangeable():
    """Подпись виджета не годится для Mini App и наоборот: ключи выводятся по-разному."""
    now = str(int(time.time()))
    widget = sign_widget({"id": "99", "auth_date": now})
    with pytest.raises(TelegramAuthError):
        verify_webapp_init_data(urlencode(widget), TOKEN)
