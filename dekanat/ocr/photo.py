"""Оценка качества снимка: размытие, мелкий текст, тёмный или бледный снимок, блики, тень, наклон, обрезанный край.

Работает в два захода:

* `assess_image` — только по пикселям, быстро и до OCR: так при выборе файла можно сразу посоветовать переснять;
* `assess_page` — по пикселям и по результату OCR (высота букв, чёткость краёв слов, уверенность распознавания,
  слова у края кадра) — это итоговая оценка, она попадает в результат проверки.

Критерий серьёзности — не «красота» снимка, а вред для чтения. Если текст всё равно прочитан уверенно, жалоба
снижается до подсказки; если читается плохо — это причина переснять. Тексты — для студента: что не так и как снять лучше.
"""

from __future__ import annotations

import statistics
from typing import Optional

import cv2
import numpy as np

from . import preprocess as pp

# ----------------------------------------------------------------------------------------------------------- пороги
# (warn, bad): значения подобраны на синтетических снимках трёх уровней порчи и искусственно испорченных копиях
# образцов (размытие, уменьшение, затемнение, блёклость, обрезка), см. tests/test_photo_quality.py
T = {
    "sharp_rel": (0.9, 0.45),         # p99,5 |Лапласиан| / контраст бумаги и чернил (до OCR), меньше — хуже
    "edge_rel": (0.12, 0.2),          # ширина края буквы / высота слова (по OCR), больше — хуже
    "text_h": (17.0, 12.0),           # высота слова в пикселях исходного снимка, меньше — хуже
    "short_side": (900, 600),         # короткая сторона снимка, px
    "paper_level": (150, 110),        # яркость бумаги (0..255), меньше — темнее
    "contrast": (95, 60),             # разница яркости бумаги и чернил, меньше — бледнее
    "glare": (0.012, 0.04),           # доля кадра, засвеченная бликом
    "uneven": (0.30, 9.0),            # размах яркости бумаги (тень): сама по себе не причина переснять
    "tilt": (20.0, 38.0),             # угол наклона строк, градусы
    "edge_words": (3, 8),             # слов, упирающихся в край кадра
}

TEXTS = {
    "blur": ("Снимок размытый", "Держите телефон неподвижно, дождитесь фокуса и снимите ещё раз."),
    "low_res": ("Мелкий текст или низкое разрешение", "Подойдите ближе, чтобы лист занимал весь кадр, или пришлите скан или PDF."),
    "dark": ("Снимок тёмный", "Включите свет или снимайте у окна."),
    "faded": ("Бледный снимок: мало контраста", "Снимайте при ярком ровном свете, без «автоулучшения» фильтрами."),
    "glare": ("На снимке блик", "Чуть наклоните лист или телефон, чтобы свет не отражался от бумаги."),
    "shadow": ("Неровное освещение, тень на листе", "Снимайте так, чтобы телефон и рука не отбрасывали тень на документ."),
    "tilt": ("Лист снят под большим углом", "Снимайте сверху, чтобы лист лежал в кадре ровно."),
    "cropped": ("Край листа обрезан", "Снимите лист целиком, оставив небольшие поля по краям кадра."),
    "empty": ("Почти нет читаемого текста", "Проверьте, что снят нужный лист: он не перевёрнут, в фокусе и целиком в кадре."),
    "low_ocr": ("Текст читается плохо", "Пришлите более чёткое фото или скан."),
}
PHYSICAL = ("blur", "low_res", "dark", "faded", "glare", "shadow")  # причины, которые не мешают, если текст всё равно прочитан
LEVEL_RANK = {"good": 0, "fair": 1, "poor": 2}
SEV_RANK = {"info": 0, "warn": 1, "bad": 2}

WORK_SIDE = 1400  # длинная сторона рабочей копии для оценки по пикселям


def _grade(value: float, thresholds: tuple[float, float], lower_is_worse: bool = True) -> Optional[str]:
    warn, bad = thresholds
    if lower_is_worse:
        return "bad" if value < bad else "warn" if value < warn else None
    return "bad" if value > bad else "warn" if value > warn else None


def _work_gray(bgr: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    s = WORK_SIDE / max(h, w)
    return cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else g


def pixel_metrics(bgr: np.ndarray) -> dict:
    """Числа по одним пикселям: чёткость, яркость бумаги, контраст, блик, неровность освещения."""
    g = _work_gray(bgr)
    paper = float(np.percentile(g, 85))
    ink = float(np.percentile(g, 0.3))  # чернил на листе мало: «3 %» уже была бы бумагой
    contrast = max(paper - ink, 1.0)
    lap = np.abs(cv2.Laplacian(g.astype(np.float32), cv2.CV_32F, ksize=3))
    sharp = float(np.percentile(lap, 99.5))
    # блик — пятна, заметно ярче самой бумаги; на белом скане (бумага = 255) блика быть не может
    glare = 0.0
    bright = g[g > 140]
    paper_med = float(np.median(bright)) if bright.size else 255.0
    if paper_med < 238:
        hot = (g >= 250).astype(np.uint8)
        hot = cv2.morphologyEx(hot, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
        glare = float(hot.sum()) / hot.size
    bg = pp.background_gain(g).astype(np.float32)
    hi = float(np.percentile(bg, 95))
    paper_area = bg[bg > 0.5 * hi]  # стол вокруг листа в оценку тени не входит
    uneven = (hi - float(np.percentile(paper_area, 5))) / max(hi, 1.0) if paper_area.size else 0.0
    return {
        "sharp_rel": round(sharp / max(contrast, 30.0), 3), "paper_level": round(paper, 1), "contrast": round(contrast, 1),
        "glare": round(glare, 4), "uneven": round(float(uneven), 3),
    }


def _issue(code: str, severity: str, **extra) -> dict:
    title, hint = TEXTS[code]
    return {"code": code, "severity": severity, "title": title, "hint": hint, **extra}


def _verdict(issues: list[dict]) -> dict:
    level = "poor" if any(i["severity"] == "bad" for i in issues) else "fair" if any(i["severity"] == "warn" for i in issues) else "good"
    return {"level": level, "retake": level == "poor", "issues": issues}


def _physical_issues(m: dict, height: int, width: int) -> list[dict]:
    out: list[dict] = []
    for code, key, lower in (("blur", "sharp_rel", True), ("dark", "paper_level", True), ("faded", "contrast", True), ("glare", "glare", False), ("shadow", "uneven", False)):
        sev = _grade(m[key], T[key], lower)
        if sev:
            out.append(_issue(code, sev))
    short = min(height, width)
    sev = _grade(short, T["short_side"])
    if sev:
        out.append(_issue("low_res", sev))
    # тёмный снимок уже объясняет низкий контраст — вторую жалобу не добавляем
    if any(i["code"] == "dark" for i in out):
        out = [i for i in out if i["code"] != "faded"]
    return out


def assess_image(bgr: np.ndarray) -> dict:
    """Быстрая оценка по пикселям (без OCR): годится для предупреждения сразу при выборе файла."""
    h, w = bgr.shape[:2]
    m = pixel_metrics(bgr)
    out = _verdict(_physical_issues(m, h, w))
    out["metrics"] = {**m, "width": w, "height": h}
    return out


# ----------------------------------------------------------------------------------------------------------- по OCR
def _scale(M: np.ndarray) -> float:
    """Во сколько раз обработанное изображение крупнее исходного (по осям матрицы «исходный -> обработанный»)."""
    return float(np.sqrt(abs(np.linalg.det(M[:2, :2])))) or 1.0


def _word_boxes_in_original(page) -> list[tuple[float, float, float, float, float]]:
    """Слова уверенно распознанных строк в координатах исходного снимка: (x0, y0, x1, y1, высота)."""
    inv = np.linalg.inv(page.transform)
    out = []
    for w in page.words:
        if w.conf < 40 or len(w.text) < 3:
            continue
        pts = np.array([[w.x0, w.y0, 1], [w.x1, w.y0, 1], [w.x1, w.y1, 1], [w.x0, w.y1, 1]], dtype=np.float64).T
        m = inv @ pts
        m = m[:2] / m[2:3]
        x0, y0 = m.min(axis=1)
        x1, y1 = m.max(axis=1)
        # высоту слова берём как короткую сторону охватывающей рамки: у наклонных слов рамка шире, но не «выше» по строке
        out.append((float(x0), float(y0), float(x1), float(y1), float(min(x1 - x0, y1 - y0))))
    return out


def edge_width_px(gray: np.ndarray, boxes: list[tuple[float, float, float, float, float]], limit: int = 120) -> float:
    """Ширина перехода «чернила — бумага» на буквах (медиана по словам), в пикселях исходного снимка: у чёткого текста 1–2 px, у размытого — больше."""
    if not boxes:
        return 0.0
    gray = gray.astype(np.float32)
    h, w = gray.shape
    step = max(1, len(boxes) // limit)
    widths = []
    for x0, y0, x1, y1, _bh in boxes[::step]:
        xa, ya, xb, yb = int(max(0, x0)), int(max(0, y0)), int(min(w, x1)), int(min(h, y1))
        if xb - xa < 6 or yb - ya < 6:
            continue
        crop = gray[ya:yb, xa:xb]
        contrast = float(np.percentile(crop, 95) - np.percentile(crop, 5))
        if contrast < 40:
            continue
        gx = np.abs(cv2.Sobel(crop, cv2.CV_32F, 1, 0, ksize=3)) / 8.0
        gy = np.abs(cv2.Sobel(crop, cv2.CV_32F, 0, 1, ksize=3)) / 8.0
        gmax = float(np.percentile(np.maximum(gx, gy), 98))
        if gmax > 0:
            widths.append(contrast / gmax)
    return float(statistics.median(widths)) if widths else 0.0


def assess_page(bgr: np.ndarray, page) -> dict:
    """Итоговая оценка страницы: пиксели + результат OCR. `bgr` — исходный снимок, `page` — OcrPage."""
    h, w = bgr.shape[:2]
    q = page.quality or {}
    score = float(q.get("score", 0.0))
    mean_conf = float(q.get("mean_conf", 0.0))
    n_words = int(q.get("words", 0))
    m = pixel_metrics(bgr)
    issues = _physical_issues(m, h, w)

    metrics = {**m, "width": w, "height": h, "ocr_score": score, "mean_conf": mean_conf, "words": n_words}
    reads_well = score >= 75 and mean_conf >= 80  # текст прочитан уверенно: физические недостатки не помеха

    if n_words < 8:
        issues = [i for i in issues if i["code"] != "low_ocr"]
        issues.append(_issue("empty", "bad"))
    else:
        boxes = _word_boxes_in_original(page)
        scale = _scale(page.transform)
        text_h = statistics.median(b[4] for b in boxes) if boxes else float(q.get("median_h", 0.0)) / scale
        metrics["text_h"] = round(text_h, 1)
        sev = _grade(text_h, T["text_h"]) if text_h else None
        if sev and not any(i["code"] == "low_res" for i in issues):
            issues.append(_issue("low_res", sev))
        elif sev:  # маленькая картинка и мелкий текст — одна жалоба, берём более строгую
            for i in issues:
                if i["code"] == "low_res" and SEV_RANK[sev] > SEV_RANK[i["severity"]]:
                    i["severity"] = sev
        if text_h:
            edge = edge_width_px(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), boxes)
            metrics["edge_px"] = round(edge, 2)
            metrics["edge_rel"] = round(edge / text_h, 3) if edge else 0.0
            # по буквам размытие видно точнее, чем по всему кадру; у совсем мелкого текста «размытость» — следствие низкого разрешения
            issues = [i for i in issues if i["code"] != "blur"]
            sev = _grade(metrics["edge_rel"], T["edge_rel"], lower_is_worse=False) if text_h >= 14 else None
            if sev:
                issues.append(_issue("blur", sev))
        # слова, упирающиеся в край кадра, — лист обрезан
        margin = 0.004
        touching = sum(1 for x0, y0, x1, y1, _bh in boxes if x0 < margin * w or y0 < margin * h or x1 > (1 - margin) * w or y1 > (1 - margin) * h)
        metrics["edge_words"] = touching
        warn_n, bad_n = T["edge_words"]
        if touching >= warn_n:
            issues.append(_issue("cropped", "bad" if touching >= bad_n else "warn"))
        angle = abs(float(getattr(page, "deskew_angle", 0.0)))
        metrics["tilt"] = round(angle, 1)
        sev = _grade(angle, T["tilt"], lower_is_worse=False)
        if sev:
            issues.append(_issue("tilt", sev))

    has_physical = any(i["code"] in PHYSICAL for i in issues)
    if reads_well:
        # текст прочитан уверенно: придирки «по пикселям» и поправленный наклон не повод переснимать
        issues = [i for i in issues if i["code"] not in PHYSICAL and i["code"] != "tilt"]
    else:
        if score < 50:  # читается совсем плохо: найденная физическая причина — уже не «замечание», а причина переснять
            for i in issues:
                if i["code"] in PHYSICAL and i["severity"] == "warn":
                    i["severity"] = "bad"
        if n_words >= 8 and not has_physical:
            if score < 45 or mean_conf < 55:
                issues.append(_issue("low_ocr", "bad"))
            elif score < 65:
                issues.append(_issue("low_ocr", "warn"))

    issues.sort(key=lambda i: -SEV_RANK[i["severity"]])
    out = _verdict(issues)
    bad, warn = sum(i["severity"] == "bad" for i in issues), sum(i["severity"] == "warn" for i in issues)
    out["score"] = int(max(0, min(100, round(min(100.0, score + 10) - 15 * bad - 6 * warn))))
    out["metrics"] = metrics
    return out


def assess_document(page_results: list[dict]) -> dict:
    """Сводка по документу: худшая страница определяет уровень; причины объединяются (у каждой — номера страниц, если их несколько)."""
    if not page_results:
        return {"level": "good", "retake": False, "score": 100, "issues": [], "pages": []}
    merged: dict[str, dict] = {}
    for idx, r in enumerate(page_results):
        for i in r.get("issues", []):
            cur = merged.get(i["code"])
            if cur is None or SEV_RANK[i["severity"]] > SEV_RANK[cur["severity"]]:
                merged[i["code"]] = {**i, "pages": [idx]}
            elif SEV_RANK[i["severity"]] == SEV_RANK[cur["severity"]]:
                cur["pages"].append(idx)
    issues = sorted(merged.values(), key=lambda i: -SEV_RANK[i["severity"]])
    worst = max(page_results, key=lambda r: LEVEL_RANK[r["level"]])
    return {
        "level": worst["level"], "retake": worst["level"] == "poor", "score": min(r.get("score", 100) for r in page_results),
        "issues": issues, "pages": [{"level": r["level"], "score": r.get("score"), "metrics": r.get("metrics", {})} for r in page_results],
    }


def summary_line(doc: dict) -> str:
    """Одна фраза для примечания к ответу и для бота: что не так со снимком и что делать."""
    bad = [i for i in doc.get("issues", []) if i["severity"] == "bad"] or [i for i in doc.get("issues", []) if i["severity"] == "warn"]
    if not bad:
        return ""
    what = ", ".join(i["title"].lower() for i in bad[:3])
    return f"Снимок плохого качества ({what}) — часть данных могла быть прочитана неверно. {bad[0]['hint']}"


def retake_text(doc: dict) -> str:
    """Ответ, когда из-за снимка не удалось даже определить тип документа: главное — переснять, а не «выберите тип»."""
    bad = [i for i in doc.get("issues", []) if i["severity"] == "bad"] or doc.get("issues", [])
    what = ", ".join(i["title"].lower() for i in bad[:3])
    hint = bad[0]["hint"] if bad else ""
    return f"Не получилось надёжно прочитать документ: {what}. {hint} Если переснять нельзя, выберите тип документа вручную — я всё равно попробую.".replace("  ", " ")
