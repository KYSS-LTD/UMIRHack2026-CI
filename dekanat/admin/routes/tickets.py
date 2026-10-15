"""Обращения студентов: список, фильтры, статусы, выгрузка CSV, вложения и заготовки ответов."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response as RawResponse
from pydantic import BaseModel, Field

from ...assistant import DOCX_MIME
from ...storage import TICKET_STATUSES, TICKET_TYPES
from ...storage.topics import all_topics

class TicketPatch(BaseModel):
    status: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    reply: Optional[str] = Field(default=None, max_length=2000)  # ответ студенту: виден ему на странице статуса


class TicketBulk(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    status: str = Field(max_length=32)
    reply: Optional[str] = Field(default=None, max_length=2000)  # один ответ студентам всех выбранных обращений


def _day(value: Optional[str], end: bool = False) -> Optional[float]:
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(400, f"Дата «{value}» должна быть в формате ГГГГ-ММ-ДД") from exc
    return (d + timedelta(days=1 if end else 0)).timestamp()


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


def register(router: APIRouter, ctx) -> None:
    svc, storage, notifier, admin = ctx.svc, ctx.storage, ctx.notifier, ctx.admin
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
