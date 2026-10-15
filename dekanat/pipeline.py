"""Единый конвейер обработки документа:

загрузка файла -> проверка формата -> OCR -> определение типа документа -> извлечение полей -> валидация
-> поиск по Положению -> формирование ответа с источниками."""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from .classify import Classification, classify
from .config import DocTypeSpec, Settings, load_doc_types, load_settings
from .extract import FieldExtractor
from .document_processing.formats import check_format
from .document_processing.validation import validate_fields
from .extract.locate import boxes_for_words, locate_fragment
from .extract.values import parse_value
from .kb import ClauseIndex, RequirementModel, load_regulation, load_synonym_groups, parse_regulation_text
from .kb.documents import BASE_ID, doc_clauses
from .llm import LLMService
from .models import Answer, Clause, FieldResult, OcrPage
from .ocr import OcrOptions, OcrRunner, TesseractEngine
from .ocr import photo as photo_quality
from .ocr.preprocess import load_pages
from .qa import QAEngine
from .qa.docqa import GENERIC_WORDS, DocumentQA

log = logging.getLogger(__name__)

Progress = Callable[[str, str, float], None]  # (этап, подпись, доля 0..1)


class Cancelled(Exception):
    """Пользователь отменил проверку."""


NEED_DOCUMENT_TEXT = ("Чтобы проверить документ, загрузите его фото или скан — без файла проверять нечего. "
                      "Вопросы о правилах (например, «Сколько дней рассматривается заявление?») можно задавать и без документа.")
NO_TYPE_TEXT = "Не удалось определить тип документа, поэтому проверить его не получится. Выберите тип документа — и проверка запустится сразу."
UNSUPPORTED_NOTE = ("Документ не похож на справку об обучении, заявление на академический отпуск или справку о практике, поэтому ответ дан по тексту "
                    "самого документа, без сверки с Положением.")
GENERIC_ID = "generic"  # doc_type для режима «другой документ»: вопросы по тексту, без карточки полей


# Подписи этапа «чтение текста» для посетителя: внутренние названия вариантов обработки на сайт не выводятся.
_OCR_VARIANT_HINTS = {"binary": "повышаю контраст", "deglare": "убираю блики", "clahe": "выравниваю освещение"}
_OCR_GEOMETRY_HINTS = {"page": "нахожу лист", "page+deskew": "нахожу и выпрямляю лист", "deskew": "выпрямляю снимок"}


def _ocr_hint(msg: str) -> str:
    """«page+deskew · clahe» -> «выравниваю освещение»; понятные подписи («проверяю поворот страницы») остаются как есть."""
    geo, _, variant = msg.rpartition(" · ")
    if not geo:
        russian = any("а" <= ch <= "я" or ch == "ё" for ch in msg.lower())
        return msg if russian else ""
    return _OCR_VARIANT_HINTS.get(variant.strip()) or _OCR_GEOMETRY_HINTS.get(geo.strip(), "")


def page_preview(img: np.ndarray, page: int = 0, max_side: int = 1600) -> dict:
    """Страница -> JPEG для показа на столе. width/height — размеры самой страницы (в её координатах считаются рамки), картинка может быть меньше."""
    h, w = img.shape[:2]
    s = min(1.0, max_side / max(h, w))
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return {"page": page, "width": w, "height": h, "data_url": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()}


@dataclass
class AnalysisResult:
    doc_type: Optional[DocTypeSpec]
    how: str  # manual | auto | none
    classification: Optional[Classification]
    pages: list[OcrPage]
    fields: list[FieldResult]
    answer: Answer
    timings: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    original_pages: list[np.ndarray] = field(default_factory=list)
    type_titles: dict = field(default_factory=dict)
    photo_quality: dict = field(default_factory=dict)  # качество снимка: уровень, причины и советы (dekanat/ocr/photo.py)

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)

    def to_dict(self, preview: bool = True) -> dict:
        dt = self.doc_type
        cls = self.classification
        out: dict[str, Any] = {
            "doc_type": {
                "id": dt.id if dt else None,
                "title": dt.title if dt else ("Другой документ" if self.how == "generic" else None),
                "how": self.how,
                "confidence": round(cls.confidence, 3) if (cls and self.how == "auto") else (1.0 if dt else 0.0),
                "uncertain": bool(cls and self.how == "auto" and cls.uncertain),
                "ranking": [{"id": i, "score": round(s, 2)} for i, s in (cls.ranking if cls else [])],
                "candidates": [{"id": c["id"], "title": self.type_titles.get(c["id"], c["id"]), "share": round(c["share"], 3)} for c in (cls.candidates if cls else [])],
                "reason": cls.reason if cls else "",
            },
            "ocr": {
                "engine": self.pages[0].engine if self.pages else None,
                "pages": [
                    {"index": p.index, "width": p.orig_size[0], "height": p.orig_size[1], "variant": p.variant, "deskew_angle": p.deskew_angle,
                     "rotation": p.rotation, "score": p.quality.get("score"), "mean_conf": p.quality.get("mean_conf"), "words": p.quality.get("words"),
                     "seconds": p.quality.get("seconds")}
                    for p in self.pages
                ],
                "text": self.text,
            },
            "photo_quality": self.photo_quality,
            "fields": [f.to_dict() for f in self.fields],
            "answer": self.answer.to_dict(),
            "warnings": self.warnings,
            "timings": self.timings,
        }
        if preview:
            out["images"] = [self._preview(i) for i in range(len(self.original_pages))]
        return out

    def _preview(self, i: int, max_side: int = 1600) -> dict:
        return page_preview(self.original_pages[i], i, max_side)


class Dekanat:
    """Фасад сервиса: настройки, типы документов, Положение, OCR и ответы."""

    def __init__(self, settings: Optional[Settings] = None, storage: Any = None, llm: Optional[LLMService] = None):
        self.settings = settings or load_settings()
        self.storage = storage  # необязательно: хранилище с документами базы знаний (без него — только файл Положения)
        # Единая точка доступа к модели. При USE_LOCAL_LLM=False backend не создаётся и модель не загружается.
        self.llm = llm or LLMService.from_settings(self.settings)
        self._lock = threading.Lock()
        # кеш в памяти процесса (на диск не пишется): повторные вопросы по тому же файлу не запускают OCR заново; ocr.cache_size: 0 — выключить
        self._cache_size = max(0, int(self.settings.get("ocr.cache_size", 4)))
        self._ocr_cache: "OrderedDict[str, list[OcrPage]]" = OrderedDict()
        self._field_cache: "OrderedDict[tuple[str, str], list[FieldResult]]" = OrderedDict()
        tessdata = self.settings.get("ocr.tessdata_dir")
        if not tessdata:  # языковые данные, скачанные командой fetch-lang, подхватываются без настройки
            local = Path(self.settings.root) / "data" / "tessdata"
            if (local / "rus.traineddata").exists():
                tessdata = str(local)
        self.engine = TesseractEngine(cmd=self.settings.get("ocr.tesseract_cmd"), languages=str(self.settings.get("ocr.languages", "rus+eng")), tessdata_dir=tessdata)
        self.runner = OcrRunner(self.engine, OcrOptions(good_score=float(self.settings.get("ocr.good_score", 70)), fast=bool(self.settings.get("ocr.fast", False))))
        self.doc_types: dict[str, DocTypeSpec] = {}
        self.clauses: list[Clause] = []
        self.regulation_name = ""
        self._file_clauses: list[Clause] = []
        self._file_name = ""
        self.reload()

    # ------------------------------------------------------------------ загрузка конфигурации и Положения
    def reload(self) -> None:
        """Перечитывает типы документов, файл Положения и документы базы знаний из хранилища; пересобирает индекс."""
        s = self.settings
        self.doc_types = load_doc_types(s.doc_types_dir)
        reg_path = s.path("regulation.path", "data/regulations/polozhenie.md")
        self._file_clauses = load_regulation(reg_path) if reg_path.exists() else []
        self._file_name = reg_path.name if reg_path.exists() else ""
        self.reload_knowledge_base()

    def reload_knowledge_base(self) -> None:
        """Собирает пункты: основное Положение (файл или его замена из хранилища) + активные дополнительные документы.

        Удалённые, отключённые и заменённые документы в индекс не попадают: старые пункты нельзя выдать как действующие."""
        clauses, name = list(self._file_clauses), self._file_name
        if self.storage is not None:
            clauses, name = self._compose_from_storage(clauses, name)
        self.clauses, self.regulation_name = clauses, name
        self._rebuild_kb()

    def _compose_from_storage(self, file_clauses: list[Clause], file_name: str) -> tuple[list[Clause], str]:
        docs = self.storage.kb.active_documents()
        base = next((d for d in docs if d["id"] == BASE_ID), None)
        base_row = self.storage.kb.get(BASE_ID)
        if base is not None:  # администратор заменил основное Положение
            clauses, name = parse_regulation_text(base["text"]), base["name"]
        elif base_row is not None:  # основное Положение отключено администратором
            clauses, name = [], base_row["name"]
        else:
            clauses, name = file_clauses, file_name
        for d in docs:
            if d["id"] != BASE_ID:
                clauses += doc_clauses(d["text"], d["id"], d["name"])
        return clauses, name

    def set_regulation_text(self, text: str, name: str = "загруженное Положение") -> int:
        """Замена Положения «в памяти» (режим без хранилища). С хранилищем замену делает KnowledgeBaseManager."""
        self._file_clauses = parse_regulation_text(text)
        self._file_name = name
        self.reload_knowledge_base()
        return len(self._file_clauses)

    def _rebuild_kb(self) -> None:
        s = self.settings
        syn_path = s.path("synonyms_file", "config/synonyms.yaml")
        groups = load_synonym_groups(syn_path)
        field_groups = load_synonym_groups(syn_path, "field_groups")
        # всё собираем во временные переменные и подменяем разом: параллельный запрос не увидит наполовину обновлённый индекс
        index = ClauseIndex(self.clauses, groups)
        requirements = RequirementModel(self.doc_types, self.clauses)
        docqa = DocumentQA(load_synonym_groups(syn_path, "doc_groups") + groups)
        qa = QAEngine(self.doc_types, self.clauses, index, requirements,
                      min_coverage=float(s.get("qa.min_coverage", 0.55)), max_related=int(s.get("qa.max_related", 2)), llm=self.llm,
                      field_groups=field_groups)
        extractors = {tid: FieldExtractor(dt, bool(s.get("marks.enabled", True))) for tid, dt in self.doc_types.items()}
        self.index, self.requirements, self.docqa, self.qa, self._extractors = index, requirements, docqa, qa, extractors
        with self._lock:
            self._field_cache.clear()

    # ------------------------------------------------------------------ OCR
    def _ocr(self, data: bytes, filename: str, progress: Optional[Progress] = None) -> tuple[list[np.ndarray], list[OcrPage], bool]:
        report = progress or (lambda *_: None)
        report("load", "Проверяю формат файла", 0.02)
        check_format(data, filename, int(self.settings.get("server.max_upload_mb", 25)) * 1024 * 1024)
        key = hashlib.sha1(data).hexdigest()
        report("load", "Открываю файл", 0.04)
        originals = load_pages(data, filename, int(self.settings.get("ocr.max_pages", 5)))
        with self._lock:
            cached = self._ocr_cache.get(key)
            if cached is not None:
                self._ocr_cache.move_to_end(key)
                return originals, cached, True
        n = len(originals)
        counter = {"k": 0}

        def step(page: int, msg: str) -> None:
            counter["k"] += 1
            frac = 0.08 + 0.62 * ((page + min(0.95, counter["k"] / 5.0)) / n)
            suffix = f" (страница {page + 1} из {n})" if n > 1 else ""
            hint = _ocr_hint(msg)
            report("ocr", f"Читаю текст{suffix}" + (f": {hint}" if hint else ""), min(frac, 0.7))

        pages = self.runner.run(originals, step)
        for orig, page in zip(originals, pages):
            try:
                page.quality["photo"] = photo_quality.assess_page(orig, page)
            except Exception:  # оценка снимка — подсказка, она не должна ломать проверку
                log.exception("Не удалось оценить качество снимка")
        with self._lock:
            if self._cache_size:
                self._ocr_cache[key] = pages
                while len(self._ocr_cache) > self._cache_size:
                    self._ocr_cache.popitem(last=False)
        return originals, pages, False

    # ------------------------------------------------------------------ анализ
    def analyze(self, data: Optional[bytes], filename: str = "", question: str = "", doc_type: Optional[str] = None,
                progress: Optional[Progress] = None, document_first: bool = False) -> AnalysisResult:
        """progress(этап, подпись, доля) можно использовать для индикатора; исключение из него (Cancelled) прерывает проверку.
        document_first — режим стола: с загруженным документом отвечаем по документу, по Положению — только если оно упомянуто
        (пакетный режим и CLI работают как в кейсе: по полям документа и пунктам Положения)."""
        report = progress or (lambda *_: None)
        t0 = time.time()
        timings: dict[str, float] = {}
        warnings: list[str] = []
        pages: list[OcrPage] = []
        originals: list[np.ndarray] = []
        spec: Optional[DocTypeSpec] = None
        how = "none"
        cls: Optional[Classification] = None
        fields: list[FieldResult] = []
        ocr_notes: list[str] = []
        photo: dict = {}
        titles = {tid: d.title for tid, d in self.doc_types.items()}
        if data:
            originals, pages, cached = self._ocr(data, filename, progress)
            timings["ocr"] = round(time.time() - t0, 2)
            if cached:
                timings["ocr_cached"] = True
            has_text = any(p.words for p in pages)
            photo = photo_quality.assess_document([p.quality["photo"] for p in pages if p.quality.get("photo")])
            if not has_text:
                warnings.append("Текст на изображении не распознан. Сфотографируйте документ ровнее, при хорошем освещении и без бликов.")
            report("classify", "Определяю тип документа", 0.74)
            if doc_type == GENERIC_ID:
                how = "generic"
            elif doc_type and doc_type not in ("auto", "") and doc_type in self.doc_types:
                spec, how = self.doc_types[doc_type], "manual"
            elif doc_type and doc_type not in ("auto", ""):
                warnings.append(f"Неизвестный тип документа «{doc_type}», определяю автоматически.")
            if has_text and how != "generic":
                cls = classify(self.doc_types, pages)
                if spec is None:
                    if cls.type_id:
                        spec, how = self.doc_types[cls.type_id], "auto"
                        if cls.uncertain:
                            warnings.append(f"Тип определён неуверенно — похоже на «{spec.title}», но проверьте и при необходимости выберите другой.")
                    else:
                        warnings.append("Документ не похож ни на один из известных типов. Ответ будет по тексту документа; тип можно выбрать вручную.")
            t1 = time.time()
            if spec is not None and has_text:
                report("extract", "Заполняю карточку полей", 0.84)
                fkey = (hashlib.sha1(data).hexdigest(), spec.id)
                with self._lock:
                    fields = self._field_cache.get(fkey)
                if fields is None:
                    fields = self._extractors[spec.id].extract(pages)
                    fields = self._llm_fill_missing(spec, pages, fields, ocr_notes)
                    if self._cache_size:
                        with self._lock:
                            self._field_cache[fkey] = fields
                            while len(self._field_cache) > 4 * self._cache_size:
                                self._field_cache.popitem(last=False)
            if fields:
                report("extract", "Проверяю значения полей", 0.9)
                validate_fields(spec, fields)  # этап «валидация»: статус обнаружения и причина сомнения у каждого поля
            by_llm = [f.title for f in fields if f.method == "llm"]
            if by_llm:  # в том числе для результата из кеша
                ocr_notes.append(f"Значения найдены языковой моделью по тексту документа и требуют проверки по снимку: {', '.join(by_llm)}.")
            timings["extract"] = round(time.time() - t1, 2)
        t2 = time.time()
        report("answer", "Ищу ответ в Положении", 0.93)
        if data is None and self.qa.needs_document(question):
            answer = Answer("no_answer", NEED_DOCUMENT_TEXT, "no_document", confidence=1.0)
        elif data and spec is None:
            answer = self._answer_unsupported(question, pages, how == "generic", ocr_notes, document_first)
        else:
            text_qa = (lambda q: self.docqa.answer(q, pages)) if (document_first and any(p.words for p in pages)) else None
            answer = self.qa.answer(question, spec, fields or None, ocr_notes, document_first=document_first, document_text=text_qa)
        if photo.get("retake") and any(p.words for p in pages):
            if answer.intent == "no_document_type":  # тип не определён из-за снимка, а не из-за документа: совет «выберите тип» — не главное
                answer.text = photo_quality.retake_text(photo)
            line = photo_quality.summary_line(photo)
            if line and line not in answer.notes:
                answer.notes.append(line)
        self._attach_explanation(question, answer)
        timings["answer"] = round(time.time() - t2, 2)
        timings["total"] = round(time.time() - t0, 2)
        report("done", "Готово", 1.0)
        return AnalysisResult(spec, how, cls, pages, fields, answer, timings, warnings, originals, titles, photo)

    def answer_for_fields(self, question: str, doc_type_id: Optional[str], fields: list[FieldResult], document_first: bool = False) -> Answer:
        """Ответ по уже готовой карточке (например, исправленной пользователем) без повторного OCR."""
        spec = self.doc_types.get(doc_type_id or "")
        if spec is None or not fields:
            return self.analyze(None, "", question).answer
        answer = self.qa.answer(question, spec, fields, document_first=document_first)
        self._attach_explanation(question, answer)
        return answer

    def _answer_unsupported(self, question: str, pages: list[OcrPage], chosen_generic: bool, ocr_notes: list[str], document_first: bool = False) -> Answer:
        """Тип документа не определён (или выбран «другой документ»): отвечаем по тексту документа и по Положению."""
        q = question.strip()
        if document_first and q and any(p.words for p in pages) and not self.qa.needs_document(q):
            # режим стола: по Положению — только если оно упомянуто, иначе только по тексту документа
            if self.qa.regulation_mentioned(q):
                return self.qa.answer_rules(q)
            ans = self.qa.from_document(q, lambda x: self.docqa.answer(x, pages))
            ans.notes += ([] if chosen_generic else [UNSUPPORTED_NOTE]) + ocr_notes
            return ans
        needs_check = self.qa.needs_document(q)
        rule = self.qa.answer(q, None, None) if q and not needs_check else None
        doc = self.docqa.answer(q, pages) if (q and not needs_check) and any(p.words for p in pages) else None
        note = [] if chosen_generic else [UNSUPPORTED_NOTE]
        if q and not needs_check and doc is not None and not self.index.query_terms(q, extra_drop=GENERIC_WORDS):
            doc.notes += note + ocr_notes  # «что в документе?» — показываем начало документа, а не случайный пункт Положения
            return doc
        if doc is not None and doc.debug.get("requisite"):
            doc.notes += note + ocr_notes  # «Какая дата/какой номер документа?» — спрашивают про сам документ, а не про Положение
            return doc
        if rule is not None and rule.verdict == "ok" and rule.debug.get("coverage", 0) >= 0.75:
            return rule  # вопрос явно про правила — тип документа не важен
        if doc is not None and doc.verdict == "ok":
            doc.notes += note + ocr_notes
            return doc
        if rule is not None and rule.verdict == "ok":
            return rule
        if doc is not None and chosen_generic:
            doc.notes += ocr_notes
            return doc
        if needs_check or not q:
            return Answer("no_answer", NO_TYPE_TEXT, "no_document_type", confidence=0.5)
        if doc is not None:
            doc.notes += note + ocr_notes
            return doc
        return Answer("no_answer", NO_TYPE_TEXT, "no_document_type", confidence=0.5)

    def info(self) -> dict:
        ocr = self.engine.info()
        return {
            "ocr": ocr,
            "doc_types": [{"id": d.id, "title": d.title, "fields": len(d.fields)} for d in self.doc_types.values()],
            "regulation": {"name": self.regulation_name, "clauses": len(self.clauses)},
            # при USE_LOCAL_LLM=False status() не обращается к сети
            "llm": self.llm.status(),
        }

    # ------------------------------------------------------------------ необязательные возможности локальной модели (USE_LOCAL_LLM=True)
    def _llm_fill_missing(self, spec: DocTypeSpec, pages: list[OcrPage], fields: list[FieldResult], notes: list[str]) -> list[FieldResult]:
        """Дозаполняет ТОЛЬКО поля, которые правила не нашли. Значение принимается, если его исходный фрагмент реально есть в OCR-тексте
        и значение проходит те же проверки формата, что и у правил. Найденное моделью помечается и считается неуверенным."""
        if not self.llm.enabled:
            return fields
        by_id = {f.id: f for f in fields}
        wanted = [f for f in spec.fields if f.kind not in ("marks", "composite") and not f.parts and not by_id[f.id].found]
        if not wanted:
            return fields
        text = "\n".join(p.text for p in pages)
        try:
            got = self.llm.extract_fields(text, spec.title, [(f.id, f.title) for f in wanted])
        except Exception as exc:  # модель не должна ронять проверку документа
            log.warning("LLM-извлечение полей не удалось: %s", exc)
            return fields
        for f in wanted:
            item = got.get(f.id)
            parsed = parse_value(f.kind, item.value, f.choices) if item else None
            if item is None or parsed is None:
                continue  # значение не прошло проверку формата — поле остаётся неподтверждённым
            words, page = locate_fragment(pages, item.source_text or "")
            boxes = boxes_for_words(page, words) if page is not None else []
            by_id[f.id] = FieldResult(f.id, f.title, f.kind, True, parsed.value, parsed.normalized, 0.45, boxes, "llm", item.source_text or "",
                                      export_title=f.export_title)
        return [by_id[f.id] for f in spec.fields]

    def _attach_explanation(self, question: str, answer: Answer) -> None:
        """Понятный пересказ уже проверенного ответа. Основной текст ответа не меняется; при любой ошибке пересказа просто нет."""
        if not self.llm.enabled or answer.verdict == "no_answer" or not question.strip():
            return
        clauses = [(s.ref, s.quote) for s in answer.sources if s.kind == "clause" and s.quote]
        facts = [f"{f['title']}: {f['value']}" for f in answer.facts]
        try:
            answer.explanation = self.llm.explain_answer(question, answer.verdict, answer.text, facts, clauses) or ""
        except Exception as exc:
            log.warning("Пересказ ответа не удался: %s", exc)
