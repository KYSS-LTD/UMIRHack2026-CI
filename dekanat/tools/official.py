"""Выгрузка results.json в формате организаторов кейса (version 1.0, README к набору данных).

    python -m dekanat results --data <папка набора> --out results.json

Для каждого документа из documents.json:
  fields      — все ключи типа (пропуск текстового поля — «не найдено», отметка подписи/печати — true/false), даты — ГГГГ-ММ-ДД;
  validation  — status (document_ok / missing_fields), missing_fields, sources («rule:3.1» …) из самого Положения;
  answers     — по одному ответу на каждый вопрос из questions.json: answerable, value, answer, sources.

Какие ключи у типа и как они называются, описано в блоке `official:` YAML типа документа (config/doc_types), код менять не нужно.
Требования к полям берутся из текста Положения (dekanat/knowledge_base/requirements.py), поэтому пункты в sources — настоящие пункты того
Положения, которое загружено (в нашем случае rules.md из набора).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..config import DocTypeSpec
from ..models import FieldResult
from ..pipeline import AnalysisResult, Dekanat
from ..textutil import normalize

VERSION = "1.0"
NOT_FOUND = "не найдено"
NO_INFO = "В документе и в Положении нет информации для ответа на этот вопрос. Обратитесь в деканат."
FINAL_DECISION = "сотрудник деканата"

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_DMY = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_FIELD_Q = re.compile(r"(?:поля|поле)\s*[«\"“„]([^»\"”]+)[»\"”]", re.IGNORECASE)
_FINAL_Q = re.compile(r"кто\b.{0,40}принима\w+.{0,40}окончательн\w+\s+решени|окончательн\w+\s+решени\w*.{0,40}\b(?:кто|принима)", re.IGNORECASE)
_COMPLETE_Q = re.compile(r"обязательн\w+\s+(?:сведени|пол|данн|реквизит)|содержит\s+ли\s+документ\s+все|все\s+ли\s+обязательн|полнот|комплектност", re.IGNORECASE)


@dataclass
class OField:
    unit: str
    key: str
    title: str
    part: Optional[str]
    mark: bool


def _norm_title(text: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", " ", str(text).lower().replace("ё", "е")).strip()


def official_type_of(dt: DocTypeSpec) -> str:
    return dt.official.get("type") or dt.id


def official_fields(dt: DocTypeSpec) -> list[OField]:
    return [OField(f["unit"], f["key"], f["title"], f["part"], f["mark"]) for f in dt.official.get("fields", [])]


def _iso(value: Any) -> Optional[str]:
    """Дата -> ГГГГ-ММ-ДД. Принимает ISO и ДД.ММ.ГГГГ; всё прочее — None."""
    s = str(value or "").strip()
    if _ISO.fullmatch(s):
        return s
    m = _DMY.fullmatch(s)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _result_for(fields: list[FieldResult], unit: str):
    """(FieldResult, PartResult | None) по идентификатору единицы «поле» или «поле.часть»."""
    fid, _, pid = unit.partition(".")
    res = next((f for f in fields if f.id == fid), None)
    if res is None:
        return None, None
    if not pid:
        return res, None
    return res, next((p for p in res.parts if p.id == pid), None)


def field_value(item: OField, fields: list[FieldResult]) -> Any:
    """Значение ключа results.json: строка (текстовое поле), true/false (подпись, печать). Не найдено -> «не найдено» / False."""
    res, part = _result_for(fields, item.unit)
    if res is None:
        return False if item.mark else NOT_FOUND
    node = part if part is not None else res
    if item.mark:
        return bool(node.found)
    if item.part:  # период: начало / окончание берутся каждое само по себе (найдена может быть только одна дата)
        norm = node.normalized if isinstance(node.normalized, dict) else {}
        got = norm.get(item.part)
        return (_iso(got) or str(got)) if got else NOT_FOUND
    if not node.found:
        return NOT_FOUND
    norm = node.normalized
    if res.kind == "date" or (part is not None and getattr(part, "id", "") == "date") or (isinstance(norm, str) and _ISO.fullmatch(norm)):
        iso = _iso(norm) or _iso(node.value)
        if iso:
            return iso
    if (part is not None and part.id == "course") or res.kind == "course":
        return str(norm if norm not in (None, "") else node.value).strip()
    if res.kind == "choice" and isinstance(norm, str) and norm:
        return norm
    return str(node.value).strip() or NOT_FOUND


def clause_number(clause) -> str:
    return str(clause.number)


class OfficialExporter:
    def __init__(self, svc: Dekanat):
        self.svc = svc
        self.by_official = {official_type_of(dt): dt for dt in svc.doc_types.values()}

    # ------------------------------------------------------------------ документ
    def spec_for(self, official_type: str) -> DocTypeSpec:
        dt = self.by_official.get(official_type)
        if dt is None or not dt.official:
            raise ValueError(f"Тип «{official_type}» не описан: добавьте блок official в config/doc_types/*.yaml")
        return dt

    def validation(self, dt: DocTypeSpec, fields: list[FieldResult], values: dict[str, Any]) -> dict:
        """Предварительная проверка: чего не хватает и какие пункты Положения этого требуют (всё выводится из текста Положения)."""
        req = self.svc.requirements.for_type(dt.id)
        items = official_fields(dt)
        required: dict[str, Any] = {}
        for it in items:
            best = req.best(it.unit) if req.found_any else None
            if best is not None:
                required[it.key] = best
            elif not req.found_any and any(it.unit == u or it.unit.startswith(u + ".") for u in dt.required_fallback):
                required[it.key] = None
        missing = []
        for it in items:
            if it.key not in required:
                continue
            ok = values[it.key] is True if it.mark else values[it.key] != NOT_FOUND
            if not ok:
                missing.append(it.key)
        basis = missing if missing else list(required)
        sources = sorted({f"rule:{clause_number(required[k].clause)}" for k in basis if required.get(k) is not None}, key=_rule_order)
        return {"status": "missing_fields" if missing else "document_ok", "missing_fields": missing, "sources": sources}

    def document(self, doc: dict, data: bytes, filename: str, questions: list[dict]) -> dict:
        dt = self.spec_for(doc["document_type"])
        res: AnalysisResult = self.svc.analyze(data, filename, "", dt.id)
        items = official_fields(dt)
        values = {it.key: field_value(it, res.fields) for it in items}
        validation = self.validation(dt, res.fields, values)
        ctx = _Context(self, dt, items, values, validation, res)
        answers = [ctx.answer(q) for q in questions]
        return {"document_id": doc["document_id"], "document_type": doc["document_type"], "fields": values, "validation": validation, "answers": answers}

    def build(self, manifest: list[dict], questions: list[dict], base: Path, prefer_pdf: bool = False, limit: Optional[int] = None) -> dict:
        by_doc: dict[str, list[dict]] = {}
        for q in questions:
            by_doc.setdefault(q["document_id"], []).append(q)
        out = []
        for doc in manifest[:limit] if limit else manifest:
            rel = doc.get("pdf") if prefer_pdf and doc.get("pdf") else doc["image"]
            path = base / rel
            out.append(self.document(doc, path.read_bytes(), path.name, by_doc.get(doc["document_id"], [])))
        return {"version": VERSION, "documents": out}


def _rule_order(ref: str) -> list:
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.:]", ref)[1:]]


class _Context:
    """Ответы на вопросы по одному документу."""

    def __init__(self, exp: OfficialExporter, dt: DocTypeSpec, items: list[OField], values: dict[str, Any], validation: dict, res: AnalysisResult):
        self.exp, self.dt, self.items, self.values, self.validation, self.res = exp, dt, items, values, validation, res
        self.by_title = {_norm_title(it.title): it for it in items}

    def _no_info(self, qid: str, text: str = NO_INFO) -> dict:
        return {"question_id": qid, "answerable": False, "value": None, "answer": text, "sources": []}

    def answer(self, q: dict) -> dict:
        qid, text = q["question_id"], q["question"]
        m = _FIELD_Q.search(text)
        if m:
            return self.field_answer(qid, m.group(1))
        if _FINAL_Q.search(text):
            return self.final_decision(qid, text)
        if _COMPLETE_Q.search(text):
            return self.completeness(qid)
        return self.other(qid, text)

    # ---- вопрос о поле
    def field_answer(self, qid: str, title: str) -> dict:
        it = self.by_title.get(_norm_title(title))
        if it is None:
            return self._no_info(qid)
        value = self.values[it.key]
        if it.mark:
            word = "есть" if value else "нет"
            return {"question_id": qid, "answerable": True, "value": "true" if value else "false", "answer": f"{it.title}: {word}.", "sources": [f"field:{it.key}"]}
        if value == NOT_FOUND:
            return self._no_info(qid, f"Поле «{it.title}» не найдено в документе. Обратитесь в деканат.")
        return {"question_id": qid, "answerable": True, "value": value, "answer": f"{it.title}: {value}.", "sources": [f"field:{it.key}"]}

    # ---- комплектность
    def completeness(self, qid: str) -> dict:
        v = self.validation
        nums = [s.split(":", 1)[1] for s in v["sources"]]
        refs = ", ".join(nums)
        if v["status"] == "document_ok":
            text = f"Документ содержит все обязательные сведения по пунктам {refs} Положения. Окончательное решение примет сотрудник деканата."
        else:
            titles = [next(it.title for it in self.items if it.key == k) for k in v["missing_fields"]]
            text = (f"Не хватает: {', '.join(t.lower() if i else t for i, t in enumerate(titles))}. Эти сведения обязательны по пунктам {refs} Положения. "
                    "Окончательное решение примет сотрудник деканата.")
        return {"question_id": qid, "answerable": True, "value": v["status"], "answer": text, "sources": list(v["sources"])}

    # ---- кто принимает решение
    def final_decision(self, qid: str, question: str) -> dict:
        for c in self.exp.svc.clauses:
            low = normalize(c.text)
            if "окончательн" in low and "решени" in low and "сотрудник" in low and "деканат" in low:
                return {"question_id": qid, "answerable": True, "value": FINAL_DECISION,
                        "answer": f"Окончательное решение принимает сотрудник деканата, пункт {c.number} Положения.", "sources": [f"rule:{c.number}"]}
        return self._no_info(qid)

    # ---- прочее: только то, что прямо сказано в Положении
    def other(self, qid: str, question: str) -> dict:
        ans = self.exp.svc.qa.answer_rules(question)
        refs = [f"rule:{s.ref}" for s in ans.sources if s.kind == "clause"]
        if ans.verdict == "no_answer" or not refs:
            return self._no_info(qid)
        return {"question_id": qid, "answerable": True, "value": ans.text, "answer": ans.text, "sources": refs}


def write_results(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


_DOC_KEYS = {"document_id", "document_type", "fields", "validation", "answers"}
_ANSWER_KEYS = {"question_id", "answerable", "value", "answer", "sources"}
_REF = re.compile(r"(?:rule:\d+(?:\.\d+)*|field:[a-z_]+)")


def check_report(report: dict) -> list[str]:
    """Проверка результата по правилам README организаторов (без их evaluate.py): ключи, типы, ISO-даты, источники. Пустой список — замечаний нет."""
    problems: list[str] = []
    if report.get("version") != VERSION or not isinstance(report.get("documents"), list):
        return ["на верхнем уровне нужны version «1.0» и массив documents"]
    for d in report["documents"]:
        did = d.get("document_id", "?")
        if set(d) != _DOC_KEYS:
            problems.append(f"{did}: ключи документа {sorted(d)} вместо {sorted(_DOC_KEYS)}")
            continue
        for key, val in d["fields"].items():
            if isinstance(val, bool):
                continue
            if not isinstance(val, str) or not val:
                problems.append(f"{did}: поле {key} должно быть непустой строкой или boolean")
            elif key.endswith(("_date", "_start", "_end")) and val != NOT_FOUND and not _ISO.fullmatch(val):
                problems.append(f"{did}: дата {key} не в формате ГГГГ-ММ-ДД: {val}")
        v = d["validation"]
        if set(v) != {"status", "missing_fields", "sources"} or v["status"] not in ("document_ok", "missing_fields"):
            problems.append(f"{did}: validation задан неверно")
        elif (v["status"] == "document_ok") != (not v["missing_fields"]):
            problems.append(f"{did}: status не согласован с missing_fields")
        for a in d["answers"]:
            if set(a) != _ANSWER_KEYS:
                problems.append(f"{did}: ключи ответа {sorted(a)}")
                continue
            qid = a["question_id"]
            if not isinstance(a["answerable"], bool) or not isinstance(a["answer"], str) or not a["answer"].strip():
                problems.append(f"{qid}: answerable — boolean, answer — непустой текст")
            if a["value"] is not None and not isinstance(a["value"], str):
                problems.append(f"{qid}: value — строка или null")
            if not a["answerable"] and (a["value"] is not None or a["sources"]):
                problems.append(f"{qid}: при answerable=false value должен быть null, а sources пустыми")
            if a["answerable"] and not a["sources"]:
                problems.append(f"{qid}: пустые sources допустимы только при answerable=false")
            if len(a["sources"]) != len(set(a["sources"])) or any(not _REF.fullmatch(x) for x in a["sources"]):
                problems.append(f"{qid}: sources — уникальные «rule:N.M» / «field:ключ»")
    return problems
