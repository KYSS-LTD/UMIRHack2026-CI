"""Разбор значений полей: даты, ФИО, номера, оценки... Устойчиво к типичным ошибкам OCR."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from rapidfuzz import fuzz

from ..textutil import clean_value, is_blank_value, normalize, title_case_name

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_MONTH_ALT = "|".join(MONTHS)

# цифры, которые OCR путает с буквами: нули и единицы путаются часто и почти всегда безопасно...
_DIGIT_FIX_SAFE = str.maketrans({"O": "0", "o": "0", "О": "0", "о": "0", "l": "1", "I": "1", "|": "1"})
# ...а «З», «Б», «S» — настоящие буквы номеров («Б-12/24»), их заменяем, только если это уверенно похоже на дату с годом из 4 цифр
_DIGIT_FIX_WIDE = str.maketrans({"O": "0", "o": "0", "О": "0", "о": "0", "l": "1", "I": "1", "|": "1", "З": "3", "з": "3", "Б": "6", "S": "5"})
_RUN = re.compile(r"(?<![A-Za-zА-Яа-яЁё])[\dOoОоlI|ЗзБS]{1,4}(?:\s?[./\-]\s?[\dOoОоlI|ЗзБS]{1,4}){1,2}(?![A-Za-zА-Яа-яЁё])")
_YEAR_TAIL = re.compile(r"(?:19|20)[\dOoОоlI|]{2}\s*$")

_DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{4}|\d{2})(?!\d)")
_DATE_TXT = re.compile(rf"(?<!\d)[«\"“]?\s*(\d{{1,2}})\s*[»\"”]?\s*({_MONTH_ALT})\s+(\d{{4}})(?:\s*(?:г\.?|года))?", re.IGNORECASE)


def repair_text(text: str) -> str:
    """Исправляет «О1.О9.2О23» -> «01.09.2023». Длина строки не меняется (смещения сохраняются)."""

    def fix(m: re.Match) -> str:
        run = m.group(0)
        if not re.search(r"\d", run):
            return run
        return run.translate(_DIGIT_FIX_WIDE if _YEAR_TAIL.search(run) else _DIGIT_FIX_SAFE)

    return _RUN.sub(fix, text)


@dataclass
class DateVal:
    day: int
    month: int
    year: int
    start: int
    end: int
    raw: str

    @property
    def iso(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    @property
    def dmy(self) -> str:
        return f"{self.day:02d}.{self.month:02d}.{self.year:04d}"


def _valid(d: int, m: int, y: int) -> bool:
    return 1 <= d <= 31 and 1 <= m <= 12 and 1900 <= y <= 2100


def find_dates(text: str) -> list[DateVal]:
    text = repair_text(text)
    out: list[DateVal] = []
    for m in _DATE_NUM.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if _valid(d, mo, y):
            out.append(DateVal(d, mo, y, m.start(), m.end(), m.group(0)))
    for m in _DATE_TXT.finditer(text):
        d, mo, y = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
        if _valid(d, mo, y):
            out.append(DateVal(d, mo, y, m.start(), m.end(), m.group(0)))
    out.sort(key=lambda x: x.start)
    return out


@dataclass
class Parsed:
    value: str
    normalized: Any = None
    span: Optional[tuple[int, int]] = None  # символы исходной строки, из которых взято значение


# ----------------------------------------------------------------------------- парсеры по видам

def parse_date(text: str) -> Optional[Parsed]:
    dates = find_dates(text)
    if not dates:
        return None
    d = dates[0]
    return Parsed(d.dmy, d.iso, (d.start, d.end))


_NAME_TOKEN = re.compile(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\-]*\.?")
_TITLE_WORDS = {"справка", "справки", "практике", "практика", "практики", "обучении", "обучения", "заявление", "заявления", "академический", "отпуск",
                "министерство", "отдел", "университет", "институт", "факультет", "организация", "направление", "подготовки", "период", "номер", "дата", "выдачи"}
_NOT_NAME = {"студент", "студентка", "студента", "студентки", "группы", "группа", "курса", "курс", "обучающийся", "обучающаяся",
             "директор", "начальник", "менеджер", "инженер", "зав", "декан", "ректор", "подпись", "печать", "оценка", "номер"}


def parse_person(text: str, strict: bool = False) -> Optional[Parsed]:
    tokens = []
    pos = 0
    for m in _NAME_TOKEN.finditer(text):
        between = text[pos:m.start()]
        if tokens and re.search(r"[,;()\[\]\d№:/]", between):
            break
        if m.group(0).lower().strip(".") in _NOT_NAME and not tokens:
            pos = m.end()
            continue
        if m.group(0).lower().strip(".") in _NOT_NAME:
            break
        word = m.group(0).strip(".")
        # третье слово — отчество: с заглавной буквы либо с типичным окончанием (иначе это уже не ФИО)
        if len(tokens) == 2 and not (word[:1].isupper() or re.search(r"(вич|вна|ична|ич|оглы|кызы)$", word.lower())):
            break
        tokens.append(m)
        pos = m.end()
        if len(tokens) == 3:
            break
    if len(tokens) < 2:
        return None
    words = [t.group(0) for t in tokens]
    if any(w.lower().strip(".") in _TITLE_WORDS for w in words):
        return None
    if sum(1 for w in words if len(w.strip(".")) >= 2) < 2 and not any(w.endswith(".") for w in words):
        return None
    if strict and not all(w[0].isupper() for w in words):
        return None
    value = title_case_name(" ".join(w.rstrip(".") + ("." if len(w.strip(".")) == 1 else "") for w in words))
    value = re.sub(r"(\b[А-ЯЁ]\.)\s+(?=[А-ЯЁ]\.)", r"\1", value)  # «И. И.» -> «И.И.»
    return Parsed(value, None, (tokens[0].start(), tokens[-1].end()))


def parse_org(text: str) -> Optional[Parsed]:
    value = clean_value(text)
    if len(value) < 5 or not re.search(r"[А-Яа-яA-Za-z]{3}", value):
        return None
    return Parsed(value, None, (0, len(text)))


def parse_text(text: str) -> Optional[Parsed]:
    value = clean_value(text)
    if len(value) < 2 or is_blank_value(value):
        return None
    return Parsed(value, None, (0, len(text)))


_NUM_PREFIX = r"(?:№|N[°oо0ºo]?\.?|No\.?|ном\w*\.?)"
_NUMBER = re.compile(rf"(?:{_NUM_PREFIX}\s*)?([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9/\-]*)")


def parse_number(text: str) -> Optional[Parsed]:
    """Номер документа: «№ 458», «458», «Б-12/24»."""
    text = text.strip()
    dates = find_dates(text)
    if dates and dates[0].start <= 1:
        return None
    cut = len(text)
    if dates:
        cut = dates[0].start
    scope = text[:cut]
    for m in _NUMBER.finditer(scope):
        token = m.group(1)
        # отсекаем слова "от", "дата" и т.п.
        if not re.search(r"\d", token):
            if m.group(0).strip() == token and re.fullmatch(r"(?i:от|дата|выдан\w*|номер)", token):
                continue
            continue
        token = repair_text(token) if re.fullmatch(r"[\dOoОоlI]+", token) else token
        return Parsed(token.strip("-/"), token.strip("-/"), (m.start(1), m.end(1)))
    return None


_CHOICE_CACHE: dict = {}


def parse_choice(text: str, choices: dict[str, list[str]]) -> Optional[Parsed]:
    norm = normalize(text)
    words = re.findall(r"[а-яa-z\-]+", norm)
    best: tuple[float, str] = (0.0, "")
    # длинные варианты («очно-заочная») проверяем первыми
    items = sorted(((canon, v) for canon, variants in choices.items() for v in variants), key=lambda cv: -len(cv[1]))
    for canon, variant in items:
        v = normalize(variant)
        if v in norm:
            return Parsed(canon, canon)
        for w in words:
            score = fuzz.ratio(w, v)
            if score >= 84 and score > best[0]:
                best = (score, canon)
    if best[1]:
        return Parsed(best[1], best[1])
    return None


_GRADE_WORDS = ["неудовлетворительно", "удовлетворительно", "отлично", "хорошо", "незачтено", "зачтено"]
_GRADE_RE = re.compile(r"\b([2-5])\b\s*(?:\(\s*([а-яё]+)\s*\))?|([А-Яа-яЁё]{5,})")


def parse_grade(text: str) -> Optional[Parsed]:
    norm = normalize(text)
    toks = re.findall(r"[а-я]{5,}", norm)
    found = None
    for tok in toks:  # сначала точные совпадения, потом нечёткие (иначе «зачтено» путается с «незачтено»)
        if tok in _GRADE_WORDS:
            found = tok
            break
    if found is None:
        best = (0.0, "")
        for tok in toks:
            for g in _GRADE_WORDS:
                r = fuzz.ratio(tok, g)
                if r >= 85 and r > best[0]:
                    best = (r, g)
        found = best[1] or None
    if found:
        digit = re.search(r"(?<![\d.])([2-5])(?![\d.])", norm)
        value = f"{digit.group(1)} ({found})" if digit else found
        return Parsed(value, found, (0, len(text)))
    m = re.search(r"(?<!\d)([2-5])(?!\d|[./,]\d)", norm)
    if m:
        return Parsed(m.group(1), m.group(1), (m.start(), m.end()))
    return None


def parse_period(text: str) -> Optional[Parsed]:
    dates = find_dates(text)
    if len(dates) >= 2:
        a, b = dates[0], dates[1]
        return Parsed(f"{a.dmy} – {b.dmy}", {"start": a.iso, "end": b.iso}, (a.start, b.end))
    years = re.search(r"((?:19|20)\d\d)\s*[-–—]\s*((?:19|20)\d\d)", text)
    if years:
        return Parsed(f"{years.group(1)} – {years.group(2)}", {"start": years.group(1), "end": years.group(2)}, (years.start(), years.end()))
    if len(dates) == 1:
        a = dates[0]
        return Parsed(a.dmy, {"start": a.iso}, (a.start, a.end))
    return None


_GROUP_RE = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])([A-Za-zА-ЯЁа-яё]{1,8})\s*[-–—]?\s*(\d{1,4}[A-Za-zА-Яа-я]?(?:-\d{1,2})?)(?![\dA-Za-zА-Яа-я])")
_GROUP_DIGIT_RE = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9.])(\d{1,3})\s*[-–—]\s*(\d{1,4})(?![\dA-Za-zА-Яа-я.])")

_LAT_TO_CYR_UP = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


def parse_group(text: str) -> Optional[Parsed]:
    t = re.sub(r"(?i)\bгрупп\w*\b[\s:№]*", "", text)
    for m in _GROUP_RE.finditer(t):
        prefix, num = m.group(1), m.group(2)
        if prefix.lower() in ("курс", "курса", "кур"):
            continue
        if not (prefix.isupper() or len(prefix) <= 4):
            continue
        letters = prefix.upper().translate(_LAT_TO_CYR_UP)
        value = f"{letters}-{num.upper()}"
        return Parsed(value, value, (m.start(), m.end()))
    # OCR часто читает «Э», «Ф», «З» как цифры («33-14» вместо «ЭЭ-14»): код всё равно есть в документе
    m = _GROUP_DIGIT_RE.search(t)
    if m:
        value = f"{m.group(1)}-{m.group(2)}"
        return Parsed(value, value, (m.start(), m.end()))
    return None


def parse_course(text: str) -> Optional[Parsed]:
    m = re.search(r"(?<![\d.])([1-6])\s*(?:-?(?:й|го))?\s*(?i:курс\w*)?(?![\d.])", text)
    if m:
        return Parsed(m.group(1), int(m.group(1)), (m.start(1), m.end(1)))
    return None


def parse_value(kind: str, text: str, choices: Optional[dict] = None) -> Optional[Parsed]:
    """Единая точка входа: разбор текста как значения поля заданного вида."""
    if text is None or is_blank_value(text):
        return None
    if kind == "person":
        return parse_person(text)
    if kind == "org":
        return parse_org(text)
    if kind == "date":
        return parse_date(text)
    if kind == "number":
        return parse_number(text)
    if kind == "choice":
        return parse_choice(text, choices or {})
    if kind == "period":
        return parse_period(text)
    if kind == "grade":
        return parse_grade(text)
    if kind == "group":
        return parse_group(text)
    if kind == "course":
        return parse_course(text)
    return parse_text(text)
