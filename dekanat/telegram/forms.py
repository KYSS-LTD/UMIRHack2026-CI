"""Анкета и обращение: ведение диалога, DOCX, предпросмотр и отправка обращения в деканат.

Работает как часть `TelegramBot`: поля (`api`, `storage`, `core`, `accounts` …) и общие методы (`_send`, `_edit_or_send`, `_typing`, `_text`) приходят из него.
"""

from __future__ import annotations

from typing import Optional

from ..assistant import ServiceError
from .views import btn, esc, kb, options_keyboard, render_submission, short


class FormsMixin:
    def _dialog(self, u: dict, sid: str, res: dict) -> None:
        reply, dialog = res["reply"], res["dialog"]
        options = list(reply.get("options") or [])
        state = self.storage.users.bot_state(u["id"])
        state["opts"] = options
        self.storage.users.save_bot_state(u["id"], state)
        text = esc(reply["text"])  # номер вопроса («Вопрос 2 из 8.») уже в тексте реплики
        if res.get("download"):
            self._send_docx(u, sid, res["download"])
        active = dialog["state"] in ("screening", "review", "document_generation")
        markup = options_keyboard(options, active)
        if dialog["state"] == "completed" and not options:
            markup = kb([btn("📤 Отправить в деканат", "sub:prev"), btn("📋 Что дальше", "ns:show")], [btn("🔁 Новая анкета", "form:any")])
        self._send(u, text, markup)

    def _send_docx(self, u: dict, sid: str, download: dict) -> None:
        try:
            row = self.core.download_row(sid, download["id"])
            with open(row["path"], "rb") as f:
                data = f.read()
        except (ServiceError, OSError):
            self._send(u, "Документ готов, но его не удалось отправить в чат: скачайте его на сайте.")
            return
        self.api.send_document(u["tg_id"], data, row["filename"], "📄 Заявление готово. Проверьте данные перед использованием.")

    def _start_form(self, u: dict, kind: str) -> None:
        sid = self.accounts.ensure_data_session(u)
        phrase = {"transfer": "Я хочу перевестись", "enrollment": "Хочу поступить"}[kind]
        try:
            res = self.core.message(sid, phrase, defer=True)
            if res["reply"]["kind"] == "offer":  # анкета предлагается по смыслу фразы — человек уже нажал кнопку, подтверждаем за него
                res = self.core.message(sid, "да", defer=True)
        except ServiceError as exc:
            self._send(u, esc(exc.detail))
            return
        self._dialog(u, sid, res)

    def _generate(self, u: dict, template_id: Optional[str]) -> None:
        sid = self.accounts.ensure_data_session(u)
        try:
            if template_id:
                result = self.core.generate(sid, template_id)
                res = {"reply": result["reply"] or {"text": "Этот документ уже сформирован.", "state": result["dialog"]["state"], "kind": "done", "options": [], "progress": None},
                       "dialog": result["dialog"], "download": result["download"]}
            else:
                res = self.core.message(sid, "сформировать", defer=True)
        except ServiceError as exc:
            if exc.status == 400 and "шаблон" in str(exc.detail).lower():
                rows = [[btn(short(t["name"], 40), f"tpl:{t['id']}")] for t in self.core.available_templates()[:8]]
                self._send(u, "Выберите шаблон заявления:", kb(*rows))
            else:
                self._send(u, esc(exc.detail))
            return
        self._dialog(u, sid, res)

    # ------------------------------------------------------------------ отправка обращения
    def _submission_preview(self, u: dict) -> None:
        sid = self.accounts.ensure_data_session(u)
        prev = self.core.submission_preview(sid, False)
        if prev.get("already_sent"):
            self._send(u, f"Обращение уже отправлено: № {prev['already_sent']}. Статус — /tickets.")
            return
        if not prev["delivery"]["available"]:
            self._send(u, "Автоматическая отправка пока недоступна: " + esc(prev["delivery"]["reason"]) + ". Заявление можно отнести в деканат самостоятельно.")
            return
        if prev["state"] != "completed":
            self._send(u, "Сначала пройдите анкету и подготовьте заявление: /form.")
            return
        if prev.get("blocked"):
            self._send(u, "Обращение не отправляется: " + esc(" ".join(prev["blocked"])))
            return
        state = self.storage.users.bot_state(u["id"])
        contact = f"@{u['username']}" if state.get("contact") and u.get("username") else ""
        rows = [[btn("✅ Отправить", "sub:ok"), btn("✖ Не отправлять", "sub:no")]]
        if u.get("username"):
            rows.insert(0, [btn("👤 Убрать контакт" if contact else f"👤 Добавить мой @{u['username']} для связи", "sub:contact")])
        self._send(u, render_submission(prev, contact), kb(*rows))

    def _submit(self, u: dict, confirm: bool) -> None:
        sid = self.accounts.ensure_data_session(u)
        state = self.storage.users.bot_state(u["id"])
        contact = f"@{u['username']}" if state.get("contact") and u.get("username") else ""
        try:
            res = self.core.submit(sid, confirm, False, contact, source="bot")
        except ServiceError as exc:
            self._send(u, esc(exc.detail))
            return
        if not res.get("sent"):
            self._send(u, esc(res["message"]))
            return
        note = "Ответ деканата я пришлю сюда." if u["notify"] else "Уведомления выключены — статус смотрите в /tickets."
        self._send(u, f"✅ <b>Обращение № {res['ticket_id']} передано в деканат.</b>\nКод для проверки статуса: <code>{esc(res['code'])}</code> (сохраните его).\n{note}\n\n"
                      f"<i>{esc(res['message'])}</i>")
