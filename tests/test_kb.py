from dekanat.knowledge_base import ClauseIndex, RequirementModel, load_regulation, load_synonym_groups, parse_regulation_text

from conftest import ROOT


def test_sample_regulation_is_parsed_with_sections():
    clauses = load_regulation(ROOT / "data" / "regulations" / "polozhenie.md")
    nums = [c.number for c in clauses]
    assert len(clauses) == 25 and nums[0] == "1.1" and "3.4" in nums and nums[-1] == "7.3"
    c34 = next(c for c in clauses if c.number == "3.4")
    assert c34.section == ["Справка об обучении"] and "печать вуза" in c34.text and c34.label == "п. 3.4"


def test_numbering_styles():
    text = """
Раздел 1. Общие положения
1.1. Первый пункт.
п. 1.2 Второй пункт, продолжение
на следующей строке.
1.3) Третий пункт:
а) подпункт один;
б) подпункт два.

2. Документы
2.1. Четвёртый пункт.
"""
    clauses = parse_regulation_text(text)
    assert [c.number for c in clauses] == ["1.1", "1.2", "1.3", "2.1"]
    assert "на следующей строке" in clauses[1].text
    assert "подпункт два" in clauses[2].text  # подпункты остаются в своём пункте
    assert clauses[3].section == ["Документы"]


def test_flat_numbering_without_subsections():
    clauses = parse_regulation_text("1. Студент обязан посещать занятия.\n2. Студент обязан сдавать сессию.\n")
    assert [c.number for c in clauses] == ["1", "2"]


def test_year_is_not_a_clause_number():
    clauses = parse_regulation_text("1.1. Положение действует.\n2024 год считается отчётным.\n")
    assert [c.number for c in clauses] == ["1.1"] and "2024 год" in clauses[0].text


def test_unnumbered_text_falls_back_to_paragraphs():
    clauses = parse_regulation_text("Первый абзац о справках и заявлениях студентов.\n\nВторой абзац о сроках рассмотрения документов.")
    assert [c.number for c in clauses] == ["§1", "§2"] and clauses[0].label == "§1"


def test_requirements_follow_the_regulation_text(app):
    study = app.requirements.for_type("study_certificate")
    assert study.best("issue.date").clause.number == "3.4"  # обязательность («без этих реквизитов не принимается») сильнее перечня
    assert study.best("seal_signature.stamp").clause.number == "3.4"
    assert study.best("direction").clause.number == "3.2"
    leave = app.requirements.for_type("academic_leave")
    assert leave.best("student_signature.signature").clause.number == "4.3"
    assert leave.best("reason").clause.number == "4.2"
    practice = app.requirements.for_type("practice_certificate")
    assert practice.best("seal_signature.signature").clause.number == "5.3"
    assert practice.best("supervisor").clause.number == "5.2"  # «подписью руководителя практики» — не требование ФИО


def test_requirements_scope_excludes_other_document_types(app):
    study = app.requirements.for_type("study_certificate")
    assert all(r.clause.number.split(".")[0] in ("2", "3") for reqs in study.by_unit.values() for r in reqs)


def test_search_finds_the_right_clause(app):
    idx = app.index
    assert idx.search("В течение какого времени деканат рассматривает заявления?")[0].clause.number == "6.1"
    assert idx.search("Сколько дней действует справка об обучении?")[0].clause.number == "3.3"
    assert idx.search("Принимаются ли документы на иностранном языке?")[0].clause.number == "2.3"


def test_synonyms_loaded():
    groups = load_synonym_groups(ROOT / "config" / "synonyms.yaml")
    assert any({"срок", "период"} <= g or len(g) > 3 for g in groups)
    assert load_synonym_groups(ROOT / "config" / "synonyms.yaml", "field_groups")
