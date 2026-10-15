"""Типы документов, которыми управляет администратор: проверка, добавление, изменение и удаление описаний (config/doc_types/*.yaml).

Одна реализация для двух входов: кабинет сотрудника (/api/admin/doctypes) и прежние адреса /api/doctypes/... — правила и тексты ошибок общие.
Описание принимается только целиком и только после разбора: сломанный файл не попадает на диск, а неудачная перезагрузка откатывается.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

from .config import ConfigError, DocTypeSpec, parse_doc_type_yaml

MAX_YAML_BYTES = 200 * 1024

NEW_TYPE_TEMPLATE = """# Новый тип документа. Опишите поля — код менять не нужно.
id: my_document            # латиница, строчные буквы, цифры и «_»
title: Название документа
short_title: Документ
genitive: документа        # «Не хватает ... документа»
aliases: [название документа]
classify:
  keywords:                # по этим фразам тип определяется автоматически
    - {phrase: название документа, weight: 6}
fields:
  - id: student_name
    title: ФИО студента
    kind: person           # person | org | text | date | number | choice | period | grade | group | course | composite | marks
    labels: [ФИО студента, ФИО, Студент]
    mention: [ФИО студента, ФИО]
    missing_phrase: ФИО студента
  - id: doc_date
    title: Дата
    kind: date
    labels: [Дата, Дата выдачи]
    missing_phrase: даты
"""


class DocTypeError(Exception):
    """Ошибка для человека: status — как её отдать по HTTP (400 — описание не принято, 404 — нет такого типа)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class DocTypeManager:
    def __init__(self, svc: Any, storage: Any):
        self.svc = svc
        self.storage = storage
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ чтение
    def _path(self, type_id: str) -> Path:
        spec = self.svc.doc_types.get(type_id)
        if spec and spec.source:
            return self.svc.settings.doc_types_dir / spec.source
        return self.svc.settings.doc_types_dir / f"{type_id}.yaml"

    def _describe(self, spec: DocTypeSpec) -> dict:
        return {
            "id": spec.id, "title": spec.title, "short_title": spec.short_title, "source": spec.source,
            "fields": len(spec.fields), "units": sum(len(f.parts) or 1 for f in spec.fields),
            "field_titles": [f.title for f in spec.fields],
            "deletable": len(self.svc.doc_types) > 1,
        }

    def list(self) -> list[dict]:
        return [self._describe(s) for s in self.svc.doc_types.values()]

    def yaml(self, type_id: str) -> str:
        path = self._path(type_id)
        if type_id not in self.svc.doc_types or not path.exists():
            raise DocTypeError("Тип документа не найден", 404)
        return path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ проверка
    @staticmethod
    def _clean(text: str) -> str:
        if len(text.encode("utf-8")) > MAX_YAML_BYTES:
            raise DocTypeError(f"Описание слишком большое (больше {MAX_YAML_BYTES // 1024} КБ) — это не похоже на описание типа документа")
        return text.lstrip("﻿").replace("\r\n", "\n")  # BOM и «виндовые» переводы строк из файла, сохранённого в Блокноте

    def validate(self, text: str) -> dict:
        """Разбор без сохранения: что за тип, сколько полей и не занят ли id. Ошибка описания — ok=False и понятное сообщение."""
        try:
            spec = parse_doc_type_yaml(self._clean(text), "описание")
        except (ConfigError, DocTypeError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": spec.id, "title": spec.title, "fields": len(spec.fields), "units": sum(len(f.parts) or 1 for f in spec.fields),
                "field_titles": [f.title for f in spec.fields], "exists": spec.id in self.svc.doc_types}

    # ------------------------------------------------------------------ запись
    def save(self, type_id: str, text: str, actor: str) -> dict:
        """type_id == "new" — добавить новый тип (занятый id не перезаписывается); иначе — заменить описание этого типа."""
        text = self._clean(text)
        try:
            spec = parse_doc_type_yaml(text, f"{type_id}.yaml")
        except ConfigError as exc:
            raise DocTypeError(str(exc)) from exc
        if type_id != "new" and spec.id != type_id:
            raise DocTypeError(f"id в YAML («{spec.id}») не совпадает с редактируемым типом («{type_id}»)")
        with self._lock:
            if type_id == "new" and spec.id in self.svc.doc_types:
                raise DocTypeError(f"Тип с id «{spec.id}» уже существует — измените id")
            path = self._path(spec.id)
            previous = path.read_text(encoding="utf-8") if path.exists() else None
            path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
            try:
                self.svc.reload()
            except Exception as exc:  # откатываем, чтобы не сломать сервис: возможно, поле не сходится с остальной конфигурацией
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(previous, encoding="utf-8")
                self.svc.reload()
                raise DocTypeError(f"Конфиг не принят: {exc}") from exc
        self.storage.audit.log(actor, "doctype.save", "doctype", spec.id, {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "created": previous is None})
        return {"ok": True, "id": spec.id, "title": spec.title, "created": previous is None, "fields": len(spec.fields)}

    def delete(self, type_id: str, actor: str) -> dict:
        with self._lock:
            if type_id not in self.svc.doc_types:
                raise DocTypeError("Тип документа не найден", 404)
            if len(self.svc.doc_types) <= 1:
                raise DocTypeError("Нельзя удалить последний тип документа")
            path = self._path(type_id)
            previous = path.read_text(encoding="utf-8") if path.exists() else None
            path.unlink(missing_ok=True)
            try:
                self.svc.reload()
            except Exception as exc:  # другие настройки ссылались на этот тип — возвращаем файл
                if previous is not None:
                    path.write_text(previous, encoding="utf-8")
                self.svc.reload()
                raise DocTypeError(f"Тип нельзя удалить: {exc}") from exc
        self.storage.audit.log(actor, "doctype.delete", "doctype", type_id)
        return {"ok": True}
