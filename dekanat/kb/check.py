"""Проверка базы знаний по контрольным вопросам: после замены Положения или добавления документа сразу видно, не сломались ли ответы.

Вопросы и ожидаемые пункты лежат в config/golden_questions.yaml; ответы даёт тот же механизм, что и на столе (без документа).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml


def load_golden(path: "str | Path") -> list[dict]:
    """Список {question, ref | no_answer}. Нет файла или он пуст — пустой список; некорректные строки пропускаются."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    items = raw.get("questions", []) if isinstance(raw, dict) else []
    out = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("question"), str) and it["question"].strip() and (it.get("no_answer") is True or it.get("ref")):
            out.append({"question": it["question"].strip(), "ref": str(it["ref"]).strip() if it.get("ref") else "", "no_answer": it.get("no_answer") is True})
    return out


def _has_ref(answer, ref: str) -> bool:
    """Пункт «7.2» совпадает и с «7.2» основного Положения, и с «doc:7.2» документа базы знаний."""
    return answer.verdict == "ok" and any(s.kind == "clause" and (s.ref == ref or s.ref.endswith(":" + ref)) for s in answer.sources)


def run_check(ask: Callable[[str], object], items: list[dict]) -> dict:
    """ask(вопрос) -> Answer. Возвращает {total, passed, items: [{question, expected, got, ok}]}."""
    rows = []
    for it in items:
        answer = ask(it["question"])
        refs = [s.ref.split(":")[-1] for s in answer.sources if s.kind == "clause"]
        got = ("п. " + ", ".join(refs)) if answer.verdict == "ok" and refs else ("нет ответа" if answer.verdict == "no_answer" else answer.verdict)
        ok = (answer.verdict == "no_answer") if it["no_answer"] else _has_ref(answer, it["ref"])
        rows.append({"question": it["question"], "expected": "нет ответа" if it["no_answer"] else "п. " + it["ref"], "got": got, "ok": ok})
    return {"total": len(rows), "passed": sum(1 for r in rows if r["ok"]), "items": rows}
