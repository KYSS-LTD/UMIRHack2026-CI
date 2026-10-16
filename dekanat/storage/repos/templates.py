"""DOCX-шаблоны и их версии."""

from __future__ import annotations

import sqlite3
from typing import Optional

from ..db import Database, iso, now
from .common import _j, _loads


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
