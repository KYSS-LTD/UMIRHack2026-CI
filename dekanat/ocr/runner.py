"""Оркестрация OCR: выпрямление фото, перебор вариантов предобработки, выбор лучшего результата."""

from __future__ import annotations

import logging
import re
import statistics
import time
from dataclasses import dataclass
from typing import Callable, Optional

import cv2
import numpy as np

from ..models import OcrPage, Row, Word
from . import preprocess as pp
from .engine import TesseractEngine

log = logging.getLogger(__name__)

_WORDLIKE = re.compile(r"^(?:[A-Za-zА-Яа-яЁё]{2,}(?:-[A-Za-zА-Яа-яЁё]+)?[.,:;)]?|\(?\d[\d./\-:,]*\)?|№|[A-Za-zА-Яа-яЁё]\.)$")


def quality(words: list[Word]) -> dict:
    """Оценка качества распознавания без эталона."""
    if not words:
        return {"score": 0.0, "mean_conf": 0.0, "wordlike": 0.0, "words": 0, "good_chars": 0.0, "median_h": 0.0}
    total = sum(len(w.text) for w in words) or 1
    mean_conf = sum(w.conf * len(w.text) for w in words) / total
    good = [w for w in words if _WORDLIKE.match(w.text.strip("«»\"'")) and w.conf >= 45]
    wordlike = sum(len(w.text) for w in good) / total
    good_chars = sum(len(w.text) * w.conf / 100.0 for w in good)
    score = mean_conf * (0.35 + 0.65 * wordlike) * min(1.0, len(words) / 10)
    return {
        "score": round(score, 1), "mean_conf": round(mean_conf, 1), "wordlike": round(wordlike, 3), "words": len(words),
        "good_chars": round(good_chars, 1), "median_h": statistics.median(w.h for w in words),
    }


_GLUED = re.compile(r"^([A-Za-zА-Яа-яЁё]{2,}):([^\s:]{2,})$")


def split_glued(words: list[Word]) -> list[Word]:
    """«курс:ЮР-32,» -> «курс:» + «ЮР-32,» (Tesseract иногда не ставит пробел после двоеточия)."""
    out = []
    for w in words:
        m = _GLUED.match(w.text)
        if not m:
            out.append(w)
            continue
        cut = len(m.group(1)) + 1
        xm = w.x0 + (w.x1 - w.x0) * cut / len(w.text)
        out.append(Word(w.text[:cut], w.conf, w.x0, w.y0, xm, w.y1))
        out.append(Word(w.text[cut:], w.conf, xm + 1, w.y0, w.x1, w.y1))
    return out


def build_rows(words: list[Word], page: int = 0, min_conf: float = 12.0) -> list[Row]:
    """Группировка слов в строки по вертикальному положению (ячейки таблицы попадают в одну строку)."""
    words = split_glued(words)
    ws = sorted((w for w in words if w.conf >= min_conf), key=lambda w: w.yc)
    rows: list[list[Word]] = []
    for w in ws:
        if rows:
            cur = rows[-1]
            cy = statistics.fmean(x.yc for x in cur)
            ch = statistics.median(x.h for x in cur)
            if abs(w.yc - cy) <= 0.55 * max(ch, w.h):
                cur.append(w)
                continue
        rows.append([w])
    out = []
    for r in rows:
        r.sort(key=lambda w: w.x0)
        out.append(Row(r, page))
    out.sort(key=lambda r: statistics.fmean(w.yc for w in r.words))
    return out


@dataclass
class OcrOptions:
    good_score: float = 70.0
    fast: bool = False
    psm: int = 6
    budget_seconds: float = 25.0  # после этого перебор вариантов предобработки прекращается


class OcrRunner:
    def __init__(self, engine: TesseractEngine, options: Optional[OcrOptions] = None):
        self.engine = engine
        self.opts = options or OcrOptions()

    # ------------------------------------------------------------------ геометрия
    def _geometries(self, img: np.ndarray, M: np.ndarray):
        """Кандидаты выравнивания: (имя, цветное нормализованное изображение, матрица, угол)."""
        quad = pp.find_page_quad(img)
        if quad is not None:
            warped, Mq = pp.warp_quad(img, quad)
            warped, Ms = pp.resize_for_ocr(warped)
            cn = pp.normalize_illumination(warped)
            angle, conf = pp.estimate_skew(pp.to_gray(cn))
            Mt = Ms @ Mq @ M
            if angle:
                cn, Mr = pp.rotate_expand(cn, angle)
                Mt = Mr @ Mt
            yield "page+deskew" if angle else "page", cn, Mt, angle
        cn = pp.normalize_illumination(img)
        angle, conf = pp.estimate_skew(pp.to_gray(cn))
        Mt = M
        if angle:
            cn, Mr = pp.rotate_expand(cn, angle)
            Mt = Mr @ M
        yield "deskew" if angle else "straight", cn, Mt, angle

    # ------------------------------------------------------------------ основной проход
    def run_page(self, bgr: np.ndarray, index: int = 0, step: Optional[Callable[[str], None]] = None) -> OcrPage:
        """step(описание) вызывается перед каждым проходом OCR — для индикатора прогресса (и для отмены: исключение прерывает работу)."""
        t0 = time.time()
        step = step or (lambda _msg: None)
        orig_h, orig_w = bgr.shape[:2]
        img = pp.limit_size(bgr)
        M = pp.scale_matrix(img.shape[1] / orig_w)
        img, M1 = pp.resize_for_ocr(img)
        M = M1 @ M

        best = None  # (good_chars, words, variant_name, cn, Mt, angle, geo, q)
        tried: list[dict] = []
        variants = ["clean"] if self.opts.fast else ["clean", "binary", "deglare", "clahe"]
        stop = False
        for geo, cn, Mt, angle in self._geometries(img, M):
            for vname in variants:
                if best is not None and time.time() - t0 > self.opts.budget_seconds:
                    stop = True
                    break
                if vname == "deglare" and pp.glare_fraction(cn) < 0.002:
                    continue  # бликов нет — этот вариант ничего не даст
                step(f"{geo} · {vname}")
                gray = pp.make_variant(cn, vname)
                words = self.engine.recognize(gray, self.opts.psm)
                q = quality(words)
                tried.append({"geometry": geo, "variant": vname, **{k: q[k] for k in ("score", "mean_conf", "wordlike", "words")}})
                if best is None or q["good_chars"] > best[0]:
                    best = (q["good_chars"], words, f"{geo}/{vname}", cn, Mt, angle, geo, q)
                if q["score"] >= self.opts.good_score:
                    stop = True
                    break
            if stop or self.opts.fast:
                break
        assert best is not None
        _, words, vname, cn, Mt, angle, geo, q = best
        rotation = 0

        # страница повёрнута на 90/180/270 -> OSD
        if q["score"] < 45 and not self.opts.fast:
            step("проверяю поворот страницы")
            deg, conf = self.engine.detect_orientation(pp.to_gray(cn))
            if deg:
                cn2, Mr = pp.rotate90(cn, deg)
                gray = pp.make_variant(cn2, vname.split("/")[-1])
                words2 = self.engine.recognize(gray, self.opts.psm)
                q2 = quality(words2)
                tried.append({"geometry": f"{geo}+rot{deg}", "variant": vname.split("/")[-1], **{k: q2[k] for k in ("score", "mean_conf", "wordlike", "words")}})
                if q2["good_chars"] > q["good_chars"]:
                    words, q, cn, Mt, rotation = words2, q2, cn2, Mr @ Mt, deg
        # слишком мелкий текст -> увеличиваем
        if q["median_h"] and q["median_h"] < 20 and not self.opts.fast:
            step("увеличиваю мелкий текст")
            up = float(np.clip(28.0 / q["median_h"], 1.3, 3.0))
            cn3 = cv2.resize(cn, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
            gray = pp.make_variant(cn3, vname.split("/")[-1])
            words3 = self.engine.recognize(gray, self.opts.psm)
            q3 = quality(words3)
            tried.append({"geometry": "upscale", "variant": vname.split("/")[-1], **{k: q3[k] for k in ("score", "mean_conf", "wordlike", "words")}})
            if q3["good_chars"] > q["good_chars"]:
                words, q, cn, Mt = words3, q3, cn3, pp.scale_matrix(up) @ Mt
        rows = build_rows(words, index)
        q["seconds"] = round(time.time() - t0, 2)
        q["tried"] = tried
        return OcrPage(
            index=index, width=cn.shape[1], height=cn.shape[0], words=words, rows=rows, transform=Mt,
            orig_size=(orig_w, orig_h), engine=self.engine.name, variant=vname, quality=q,
            deskew_angle=round(float(angle), 2), rotation=rotation, image=cn,
        )

    def run(self, pages: list[np.ndarray], step: Optional[Callable[[int, str], None]] = None) -> list[OcrPage]:
        return [self.run_page(p, i, (lambda msg, i=i: step(i, msg)) if step else None) for i, p in enumerate(pages)]
