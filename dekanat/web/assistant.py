"""API ассистента для пользователя без аккаунта (/api/assistant/*): диалог с анкетой, документ сессии, DOCX, отправка обращения.

Идентификатор сессии — случайный токен в HttpOnly-cookie. Все данные привязаны к нему на сервере: чужую анкету,
документ или файл получить нельзя, даже зная идентификатор документа.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..dialog import COMPLETED, DOCUMENT_GENERATION, DialogContext, DialogMachine, Reply
from ..dialog.docstate import apply_correction, make_record, public_fields, restore_fields, suggestions_from_record
from ..dialog.questions import REQUEST_TYPES
from ..templates import TemplateError, TemplateNotFound, TemplateStore, fill_docx
from ..storage import session_ref
from ..storage.topics import classify_topic
from ..ocr.engine import OcrUnavailable
from ..ocr.preprocess import UnsupportedFile

log = logging.getLogger("dekanat.assistant")

COOKIE_SID = "dekanat_sid"
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


class MessageIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    template_id: Optional[str] = Field(default=None, max_length=80)
    # сообщение не про анкету: не отвечать здесь, клиент сам запустит обычную проверку (/api/jobs, с прогрессом и снимком)
    defer: bool = False


class FieldIn(BaseModel):
    unit: str = Field(max_length=120)
    value: str = Field(default="", max_length=400)
    question: str = Field(default="", max_length=2000)  # вопрос, на который пересчитать ответ по исправленной карточке (пусто — общая проверка)


class GenerateIn(BaseModel):
    template_id: Optional[str] = Field(default=None, max_length=80)


class SubmitIn(BaseModel):
    confirm: bool = False
    include_passport: bool = False


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


def build_assistant_router(svc, storage, templates: TemplateStore, ocr_slots, max_bytes: int, delivery_status, cookie_secure,
                           locks: Optional[SessionLocks] = None) -> APIRouter:
    router = APIRouter(prefix="/api/assistant", tags=["assistant"])
    machine = DialogMachine(svc.llm)
    locks = locks or SessionLocks()
    last_purge = {"t": 0.0}

    # ------------------------------------------------------------------ сессия
    def ttl() -> float:
        return storage.session_ttl

    def maybe_purge() -> None:
        if time.time() - last_purge["t"] > 600:
            last_purge["t"] = time.time()
            try:
                storage.purge()
            except Exception:  # чистка не должна мешать работе
                log.exception("Не удалось очистить просроченные данные")

    created: dict[str, list[float]] = {}  # адрес -> время создания последних сессий (защита от массового создания)

    def throttle_new_sessions(request: Request) -> None:
        limit = int(svc.settings.get("server.max_new_sessions_per_minute", 60))
        ip = request.client.host if request.client else "?"
        now = time.time()
        recent = [t for t in created.get(ip, []) if now - t < 60]
        if limit and len(recent) >= limit:
            created[ip] = recent
            raise HTTPException(429, "Слишком много новых сессий с вашего адреса. Подождите минуту и повторите.")
        created[ip] = recent + [now]
        if len(created) > 5000:
            created.clear()

    def ensure(request: Request, response: Response) -> tuple[str, str]:
        """(идентификатор сессии, ok | new | expired). Пустая или просроченная сессия заменяется новой."""
        maybe_purge()  # сроки хранения соблюдаются и на сервере, где новых сессий давно нет
        cookie = request.cookies.get(COOKIE_SID, "")
        if cookie and storage.sessions.touch(cookie, ttl()):
            return cookie, "ok"
        throttle_new_sessions(request)
        sid = storage.sessions.create(ttl())
        response.set_cookie(COOKIE_SID, sid, max_age=int(ttl()), httponly=True, samesite="lax", secure=cookie_secure(request), path="/")
        return sid, ("expired" if cookie else "new")

    def require(request: Request) -> str:
        maybe_purge()
        cookie = request.cookies.get(COOKIE_SID, "")
        if not cookie or not storage.sessions.touch(cookie, ttl()):
            raise HTTPException(410, "Сессия истекла или не найдена: данные удалены. Обновите страницу и начните заново.")
        return cookie

    def load_ctx(sid: str) -> DialogContext:
        ctx = DialogContext.from_dict(storage.sessions.load_state(sid))
        if DialogMachine.expire_sensitive(ctx, storage.passport_ttl):
            save_ctx(sid, ctx)  # срок хранения паспортных данных истёк — они удалены из анкеты
        return ctx

    def save_ctx(sid: str, ctx: DialogContext) -> None:
        storage.sessions.save_state(sid, ctx.to_dict())

    def log_events(sid: str, ctx: DialogContext, events: list) -> None:
        topic = SCENARIO_TOPICS.get(ctx.scenario, "Анкета")
        for name, value in events:
            if name == "screening_started":
                storage.events.log("screening_started", SCENARIO_TOPICS.get(value, "Анкета"), value or "unknown", sid)
            elif name == "document_generated":
                storage.events.log("document_generated", topic, "ok", sid)
            elif name in ("offer", "screening_completed", "screening_cancelled", "review_confirmed", "generation_failed"):
                storage.events.log(name, topic, value or "", sid)

    def doc_record(sid: str) -> Optional[dict]:
        return storage.sessions.load_document(sid)

    def public_doc(record: Optional[dict]) -> Optional[dict]:
        if not record:
            return None
        return {"filename": record["filename"], "doc_type": record["doc_type"], "fields": public_fields(record), "warnings": record.get("warnings", []),
                "corrected": record.get("corrected", [])}

    # ------------------------------------------------------------------ вопросы вне анкеты
    def answer_question(sid: str, text: str, record: Optional[dict]) -> dict:
        if record and record["doc_type"]["id"]:
            ans = svc.answer_for_fields(text, record["doc_type"]["id"], restore_fields(record), document_first=True)
        else:
            ans = svc.analyze(None, "", text).answer
        storage.events.log("question", classify_topic(text), ans.verdict, sid)
        return ans.to_dict()

    # ------------------------------------------------------------------ генерация документа
    def available_templates() -> list[dict]:
        return [{"id": t["id"], "name": t["name"], "placeholders": [p["key"] for p in t["placeholders"]]} for t in storage.templates.list()]

    def do_generate(sid: str, ctx: DialogContext, template_id: Optional[str]) -> dict:
        if ctx.state not in (DOCUMENT_GENERATION, COMPLETED):
            raise HTTPException(409, "Сначала пройдите анкету и подтвердите собранные данные.")
        if not template_id:
            items = storage.templates.list()
            if not items:
                raise HTTPException(404, "Пока нет ни одного шаблона документа: администратор ещё не загрузил DOCX-шаблон. Ваши ответы сохранены на время сессии.")
            if len(items) > 1:
                raise HTTPException(400, "Выберите шаблон из списка.")
            template_id = items[0]["id"]
        row = storage.templates.get(template_id)
        if row is None:
            raise HTTPException(404, "Шаблон не найден (возможно, его только что удалили). Выберите другой шаблон.")
        try:
            path = templates.path_of(template_id)
        except TemplateNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        data = machine.template_data(ctx)
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
            reply = machine.generation_failed(ctx, f"Не удалось сформировать документ: {exc}")
            log_events(sid, ctx, reply.events)
            raise HTTPException(422, reply.text) from exc
        try:
            storage.generated.add(doc_id, sid, template_id, f"{row['name']}.docx", str(result.path), storage.generated_ttl, result.unfilled)
        except Exception:
            result.path.unlink(missing_ok=True)  # документ без записи о владельце никому не выдать и не удалить по сроку — убираем сразу
            raise
        ctx.gen_cache[fingerprint] = doc_id
        titles = [placeholder_title(k) for k in result.unfilled]
        if ctx.state == DOCUMENT_GENERATION:
            reply = machine.generation_done(ctx, doc_id, titles)
        else:  # повторная генерация из completed (например, по другому шаблону)
            ctx.generated.append(doc_id)
            reply = Reply("Ещё один документ готов." + (f" Не заполнены (данных нет): {', '.join(titles)}." if titles else ""), ctx.state, "done",
                          events=[("document_generated", ctx.scenario)])
        log_events(sid, ctx, reply.events)
        return {"doc_id": doc_id, "duplicate": False, "unfilled": titles, "reply": reply}

    def download_info(sid: str, doc_id: Optional[str]) -> Optional[dict]:
        if not doc_id:
            return None
        row = storage.generated.get(doc_id, sid)
        return {"id": doc_id, "url": f"/api/assistant/download/{doc_id}", "filename": row["filename"]} if row else None

    # ------------------------------------------------------------------ маршруты
    @router.get("/state")
    def state(request: Request, response: Response):
        sid, status = ensure(request, response)
        ctx = load_ctx(sid)
        return {"session": status, "welcome": WELCOME, "dialog": ctx.public(), "document": public_doc(doc_record(sid)),
                "templates": available_templates(), "delivery": delivery_status(), "llm": {"enabled": svc.llm.enabled},
                "downloads": [d for d in (download_info(sid, g) for g in ctx.generated) if d], "ticket_id": ctx.ticket_id}

    @router.post("/message")
    def message(body: MessageIn, request: Request, response: Response):
        sid, status = ensure(request, response)
        with locks.get(sid):
            ctx = load_ctx(sid)
            record = doc_record(sid)
            ctx.suggestions = suggestions_from_record(record)
            reply = machine.handle(ctx, body.text)
            answer, download = None, None
            if reply.kind == "passthrough":
                answer = answer_question(sid, body.text.strip(), record) if body.text.strip() and not body.defer else None
                if answer:
                    reply.text = answer["text"]
            elif reply.kind == "generate":
                result = do_generate(sid, ctx, body.template_id)
                if result["reply"] is not None:
                    reply = result["reply"]
                else:
                    reply = Reply("Этот документ уже сформирован — его можно скачать.", ctx.state, "done")
                download = download_info(sid, result["doc_id"])
            log_events(sid, ctx, reply.events)
            save_ctx(sid, ctx)
        return {"session": status, "reply": reply.to_dict(), "dialog": ctx.public(), "answer": answer, "download": download}

    @router.post("/generate")
    def generate(body: GenerateIn, request: Request):
        sid = require(request)
        with locks.get(sid):
            ctx = load_ctx(sid)
            result = do_generate(sid, ctx, body.template_id)
            save_ctx(sid, ctx)
        reply = result["reply"]
        return {"download": download_info(sid, result["doc_id"]), "duplicate": result["duplicate"], "unfilled": result["unfilled"],
                "reply": reply.to_dict() if reply else None, "dialog": ctx.public()}

    @router.get("/download/{doc_id}")
    def download(doc_id: str, request: Request):
        sid = require(request)
        row = storage.generated.get(doc_id, sid)  # только владелец сессии и только до окончания срока хранения
        if row is None or not Path(row["path"]).exists():
            raise HTTPException(404, "Документ не найден или срок его хранения истёк. Сформируйте его заново.")
        return FileResponse(row["path"], media_type=DOCX_MIME, filename=row["filename"], headers={"Cache-Control": "no-store"})

    @router.get("/templates")
    def list_templates():
        return available_templates()

    # ---- документ сессии
    @router.post("/document")
    async def upload_document(request: Request, response: Response, file: UploadFile = File(...), doc_type: str = Form("auto")):
        sid, status = ensure(request, response)
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(413, f"Файл больше {max_bytes // (1024 * 1024)} МБ")
        if not data:
            raise HTTPException(400, "Файл пустой")
        name = Path(file.filename or "документ").name

        def work():
            with ocr_slots:
                return svc.analyze(data, name, "", doc_type)

        try:
            result = await run_in_threadpool(work)
        except (UnsupportedFile, OcrUnavailable) as exc:
            raise HTTPException(400 if isinstance(exc, UnsupportedFile) else 503, str(exc)) from exc
        record = make_record(result, name)
        with locks.get(sid):
            storage.sessions.save_document(sid, record)
        storage.events.log("document_check", classify_topic(""), result.answer.verdict, sid)
        return {"session": status, "document": public_doc(record), "answer": result.answer.to_dict()}

    @router.get("/document")
    def get_document(request: Request, response: Response):
        sid, status = ensure(request, response)
        return {"session": status, "document": public_doc(doc_record(sid))}

    @router.delete("/document")
    def delete_document(request: Request):
        sid = require(request)
        storage.sessions.delete_document(sid)
        return {"ok": True}

    @router.patch("/document/fields")
    def patch_field(body: FieldIn, request: Request):
        sid = require(request)
        with locks.get(sid):
            record = doc_record(sid)
            if not record:
                raise HTTPException(404, "Документ не загружен")
            spec = svc.doc_types.get(record["doc_type"]["id"] or "")
            ok, error = apply_correction(record, spec, body.unit, body.value)
            if not ok:
                raise HTTPException(400, error)
            storage.sessions.save_document(sid, record)
        fields = restore_fields(record)
        ans = svc.answer_for_fields(body.question.strip(), record["doc_type"]["id"], fields, document_first=True) if record["doc_type"]["id"] else None
        # fields — карточка в том же виде, что в ответе проверки (без рамок): интерфейс стола обновляет её на месте
        return {"document": public_doc(record), "fields": [f.to_dict() for f in fields], "answer": ans.to_dict() if ans else None}

    # ---- отправка обращения
    def build_payload(ctx: DialogContext, include_passport: bool) -> tuple[dict, list]:
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
        return {"request": REQUEST_TYPES.get(ctx.scenario, "не указано"), "fields": fields}, excluded

    @router.get("/submission")
    def submission_preview(request: Request, include_passport: bool = False):
        sid = require(request)
        ctx = load_ctx(sid)
        payload, excluded = build_payload(ctx, include_passport)
        return {"delivery": delivery_status(), "payload": payload, "excluded": excluded, "attachment": bool(ctx.generated), "state": ctx.state,
                "already_sent": ctx.ticket_id}

    @router.post("/submission")
    def submit(body: SubmitIn, request: Request):
        sid = require(request)
        with locks.get(sid):
            ctx = load_ctx(sid)
            if not body.confirm:
                storage.events.log("ticket", "Анкета", "declined", sid)
                return {"sent": False, "message": "Хорошо, ничего не отправлено. Документ можно скачать и передать в деканат самостоятельно."}
            status = delivery_status()
            if not status["available"]:
                raise HTTPException(503, f"Автоматическая отправка пока недоступна: {status['reason']}. Скачайте готовый документ и передайте его в деканат лично.")
            if ctx.state != COMPLETED:
                raise HTTPException(409, "Сначала пройдите анкету, подтвердите данные и сформируйте документ.")
            if ctx.ticket_id:
                return {"sent": True, "duplicate": True, "ticket_id": ctx.ticket_id, "message": "Обращение уже отправлено ранее."}
            payload, excluded = build_payload(ctx, body.include_passport)
            ref = session_ref(sid)
            topic = SCENARIO_TOPICS.get(ctx.scenario, "Анкета")
            ticket, created = storage.tickets.create(ref, ctx.scenario if ctx.scenario in REQUEST_TYPES else "other", topic, payload, "queue", f"{ref}:{ctx.case_id}")
            if created and ctx.generated:
                last = storage.generated.get(ctx.generated[-1], sid)
                if last and Path(last["path"]).exists():
                    target = storage.ticket_dir(ticket["id"])
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(last["path"], target / "application.docx")
            ctx.ticket_id = ticket["id"]
            save_ctx(sid, ctx)
            if created:
                storage.events.log("ticket", topic, "sent", sid)
        return {"sent": True, "duplicate": not created, "ticket_id": ticket["id"], "excluded": excluded,
                "message": "Обращение передано в очередь деканата. Окончательное решение принимает сотрудник деканата."}

    # ---- удаление данных и новая анкета
    @router.delete("/data")
    def delete_data(request: Request, response: Response):
        cookie = request.cookies.get(COOKIE_SID, "")
        if cookie:
            storage.delete_session(cookie)
        response.delete_cookie(COOKIE_SID, path="/")
        return {"ok": True, "message": "Ваши данные удалены: анкета, загруженный документ и готовые файлы."}

    return router
