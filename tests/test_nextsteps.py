"""«Что дальше»: что принести и срок решения берутся из действующего Положения, со ссылкой на пункт; где в Положении ничего нет — пусто."""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from dekanat.dialog.nextsteps import add_working_days, build_next_steps, parse_term
from dekanat.web.app import create_app


def say(c, *texts):
    for t in texts:
        r = c.post("/api/assistant/message", json={"text": t, "defer": True})
        assert r.status_code == 200, r.text


@pytest.mark.parametrize("text, expected", [
    ("Решение принимается в течение 10 рабочих дней.", (10, True)),
    ("в течение 30 календарных дней со дня подачи", (30, False)),
    ("в течение 5 дней", (5, False)),
    ("Срок не установлен.", None),
    ("", None),
])
def test_parse_term(text, expected):
    assert parse_term(text) == expected


def test_working_days_skip_weekends():
    friday = date(2026, 10, 16)
    assert add_working_days(friday, 1) == date(2026, 10, 19)  # понедельник
    assert add_working_days(friday, 5) == date(2026, 10, 23)
    assert add_working_days(date(2026, 10, 14), 10) == date(2026, 10, 28)
    assert add_working_days(friday, 0) == friday


def answer(verdict, text="", label="п. 7.3", quote=""):
    sources = [SimpleNamespace(kind="clause", label=label, quote=quote or text)] if verdict == "ok" else []
    return SimpleNamespace(verdict=verdict, text=text, sources=sources)


def test_build_uses_the_regulation_answers_and_stays_empty_without_them():
    asked = []

    def ask(question):
        asked.append(question)
        if "документы" in question:
            return answer("ok", "Нужна справка об обучении. (п. 7.2 Положения)", "п. 7.2")
        return answer("ok", "Решение принимается в течение 10 рабочих дней. (п. 7.3 Положения)")

    steps = build_next_steps("transfer", ask, today=date(2026, 10, 14))
    assert steps["documents"] == {"text": "Нужна справка об обучении. (п. 7.2 Положения)", "sources": ["п. 7.2"]}
    assert steps["deadline"]["date"] == "2026-10-28" and steps["deadline"]["days"] == 10 and steps["deadline"]["working"] is True and steps["deadline"]["source"] == "п. 7.3"
    assert len(asked) == 2

    nothing = build_next_steps("enrollment", lambda q: answer("no_answer", "Нет информации."), today=date(2026, 10, 14))
    assert nothing == {"documents": None, "deadline": None}
    assert build_next_steps("", ask) == {"documents": None, "deadline": None}  # анкета ещё не началась — нечего советовать
    no_number = build_next_steps("transfer", lambda q: answer("ok", "Решение принимается без промедления.") if "дней" in q else answer("no_answer"), today=date(2026, 10, 14))
    assert no_number["deadline"] is None  # срок без числа не выдумывается


def test_endpoint_follows_the_scenario_and_the_current_regulation(tmp_settings):
    app = create_app(tmp_settings)
    transfer = TestClient(app)
    say(transfer, "Я хочу перевестись", "да")
    steps = transfer.get("/api/assistant/next-steps").json()
    assert "справку об обучении" in steps["documents"]["text"] and steps["documents"]["sources"] == ["п. 7.2"]
    assert steps["deadline"]["days"] == 10 and steps["deadline"]["working"] and steps["deadline"]["source"] == "п. 7.3"

    enroll = TestClient(app)  # в учебном Положении нет правил зачисления: честно пусто
    say(enroll, "Как к вам поступить?", "да")
    assert enroll.get("/api/assistant/next-steps").json() == {"documents": None, "deadline": None}

    fresh = TestClient(app)
    fresh.get("/api/assistant/state")
    assert fresh.get("/api/assistant/next-steps").json() == {"documents": None, "deadline": None}  # без анкеты
    assert TestClient(app).get("/api/assistant/next-steps").status_code in (200, 410)
