"""Сессии пользователей без аккаунта, их состояние и документ, готовые файлы."""

from __future__ import annotations

import secrets
from typing import Optional

from ..db import Database, iso, now
from .common import _j, _loads


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
