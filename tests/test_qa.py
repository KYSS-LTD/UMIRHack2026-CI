"""Логика ответов при идеальном OCR: поля карточки берутся из эталона."""

import pytest

from dekanat.evaluate import fields_from_truth

STUDY = {
    "student_name": "Иванов Иван Иванович", "university": "Южный университет", "direction": "09.03.01 Информатика",
    "study_form": "очная", "study_period": "01.09.2022 – 30.06.2026", "issue.number": "458", "issue.date": "15.05.2024",
    "seal_signature.signature": True, "seal_signature.stamp": True,
}
LEAVE = {
    "student_name": "Петрова Анна Сергеевна", "group_course.group": "ИВТ-21", "group_course.course": "2", "reason": "по состоянию здоровья",
    "start_date": "01.09.2024", "end_date": "31.08.2025", "application_date": "20.08.2024", "student_signature.signature": True,
}
PRACTICE = {
    "student_name": "Сидоров Пётр Петрович", "organization": "ООО «Вектор»", "period": "01.07.2024 – 28.07.2024", "supervisor": "Орлов Олег Олегович",
    "grade": "хорошо", "issue.number": "12", "issue.date": "01.08.2024", "seal_signature.signature": True, "seal_signature.stamp": True,
}


def ask(app, type_id, truth, question, **absent):
    units = dict(truth)
    for k in absent.get("absent", ()):
        units[k] = False if isinstance(units[k], bool) else None
    spec = app.doc_types[type_id]
    return app.qa.answer(question, spec, fields_from_truth(spec, units))


class TestCompleteness:
    def test_ok_matches_the_example_from_the_brief(self, app):
        a = ask(app, "study_certificate", STUDY, "Хватит ли этой справки для перевода на 2 курс?")
        assert a.verdict == "ok"
        assert a.text.startswith("Справка содержит все данные, которые требует п. 3.2 Положения.")
        assert "Окончательное решение примет сотрудник деканата" in a.text
        assert a.sources[0].ref == "3.2"

    def test_missing_matches_the_example_from_the_brief(self, app):
        a = ask(app, "study_certificate", STUDY, "Хватит ли этой справки для перевода на 2 курс?", absent=["issue.date", "seal_signature.stamp"])
        assert a.verdict == "missing"
        assert a.text == "Не хватает даты выдачи и печати вуза. Эти поля обязательны по п. 3.4 Положения."
        assert [s.ref for s in a.sources] == ["3.4"]
        assert {m["unit"] for m in a.missing} == {"issue.date", "seal_signature.stamp"}

    def test_no_answer_matches_the_example_from_the_brief(self, app):
        a = ask(app, "study_certificate", STUDY, "Сколько стоит обучение?")
        assert a.verdict == "no_answer" and not a.sources
        assert a.text == "В документе и в Положении нет информации о стоимости обучения. Обратитесь в деканат."
        other = app.analyze(None, "", "Какая погода завтра?").answer  # тема не распознана — называем сам вопрос
        assert other.text == "В документе и в Положении нет информации по вопросу «Какая погода завтра?». Обратитесь в деканат."

    def test_reasons_from_different_clauses_are_listed_separately(self, app):
        a = ask(app, "study_certificate", STUDY, "Достаточно ли этой справки?", absent=["direction", "issue.date"])
        assert a.verdict == "missing" and {s.ref for s in a.sources} == {"3.2", "3.4"}
        assert "п. 3.2" in a.text and "п. 3.4" in a.text

    def test_other_document_types(self, app):
        a = ask(app, "academic_leave", LEAVE, "Всё ли верно оформлено в заявлении?", absent=["student_signature.signature"])
        assert a.verdict == "missing" and a.sources[0].ref == "4.3" and "подписи студента" in a.text
        a = ask(app, "practice_certificate", PRACTICE, "Подойдёт ли эта справка о практике?")
        assert a.verdict == "ok" and a.sources[0].ref == "5.2"
        a = ask(app, "practice_certificate", PRACTICE, "Чего не хватает в справке?", absent=["seal_signature.signature", "seal_signature.stamp"])
        assert "подписи и печати организации" in a.text.replace(" и ", " и ") or ("подписи" in a.text and "печати организации" in a.text)
        assert a.sources[0].ref == "5.3"

    @pytest.mark.parametrize("question", [
        "Достаточно ли этого заявления?", "Примет ли деканат моё заявление на академ?", "Заявление принимается к рассмотрению?",
        "Можно ли с таким заявлением уйти в академ?", "Нет ли ошибок в заявлении?", "Проверьте, пожалуйста, моё заявление",
    ])
    def test_phrasing_variants_are_recognised(self, app, question):
        a = ask(app, "academic_leave", LEAVE, question, absent=["reason"])
        assert a.intent == "completeness" and a.verdict == "missing" and "причины отпуска" in a.text

    def test_empty_question_means_check_the_document(self, app):
        spec = app.doc_types["study_certificate"]
        a = app.qa.answer("", spec, fields_from_truth(spec, STUDY))
        assert a.verdict == "ok" and a.intent == "completeness"

    def test_wrong_document_for_the_purpose(self, app):
        a = ask(app, "practice_certificate", PRACTICE, "Подойдёт ли эта справка для перевода из другого вуза?")
        assert a.verdict == "missing" and "справка об обучении" in a.text
        assert any(s.ref == "3.2" or s.ref == "7.2" for s in a.sources)
        # а справка об обучении для перевода — подходит
        assert ask(app, "study_certificate", STUDY, "Подойдёт ли эта справка для перевода из другого вуза?").verdict == "ok"

    def test_date_order_note(self, app):
        bad = dict(LEAVE, start_date="01.09.2025", end_date="01.09.2024")
        a = ask(app, "academic_leave", bad, "Всё ли в порядке?")
        assert any("раньше даты начала" in n for n in a.notes)


class TestFactsAndRules:
    def test_fact_question_answers_with_the_field(self, app):
        a = ask(app, "study_certificate", STUDY, "Какая форма обучения указана в справке?")
        assert a.verdict == "ok" and a.intent == "fact" and "очная" in a.text and a.sources[0].kind == "field"
        a = ask(app, "academic_leave", LEAVE, "С какой даты начинается отпуск?")
        assert "01.09.2024" in a.text
        a = ask(app, "academic_leave", LEAVE, "Когда заканчивается отпуск?")
        assert "31.08.2025" in a.text
        a = ask(app, "practice_certificate", PRACTICE, "Кто руководитель практики?")
        assert "Орлов Олег Олегович" in a.text

    def test_fact_about_a_missing_required_field_is_reported(self, app):
        a = ask(app, "study_certificate", STUDY, "Какая форма обучения указана в справке?", absent=["study_form"])
        assert a.verdict == "missing" and a.sources[0].ref == "3.2"

    @pytest.mark.parametrize("question, clause", [
        ("В течение какого времени деканат рассматривает заявления студентов?", "6.1"),
        ("Какой максимальный срок академического отпуска?", "4.1"),
        ("Сколько дней действительна справка об обучении?", "3.3"),
        ("Какие основания есть для академического отпуска?", "4.4"),
        ("В какой срок принимается решение о переводе?", "7.3"),
        ("Принимаются ли документы на иностранном языке?", "2.3"),
    ])
    def test_rule_questions_cite_the_clause(self, app, question, clause):
        for type_id, truth in (("study_certificate", STUDY), ("practice_certificate", PRACTICE), ("academic_leave", LEAVE)):
            a = ask(app, type_id, truth, question)
            assert a.verdict == "ok" and a.sources[0].ref == clause, (type_id, a.text)
            assert f"п. {clause} Положения" in a.text

    def test_rule_question_without_a_document(self, app):
        a = app.qa.answer("Сколько дней рассматривается заявление?", None, None)
        assert a.verdict == "ok" and a.sources[0].ref == "6.1"

    @pytest.mark.parametrize("question", [
        "Сколько стоит обучение в вузе?", "Предоставляется ли студентам общежитие?", "Какой размер стипендии?", "Когда начинается сессия?",
        "Есть ли в вузе столовая?", "Какой проходной балл на направление?", "Дают ли скидку на оплату обучения?",
    ])
    def test_honest_no_answer(self, app, question):
        a = ask(app, "study_certificate", STUDY, question)
        assert a.verdict == "no_answer" and not a.sources
        assert "нет информации" in a.text and "Обратитесь в деканат" in a.text

    def test_never_invents_a_clause(self, app):
        nums = {c.number for c in app.clauses}
        for q in ["Сколько дней действительна справка?", "Как оформить пропуск?", "Нужна ли медицинская справка?"]:
            a = app.qa.answer(q, None, None)
            assert all(s.ref in nums for s in a.sources)


class TestPhrasingsFromLiveTesting:
    """Формулировки, на которых секретарь ошибался при проверке вживую."""

    @pytest.mark.parametrize("question, clause", [
        ("Какие документы нужны для перевода из другого вуза?", "7.2"),
        ("Что нужно принести для перевода?", "7.2"),
        ("Принимаются ли документы на английском?", "2.3"),
        ("Кто подписывает справку о практике?", "5.3"),
        ("Нужна ли подпись на справке о практике?", "5.3"),
        ("Нужна ли медицинская справка при академическом отпуске?", "4.4"),
    ])
    def test_rule_questions(self, app, question, clause):
        a = app.analyze(None, "", question).answer
        assert a.verdict == "ok" and a.sources[0].ref == clause, a.text

    @pytest.mark.parametrize("question", ["Можно ли подать заявление онлайн?", "Как подать заявление на академ?", "Куда отправить справку?", "Как получить справку об обучении?"])
    def test_questions_about_the_procedure_do_not_demand_a_document(self, app, question):
        assert not app.qa.needs_document(question)
        assert "загрузите его фото" not in app.analyze(None, "", question).answer.text

    @pytest.mark.parametrize("question", ["Хватит ли этой справки для перевода?", "Примут ли мою справку?", "Можно ли подать эту справку для перевода?", "Подойдёт ли справка?"])
    def test_document_checks_still_need_the_document(self, app, question):
        assert app.qa.needs_document(question)
