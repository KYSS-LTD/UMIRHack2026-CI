"""Бонус «новый тип без кода»: документ нового вида добавляется только описанием полей в конфиге."""

import pytest
from PIL import Image, ImageDraw, ImageFont

from conftest import needs_ocr
from dekanat.tools import synth
from dekanat.config import parse_doc_type_yaml
from dekanat.pipeline import Dekanat

SCHOLARSHIP = """
id: scholarship_certificate
title: Справка о стипендии
short_title: Справка
genitive: справки о стипендии
aliases: [справка о стипендии, справка о выплате стипендии]
classify:
  keywords:
    - {phrase: справка о стипендии, weight: 7}
    - {phrase: размер стипендии, weight: 4}
fields:
  - id: student_name
    title: ФИО студента
    kind: person
    mention: [ФИО студента]
    labels: [ФИО студента, ФИО]
    missing_phrase: ФИО студента
  - id: amount
    title: Размер стипендии
    kind: number
    labels: [Размер стипендии, Сумма]
    missing_phrase: размера стипендии
  - id: pay_period
    title: Период выплаты
    kind: period
    labels: [Период выплаты]
    missing_phrase: периода выплаты
  - id: issue_date
    title: Дата выдачи
    kind: date
    labels: [Дата выдачи]
    missing_phrase: даты выдачи
"""

REGULATION_EXTRA = """
## 8. Справка о стипендии

8.1. Справка о стипендии должна содержать: ФИО студента, размер стипендии, период выплаты, дату выдачи.

8.2. Дата выдачи является обязательным реквизитом справки. Справка без даты выдачи не принимается.
"""


def render_doc(path, rows, title="СПРАВКА О СТИПЕНДИИ"):
    reg, bold = synth.find_font_families()[0]
    img = Image.new("RGB", (1240, 900), "white")
    d = ImageDraw.Draw(img)
    d.text((620, 120), title, font=ImageFont.truetype(str(bold), 40), fill="black", anchor="ma")
    y = 230
    for label, value in rows:
        d.text((118, y), f"{label}: {value}", font=ImageFont.truetype(str(reg), 30), fill="black")
        y += 62
    img.save(path)


def test_config_for_a_new_type_is_valid():
    spec = parse_doc_type_yaml(SCHOLARSHIP)
    assert [f.id for f in spec.fields] == ["student_name", "amount", "pay_period", "issue_date"]


@needs_ocr
def test_new_document_type_works_without_code_changes(tmp_settings, tmp_path):
    (tmp_settings.doc_types_dir / "scholarship_certificate.yaml").write_text(SCHOLARSHIP, encoding="utf-8")
    reg = tmp_settings.path("regulation.path")
    reg.write_text(reg.read_text(encoding="utf-8") + REGULATION_EXTRA, encoding="utf-8")
    app = Dekanat(tmp_settings)
    assert "scholarship_certificate" in app.doc_types

    full = tmp_path / "full.png"
    render_doc(full, [("ФИО студента", "Кузнецова Мария Андреевна"), ("Размер стипендии", "3500"), ("Период выплаты", "с 01.09.2024 по 31.12.2024"), ("Дата выдачи", "10.01.2025")])
    res = app.analyze(full.read_bytes(), "full.png", "Хватит ли этой справки?", None)
    f = {x.id: x for x in res.fields}
    assert res.doc_type.id == "scholarship_certificate" and res.how == "auto"
    assert f["student_name"].value == "Кузнецова Мария Андреевна" and f["amount"].value == "3500"
    assert f["pay_period"].normalized == {"start": "2024-09-01", "end": "2024-12-31"} and f["issue_date"].value == "10.01.2025"
    assert res.answer.verdict == "ok" and res.answer.sources[0].ref == "8.1"

    nodate = tmp_path / "nodate.png"
    render_doc(nodate, [("ФИО студента", "Кузнецова Мария Андреевна"), ("Размер стипендии", "3500"), ("Период выплаты", "с 01.09.2024 по 31.12.2024")])
    res = app.analyze(nodate.read_bytes(), "nodate.png", "Хватит ли этой справки?", None)
    assert res.answer.verdict == "missing"
    assert res.answer.text == "Не хватает даты выдачи. Это поле обязательно по п. 8.2 Положения."

    # старые типы продолжают работать
    other = app.analyze((tmp_settings.root / "data" / "samples" / "sample_01.png").read_bytes(), "s.png", "", None)
    assert other.doc_type.id == "study_certificate"
