"""Этапы конвейера по ТЗ: загрузка -> проверка формата -> OCR -> тип -> извлечение -> валидация -> поиск по Положению -> ответ."""

import io

import pytest
from PIL import Image

from conftest import needs_ocr
from dekanat.extract.validation import doubt_reason, validate_fields
from dekanat.ocr.formats import SUPPORTED_FORMATS, check_format, detect_format
from dekanat.tools.evaluate import fields_from_truth
from dekanat.ocr.preprocess import UnsupportedFile


def image_bytes(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, fmt)
    return buf.getvalue()


# ============================================================================ проверка формата
def test_supported_formats_are_exactly_jpg_jpeg_png_pdf():
    assert SUPPORTED_FORMATS == ("JPG", "JPEG", "PNG", "PDF")
    assert detect_format(image_bytes("JPEG")) == "jpeg" and detect_format(image_bytes("PNG")) == "png"
    assert detect_format(b"%PDF-1.7\n...") == "pdf" and detect_format(b"\n\n%PDF-1.4") == "pdf"
    for fmt in ("BMP", "GIF", "TIFF", "WEBP"):
        assert detect_format(image_bytes(fmt)) is None, fmt


@pytest.mark.parametrize("fmt, name", [("BMP", "a.bmp"), ("GIF", "a.gif"), ("TIFF", "a.tif"), ("WEBP", "a.webp"), ("WEBP", "renamed.png")])
def test_other_image_formats_are_rejected_even_with_a_misleading_name(fmt, name):
    with pytest.raises(UnsupportedFile, match="JPG, JPEG, PNG и PDF"):
        check_format(image_bytes(fmt), name)


def test_format_is_taken_from_content_not_from_the_extension():
    assert check_format(image_bytes("JPEG"), "photo.png") == "jpeg"  # телефоны нередко путают расширения


def test_empty_and_oversized_files():
    with pytest.raises(UnsupportedFile, match="пустой"):
        check_format(b"", "a.png")
    with pytest.raises(UnsupportedFile, match="больше 1 МБ"):
        check_format(image_bytes("PNG") + b"0" * (2 * 1024 * 1024), "a.png", max_bytes=1024 * 1024)


def test_pipeline_rejects_unsupported_format_before_ocr(app, monkeypatch):
    called = []
    monkeypatch.setattr(app.runner, "run", lambda *a, **k: called.append(1))
    with pytest.raises(UnsupportedFile):
        app.analyze(image_bytes("BMP"), "scan.bmp", "Хватит ли справки?")
    assert called == []  # до OCR дело не дошло


def test_web_api_reports_unsupported_format(tmp_settings):
    from fastapi.testclient import TestClient

    from dekanat.web.app import create_app

    c = TestClient(create_app(tmp_settings))
    r = c.post("/api/analyze", files={"file": ("scan.webp", image_bytes("WEBP"), "image/webp")}, data={"question": "Что не так?"})
    assert r.status_code == 400 and "JPG, JPEG, PNG и PDF" in r.json()["detail"]


# ============================================================================ валидация полей
STUDY = {
    "student_name": "Иванов Иван Иванович", "university": "Южный университет", "direction": "09.03.01 Информатика", "study_form": "очная",
    "study_period": "01.09.2022 – 30.06.2026", "issue.number": "458", "issue.date": "15.05.2024", "seal_signature.signature": True, "seal_signature.stamp": False,
}


def test_every_field_gets_a_detection_status_and_doubt_reason(app):
    spec = app.doc_types["study_certificate"]
    fields = fields_from_truth(spec, STUDY)
    by = {f.id: f for f in fields}
    by["university"].confidence = 0.3
    by["direction"].method = "llm"
    by["student_name"].found, by["student_name"].value = False, ""
    validate_fields(spec, fields)
    assert by["student_name"].doubt == "не найдено в тексте документа"
    assert "неуверенно" in by["university"].doubt and "30 %" in by["university"].doubt
    assert "языковой моделью" in by["direction"].doubt
    assert by["study_form"].doubt == "" and by["study_form"].to_dict()["confirmed"] is True
    stamp = next(p for p in by["seal_signature"].parts if p.id == "stamp")
    assert stamp.doubt == "графический элемент не обнаружен на изображении" and "печать" in by["seal_signature"].doubt
    d = by["university"].to_dict()
    assert {"title", "normalized", "evidence", "status", "confidence", "doubt", "confirmed"} <= set(d) and d["confirmed"] is False


def test_validation_does_not_change_values_or_answers(app):
    spec = app.doc_types["study_certificate"]
    a = fields_from_truth(spec, STUDY)
    b = fields_from_truth(spec, STUDY)
    validate_fields(spec, b)
    assert [(f.value, f.found, f.normalized) for f in a] == [(f.value, f.found, f.normalized) for f in b]
    q = "Хватит ли этой справки для перевода на 2 курс?"
    assert app.qa.answer(q, spec, a).text == app.qa.answer(q, spec, b).text


def test_implausible_dates_are_flagged():
    assert "диапазона" in doubt_reason("date", True, 0.9, "label", "1812-09-07")
    assert doubt_reason("date", True, 0.9, "label", "2024-05-15") == ""
    assert "косвенно" in doubt_reason("org", True, 0.9, "header")


@needs_ocr
def test_real_document_goes_through_all_stages(app, samples):
    stages = []
    res = app.analyze((samples / "sample_02.png").read_bytes(), "sample_02.png", "Хватит ли этой справки для перевода на 2 курс?",
                      progress=lambda stage, label, frac: stages.append((stage, label)))
    labels = [label for _, label in stages]
    assert labels[0] == "Проверяю формат файла" and "Проверяю значения полей" in labels
    order = [s for s, _ in stages]
    for a, b in (("load", "ocr"), ("ocr", "classify"), ("classify", "extract"), ("extract", "answer")):
        assert order.index(a) < order.index(b)
    assert all(isinstance(f.doubt, str) for f in res.fields)
    missing = [f for f in res.fields if f.status == "missing"]
    assert all(f.doubt for f in missing)


def test_templates_are_stored_in_the_internal_templates_folder():
    from dekanat.config import load_settings

    st = load_settings()
    assert st.path("storage.templates_dir").name == "templates" and st.path("storage.templates_dir").parent == st.root
