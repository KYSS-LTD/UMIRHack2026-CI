"""Можно ли передать обращение в деканат. Документ студент скачивает всегда; в очередь деканата уходит только полная и достоверная анкета.

Условия (правила вуза, а не код, поэтому две из них — в настройках `submission.*`):
* все обязательные ответы анкеты есть и проходят свои же проверки формата;
* направление обращения известно (перевод или зачисление);
* целевое направление указано (`submission.require_target_specialty`, по умолчанию да);
* академических задолженностей нет (`submission.allow_debts`, по умолчанию нет).
Учебное Положение других проверяемых по анкете условий не содержит: места и решение — за сотрудником деканата.
"""

from __future__ import annotations

from typing import Callable

from .questions import CORE_KEYS, QUESTIONS, REQUEST_TYPES
from .state import DialogContext

DEBTS_TEXT = ("Есть академические задолженности: деканат принимает обращения о переводе и зачислении только без задолженностей, "
              "поэтому отправить его нельзя. Готовый документ можно скачать и передать в деканат лично.")
TARGET_TEXT = "Не указано направление, на которое вы хотите перевестись или поступить."


def check_submission(ctx: DialogContext, setting: Callable[[str, object], object] = lambda key, default: default) -> list[str]:
    """Причины, по которым обращение отправить нельзя (пусто — можно)."""
    data, reasons = ctx.data, []
    bad = []
    for key in CORE_KEYS:
        value = data.get(key)
        if key == "has_debts":
            valid = isinstance(value, bool)
        else:
            valid = value not in (None, "") and QUESTIONS[key].parse(str(value)).ok
        if not valid:
            bad.append(QUESTIONS[key].title)
    if ctx.scenario not in REQUEST_TYPES:
        bad.append(QUESTIONS["request_type"].title)
    if bad:
        reasons.append("Не заполнено или заполнено неверно: " + ", ".join(bad) + ". Вернитесь к анкете и исправьте ответы.")
    if setting("submission.require_target_specialty", True) and not data.get("target_specialty"):
        reasons.append(TARGET_TEXT)
    if data.get("has_debts") is True and not setting("submission.allow_debts", False):
        reasons.append(DEBTS_TEXT)
    return reasons
