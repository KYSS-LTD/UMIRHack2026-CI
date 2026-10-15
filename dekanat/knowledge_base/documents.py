"""Документы базы знаний: чтение загруженных файлов и превращение их в пункты с уникальными номерами.

Основное Положение (`data/regulations/polozhenie.md` или заменённое администратором) сохраняет свою обычную нумерацию («3.4»),
поэтому всё, что настроено на него, работает как раньше. Пункты дополнительных документов получают ключ «<id документа>:<номер>»
и подпись «п. 3.4 (Название документа)», чтобы одинаковые номера в разных документах не путались.
"""

from __future__ import annotations

import secrets
import tempfile
from pathlib import Path

from ..models import Clause
from .parser import parse_regulation_text, read_regulation_file

ALLOWED_SUFFIXES = (".txt", ".md", ".docx", ".pdf")
BASE_ID = "base"


class KnowledgeBaseError(ValueError):
    """Файл нельзя принять в базу знаний (понятное пользователю сообщение)."""


def new_doc_id() -> str:
    return "d" + secrets.token_hex(3)


def read_upload(data: bytes, filename: str, max_bytes: int) -> str:
    """Текст документа из загруженных байт. Файл никогда не выполняется: он только читается как текст/DOCX/PDF."""
    suffix = Path(filename or "").suffix.lower() or ".txt"
    if suffix not in ALLOWED_SUFFIXES:
        raise KnowledgeBaseError("Поддерживаются файлы .txt, .md, .docx и .pdf")
    if not data:
        raise KnowledgeBaseError("Файл пустой")
    if len(data) > max_bytes:
        raise KnowledgeBaseError(f"Файл больше {max_bytes // (1024 * 1024)} МБ")
    if suffix == ".docx" and not data.startswith(b"PK"):
        raise KnowledgeBaseError("Файл с расширением .docx не является документом Word")
    if suffix == ".pdf" and not data.startswith(b"%PDF"):
        raise KnowledgeBaseError("Файл с расширением .pdf не является PDF")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows может ещё держать файл после ошибки чтения: это не должно давать 500
        path = Path(tmp) / f"doc{suffix}"
        path.write_bytes(data)
        try:
            text = read_regulation_file(path)
        except Exception as exc:  # повреждённый DOCX/PDF
            raise KnowledgeBaseError(f"Не удалось прочитать файл: {exc}") from exc
    if not text.strip():
        raise KnowledgeBaseError("В файле нет текста (для сканов без текстового слоя нужен OCR — загрузите текстовую версию)")
    return text


def base_clauses(text: str) -> list[Clause]:
    return parse_regulation_text(text)


def doc_clauses(text: str, doc_id: str, doc_name: str) -> list[Clause]:
    """Пункты дополнительного документа с уникальными ключами и подписью документа."""
    out = []
    for c in parse_regulation_text(text):
        out.append(Clause(f"{doc_id}:{c.number}", c.text, list(c.section), c.numbered, doc_id=doc_id, doc_name=doc_name, display_number=c.number))
    return out
