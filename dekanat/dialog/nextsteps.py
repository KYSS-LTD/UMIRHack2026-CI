"""«Что дальше» после готового заявления: что принести и в какой срок решат. Всё берётся из действующего Положения (поиск по тем же правилам,
что и ответы секретаря), со ссылкой на пункт, а не зашито в код: при замене Положения подсказка меняется вместе с ним.

Если в Положении об этом ничего нет (например, правил зачисления), блок честно пуст — секретарь ничего не выдумывает.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Callable, Optional

QUESTIONS = {
    "transfer": ("Какие документы нужны для перевода из другого вуза?", "В течение скольки дней принимается решение о переводе?"),
    "enrollment": ("Какие документы нужны для зачисления?", "В течение скольки дней принимается решение о зачислении?"),
}
_TERM = re.compile(r"в\s+течение\s+(\d{1,3})\s+(рабоч\w*|календарн\w*)?\s*дн", re.IGNORECASE)


def add_working_days(start: date, days: int) -> date:
    """Дата через `days` рабочих дней (суббота и воскресенье не считаются; праздники не учитываются)."""
    current, left = start, days
    while left > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            left -= 1
    return current


def parse_term(text: str) -> Optional[tuple[int, bool]]:
    """«в течение 10 рабочих дней» -> (10, True); «в течение 30 календарных дней» -> (30, False); без слова — календарные."""
    m = _TERM.search(text or "")
    if not m:
        return None
    return int(m.group(1)), bool(m.group(2) and m.group(2).lower().startswith("рабоч"))


def build_next_steps(scenario: str, ask: Callable[[str], object], today: Optional[date] = None) -> dict:
    """ask(вопрос) -> Answer. Возвращает {"documents": {...}|None, "deadline": {...}|None}."""
    today = today or date.today()
    questions = QUESTIONS.get(scenario)
    if not questions:
        return {"documents": None, "deadline": None}
    docs = ask(questions[0])
    documents = None
    if docs.verdict == "ok" and docs.sources:
        documents = {"text": docs.text, "sources": [s.label for s in docs.sources if s.kind == "clause"]}
    term = ask(questions[1])
    deadline = None
    if term.verdict == "ok" and term.sources:
        src = term.sources[0]
        parsed = parse_term(src.quote or term.text)
        if parsed:
            days, working = parsed
            when = add_working_days(today, days) if working else today + timedelta(days=days)
            deadline = {"days": days, "working": working, "date": when.isoformat(), "source": src.label, "text": term.text}
    return {"documents": documents, "deadline": deadline}
