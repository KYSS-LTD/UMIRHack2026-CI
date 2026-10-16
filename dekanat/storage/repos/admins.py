"""Администраторы кабинета и их сессии входа."""

from __future__ import annotations

import secrets
import sqlite3
from typing import Optional

from ..db import Database, iso, now
from .common import sha256_hex


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
