"""SQLite: тонкая обёртка с блокировкой и схемой. Только стандартная библиотека."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS admins(
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE, pw_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin', created_at REAL NOT NULL, last_login REAL, disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS admin_sessions(
    token_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL UNIQUE, tg_username TEXT, first_name TEXT, last_name TEXT, language TEXT,
    data_sid TEXT, notify INTEGER NOT NULL DEFAULT 1, bot_state_json TEXT, created_at REAL NOT NULL, last_seen REAL NOT NULL, web_linked_at REAL);
CREATE TABLE IF NOT EXISTS login_requests(
    token_hash TEXT PRIMARY KEY, session_id TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
    tg_id INTEGER, profile_json TEXT, device TEXT);
CREATE TABLE IF NOT EXISTS notifications(
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER, kind TEXT NOT NULL, text TEXT NOT NULL, created_at REAL NOT NULL,
    send_after REAL NOT NULL, sent_at REAL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, dedupe_key TEXT);
CREATE TABLE IF NOT EXISTS user_sessions(
    id TEXT PRIMARY KEY, created_at REAL NOT NULL, last_seen REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS dialog_states(
    session_id TEXT PRIMARY KEY REFERENCES user_sessions(id) ON DELETE CASCADE, state_json TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS session_documents(
    session_id TEXT PRIMARY KEY REFERENCES user_sessions(id) ON DELETE CASCADE, data_json TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS generated_docs(
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES user_sessions(id) ON DELETE CASCADE, template_id TEXT,
    filename TEXT NOT NULL, path TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, unfilled_json TEXT);
CREATE TABLE IF NOT EXISTS templates(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, original_name TEXT, current_version INTEGER NOT NULL, placeholders_json TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, created_by TEXT);
CREATE TABLE IF NOT EXISTS template_versions(
    template_id TEXT NOT NULL, version INTEGER NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
    placeholders_json TEXT, created_at REAL NOT NULL, created_by TEXT, note TEXT, PRIMARY KEY(template_id, version));
CREATE TABLE IF NOT EXISTS kb_documents(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, filename TEXT, kind TEXT NOT NULL DEFAULT 'uploaded', active INTEGER NOT NULL DEFAULT 1,
    current_version INTEGER NOT NULL, clause_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, created_by TEXT);
CREATE TABLE IF NOT EXISTS kb_versions(
    doc_id TEXT NOT NULL, version INTEGER NOT NULL, text TEXT NOT NULL, sha256 TEXT NOT NULL, clause_count INTEGER,
    created_at REAL NOT NULL, created_by TEXT, action TEXT, PRIMARY KEY(doc_id, version));
CREATE TABLE IF NOT EXISTS tickets(
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, updated_at REAL NOT NULL, session_ref TEXT NOT NULL,
    type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', topic TEXT, payload_json TEXT NOT NULL, channel TEXT, note TEXT,
    idem_key TEXT UNIQUE, public_hash TEXT, reply TEXT, reply_at REAL);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, session_ref TEXT, category TEXT, scenario TEXT, status TEXT, flags TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, object_type TEXT NOT NULL,
    object_id TEXT, details_json TEXT);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_tickets_created ON tickets(created_at);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS ix_sessions_exp ON user_sessions(expires_at);
CREATE INDEX IF NOT EXISTS ix_gen_exp ON generated_docs(expires_at);
CREATE INDEX IF NOT EXISTS ix_notif_due ON notifications(sent_at, send_after);
CREATE INDEX IF NOT EXISTS ix_login_exp ON login_requests(expires_at);
"""


def now() -> float:
    return time.time()


def iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: "Path | str"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            # базы, созданные до появления кода статуса и ответа студенту, дополняются столбцами
            self._add_missing_columns("tickets", {"public_hash": "TEXT", "reply": "TEXT", "reply_at": "REAL", "user_id": "INTEGER"})
            # сессия может принадлежать аккаунту (вход через Telegram); «account» — не cookie, а общие данные аккаунта для сайта и бота
            self._add_missing_columns("user_sessions", {"user_id": "INTEGER", "kind": "TEXT NOT NULL DEFAULT 'web'"})
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_user ON user_sessions(user_id)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_tickets_user ON tickets(user_id)")
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))

    def _add_missing_columns(self, table: str, columns: dict) -> None:
        have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def meta_get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        self.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
