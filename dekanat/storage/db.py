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
            self._add_missing_columns("tickets", {"public_hash": "TEXT", "reply": "TEXT", "reply_at": "REAL"})
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
