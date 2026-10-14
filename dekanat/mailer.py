"""Письмо студенту с номером обращения и кодом статуса — по желанию и только если почта настроена (mail.* в config/settings.yaml).

Канал выключен по умолчанию. Пароль почтового сервера берётся только из переменной окружения DEKANAT_MAIL_PASSWORD — в репозитории секретов нет.
В письме нет ни ФИО, ни данных анкеты: только номер, код и ссылка. Сбой отправки не мешает обращению: сервис честно говорит, что письмо не ушло.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

log = logging.getLogger("dekanat.mail")

SENT, FAILED, DISABLED = "sent", "failed", "disabled"


def mask_email(address: str) -> str:
    """ivan@example.com -> i***@example.com: в чате и ответах сервера адрес целиком не повторяется."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class Mailer:
    def __init__(self, settings: Any):
        self.settings = settings

    # ------------------------------------------------------------------ состояние
    def _get(self, key: str, default: Any = "") -> Any:
        return self.settings.get(f"mail.{key}", default)

    @property
    def available(self) -> bool:
        return bool(self._get("enabled", False)) and bool(str(self._get("host")).strip()) and bool(str(self._get("from")).strip())

    def status(self) -> dict:
        if not self._get("enabled", False):
            return {"available": False, "reason": "почта не настроена (mail.enabled)"}
        if not self.available:
            return {"available": False, "reason": "в настройках почты не указан сервер или адрес отправителя (mail.host, mail.from)"}
        return {"available": True, "reason": ""}

    # ------------------------------------------------------------------ письмо
    def _body(self, ticket_id: int, code: str) -> str:
        base = str(self._get("public_url")).strip().rstrip("/")
        where = f"Страница статуса: {base}/#/status/{ticket_id} (введите номер и код)." if base else "Откройте сайт деканата: меню → «Статус обращения» — и введите номер и код."
        return (f"Здравствуйте!\n\nВаше обращение № {ticket_id} принято в деканат.\n\nКод для проверки статуса: {code}\n{where}\n\n"
                "Сохраните это письмо: по номеру и коду без входа видно, что решил деканат. Окончательное решение принимает сотрудник деканата.\n\n"
                "Если вы не отправляли обращение, просто проигнорируйте это письмо.\n")

    def send_ticket_code(self, to: str, ticket_id: int, code: str) -> str:
        """Возвращает SENT, FAILED или DISABLED. Исключений наружу не бросает."""
        if not self.available:
            return DISABLED
        msg = EmailMessage()
        msg["Subject"] = f"Обращение № {ticket_id}: номер и код для проверки статуса"
        msg["From"] = str(self._get("from")).strip()
        msg["To"] = to
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=str(self._get("from")).rpartition("@")[2] or None)
        msg.set_content(self._body(ticket_id, code))
        host, port = str(self._get("host")).strip(), int(self._get("port", 587) or 587)
        security = str(self._get("security", "starttls")).lower()
        try:
            if security == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(host, port, timeout=15)
            with server:
                if security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                user, password = str(self._get("username")).strip(), os.environ.get("DEKANAT_MAIL_PASSWORD", "")
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
            return SENT
        except Exception as exc:  # сеть, TLS, вход, отказ адреса — всё это «письмо не ушло», а не сбой обращения
            log.warning("Письмо с кодом обращения не отправлено: %s", exc)
            return FAILED
