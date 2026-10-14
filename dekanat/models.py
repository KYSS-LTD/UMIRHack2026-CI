"""Структуры данных, общие для всех этапов пайплайна."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np


@dataclass
class Box:
    """Прямоугольник в пикселях исходного изображения (после EXIF-поворота)."""

    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 0

    def union(self, other: "Box") -> "Box":
        return Box(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1), self.page)

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def to_dict(self) -> dict:
        return {"page": self.page, "x0": round(self.x0), "y0": round(self.y0), "x1": round(self.x1), "y1": round(self.y1)}


@dataclass
class Word:
    """Слово OCR в координатах обработанного (выпрямленного) изображения."""

    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Row:
    """Строка текста: слова, лежащие на одной горизонтали (в т.ч. ячейки таблицы)."""

    words: list[Word]
    page: int = 0

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class OcrPage:
    index: int
    width: int
    height: int
    words: list[Word]
    rows: list[Row]
    transform: np.ndarray  # 3x3: исходные координаты -> координаты обработанного изображения
    orig_size: tuple[int, int]  # (w, h) исходного изображения
    engine: str = "tesseract"
    variant: str = "base"
    quality: dict = field(default_factory=dict)
    deskew_angle: float = 0.0
    rotation: int = 0
    image: Optional[np.ndarray] = None  # обработанное цветное изображение (BGR), не сериализуется

    @property
    def text(self) -> str:
        return "\n".join(r.text for r in self.rows)

    def map_box(self, x0: float, y0: float, x1: float, y1: float) -> Box:
        """Прямоугольник обработанного изображения -> ограничивающий прямоугольник в исходном."""
        inv = np.linalg.inv(self.transform)
        pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], dtype=np.float64).T
        mapped = inv @ pts
        mapped = mapped[:2] / mapped[2:3]
        ow, oh = self.orig_size
        return Box(
            float(np.clip(mapped[0].min(), 0, ow)),
            float(np.clip(mapped[1].min(), 0, oh)),
            float(np.clip(mapped[0].max(), 0, ow)),
            float(np.clip(mapped[1].max(), 0, oh)),
            self.index,
        )

    def map_words(self, words: list[Word]) -> list[Box]:
        return [self.map_box(w.x0, w.y0, w.x1, w.y1) for w in words]


@dataclass
class PartResult:
    id: str
    title: str
    found: bool
    value: str = ""
    normalized: Any = None
    confidence: float = 0.0
    boxes: list[Box] = field(default_factory=list)
    missing_phrase: str = ""
    absent_text: str = "не найдено"
    doubt: str = ""  # причина сомнения (этап валидации); пусто — значение подтверждено

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "found": self.found,
            "value": self.value if (self.found or self.value) else self.absent_text,
            "normalized": self.normalized,
            "confidence": round(self.confidence, 3),
            "boxes": [b.to_dict() for b in self.boxes],
            "doubt": self.doubt,
            "confirmed": bool(self.found and not self.doubt),
        }


@dataclass
class FieldResult:
    id: str
    title: str
    kind: str
    found: bool
    value: str = ""
    normalized: Any = None
    confidence: float = 0.0
    boxes: list[Box] = field(default_factory=list)
    method: str = ""
    evidence: str = ""
    parts: list[PartResult] = field(default_factory=list)
    export_title: str = ""
    doubt: str = ""  # причина сомнения (этап валидации); пусто — значение подтверждено

    @property
    def status(self) -> str:
        if self.parts:
            n = sum(p.found for p in self.parts)
            if n == len(self.parts):
                return "found"
            return "partial" if n else "missing"
        return "found" if self.found else "missing"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "export_title": self.export_title or self.title,
            "kind": self.kind,
            "status": self.status,
            "found": self.found,
            "value": self.value if self.value else "не найдено",
            "normalized": self.normalized,
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "evidence": self.evidence,  # исходный фрагмент: строка документа, где найдено значение
            "doubt": self.doubt,
            "confirmed": bool(self.status == "found" and not self.doubt),
            "boxes": [b.to_dict() for b in self.boxes],
            "parts": [p.to_dict() for p in self.parts],
        }


@dataclass
class Clause:
    """Пункт Положения."""

    number: str  # «3.4»; у пунктов дополнительных документов базы знаний — «d2:3.4» (уникальный ключ)
    text: str
    section: list[str] = field(default_factory=list)  # заголовки разделов, в которые входит пункт
    numbered: bool = True
    doc_id: str = ""  # пусто — основное Положение
    doc_name: str = ""  # название дополнительного документа базы знаний
    display_number: str = ""  # «3.4» без префикса документа

    @property
    def shown(self) -> str:
        return self.display_number or self.number

    @property
    def label(self) -> str:
        base = f"п. {self.shown}" if self.numbered else self.shown
        return f"{base} ({self.doc_name})" if self.doc_name else base

    @property
    def cite(self) -> str:
        """Как ссылаться на пункт в тексте ответа: «п. 3.4 Положения» / «п. 3.4 документа «Регламент»»."""
        base = f"п. {self.shown}" if self.numbered else self.shown
        return f"{base} документа «{self.doc_name}»" if self.doc_name else f"{base} Положения"

    @property
    def heading(self) -> str:
        return self.section[-1] if self.section else ""

    def to_dict(self) -> dict:
        out = {"number": self.number, "display": self.shown, "label": self.label, "text": self.text, "section": self.section}
        if self.doc_name:
            out["document"] = {"id": self.doc_id, "name": self.doc_name}
        return out


@dataclass
class Source:
    kind: str  # clause | field
    ref: str  # «3.4» либо id поля
    label: str  # «п. 3.4» / «поле «Дата выдачи»»
    quote: str = ""
    boxes: list[Box] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "label": self.label, "quote": self.quote, "boxes": [b.to_dict() for b in self.boxes]}


DISCLAIMER = "Это предварительная оценка, а не решение. Окончательное решение принимает сотрудник деканата."
NOT_FOUND = "не найдено"


@dataclass
class Answer:
    verdict: str  # ok | missing | no_answer
    text: str
    intent: str = ""
    sources: list[Source] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    related: list[Source] = field(default_factory=list)
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)
    debug: dict = field(default_factory=dict)
    facts: list[dict] = field(default_factory=list)  # факты исходного документа, относящиеся к вопросу
    explanation: str = ""  # пересказ проверенного ответа локальной моделью (только USE_LOCAL_LLM=True); text остаётся главным

    def report(self) -> dict:
        """Структура ответа по ТЗ: вывод; факты документа; ссылки на пункты; отсутствующие/сомнительные данные; оговорка о деканате."""
        clauses = [s for s in self.sources if s.kind == "clause"]
        fields = [s for s in self.sources if s.kind == "field"]
        absent = [m.get("phrase") or m.get("title") for m in self.missing]
        absent += [f["title"] for f in self.facts if not f["found"] and (f["title"] not in absent)]
        doubtful = [f["title"] for f in self.facts if f.get("doubtful")]
        out = {
            "conclusion": self.text,
            "verdict": self.verdict,
            "facts": self.facts,
            "clauses": [{"ref": s.ref, "label": s.label, "quote": s.quote} for s in clauses],
            "document_fields": [{"ref": s.ref, "label": s.label, "value": s.quote} for s in fields],
            "missing": list(dict.fromkeys(absent)),
            "doubtful": doubtful,
            "no_source": not self.sources,
            "disclaimer": DISCLAIMER,
        }
        if self.explanation:
            out["explanation"] = self.explanation
            out["explanation_note"] = "Пересказ подготовлен локальной моделью по проверенным фактам; основным остаётся вывод выше."
        return out

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "text": self.text,
            "intent": self.intent,
            "sources": [s.to_dict() for s in self.sources],
            "missing": self.missing,
            "related": [s.to_dict() for s in self.related],
            "confidence": round(self.confidence, 3),
            "notes": self.notes,
            "debug": self.debug,
            "report": self.report(),
        }


VERDICT_TITLES = {
    "ok": "Документ в порядке",
    "missing": "Чего-то не хватает",
    "no_answer": "Ответа нет",
}
