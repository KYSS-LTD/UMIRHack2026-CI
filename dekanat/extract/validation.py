"""Этап «валидация»: между извлечением полей и поиском по Положению.

Для каждого поля карточки фиксируется статус обнаружения и, если значение под сомнением, причина сомнения.
Поле, которое OCR не смог прочитать, остаётся «не найдено» и не дополняется предположениями; найденное неуверенно
или найденное моделью получает пометку — его значение нужно сверить со снимком.
"""

from __future__ import annotations

from typing import Optional

from ..config import DocTypeSpec
from ..models import FieldResult

LOW_CONFIDENCE = 0.5
YEARS = (1950, 2100)  # правдоподобный диапазон лет для дат в студенческих документах

NOT_FOUND_TEXT = "не найдено в тексте документа"
NOT_FOUND_MARK = "графический элемент не обнаружен на изображении"


def _date_doubt(normalized) -> str:
    years = []
    if isinstance(normalized, str):
        years = [normalized[:4]]
    elif isinstance(normalized, dict):
        years = [str(v)[:4] for v in normalized.values() if isinstance(v, str)]
    for y in years:
        if y.isdigit() and not YEARS[0] <= int(y) <= YEARS[1]:
            return "дата вне правдоподобного диапазона — проверьте по снимку"
    return ""


def doubt_reason(kind: str, found: bool, confidence: float, method: str = "", normalized=None) -> str:
    """Причина сомнения в значении поля или пустая строка, если значение подтверждено."""
    is_mark = kind in ("mark", "marks")
    if not found:
        return NOT_FOUND_MARK if is_mark else NOT_FOUND_TEXT
    if method == "llm":
        return "найдено языковой моделью по тексту документа — требует проверки"
    if confidence < LOW_CONFIDENCE:
        return "отметка определена неуверенно" if is_mark else f"текст распознан неуверенно (уверенность {round(confidence * 100)} %)"
    if method in ("header", "fallback"):
        return "значение найдено косвенно, без подписи поля в документе"
    if kind in ("date", "period"):
        return _date_doubt(normalized)
    return ""


def validate_fields(spec: Optional[DocTypeSpec], fields: list[FieldResult]) -> list[FieldResult]:
    """Проставляет каждому полю (и его частям) причину сомнения. Значения не меняет и ничего не выдумывает."""
    for f in fields:
        fspec = spec.field_by_id(f.id) if spec else None
        if f.parts:
            for p in f.parts:
                pspec = fspec.part(p.id) if fspec else None
                kind = pspec.kind if pspec else ("mark" if f.kind == "marks" else "")
                p.doubt = doubt_reason(kind, p.found, p.confidence, f.method if len(f.parts) == 1 else "", p.normalized)
            f.doubt = "; ".join(f"{p.title}: {p.doubt}" for p in f.parts if p.doubt)
        else:
            f.doubt = doubt_reason(f.kind, f.found, f.confidence, f.method, f.normalized)
    return fields
