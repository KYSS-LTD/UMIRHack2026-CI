"""Репозитории хранилища: администраторы, сессии пользователей, шаблоны, база знаний, обращения, события, журнал изменений."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from typing import Any, Optional

from .db import Database, iso, now


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def sha256_hex(data: "bytes | str") -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def session_ref(sid: str) -> str:
    """Обезличенный идентификатор сессии для статистики и обращений: по нему нельзя получить сам cookie."""
    return sha256_hex("dekanat-session:" + sid)[:16]


# ====================================================================== администраторы
class AdminRepo:
    def __init__(self, db: Database):
        self.db = db

    def count(self) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM admins WHERE disabled=0")["n"])

    def create(self, username: str, pw_hash: str, role: str = "admin") -> int:
        try:
            cur = self.db.execute("INSERT INTO admins(username, pw_hash, role, created_at) VALUES(?,?,?,?)", (username, pw_hash, role, now()))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Администратор «{username}» уже существует") from exc
        return int(cur.lastrowid)

    def get_by_username(self, username: str) -> Optional[dict]:
        row = self.db.one("SELECT * FROM admins WHERE username=?", (username,))
        return dict(row) if row else None

    def list(self) -> list[dict]:
        return [{"id": r["id"], "username": r["username"], "role": r["role"], "created_at": iso(r["created_at"]), "last_login": iso(r["last_login"]), "disabled": bool(r["disabled"])}
                for r in self.db.query("SELECT * FROM admins ORDER BY id")]

    def set_password(self, admin_id: int, pw_hash: str) -> None:
        self.db.execute("UPDATE admins SET pw_hash=? WHERE id=?", (pw_hash, admin_id))

    def touch_login(self, admin_id: int) -> None:
        self.db.execute("UPDATE admins SET last_login=? WHERE id=?", (now(), admin_id))

    def create_session(self, admin_id: int, ttl_seconds: float) -> str:
        token = secrets.token_urlsafe(32)
        t = now()
        self.db.execute("INSERT INTO admin_sessions(token_hash, admin_id, created_at, expires_at) VALUES(?,?,?,?)", (sha256_hex(token), admin_id, t, t + ttl_seconds))
        return token

    def get_session(self, token: str) -> Optional[dict]:
        if not token:
            return None
        row = self.db.one(
            "SELECT a.id, a.username, a.role, s.expires_at FROM admin_sessions s JOIN admins a ON a.id=s.admin_id "
            "WHERE s.token_hash=? AND s.expires_at>? AND a.disabled=0", (sha256_hex(token), now()))
        return dict(row) if row else None

    def delete_session(self, token: str) -> None:
        self.db.execute("DELETE FROM admin_sessions WHERE token_hash=?", (sha256_hex(token),))

    def delete_sessions_of(self, admin_id: int, except_token: Optional[str] = None) -> None:
        if except_token:
            self.db.execute("DELETE FROM admin_sessions WHERE admin_id=? AND token_hash<>?", (admin_id, sha256_hex(except_token)))
        else:
            self.db.execute("DELETE FROM admin_sessions WHERE admin_id=?", (admin_id,))

    def purge_expired(self) -> int:
        return self.db.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (now(),)).rowcount


# ====================================================================== сессии пользователей без аккаунта
class UserSessionRepo:
    def __init__(self, db: Database):
        self.db = db

    def create(self, ttl_seconds: float, user_id: Optional[int] = None, kind: str = "web") -> str:
        sid = secrets.token_urlsafe(24)
        t = now()
        self.db.execute("INSERT INTO user_sessions(id, created_at, last_seen, expires_at, user_id, kind) VALUES(?,?,?,?,?,?)", (sid, t, t, t + ttl_seconds, user_id, kind))
        return sid

    def touch(self, sid: str, ttl_seconds: float, user_ttl_seconds: Optional[float] = None) -> bool:
        """Продлевает живую сессию (срок только растёт: короткая проверка не сокращает долгую сессию аккаунта). False — сессии нет или она истекла.
        user_ttl_seconds — срок для сессии, привязанной к аккаунту (вход через Telegram); без него действует ttl_seconds."""
        if not sid:
            return False
        t = now()
        longer = user_ttl_seconds if user_ttl_seconds is not None else ttl_seconds
        cur = self.db.execute(
            "UPDATE user_sessions SET last_seen=?, expires_at=MAX(expires_at, ? + CASE WHEN user_id IS NULL THEN ? ELSE ? END) WHERE id=? AND expires_at>?",
            (t, t, ttl_seconds, longer, sid, t))
        return cur.rowcount > 0

    def get(self, sid: str) -> Optional[dict]:
        row = self.db.one("SELECT id, user_id, kind, expires_at FROM user_sessions WHERE id=? AND expires_at>?", (sid, now())) if sid else None
        return dict(row) if row else None

    def attach_user(self, sid: str, user_id: int) -> None:
        self.db.execute("UPDATE user_sessions SET user_id=? WHERE id=?", (user_id, sid))

    def delete_for_user(self, user_id: int) -> list[str]:
        """Все сессии аккаунта (входы на сайте) удаляются; возвращает пути их готовых документов."""
        paths: list[str] = []
        for r in self.db.query("SELECT id FROM user_sessions WHERE user_id=?", (user_id,)):
            paths += self.delete(r["id"])
        return paths

    def has_data(self, sid: str) -> bool:
        return any(self.db.one(f"SELECT 1 FROM {t} WHERE session_id=?", (sid,)) for t in ("dialog_states", "session_documents", "generated_docs"))

    def move_data(self, src: str, dst: str) -> dict:
        """Данные анонимной сессии переходят в аккаунт: анкета и карточка документа — если у аккаунта своих ещё нет, готовые файлы — всегда."""
        moved = {"dialog": False, "document": False, "files": 0}
        if not src or src == dst:
            return moved
        with self.db.transaction() as conn:
            for table, key in (("dialog_states", "dialog"), ("session_documents", "document")):
                if conn.execute(f"SELECT 1 FROM {table} WHERE session_id=?", (dst,)).fetchone() is None:
                    moved[key] = conn.execute(f"UPDATE {table} SET session_id=? WHERE session_id=?", (dst, src)).rowcount > 0
            moved["files"] = conn.execute("UPDATE generated_docs SET session_id=? WHERE session_id=?", (dst, src)).rowcount
        return moved

    def delete(self, sid: str) -> list[str]:
        """Удаляет сессию со всеми данными. Возвращает пути файлов готовых документов, которые нужно стереть с диска."""
        paths = [r["path"] for r in self.db.query("SELECT path FROM generated_docs WHERE session_id=?", (sid,))]
        self.db.execute("DELETE FROM user_sessions WHERE id=?", (sid,))
        return paths

    def save_state(self, sid: str, state: dict) -> None:
        self.db.execute("INSERT INTO dialog_states(session_id, state_json, updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(session_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at", (sid, _j(state), now()))

    def load_state(self, sid: str) -> Optional[dict]:
        row = self.db.one("SELECT state_json FROM dialog_states WHERE session_id=?", (sid,))
        return _loads(row["state_json"]) if row else None

    def save_document(self, sid: str, data: dict) -> None:
        self.db.execute("INSERT INTO session_documents(session_id, data_json, updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(session_id) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at", (sid, _j(data), now()))

    def load_document(self, sid: str) -> Optional[dict]:
        row = self.db.one("SELECT data_json FROM session_documents WHERE session_id=?", (sid,))
        return _loads(row["data_json"]) if row else None

    def delete_document(self, sid: str) -> None:
        self.db.execute("DELETE FROM session_documents WHERE session_id=?", (sid,))

    def delete_state(self, sid: str) -> None:
        self.db.execute("DELETE FROM dialog_states WHERE session_id=?", (sid,))

    def purge_expired(self) -> tuple[int, list[str]]:
        t = now()
        paths = [r["path"] for r in self.db.query(
            "SELECT g.path FROM generated_docs g JOIN user_sessions s ON s.id=g.session_id WHERE s.expires_at<=?", (t,))]
        n = self.db.execute("DELETE FROM user_sessions WHERE expires_at<=?", (t,)).rowcount
        return n, paths

    def count_active(self) -> int:
        """Живые сессии посетителей: общие данные аккаунтов («account») не считаются — это не открытая вкладка."""
        return int(self.db.one("SELECT COUNT(*) AS n FROM user_sessions WHERE expires_at>? AND kind='web'", (now(),))["n"])


class GeneratedRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, doc_id: str, sid: str, template_id: Optional[str], filename: str, path: str, ttl_seconds: float, unfilled: list[str]) -> None:
        t = now()
        self.db.execute("INSERT INTO generated_docs(id, session_id, template_id, filename, path, created_at, expires_at, unfilled_json) VALUES(?,?,?,?,?,?,?,?)",
                        (doc_id, sid, template_id, filename, path, t, t + ttl_seconds, _j(unfilled)))

    def get(self, doc_id: str, sid: str) -> Optional[dict]:
        """Документ отдаётся только владельцу сессии и пока не истёк срок хранения."""
        row = self.db.one("SELECT * FROM generated_docs WHERE id=? AND session_id=? AND expires_at>?", (doc_id, sid, now()))
        return dict(row) if row else None

    def list_for_session(self, sid: str) -> list[dict]:
        rows = self.db.query("SELECT id, template_id, filename, created_at, expires_at, unfilled_json FROM generated_docs WHERE session_id=? AND expires_at>? ORDER BY created_at DESC", (sid, now()))
        return [{"id": r["id"], "template_id": r["template_id"], "filename": r["filename"], "created_at": iso(r["created_at"]),
                 "expires_at": iso(r["expires_at"]), "unfilled": _loads(r["unfilled_json"], [])} for r in rows]

    def purge_expired(self) -> list[str]:
        t = now()
        paths = [r["path"] for r in self.db.query("SELECT path FROM generated_docs WHERE expires_at<=?", (t,))]
        self.db.execute("DELETE FROM generated_docs WHERE expires_at<=?", (t,))
        return paths

    def delete_for_session(self, sid: str) -> list[str]:
        paths = [r["path"] for r in self.db.query("SELECT path FROM generated_docs WHERE session_id=?", (sid,))]
        self.db.execute("DELETE FROM generated_docs WHERE session_id=?", (sid,))
        return paths


# ====================================================================== DOCX-шаблоны
class TemplateRepo:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "name": r["name"], "original_name": r["original_name"], "version": r["current_version"],
                "placeholders": _loads(r["placeholders_json"], []), "created_at": iso(r["created_at"]), "updated_at": iso(r["updated_at"]),
                "created_by": r["created_by"]}

    def get(self, template_id: str) -> Optional[dict]:
        r = self.db.one("SELECT * FROM templates WHERE id=?", (template_id,))
        if not r:
            return None
        out = self._row(r)
        v = self.db.one("SELECT path, sha256, size FROM template_versions WHERE template_id=? AND version=?", (template_id, r["current_version"]))
        out["path"], out["sha256"], out["size"] = (v["path"], v["sha256"], v["size"]) if v else (None, None, None)
        return out

    def list(self) -> list[dict]:
        return [self._row(r) for r in self.db.query("SELECT * FROM templates ORDER BY name COLLATE NOCASE, id")]

    def add_version(self, template_id: str, name: str, original_name: str, path: str, sha: str, size: int, placeholders: list[dict], actor: str, note: str = "") -> int:
        """Первая версия создаёт шаблон, следующие — заменяют текущую (прежние остаются в истории)."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT current_version FROM templates WHERE id=?", (template_id,)).fetchone()
            t = now()
            if row is None:
                version = 1
                conn.execute("INSERT INTO templates(id, name, original_name, current_version, placeholders_json, created_at, updated_at, created_by) VALUES(?,?,?,?,?,?,?,?)",
                             (template_id, name, original_name, version, _j(placeholders), t, t, actor))
            else:
                version = int(row["current_version"]) + 1
                conn.execute("UPDATE templates SET name=?, original_name=?, current_version=?, placeholders_json=?, updated_at=? WHERE id=?",
                             (name, original_name, version, _j(placeholders), t, template_id))
            conn.execute("INSERT INTO template_versions(template_id, version, path, sha256, size, placeholders_json, created_at, created_by, note) VALUES(?,?,?,?,?,?,?,?,?)",
                         (template_id, version, path, sha, size, _j(placeholders), t, actor, note))
        return version

    def versions(self, template_id: str) -> list[dict]:
        return [{"version": r["version"], "sha256": r["sha256"], "size": r["size"], "created_at": iso(r["created_at"]), "created_by": r["created_by"],
                 "note": r["note"], "placeholders": [p["key"] for p in _loads(r["placeholders_json"], [])]}
                for r in self.db.query("SELECT * FROM template_versions WHERE template_id=? ORDER BY version DESC", (template_id,))]

    def delete(self, template_id: str) -> list[str]:
        paths = [r["path"] for r in self.db.query("SELECT path FROM template_versions WHERE template_id=?", (template_id,))]
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM template_versions WHERE template_id=?", (template_id,))
            conn.execute("DELETE FROM templates WHERE id=?", (template_id,))
        return paths

    def rename(self, template_id: str, name: str) -> None:
        self.db.execute("UPDATE templates SET name=?, updated_at=? WHERE id=?", (name, now(), template_id))


# ====================================================================== база знаний (документы с регламентами)
class KBRepo:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "name": r["name"], "filename": r["filename"], "kind": r["kind"], "active": bool(r["active"]),
                "version": r["current_version"], "clauses": r["clause_count"], "created_at": iso(r["created_at"]),
                "updated_at": iso(r["updated_at"]), "created_by": r["created_by"]}

    def list(self) -> list[dict]:
        return [self._row(r) for r in self.db.query("SELECT * FROM kb_documents ORDER BY (kind='base') DESC, created_at, id")]

    def get(self, doc_id: str, with_text: bool = False) -> Optional[dict]:
        r = self.db.one("SELECT * FROM kb_documents WHERE id=?", (doc_id,))
        if not r:
            return None
        out = self._row(r)
        if with_text:
            v = self.db.one("SELECT text, sha256 FROM kb_versions WHERE doc_id=? AND version=?", (doc_id, r["current_version"]))
            out["text"], out["sha256"] = (v["text"], v["sha256"]) if v else ("", "")
        return out

    def create(self, doc_id: str, name: str, filename: str, text: str, clauses: int, actor: str, kind: str = "uploaded") -> None:
        t = now()
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO kb_documents(id, name, filename, kind, active, current_version, clause_count, created_at, updated_at, created_by) VALUES(?,?,?,?,1,1,?,?,?,?)",
                         (doc_id, name, filename, kind, clauses, t, t, actor))
            conn.execute("INSERT INTO kb_versions(doc_id, version, text, sha256, clause_count, created_at, created_by, action) VALUES(?,?,?,?,?,?,?,?)",
                         (doc_id, 1, text, sha256_hex(text), clauses, t, actor, "created"))

    def new_version(self, doc_id: str, text: str, clauses: int, actor: str, action: str = "replaced", filename: Optional[str] = None) -> int:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT current_version FROM kb_documents WHERE id=?", (doc_id,)).fetchone()
            if row is None:
                raise KeyError(doc_id)
            version = int(row["current_version"]) + 1
            t = now()
            conn.execute("INSERT INTO kb_versions(doc_id, version, text, sha256, clause_count, created_at, created_by, action) VALUES(?,?,?,?,?,?,?,?)",
                         (doc_id, version, text, sha256_hex(text), clauses, t, actor, action))
            conn.execute("UPDATE kb_documents SET current_version=?, clause_count=?, updated_at=?, filename=COALESCE(?, filename) WHERE id=?",
                         (version, clauses, t, filename, doc_id))
        return version

    def set_active(self, doc_id: str, active: bool) -> None:
        self.db.execute("UPDATE kb_documents SET active=?, updated_at=? WHERE id=?", (1 if active else 0, now(), doc_id))

    def rename(self, doc_id: str, name: str) -> None:
        self.db.execute("UPDATE kb_documents SET name=?, updated_at=? WHERE id=?", (name, now(), doc_id))

    def delete(self, doc_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM kb_versions WHERE doc_id=?", (doc_id,))
            conn.execute("DELETE FROM kb_documents WHERE id=?", (doc_id,))

    def active_documents(self) -> list[dict]:
        out = []
        for r in self.db.query("SELECT d.*, v.text AS text FROM kb_documents d JOIN kb_versions v ON v.doc_id=d.id AND v.version=d.current_version "
                               "WHERE d.active=1 ORDER BY (d.kind='base') DESC, d.created_at, d.id"):
            item = self._row(r)
            item["text"] = r["text"]
            out.append(item)
        return out

    def versions(self, doc_id: str) -> list[dict]:
        return [{"version": r["version"], "sha256": r["sha256"], "clauses": r["clause_count"], "created_at": iso(r["created_at"]),
                 "created_by": r["created_by"], "action": r["action"]}
                for r in self.db.query("SELECT doc_id, version, sha256, clause_count, created_at, created_by, action FROM kb_versions WHERE doc_id=? ORDER BY version DESC", (doc_id,))]

    def version_text(self, doc_id: str, version: int) -> Optional[str]:
        r = self.db.one("SELECT text FROM kb_versions WHERE doc_id=? AND version=?", (doc_id, version))
        return r["text"] if r else None


# ====================================================================== обращения в деканат
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


# ====================================================================== события и статистика
class EventRepo:
    def __init__(self, db: Database):
        self.db = db

    def log(self, scenario: str, category: str = "", status: str = "", session: str = "", flags: Optional[dict] = None) -> None:
        """Только обезличенное: время, хэш сессии, тема, сценарий, статус. Ни текста вопросов, ни OCR, ни персональных данных."""
        self.db.execute("INSERT INTO events(ts, session_ref, category, scenario, status, flags) VALUES(?,?,?,?,?,?)",
                        (now(), session_ref(session) if session else None, category or None, scenario, status or None, _j(flags) if flags else None))

    def purge_older_than(self, days: float) -> int:
        return self.db.execute("DELETE FROM events WHERE ts<?", (now() - days * 86400,)).rowcount

    def stats(self, days: int = 30) -> dict:
        since = now() - days * 86400
        day_expr = "strftime('%Y-%m-%d', ts, 'unixepoch')"

        def count(scenario: str, status: Optional[str] = None) -> int:
            if status:
                return int(self.db.one("SELECT COUNT(*) AS n FROM events WHERE ts>=? AND scenario=? AND status=?", (since, scenario, status))["n"])
            return int(self.db.one("SELECT COUNT(*) AS n FROM events WHERE ts>=? AND scenario=?", (since, scenario))["n"])

        by_day: dict[str, dict] = {}
        for r in self.db.query(f"SELECT {day_expr} AS day, scenario, COUNT(*) AS n FROM events WHERE ts>=? GROUP BY day, scenario", (since,)):
            by_day.setdefault(r["day"], {"day": r["day"]})[r["scenario"]] = r["n"]
        topics = [{"topic": r["category"], "count": r["n"]} for r in self.db.query(
            "SELECT category, COUNT(*) AS n FROM events WHERE ts>=? AND category IS NOT NULL AND scenario IN ('question','document_check') "
            "GROUP BY category ORDER BY n DESC, category LIMIT 20", (since,))]
        statuses = {r["status"]: r["n"] for r in self.db.query(
            "SELECT status, COUNT(*) AS n FROM events WHERE ts>=? AND scenario IN ('question','document_check') AND status IS NOT NULL GROUP BY status", (since,))}
        unique_sessions = int(self.db.one("SELECT COUNT(DISTINCT session_ref) AS n FROM events WHERE ts>=? AND session_ref IS NOT NULL", (since,))["n"])
        return {
            "period_days": days,
            "totals": {
                "visits": count("visit"),
                "sessions": unique_sessions,
                "questions": count("question"),
                "document_checks": count("document_check"),
                "screenings_started": count("screening_started"),
                "screenings_transfer": count("screening_started", "transfer"),
                "screenings_enrollment": count("screening_started", "enrollment"),
                "documents_generated": count("document_generated"),
                "tickets_sent": count("ticket", "sent"),
                "tickets_declined": count("ticket", "declined"),
            },
            "topics": topics,
            "statuses": statuses,
            "by_day": [by_day[d] for d in sorted(by_day)],
        }


# ====================================================================== журнал изменений
class AuditRepo:
    def __init__(self, db: Database):
        self.db = db

    def log(self, actor: str, action: str, object_type: str, object_id: str = "", details: Optional[dict] = None) -> None:
        self.db.execute("INSERT INTO audit_log(ts, actor, action, object_type, object_id, details_json) VALUES(?,?,?,?,?,?)",
                        (now(), actor, action, object_type, object_id, _j(details) if details else None))

    def list(self, object_type: Optional[str] = None, object_id: Optional[str] = None, limit: int = 100, offset: int = 0) -> list[dict]:
        where, params = ["1=1"], []
        if object_type:
            where.append("object_type=?")
            params.append(object_type)
        if object_id:
            where.append("object_id=?")
            params.append(object_id)
        rows = self.db.query(f"SELECT * FROM audit_log WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?", [*params, max(1, min(500, limit)), max(0, offset)])
        return [{"id": r["id"], "ts": iso(r["ts"]), "actor": r["actor"], "action": r["action"], "object_type": r["object_type"],
                 "object_id": r["object_id"], "details": _loads(r["details_json"], {})} for r in rows]


# ====================================================================== аккаунты (вход через Telegram)
class UserRepo:
    """Необязательные аккаунты: только Telegram-идентификатор и имя. Пароля нет, телефона и почты не храним."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "tg_id": r["tg_id"], "username": r["tg_username"], "first_name": r["first_name"], "last_name": r["last_name"], "language": r["language"],
                "data_sid": r["data_sid"], "notify": bool(r["notify"]), "created_at": iso(r["created_at"]), "last_seen": iso(r["last_seen"]),
                "web_linked_at": iso(r["web_linked_at"])}

    def upsert_tg(self, tg_id: int, username: Optional[str] = None, first_name: Optional[str] = None, last_name: Optional[str] = None,
                  language: Optional[str] = None) -> tuple[dict, bool]:
        """Создаёт аккаунт по Telegram-идентификатору или обновляет имя. Возвращает (аккаунт, создан ли сейчас)."""
        t = now()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM users WHERE tg_id=?", (int(tg_id),)).fetchone()
            if row is None:
                cur = conn.execute("INSERT INTO users(tg_id, tg_username, first_name, last_name, language, created_at, last_seen) VALUES(?,?,?,?,?,?,?)",
                                   (int(tg_id), username, first_name, last_name, language, t, t))
                row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
                return self._row(row), True
            conn.execute("UPDATE users SET tg_username=?, first_name=?, last_name=?, language=COALESCE(?, language), last_seen=? WHERE id=?",
                         (username, first_name, last_name, language, t, row["id"]))
            row = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
        return self._row(row), False

    def get(self, user_id: int) -> Optional[dict]:
        r = self.db.one("SELECT * FROM users WHERE id=?", (user_id,))
        return self._row(r) if r else None

    def get_by_tg(self, tg_id: int) -> Optional[dict]:
        r = self.db.one("SELECT * FROM users WHERE tg_id=?", (int(tg_id),))
        return self._row(r) if r else None

    def by_data_sid(self, sid: str) -> Optional[dict]:
        r = self.db.one("SELECT * FROM users WHERE data_sid=?", (sid,)) if sid else None
        return self._row(r) if r else None

    def touch(self, user_id: int) -> None:
        self.db.execute("UPDATE users SET last_seen=? WHERE id=?", (now(), user_id))

    def mark_web_linked(self, user_id: int) -> None:
        self.db.execute("UPDATE users SET web_linked_at=?, last_seen=? WHERE id=?", (now(), now(), user_id))

    def set_data_sid(self, user_id: int, sid: Optional[str]) -> None:
        self.db.execute("UPDATE users SET data_sid=? WHERE id=?", (sid, user_id))

    def set_notify(self, user_id: int, on: bool) -> None:
        self.db.execute("UPDATE users SET notify=? WHERE id=?", (1 if on else 0, user_id))

    def bot_state(self, user_id: int) -> dict:
        r = self.db.one("SELECT bot_state_json FROM users WHERE id=?", (user_id,))
        state = _loads(r["bot_state_json"], {}) if r else {}
        return state if isinstance(state, dict) else {}

    def save_bot_state(self, user_id: int, state: dict) -> None:
        self.db.execute("UPDATE users SET bot_state_json=? WHERE id=?", (_j(state), user_id))

    def delete(self, user_id: int) -> None:
        self.db.execute("DELETE FROM notifications WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM users WHERE id=?", (user_id,))

    def count(self) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM users")["n"])

    def count_web_linked(self) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM users WHERE web_linked_at IS NOT NULL")["n"])

    def inactive_since(self, before: float) -> list[dict]:
        return [self._row(r) for r in self.db.query("SELECT * FROM users WHERE last_seen<? ORDER BY last_seen LIMIT 500", (before,))]


class LoginRepo:
    """Запросы входа через бота: сайт создаёт запрос, человек подтверждает его в Telegram, сайт получает вход. В базе — только хэш токена."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, session_id: str, ttl_seconds: float, device: str = "") -> str:
        token = secrets.token_urlsafe(32)
        t = now()
        self.db.execute("INSERT INTO login_requests(token_hash, session_id, created_at, expires_at, device) VALUES(?,?,?,?,?)",
                        (sha256_hex(token), session_id, t, t + ttl_seconds, device[:160]))
        return token

    def pending_count(self, session_id: str) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM login_requests WHERE session_id=? AND state='pending' AND expires_at>?", (session_id, now()))["n"])

    def get(self, token: str) -> Optional[dict]:
        r = self.db.one("SELECT * FROM login_requests WHERE token_hash=?", (sha256_hex(token),)) if token else None
        return self._row(r) if r else None

    def find_by_prefix(self, prefix: str) -> Optional[dict]:
        """Бот получает из кнопки только начало хэша: по нему находится ожидающий запрос (однозначно, если начало достаточно длинное)."""
        if len(prefix) < 12 or not re.fullmatch(r"[0-9a-f]+", prefix):
            return None
        rows = self.db.query("SELECT * FROM login_requests WHERE token_hash LIKE ? AND expires_at>? LIMIT 2", (prefix + "%", now()))
        return self._row(rows[0]) if len(rows) == 1 else None

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        return {"hash": r["token_hash"], "session_id": r["session_id"], "created_at": r["created_at"], "expires_at": r["expires_at"], "state": r["state"],
                "tg_id": r["tg_id"], "profile": _loads(r["profile_json"], {}), "device": r["device"] or ""}

    def confirm(self, token_hash: str, tg_id: int, profile: dict) -> bool:
        """Подтверждает только ожидающий и непросроченный запрос; повторное подтверждение ничего не меняет."""
        return self.db.execute("UPDATE login_requests SET state='confirmed', tg_id=?, profile_json=? WHERE token_hash=? AND state='pending' AND expires_at>?",
                               (int(tg_id), _j(profile), token_hash, now())).rowcount > 0

    def deny(self, token_hash: str) -> bool:
        return self.db.execute("UPDATE login_requests SET state='denied' WHERE token_hash=? AND state='pending'", (token_hash,)).rowcount > 0

    def consume(self, token_hash: str) -> bool:
        """Вход выдаётся один раз: после выдачи запрос закрыт."""
        return self.db.execute("UPDATE login_requests SET state='used' WHERE token_hash=? AND state='confirmed'", (token_hash,)).rowcount > 0

    def purge_expired(self) -> int:
        return self.db.execute("DELETE FROM login_requests WHERE expires_at<?", (now() - 3600,)).rowcount


class NotificationRepo:
    """Очередь сообщений в Telegram: ответы деканата, напоминания, сообщения сотрудникам. Отправка с повторами, пока не получится."""

    MAX_ATTEMPTS = 5

    def __init__(self, db: Database):
        self.db = db

    def add(self, chat_id: int, kind: str, text: str, user_id: Optional[int] = None, send_after: Optional[float] = None, dedupe_key: str = "") -> Optional[int]:
        """Ставит сообщение в очередь. dedupe_key: то же событие не ставится дважды, пока предыдущее не отправлено."""
        if dedupe_key and self.db.one("SELECT 1 FROM notifications WHERE dedupe_key=? AND sent_at IS NULL AND attempts<?", (dedupe_key, self.MAX_ATTEMPTS)):
            return None
        t = now()
        cur = self.db.execute("INSERT INTO notifications(chat_id, user_id, kind, text, created_at, send_after, dedupe_key) VALUES(?,?,?,?,?,?,?)",
                              (int(chat_id), user_id, kind, text[:3900], t, send_after if send_after is not None else t, dedupe_key or None))
        return int(cur.lastrowid)

    def due(self, limit: int = 20) -> list[dict]:
        rows = self.db.query("SELECT * FROM notifications WHERE sent_at IS NULL AND attempts<? AND send_after<=? ORDER BY send_after, id LIMIT ?", (self.MAX_ATTEMPTS, now(), limit))
        return [dict(r) for r in rows]

    def mark_sent(self, nid: int) -> None:
        self.db.execute("UPDATE notifications SET sent_at=?, last_error=NULL WHERE id=?", (now(), nid))

    def mark_failed(self, nid: int, error: str, final: bool = False, retry_in: float = 60.0) -> None:
        """final: повторять бесполезно (бот заблокирован) — попытки исчерпываются сразу; иначе повтор через retry_in секунд."""
        if final:
            self.db.execute("UPDATE notifications SET attempts=?, last_error=? WHERE id=?", (self.MAX_ATTEMPTS, error[:200], nid))
        else:
            self.db.execute("UPDATE notifications SET attempts=attempts+1, last_error=?, send_after=? WHERE id=?", (error[:200], now() + retry_in, nid))

    def postpone(self, nid: int, seconds: float) -> None:
        """Telegram просит подождать (flood control): сообщение уходит позже, попытка не засчитывается."""
        self.db.execute("UPDATE notifications SET send_after=? WHERE id=?", (now() + seconds, nid))

    def pending_for_user(self, user_id: int, kind: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND sent_at IS NULL AND attempts<?"
        params: list = [user_id, self.MAX_ATTEMPTS]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        return int(self.db.one(sql, params)["n"])

    def cancel_for_user(self, user_id: int, kind: str) -> int:
        return self.db.execute("DELETE FROM notifications WHERE user_id=? AND kind=? AND sent_at IS NULL", (user_id, kind)).rowcount

    def purge_old(self, days: float = 30) -> int:
        return self.db.execute("DELETE FROM notifications WHERE created_at<? AND (sent_at IS NOT NULL OR attempts>=?)", (now() - days * 86400, self.MAX_ATTEMPTS)).rowcount
