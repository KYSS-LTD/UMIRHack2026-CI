import math

import cv2
import numpy as np
import pytest

from dekanat.models import OcrPage
from dekanat.ocr import preprocess as pp
from dekanat.ocr.runner import build_rows, quality, split_glued
from dekanat.models import Word

from conftest import ROOT


@pytest.fixture(scope="module")
def doc():
    return cv2.imdecode(np.fromfile(str(ROOT / "data" / "samples" / "sample_01.png"), np.uint8), cv2.IMREAD_COLOR)


@pytest.mark.parametrize("angle", [-30, -17, -5, 4, 12, 29])
def test_skew_estimation(doc, angle):
    tilted = pp.rotate_expand(doc, angle)[0]
    est, _ = pp.estimate_skew(pp.to_gray(pp.normalize_illumination(tilted)))
    # оценка — угол поворота, который нужно применить, чтобы выпрямить: противоположный наклону
    assert abs(est + angle) <= 1.5, (angle, est)


def test_straight_page_is_left_alone(doc):
    assert pp.estimate_skew(pp.to_gray(doc))[0] == 0.0


def test_box_mapping_roundtrip(doc):
    h, w = doc.shape[:2]
    rotated, m = pp.rotate_expand(doc, 20)
    page = OcrPage(0, rotated.shape[1], rotated.shape[0], [], [], m, (w, h))
    # прямоугольник в координатах повёрнутого изображения -> в исходных; его центр совпадает с прямым отображением
    cx, cy = 300.0, 220.0
    p = m @ np.array([cx, cy, 1.0])
    p = p[:2] / p[2]
    box = page.map_box(p[0] - 10, p[1] - 10, p[0] + 10, p[1] + 10)
    assert abs((box.x0 + box.x1) / 2 - cx) < 3 and abs((box.y0 + box.y1) / 2 - cy) < 3


def test_tilted_scan_gets_a_slanted_quad_along_the_text_line(doc):
    """Снимок под углом: рамка строки — наклонный четырёхугольник, а не раздутый осевой прямоугольник."""
    h, w = doc.shape[:2]
    rotated, m = pp.rotate_expand(doc, 12)
    page = OcrPage(0, rotated.shape[1], rotated.shape[0], [], [], m, (w, h))
    box = page.map_box(700, 900, 1100, 940)  # длинная узкая строка 400×40, целиком внутри листа
    assert box.quad is not None and len(box.quad) == 4
    top, right = box.quad[1][1] - box.quad[0][1], box.quad[1][0] - box.quad[0][0]
    assert abs(math.degrees(math.atan2(abs(top), right)) - 12) < 1.0  # верхняя сторона наклонена так же, как лист
    side = math.dist(box.quad[0], box.quad[1])
    assert abs(side - 400) < 2 and abs(math.dist(box.quad[1], box.quad[2]) - 40) < 2  # размеры строки сохранились
    assert (box.h * box.w) > 2 * side * 40  # описанный прямоугольник сильно больше самой строки: именно он и «кривил» подсветку
    d = box.to_dict()
    assert len(d["quad"]) == 4 and all(len(p) == 2 for p in d["quad"])


def test_straight_scan_keeps_a_plain_box(doc):
    h, w = doc.shape[:2]
    page = OcrPage(0, w, h, [], [], np.eye(3), (w, h))
    box = page.map_box(100, 200, 500, 240)
    assert box.quad is None and "quad" not in box.to_dict()
    # несущественный наклон (доли градуса) тоже не порождает четырёхугольник
    rotated, m = pp.rotate_expand(doc, 0.2)
    slight = OcrPage(0, rotated.shape[1], rotated.shape[0], [], [], m, (w, h)).map_box(100, 200, 500, 240)
    assert slight.quad is None


def test_overlay_follows_the_tilt(doc):
    from dekanat.highlight import draw_overlay
    from dekanat.models import Box

    flat = Box(100, 100, 500, 140)
    slanted = Box(100, 100, 500, 240, 0, ((100, 140), (480, 100), (500, 200), (120, 240)))
    img = np.full((300, 600, 3), 255, np.uint8)
    out_flat = draw_overlay(img, [(flat, "", (255, 140, 0))], 0)
    out_quad = draw_overlay(img, [(slanted, "", (255, 140, 0))], 0)
    assert (out_flat != 255).any() and (out_quad != 255).any()
    # в левом верхнем углу описанного прямоугольника рамка наклонного четырёхугольника не закрашивает ничего
    assert (out_quad[102:110, 102:110] == 255).all()


def test_page_quad_found_on_a_photo(doc):
    h, w = doc.shape[:2]
    pad = 160
    canvas = np.full((h + 2 * pad, w + 2 * pad, 3), (70, 90, 110), np.uint8)
    canvas[pad:pad + h, pad:pad + w] = doc
    photo = pp.rotate_expand(canvas, 18, (70, 90, 110))[0]
    quad = pp.find_page_quad(photo)
    assert quad is not None
    warped, _ = pp.warp_quad(photo, quad)
    assert 0.6 < warped.shape[1] / warped.shape[0] < 0.85  # лист A4 книжной ориентации
    assert pp.find_page_quad(doc) is None  # скан во весь кадр — искать нечего


def test_exif_orientation_is_respected(tmp_path, doc):
    from PIL import Image

    img = Image.fromarray(cv2.cvtColor(doc, cv2.COLOR_BGR2RGB))
    exif = img.getexif()
    exif[274] = 6  # «повернуть на 90° по часовой»
    path = tmp_path / "exif.jpg"
    img.save(path, exif=exif)
    page = pp.load_pages(path.read_bytes(), "exif.jpg")[0]
    assert page.shape[:2] == (doc.shape[1], doc.shape[0])


def test_table_lines_are_removed():
    img = np.full((600, 900), 255, np.uint8)
    cv2.rectangle(img, (50, 50), (850, 550), 0, 3)
    cv2.line(img, (450, 50), (450, 550), 0, 3)
    cv2.putText(img, "Hello", (80, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
    cleaned = pp.remove_lines(img)
    assert (cleaned[48:54, 100:800] < 128).sum() == 0  # линия стёрта
    assert (cleaned[80:130, 80:240] < 128).sum() > 100  # текст остался


def test_glued_label_is_split():
    words = [Word("курс:ЮР-32,", 90, 100, 10, 210, 30), Word("2", 90, 230, 10, 240, 30)]
    out = split_glued(words)
    assert [w.text for w in out] == ["курс:", "ЮР-32,", "2"] and out[0].x1 <= out[1].x0


def test_rows_group_cells_of_a_table_row():
    words = [Word("Форма", 95, 100, 100, 180, 124), Word("обучения", 95, 190, 101, 300, 125), Word("очная", 95, 600, 98, 680, 124),
             Word("Период", 95, 100, 160, 190, 184)]
    rows = build_rows(words)
    assert [r.text for r in rows] == ["Форма обучения очная", "Период"]


def test_quality_prefers_real_text():
    good = [Word("Справка", 95, 0, 0, 80, 20), Word("об", 95, 90, 0, 110, 20), Word("обучении", 94, 120, 0, 210, 20)] * 4
    junk = [Word("a(|", 40, 0, 0, 30, 20), Word("~~", 30, 40, 0, 60, 20), Word("Ё5", 20, 70, 0, 90, 20)] * 4
    assert quality(good)["score"] > 60 > quality(junk)["score"]


def test_full_frame_white_page_is_not_cropped_to_its_text_block():
    """Регресс: белый лист во весь кадр с текстом только в нижней части. Блок текста не должен приниматься за «лист»
    (обрезка по нему отрезала шапку, заголовок и номера пунктов)."""
    img = np.full((1280, 905, 3), 255, np.uint8)
    cv2.putText(img, "DECREE No 2", (250, 380), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    for i in range(14):
        cv2.putText(img, f"{i + 1}. take measures to ensure the work of water systems in the period", (60, 640 + i * 38), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    assert pp.find_page_quad(img) is None


def test_page_on_a_dark_table_is_still_found():
    table = np.full((1000, 1400, 3), (70, 80, 90), np.uint8)
    cv2.rectangle(table, (250, 120), (1150, 900), (245, 245, 240), -1)
    for i in range(10):
        cv2.putText(table, f"line {i} of the document text", (300, 220 + i * 60), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2)
    quad = pp.find_page_quad(table)
    assert quad is not None and 200 < quad[:, 0].min() < 300 and 1100 < quad[:, 0].max() < 1200
