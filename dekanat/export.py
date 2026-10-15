"""Формат results.json для автопроверки.

ВАЖНО: точный формат ответов задаётся README к набору данных организаторов (его нет в архиве с ТЗ).
Если формат отличается, правится только эта функция — остальной код не затрагивается.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Optional

from .pipeline import AnalysisResult


def field_value_for_export(f: dict) -> str:
    """Значение поля для карточки: как в документе, либо «не найдено» (по ТЗ)."""
    if f["kind"] == "marks":
        return f["value"]  # есть / нет
    if f["parts"] and f["status"] != "found":
        found = [f"{p['title']}: {p['value']}" for p in f["parts"] if p["found"]]
        lost = [p["title"] for p in f["parts"] if not p["found"]]
        if not found:
            return "не найдено"
        return "; ".join(found) + f" (не найдено: {', '.join(lost)})"
    return f["value"] if f["found"] else "не найдено"


def result_record(result: AnalysisResult, rec_id: Optional[str] = None, image: Optional[str] = None, question: str = "") -> dict:
    d = result.to_dict(preview=False)
    return {
        "id": rec_id,
        "image": image,
        "question": question,
        "doc_type": d["doc_type"]["id"],
        "doc_type_title": d["doc_type"]["title"],
        "fields": {f["export_title"]: field_value_for_export(f) for f in d["fields"]},
        "fields_detail": [
            {"id": f["id"], "title": f["title"], "value": f["value"], "status": f["status"], "confidence": f["confidence"],
             "parts": [{"id": p["id"], "title": p["title"], "value": p["value"], "found": p["found"]} for p in f["parts"]]}
            for f in d["fields"]
        ],
        "answer": d["answer"]["text"],
        "verdict": d["answer"]["verdict"],
        "sources": [s["label"] for s in d["answer"]["sources"]],
        "missing": [m.get("phrase") or m.get("title") for m in d["answer"]["missing"]],
        "notes": d["answer"]["notes"],
    }


_ID_KEYS = ("id", "question_id", "qid", "uid")
_IMAGE_KEYS = ("image", "file", "path", "image_path", "document", "file_name", "filename", "doc", "photo", "scan")
_Q_KEYS = ("question", "query", "q", "text", "вопрос")
_TYPE_KEYS = ("doc_type", "type", "document_type", "тип")


def _pick(row: dict, keys: Iterable[str]) -> Optional[str]:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return str(row[k])
    return None


def read_batch_input(path: Path) -> list[dict]:
    """JSON-список / {"items"|"questions"|"data": [...]} / JSONL / CSV -> [{id, image, question, doc_type}]."""
    text = path.read_text(encoding="utf-8-sig")
    rows: list[dict]
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines(), delimiter=";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","))
    elif path.suffix.lower() in (".jsonl", ".ndjson"):
        rows = [json.loads(l) for l in text.splitlines() if l.strip()]
    else:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("items", "questions", "data", "tasks", "samples"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                data = [data]
        rows = data
    out = []
    for i, row in enumerate(rows):
        out.append({
            "id": _pick(row, _ID_KEYS) or str(i + 1),
            "image": _pick(row, _IMAGE_KEYS),
            "question": _pick(row, _Q_KEYS) or "",
            "doc_type": _pick(row, _TYPE_KEYS),
            "raw": row,
        })
    return out
