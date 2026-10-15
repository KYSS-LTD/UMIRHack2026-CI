"""Поиск подписи и печати по изображению.

Слова «М.П.» и «(подпись)» стоят и на пустом бланке, поэтому текст ничего не доказывает —
смотрим на сами чернила:
  * печать — цветное кольцо/пятно крупного размера (Хафф + крупный компактный цветной объект);
  * подпись — штрихи рукописного вида вне распознанного текста рядом со строкой подписи.
Это эвристики: у каждой находки есть уверенность и рамка для подсветки.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from ..models import OcrPage

_ANCHOR = re.compile(r"подпис|\bдекан\b|руководитель\b|директор\b|ректор\b|проректор|заявител|/\s*[А-ЯЁA-Z]\.\s?[А-ЯЁA-Z]?\.?\s*[А-ЯЁ][а-яё]+\s*/", re.IGNORECASE)
_CYR_WORD = re.compile(r"^(?:[А-Яа-яЁё]{4,}[.,:;]?|[А-Яа-яЁё]{1,2}\.[А-Яа-яЁё]?\.?)$")  # слово или сокращение вроде «М.П.»
_PLAIN_TEXT = re.compile(r"^[A-Za-zА-Яа-яЁё0-9.,:;№«»()/\-–—]+$")


@dataclass
class MarkDetection:
    present: bool
    confidence: float
    box: Optional[tuple[float, float, float, float]] = None  # x0,y0,x1,y1 в координатах обработанного изображения
    details: dict = field(default_factory=dict)


def _text_mask(page: OcrPage, scale: float, shape: tuple[int, int], colored: np.ndarray) -> np.ndarray:
    """Маска напечатанного текста: уверенно распознанные «обычные» слова (в т.ч. инициалы «А.М.», «М.П.»).

    Слова, чьи пиксели в основном цветные, пропускаем: это текст внутри печати, а не печатный текст."""
    mask = np.zeros(shape, np.uint8)
    for w in page.words:
        t = w.text.strip()
        if not _PLAIN_TEXT.match(t) or not re.search(r"[A-Za-zА-Яа-яЁё0-9]", t):
            continue
        if w.conf < 60:
            continue
        x0, y0, x1, y1 = (int(round(v * scale)) for v in (w.x0, w.y0, w.x1, w.y1))
        x0, y0 = max(0, x0), max(0, y0)
        box = colored[y0:y1, x0:x1]
        if box.size and np.count_nonzero(box) / box.size > 0.22:
            continue
        cv2.rectangle(mask, (x0 - 1, y0 - 1), (x1 + 1, y1 + 1), 255, -1)
    return mask


def _remove_rules(ink: np.ndarray, w: int, h: int) -> np.ndarray:
    """Убирает длинные горизонтальные/вертикальные линии (подчёркивания, рамки таблиц)."""
    kh = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, int(0.05 * w)), 1))
    kv = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, int(0.03 * h))))
    rules = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kh) | cv2.morphologyEx(ink, cv2.MORPH_OPEN, kv)
    rules = cv2.dilate(rules, np.ones((3, 3), np.uint8))
    return ink & ~rules


def _ring_coverage(mask: np.ndarray, cx: float, cy: float, r: float) -> float:
    hit = 0
    n = 72
    h, w = mask.shape
    for k in range(n):
        a = 2 * math.pi * k / n
        ok = False
        for dr in (-4, -2, 0, 2, 4):
            x, y = int(round(cx + (r + dr) * math.cos(a))), int(round(cy + (r + dr) * math.sin(a)))
            if 0 <= x < w and 0 <= y < h and mask[y, x]:
                ok = True
                break
        hit += ok
    return hit / n


def _find_circle(mask: np.ndarray, min_r: int, max_r: int) -> Optional[tuple[float, float, float, float]]:
    """Круг (кольцо печати): -> (cx, cy, r, покрытие кольца)."""
    blur = cv2.GaussianBlur(mask, (7, 7), 0)
    circles = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.2, minDist=max(20, min_r), param1=120, param2=20, minRadius=min_r, maxRadius=max_r)
    found = []
    if circles is not None:
        for cx, cy, r in circles[0][:12]:
            found.append((float(cx), float(cy), float(r), _ring_coverage(mask, cx, cy, r)))
    if not found:
        return None
    top = max(c[3] for c in found)
    return max((c for c in found if c[3] >= top - 0.06), key=lambda c: c[2])


def detect_marks(page: OcrPage) -> dict[str, MarkDetection]:
    img = page.image
    if img is None:
        return {"signature": MarkDetection(False, 0.0), "stamp": MarkDetection(False, 0.0)}
    H0, W0 = img.shape[:2]
    scale = min(1.0, 1200.0 / W0)
    work = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img
    H, W = work.shape[:2]
    work = cv2.GaussianBlur(work, (3, 3), 0)
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    hue_ok = ((hue >= 95) & (hue <= 155)) | (hue <= 10) | (hue >= 165)
    colored = (hue_ok & (sat > 34) & (val < 238)).astype(np.uint8) * 255
    dark = (gray < 205).astype(np.uint8) * 255
    ink = cv2.bitwise_or(colored, dark)

    # граница кадра и «хвосты» перспективы не считаем
    border = np.zeros_like(ink)
    bx, by = int(0.02 * W), int(0.015 * H)
    border[by:H - by, bx:W - bx] = 255
    ink &= border
    colored &= border

    text = _text_mask(page, scale, (H, W), colored)
    text_d = cv2.dilate(text, np.ones((3, 3), np.uint8))
    resid_all = _remove_rules(ink, W, H) & ~text_d
    colr = colored & ~text_d  # у цветной маски линии не убираем: рамка прямоугольной печати — это прямые линии

    # ------------------------------------------------------------ печать
    stamp = MarkDetection(False, 0.0)
    disc = None
    min_r, max_r = int(0.04 * W), int(0.16 * W)
    for name, mask in (("colored", colr), ("ink", resid_all)):
        if int(np.count_nonzero(mask)) < 400:
            continue
        circ = _find_circle(cv2.dilate(mask, np.ones((3, 3), np.uint8)), min_r, max_r)
        if circ and circ[3] >= (0.5 if name == "colored" else 0.78):
            cx, cy, r, cov = circ
            r_out = r
            for rr in np.arange(r + 2, r * 1.6, 2.0):
                if _ring_coverage(mask, cx, cy, float(rr)) >= 0.4:
                    r_out = float(rr)
            r = max(r, r_out)
            conf = float(np.clip(0.55 + 0.45 * cov, 0, 0.98))
            stamp = MarkDetection(True, conf, ((cx - r) / scale, (cy - r) / scale, (cx + r) / scale, (cy + r) / scale), {"how": f"circle/{name}", "coverage": round(cov, 2)})
            disc = ("circle", cx, cy, r)
            break
    if not stamp.present:
        # прямоугольная/частично закрытая печать: крупный компактный цветной объект
        merged = cv2.dilate(colr, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
        best = None
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            aspect = w / max(h, 1)
            ink_px = int(np.count_nonzero(colr[labels == i]))
            if w >= 0.085 * W and h >= 0.05 * H and 0.5 <= aspect <= 2.3 and ink_px >= 350:
                if best is None or ink_px > best[0]:
                    best = (ink_px, x, y, w, h)
        if best:
            ink_px, x, y, w, h = best
            stamp = MarkDetection(True, 0.7, (x / scale, y / scale, (x + w) / scale, (y + h) / scale), {"how": "blob", "ink": ink_px})
            disc = ("rect", x, y, w, h)

    # ------------------------------------------------------------ подпись
    sig_src = resid_all.copy()
    if stamp.present and stamp.box:
        sx0, sy0, sx1, sy1 = (v * scale for v in stamp.box)
        seed = np.zeros_like(colr)
        seed[int(max(0, sy0)):int(sy1), int(max(0, sx0)):int(sx1)] = 255
        in_seed = (colr > 0) & (seed > 0)
        zx0, zy0, zx1, zy1 = sx0, sy0, sx1, sy1
        if np.count_nonzero(in_seed) >= 120:
            # «цвет оттиска»: медианный оттенок и яркость; подпись (ручка) обычно темнее оттиска
            h_shift = (hue.astype(np.int16) + 90) % 180
            med_h = float(np.median(h_shift[in_seed]))
            med_v = float(np.median(val[in_seed]))
            stamp_like = (colr > 0) & (np.abs(h_shift - med_h) <= 16) & (val >= med_v - 28)
            merged_c = cv2.dilate(stamp_like.astype(np.uint8) * 255, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
            nlab, lab, lst, _ = cv2.connectedComponentsWithStats(merged_c, connectivity=8)
            for i in range(1, nlab):
                x, y, w, h, area = lst[i]
                if x < sx1 and x + w > sx0 and y < sy1 and y + h > sy0 and int(np.count_nonzero(stamp_like[lab == i])) >= 150:
                    zx0, zy0, zx1, zy1 = min(zx0, x), min(zy0, y), max(zx1, x + w), max(zy1, y + h)
            dark_than_stamp = val < med_v - 28
        else:
            dark_than_stamp = np.zeros(val.shape, bool)
        stamp.box = (zx0 / scale, zy0 / scale, zx1 / scale, zy1 / scale)
        pad = 8
        xa, ya, xb, yb = int(max(0, zx0 - pad)), int(max(0, zy0 - pad)), int(min(W, zx1 + pad)), int(min(H, zy1 + pad))
        # внутри оттиска остаются только штрихи заметно темнее его чернил (подпись поверх печати)
        zone = sig_src[ya:yb, xa:xb]
        zone[~dark_than_stamp[ya:yb, xa:xb]] = 0
        if disc and disc[0] == "circle" and stamp.details.get("how") == "circle/ink":
            cv2.circle(sig_src, (int(disc[1]), int(disc[2])), int(disc[3] + 10), 0, -1)  # чёрная печать: убираем диск целиком
    wordish = [(w.x0 * scale, w.y0 * scale, w.x1 * scale, w.y1 * scale, w.conf) for w in page.words if w.conf >= 25 and _CYR_WORD.match(w.text.strip())]

    def is_printed_text(x: int, y: int, w: int, h: int, px: int = 0) -> bool:
        """Рамка почти целиком занята распознанным словом. Слово с невысокой уверенностью (25–45) считается печатным только при небольшом количестве
        чернил: у росчерка подписи пикселей на порядок больше, чем у слова."""
        area = float(max(1, w * h))
        for wx0, wy0, wx1, wy1, conf in wordish:
            ox = max(0.0, min(x + w, wx1) - max(x, wx0))
            oy = max(0.0, min(y + h, wy1) - max(y, wy0))
            if ox * oy / area >= 0.5 and (conf >= 45 or px < 700):
                return True
        return False

    # область поиска
    region = np.zeros_like(sig_src)
    heights = sorted(r.y1 - r.y0 for r in page.rows) or [20.0]
    rh_page = max(12.0, heights[len(heights) // 2])
    rh_work = rh_page * scale
    for row in page.rows:
        if _ANCHOR.search(row.text):
            ya, yb = int(max(0, (row.y0 - 4.8 * rh_page) * scale)), int(min(H, (row.y1 + 1.6 * rh_page) * scale))
            region[ya:yb, :] = 255
    # нижняя часть содержимого листа: подпись обычно в конце документа
    body = [r for r in page.rows if len(r.words) >= 2 or len(r.text) >= 5]
    if body:
        top, bottom = min(r.y0 for r in body), max(r.y1 for r in body)
        region[int(max(0, top + 0.55 * (bottom - top)) * scale):, :] = 255
    else:
        region[int(0.58 * H):, :] = 255
    sig_src &= region
    # на бланке с подписанной строкой («Подпись сотрудника:») подпись ищется у этой строки, а не где-то ниже по листу
    label_bands = []
    for row in page.rows:
        low = row.text.strip().lower()
        if low.startswith("подпись") and "являются" not in low and "макет" not in low:
            label_bands.append((int((row.y0 - 3.0 * rh_page) * scale), int((row.y1 + 3.0 * rh_page) * scale)))
    merged = cv2.dilate(sig_src, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    sig = MarkDetection(False, 0.0)
    best = None
    seen = []
    rejected: list[tuple] = []  # кандидаты, отклонённые как печатный текст: нужны для отладки
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        px = int(np.count_nonzero(sig_src[labels == i]))
        if px >= 30:
            seen.append((px, int(x), int(y), int(w), int(h)))
        if not (w >= 0.035 * W and h >= 0.016 * H and px >= 110 and w / max(h, 1) < 9):
            continue
        # строка нераспознанного печатного текста: ≥3 «буквы» высотой с обычный текст на одной линии
        sub = sig_src[y:y + h, x:x + w]
        m, _, st, cen = cv2.connectedComponentsWithStats(sub, connectivity=8)
        letters = [(t, c) for t, c in zip(st[1:], cen[1:]) if t[4] >= 12 and 0.35 * rh_work <= t[3] <= 1.3 * rh_work and t[2] <= 1.4 * rh_work]
        if len(letters) >= 3 and float(np.std([c[1] for _, c in letters])) <= 0.25 * rh_work and h < 2.2 * rh_work:
            rejected.append(("строка-текст", int(x), int(y), int(w), int(h), px))
            continue
        if is_printed_text(int(x), int(y), int(w), int(h), px):
            rejected.append(("распознанный текст", int(x), int(y), int(w), int(h), px))
            continue
        if label_bands and not any(ya <= y + h / 2 <= yb for ya, yb in label_bands):
            rejected.append(("не у строки подписи", int(x), int(y), int(w), int(h), px))
            continue
        if stamp.present and stamp.box:
            sx0, sy0, sx1, sy1 = (v * scale for v in stamp.box)
            if sx0 <= x + w / 2 <= sx1 and sy0 <= y + h / 2 <= sy1 and px < 350:
                rejected.append(("внутри печати", int(x), int(y), int(w), int(h), px))  # буквы и кольцо оттиска, а не подпись
                continue
        score = px * min(1.0, w / (0.08 * W))
        if best is None or score > best[0]:
            best = (score, x, y, w, h, px)
    if best:
        score, x, y, w, h, px = best
        conf = float(np.clip(0.55 + min(px, 700) / 1800.0, 0, 0.95))
        sig = MarkDetection(True, conf, (x / scale, y / scale, (x + w) / scale, (y + h) / scale), {"ink": px})
    sig.details["candidates"] = sorted(seen, reverse=True)[:4]
    sig.details["rejected"] = rejected[:4]
    sig.details["scale"] = round(scale, 3)
    sig.details["size"] = (W, H)
    absent_conf = 0.85
    if not stamp.present:
        stamp.confidence = absent_conf
    if not sig.present:
        sig.confidence = absent_conf
    if stamp.present and stamp.box:
        mp_rows = [r for r in page.rows if re.fullmatch(r"м\.?\s?п\.?", r.text.strip().lower().replace(" ", ""))]
        if mp_rows:
            cx, cy = (stamp.box[0] + stamp.box[2]) / 2, (stamp.box[1] + stamp.box[3]) / 2
            near = any(abs(cy - (r.y0 + r.y1) / 2) <= 8.0 * rh_page and abs(cx - (r.x0 + r.x1) / 2) <= 0.4 * (W / scale) for r in mp_rows)
            if not near:
                stamp = MarkDetection(False, 0.8, None, {"how": "misplaced", "was": stamp.details})
    return {"signature": sig, "stamp": stamp}
