"""Структурированный ответ (`answer.report`): вывод, факты документа, пункты, отсутствующее и сомнительное, оговорка о решении деканата."""

from dekanat.tools.evaluate import fields_from_truth
from dekanat.models import DISCLAIMER

STUDY = {
    "student_name": "Иванов Иван Иванович", "university": "Южный университет", "direction": "09.03.01 Информатика", "study_form": "очная",
    "study_period": "01.09.2022 – 30.06.2026", "issue.number": "458", "issue.date": "15.05.2024", "seal_signature.signature": True, "seal_signature.stamp": True,
}


def ask(app, truth, question, absent=()):
    units = dict(truth)
    for k in absent:
        units[k] = False if isinstance(units[k], bool) else None
    spec = app.doc_types["study_certificate"]
    return app.qa.answer(question, spec, fields_from_truth(spec, units))


def test_every_answer_carries_the_disclaimer(app):
    for q in ("Хватит ли этой справки для перевода на 2 курс?", "Какая форма обучения указана?", "Сколько дней рассматривается заявление?", "Сколько стоит обучение?"):
        rep = ask(app, STUDY, q).to_dict()["report"]
        assert rep["disclaimer"] == DISCLAIMER and "Окончательное решение принимает сотрудник деканата" in rep["disclaimer"]


def test_completeness_report_lists_facts_clauses_and_what_is_missing(app):
    a = ask(app, STUDY, "Хватит ли этой справки для перевода на 2 курс?", absent=["issue.date", "seal_signature.stamp"])
    rep = a.to_dict()["report"]
    assert rep["conclusion"] == a.text and rep["verdict"] == "missing"
    assert [c["ref"] for c in rep["clauses"]] == ["3.4"] and rep["clauses"][0]["quote"].startswith("Номер и дата выдачи")
    by_title = {f["title"]: f for f in rep["facts"]}
    assert by_title["ФИО студента"]["value"] == "Иванов Иван Иванович" and by_title["ФИО студента"]["found"] is True
    assert by_title["Дата выдачи"]["found"] is False and by_title["Дата выдачи"]["value"] == "не найдено"   # отсутствующее поле — «не найдено»
    assert set(rep["missing"]) >= {"даты выдачи", "печати вуза"} and rep["no_source"] is False


def test_fact_report_points_to_the_document_field(app):
    rep = ask(app, STUDY, "Какая форма обучения указана в справке?").to_dict()["report"]
    assert rep["facts"][0]["value"] == "очная" and rep["document_fields"][0]["label"].startswith("поле") and rep["clauses"] == []


def test_rule_report_has_the_clause_and_no_document_facts(app):
    rep = app.qa.answer("Сколько дней рассматривается заявление?", None, None).to_dict()["report"]
    assert rep["clauses"][0]["ref"] == "6.1" and rep["facts"] == [] and rep["missing"] == [] and rep["no_source"] is False


def test_no_answer_report_admits_there_is_no_source(app):
    a = ask(app, STUDY, "Сколько стоит обучение в вузе?")
    rep = a.to_dict()["report"]
    assert a.verdict == "no_answer" and rep["no_source"] is True and rep["clauses"] == [] and "Обратитесь в деканат" in rep["conclusion"]


def test_low_confidence_values_are_reported_as_doubtful(app):
    spec = app.doc_types["study_certificate"]
    fields = fields_from_truth(spec, STUDY)
    for f in fields:
        if f.id == "student_name":
            f.confidence = 0.3
    rep = app.qa.answer("Хватит ли этой справки для перевода на 2 курс?", spec, fields).to_dict()["report"]
    assert rep["doubtful"] == ["ФИО студента"]


def test_report_does_not_change_the_legacy_answer_fields(app):
    d = ask(app, STUDY, "Хватит ли этой справки для перевода на 2 курс?", absent=["issue.date"]).to_dict()
    assert {"verdict", "text", "intent", "sources", "missing", "related", "confidence", "notes", "debug"} <= set(d)
    assert d["text"].startswith("Не хватает даты выдачи")
