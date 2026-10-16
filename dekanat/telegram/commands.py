"""Команды бота: приветствие, проверка, вопрос, анкета, обращения, настройки, приватность; вход на сайте через бота.

Работает как часть `TelegramBot`: поля (`api`, `storage`, `core`, `accounts` …) и общие методы (`_send`, `_edit_or_send`, `_typing`, `_text`) приходят из него.
"""

from __future__ import annotations

import hashlib
import re
import time

from ..accounts import display_name
from ..assistant import ServiceError
from .views import STATUS_TITLES, btn, esc, kb, menu_keyboard, render_ticket, render_tickets

HELLO = ("Здравствуйте! Я цифровой секретарь деканата.\n\n"
         "<b>Что я умею:</b>\n"
         "• проверить справку или заявление — пришлите <b>фото или PDF</b>;\n"
         "• ответить на вопрос по Положению — просто напишите вопрос;\n"
         "• провести анкету и подготовить заявление о переводе или зачислении (DOCX);\n"
         "• сообщить, когда деканат ответит на ваше обращение.\n\n"
         "Я даю предварительную оценку — окончательное решение принимает сотрудник деканата.")
_STATUS_ARGS = re.compile(r"^\s*(\d{1,9})\s+([A-Za-z0-9\-]{4,16})\s*$")


class CommandsMixin:
    def _hello(self, u: dict) -> None:
        extra = "\n\nВы вошли и на сайте: анкета и документы общие." if u.get("web_linked_at") else (
            "\n\n💡 Если зайти на сайт и нажать «Войти через Telegram», анкета и документы станут общими для сайта и бота.")
        self._send(u, HELLO + extra, menu_keyboard(self.webapp_url))

    def _cmd_check(self, u: dict) -> None:
        self._send(u, "Пришлите фото или PDF документа — справки, заявления, справки о практике. Лучше ровно, при хорошем свете; подпись можно добавить вопросом: "
                      "например, «Хватит ли этой справки для перевода?».")

    def _cmd_ask(self, u: dict) -> None:
        self._send(u, "Напишите вопрос — отвечу по Положению со ссылкой на пункт, а если вы прислали документ, то по нему. Например: «Какие документы нужны для перевода?». "
                      "Если ответа нет ни в документе, ни в Положении, так и скажу.")

    def _cmd_form(self, u: dict) -> None:
        self._send(u, "📝 <b>Анкета для заявления</b>\nЯ задам несколько коротких вопросов и подготовлю заявление (DOCX). Что оформляем?",
                   kb([btn("Перевод из другого вуза", "form:transfer")], [btn("Поступление и зачисление", "form:enrollment")]))

    def _cmd_cancel(self, u: dict) -> None:
        sid = self.accounts.ensure_data_session(u)
        self._dialog(u, sid, self.core.message(sid, "отмена", defer=True))

    def _cmd_app(self, u: dict) -> None:
        if self.webapp_url:
            self._send(u, "Откройте приложение: те же возможности с большим экраном — снимок с подсветкой, карточка полей и анкета.",
                       kb([btn("🌐 Открыть приложение", web_app=self.webapp_url)]))
        else:
            self._send(u, "Приложение пока не подключено: администратору нужно указать https-адрес сайта (telegram.public_url). Всё остальное работает здесь, в чате.")

    def _cmd_tickets(self, u: dict) -> None:
        items = self.storage.tickets.list_for_user(u["id"], 10)
        rows = [[btn(f"№ {t['id']} · {STATUS_TITLES.get(t['status'], t['status'])}", f"t:{t['id']}")] for t in items[:8]]
        self._send(u, render_tickets(items), kb(*rows))

    def _cmd_status(self, u: dict, arg: str) -> None:
        m = _STATUS_ARGS.match(arg or "")
        if not m:
            state = self.storage.users.bot_state(u["id"])
            state["await"] = "status"
            self.storage.users.save_bot_state(u["id"], state)
            self._send(u, "Напишите номер обращения и код через пробел, например: <code>12 ABCD-2345</code>. Если вы входили на сайте через Telegram, "
                          "свои обращения видно без кода — /tickets.")
            return
        try:
            row = self.core.ticket_status(int(m.group(1)), m.group(2), f"tg:{u['tg_id']}")
        except ServiceError as exc:
            self._send(u, esc(exc.detail))
            return
        full = self.storage.tickets.get(row["id"]) or {}
        owner = self.storage.tickets.owner_of(row["id"])
        markup = None
        if owner is None:  # обращение ещё ничьё: можно привязать, чтобы ответы приходили в Telegram
            state = self.storage.users.bot_state(u["id"])
            state["pending_link"] = {"id": row["id"], "at": time.time()}
            self.storage.users.save_bot_state(u["id"], state)
            markup = kb([btn("🔔 Присылать ответы сюда", f"link:{row['id']}")])
        self._send(u, render_ticket({**full, **row}), markup)

    def _cmd_settings(self, u: dict) -> None:
        sid = self.accounts.ensure_data_session(u)
        has_doc = bool(self.core.doc_record(sid))
        ctx = self.core.load_ctx(sid)
        site = "вход выполнен — данные общие с сайтом" if u.get("web_linked_at") else "вход не выполнен (на сайте — «Войти через Telegram»)"
        lines = ["⚙️ <b>Настройки</b>", f"Аккаунт: {esc(display_name(u))}" + (f" (@{esc(u['username'])})" if u.get("username") else ""), f"Сайт: {site}",
                 f"Уведомления об ответах: <b>{'включены' if u['notify'] else 'выключены'}</b>",
                 f"Сейчас сохранено: документ — {'да' if has_doc else 'нет'}, анкета — {'идёт' if ctx.state in ('screening', 'review', 'document_generation') else ('готова' if ctx.state == 'completed' else 'нет')}"]
        self._send(u, "\n".join(lines), kb([btn("🔕 Выключить уведомления" if u["notify"] else "🔔 Включить уведомления", "set:notify")],
                                          [btn("🧹 Удалить мои данные", "set:data"), btn("🗑 Удалить аккаунт", "set:del")]))

    def _cmd_forget(self, u: dict) -> None:
        self._send(u, "Удалить анкету, загруженный документ и готовые файлы? Они пропадут и в боте, и на сайте. Обращения, уже отправленные в деканат, останутся.",
                   kb([btn("Да, удалить", "set:data!"), btn("Отмена", "set:cancel")]))

    def _cmd_privacy(self, u: dict) -> None:
        days = int(self.settings.get("accounts.data_days", 90))
        self._send(u, "🔒 <b>Что я храню</b>\n"
                      "• Ваш Telegram-идентификатор, имя и @имя — чтобы бот и сайт узнавали вас. Телефон и почту не запрашиваю.\n"
                      f"• Анкету, карточку проверенного документа и готовые DOCX — до {days} дн. без активности; удалить сразу — /forget.\n"
                      "• Снимки и PDF не сохраняю: помню только идентификатор файла Telegram, чтобы повторить вопрос по тому же документу.\n"
                      "• Паспортные данные из анкеты стираются через час.\n"
                      "• Обращение, отправленное в деканат, хранит деканат (до 180 дней).\n\n"
                      "Документы читаются на сервере деканата, без внешних языковых моделей. Но всё, что вы пишете и присылаете боту, проходит через серверы Telegram — "
                      "как в любой переписке. Если это нежелательно, пользуйтесь сайтом.")

    def _login_request(self, u: dict, token: str) -> None:
        prefix = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
        req = self.accounts.describe_login(prefix)
        if not req:
            self._send(u, "Эта ссылка для входа устарела или уже использована. Откройте сайт и нажмите «Войти через Telegram» ещё раз.")
            return
        minutes = max(0, int((time.time() - req["created_at"]) // 60))
        self._send(u, "🔐 <b>Вход на сайт «Онлайн-деканат»</b>\n"
                      f"Запрос с устройства: {esc(req['device'] or 'неизвестно')}\n"
                      f"Отправлен {minutes} мин назад.\n\n"
                      "Если это вы — подтвердите: анкета и документы станут общими для сайта и бота. Если вы ничего не запрашивали, нажмите «Это не я».",
                   kb([btn("✅ Подтвердить вход", f"lg:y:{prefix}"), btn("✖ Это не я", f"lg:n:{prefix}")]))
