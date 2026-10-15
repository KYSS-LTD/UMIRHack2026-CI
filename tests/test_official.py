"""results.json в формате организаторов (version 1.0): ключи типов, нормализация, проверка комплектности по пунктам Положения, вопросы, команда `results`."""

import json
import shutil

import pytest

from conftest import ROOT, needs_ocr
from dekanat.cli import main
from dekanat.config import ConfigError, parse_doc_type
from dekanat.models import FieldResult, PartResult
from dekanat.tools.official import NOT_FOUND, OfficialExporter, OField, check_report, field_value, official_fields

# ключи из README набора организаторов
KEYS = {
    "study_certificate": ["student_name", "university", "program", "study_form", "study_start", "study_end", "document_number", "issue_date", "signature_present", "seal_present"],
    "academic_leave_application": ["student_name", "group", "course", "leave_reason", "leave_start", "leave_end", "application_date", "signature_present"],
    "internship_certificate": ["student_name", "organization", "internship_start", "internship_end", "supervisor_name", "grade", "document_number", "issue_date", "signature_present", "seal_present"],
}


@pytest.fixture(scope="module")
def exporter(app):
    return OfficialExporter(app)


def test_every_type_describes_exactly_the_organizers_keys(app):
    seen = {}
    for dt in app.doc_types.values():
        seen[dt.official["type"]] = [f.key for f in official_fields(dt)]
    assert {k: sorted(v) for k, v in seen.items()} == {k: sorted(v) for k, v in KEYS.items()}


def test_unknown_official_type_is_refused_with_a_hint(exporter):
    with pytest.raises(ValueError, match="official"):
        exporter.spec_for("passport")


# ------------------------------------------------------------------ конфиг
BASE = {"id": "t", "title": "Т", "fields": [{"id": "a", "title": "А", "kind": "text", "labels": ["А"]}, {"id": "p", "title": "П", "kind": "period", "labels": ["П"]}]}


def spec(official):
    return parse_doc_type({**BASE, "official": official}, "t.yaml")


def test_official_block_is_optional():
    assert parse_doc_type(BASE, "t.yaml").official == {}


def test_official_block_is_checked():
    ok = spec({"type": "x", "fields": [{"unit": "a", "key": "a_key", "title": "А"}, {"unit": "p", "part": "start", "key": "p_start", "title": "Начало"}]})
    assert [f["key"] for f in ok.official["fields"]] == ["a_key", "p_start"]
    for bad, message in [
        ({"fields": []}, "непустым списком"),
        ({"fields": [{"unit": "a", "key": "k"}]}, "unit, key и title"),
        ({"fields": [{"unit": "zzz", "key": "k", "title": "Т"}]}, "неизвестную единицу"),
        ({"fields": [{"unit": "a", "key": "k", "title": "Т"}, {"unit": "p", "key": "k", "title": "Т2"}]}, "повторяющиеся key"),
        ({"fields": [{"unit": "p", "part": "middle", "key": "k", "title": "Т"}]}, "start или end"),
    ]:
        with pytest.raises(ConfigError, match=message):
            spec(bad)


def test_period_labels_only_for_period_fields():
    with pytest.raises(ConfigError, match="только у kind=period"):
        parse_doc_type({**BASE, "fields": [{"id": "a", "title": "А", "kind": "text", "labels": ["А"], "start_labels": ["Начало"]}]}, "t.yaml")


# ------------------------------------------------------------------ нормализация значений
def fr(id_, kind, found, value="", norm=None, parts=None):
    return FieldResult(id_, id_, kind, found, value, norm, 0.9, [], "x", "", parts or [])


def pr(id_, found, value="", norm=None):
    return PartResult(id_, id_, found, value, norm, 0.9, [], "")


def test_values_are_normalized_like_the_readme_asks():
    fields = [
        fr("name", "person", True, "Иванов Иван", "Иванов Иван"),
        fr("born", "date", True, "01.09.2026", "2026-09-01"),
        fr("form", "choice", True, "очная", "очная"),
        fr("period", "period", False, "31.08.2026", {"end": "2026-08-31"}),  # найдена только дата окончания
        fr("gc", "composite", True, "", None, [pr("group", True, "ЖР-25-1", "ЖР-25-1"), pr("course", True, "3", 3)]),
        fr("marks", "marks", False, "нет", None, [pr("signature", True, "есть", True), pr("stamp", False, "нет", False)]),
        fr("empty", "text", False),
    ]
    get = lambda unit, key, **kw: field_value(OField(unit, key, key, kw.get("part"), kw.get("mark", False)), fields)
    assert get("name", "n") == "Иванов Иван" and get("born", "d") == "2026-09-01" and get("form", "f") == "очная"
    assert get("period", "s", part="start") == NOT_FOUND and get("period", "e", part="end") == "2026-08-31"  # пустое начало не подменяется датой окончания
    assert get("gc.group", "g") == "ЖР-25-1" and get("gc.course", "c") == "3"  # курс — строка с целым числом
    assert get("marks.signature", "sig", mark=True) is True and get("marks.stamp", "seal", mark=True) is False
    assert get("empty", "x") == NOT_FOUND and get("missing_unit", "y") == NOT_FOUND and get("missing_unit", "z", mark=True) is False


def test_dates_in_dmy_become_iso():
    fields = [fr("d", "date", True, "07.10.2026", "07.10.2026")]
    assert field_value(OField("d", "k", "k", None, False), fields) == "2026-10-07"


# ------------------------------------------------------------------ самопроверка формата
def good_report():
    return {"version": "1.0", "documents": [{
        "document_id": "d1", "document_type": "study_certificate", "fields": {"student_name": "А Б В", "issue_date": "2026-01-02", "signature_present": True, "seal_present": False},
        "validation": {"status": "missing_fields", "missing_fields": ["seal_present"], "sources": ["rule:3.3"]},
        "answers": [{"question_id": "q1", "answerable": True, "value": "missing_fields", "answer": "Не хватает печати.", "sources": ["rule:3.3"]},
                    {"question_id": "q2", "answerable": False, "value": None, "answer": "Нет информации.", "sources": []}]}]}


def test_check_report_accepts_a_valid_report():
    assert check_report(good_report()) == []


@pytest.mark.parametrize("mutate, expect", [
    (lambda r: r["documents"][0].pop("answers"), "ключи документа"),
    (lambda r: r["documents"][0]["fields"].update(issue_date="02.01.2026"), "ГГГГ-ММ-ДД"),
    (lambda r: r["documents"][0]["validation"].update(status="document_ok"), "не согласован"),
    (lambda r: r["documents"][0]["answers"][0].update(sources=[]), "пустые sources"),
    (lambda r: r["documents"][0]["answers"][1].update(value="x"), "должен быть null"),
    (lambda r: r["documents"][0]["answers"][0].update(sources=["rule:3.3", "rule:3.3"]), "уникальные"),
    (lambda r: r["documents"][0]["answers"][0].update(answer=" "), "непустой текст"),
    (lambda r: r.update(version="2.0"), "version"),
])
def test_check_report_finds_each_kind_of_problem(mutate, expect):
    report = good_report()
    mutate(report)
    assert any(expect in p for p in check_report(report))


# ------------------------------------------------------------------ сквозной прогон на образцах проекта
def sample_set(tmp_path):
    """Мини-набор «как у организаторов»: documents.json, questions.json и три образца из data/samples."""
    docs, qs = [], []
    plan = [("sample_01.png", "study_certificate"), ("sample_02.png", "study_certificate"), ("sample_04.png", "academic_leave_application"), ("sample_06.png", "internship_certificate")]
    (tmp_path / "documents").mkdir(parents=True, exist_ok=True)
    for i, (name, typ) in enumerate(plan, 1):
        shutil.copy(ROOT / "data" / "samples" / name, tmp_path / "documents" / name)
        did = f"t-{i:03d}"
        docs.append({"document_id": did, "document_type": typ, "image": f"documents/{name}"})
        key = {"study_certificate": "ФИО студента", "academic_leave_application": "ФИО студента", "internship_certificate": "ФИО руководителя практики"}[typ]
        qs += [{"question_id": f"{did}-Q1", "document_id": did, "question": "Содержит ли документ все обязательные сведения по Положению?"},
               {"question_id": f"{did}-Q2", "document_id": did, "question": f"Укажи значение поля «{key}»."},
               {"question_id": f"{did}-Q3", "document_id": did, "question": "Кто принимает окончательное решение о принятии документа?"},
               {"question_id": f"{did}-Q4", "document_id": did, "question": "Какова стоимость обучения за следующий семестр?"}]
    (tmp_path / "documents.json").write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "questions.json").write_text(json.dumps(qs, ensure_ascii=False), encoding="utf-8")
    return tmp_path


@needs_ocr
def test_results_command_writes_a_valid_report(tmp_path, monkeypatch, capsys):
    data = sample_set(tmp_path / "set")
    out = tmp_path / "results.json"
    assert main(["results", "--data", str(data), "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert check_report(report) == [] and "замечание" not in capsys.readouterr().out
    assert [d["document_id"] for d in report["documents"]] == ["t-001", "t-002", "t-003", "t-004"]
    for d in report["documents"]:
        assert list(d["fields"]) == KEYS[d["document_type"]] or sorted(d["fields"]) == sorted(KEYS[d["document_type"]])
        assert [a["question_id"] for a in d["answers"]] == [f"{d['document_id']}-Q{i}" for i in (1, 2, 3, 4)]
        q1, q2, q3, q4 = d["answers"]
        assert q1["value"] == d["validation"]["status"] and q1["sources"] == d["validation"]["sources"] and q1["answerable"] is True
        assert q4["answerable"] is False and q4["value"] is None and q4["sources"] == []  # стоимости обучения нет ни в документе, ни в Положении
        assert q2["sources"] in ([], [f"field:{k}" for k in d["fields"] if q2["sources"] == [f"field:{k}"]])
    ok, no_stamp = report["documents"][0], report["documents"][1]
    assert ok["validation"]["status"] == "document_ok" and ok["fields"]["signature_present"] is True and ok["fields"]["seal_present"] is True
    assert no_stamp["validation"]["status"] == "missing_fields" and "seal_present" in no_stamp["validation"]["missing_fields"]
    assert all(s.startswith("rule:") for s in no_stamp["validation"]["sources"]) and no_stamp["validation"]["sources"]


def test_results_command_explains_a_wrong_folder(tmp_path, capsys):
    assert main(["results", "--data", str(tmp_path), "--out", str(tmp_path / "r.json")]) == 1
    assert "documents.json" in capsys.readouterr().err


def test_a_clause_saying_a_field_is_not_required_is_not_a_requirement():
    from dekanat.knowledge_base.requirements import cue_level

    assert cue_level("Печать вуза и подпись сотрудника деканата для предварительной проверки заявления не требуются.") == 0
    assert cue_level("Обязательна подпись студента.") == 3
    assert cue_level("Обязательные текстовые поля: ФИО студента, группа.") == 3
    assert cue_level("Заявление должно содержать подпись.") == 2
    assert cue_level("Нотариальное заверение не требуется, но обязательна подпись студента.") == 3
