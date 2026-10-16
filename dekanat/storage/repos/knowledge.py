"""База знаний: документы с регламентами и их версии."""

from __future__ import annotations

import sqlite3
from typing import Optional

from ..db import Database, iso, now
from .common import sha256_hex


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
