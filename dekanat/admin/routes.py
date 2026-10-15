"""REST API администратора (/api/admin/*). Каждый маршрут, кроме входа, требует действующую сессию администратора — проверка на сервере."""

from __future__ import annotations

import csv
import io
import logging
import tempfile

import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, Response as RawResponse
from pydantic import BaseModel, Field

from ..templates import TemplateError, TemplateExists, TemplateNotFound, TemplateStore
from ..templates.docx_templates import MAX_TEMPLATE_BYTES
from ..kb.check import load_golden, run_check
from ..doctypes import NEW_TYPE_TEMPLATE, DocTypeError
from ..kb.documents import KnowledgeBaseError
from ..storage import TICKET_STATUSES, TICKET_TYPES
from ..storage.topics import all_topics
from .auth import COOKIE_ADMIN

log = logging.getLogger("dekanat.admin")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class LoginIn(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=300)


class PasswordIn(BaseModel):
    current: str = Field(max_length=300)
    new: str = Field(max_length=300)


class TicketPatch(BaseModel):
    status: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    reply: Optional[str] = Field(default=None, max_length=2000)  # ответ студенту: виден ему на странице статуса


class TicketBulk(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    status: str = Field(max_length=32)
    reply: Optional[str] = Field(default=None, max_length=2000)  # один ответ студентам всех выбранных обращений


class KbPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    active: Optional[bool] = None


class DocTypeIn(BaseModel):
    yaml: str = Field(max_length=400_000)


class NamePatch(BaseModel):
    name: str = Field(max_length=200)


def _day(value: Optional[str], end: bool = False) -> Optional[float]:
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(400, f"Дата «{value}» должна быть в формате ГГГГ-ММ-ДД") from exc
    return (d + timedelta(days=1 if end else 0)).timestamp()


# заготовки ответа студенту: кнопки над полем «Ответ студенту»; свои — в config/reply_templates.yaml
DEFAULT_REPLIES = [
    {"title": "Приглашение в деканат", "text": "Приходите в деканат с паспортом и оригиналом справки об обучении. Если время приёма вам не подходит, напишите, как с вами связаться."},
    {"title": "Не хватает справки", "text": "Для рассмотрения нужна справка об обучении из прежнего вуза с номером и датой выдачи, подписью и печатью. Принесите её в деканат."},
    {"title": "Есть задолженности", "text": "По вашим данным есть академические задолженности. Рассмотрение возможно после их закрытия."},
    {"title": "Решение принято", "text": "Решение по вашему обращению принято. Подробности сообщит сотрудник деканата."},
]


def load_reply_templates(path: Path) -> list:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, yaml.YAMLError):
        raw = None
    items = raw.get("templates", []) if isinstance(raw, dict) else []
    good = [{"title": str(i["title"]).strip()[:60], "text": str(i["text"]).strip()[:2000]} for i in items
            if isinstance(i, dict) and str(i.get("title", "")).strip() and str(i.get("text", "")).strip()]
    return good or DEFAULT_REPLIES


def build_admin_router(svc, storage, auth, kbm, templates: TemplateStore, max_bytes: int, notifier=None, telegram=None, doctypes=None) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])
    admin = Depends(auth.require)

    # ------------------------------------------------------------------ вход / выход
    @router.post("/login")
    def login(body: LoginIn, request: Request, response: Response):
        token, info = auth.login(request, body.username, body.password)
        auth.set_cookie(response, request, token)
        return info

    @router.post("/logout")
    def logout(request: Request, response: Response):
        auth.logout(request)
        auth.clear_cookie(response)
        return {"ok": True}

    @router.get("/me")
    def me(who: dict = admin):
        return {"username": who["username"], "role": who["role"]}

    @router.post("/password")
    def password(body: PasswordIn, request: Request, who: dict = admin):
        auth.change_password(who, body.current, body.new, keep_token=request.cookies.get(COOKIE_ADMIN, ""))
        return {"ok": True}

    @router.get("/info")
    def info(who: dict = admin):
        tickets_new = storage.tickets.list(status="new", limit=1)[1]
        return {
            "username": who["username"], "llm": svc.llm.status(), "regulation": {"name": svc.regulation_name, "clauses": len(svc.clauses)},
            "templates": len(storage.templates.list()), "kb_documents": len(kbm.list()), "tickets_new": tickets_new,
            "delivery": delivery_status(), "active_sessions": storage.sessions.count_active(), "ocr": svc.engine.info(),
            "telegram": {**(telegram.status() if telegram else {"enabled": False}), "users": storage.users.count(), "web_linked": storage.users.count_web_linked()},
        }

    def delivery_status() -> dict:
        channel = str(svc.settings.get("delivery.channel", "none")).lower()
        if channel == "queue":
            if storage.admins.count() == 0:
                return {"channel": "queue", "available": False, "reason": "нет ни одного администратора, который мог бы читать очередь"}
            return {"channel": "queue", "available": True, "reason": ""}
        return {"channel": "none", "available": False, "reason": "канал отправки обращений не настроен (delivery.channel)"}

    # ------------------------------------------------------------------ шаблоны DOCX
    def _template_dict(row: dict) -> dict:
        return {k: row[k] for k in ("id", "name", "original_name", "version", "placeholders", "created_at", "updated_at", "created_by")}

    def _upload_to_template(file: UploadFile, data: bytes, **kwargs) -> dict:
        if len(data) > MAX_TEMPLATE_BYTES:
            raise HTTPException(413, f"Файл больше {MAX_TEMPLATE_BYTES // (1024 * 1024)} МБ")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows может ещё держать файл после ошибки чтения: это не должно давать 500
            path = Path(tmp) / "upload.docx"  # имя файла от пользователя на диск не попадает
            path.write_bytes(data)
            original = Path(file.filename or "шаблон.docx").name
            if Path(original).suffix.lower() != ".docx":
                raise HTTPException(400, "Шаблон должен быть файлом .docx")
            kwargs["name"] = (kwargs.get("name") or Path(original).stem).strip()
            try:
                return templates.add(path, **kwargs)
            except TemplateExists as exc:
                raise HTTPException(409, str(exc)) from exc
            except TemplateError as exc:
                raise HTTPException(400, str(exc)) from exc

    @router.get("/templates")
    def list_templates(who: dict = admin):
        return [_template_dict(t) for t in storage.templates.list()]

    @router.post("/templates")
    async def add_template(file: UploadFile = File(...), name: str = Form(""), template_id: str = Form(""), replace: bool = Form(False), who: dict = admin):
        data = await file.read(MAX_TEMPLATE_BYTES + 1)
        info = _upload_to_template(file, data, name=name or None, template_id=template_id.strip() or None, overwrite=bool(replace), actor=who["username"])
        storage.audit.log(who["username"], "template.replace" if (replace and info.version > 1) else "template.create", "template", info.template_id,
                          {"name": info.name, "version": info.version, "placeholders": info.keys, "sha256": info.sha256})
        return info.to_dict()

    @router.put("/templates/{template_id}")
    async def replace_template(template_id: str, file: UploadFile = File(...), who: dict = admin):
        if storage.templates.get(template_id) is None:
            raise HTTPException(404, "Шаблон не найден")
        data = await file.read(MAX_TEMPLATE_BYTES + 1)
        current = storage.templates.get(template_id)
        info = _upload_to_template(file, data, template_id=template_id, name=current["name"], overwrite=True, actor=who["username"], note="замена файла")
        storage.audit.log(who["username"], "template.replace", "template", template_id, {"version": info.version, "placeholders": info.keys, "sha256": info.sha256})
        return info.to_dict()

    @router.patch("/templates/{template_id}")
    def rename_template(template_id: str, body: NamePatch, who: dict = admin):
        if storage.templates.get(template_id) is None:
            raise HTTPException(404, "Шаблон не найден")
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Название не может быть пустым")
        storage.templates.rename(template_id, name)
        storage.audit.log(who["username"], "template.rename", "template", template_id, {"name": name})
        return _template_dict(storage.templates.get(template_id))

    @router.get("/templates/{template_id}")
    def get_template(template_id: str, who: dict = admin):
        row = storage.templates.get(template_id)
        if row is None:
            raise HTTPException(404, "Шаблон не найден")
        return {**_template_dict(row), "history": storage.templates.versions(template_id)}

    @router.get("/templates/{template_id}/download")
    def download_template(template_id: str, who: dict = admin):
        try:
            path = templates.path_of(template_id)
        except TemplateNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        row = storage.templates.get(template_id)
        return FileResponse(path, media_type=DOCX_MIME, filename=f"{row['name']}.docx")

    @router.delete("/templates/{template_id}")
    def delete_template(template_id: str, who: dict = admin):
        row = storage.templates.get(template_id)
        if row is None:
            raise HTTPException(404, "Шаблон не найден")
        try:
            templates.delete(template_id)
        except TemplateNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        storage.audit.log(who["username"], "template.delete", "template", template_id, {"name": row["name"], "version": row["version"]})
        return {"ok": True}

    @router.get("/templates/{template_id}/history")
    def template_history(template_id: str, who: dict = admin):
        return {"versions": storage.templates.versions(template_id), "log": storage.audit.list("template", template_id, limit=100)}

    # ------------------------------------------------------------------ типы документов
    def _type_call(fn, *args, write: bool = False):
        if write and not svc.settings.get("server.allow_edit", True):
            raise HTTPException(403, "Редактирование отключено (server.allow_edit: false)")
        try:
            return fn(*args)
        except DocTypeError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.get("/doctypes")
    def list_doctypes(who: dict = admin):
        return doctypes.list()

    @router.get("/doctypes/template")
    def doctype_template(who: dict = admin):
        return {"yaml": NEW_TYPE_TEMPLATE}

    @router.post("/doctypes/validate")
    def validate_doctype(body: DocTypeIn, who: dict = admin):
        return doctypes.validate(body.yaml)

    @router.get("/doctypes/{type_id}/yaml")
    def doctype_yaml(type_id: str, who: dict = admin):
        return {"id": type_id, "yaml": _type_call(doctypes.yaml, type_id)}

    @router.post("/doctypes")
    def add_doctype(body: DocTypeIn, who: dict = admin):
        return _type_call(doctypes.save, "new", body.yaml, who["username"], write=True)

    @router.put("/doctypes/{type_id}")
    def replace_doctype(type_id: str, body: DocTypeIn, who: dict = admin):
        return _type_call(doctypes.save, type_id, body.yaml, who["username"], write=True)

    @router.delete("/doctypes/{type_id}")
    def delete_doctype(type_id: str, who: dict = admin):
        return _type_call(doctypes.delete, type_id, who["username"], write=True)

    # ------------------------------------------------------------------ база знаний
    def _kb_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KnowledgeBaseError as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "Документ базы знаний не найден") from exc

    @router.get("/kb")
    def list_kb(who: dict = admin):
        return kbm.list()

    @router.post("/kb/check")
    def check_kb(who: dict = admin):
        """Контрольные вопросы (config/golden_questions.yaml) -> ответы секретаря по текущей базе знаний: что сходится с ожидаемым, а что сломалось."""
        items = load_golden(Path(svc.settings.root) / "config" / "golden_questions.yaml")
        if not items:
            raise HTTPException(404, "Контрольных вопросов нет: добавьте их в config/golden_questions.yaml.")
        result = run_check(lambda question: svc.analyze(None, "", question).answer, items)
        storage.audit.log(who["username"], "kb.check", "kb", "", {"passed": result["passed"], "total": result["total"]})
        return result

    @router.post("/kb")
    async def add_kb(file: UploadFile = File(...), name: str = Form(""), who: dict = admin):
        data = await file.read(max_bytes + 1)
        return _kb_call(kbm.add, name, Path(file.filename or "документ.txt").name, data, who["username"])

    @router.get("/kb/{doc_id}")
    def get_kb(doc_id: str, who: dict = admin):
        row = kbm.get(doc_id)
        if row is None:
            raise HTTPException(404, "Документ базы знаний не найден")
        return {**row, "clause_list": _kb_call(kbm.clauses, doc_id)}

    @router.put("/kb/{doc_id}")
    async def replace_kb(doc_id: str, file: UploadFile = File(...), who: dict = admin):
        data = await file.read(max_bytes + 1)
        return _kb_call(kbm.replace, doc_id, Path(file.filename or "документ.txt").name, data, who["username"])

    @router.patch("/kb/{doc_id}")
    def patch_kb(doc_id: str, body: KbPatch, who: dict = admin):
        return _kb_call(kbm.update, doc_id, who["username"], name=body.name, active=body.active)

    @router.delete("/kb/{doc_id}")
    def delete_kb(doc_id: str, who: dict = admin):
        return {"ok": True, "action": _kb_call(kbm.delete, doc_id, who["username"])}

    @router.post("/reindex")
    def reindex(who: dict = admin):
        return kbm.reindex(who["username"])

    @router.get("/kb/{doc_id}/history")
    def kb_history(doc_id: str, who: dict = admin):
        return {"versions": kbm.history(doc_id), "log": storage.audit.list("kb", doc_id, limit=100)}

    # ------------------------------------------------------------------ обращения
    @router.get("/tickets")
    def list_tickets(date_from: str = "", date_to: str = "", type: str = "", status: str = "", topic: str = "", limit: int = 50, offset: int = 0, who: dict = admin):
        if type and type not in TICKET_TYPES:
            raise HTTPException(400, f"type: одно из {list(TICKET_TYPES)}")
        if status and status not in TICKET_STATUSES:
            raise HTTPException(400, f"status: одно из {list(TICKET_STATUSES)}")
        rows, total = storage.tickets.list(_day(date_from), _day(date_to, end=True), type or None, status or None, topic or None, limit, offset)
        counts = storage.tickets.status_counts(_day(date_from), _day(date_to, end=True), type or None, topic or None)
        return {"items": rows, "total": total, "counts": counts, "statuses": list(TICKET_STATUSES), "types": list(TICKET_TYPES), "topics": sorted(set(storage.tickets.topics()) | set(all_topics()))}

    CSV_TYPES = {"transfer": "перевод", "enrollment": "зачисление", "other": "другое"}
    CSV_STATUSES = {"new": "новое", "in_progress": "в работе", "done": "обработано", "rejected": "отклонено"}
    CSV_FIELDS = ["ФИО", "Текущий вуз", "Текущий курс", "Направление (текущее)", "Форма обучения", "Академические задолженности", "Целевое направление", "Контакт", "Паспортные данные"]

    def _csv_cell(value) -> str:
        """Ячейка без «формул»: значение, начинающееся с = + - @, Excel выполнил бы как формулу."""
        text = "" if value is None else str(value).replace("\r", " ")
        return "'" + text if text[:1] in ("=", "+", "-", "@", "\t") else text

    @router.get("/tickets/export.csv")
    def export_tickets(date_from: str = "", date_to: str = "", type: str = "", status: str = "", topic: str = "", who: dict = admin):
        if type and type not in TICKET_TYPES:
            raise HTTPException(400, f"type: одно из {list(TICKET_TYPES)}")
        if status and status not in TICKET_STATUSES:
            raise HTTPException(400, f"status: одно из {list(TICKET_STATUSES)}")
        rows = storage.tickets.export(_day(date_from), _day(date_to, end=True), type or None, status or None, topic or None)
        out = io.StringIO()
        w = csv.writer(out, delimiter=";", lineterminator="\r\n")  # «;» и BOM: Excel в русской локали открывает без «кракозябр»
        w.writerow(["№", "Дата (UTC)", "Тип", "Тема", "Статус", *CSV_FIELDS, "Ответ студенту", "Заметка сотрудника"])
        for t in rows:
            fields = (t.get("payload") or {}).get("fields", {})
            w.writerow([t["id"], (t["created_at"] or "")[:16].replace("T", " "), CSV_TYPES.get(t["type"], t["type"]), _csv_cell(t["topic"]), CSV_STATUSES.get(t["status"], t["status"]),
                        *[_csv_cell(fields.get(k)) for k in CSV_FIELDS], _csv_cell(t.get("reply")), _csv_cell(t.get("note"))])
        storage.audit.log(who["username"], "ticket.export", "ticket", "", {"rows": len(rows)})
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return RawResponse(("\ufeff" + out.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                           headers={"Content-Disposition": f'attachment; filename="obrasheniya-{stamp}.csv"', "Cache-Control": "no-store"})

    STATUS_RU = {"new": "новое", "in_progress": "в работе", "done": "обработано", "rejected": "отклонено"}

    def _ticket_history(row: dict) -> list:
        """Что происходило с обращением: отправка студентом и действия сотрудников (кто, когда, что). Новые записи сверху."""
        out = []
        for e in storage.audit.list("ticket", str(row["id"]), limit=50):
            if e["action"] != "ticket.update":
                continue
            d = e["details"] or {}
            parts = []
            if d.get("status"):
                parts.append(f"статус: {STATUS_RU.get(d['status'], d['status'])}")
            if d.get("reply"):
                parts.append("изменён ответ студенту")
            if d.get("note"):
                parts.append("изменена заметка")
            if parts:
                out.append({"at": e["ts"], "actor": e["actor"], "text": ", ".join(parts) + (" (вместе с другими обращениями)" if d.get("bulk") else "")})
        out.append({"at": row["created_at"], "actor": "student", "text": "обращение отправлено студентом"})
        return out

    @router.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: int, who: dict = admin):
        row = storage.tickets.get(ticket_id)
        if row is None:
            raise HTTPException(404, "Обращение не найдено")
        row["has_attachment"] = (storage.ticket_dir(ticket_id) / "application.docx").exists()
        row["similar"] = storage.tickets.similar(ticket_id)
        row["history"] = _ticket_history(row)
        return row

    @router.get("/reply-templates")
    def reply_templates(who: dict = admin):
        return {"templates": load_reply_templates(Path(svc.settings.root) / "config" / "reply_templates.yaml")}

    def _notify_owner(before, after) -> None:
        """Владельцу обращения (вошёл через Telegram) уходит уведомление о новом статусе или ответе. Сбой очереди не должен ломать работу сотрудника."""
        if notifier is None:
            return
        try:
            notifier.ticket_changed(before, after)
        except Exception:
            logging.getLogger("dekanat.admin").exception("Не удалось поставить уведомление в очередь")

    @router.patch("/tickets/{ticket_id}")
    def patch_ticket(ticket_id: int, body: TicketPatch, who: dict = admin):
        if body.status is not None and body.status not in TICKET_STATUSES:
            raise HTTPException(400, f"status: одно из {list(TICKET_STATUSES)}")
        before = storage.tickets.get(ticket_id)
        if not storage.tickets.update(ticket_id, body.status, body.note, body.reply):
            raise HTTPException(404, "Обращение не найдено")
        storage.audit.log(who["username"], "ticket.update", "ticket", str(ticket_id), {"status": body.status, "note": bool(body.note), "reply": bool(body.reply)})
        after = storage.tickets.get(ticket_id)
        _notify_owner(before, after)
        return after

    @router.post("/tickets/bulk")
    def bulk_tickets(body: TicketBulk, who: dict = admin):
        """Один статус (и, если нужно, один ответ студентам) сразу для нескольких обращений. В журнале и истории — запись по каждому."""
        if body.status not in TICKET_STATUSES:
            raise HTTPException(400, f"status: одно из {list(TICKET_STATUSES)}")
        updated, missing = [], []
        for ticket_id in dict.fromkeys(body.ids):  # повторы в запросе не в счёт
            before = storage.tickets.get(ticket_id)
            if storage.tickets.update(ticket_id, body.status, None, body.reply):
                _notify_owner(before, storage.tickets.get(ticket_id))
                storage.audit.log(who["username"], "ticket.update", "ticket", str(ticket_id), {"status": body.status, "note": False, "reply": bool(body.reply), "bulk": True})
                updated.append(ticket_id)
            else:
                missing.append(ticket_id)
        return {"updated": updated, "missing": missing, "status": body.status}

    @router.get("/tickets/{ticket_id}/attachment")
    def ticket_attachment(ticket_id: int, who: dict = admin):
        path = storage.ticket_dir(ticket_id) / "application.docx"
        if storage.tickets.get(ticket_id) is None or not path.exists():
            raise HTTPException(404, "Вложения нет")
        return FileResponse(path, media_type=DOCX_MIME, filename=f"obrashenie-{ticket_id}.docx")

    # ------------------------------------------------------------------ статистика и история
    @router.get("/stats")
    def stats(days: int = 30, who: dict = admin):
        return storage.events.stats(max(1, min(365, days)))

    @router.get("/audit")
    def audit(object_type: str = "", object_id: str = "", limit: int = 100, offset: int = 0, who: dict = admin):
        return storage.audit.list(object_type or None, object_id or None, limit, offset)

    @router.post("/purge")
    def purge(who: dict = admin):
        result = storage.purge()
        storage.audit.log(who["username"], "storage.purge", "storage", "", result)
        return result

    return router
