"""Уведомления в Telegram: ответы деканата по обращениям, напоминания, сообщения сотрудникам о новых обращениях.

Сообщения ставятся в очередь в базе и уходят отдельным потоком с повторами: если Telegram недоступен, ответ не теряется.
Человек, заблокировавший бота, больше не получает уведомлений (notify выключается).
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

from .api import TelegramAPI, TelegramError
from .views import STATUS_EMOJI, STATUS_TITLES, esc, short

log = logging.getLogger("dekanat.telegram")


def ticket_update_text(ticket: dict, status_changed: bool, reply: bool) -> str:
    lines = []
    if status_changed:
        lines.append(f"{STATUS_EMOJI.get(ticket['status'], '•')} <b>Обращение № {ticket['id']}:</b> {esc(STATUS_TITLES.get(ticket['status'], ticket['status']))}")
    else:
        lines.append(f"💬 <b>Обращение № {ticket['id']}:</b> есть ответ деканата")
    if reply and ticket.get("reply"):
        lines += ["", "<b>Сообщение от деканата:</b>", esc(ticket["reply"])]
    lines += ["", "<i>Все обращения — /tickets. Окончательное решение принимает сотрудник деканата.</i>"]
    return "\n".join(lines)


class Notifier:
    def __init__(self, storage, settings):
        self.storage = storage
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("telegram.enabled", False)) and bool(self.settings.get("telegram.bot_token"))

    # ------------------------------------------------------------------ события
    def ticket_created(self, ticket: dict, owner: Optional[dict], source: str = "web") -> None:
        """Новое обращение: сообщение в чат сотрудников (если задан) и подтверждение вошедшему, отправившему обращение с сайта."""
        if not self.enabled:
            return
        staff = self.settings.get("telegram.staff_chat_id")
        if staff:
            url = (self.settings.get("telegram.public_url") or "").rstrip("/")
            link = f'\n<a href="{esc(url)}/admin#tickets">Открыть в кабинете</a>' if url.startswith("http") else ""
            text = f"🆕 <b>Новое обращение № {ticket['id']}</b>\n{esc(ticket.get('topic') or 'Обращение')}{link}"
            self.storage.notifications.add(int(staff), "staff", text, None, dedupe_key=f"new:{ticket['id']}")
        if owner and owner.get("notify") and source != "bot":
            self.storage.notifications.add(owner["tg_id"], "ticket", f"📬 <b>Обращение № {ticket['id']}</b> принято и ждёт рассмотрения. Ответ деканата придёт сюда.",
                                           owner["id"], dedupe_key=f"ack:{ticket['id']}")

    def ticket_changed(self, before: Optional[dict], after: Optional[dict]) -> None:
        """Сотрудник сменил статус или написал ответ: владельцу обращения уходит уведомление."""
        if not self.enabled or not before or not after:
            return
        owner_id = self.storage.tickets.owner_of(after["id"])
        user = self.storage.users.get(owner_id) if owner_id is not None else None
        if not user or not user["notify"]:
            return
        status_changed = before["status"] != after["status"]
        reply_changed = bool(after.get("reply")) and (after.get("reply") or "") != (before.get("reply") or "")
        if not status_changed and not reply_changed:
            return
        key = f"upd:{after['id']}:{after['status']}:{hashlib.sha1((after.get('reply') or '').encode('utf-8')).hexdigest()[:8]}"
        self.storage.notifications.add(user["tg_id"], "ticket", ticket_update_text(after, status_changed, reply_changed), user["id"], dedupe_key=key)

    def reminder(self, user: dict, text: str, when: float, limit: int = 3) -> Optional[int]:
        """Напоминание человеку в назначенное время. Не больше `limit` ожидающих напоминаний на человека."""
        if not self.enabled or self.storage.notifications.pending_for_user(user["id"], "reminder") >= limit:
            return None
        return self.storage.notifications.add(user["tg_id"], "reminder", "⏰ <b>Напоминание</b>\n" + esc(short(text, 900)), user["id"], send_after=when)

    # ------------------------------------------------------------------ отправка
    def dispatch_due(self, api: TelegramAPI, limit: int = 20) -> int:
        """Отправляет то, что пора отправить. Возвращает число отправленных."""
        sent = 0
        for n in self.storage.notifications.due(limit):
            try:
                api.send_message(n["chat_id"], n["text"])
            except TelegramError as exc:
                if exc.retry_after:
                    self.storage.notifications.postpone(n["id"], exc.retry_after + 1)
                elif exc.blocked:
                    self.storage.notifications.mark_failed(n["id"], exc.description, final=True)
                    if n.get("user_id"):
                        self.storage.users.set_notify(n["user_id"], False)  # писать тому, кто заблокировал бота, незачем
                else:
                    self.storage.notifications.mark_failed(n["id"], exc.description)
                    log.warning("Уведомление %s не отправлено: %s", n["id"], exc.description)
                continue
            self.storage.notifications.mark_sent(n["id"])
            sent += 1
            time.sleep(0.04)  # не больше ~25 сообщений в секунду: лимиты Telegram
        return sent
