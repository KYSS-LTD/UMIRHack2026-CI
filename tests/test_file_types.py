"""Все форматы файлов из ТЗ: JPG, JPEG, PNG и PDF. Один и тот же документ в разных «упаковках» (регистр расширения, неверное расширение, серый и прозрачный PNG,
повёрнутый по EXIF снимок, многостраничный PDF): читается постранично, получает предпросмотр первого листа для стола и проверяется одинаково.
Формат определяется по содержимому; всё остальное (WEBP, BMP, TIFF, GIF, SVG…) отклоняется понятной ошибкой."""

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import needs_ocr
from dekanat.ocr.preprocess import UnsupportedFile, load_pages
from dekanat.web.app import create_app

QUESTION = "Хватит ли этой справки для перевода на 2 курс?"


@pytest.fixture(scope="module")
def base(samples):
    """Чистый скан справки об обучении «всё в порядке»."""
    return Image.open(samples / "sample_01.png").convert("RGB")


def encode(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def exif_rotated_jpeg(img: Image.Image) -> bytes:
    """Снимок «лёжа» с меткой EXIF «повернуть на 90° по часовой»: после поворота лист стоит ровно."""
    exif = Image.Exif()
    exif[0x0112] = 6
    return encode(img.rotate(90, expand=True), "JPEG", quality=95, exif=exif)


def multipage_pdf(img: Image.Image) -> bytes:
    blank = Image.new("RGB", img.size, "white")
    return encode(img, "PDF", save_all=True, append_images=[blank])


def multipage_tiff(img: Image.Image) -> bytes:
    return encode(img, "TIFF", save_all=True, append_images=[Image.new("RGB", img.size, "white")])


def make_cases(img: Image.Image) -> list[tuple[str, str, bytes, int, bool]]:
    """(id, имя файла, содержимое, число страниц, без потерь?). Без потерь — значения полей должны совпасть с PNG до символа."""
    rgba = img.convert("RGBA")
    return [
        ("png", "doc.png", encode(img, "PNG"), 1, True),
        ("png-upper-ext", "DOC.PNG", encode(img, "PNG"), 1, True),
        ("png-grayscale", "gray.png", encode(img.convert("L"), "PNG"), 1, True),
        ("png-rgba", "rgba.png", encode(rgba, "PNG"), 1, True),
        ("png-palette", "pal.png", encode(img.convert("P", palette=Image.ADAPTIVE), "PNG"), 1, False),
        ("jpg", "doc.jpg", encode(img, "JPEG", quality=95), 1, False),
        ("jpeg", "doc.jpeg", encode(img, "JPEG", quality=95), 1, False),
        ("jpg-upper-ext", "DOC.JPG", encode(img, "JPEG", quality=95), 1, False),
        ("jpg-progressive", "prog.jpg", encode(img, "JPEG", quality=92, progressive=True), 1, False),
        ("jpg-exif-rotated", "phone.jpg", exif_rotated_jpeg(img), 1, False),
        ("png-with-jpg-name", "really-png.jpg", encode(img, "PNG"), 1, True),
        ("jpg-with-png-name", "really-jpg.png", encode(img, "JPEG", quality=95), 1, False),
        ("pdf", "scan.pdf", encode(img, "PDF"), 1, False),
        ("pdf-upper-ext", "SCAN.PDF", encode(img, "PDF"), 1, False),
        ("pdf-no-extension", "scan", encode(img, "PDF"), 1, False),
        ("pdf-two-pages", "two.pdf", multipage_pdf(img), 2, False),
    ]


def unsupported_cases(img: Image.Image) -> list[tuple[str, str, bytes]]:
    """Настоящие файлы других форматов, в том числе под «правильными» расширениями: проверка идёт по содержимому."""
    return [
        ("webp", "doc.webp", encode(img, "WEBP", quality=95)), ("webp-as-jpg", "photo.jpg", encode(img, "WEBP", quality=95)),
        ("bmp", "doc.bmp", encode(img, "BMP")), ("bmp-as-png", "scan.png", encode(img, "BMP")),
        ("tiff", "doc.tif", encode(img, "TIFF")), ("tiff-two-pages", "two.tiff", multipage_tiff(img)), ("tiff-as-pdf", "scan.pdf", encode(img, "TIFF")),
        ("gif", "anim.gif", encode(img.convert("P"), "GIF")),
        ("svg-as-png", "pic.png", b"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'></svg>"),
        ("text-as-pdf", "doc.pdf", "Справка об обучении. Иванов Иван.".encode("utf-8")), ("zip-as-docx-pdf", "w.pdf", b"PK\x03\x04" + b"0" * 64),
    ]


@pytest.fixture(scope="module")
def cases(base):
    return make_cases(base)


def ids(cases):
    return [c[0] for c in cases]


# ============================================================================ чтение страниц (без OCR)
def test_every_format_is_read_page_by_page_with_the_right_shape(base, cases):
    w, h = base.size
    for cid, name, data, pages, _ in cases:
        got = load_pages(data, name, 5)
        assert len(got) == pages, cid
        page = got[0]
        assert page.dtype == np.uint8 and page.ndim == 3 and page.shape[2] == 3, cid
        ph, pw = page.shape[:2]
        assert abs(pw / ph - w / h) < 0.02, (cid, "после EXIF-поворота и растеризации PDF лист должен стоять ровно, как исходный")


def test_a_page_limit_keeps_only_the_first_pages(cases):
    two = [c for c in cases if c[3] == 2]
    assert [c[0] for c in two] == ["pdf-two-pages"]
    assert len(load_pages(two[0][2], two[0][1], 1)) == 1 and len(load_pages(two[0][2], two[0][1], 5)) == 2


def test_lossless_formats_keep_every_pixel(base, cases):
    ref = np.array(base)[:, :, ::-1]
    for cid, name, data, pages, lossless in cases:
        if cid in ("png-palette",) or not lossless or cid.startswith(("png-rgba", "png-grayscale")):
            continue
        got = load_pages(data, name, 1)[0]
        assert got.shape == ref.shape and np.array_equal(got, ref), cid


@pytest.mark.parametrize("name, data", [
    ("empty.png", b""), ("empty.pdf", b""), ("text.jpg", b"just text"), ("text.pdf", b"%PDF-1.4 definitely not a pdf"), ("exe.png", b"MZ\x90\x00\x03\x00\x00\x00"),
    ("docx.pdf", b"PK\x03\x04" + b"0" * 64), ("svg.png", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"),
])
def test_unreadable_files_are_refused_with_a_clear_error(name, data):
    with pytest.raises(UnsupportedFile):
        load_pages(data, name, 1)


def test_only_jpg_jpeg_png_and_pdf_are_accepted_and_by_content(base, cases):
    from dekanat.document_processing.formats import check_format

    for cid, name, data, _, _ in cases:
        assert check_format(data, name) in ("jpeg", "png", "pdf"), cid
    for cid, name, data in unsupported_cases(base):
        with pytest.raises(UnsupportedFile, match="только JPG, JPEG, PNG и PDF"):
            check_format(data, name)


# ============================================================================ предпросмотр первого листа для стола
@pytest.fixture()
def client(tmp_settings):
    return TestClient(create_app(tmp_settings))


def test_preview_returns_the_first_page_of_every_format(base, cases, client):
    w, h = base.size
    for cid, name, data, pages, _ in cases:
        r = client.post("/api/preview", files={"file": (name, data, "application/octet-stream")})
        assert r.status_code == 200, (cid, r.text)
        p = r.json()
        assert p["data_url"].startswith("data:image/jpeg;base64,") and len(p["data_url"]) > 1000, cid
        assert p["page"] == 0 and abs(p["width"] / p["height"] - w / h) < 0.02, cid  # размеры страницы, в которых потом придут рамки
        assert "pages" not in p or p["pages"] == 1  # многостраничный файл: показывается только первый лист


def test_preview_page_matches_the_page_the_check_will_use(base, client):
    """Рамки из проверки считаются в координатах страницы; предпросмотр должен быть той же страницей и того же размера."""
    pdf = encode(base, "PDF")
    r = client.post("/api/preview", files={"file": ("scan.pdf", pdf, "application/pdf")}).json()
    page = load_pages(pdf, "scan.pdf", 1)[0]
    assert (r["height"], r["width"]) == page.shape[:2]


def test_preview_and_analysis_refuse_other_formats_with_the_same_message(base, client, app):
    for cid, name, data in unsupported_cases(base):
        r = client.post("/api/preview", files={"file": (name, data, "application/octet-stream")})
        assert r.status_code == 400 and "поддерживаются только JPG, JPEG, PNG и PDF" in r.json()["detail"], cid
        with pytest.raises(UnsupportedFile):
            app.analyze(data, name, "", "auto")


@pytest.mark.parametrize("name, data, status", [
    ("empty.pdf", b"", 400), ("text.jpg", b"just text", 400), ("broken.pdf", b"%PDF-1.4 broken", 400), ("exe.png", b"MZ\x90\x00" + b"0" * 64, 400),
])
def test_preview_refuses_bad_files(client, name, data, status):
    assert client.post("/api/preview", files={"file": (name, data, "application/octet-stream")}).status_code == status


def test_preview_refuses_too_large_files(tmp_settings):
    tmp_settings.raw["server"]["max_upload_mb"] = 1
    c = TestClient(create_app(tmp_settings))
    r = c.post("/api/preview", files={"file": ("big.png", b"\x89PNG" + b"0" * (2 * 1024 * 1024), "image/png")})
    assert r.status_code == 413
    assert c.post("/api/preview").status_code == 422  # файл обязателен


# ============================================================================ проверка документа (нужен Tesseract)
@pytest.fixture(scope="module")
def baseline(app, cases):
    png = next(c for c in cases if c[0] == "png")
    return app.analyze(png[2], png[1], QUESTION, "study_certificate")


def found(res) -> dict:
    return {f.id: f.value for f in res.fields if f.found}


@needs_ocr
def test_the_reference_document_is_understood(baseline):
    f = found(baseline)
    assert baseline.doc_type.id == "study_certificate" and "Макарова" in f["student_name"] and baseline.answer.verdict in ("ok", "missing")
    assert len(f) >= 5


@needs_ocr
def test_every_format_gives_the_same_check_as_the_png(app, cases, baseline):
    ref = found(baseline)
    for cid, name, data, pages, lossless in cases:
        res = app.analyze(data, name, QUESTION, "study_certificate")
        got = found(res)
        assert res.doc_type.id == baseline.doc_type.id, cid
        assert len(res.original_pages) == pages, cid
        assert "Макарова" in got.get("student_name", ""), (cid, got)
        if cid == "png-grayscale":
            # чёрно-белый скан: подпись без цвета не отделить от кольца печати (см. test_grayscale_scan_known_limitation); остальные поля те же
            assert {k: v for k, v in got.items() if k != "seal_signature"} == {k: v for k, v in ref.items() if k != "seal_signature"}, cid
            continue
        assert res.answer.verdict == baseline.answer.verdict, (cid, res.answer.verdict)
        assert set(ref) <= set(got), (cid, "не нашлись поля, найденные в PNG", set(ref) - set(got))
        if lossless and cid in ("png", "png-upper-ext", "png-with-jpg-name"):
            assert got == ref, cid  # без потерь — значения совпадают до символа


@needs_ocr
def test_grayscale_scan_known_limitation(app, base):
    """Известное ограничение: в чёрно-белом скане печать находится (по форме кольца), а подпись нет — у неё нет цвета, и её штрихи нельзя отделить от кольца
    печати. Ответ при этом честный («подпись: не обнаружена»), решение остаётся за сотрудником. Тест фиксирует поведение, чтобы улучшение не прошло незамеченным."""
    res = app.analyze(encode(base.convert("L"), "PNG"), "bw.png", QUESTION, "study_certificate")
    seal = next(f for f in res.fields if f.id == "seal_signature")
    assert "печать вуза: есть" in seal.evidence
    if seal.found:  # детектор подписи улучшили — это хорошо: обновите описание ограничения в README
        pytest.fail("Подпись в чёрно-белом скане теперь находится: уберите «известное ограничение» из README и этого теста")
    assert res.answer.verdict == "missing" and "подпись" in " ".join([res.answer.text, seal.doubt]).lower()


@needs_ocr
def test_auto_detection_of_the_type_works_for_every_container(app, cases):
    for cid, name, data, _, _ in cases:
        res = app.analyze(data, name, "", "auto")
        assert res.doc_type.id == "study_certificate", (cid, res.doc_type.id)


@needs_ocr
def test_api_analyze_and_jobs_accept_the_formats_from_the_task(base, client):
    for name, data, mime in (("a.jpg", encode(base, "JPEG", quality=95), "image/jpeg"), ("a.jpeg", encode(base, "JPEG", quality=95), "image/jpeg"),
                             ("a.png", encode(base, "PNG"), "image/png"), ("a.pdf", encode(base, "PDF"), "application/pdf"),
                             ("two.pdf", multipage_pdf(base), "application/pdf")):
        r = client.post("/api/analyze", files={"file": (name, data, mime)}, data={"question": QUESTION, "doc_type": "auto"})
        assert r.status_code == 200, (name, r.text)
        j = r.json()
        assert j["doc_type"]["id"] == "study_certificate" and j["fields"] and j["answer"]["text"], name
        assert len(j["images"]) == (2 if name == "two.pdf" else 1), name  # предпросмотр каждой страницы для стола
        assert all(i["data_url"].startswith("data:image/jpeg;base64,") for i in j["images"]), name
        job = client.post("/api/jobs", files={"file": (name, data, mime)}, data={"question": "", "doc_type": "auto"})
        assert job.status_code == 200 and job.json()["id"], name
