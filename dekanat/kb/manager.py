"""Управление документами базы знаний: загрузка, замена, отключение, удаление, переиндексация и история изменений.

Источник правды — хранилище (SQLite). После любого изменения индекс пересобирается сразу, поэтому удалённые, отключённые и
заменённые пункты никогда не выдаются как действующие правила.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models import Clause
from .documents import BASE_ID, KnowledgeBaseError, doc_clauses, new_doc_id, read_upload
from .parser import parse_regulation_text, read_regulation_file

log = logging.getLogger(__name__)


NO_NUMBERING = ("В файле не нашлось нумерации пунктов («3.4.», «п. 3.4») — абзацы пронумерованы подряд (§1, §2…), "
                "и ссылки в ответах секретаря будут вида «§2». Чтобы ссылки были точными, пронумеруйте пункты в файле и загрузите его заново.")


def _with_notice(row: dict, clauses: list) -> dict:
    """Строка документа для ответа API; если нумерации пунктов в файле нет — с пояснением для сотрудника."""
    if clauses and all(str(c.number).startswith("§") for c in clauses):
        return {**row, "notice": NO_NUMBERING}
    return row


class KnowledgeBaseManager:
    def __init__(self, svc, storage, max_bytes: int):
        self.svc = svc
        self.storage = storage
        self.max_bytes = max_bytes

    # ------------------------------------------------------------------ чтение
    def _base_file_row(self) -> dict:
        n = len(self.svc._file_clauses)
        return {"id": BASE_ID, "name": self.svc._file_name or "Положение", "filename": self.svc._file_name, "kind": "base", "active": True,
                "version": 0, "clauses": n, "created_at": None, "updated_at": None, "created_by": None, "from_file": True}

    def list(self) -> list[dict]:
        rows = self.storage.kb.list()
        if not any(r["id"] == BASE_ID for r in rows):
            rows.insert(0, self._base_file_row())
        return rows

    def get(self, doc_id: str) -> Optional[dict]:
        if doc_id == BASE_ID and self.storage.kb.get(BASE_ID) is None:
            return self._base_file_row()
        return self.storage.kb.get(doc_id)

    def clauses(self, doc_id: str) -> list[dict]:
        row = self.storage.kb.get(doc_id, with_text=True)
        if row is None:
            if doc_id == BASE_ID:
                return [c.to_dict() for c in self.svc._file_clauses]
            raise KeyError(doc_id)
        parsed = parse_regulation_text(row["text"]) if doc_id == BASE_ID else doc_clauses(row["text"], doc_id, row["name"])
        return [c.to_dict() for c in parsed]

    def history(self, doc_id: str) -> list[dict]:
        return self.storage.kb.versions(doc_id)

    # ------------------------------------------------------------------ запись
    def _parse(self, data: bytes, filename: str) -> tuple[str, list[Clause]]:
        text = read_upload(data, filename, self.max_bytes)
        clauses = parse_regulation_text(text)
        if not clauses:
            raise KnowledgeBaseError("В файле не найдено ни одного пункта (проверьте, что текст не пустой и пункты пронумерованы, например «3.4. …»)")
        return text, clauses

    def _reindex(self) -> None:
        self.svc.reload_knowledge_base()

    def _ensure_base_row(self) -> None:
        """Прежде чем заменять основное Положение, фиксируем в истории исходный файл как версию 1."""
        if self.storage.kb.get(BASE_ID) is not None:
            return
        path = self.svc.settings.path("regulation.path", "data/regulations/polozhenie.md")
        text = read_regulation_file(path) if path.exists() else ""
        self.storage.kb.create(BASE_ID, self.svc._file_name or "Положение", self.svc._file_name, text, len(self.svc._file_clauses), "system", kind="base")

    def add(self, name: str, filename: str, data: bytes, actor: str) -> dict:
        name = (name or "").strip() or (filename.rsplit(".", 1)[0] if filename else "")
        if not name:
            raise KnowledgeBaseError("Укажите название документа")
        if len(name) > 120:
            raise KnowledgeBaseError("Название слишком длинное (не больше 120 символов)")
        if any(r["name"].strip().lower() == name.lower() for r in self.storage.kb.list()):
            raise KnowledgeBaseError(f"Документ «{name}» уже есть в базе знаний — дайте другое название или замените существующий")
        text, clauses = self._parse(data, filename)
        doc_id = new_doc_id()
        self.storage.kb.create(doc_id, name, filename, text, len(clauses), actor)
        try:
            self._reindex()
        except Exception:
            self.storage.kb.delete(doc_id)
            self._reindex()
            raise
        self.storage.audit.log(actor, "kb.create", "kb", doc_id, {"name": name, "filename": filename, "clauses": len(clauses)})
        return _with_notice(self.storage.kb.get(doc_id), clauses)

    def replace(self, doc_id: str, filename: str, data: bytes, actor: str) -> dict:
        if doc_id != BASE_ID and self.storage.kb.get(doc_id) is None:
            raise KeyError(doc_id)
        text, clauses = self._parse(data, filename)
        if doc_id == BASE_ID:
            self._ensure_base_row()
        version = self.storage.kb.new_version(doc_id, text, len(clauses), actor, "replaced", filename)
        self._reindex()
        self.storage.audit.log(actor, "kb.replace", "kb", doc_id, {"filename": filename, "clauses": len(clauses), "version": version})
        return _with_notice(self.storage.kb.get(doc_id), clauses)

    def update(self, doc_id: str, actor: str, name: Optional[str] = None, active: Optional[bool] = None) -> dict:
        row = self.storage.kb.get(doc_id)
        if row is None:
            if doc_id == BASE_ID and (active is not None or name):
                self._ensure_base_row()
                row = self.storage.kb.get(BASE_ID)
            else:
                raise KeyError(doc_id)
        changes = {}
        if name is not None:
            name = name.strip()
            if not name or len(name) > 120:
                raise KnowledgeBaseError("Название должно быть от 1 до 120 символов")
            if any(r["id"] != doc_id and r["name"].strip().lower() == name.lower() for r in self.storage.kb.list()):
                raise KnowledgeBaseError(f"Документ «{name}» уже есть в базе знаний")
            self.storage.kb.rename(doc_id, name)
            changes["name"] = name
        if active is not None:
            self.storage.kb.set_active(doc_id, active)
            changes["active"] = active
        self._reindex()
        self.storage.audit.log(actor, "kb.update", "kb", doc_id, changes)
        return self.storage.kb.get(doc_id)

    def delete(self, doc_id: str, actor: str) -> str:
        """Обычный документ удаляется; у основного Положения удаляется только замена — возвращается файл из настроек."""
        row = self.storage.kb.get(doc_id)
        if row is None:
            raise KeyError(doc_id)
        self.storage.kb.delete(doc_id)
        self._reindex()
        action = "kb.reset_base" if doc_id == BASE_ID else "kb.delete"
        self.storage.audit.log(actor, action, "kb", doc_id, {"name": row["name"]})
        return action

    def reindex(self, actor: str) -> dict:
        self._reindex()
        info = {"clauses": len(self.svc.clauses), "documents": len([r for r in self.list() if r["active"]])}
        self.storage.audit.log(actor, "kb.reindex", "kb", "", info)
        return info
