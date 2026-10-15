"""Telegram: бот (текстовое управление и Mini App), вход на сайт через Telegram, уведомления.

Всё необязательно и по умолчанию выключено (telegram.enabled: false). Разбор документов и ответы считаются локально, как и без Telegram:
в сторону Telegram уходят только сообщения, которыми пользователь сам обменивается с ботом.
"""

from .auth import TelegramAuthError, verify_login_widget, verify_webapp_init_data

__all__ = ["TelegramAuthError", "verify_login_widget", "verify_webapp_init_data"]
