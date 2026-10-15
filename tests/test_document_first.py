"""Стол декана: пока загружен документ, секретарь отвечает по документу; по Положению — без документа и когда оно названо в вопросе."""

import time

import pytest
from fastapi.testclient import TestClient

from conftest import needs_ocr
from dekanat.models import Answer, Source
from dekanat.web.app import create_app
from test_qa import STUDY, ask

DOC = {"document_first": True}


def doc_answer(app, question, **absent):
    """Ответ по карточке справки об обучении в режиме «сначала документ»."""
    from dekanat.tools.evaluate import fields_from_truth

    units = dict(STUDY)
    for k in absent.get("absent", ()):
        units[k] = False if isinstance(units[k], bool) else None
    spec = app.doc_types["study_certificate"]
    return app.qa.answer(question, spec, fields_from_truth(spec, units), **DOC)


class TestShowQuestionsFindTheFieldInTheDocument:
    @pytest.mark.parametrize("question, field", [
        ("покажи фио", "student_name"), ("Покажи ФИО", "student_name"), ("покажи фио студента", "student_name"), ("кто студент", "student_name"),
        ("покажи название вуза", "university"), ("покажи вуз", "university"), ("найди название вуза", "university"), ("выдели форму обучения", "study_form"),
        ("покажи период обучения", "study_period"), ("покажи направление", "direction"), ("показать ФИО в документе", "student_name"),
    ])
    def test_field_is_found(self, app, question, field):
        a = doc_answer(app, question)
        assert a.intent == "fact" and a.verdict == "ok", (question, a.text)
        assert a.sources[0].kind == "field" and a.sources[0].ref == field

    def test_seal_and_signature_are_marks_not_places(self, app):
        a = doc_answer(app, "где печать")
        assert a.sources[0].ref == "seal_signature.stamp" and a.intent == "fact"
        assert doc_answer(app, "покажи подпись").sources[0].ref == "seal_signature.signature"

    def test_missing_field_is_reported_with_the_clause(self, app):
        a = doc_answer(app, "покажи печать", absent=["seal_signature.stamp"])
        assert a.verdict == "missing" and a.sources[0].kind == "clause" and "печати" in a.text

    def test_without_a_document_the_regulation_answers(self, app):
        a = app.qa.answer("покажи фио", None, None, **DOC)
        assert a.intent == "rule"  # документа нет — отвечаем по Положению, как раньше


class TestRegulationOnlyWhenNamed:
    def test_rule_question_with_a_document_points_to_the_regulation(self, app):
        a = doc_answer(app, "Сколько дней рассматривается заявление?")
        assert a.verdict == "no_answer" and a.intent == "document" and not a.sources
        assert "загруженном документе нет ответа" in a.text and "по Положению" in a.text
        assert a.debug["regulation_question"] == "Сколько дней рассматривается заявление — по Положению?"

    @pytest.mark.parametrize("question, clause", [
        ("Сколько дней рассматривается заявление по Положению?", "6.1"), ("Что говорит Положение о сроке рассмотрения заявления?", "6.1"),
        ("По правилам, сколько дней рассматривается заявление?", "6.1"), ("Что сказано в пункте 3.4?", "3.4"), ("что в п. 7.2", "7.2"),
        ("Согласно регламенту, в какой срок принимается решение о переводе?", "7.3"),
    ])
    def test_named_regulation_answers_from_it(self, app, question, clause):
        a = doc_answer(app, question)
        assert a.verdict == "ok" and a.intent == "rule" and a.sources[0].ref == clause, (question, a.text)

    def test_the_regulation_hint_really_gets_an_answer(self, app):
        hint = doc_answer(app, "Сколько дней рассматривается заявление?").debug["regulation_question"]
        a = doc_answer(app, hint)
        assert a.verdict == "ok" and a.sources[0].ref == "6.1"

    def test_a_question_about_the_document_is_still_a_check(self, app):
        a = doc_answer(app, "Хватит ли этой справки для перевода на 2 курс?", absent=["issue.date", "seal_signature.stamp"])
        assert a.intent == "completeness" and a.verdict == "missing" and a.sources[0].ref == "3.4"

    def test_no_answer_anywhere_is_honest(self, app):
        a = doc_answer(app, "Сколько стоит обучение?")
        assert a.verdict == "no_answer" and a.text == "В документе и в Положении нет информации о стоимости обучения. Обратитесь в деканат."

    def test_text_of_the_document_is_used_when_no_field_matches(self, app):
        found = Answer("ok", "Оплата: 1000 ₽", "document", [Source("document", "1:3", "строка 3", "Оплата 1000")])
        a = app.qa.answer("Какая сумма оплаты?", app.doc_types["study_certificate"], doc_fields(app), document_text=lambda q: found, **DOC)
        assert a.text == "Оплата: 1000 ₽" and a.intent == "document"

    def test_the_default_mode_is_unchanged(self, app):
        """Пакетный режим и CLI работают как в кейсе: по полям документа и пунктам Положения."""
        a = ask(app, "study_certificate", STUDY, "Сколько дней рассматривается заявление?")
        assert a.verdict == "ok" and a.sources[0].ref == "6.1"


def doc_fields(app):
    from dekanat.tools.evaluate import fields_from_truth

    return fields_from_truth(app.doc_types["study_certificate"], dict(STUDY))


# ============================================================================ весь путь через сайт (нужен Tesseract)
def wait_job(c, job_id, timeout=180.0):
    end = time.time() + timeout
    while time.time() < end:
        snap = c.get(f"/api/jobs/{job_id}").json()
        if snap["status"] in ("done", "error", "cancelled"):
            return snap
        time.sleep(0.2)
    raise AssertionError("задача не завершилась")


def desk_check(c, data, question, doc_type="study_certificate"):
    r = c.post("/api/jobs", files={"file": ("s.png", data, "image/png")}, data={"question": question, "doc_type": doc_type})
    assert r.status_code == 200, r.text
    snap = wait_job(c, r.json()["id"])
    assert snap["status"] == "done", snap.get("error")
    return snap["result"]


@needs_ocr
def test_desk_shows_and_highlights_the_field_when_asked(tmp_settings, samples):
    c = TestClient(create_app(tmp_settings))
    data = (samples / "sample_02.png").read_bytes()
    for question, field in (("покажи фио", "student_name"), ("покажи название вуза", "university"), ("покажи фио в документе", "student_name")):
        res = desk_check(c, data, question)
        src = res["answer"]["sources"][0]
        assert res["answer"]["intent"] == "fact" and src["kind"] == "field" and src["ref"] == field, (question, res["answer"]["text"])
        card = next(f for f in res["fields"] if f["id"] == field)
        assert card["boxes"], "у найденного поля есть рамка, которую стол выделит и приблизит"


@needs_ocr
def test_desk_answers_from_the_document_and_names_the_regulation_only_on_request(tmp_settings, samples):
    c = TestClient(create_app(tmp_settings))
    data = (samples / "sample_02.png").read_bytes()
    plain = desk_check(c, data, "Сколько дней рассматривается заявление?")["answer"]
    assert plain["verdict"] == "no_answer" and plain["intent"] == "document" and "по Положению" in plain["text"]
    named = desk_check(c, data, "Сколько дней рассматривается заявление по Положению?")["answer"]
    assert named["verdict"] == "ok" and named["sources"][0]["ref"] == "6.1"
    # без файла вопрос идёт к Положению, как раньше; синхронный /api/analyze (скрипты) работает как в кейсе
    rules = c.post("/api/jobs", data={"question": "Сколько дней рассматривается заявление?"}).json()
    assert wait_job(c, rules["id"])["result"]["answer"]["sources"][0]["ref"] == "6.1"
    legacy = c.post("/api/analyze", files={"file": ("s.png", data, "image/png")}, data={"question": "Сколько дней рассматривается заявление?", "doc_type": "study_certificate"}).json()
    assert legacy["answer"]["sources"][0]["ref"] == "6.1"


@needs_ocr
def test_assistant_chat_follows_the_same_rule_for_the_session_document(tmp_settings, samples):
    c = TestClient(create_app(tmp_settings))
    c.get("/api/assistant/state")
    desk_check(c, (samples / "sample_02.png").read_bytes(), "")
    r = c.post("/api/assistant/message", json={"text": "Сколько дней рассматривается заявление?"}).json()
    assert r["answer"]["intent"] == "document" and r["answer"]["verdict"] == "no_answer"
    r = c.post("/api/assistant/message", json={"text": "Сколько дней рассматривается заявление по Положению?"}).json()
    assert r["answer"]["sources"][0]["ref"] == "6.1"
    r = c.post("/api/assistant/message", json={"text": "покажи фио"}).json()
    assert r["answer"]["sources"][0]["ref"] == "student_name"
