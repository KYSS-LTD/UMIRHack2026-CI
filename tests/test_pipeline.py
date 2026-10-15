"""Весь путь: изображение -> OCR -> поля -> ответ (нужен Tesseract)."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from conftest import needs_ocr
from dekanat.tools.evaluate import _unit_value, unit_correct
from dekanat.ocr import preprocess as pp

pytestmark = needs_ocr


def read(path: Path) -> bytes:
    return path.read_bytes()


def test_samples_give_the_expected_verdicts(app, samples):
    manifest = json.loads((samples / "manifest.json").read_text(encoding="utf-8"))
    expected = {
        "sample_01.png": "ok", "sample_02.png": "missing", "sample_03.jpg": "ok", "sample_04.png": "ok",
        "sample_05.jpg": "missing", "sample_06.png": "ok", "sample_07.jpg": "missing", "sample_08.png": "no_answer",
        "sample_09.jpg": "ok",  # приказ не из вуза: ответ по тексту самого документа
    }
    wrong = []
    for item in manifest:
        res = app.analyze(read(samples / item["file"]), item["file"], item["question"], None)
        if item["file"] == "sample_10.jpg":
            # снимок размытый и тёмный: честного «в порядке» быть не может, а совет переснять обязан быть
            if res.answer.verdict == "ok" or not res.photo_quality["retake"]:
                wrong.append((item["file"], res.answer.verdict, res.photo_quality))
        elif res.answer.verdict != expected[item["file"]]:
            wrong.append((item["file"], res.answer.verdict, res.answer.text))
    assert not wrong, wrong


def test_missing_date_and_stamp_example(app, samples):
    res = app.analyze(read(samples / "sample_02.png"), "sample_02.png", "Хватит ли этой справки для перевода на 2 курс?", None)
    assert res.doc_type.id == "study_certificate" and res.how == "auto"
    assert res.answer.text == "Не хватает даты выдачи и печати вуза. Эти поля обязательны по п. 3.4 Положения."
    issue = next(f for f in res.fields if f.id == "issue")
    assert issue.status == "partial" and issue.parts[0].found and not issue.parts[1].found
    # у каждого найденного поля есть рамка на исходном снимке — для подсветки
    h, w = res.original_pages[0].shape[:2]
    for f in res.fields:
        for b in f.boxes:
            assert 0 <= b.x0 < b.x1 <= w and 0 <= b.y0 < b.y1 <= h


def test_manual_type_overrides_auto(app, samples):
    res = app.analyze(read(samples / "sample_01.png"), "x.png", "", "practice_certificate")
    assert res.how == "manual" and res.doc_type.id == "practice_certificate"


def test_clean_scans_and_light_photos_are_read_accurately(app, synth_set):
    labels = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((synth_set / "labels").glob("*.json"))]
    good = total = 0
    types_ok = 0
    for lab in labels:
        res = app.analyze(read(synth_set / lab["image"]), lab["image"], "", None).to_dict(preview=False)
        types_ok += res["doc_type"]["id"] == lab["doc_type"]
        fields = res["fields"] if res["doc_type"]["id"] == lab["doc_type"] else app.analyze(read(synth_set / lab["image"]), "", "", lab["doc_type"]).to_dict(preview=False)["fields"]
        for uid, exp in lab["units"].items():
            total += 1
            good += unit_correct(exp, *_unit_value(fields, uid))
    assert types_ok == len(labels)
    assert good / total >= 0.88, f"точность полей {good}/{total}"


def test_pdf_input(app, samples, tmp_path):
    pdf = tmp_path / "doc.pdf"
    Image.open(samples / "sample_04.png").convert("RGB").save(pdf, "PDF", resolution=150)
    res = app.analyze(read(pdf), "doc.pdf", "Достаточно ли этого заявления?", None)
    assert res.doc_type.id == "academic_leave" and res.answer.verdict == "ok"


def test_unreadable_file_is_a_clear_error(app):
    from dekanat.ocr.preprocess import UnsupportedFile

    with pytest.raises(UnsupportedFile, match="Не удалось"):
        app.analyze(b"not an image", "x.jpg", "вопрос", None)


def test_blank_image_gives_no_crash_and_a_hint(app):
    blank = np.full((900, 700, 3), 255, np.uint8)
    ok, buf = cv2.imencode(".png", blank)
    res = app.analyze(buf.tobytes(), "blank.png", "Всё ли в порядке?", None)
    assert res.doc_type is None and res.warnings
    assert res.answer.verdict == "no_answer"


def test_question_without_a_document(app):
    res = app.analyze(None, "", "В течение какого времени деканат рассматривает заявления?", None)
    assert res.answer.verdict == "ok" and res.answer.sources[0].ref == "6.1" and not res.fields


def test_ocr_result_is_cached(app, samples):
    data = read(samples / "sample_01.png")
    app.analyze(data, "a.png", "", None)
    again = app.analyze(data, "a.png", "Какая форма обучения?", None)
    assert again.timings.get("ocr_cached") is True


# ----------------------------------------------------------------- плохие снимки


def rotate_image(img: np.ndarray, angle: float) -> np.ndarray:
    return pp.rotate_expand(img, angle, (140, 130, 120))[0]


@pytest.mark.parametrize("angle", [-28, -12, 9, 25])
def test_tilted_photo_is_deskewed(app, samples, angle):
    img = cv2.imdecode(np.fromfile(str(samples / "sample_01.png"), np.uint8), cv2.IMREAD_COLOR)
    tilted = rotate_image(img, angle)
    ok, buf = cv2.imencode(".jpg", tilted, [cv2.IMWRITE_JPEG_QUALITY, 70])
    res = app.analyze(buf.tobytes(), "tilted.jpg", "Хватит ли этой справки для перевода?", None)
    assert res.doc_type is not None and res.doc_type.id == "study_certificate"
    names = {f.id: f for f in res.fields}
    assert names["student_name"].found and names["study_period"].found
    # рамка найденного значения на исходном (наклонённом) снимке лежит там, где в нём действительно текст
    b = names["student_name"].boxes[0]
    crop = tilted[int(b.y0):int(b.y1), int(b.x0):int(b.x1)]
    assert crop.size and (cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) < 110).mean() > 0.03


def test_shadow_and_glare(app, samples):
    img = cv2.imdecode(np.fromfile(str(samples / "sample_01.png"), np.uint8), cv2.IMREAD_COLOR).astype(np.float32)
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    shade = 1 - 0.55 * (xx / w)  # тень слева направо
    glare = np.exp(-(((xx - w * 0.6) / (w * 0.12)) ** 2 + ((yy - h * 0.12) / (h * 0.03)) ** 2)) * 120
    bad = np.clip(img * shade[..., None] + glare[..., None], 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", bad)
    res = app.analyze(buf.tobytes(), "shadow.jpg", "", None)
    fields = {f.id: f for f in res.fields}
    assert res.doc_type.id == "study_certificate" and fields["student_name"].found and fields["study_form"].found


def test_low_resolution(app, samples):
    img = cv2.imdecode(np.fromfile(str(samples / "sample_01.png"), np.uint8), cv2.IMREAD_COLOR)
    small = cv2.resize(img, None, fx=0.45, fy=0.45, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 60])
    res = app.analyze(buf.tobytes(), "small.jpg", "", None)
    fields = {f.id: f for f in res.fields}
    assert res.doc_type.id == "study_certificate" and fields["student_name"].found and fields["study_period"].found


def test_upside_down_page(app, samples):
    img = cv2.imdecode(np.fromfile(str(samples / "sample_01.png"), np.uint8), cv2.IMREAD_COLOR)
    ok, buf = cv2.imencode(".png", cv2.rotate(img, cv2.ROTATE_180))
    res = app.analyze(buf.tobytes(), "flipped.png", "", None)
    assert res.pages[0].rotation == 180 and res.doc_type is not None and res.doc_type.id == "study_certificate"
