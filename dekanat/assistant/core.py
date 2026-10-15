"""Ядро ассистента: диалог с анкетой, документ сессии, DOCX, отправка обращения.

Не знает ни про HTTP, ни про Telegram: сайт (`web/assistant.py`) обращается к нему через cookie-сессию, бот (`telegram/`) — через аккаунт.
Идентификатор сессии — случайный токен в HttpOnly-cookie (или данные аккаунта у вошедшего). Все данные привязаны к сессии на сервере:
чужую анкету, документ или файл получить нельзя, даже зная идентификатор документа. Ошибки сценария — `ServiceError` (текст для человека
и код, который сайт отдаёт как HTTP-статус).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from ..dialog import COMPLETED, DOCUMENT_GENERATION, DialogContext, DialogMachine, Reply
from ..dialog.eligibility import check_submission
from ..dialog.nextsteps import build_next_steps
from ..mailer import Mailer, mask_email
from ..dialog.docstate import apply_correction, make_record, public_fields, restore_fields, suggestions_from_record
from ..dialog.questions import REQUEST_TYPES
from ..templates import TemplateError, TemplateNotFound, TemplateStore, fill_docx
from ..storage import session_ref
from ..storage.repos import new_public_code
from ..storage.topics import classify_topic
from ..ocr.engine import OcrUnavailable
from ..ocr.preprocess import UnsupportedFile
from .errors import ServiceError

log = logging.getLogger("dekanat.assistant")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PLACEHOLDER_TITLES = {
    "student_name": "ФИО", "passport_data": "паспортные данные", "current_university": "текущий вуз", "current_course": "текущий курс",
    "current_specialty": "направление (текущее)", "target_specialty": "целевое направление", "education_form": "форма обучения",
    "debts": "задолженности", "application_date": "дата заявления",
}


def placeholder_title(key: str) -> str:
    """Название метки шаблона для человека: известные — по-русски, свои — без подчёркиваний («номер_зачётки» -> «номер зачётки»)."""
    return PLACEHOLDER_TITLES.get(key) or key.replace("_", " ").strip()


SCENARIO_TOPICS = {"transfer": "Перевод из другого вуза", "enrollment": "Поступление и зачисление"}
WELCOME = ("Здравствуйте! Я цифровой секретарь деканата. Можно задать вопрос по Положению, загрузить справку или заявление для проверки "
           "либо пройти короткую анкету и получить готовое заявление (напишите, например: «Я хочу перевестись»). "
           "Я даю предварительную оценку — окончательное решение принимает сотрудник деканата.")


STATUS_TITLES = {"new": "Принято, ждёт рассмотрения", "in_progress": "Рассматривается", "done": "Рассмотрено", "rejected": "Отклонено"}
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
_TELEGRAM = re.compile(r"^@[A-Za-z0-9_]{4,32}$")


def clean_contact(raw: str) -> str:
    """Контакт по желанию: пусто — можно; иначе почта, телефон (10–15 цифр) или Telegram (@имя). Остальное не принимается."""
    text = (raw or "").strip()
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    phone = re.fullmatch(r"\+?[\d\s\-()]{10,24}", text) is not None and 10 <= len(digits) <= 15
    if _EMAIL.match(text) or _TELEGRAM.match(text) or phone:
        return text
    raise ServiceError(400, "Контакт: укажите почту, телефон или Telegram (@имя) — или оставьте поле пустым.")


class AttemptLimiter:
    """Не даёт перебирать коды статуса: не больше `limit` неудачных попыток с одного адреса за окно."""

    def __init__(self, limit: int = 8, window: float = 600.0):
        self.limit, self.window = limit, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        cutoff = time.time() - self.window
        keep = [t for t in self._fails.get(key, []) if t > cutoff]
        if keep:
            self._fails[key] = keep
        else:
            self._fails.pop(key, None)
        return keep

    def allowed(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) < self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            self._recent(key)
            self._fails.setdefault(key, []).append(time.time())


class SessionLocks:
    """Запросы одной сессии выполняются по очереди: двойной клик не портит состояние анкеты."""

    def __init__(self):
        self._g = threading.Lock()
        self._d: dict[str, threading.Lock] = {}

    def get(self, sid: str) -> threading.Lock:
        with self._g:
            if len(self._d) > 2000:
                self._d.clear()
            return self._d.setdefault(sid, threading.Lock())


def _overlay_corrections(fields: list, record: dict) -> list:
    """Значения, исправленные пользователем, поверх свежего распознавания того же файла.
    Изменённые поля копируются: результат распознавания кешируется и не должен меняться для других проверок."""
    corrected = set(record.get("corrected", []))
    saved = {f["id"]: f for f in record.get("fields", [])}
    out = []
    for f in fields:
        rec = saved.get(f.id)
        units = {u for u in corrected if u == f.id or u.startswith(f.id + ".")}
        if rec is None or not units:
            out.append(f)
            continue
        f = copy.deepcopy(f)
        for p in f.parts:
            sp = next((x for x in rec.get("parts", []) if x["id"] == p.id), None)
            if sp is not None and f"{f.id}.{p.id}" in units:
                p.found, p.value, p.normalized, p.confidence, p.doubt = bool(sp["found"]), sp.get("value") or "", sp.get("normalized"), float(sp.get("confidence") or 0.0), ""
        f.found, f.value, f.normalized, f.confidence = bool(rec["found"]), rec.get("value") or "", rec.get("normalized"), float(rec.get("confidence") or 0.0)
        f.doubt = "" if f.id in units else rec.get("doubt", "")
        if f.id in units:
            f.method = "user"
        out.append(f)
    return out


def remember_document(storage, locks: SessionLocks, sid: str, result, filename: str, digest: str = "") -> list[str]:
    """Проверенный документ становится документом сессии (только карточка полей): его поля подсказывают ответы анкеты и правятся пользователем.

    Тот же файл проверяют снова (уточняющий вопрос) — исправления пользователя не теряются: поля результата заменяются
    исправленными, карточка сессии остаётся прежней. Возвращает исправленные пользователем единицы (поле или «поле.часть»).
    """
    if not sid or not storage.sessions.touch(sid, storage.session_ttl):
        return []
    type_id = result.doc_type.id if result.doc_type else None
    with locks.get(sid):
        old = storage.sessions.load_document(sid)
        if digest and old and old.get("digest") == digest and old["doc_type"]["id"] == type_id and old.get("corrected"):
            result.fields = _overlay_corrections(result.fields, old)
            return list(old["corrected"])
        record = make_record(result, filename)
        record["digest"] = digest
        storage.sessions.save_document(sid, record)
    return []


class AssistantCore:
    """Логика ассистента для одной «сессии данных» (sid): анкета, документ, DOCX, обращение.

    Её используют и сайт (идентификатор берётся из cookie), и Telegram-бот (идентификатор аккаунта): поэтому анкета, начатая в боте,
    продолжается на сайте и наоборот. Ошибки поднимаются как ServiceError — сайт отдаёт их как есть, бот показывает текст человеку.
    """

    def __init__(self, svc, storage, templates: TemplateStore, ocr_slots, max_bytes: int, delivery_status, locks: Optional[SessionLocks] = None, notifier=None):
        self.svc, self.storage, self.templates = svc, storage, templates
        self.ocr_slots, self.max_bytes, self.delivery_status = ocr_slots, max_bytes, delivery_status
        self.machine = DialogMachine(svc.llm)
        self.locks = locks or SessionLocks()
        self.mailer = Mailer(svc.settings)
        self.status_limiter = AttemptLimiter()
        self.notifier = notifier  # сообщения в Telegram о новых обращениях (None — не нужны)

    # ------------------------------------------------------------------ состояние диалога
    def load_ctx(self, sid: str) -> DialogContext:
        ctx = DialogContext.from_dict(self.storage.sessions.load_state(sid))
        if DialogMachine.expire_sensitive(ctx, self.storage.passport_ttl):
            self.save_ctx(sid, ctx)  # срок хранения паспортных данных истёк — они удалены из анкеты
        return ctx

    def save_ctx(self, sid: str, ctx: DialogContext) -> None:
        self.storage.sessions.save_state(sid, ctx.to_dict())

    def log_events(self, sid: str, ctx: DialogContext, events: list) -> None:
        topic = SCENARIO_TOPICS.get(ctx.scenario, "Анкета")
        for name, value in events:
            if name == "screening_started":
                self.storage.events.log("screening_started", SCENARIO_TOPICS.get(value, "Анкета"), value or "unknown", sid)
            elif name == "document_generated":
                self.storage.events.log("document_generated", topic, "ok", sid)
            elif name in ("offer", "screening_completed", "screening_cancelled", "review_confirmed", "generation_failed"):
                self.storage.events.log(name, topic, value or "", sid)

    def doc_record(self, sid: str) -> Optional[dict]:
        return self.storage.sessions.load_document(sid)

    @staticmethod
    def public_doc(record: Optional[dict]) -> Optional[dict]:
        if not record:
            return None
        return {"filename": record["filename"], "doc_type": record["doc_type"], "fields": public_fields(record), "warnings": record.get("warnings", []),
                "corrected": record.get("corrected", [])}

    # ------------------------------------------------------------------ вопросы вне анкеты
    def answer_question(self, sid: str, text: str, record: Optional[dict]) -> dict:
        if record and record["doc_type"]["id"]:
            ans = self.svc.answer_for_fields(text, record["doc_type"]["id"], restore_fields(record), document_first=True)
        else:
            ans = self.svc.analyze(None, "", text).answer
        self.storage.events.log("question", classify_topic(text), ans.verdict, sid)
        return ans.to_dict()

    # ------------------------------------------------------------------ генерация документа
    def available_templates(self) -> list[dict]:
        return [{"id": t["id"], "name": t["name"], "placeholders": [p["key"] for p in t["placeholders"]]} for t in self.storage.templates.list()]

    def do_generate(self, sid: str, ctx: DialogContext, template_id: Optional[str]) -> dict:
        storage = self.storage
        if ctx.state not in (DOCUMENT_GENERATION, COMPLETED):
            raise ServiceError(409, "Сначала пройдите анкету и подтвердите собранные данные.")
        if not template_id:
            items = storage.templates.list()
            if not items:
                raise ServiceError(404, "Пока нет ни одного шаблона документа: администратор ещё не загрузил DOCX-шаблон. Ваши ответы сохранены на время сессии.")
            if len(items) > 1:
                raise ServiceError(400, "Выберите шаблон из списка.")
            template_id = items[0]["id"]
        row = storage.templates.get(template_id)
        if row is None:
            raise ServiceError(404, "Шаблон не найден (возможно, его только что удалили). Выберите другой шаблон.")
        try:
            path = self.templates.path_of(template_id)
        except TemplateNotFound as exc:
            raise ServiceError(404, str(exc)) from exc
        data = self.machine.template_data(ctx)
        fingerprint = hashlib.sha256(f"{template_id}:{row['version']}:{json.dumps(data, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()
        known = ctx.gen_cache.get(fingerprint)
        if known:
            existing = storage.generated.get(known, sid)
            if existing and Path(existing["path"]).exists():
                return {"doc_id": known, "duplicate": True, "unfilled": [], "reply": None}
        doc_id = uuid.uuid4().hex
        try:
            # данных нет — на месте метки строка «________» для заполнения от руки; список незаполненных полей сообщается пользователю
            result = fill_docx(path, data, storage.output_dir, blank_unfilled=True, output_name=f"{doc_id}.docx")
        except TemplateError as exc:
            reply = self.machine.generation_failed(ctx, f"Не удалось сформировать документ: {exc}")
            self.log_events(sid, ctx, reply.events)
            raise ServiceError(422, reply.text) from exc
        try:
            storage.generated.add(doc_id, sid, template_id, f"{row['name']}.docx", str(result.path), storage.generated_ttl, result.unfilled)
        except Exception:
            result.path.unlink(missing_ok=True)  # документ без записи о владельце никому не выдать и не удалить по сроку — убираем сразу
            raise
        ctx.gen_cache[fingerprint] = doc_id
        titles = [placeholder_title(k) for k in result.unfilled]
        if ctx.state == DOCUMENT_GENERATION:
            reply = self.machine.generation_done(ctx, doc_id, titles)
        else:  # повторная генерация из completed (например, по другому шаблону)
            ctx.generated.append(doc_id)
            reply = Reply("Ещё один документ готов." + (f" Не заполнены (данных нет): {', '.join(titles)}." if titles else ""), ctx.state, "done",
                          events=[("document_generated", ctx.scenario)])
        self.log_events(sid, ctx, reply.events)
        return {"doc_id": doc_id, "duplicate": False, "unfilled": titles, "reply": reply}

    def download_info(self, sid: str, doc_id: Optional[str]) -> Optional[dict]:
        if not doc_id:
            return None
        row = self.storage.generated.get(doc_id, sid)
        return {"id": doc_id, "url": f"/api/assistant/download/{doc_id}", "filename": row["filename"]} if row else None

    def download_row(self, sid: str, doc_id: str) -> dict:
        row = self.storage.generated.get(doc_id, sid)  # только владелец сессии и только до окончания срока хранения
        if row is None or not Path(row["path"]).exists():
            raise ServiceError(404, "Документ не найден или срок его хранения истёк. Сформируйте его заново.")
        return row

    # ------------------------------------------------------------------ действия
    def state(self, sid: str, status: str = "ok") -> dict:
        ctx = self.load_ctx(sid)
        return {"session": status, "welcome": WELCOME, "dialog": ctx.public(), "document": self.public_doc(self.doc_record(sid)),
                "templates": self.available_templates(), "delivery": self.delivery_status(), "llm": {"enabled": self.svc.llm.enabled},
                "downloads": [d for d in (self.download_info(sid, g) for g in ctx.generated) if d], "ticket_id": ctx.ticket_id}

    def message(self, sid: str, text: str, template_id: Optional[str] = None, defer: bool = False, status: str = "ok") -> dict:
        with self.locks.get(sid):
            ctx = self.load_ctx(sid)
            record = self.doc_record(sid)
            ctx.suggestions = suggestions_from_record(record)
            reply = self.machine.handle(ctx, text)
            if reply.kind == "review":  # о запрете отправки студент узнаёт на сверке, пока ответы ещё можно исправить
                blocked = check_submission(ctx, self.svc.settings.get)
                if blocked:
                    reply.text += "\n\nОбратите внимание на отправку в деканат. " + " ".join(blocked)
            answer, download = None, None
            if reply.kind == "passthrough":
                answer = self.answer_question(sid, text.strip(), record) if text.strip() and not defer else None
                if answer:
                    reply.text = answer["text"]
            elif reply.kind == "generate":
                result = self.do_generate(sid, ctx, template_id)
                if result["reply"] is not None:
                    reply = result["reply"]
                else:
                    reply = Reply("Этот документ уже сформирован — его можно скачать.", ctx.state, "done")
                download = self.download_info(sid, result["doc_id"])
            self.log_events(sid, ctx, reply.events)
            self.save_ctx(sid, ctx)
        return {"session": status, "reply": reply.to_dict(), "dialog": ctx.public(), "answer": answer, "download": download}

    def generate(self, sid: str, template_id: Optional[str]) -> dict:
        with self.locks.get(sid):
            ctx = self.load_ctx(sid)
            result = self.do_generate(sid, ctx, template_id)
            self.save_ctx(sid, ctx)
        reply = result["reply"]
        return {"download": self.download_info(sid, result["doc_id"]), "duplicate": result["duplicate"], "unfilled": result["unfilled"],
                "reply": reply.to_dict() if reply else None, "dialog": ctx.public()}

    # ---- документ сессии
    def upload_document(self, sid: str, data: bytes, name: str, doc_type: str = "auto", status: str = "ok") -> dict:
        if len(data) > self.max_bytes:
            raise ServiceError(413, f"Файл больше {self.max_bytes // (1024 * 1024)} МБ")
        if not data:
            raise ServiceError(400, "Файл пустой")
        name = Path(name or "документ").name
        try:
            with self.ocr_slots:
                result = self.svc.analyze(data, name, "", doc_type)
        except (UnsupportedFile, OcrUnavailable) as exc:
            raise ServiceError(400 if isinstance(exc, UnsupportedFile) else 503, str(exc)) from exc
        record = make_record(result, name)
        with self.locks.get(sid):
            self.storage.sessions.save_document(sid, record)
        self.storage.events.log("document_check", classify_topic(""), result.answer.verdict, sid)
        return {"session": status, "document": self.public_doc(record), "answer": result.answer.to_dict()}

    def delete_document(self, sid: str) -> None:
        self.storage.sessions.delete_document(sid)

    def patch_field(self, sid: str, unit: str, value: str, question: str) -> dict:
        with self.locks.get(sid):
            record = self.doc_record(sid)
            if not record:
                raise ServiceError(404, "Документ не загружен")
            spec = self.svc.doc_types.get(record["doc_type"]["id"] or "")
            ok, error = apply_correction(record, spec, unit, value)
            if not ok:
                raise ServiceError(400, error)
            self.storage.sessions.save_document(sid, record)
        fields = restore_fields(record)
        ans = self.svc.answer_for_fields(question.strip(), record["doc_type"]["id"], fields, document_first=True) if record["doc_type"]["id"] else None
        # fields — карточка в том же виде, что в ответе проверки (без рамок): интерфейс стола обновляет её на месте
        return {"document": self.public_doc(record), "fields": [f.to_dict() for f in fields], "answer": ans.to_dict() if ans else None}

    # ---- отправка обращения
    @staticmethod
    def build_payload(ctx: DialogContext, include_passport: bool, contact: str = "") -> tuple[dict, list]:
        data = DialogMachine.template_data(ctx)
        titles = {"student_name": "ФИО", "current_university": "Текущий вуз", "current_course": "Текущий курс", "current_specialty": "Направление (текущее)",
                  "education_form": "Форма обучения", "debts": "Академические задолженности", "target_specialty": "Целевое направление",
                  "application_date": "Дата заявления"}
        fields = {titles[k]: v for k, v in data.items() if k in titles and v}
        excluded = []
        if data.get("passport_data"):
            if include_passport:
                fields["Паспортные данные"] = data["passport_data"]
            else:
                excluded.append("Паспортные данные")
        if contact:
            fields["Контакт"] = contact
        return {"request": REQUEST_TYPES.get(ctx.scenario, "не указано"), "fields": fields}, excluded

    def submission_preview(self, sid: str, include_passport: bool = False) -> dict:
        ctx = self.load_ctx(sid)
        payload, excluded = self.build_payload(ctx, include_passport)
        return {"delivery": self.delivery_status(), "payload": payload, "excluded": excluded, "attachment": bool(ctx.generated), "state": ctx.state,
                "already_sent": ctx.ticket_id, "ticket_code": ctx.ticket_code if ctx.ticket_id else "", "blocked": check_submission(ctx, self.svc.settings.get),
                "mail": self.mailer.status()}

    def submit(self, sid: str, confirm: bool, include_passport: bool = False, contact: str = "", source: str = "web") -> dict:
        storage = self.storage
        with self.locks.get(sid):
            ctx = self.load_ctx(sid)
            if not confirm:
                storage.events.log("ticket", "Анкета", "declined", sid)
                return {"sent": False, "message": "Хорошо, ничего не отправлено. Документ можно скачать и передать в деканат самостоятельно."}
            status = self.delivery_status()
            if not status["available"]:
                raise ServiceError(503, f"Автоматическая отправка пока недоступна: {status['reason']}. Скачайте готовый документ и передайте его в деканат лично.")
            if ctx.state != COMPLETED:
                raise ServiceError(409, "Сначала пройдите анкету, подтвердите данные и сформируйте документ.")
            if ctx.ticket_id:
                return {"sent": True, "duplicate": True, "ticket_id": ctx.ticket_id, "code": ctx.ticket_code, "message": "Обращение уже отправлено ранее."}
            blocked = check_submission(ctx, self.svc.settings.get)
            if blocked:
                storage.events.log("ticket", SCENARIO_TOPICS.get(ctx.scenario, "Анкета"), "blocked", sid)
                raise ServiceError(409, "Обращение не отправлено. " + " ".join(blocked))
            contact = clean_contact(contact)
            payload, excluded = self.build_payload(ctx, include_passport, contact)
            ref = session_ref(sid)
            topic = SCENARIO_TOPICS.get(ctx.scenario, "Анкета")
            owner = storage.users.by_data_sid(sid)  # обращение вошедшего привязывается к аккаунту: статус и ответы видны без кода, ответы приходят в Telegram
            ticket, created = storage.tickets.create(ref, ctx.scenario if ctx.scenario in REQUEST_TYPES else "other", topic, payload, "queue", f"{ref}:{ctx.case_id}",
                                                     user_id=owner["id"] if owner else None)
            if created and ctx.generated:
                last = storage.generated.get(ctx.generated[-1], sid)
                if last and Path(last["path"]).exists():
                    target = storage.ticket_dir(ticket["id"])
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(last["path"], target / "application.docx")
            ctx.ticket_id = ticket["id"]
            if created:
                ctx.ticket_code = new_public_code()
                storage.tickets.attach_code(ticket["id"], ctx.ticket_code)
            self.save_ctx(sid, ctx)
            if created:
                storage.events.log("ticket", topic, "sent", sid)
            code, ticket_id = ctx.ticket_code, ticket["id"]
        if created and self.notifier is not None:
            try:  # сообщение сотрудникам и подтверждение вошедшему не должны ломать отправку
                self.notifier.ticket_created(ticket, owner, source)
            except Exception:
                log.exception("Не удалось поставить в очередь уведомление о новом обращении")
        # письмо с номером и кодом — по желанию (студент указал почту) и вне блокировки сессии: почтовый сервер может отвечать долго
        mail = {"status": "none"}
        if created and contact:
            if "@" not in contact or contact.startswith("@"):
                mail = {"status": "skipped"}  # телефон или Telegram: письма уходят только на почту
            else:
                mail = {"status": self.mailer.send_ticket_code(contact, ticket_id, code), "to": mask_email(contact)}
        return {"sent": True, "duplicate": not created, "ticket_id": ticket_id, "code": code, "excluded": excluded, "mail": mail,
                "message": "Обращение передано в очередь деканата. Окончательное решение принимает сотрудник деканата."}

    def next_steps(self, sid: str) -> dict:
        """Что принести и в какой срок решат: из действующего Положения, со ссылкой на пункт."""
        ctx = self.load_ctx(sid)
        return build_next_steps(ctx.scenario, lambda question: self.svc.analyze(None, "", question).answer)

    def ticket_status(self, ticket_id: int, code: str, key: str) -> dict:
        """Статус обращения по номеру и коду (без входа и без сессии)."""
        if not self.status_limiter.allowed(key):
            raise ServiceError(429, "Слишком много неверных попыток. Подождите несколько минут и попробуйте снова.")
        row = self.storage.tickets.check_code(ticket_id, code)
        if row is None:  # одинаковый ответ на «нет такого номера» и «неверный код»: номера нельзя перебирать
            self.status_limiter.fail(key)
            raise ServiceError(404, "Обращение с таким номером и кодом не найдено. Проверьте номер и код.")
        return {"id": row["id"], "status": row["status"], "status_title": STATUS_TITLES.get(row["status"], row["status"]), "topic": row["topic"],
                "created_at": row["created_at"], "updated_at": row["updated_at"], "reply": row["reply"], "reply_at": row["reply_at"]}
