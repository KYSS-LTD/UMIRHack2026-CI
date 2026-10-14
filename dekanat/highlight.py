"""Подсветка на снимке: где найдено каждое поле и откуда взят ответ."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .models import Box

_FONTS = [
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
]

PALETTE = {"found": (46, 160, 90), "answer": (232, 136, 58), "mark": (110, 70, 190), "weak": (214, 160, 30)}


def _font(size: int) -> ImageFont.ImageFont:
    for f in _FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _grow(quad, pad: float) -> list[tuple[float, float]]:
    """Наклонную рамку чуть раздвигаем от центра — так же, как прямоугольную, чтобы она не прилипала к буквам."""
    cx, cy = sum(p[0] for p in quad) / len(quad), sum(p[1] for p in quad) / len(quad)
    out = []
    for x, y in quad:
        dist = max(float(np.hypot(x - cx, y - cy)), 1.0)
        k = (dist + pad * 1.4) / dist
        out.append((cx + (x - cx) * k, cy + (y - cy) * k))
    return out


def draw_overlay(image_bgr: np.ndarray, items: list[tuple[Box, str, tuple]], page: int = 0) -> np.ndarray:
    """items: (рамка, подпись, цвет RGB). Рисует только рамки нужной страницы."""
    h, w = image_bgr.shape[:2]
    pil = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)).convert("RGBA")
    layer = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    size = max(14, int(w / 70))
    font = _font(size)
    lw = max(2, int(w / 400))
    for box, label, color in items:
        if box.page != page:
            continue
        pad = lw * 2
        x0, y0, x1, y1 = box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad
        if box.quad:  # наклонный снимок: рамка идёт вдоль строки, а не по описанному прямоугольнику; подпись — у её левого верхнего угла
            pts = _grow(box.quad, pad)
            d.polygon(pts, fill=color + (45,))
            d.line(pts + [pts[0]], fill=color + (255,), width=lw, joint="curve")
            rising = pts[1][1] < pts[0][1]  # строка идёт вверх вправо: плашка над левым концом легла бы на сам текст — ставим её под левым концом
            x0, y0, y1 = (pts[3][0], pts[3][1], pts[3][1]) if rising else (pts[0][0], pts[0][1], pts[3][1])
        else:
            rising = False
            d.rectangle([x0, y0, x1, y1], outline=color + (255,), width=lw, fill=color + (45,))
        if label:
            tw = d.textlength(label, font=font)
            ty = y0 - size - 6 if (y0 > size + 8 and not rising) else y1 + 2
            d.rectangle([x0, ty, x0 + tw + 10, ty + size + 6], fill=color + (235,))
            d.text((x0 + 5, ty + 2), label, font=font, fill=(255, 255, 255, 255))
    out = Image.alpha_composite(pil, layer).convert("RGB")
    return cv2.cvtColor(np.array(out), cv2.COLOR_RGB2BGR)


def overlay_items(fields, answer=None) -> list[tuple[Box, str, tuple]]:
    items = []
    for f in fields:
        if f.parts:
            for p in f.parts:
                for b in p.boxes:
                    items.append((b, f"{p.title}", PALETTE["mark"] if f.kind == "marks" else PALETTE["found"]))
        else:
            for b in f.boxes:
                items.append((b, f.title, PALETTE["found"] if f.confidence >= 0.5 else PALETTE["weak"]))
    if answer is not None:
        for s in answer.sources:
            for b in s.boxes:
                items.append((b, "источник ответа", PALETTE["answer"]))
    return items
