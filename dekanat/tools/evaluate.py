"""Автооценка на синтетическом наборе (аналог автопроверки организаторов).

    python -m dekanat eval --data data/synthetic            # весь путь: фото -> OCR -> поля -> ответ
    python -m dekanat eval --data data/synthetic --oracle   # только логика ответов (поля берутся из эталона)

Метрики: определение типа, точность извлечённых полей, правильность вердикта и ссылок на пункты.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from rapidfuzz import fuzz

from .config import DocTypeSpec
from .models import FieldResult, PartResult
from .pipeline import Dekanat


def _norm(s: Any) -> str:
    s = str(s).lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", s).strip()


def _iso(dmy: Optional[str]) -> Optional[str]:
    if not dmy or not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", str(dmy)):
        return None
    return dt.datetime.strptime(str(dmy), "%d.%m.%Y").date().isoformat()


def _unit_value(res_fields: list[dict], uid: str) -> tuple[bool, Any, Any]:
    """(найдено, значение, нормализованное) по идентификатору «поле» или «поле.часть»."""
    fid, _, pid = uid.partition(".")
    f = next((x for x in res_fields if x["id"] == fid), None)
    if f is None:
        return False, None, None
    if pid:
        p = next((x for x in f["parts"] if x["id"] == pid), None)
        if p is None:
            return False, None, None
        return bool(p["found"]), p["value"], p["normalized"]
    return bool(f["found"]), f["value"], f["normalized"]


def unit_correct(exp: Any, found: bool, value: Any, normalized: Any, lenient: bool = True) -> bool:
    if isinstance(exp, bool):
        return found == exp
    if exp is None:
        return not found
    if not found:
        return False
    e = str(exp)
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}\s*[–-]\s*\d{2}\.\d{2}\.\d{4}", e):  # период
        d = re.findall(r"(\d{2})\.(\d{2})\.(\d{4})", e)
        iso = [f"{y}-{m}-{dd}" for dd, m, y in d]
        return isinstance(normalized, dict) and normalized.get("start") == iso[0] and normalized.get("end") == iso[1]
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", e) or re.fullmatch(r"\d+", e):
        return _norm(value) == _norm(e)
    a, b = _norm(value), _norm(e)
    if a == b:
        return True
    return lenient and (fuzz.ratio(a, b) >= 90 or (b in a and len(b) >= 8))


# ----------------------------------------------------------------------------- оценка ответов


class _Stats:
    def __init__(self):
        self.d: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: [0, 0]))

    def add(self, group: str, key: str, ok: bool):
        self.d[group][key][0] += int(ok)
        self.d[group][key][1] += 1

    def summary(self) -> dict:
        return {g: {k: {"correct": v[0], "total": v[1], "share": round(v[0] / v[1], 3) if v[1] else None} for k, v in d.items()} for g, d in self.d.items()}


def _score_answer(stats: _Stats, q: dict, lab: dict, ans: dict, verbose: bool) -> dict:
    exp = q["expected"]
    verdict_ok = ans["verdict"] == exp["verdict"]
    src_nums = [s["ref"] for s in ans["sources"]]
    src_ok = None
    if exp.get("sources"):
        if exp["verdict"] == "missing":
            src_ok = set(exp["sources"]) <= set(src_nums)
        else:
            src_ok = bool(set(exp["sources"]) & set(src_nums))
    if q["kind"] == "fact":
        val = exp.get("value")
        src_ok = _norm(val) in _norm(ans["text"]) if val else None
    for key in ("all", q["kind"], f"level{lab['level']}"):
        stats.add("verdict", key, verdict_ok)
        if src_ok is not None:
            stats.add("sources" if q["kind"] != "fact" else "fact_value", key, bool(src_ok))
    if q["kind"] == "completeness" and exp["verdict"] == "missing":
        stats.add("missing_exact", "all", {m.get("unit") for m in ans["missing"]} == set(exp["missing"]))
    row = {"id": q["id"], "kind": q["kind"], "question": q["question"], "expected": exp, "verdict": ans["verdict"], "text": ans["text"], "sources": src_nums,
           "ok": bool(verdict_ok and (src_ok in (None, True)))}
    if verbose and not row["ok"]:
        print(f"  ✗ {q['id']} [{q['kind']}] {q['question']}\n      ожидали {exp}\n      получили {ans['verdict']} {src_nums}: {ans['text']}")
    return row


def fields_from_truth(spec: DocTypeSpec, units: dict) -> list[FieldResult]:
    """Карточка полей «как если бы OCR был идеальным» — для проверки одной только логики ответов."""
    out = []
    for f in spec.fields:
        if f.parts:
            parts = []
            for p in f.parts:
                v = units.get(f"{f.id}.{p.id}")
                if f.kind == "marks":
                    parts.append(PartResult(p.id, p.title, bool(v), "есть" if v else "нет", bool(v), 0.9, [], p.missing_phrase, "нет"))
                else:
                    parts.append(PartResult(p.id, p.title, v is not None, str(v or ""), _iso(v) or v, 0.9, [], p.missing_phrase))
            found = [p for p in parts if p.found]
            value = ("есть" if len(found) == len(parts) else "нет") if f.kind == "marks" else "; ".join(f"{p.title}: {p.value}" for p in found)
            out.append(FieldResult(f.id, f.title, f.kind, bool(found) if f.kind != "marks" else len(found) == len(parts), value, None, 0.9, [], "oracle", "", parts))
        else:
            v = units.get(f.id)
            norm = _iso(v) or v
            if f.kind == "period" and v:
                d = re.findall(r"(\d{2})\.(\d{2})\.(\d{4})", v)
                norm = {"start": f"{d[0][2]}-{d[0][1]}-{d[0][0]}", "end": f"{d[1][2]}-{d[1][1]}-{d[1][0]}"} if len(d) == 2 else v
            out.append(FieldResult(f.id, f.title, f.kind, v is not None, str(v or ""), norm, 0.9, [], "oracle"))
    return out


def run_qa_oracle(app: Dekanat, root: Path, verbose: bool = False, out: Optional[Path] = None) -> dict:
    questions = json.loads((root / "questions.json").read_text(encoding="utf-8"))
    docs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (root / "labels").glob("*.json")}
    stats, rows = _Stats(), []
    t0 = time.time()
    for q in questions:
        lab = docs[q["doc_id"]]
        spec = app.doc_types[lab["doc_type"]]
        ans = app.qa.answer(q["question"], spec, fields_from_truth(spec, lab["units"])).to_dict()
        rows.append(_score_answer(stats, q, lab, ans, verbose))
    report = {"seconds": round(time.time() - t0, 1), "documents": len(docs), "questions": len(questions), "metrics": stats.summary(), "field_failures": [], "qa": rows}
    if out:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_eval(app: Dekanat, root: Path, limit: Optional[int] = None, verbose: bool = False, out: Optional[Path] = None) -> dict:
    questions = json.loads((root / "questions.json").read_text(encoding="utf-8"))
    docs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (root / "labels").glob("*.json")}
    if limit:
        keep = list(docs)[:limit]
        docs = {k: docs[k] for k in keep}
        questions = [q for q in questions if q["doc_id"] in docs]
    stats = _Stats()
    t0 = time.time()
    failures = []
    for doc_id, lab in docs.items():
        data = (root / lab["image"]).read_bytes()
        res = app.analyze(data, lab["image"], "", None).to_dict(preview=False)  # автоопределение типа
        got_type = res["doc_type"]["id"]
        for key in ("all", f"level{lab['level']}", f"layout:{lab['layout']}", lab["doc_type"]):
            stats.add("type", key, got_type == lab["doc_type"])
        # поля считаем по правильному типу (ошибка типа учтена отдельно)
        fields = res["fields"] if got_type == lab["doc_type"] else app.analyze(data, lab["image"], "", lab["doc_type"]).to_dict(preview=False)["fields"]
        for uid, exp in lab["units"].items():
            found, value, norm = _unit_value(fields, uid)
            ok = unit_correct(exp, found, value, norm)
            is_mark = uid.split(".")[-1] in ("signature", "stamp")
            for key in ("all", f"level{lab['level']}", f"layout:{lab['layout']}", lab["doc_type"], "marks" if is_mark else "text_fields"):
                stats.add("fields", key, ok)
            stats.add("fields_strict", "all", unit_correct(exp, found, value, norm, lenient=False))
            if not ok:
                failures.append({"doc": doc_id, "unit": uid, "expected": exp, "got": value if found else None, "level": lab["level"], "layout": lab["layout"]})
    rows = []
    for q in questions:
        lab = docs[q["doc_id"]]
        data = (root / lab["image"]).read_bytes()
        ans = app.analyze(data, lab["image"], q["question"], lab["doc_type"]).to_dict(preview=False)["answer"]
        rows.append(_score_answer(stats, q, lab, ans, verbose))
    report = {"seconds": round(time.time() - t0, 1), "documents": len(docs), "questions": len(questions), "metrics": stats.summary(), "field_failures": failures, "qa": rows}
    if out:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def print_report(rep: dict) -> None:
    print(f"\nДокументов: {rep['documents']}, вопросов: {rep['questions']}, время: {rep['seconds']} с\n")
    names = {"type": "Определение типа документа", "fields": "Извлечение полей (допуск ≤10% на опечатки OCR)", "fields_strict": "Извлечение полей (строго)",
             "verdict": "Вердикт ответа (ok / missing / no_answer)", "sources": "Ссылка на пункт Положения", "fact_value": "Значение поля в ответе на фактический вопрос",
             "missing_exact": "Точный список недостающих полей"}
    for g, title in names.items():
        if g not in rep["metrics"]:
            continue
        print(title)
        for k, v in sorted(rep["metrics"][g].items()):
            print(f"   {k:24s} {v['correct']:4d}/{v['total']:<4d} {100 * v['share']:5.1f}%")
        print()
