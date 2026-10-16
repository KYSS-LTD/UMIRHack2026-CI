"""Обращения в деканат: создание, фильтры, статусы, ответы, код статуса для студента."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from typing import Optional

from ..db import Database, iso, now
from .common import _j, _loads


TICKET_STATUSES = ("new", "in_progress", "done", "rejected")
TICKET_TYPES = ("transfer", "enrollment", "other")
# код статуса: 8 знаков без похожих (0/O, 1/I), показывается студенту один раз; в базе — только хэш вместе с номером обращения
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_public_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _code_hash(ticket_id: int, code: str) -> str:
    return hashlib.sha256(f"{ticket_id}:{re.sub(r'[^A-Z0-9]', '', str(code).upper())}".encode("utf-8")).hexdigest()


class TicketRepo:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r: sqlite3.Row, with_payload: bool) -> dict:
        out = {"id": r["id"], "created_at": iso(r["created_at"]), "updated_at": iso(r["updated_at"]), "type": r["type"], "status": r["status"],
               "topic": r["topic"], "channel": r["channel"], "note": r["note"], "reply": r["reply"], "reply_at": iso(r["reply_at"])}
        if with_payload:
            out["payload"] = _loads(r["payload_json"], {})
        return out

    def create(self, ref: str, type_: str, topic: str, payload: dict, channel: str, idem_key: str, user_id: Optional[int] = None) -> tuple[dict, bool]:
        """Идемпотентно: повторная отправка той же анкеты возвращает уже созданное обращение (created=False)."""
        t = now()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE idem_key=?", (idem_key,)).fetchone()
            if row is not None:
                return self._row(row, True), False
            cur = conn.execute("INSERT INTO tickets(created_at, updated_at, session_ref, type, status, topic, payload_json, channel, idem_key, user_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (t, t, ref, type_, "new", topic, _j(payload), channel, idem_key, user_id))
            row = conn.execute("SELECT * FROM tickets WHERE id=?", (cur.lastrowid,)).fetchone()
        return self._row(row, True), True

    def get(self, ticket_id: int) -> Optional[dict]:
        r = self.db.one("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        return self._row(r, True) if r else None

    def find_by_key(self, idem_key: str) -> Optional[dict]:
        r = self.db.one("SELECT * FROM tickets WHERE idem_key=?", (idem_key,))
        return self._row(r, True) if r else None

    @staticmethod
    def _where(date_from, date_to, type_, status, topic) -> tuple[str, list]:
        where, params = ["1=1"], []
        if date_from is not None:
            where.append("created_at>=?")
            params.append(date_from)
        if date_to is not None:
            where.append("created_at<?")
            params.append(date_to)
        if type_:
            where.append("type=?")
            params.append(type_)
        if status:
            where.append("status=?")
            params.append(status)
        if topic:
            where.append("topic=?")
            params.append(topic)
        return " AND ".join(where), params

    def status_counts(self, date_from: Optional[float] = None, date_to: Optional[float] = None, type_: Optional[str] = None, topic: Optional[str] = None) -> dict:
        """Сколько обращений в каждом статусе при тех же фильтрах (без фильтра по статусу): счётчики над списком одним запросом."""
        w, params = self._where(date_from, date_to, type_, None, topic)
        counts = {s: 0 for s in TICKET_STATUSES}
        for r in self.db.query(f"SELECT status, COUNT(*) AS n FROM tickets WHERE {w} GROUP BY status", params):
            counts[r["status"]] = int(r["n"])
        return counts

    def list(self, date_from: Optional[float] = None, date_to: Optional[float] = None, type_: Optional[str] = None, status: Optional[str] = None,
             topic: Optional[str] = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        w, params = self._where(date_from, date_to, type_, status, topic)
        total = int(self.db.one(f"SELECT COUNT(*) AS n FROM tickets WHERE {w}", params)["n"])
        rows = self.db.query(f"SELECT * FROM tickets WHERE {w} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", [*params, max(1, min(500, limit)), max(0, offset)])
        out = []
        for r in rows:
            item = self._row(r, False)
            payload = _loads(r["payload_json"], {})
            # для сотрудника: кто обратился и с чем — строка списка читается без открытия обращения (публичная страница статуса этого не получает)
            item["student"] = str((payload.get("fields") or {}).get("ФИО") or "").strip() or None
            item["request"] = str(payload.get("request") or "").strip()[:140] or None
            out.append(item)
        return out, total

    def similar(self, ticket_id: int, limit: int = 5) -> list[dict]:
        """Другие обращения с тем же ФИО (регистр, «ё» и пробелы не важны): сотрудник видит, что студент уже обращался."""
        def norm(text) -> str:
            return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()

        r = self.db.one("SELECT payload_json FROM tickets WHERE id=?", (ticket_id,))
        name = norm(_loads(r["payload_json"], {}).get("fields", {}).get("ФИО")) if r else ""
        if not name:
            return []
        out = []
        for o in self.db.query("SELECT id, status, created_at, payload_json FROM tickets WHERE id!=? ORDER BY id DESC LIMIT 2000", (ticket_id,)):
            if norm(_loads(o["payload_json"], {}).get("fields", {}).get("ФИО")) == name:
                out.append({"id": o["id"], "status": o["status"], "created_at": iso(o["created_at"])})
                if len(out) >= limit:
                    break
        return out

    def list_for_user(self, user_id: int, limit: int = 50) -> list[dict]:
        """Обращения аккаунта (без кода: вошедший пользователь видит их сам). Новые сверху."""
        rows = self.db.query("SELECT * FROM tickets WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?", (user_id, max(1, min(200, limit))))
        return [self._row(r, False) for r in rows]

    def get_for_user(self, ticket_id: int, user_id: int) -> Optional[dict]:
        r = self.db.one("SELECT * FROM tickets WHERE id=? AND user_id=?", (ticket_id, user_id))
        return self._row(r, False) if r else None

    def owner_of(self, ticket_id: int) -> Optional[int]:
        r = self.db.one("SELECT user_id FROM tickets WHERE id=?", (ticket_id,))
        return int(r["user_id"]) if r and r["user_id"] is not None else None

    def attach_user(self, ticket_id: int, user_id: int) -> bool:
        """Привязывает ещё ничьё обращение к аккаунту (по номеру и коду): чужое обращение отобрать нельзя."""
        return self.db.execute("UPDATE tickets SET user_id=? WHERE id=? AND user_id IS NULL", (user_id, ticket_id)).rowcount > 0

    def detach_user(self, user_id: int) -> int:
        """Аккаунт удалён: обращения остаются у деканата, но уже ни к кому не привязаны."""
        return self.db.execute("UPDATE tickets SET user_id=NULL WHERE user_id=?", (user_id,)).rowcount

    def attach_code(self, ticket_id: int, code: str) -> None:
        self.db.execute("UPDATE tickets SET public_hash=? WHERE id=?", (_code_hash(ticket_id, code), ticket_id))

    def check_code(self, ticket_id: int, code: str) -> Optional[dict]:
        """Обращение, если номер и код верны (сравнение без утечки по времени); иначе None."""
        r = self.db.one("SELECT * FROM tickets WHERE id=?", (ticket_id,))
        if r is None or not r["public_hash"] or not hmac.compare_digest(r["public_hash"], _code_hash(ticket_id, code)):
            return None
        return self._row(r, False)

    def export(self, date_from: Optional[float] = None, date_to: Optional[float] = None, type_: Optional[str] = None, status: Optional[str] = None,
               topic: Optional[str] = None, limit: int = 5000) -> list[dict]:
        """Обращения с данными анкеты для выгрузки (те же фильтры, что у списка); только для сотрудников."""
        w, params = self._where(date_from, date_to, type_, status, topic)
        rows = self.db.query(f"SELECT * FROM tickets WHERE {w} ORDER BY created_at DESC, id DESC LIMIT ?", [*params, max(1, min(20000, limit))])
        return [self._row(r, True) for r in rows]

    def update(self, ticket_id: int, status: Optional[str] = None, note: Optional[str] = None, reply: Optional[str] = None) -> bool:
        sets, params = ["updated_at=?"], [now()]
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if note is not None:
            sets.append("note=?")
            params.append(note)
        if reply is not None:  # ответ студенту; пустой — снимает ответ
            sets.append("reply=?")
            params.append(reply.strip() or None)
            sets.append("reply_at=?")
            params.append(now() if reply.strip() else None)
        return self.db.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id=?", [*params, ticket_id]).rowcount > 0

    def purge_older_than(self, days: float) -> list[int]:
        """Удаляет старые обращения, возвращает их номера (чтобы стереть вложения)."""
        cutoff = now() - days * 86400
        ids = [r["id"] for r in self.db.query("SELECT id FROM tickets WHERE created_at<?", (cutoff,))]
        self.db.execute("DELETE FROM tickets WHERE created_at<?", (cutoff,))
        return ids

    def topics(self) -> list[str]:
        return [r["topic"] for r in self.db.query("SELECT DISTINCT topic FROM tickets WHERE topic IS NOT NULL ORDER BY topic")]
