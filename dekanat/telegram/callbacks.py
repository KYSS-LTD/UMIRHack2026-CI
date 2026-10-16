"""Нажатия кнопок под сообщениями бота.

Работает как часть `TelegramBot`: поля (`api`, `storage`, `core`, `accounts` …) и общие методы (`_send`, `_edit_or_send`, `_typing`, `_text`) приходят из него.
"""

from __future__ import annotations

import time
from typing import Optional

from ..assistant import ServiceError
from .api import TelegramError, profile_of
from .documents import QUICK_QUESTIONS
from .views import btn, esc, kb, render_next_steps, render_ticket, short

# Короткая подсказка-«тост» при нажатии кнопки: человек сразу видит, что нажатие принято, пока бот работает
CALLBACK_TOASTS = (("lg:y:", "Подтверждаю вход…"), ("lg:n:", "Отклоняю запрос…"), ("sub:ok", "Отправляю в деканат…"), ("doc:img", "Рисую подсветку…"),
                   ("q:", "Ищу ответ…"), ("ns:", "Смотрю в Положении…"), ("set:data!", "Удаляю…"), ("set:notify", "Сохраняю…"), ("rem:", "Ставлю напоминание…"),
                   ("form:transfer", "Начинаем анкету"), ("form:enrollment", "Начинаем анкету"), ("link:", "Привязываю обращение…"))


def toast_for(data: str) -> str:
    return next((text for prefix, text in CALLBACK_TOASTS if data.startswith(prefix)), "")


class CallbacksMixin:
    def _callback(self, cq: dict) -> None:
        frm, msg = cq.get("from") or {}, cq.get("message") or {}
        data = str(cq.get("data") or "")
        if frm.get("is_bot") or not frm.get("id") or (msg.get("chat") or {}).get("type") != "private":
            return
        try:
            self.api.answer_callback(cq["id"], toast_for(data))
        except TelegramError:
            pass
        with self._lock(int(frm["id"])):
            if not self.limiter.hit(frm["id"]):
                return
            u, _ = self.accounts.upsert_tg(profile_of(frm))
            mid = msg.get("message_id")
            act, _, arg = data.partition(":")
            if act == "lg":  # lg:y|n:<префикс запроса>
                ans, _, prefix = arg.partition(":")
                return self._login_answer(u, mid, prefix, ans == "y")
            handler = {"m": self._cb_menu, "o": self._cb_option, "form": self._cb_form, "tpl": self._cb_template, "sub": self._cb_submit, "ns": self._cb_next,
                       "rem": self._cb_remind, "q": self._cb_quick, "t": self._cb_ticket, "link": self._cb_link, "set": self._cb_settings, "doc": self._cb_doc}.get(act)
            if handler:
                handler(u, arg, mid)

    def _clear_buttons(self, u: dict, mid: Optional[int]) -> None:
        if mid:
            try:
                self.api.edit_reply_markup(u["tg_id"], mid, None)
            except TelegramError:
                pass

    def _login_answer(self, u: dict, mid: Optional[int], prefix: str, approve: bool) -> None:
        result = self.accounts.answer_login(prefix, profile_of({"id": u["tg_id"], "username": u.get("username"), "first_name": u.get("first_name"),
                                                                "last_name": u.get("last_name"), "language_code": u.get("language")}), approve)
        text = {"ok": "✅ Готово. Вернитесь на сайт — вход выполнится сам. Теперь анкета и документы общие для сайта и бота.",
                "denied": "Хорошо, вход отклонён. Если это были не вы, ничего делать не нужно.",
                "expired": "Запрос входа устарел. Откройте сайт и нажмите «Войти через Telegram» ещё раз."}[result]
        self._edit_or_send(u, {"message_id": mid} if mid else None, text)

    def _cb_menu(self, u: dict, arg: str, mid) -> None:
        {"check": self._cmd_check, "ask": self._cmd_ask, "form": self._cmd_form, "tickets": self._cmd_tickets, "settings": self._cmd_settings, "app": self._cmd_app}.get(arg, self._hello)(u)

    def _cb_option(self, u: dict, arg: str, mid) -> None:
        state = self.storage.users.bot_state(u["id"])
        opts = state.get("opts") or []
        if arg in ("back", "cancel"):
            text = {"back": "назад", "cancel": "отмена"}[arg]
        else:
            try:
                text = str(opts[int(arg)])
            except (ValueError, IndexError):
                self._send(u, "Эти кнопки устарели — напишите ответ сообщением.")
                return
        self._clear_buttons(u, mid)
        low = text.lower()
        if "сформировать" in low:
            return self._generate(u, None)
        sid = self.accounts.ensure_data_session(u)
        try:
            res = self.core.message(sid, text, defer=True)
        except ServiceError as exc:
            self._send(u, esc(exc.detail))
            return
        self._dialog(u, sid, res)

    def _cb_form(self, u: dict, arg: str, mid) -> None:
        self._clear_buttons(u, mid)
        if arg in ("transfer", "enrollment"):
            self._start_form(u, arg)
        else:  # «Составить заявление» / «Новая анкета»: сначала выбрать, что оформляем
            self._cmd_form(u)

    def _cb_template(self, u: dict, arg: str, mid) -> None:
        self._clear_buttons(u, mid)
        self._generate(u, arg)

    def _cb_submit(self, u: dict, arg: str, mid) -> None:
        if arg == "prev":
            return self._submission_preview(u)
        if arg == "contact":
            state = self.storage.users.bot_state(u["id"])
            state["contact"] = not state.get("contact")
            self.storage.users.save_bot_state(u["id"], state)
            self._clear_buttons(u, mid)
            return self._submission_preview(u)
        self._clear_buttons(u, mid)
        self._submit(u, arg == "ok")

    def _cb_next(self, u: dict, arg: str, mid) -> None:
        sid = self.accounts.ensure_data_session(u)
        text, remind = render_next_steps(self.core.next_steps(sid))
        state = self.storage.users.bot_state(u["id"])
        state["reminder"] = {"text": remind} if remind else {}
        self.storage.users.save_bot_state(u["id"], state)
        self._send(u, text, kb([btn("⏰ Напомнить через 3 дня", "rem:3")]) if remind else None)

    def _cb_remind(self, u: dict, arg: str, mid) -> None:
        state = self.storage.users.bot_state(u["id"])
        text = (state.get("reminder") or {}).get("text")
        if not text:
            self._send(u, "Напоминать пока не о чем: сначала проверьте документ или пройдите анкету.")
            return
        days = max(1, min(14, int(arg) if arg.isdigit() else 3))
        if not u["notify"]:
            self._send(u, "Уведомления выключены, поэтому напоминание не дойдёт. Включите их в /settings.")
            return
        when = time.time() + days * 86400
        if self.notifier.reminder(u, text, when) is None:
            self._send(u, "Напоминаний уже достаточно (не больше трёх ожидающих) или уведомления отключены.")
            return
        self._clear_buttons(u, mid)
        self._send(u, f"Хорошо, напомню через {days} дн.: {esc(short(text, 160))}")

    def _cb_quick(self, u: dict, arg: str, mid) -> None:
        """Быстрый вопрос кнопкой — то же, что набрать его текстом."""
        if arg.isdigit() and int(arg) < len(QUICK_QUESTIONS):
            self._text(u, QUICK_QUESTIONS[int(arg)][1])

    def _cb_ticket(self, u: dict, arg: str, mid) -> None:
        row = self.storage.tickets.get_for_user(int(arg), u["id"]) if arg.isdigit() else None
        self._send(u, render_ticket(row) if row else "Обращение не найдено.")

    def _cb_link(self, u: dict, arg: str, mid) -> None:
        state = self.storage.users.bot_state(u["id"])
        pending = state.get("pending_link") or {}
        if not arg.isdigit() or pending.get("id") != int(arg) or time.time() - float(pending.get("at", 0)) > 600:
            self._send(u, "Время привязки вышло. Повторите: /status номер код.")
            return
        if self.storage.tickets.attach_user(int(arg), u["id"]):
            state.pop("pending_link", None)
            self.storage.users.save_bot_state(u["id"], state)
            self._clear_buttons(u, mid)
            self._send(u, f"✅ Обращение № {arg} привязано. Ответы деканата будут приходить сюда; все обращения — /tickets.")
        else:
            self._send(u, "Это обращение уже привязано к другому аккаунту.")

    def _cb_settings(self, u: dict, arg: str, mid) -> None:
        if arg == "notify":
            self.storage.users.set_notify(u["id"], not u["notify"])
            self._clear_buttons(u, mid)
            return self._cmd_settings({**u, "notify": not u["notify"]})
        if arg == "data":
            return self._cmd_forget(u)
        if arg == "data!":
            self.storage.delete_user_data(self.accounts.ensure_data_session(u))
            self._drop_file(u)
            state = self.storage.users.bot_state(u["id"])
            for k in ("last_file", "last_question", "reminder", "opts", "pending_link", "contact"):
                state.pop(k, None)
            self.storage.users.save_bot_state(u["id"], state)
            self._clear_buttons(u, mid)
            self._send(u, "🧹 Готово: анкета, документ и готовые файлы удалены — и в боте, и на сайте.")
        elif arg == "del":
            self._send(u, "Удалить аккаунт? Входы на сайте закроются, данные и уведомления будут удалены. Отправленные обращения останутся у деканата без привязки к вам.",
                       kb([btn("Да, удалить аккаунт", "set:del!"), btn("Отмена", "set:cancel")]))
        elif arg == "del!":
            self._drop_file(u)
            self.accounts.delete_account(u["id"])
            self._clear_buttons(u, mid)
            self._send(u, "Аккаунт удалён. Если захотите вернуться — просто напишите мне снова.")
        else:
            self._clear_buttons(u, mid)

    def _cb_doc(self, u: dict, arg: str, mid) -> None:
        if arg == "img":
            return self._show_on_photo(u)
        if arg == "forget":
            self.core.delete_document(self.accounts.ensure_data_session(u))
            self._drop_file(u)
            state = self.storage.users.bot_state(u["id"])
            for k in ("last_file", "last_question", "reminder"):
                state.pop(k, None)
            self.storage.users.save_bot_state(u["id"], state)
            self._clear_buttons(u, mid)
            self._send(u, "Документ забыт. Можно прислать другой.")
