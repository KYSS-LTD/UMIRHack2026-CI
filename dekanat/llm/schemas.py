"""Проверка ответов модели средствами Python: структура, типы, допустимые значения и «заземление» в исходных данных.

Принцип: модель только предлагает. Всё, что нельзя проверить по исходному тексту (номер пункта, значение поля, число в пояснении),
отбрасывается — и вызывающий код падает на детерминированную логику.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from rapidfuzz import fuzz


class SchemaError(ValueError):
    """Ответ модели не соответствует схеме или не подтверждён исходными данными."""


def norm(text: str) -> str:
    t = str(text or "").lower().replace("ё", "е")
    t = re.sub(r"[^\w\s./-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_json_object(raw: str) -> dict:
    """Достаёт JSON-объект из ответа модели (терпимо к ```-обёртке и лишнему тексту вокруг)."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise SchemaError("в ответе нет JSON-объекта") from None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError as exc:
            raise SchemaError(f"некорректный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaError("ожидался JSON-объект")
    return data


def _opt_str(value: Any, limit: int = 500) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaError("ожидалась строка или null")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", value).strip()
    if len(value) > limit:
        raise SchemaError(f"строка длиннее {limit} символов")
    return value or None


# ---------------------------------------------------------------- 1. арбитр по Положению
@dataclass
class Judgement:
    answerable: bool
    clause_number: Optional[str] = None


def _as_bool(value: Any) -> Optional[bool]:
    """true / false. Небольшие модели часто берут значение в кавычки ("true"): строка ровно «true» или «false» принимается, всё остальное («да», 1, «возможно») — нет."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def validate_judgement(data: dict, valid_numbers: Iterable[str]) -> Judgement:
    answerable = _as_bool(data.get("answerable"))
    if answerable is None:
        raise SchemaError("answerable должен быть true или false")
    number = data.get("clause")
    number = None if number is None else str(number).strip()
    valid = set(valid_numbers)
    if number is not None and number not in valid:
        number = None  # модель назвала несуществующий пункт — игнорируем, номер выдумать она не может
    return Judgement(answerable, number if answerable else None)


# ---------------------------------------------------------------- 2. намерение
INTENTS = ("transfer", "enrollment", "statement", "question", "other")
CONFIDENCE = ("high", "medium", "low")


@dataclass
class IntentResult:
    intent: str
    confidence: str


def validate_intent(data: dict) -> IntentResult:
    intent, conf = data.get("intent"), data.get("confidence", "low")
    if intent not in INTENTS:
        raise SchemaError(f"intent должен быть одним из {list(INTENTS)}")
    if conf not in CONFIDENCE:
        raise SchemaError(f"confidence должен быть одним из {list(CONFIDENCE)}")
    return IntentResult(intent, conf)


# ---------------------------------------------------------------- 3. нормализация ответа анкеты
@dataclass
class NormalizedAnswer:
    status: str  # clear | ambiguous | invalid
    value: Any = None
    details: Optional[str] = None
    clarification: Optional[str] = None


def validate_normalized(data: dict, kind: str, choices: Sequence[str] = ()) -> NormalizedAnswer:
    status = data.get("status")
    if status not in ("clear", "ambiguous", "invalid"):
        raise SchemaError("status должен быть clear, ambiguous или invalid")
    details = _opt_str(data.get("details"), 300)
    clarification = _opt_str(data.get("clarification"), 300)
    if status != "clear":
        return NormalizedAnswer(status, None, None, clarification)
    value = data.get("value")
    if kind == "bool":
        value = _as_bool(value)
        if value is None:
            raise SchemaError("value должен быть true или false")
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            else:
                raise SchemaError("value должен быть целым числом")
    elif kind == "enum":
        if value not in choices:
            raise SchemaError(f"value должен быть одним из {list(choices)}")
    else:
        value = _opt_str(value, 200)
        if value is None:
            raise SchemaError("value не должен быть пустым при status=clear")
    return NormalizedAnswer("clear", value, details, clarification)


def is_grounded_in(value: str, message: str) -> bool:
    """Значение из ответа модели должно состоять из слов самого сообщения (модель не имеет права придумывать названия и имена)."""
    text = " " + norm(message) + " "
    tokens = [t for t in norm(value).split() if t]
    return bool(tokens) and all(f" {t} " in text or t in text for t in tokens)


# ---------------------------------------------------------------- 4. извлечение полей
FIELD_STATUSES = ("found", "not_found", "uncertain")


@dataclass
class ExtractedField:
    field_id: str
    value: Optional[str]
    status: str
    source_text: Optional[str]


def validate_extraction(data: dict, allowed_ids: Iterable[str]) -> dict[str, ExtractedField]:
    raw = data.get("fields")
    if not isinstance(raw, dict):
        raise SchemaError("нет объекта fields")
    allowed = set(allowed_ids)
    out: dict[str, ExtractedField] = {}
    for fid, item in raw.items():
        if fid not in allowed:
            continue  # неизвестные поля молча отбрасываем: модель не добавляет свои
        if not isinstance(item, dict):
            raise SchemaError(f"поле {fid}: ожидался объект")
        status = item.get("status")
        if status not in FIELD_STATUSES:
            raise SchemaError(f"поле {fid}: status должен быть одним из {list(FIELD_STATUSES)}")
        value, src = _opt_str(item.get("value"), 300), _opt_str(item.get("source_text"), 400)
        if status == "found" and (value is None or src is None):
            raise SchemaError(f"поле {fid}: для found нужны value и source_text")
        out[fid] = ExtractedField(fid, value, status, src)
    return out


def ground_extraction(item: ExtractedField, ocr_text: str, min_ratio: float = 92.0) -> bool:
    """Значение принимается, только если исходный фрагмент реально есть в OCR-тексте, а значение — во фрагменте."""
    if item.status != "found" or not item.value or not item.source_text:
        return False
    src, text, val = norm(item.source_text), norm(ocr_text), norm(item.value)
    if len(src) < 3 or not val:
        return False
    if src not in text and fuzz.partial_ratio(src, text) < min_ratio:
        return False
    if val in src:
        return True
    if fuzz.partial_ratio(val, src) >= 88:
        return True
    digits_v = re.findall(r"\d+", val)
    return bool(digits_v) and set(digits_v) <= set(re.findall(r"\d+", src))


# ---------------------------------------------------------------- 5. пояснение к ответу
_CLAUSE_REF = re.compile(r"(?:\bп(?:ункт\w*|\.)?\s*)(\d+(?:\.\d+)*)", re.IGNORECASE)
_DOTTED = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)(?![\d])")
_NUMBERS = re.compile(r"\d+")


def validate_explanation(data: dict, allowed_clause_numbers: Iterable[str], grounding_text: str) -> str:
    text = _opt_str(data.get("text"), 900)
    if not text:
        raise SchemaError("пустой текст")
    allowed = {str(n).rstrip(".") for n in allowed_clause_numbers}
    for m in list(_CLAUSE_REF.finditer(text)) + list(_DOTTED.finditer(text)):
        ref = m.group(1).rstrip(".")
        if ref not in allowed and not any(a.endswith(":" + ref) for a in allowed):
            raise SchemaError(f"назван пункт {ref}, которого нет среди переданных")
    known = set(_NUMBERS.findall(grounding_text))
    for num in _NUMBERS.findall(text):
        if num not in known:
            raise SchemaError(f"в тексте появилось число {num}, которого нет в исходных данных")
    return text
