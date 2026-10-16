"""Аккаунты (вход через Telegram): пользователи, запросы входа на сайте, напоминания."""

from __future__ import annotations

import re
import secrets
import sqlite3
from typing import Optional

from ..db import Database, iso, now
from .common import _j, _loads, sha256_hex


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
