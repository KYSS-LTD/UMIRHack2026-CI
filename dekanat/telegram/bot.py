"""Telegram-бот «Онлайн-деканата»: то же, что сайт, в переписке. Текстовое управление (команды, обычные сообщения, кнопки) и кнопка Mini App.

Бот работает поверх того же ядра, что и сайт (`AssistantCore`): анкета, карточка документа и готовые файлы лежат в «сессии данных»
аккаунта, поэтому начатое в боте продолжается на сайте и наоборот. Документы распознаются локально (Tesseract), ответы — по правилам и Положению;
в Telegram уходят только сообщения этой переписки. Снимки не сохраняются: запоминается только идентификатор файла Telegram, чтобы
повторить вопрос по тому же документу.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

import cv2
from fastapi import HTTPException

from ..accounts import Accounts, display_name
from ..dialog.docstate import public_fields
from ..highlight import draw_overlay, overlay_items
from ..ocr.engine import OcrUnavailable
from ..ocr.preprocess import UnsupportedFile
from ..storage.topics import classify_topic
from ..web.assistant import AssistantCore, remember_document
from .api import TelegramAPI, TelegramError
from .notify import Notifier
from .views import (COMMANDS, MENU, STATUS_TITLES, btn, esc, kb, menu_keyboard, options_keyboard, render_answer, render_document, render_next_steps, render_submission,
                    render_ticket, render_tickets, short, split_message)

log = logging.getLogger("dekanat.telegram")

OCR_DOWN_TEXT = "Распознавание текста сейчас недоступно. Попробуйте позже; вопросы по Положению работают и без документа."
GENERIC_ERROR = "Не получилось выполнить запрос. Попробуйте ещё раз чуть позже."
MAX_TG_FILE = 20 * 1024 * 1024
FILE_EXT = (".jpg", ".jpeg", ".png", ".pdf")
_STATUS_ARGS = re.compile(r"^\s*(\d{1,9})\s+([A-Za-z0-9\-]{4,16})\s*$")

HELLO = ("Здравствуйте! Я цифровой секретарь деканата.\n\n"
         "<b>Что я умею:</b>\n"
         "• проверить справку или заявление — пришлите <b>фото или PDF</b>;\n"
         "• ответить на вопрос по Положению — просто напишите вопрос;\n"
         "• провести анкету и подготовить заявление о переводе или зачислении (DOCX);\n"
         "• сообщить, когда деканат ответит на ваше обращение.\n\n"
         "Я даю предварительную оценку — окончательное решение принимает сотрудник деканата.")


class RateLimiter:
    """Не больше `limit` событий за `window` секунд от одного ключа: бот не должен становиться способом перегрузить распознавание."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[Any, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: Any) -> bool:
        now = time.time()
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(recent) >= self.limit:
                self._hits[key] = recent
                return False
            self._hits[key] = recent + [now]
            if len(self._hits) > 5000:
                self._hits.clear()
            return True


def profile_of(frm: dict) -> dict:
    return {"id": int(frm["id"]), "username": frm.get("username"), "first_name": frm.get("first_name"), "last_name": frm.get("last_name"), "language": frm.get("language_code")}


class TelegramBot:
    def __init__(self, api: TelegramAPI, svc, storage, core: AssistantCore, accounts: Accounts, notifier: Notifier, settings, ocr_slots, locks):
        self.api, self.svc, self.storage, self.core, self.accounts, self.notifier = api, svc, storage, core, accounts, notifier
        self.settings, self.ocr_slots, self.locks = settings, ocr_slots, locks
        self.username = str(settings.get("telegram.bot_username") or "")
        self.public_url = str(settings.get("telegram.public_url") or "").rstrip("/")
        self.max_bytes = min(MAX_TG_FILE, int(settings.get("server.max_upload_mb", 25)) * 1024 * 1024)
        self.limiter = RateLimiter(int(settings.get("telegram.rate_per_minute", 20)), 60)
        self.file_limiter = RateLimiter(int(settings.get("telegram.files_per_hour", 12)), 3600)
        self._user_locks: dict[int, threading.Lock] = {}
        self._guard = threading.Lock()
        self._files: "OrderedDict[str, bytes]" = OrderedDict()  # последние присланные файлы (в памяти, не на диске): повторный вопрос не качает файл заново

    # ------------------------------------------------------------------ настройка
    @property
    def webapp_url(self) -> Optional[str]:
        """Адрес Mini App. Telegram принимает только https: без публичного адреса кнопки приложения нет."""
        return f"{self.public_url}/?tg=1" if self.public_url.startswith("https://") else None

    def setup(self) -> None:
        """Команды, описание и кнопка меню бота. Ошибки не критичны: бот работает и без этого."""
        steps = [
            lambda: self.api.set_my_commands([{"command": c, "description": d} for c, d in COMMANDS]),
            lambda: self.api.set_my_description("Цифровой секретарь деканата: проверит справку по фото, ответит по Положению со ссылкой на пункт, поможет составить заявление.",
                                                "Секретарь деканата: справки, Положение, заявления"),
            lambda: self.api.set_chat_menu_button(self.webapp_url),
        ]
        for step in steps:
            try:
                step()
            except TelegramError as exc:
                log.warning("Настройка бота: %s", exc.description)

    # ------------------------------------------------------------------ вход обновлений
    def handle_update(self, update: dict) -> None:
        try:
            if isinstance(update.get("message"), dict):
                self._message(update["message"])
            elif isinstance(update.get("callback_query"), dict):
                self._callback(update["callback_query"])
        except TelegramError as exc:
            log.warning("Telegram: %s", exc)
        except Exception:
            log.exception("Ошибка при обработке обновления Telegram")
            frm = (update.get("message") or (update.get("callback_query") or {})).get("from") or {}
            if frm.get("id"):
                try:
                    self.api.send_message(frm["id"], GENERIC_ERROR)
                except TelegramError:
                    pass

    def _lock(self, tg_id: int) -> threading.Lock:
        with self._guard:
            if len(self._user_locks) > 5000:
                self._user_locks.clear()
            return self._user_locks.setdefault(tg_id, threading.Lock())

    # ------------------------------------------------------------------ сообщения
    def _message(self, msg: dict) -> None:
        chat, frm = msg.get("chat") or {}, msg.get("from") or {}
        if chat.get("type") != "private" or frm.get("is_bot") or not frm.get("id"):
            return  # бот работает только в личной переписке
        with self._lock(int(frm["id"])):
            if not self.limiter.hit(frm["id"]):
                self.api.send_message(frm["id"], "Слишком много сообщений подряд. Подождите минуту и продолжим.")
                return
            user, created = self.accounts.upsert_tg(profile_of(frm))
            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                cmd, _, arg = text.partition(" ")
                return self._command(user, cmd.split("@")[0].lower(), arg.strip(), created)
            photo = msg.get("photo")
            doc = msg.get("document")
            if photo:
                best = max(photo, key=lambda p: (p.get("file_size") or 0, p.get("width", 0) * p.get("height", 0)))
                return self._file(user, best["file_id"], "photo.jpg", best.get("file_size") or 0, msg.get("caption") or "")
            if doc:
                name = doc.get("file_name") or "document"
                mime = (doc.get("mime_type") or "").lower()
                if not (name.lower().endswith(FILE_EXT) or mime in ("image/jpeg", "image/png", "application/pdf")):
                    self._send(user, "Такой файл я не читаю. Пришлите фото (JPG, PNG) или PDF с документом.")
                    return
                return self._file(user, doc["file_id"], name, doc.get("file_size") or 0, msg.get("caption") or "")
            if text:
                return self._text(user, text)
            self._send(user, "Пока я понимаю текст, фото и PDF. Пришлите документ или напишите вопрос.")

    def _command(self, u: dict, cmd: str, arg: str, created: bool = False) -> None:
        if cmd == "/start":
            if arg.startswith("login_"):
                return self._login_request(u, arg[len("login_"):])
            return self._hello(u)
        handlers = {"/help": self._hello, "/menu": self._hello, "/check": self._cmd_check, "/ask": self._cmd_ask, "/form": self._cmd_form, "/anketa": self._cmd_form,
                    "/tickets": self._cmd_tickets, "/settings": self._cmd_settings, "/app": self._cmd_app, "/forget": self._cmd_forget, "/privacy": self._cmd_privacy,
                    "/cancel": self._cmd_cancel}
        if cmd == "/status":
            return self._cmd_status(u, arg)
        fn = handlers.get(cmd)
        if fn is None:
            self._send(u, "Такой команды я не знаю. Список — /help или меню под полем ввода.")
            return
        fn(u)

    def _text(self, u: dict, text: str) -> None:
        action = MENU.get(text)
        if action:
            return {"check": self._cmd_check, "ask": self._cmd_ask, "form": self._cmd_form, "tickets": self._cmd_tickets, "settings": self._cmd_settings}[action](u)
        state = self.storage.users.bot_state(u["id"])
        if state.get("await") == "status":
            state.pop("await", None)
            self.storage.users.save_bot_state(u["id"], state)
            return self._cmd_status(u, text)
        self._typing(u)
        sid = self.accounts.ensure_data_session(u)
        try:
            res = self.core.message(sid, text, defer=True)
        except HTTPException as exc:
            self._send(u, esc(exc.detail))
            return
        if res["reply"]["kind"] != "passthrough":
            return self._dialog(u, sid, res)
        self._question(u, sid, text)

    # ------------------------------------------------------------------ команды
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
        except HTTPException as exc:
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

    # ------------------------------------------------------------------ документ
    def _file(self, u: dict, file_id: str, name: str, size: int, caption: str) -> None:
        if size and size > self.max_bytes:
            self._send(u, f"Файл больше {self.max_bytes // (1024 * 1024)} МБ — Telegram не отдаёт такие файлы боту. Сожмите фото или пришлите первую страницу.")
            return
        if not self.file_limiter.hit(u["tg_id"]):
            self._send(u, "Вы прислали много документов за час. Подождите немного или воспользуйтесь сайтом.")
            return
        wait = self._send(u, "⏳ Читаю документ… Обычно это занимает до полуминуты.")
        self._typing(u, "upload_photo")
        sid = self.accounts.ensure_data_session(u)
        try:
            data = self._file_bytes(file_id)
            with self.ocr_slots:
                res = self.svc.analyze(data, name, caption.strip(), "auto", document_first=True)
        except UnsupportedFile as exc:
            return self._edit_or_send(u, wait, esc(str(exc)))
        except OcrUnavailable as exc:
            log.error("Распознавание текста недоступно: %s", exc)
            return self._edit_or_send(u, wait, OCR_DOWN_TEXT)
        except TelegramError as exc:
            log.warning("Файл не получен: %s", exc)
            return self._edit_or_send(u, wait, "Не удалось получить файл от Telegram. Пришлите его ещё раз.")
        except Exception:
            log.exception("Ошибка при проверке документа из Telegram")
            return self._edit_or_send(u, wait, "Не удалось проверить документ. Попробуйте другой снимок — ровнее и при хорошем свете.")
        remember_document(self.storage, self.locks, sid, res, name, hashlib.sha256(data).hexdigest())
        self.storage.events.log("document_check", classify_topic(caption), res.answer.verdict, sid)
        state = self.storage.users.bot_state(u["id"])
        state["last_file"] = {"id": file_id, "name": name}
        state["last_question"] = caption.strip()
        remind = ""
        if res.answer.verdict == "missing":
            report = res.answer.report()
            remind = "Документ не подходит: не хватает — " + ", ".join(report.get("missing") or []) if report.get("missing") else "Проверьте документ: в нём не хватает данных."
        state["reminder"] = {"text": remind} if remind else {}
        self.storage.users.save_bot_state(u["id"], state)
        record = self.core.doc_record(sid)
        text = render_document(record, public_fields(record), res.warnings) + "\n\n" + render_answer(res.answer.to_dict())
        rows = [[btn("🖼 Показать на снимке", "doc:img"), btn("📝 Составить заявление", "form:any")]]
        second = [btn("🗑 Забыть документ", "doc:forget")]
        if remind:
            second.insert(0, btn("⏰ Напомнить через 3 дня", "rem:3"))
        rows.append(second)
        self._edit_or_send(u, wait, text, kb(*rows))

    def _file_bytes(self, file_id: str) -> bytes:
        if file_id in self._files:
            self._files.move_to_end(file_id)
            return self._files[file_id]
        data = self.api.download_file(file_id, self.max_bytes)
        self._files[file_id] = data
        while len(self._files) > 3 or sum(len(v) for v in self._files.values()) > 40 * 1024 * 1024:
            self._files.popitem(last=False)
        return data

    def _drop_file(self, u: dict) -> None:
        """Забытый документ не должен оставаться и в памяти процесса."""
        last = self.storage.users.bot_state(u["id"]).get("last_file")
        if last:
            self._files.pop(last.get("id"), None)

    def _show_on_photo(self, u: dict) -> None:
        state = self.storage.users.bot_state(u["id"])
        last = state.get("last_file")
        if not last:
            self._send(u, "Сначала пришлите документ — тогда я смогу показать найденное на снимке.")
            return
        sid = self.accounts.ensure_data_session(u)
        record = self.core.doc_record(sid)
        self._typing(u, "upload_photo")
        try:
            data = self._file_bytes(last["id"])
            with self.ocr_slots:
                res = self.svc.analyze(data, last["name"], state.get("last_question", ""), (record or {}).get("doc_type", {}).get("id") or "auto", document_first=True)
        except (UnsupportedFile, OcrUnavailable, TelegramError):
            self._send(u, "Не удалось показать подсветку: файл больше недоступен. Пришлите документ ещё раз.")
            return
        items = overlay_items(res.fields, res.answer)
        pages = sorted({box.page for box, _, _ in items if 0 <= box.page < len(res.original_pages)})[:3]
        if not pages:
            self._send(u, "На снимке нечего подсвечивать: найденных полей нет. Попробуйте задать вопрос, например «Покажи ФИО».")
            return
        for p in pages:
            ok, buf = cv2.imencode(".jpg", draw_overlay(res.original_pages[p], [it for it in items if it[0].page == p], p), [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                self.api.send_photo(u["tg_id"], buf.tobytes(), "Найденное подсвечено: зелёным — поля карточки, оранжевым — источник ответа.", "highlight.jpg")

    # ------------------------------------------------------------------ вопросы вне анкеты
    def _question(self, u: dict, sid: str, text: str) -> None:
        record = self.core.doc_record(sid)
        state = self.storage.users.bot_state(u["id"])
        last = state.get("last_file")
        try:
            if record and record["doc_type"]["id"]:
                ans = self.core.answer_question(sid, text, record)
            elif record and last:  # тип документа не определён: ответ по тексту документа — читаем файл заново (распознавание кешируется)
                data = self._file_bytes(last["id"])
                with self.ocr_slots:
                    ans = self.svc.analyze(data, last["name"], text, "auto", document_first=True).answer.to_dict()
                self.storage.events.log("question", classify_topic(text), ans["verdict"], sid)
            else:
                ans = self.core.answer_question(sid, text, None)
        except (UnsupportedFile, OcrUnavailable, TelegramError):
            ans = self.core.answer_question(sid, text, None)
        state["last_question"] = text
        self.storage.users.save_bot_state(u["id"], state)
        markup = kb([btn("🖼 Показать на снимке", "doc:img")]) if (record and last) else None
        self._send(u, render_answer(ans), markup)

    # ------------------------------------------------------------------ анкета
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
        except (HTTPException, OSError):
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
        except HTTPException as exc:
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
        except HTTPException as exc:
            if exc.status_code == 400 and "шаблон" in str(exc.detail).lower():
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
        except HTTPException as exc:
            self._send(u, esc(exc.detail))
            return
        if not res.get("sent"):
            self._send(u, esc(res["message"]))
            return
        note = "Ответ деканата я пришлю сюда." if u["notify"] else "Уведомления выключены — статус смотрите в /tickets."
        self._send(u, f"✅ <b>Обращение № {res['ticket_id']} передано в деканат.</b>\nКод для проверки статуса: <code>{esc(res['code'])}</code> (сохраните его).\n{note}\n\n"
                      f"<i>{esc(res['message'])}</i>")

    # ------------------------------------------------------------------ вход на сайте через бота
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

    # ------------------------------------------------------------------ кнопки
    def _callback(self, cq: dict) -> None:
        frm, msg = cq.get("from") or {}, cq.get("message") or {}
        data = str(cq.get("data") or "")
        if frm.get("is_bot") or not frm.get("id") or (msg.get("chat") or {}).get("type") != "private":
            return
        try:
            self.api.answer_callback(cq["id"])
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
                       "rem": self._cb_remind, "t": self._cb_ticket, "link": self._cb_link, "set": self._cb_settings, "doc": self._cb_doc}.get(act)
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
        except HTTPException as exc:
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

    # ------------------------------------------------------------------ отправка сообщений
    def _typing(self, u: dict, action: str = "typing") -> None:
        try:
            self.api.send_chat_action(u["tg_id"], action)
        except TelegramError:
            pass

    def _send(self, u: dict, text: str, markup: Optional[dict] = None) -> Optional[dict]:
        """Сообщение (длинное — несколькими); кнопки — под последним."""
        parts = split_message(text) or ["…"]
        last = None
        for i, part in enumerate(parts):
            last = self.api.send_message(u["tg_id"], part, markup if i == len(parts) - 1 else None)
        return last

    def _edit_or_send(self, u: dict, sent: Optional[dict], text: str, markup: Optional[dict] = None) -> None:
        """Заменяет сообщение «Читаю документ…» результатом; не получилось (или результат длинный) — отправляет новыми сообщениями."""
        parts = split_message(text) or ["…"]
        if sent and len(parts) == 1:
            try:
                self.api.edit_message_text(u["tg_id"], sent["message_id"], parts[0], markup)
                return
            except TelegramError:
                pass
        if sent:
            try:
                self.api.delete_message(u["tg_id"], sent["message_id"])
            except TelegramError:
                pass
        self._send(u, text, markup)
