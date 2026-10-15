"""Определение снимков плохого качества: по пикселям (до OCR), по результату OCR, через API и в ответе проверки."""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import ROOT, needs_ocr
from dekanat.models import OcrPage, Word
from dekanat.ocr import photo
from dekanat.web.app import create_app


# ------------------------------------------------------------------ синтетические снимки
def page_image(w=1400, h=1800, paper=255, ink=20):
    """Белый лист с несколькими строками латинского текста: достаточно, чтобы оценить чёткость и яркость."""
    img = np.full((h, w, 3), paper, np.uint8)
    for k in range(18):
        cv2.putText(img, "STUDENT CERTIFICATE NUMBER 2172", (110, 160 + k * 85), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (ink,) * 3, 3, cv2.LINE_AA)
    return img


def codes(res, severity=None):
    return {i["code"] for i in res["issues"] if severity is None or i["severity"] == severity}


def test_a_sharp_clear_page_is_good():
    res = photo.assess_image(page_image())
    assert res["level"] == "good" and res["issues"] == [] and res["retake"] is False


def test_heavy_blur_is_found_and_asks_to_retake():
    res = photo.assess_image(cv2.GaussianBlur(page_image(), (0, 0), 9))
    assert "blur" in codes(res, "bad") and res["retake"] is True and res["level"] == "poor"
    issue = next(i for i in res["issues"] if i["code"] == "blur")
    assert "размыт" in issue["title"].lower() and "снимите" in issue["hint"].lower()


def test_dark_and_faded_photos():
    dark = photo.assess_image((page_image() * 0.3).astype(np.uint8))
    assert "dark" in codes(dark, "bad") and "faded" not in codes(dark)  # тёмный снимок уже объясняет низкий контраст
    faded = photo.assess_image(page_image(paper=200, ink=170))
    assert "faded" in codes(faded) and "dark" not in codes(faded)


def test_low_resolution_is_found():
    res = photo.assess_image(cv2.resize(page_image(), (420, 540), interpolation=cv2.INTER_AREA))
    assert "low_res" in codes(res, "bad")


def test_glare_on_a_photo_but_not_on_a_white_scan():
    photo_like = page_image(paper=205)
    cv2.circle(photo_like, (900, 700), 220, (255, 255, 255), -1)
    assert "glare" in codes(photo.assess_image(photo_like))
    scan = page_image(paper=255)
    cv2.circle(scan, (900, 700), 220, (255, 255, 255), -1)
    assert "glare" not in codes(photo.assess_image(scan))  # на белом скане «блика» быть не может: белая бумага — это норма


def test_every_issue_has_a_human_title_and_a_hint():
    for code, (title, hint) in photo.TEXTS.items():
        assert title and hint and title[0].isupper() and hint.endswith("."), code


# ------------------------------------------------------------------ по результату OCR
def fake_page(n_words=40, conf=90.0, score=85.0, h=22.0, w=1400, hh=1800, angle=0.0, x_start=120.0):
    words = [Word("слово", conf, x_start + (i % 8) * 120, 100 + (i // 8) * 60, x_start + (i % 8) * 120 + 100, 100 + (i // 8) * 60 + h) for i in range(n_words)]
    q = {"score": score, "mean_conf": conf, "wordlike": 0.9, "words": n_words, "median_h": h}
    return OcrPage(0, w, hh, words, [], np.eye(3), (w, hh), quality=q, deskew_angle=angle)


def test_confident_reading_cancels_pixel_complaints():
    blurry = cv2.GaussianBlur(page_image(), (0, 0), 9)
    res = photo.assess_page(blurry, fake_page(conf=92, score=88))
    assert res["level"] == "good" and res["issues"] == []  # если текст прочитан уверенно, придираться не к чему


def test_poor_reading_plus_blur_is_a_retake():
    blurry = cv2.GaussianBlur(page_image(), (0, 0), 9)
    res = photo.assess_page(blurry, fake_page(conf=50, score=30))
    assert res["retake"] is True and "blur" in codes(res, "bad")
    assert res["score"] < 40


def test_poor_reading_of_a_sharp_page_is_reported_without_a_cause():
    res = photo.assess_page(page_image(), fake_page(conf=50, score=30))
    assert codes(res, "bad") == {"low_ocr"}


def test_almost_no_text_is_a_retake():
    res = photo.assess_page(page_image(), fake_page(n_words=3, conf=60, score=10))
    assert "empty" in codes(res, "bad") and res["retake"] is True


def test_words_at_the_frame_edge_mean_a_cropped_sheet():
    res = photo.assess_page(page_image(), fake_page(x_start=0.0))
    assert "cropped" in codes(res)
    assert "cropped" not in codes(photo.assess_page(page_image(), fake_page(x_start=120.0)))


def test_strong_tilt_is_a_hint_only_when_reading_is_weak():
    weak = photo.assess_page(page_image(), fake_page(conf=60, score=50, angle=30.0))
    assert "tilt" in codes(weak)
    strong = photo.assess_page(page_image(), fake_page(conf=92, score=90, angle=30.0))
    assert "tilt" not in codes(strong)  # лист уже выпрямлен, текст читается


def test_document_summary_takes_the_worst_page():
    good = photo.assess_page(page_image(), fake_page())
    bad = photo.assess_page(cv2.GaussianBlur(page_image(), (0, 0), 9), fake_page(conf=50, score=30))
    doc = photo.assess_document([good, bad])
    assert doc["level"] == "poor" and doc["retake"] is True
    blur = next(i for i in doc["issues"] if i["code"] == "blur")
    assert blur["pages"] == [1]
    assert photo.assess_document([])["level"] == "good"
    line = photo.summary_line(doc)
    assert line.startswith("Снимок плохого качества") and "размыт" in line
    assert photo.summary_line(photo.assess_document([good])) == ""


# ------------------------------------------------------------------ API
@pytest.fixture()
def client(tmp_settings):
    return TestClient(create_app(tmp_settings))


def png(img) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


def test_quality_endpoint_warns_before_sending(client):
    res = client.post("/api/quality", files={"file": ("blur.png", png(cv2.GaussianBlur(page_image(), (0, 0), 9)), "image/png")})
    assert res.status_code == 200
    body = res.json()
    assert body["retake"] is True and any(i["code"] == "blur" for i in body["issues"])
    ok = client.post("/api/quality", files={"file": ("ok.png", png(page_image()), "image/png")}).json()
    assert ok["level"] == "good" and ok["issues"] == []


def test_quality_endpoint_skips_pdf_and_refuses_junk(client):
    pdf = (ROOT / "data" / "samples" / "manifest.json")  # любой не-PDF файл с расширением .pdf формат проверку не пройдёт
    junk = client.post("/api/quality", files={"file": ("x.pdf", b"not a pdf at all", "application/pdf")})
    assert junk.status_code == 400 and pdf.exists()
    txt = client.post("/api/quality", files={"file": ("x.txt", b"hello", "text/plain")})
    assert txt.status_code == 400


# ------------------------------------------------------------------ сквозной прогон
@needs_ocr
def test_blurred_photo_gets_a_retake_advice_in_the_answer(app):
    src = cv2.imread(str(ROOT / "data" / "samples" / "sample_06.png"))
    h, w = src.shape[:2]
    blurry = cv2.GaussianBlur(cv2.resize(src, (1600, int(1600 * h / w)), interpolation=cv2.INTER_CUBIC), (0, 0), 5)
    res = app.analyze(png(blurry), "blur.png", "Достаточно ли этого документа?")
    pq = res.photo_quality
    assert pq["retake"] is True and any(i["code"] == "blur" for i in pq["issues"])
    assert any("Снимок плохого качества" in n for n in res.answer.notes)
    assert res.to_dict(preview=False)["photo_quality"]["level"] == "poor"


@needs_ocr
def test_a_clean_sample_is_not_nagged(app, samples):
    res = app.analyze((samples / "sample_01.png").read_bytes(), "sample_01.png", "Достаточно ли этого документа?")
    assert res.photo_quality["level"] == "good" and res.photo_quality["issues"] == []
    assert not any("плохого качества" in n for n in res.answer.notes)
