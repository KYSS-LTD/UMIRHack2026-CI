"""Приветственная картинка Telegram-бота («Что умеет этот бот?» в пустом чате) в стиле сайта: бумага, чернила, лампа, штамп.

Запуск:  python scripts/make_bot_welcome.py [папка_вывода]
Результат: bot-welcome.png (640x360 — именно этот размер просит @BotFather) и bot-welcome@2x.png (1280x720 для просмотра и печати).
В @BotFather: /setdescriptionpic → выберите бота → отправьте bot-welcome.png.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_bot_avatar import draw as draw_mark  # noqa: E402  знак сайта (печать, лист, галочка) — тот же, что у аватара

W, H = 1280, 720  # рабочие координаты; рисуем в S раз крупнее и уменьшаем
S = 2

INK, INK2, INK3 = (0x14, 0x21, 0x3A), (0x44, 0x50, 0x6A), (0x66, 0x72, 0x90)
PAPER, PAPER_LINE = (0xFF, 0xFD, 0xF8), (0xD8, 0xD1, 0xBF)
OK, OK_SOFT, WARN = (0x18, 0x76, 0x4A), (0xE2, 0xF3, 0xE9), (0xE8, 0x88, 0x3A)
STAMP = (0xC6, 0x3A, 0x25)
LAMP = (0xE0, 0x8A, 0x0B)

SERIF_BOLD = ["C:/Windows/Fonts/palab.ttf", "C:/Windows/Fonts/georgiab.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "/Library/Fonts/Georgia Bold.ttf"]
SANS = ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]
SANS_SEMI = ["C:/Windows/Fonts/seguisb.ttf", "C:/Windows/Fonts/segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
SANS_BLACK = ["C:/Windows/Fonts/seguibl.ttf", "C:/Windows/Fonts/segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Black.ttf"]


def font(paths: list[str], size: float) -> ImageFont.FreeTypeFont:
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, int(round(size * S)))
    return ImageFont.load_default()


def px(v: float) -> int:
    return int(round(v * S))


def background() -> Image.Image:
    """Стол: тёплая бумага, лампа слева сверху, тонкая сетка (как под документом на сайте)."""
    w, h = px(W), px(H)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xn, yn = xx / (w - 1), yy / (h - 1)
    top, bottom = np.array((0xEF, 0xE8, 0xD8), np.float32), np.array((0xE0, 0xD6, 0xC0), np.float32)
    base = top * (1 - yn[..., None]) + bottom * yn[..., None]
    d = np.sqrt(((xn - 0.06) * 1.78) ** 2 + (yn - 0.0) ** 2) / 0.75
    glow = np.clip(1 - d, 0, 1)[..., None] ** 1.6 * 0.38
    out = base * (1 - glow) + np.array((0xF6, 0xC2, 0x6A), np.float32) * glow
    img = Image.fromarray(out.astype(np.uint8), "RGB")
    g = ImageDraw.Draw(img, "RGBA")
    for x in range(0, W + 1, 40):
        g.line((px(x), 0, px(x), h), fill=INK + (11,), width=1)
    for y in range(0, H + 1, 40):
        g.line((0, px(y), w, px(y)), fill=INK + (11,), width=1)
    return img


def shadow(layer: Image.Image, offset=(0, 14), blur=22, alpha=70) -> Image.Image:
    a = layer.getchannel("A").point(lambda v: v * alpha // 255)
    sh = Image.new("RGBA", layer.size, (20, 33, 58, 0))
    sh.putalpha(a)
    sh = ImageChops.offset(sh, px(offset[0]), px(offset[1]))
    return sh.filter(ImageFilter.GaussianBlur(px(blur) / 2))


def paste_layer(base: Image.Image, layer: Image.Image, with_shadow=True, **kw) -> None:
    if with_shadow:
        base.alpha_composite(shadow(layer, **kw))
    base.alpha_composite(layer)


def rounded_tile(mark: Image.Image, size: int, radius: int) -> Image.Image:
    tile = mark.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    tile.putalpha(mask)
    return tile


def document_card() -> Image.Image:
    """Лист документа с подсветкой найденных полей (зелёная — карточка, оранжевая — источник ответа) и штампом."""
    cw, ch = 360, 470
    pad = 80  # поле под тень и поворот
    layer = Image.new("RGBA", (px(cw + 2 * pad), px(ch + 2 * pad)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    ox = oy = pad
    fold = 64
    sheet = [(ox, oy), (ox + cw - fold, oy), (ox + cw, oy + fold), (ox + cw, oy + ch), (ox, oy + ch)]
    d.polygon([(px(x), px(y)) for x, y in sheet], fill=PAPER)
    d.polygon([(px(ox + cw - fold), px(oy)), (px(ox + cw - fold), px(oy + fold)), (px(ox + cw), px(oy + fold))], fill=(0xE4, 0xD9, 0xBD))
    d.line([(px(x), px(y)) for x, y in sheet + [sheet[0]]], fill=PAPER_LINE, width=px(2))

    def bar(x, y, w, h=12, fill=(0xB3, 0xA8, 0x89)):
        d.rounded_rectangle((px(ox + x), px(oy + y), px(ox + x + w), px(oy + y + h)), px(h / 2), fill=fill)

    # шапка документа
    bar(34, 40, 180, 14, INK3 + (255,))
    bar(34, 66, 120, 10, (0xC9, 0xC0, 0xA6))
    # строки «справки»; на двух — подсветка найденных полей
    rows = [(146, 230), (174, 280), (302, 250), (330, 190), (358, 270), (390, 160)]
    for y, w in rows:
        bar(34, y, w, 11, (0xC9, 0xC0, 0xA6))
    # зелёная рамка: поле из карточки
    d.rounded_rectangle((px(ox + 24), px(oy + 104), px(ox + 322), px(oy + 134)), px(8), fill=(46, 160, 90, 40), outline=(46, 160, 90, 255), width=px(3))
    bar(34, 112, 220, 14, (0x2B, 0x5E, 0x45))
    # оранжевая рамка: источник ответа
    d.rounded_rectangle((px(ox + 24), px(oy + 216), px(ox + 322), px(oy + 276)), px(8), fill=(232, 136, 58, 40), outline=WARN + (255,), width=px(3))
    bar(36, 228, 262, 11, (0x6B, 0x5A, 0x3A))
    bar(36, 250, 196, 11, (0x6B, 0x5A, 0x3A))
    # подпись и печать внизу листа
    d.arc((px(ox + 36), px(oy + 410), px(ox + 140), px(oy + 440)), 200, 340, fill=INK2 + (255,), width=px(3))
    d.ellipse((px(ox + 215), px(oy + 395), px(ox + 295), px(oy + 475)), outline=(0x42, 0x58, 0x7F, 200), width=px(3))
    d.ellipse((px(ox + 226), px(oy + 406), px(ox + 284), px(oy + 464)), outline=(0x42, 0x58, 0x7F, 150), width=px(2))
    return layer.rotate(5, resample=Image.BICUBIC, center=(px(ox + cw / 2), px(oy + ch / 2)))


def stamp_layer(text: str) -> Image.Image:
    """Штамп вердикта: рамка киноварью, заглавные буквы (как .status-stamp на сайте)."""
    f = font(SANS_BLACK, 40)
    tw = int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=f) + px(len(text) * 3))
    w, h = tw + px(52), px(78)
    pad = px(20)
    layer = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((pad, pad, pad + w, pad + h), px(8), outline=STAMP + (235,), width=px(6))
    d.rounded_rectangle((pad + px(9), pad + px(9), pad + w - px(9), pad + h - px(9)), px(4), outline=STAMP + (150,), width=px(2))
    x = pad + px(26)
    for ch in text:  # разрядка: у штампа буквы не слипаются
        d.text((x, pad + h / 2), ch, font=f, fill=STAMP + (235,), anchor="lm")
        x += d.textlength(ch, font=f) + px(3)
    return layer.rotate(10, resample=Image.BICUBIC, expand=True)


def chat_bubble() -> Image.Image:
    """Ответ секретаря: вывод и ссылка на пункт Положения."""
    w, h = 430, 150
    pad = 60
    layer = Image.new("RGBA", (px(w + 2 * pad), px(h + 2 * pad)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    box = (px(pad), px(pad), px(pad + w), px(pad + h))
    d.rounded_rectangle(box, px(24), fill=PAPER, outline=PAPER_LINE, width=px(2))
    # хвостик
    d.polygon([(px(pad + 34), px(pad + h - 2)), (px(pad + 20), px(pad + h + 26)), (px(pad + 72), px(pad + h - 2))], fill=PAPER)
    d.line([(px(pad + 34), px(pad + h - 1)), (px(pad + 20), px(pad + h + 26)), (px(pad + 72), px(pad + h - 1))], fill=PAPER_LINE, width=px(2))
    d.line([(px(pad + 35), px(pad + h - 1)), (px(pad + 71), px(pad + h - 1))], fill=PAPER, width=px(4))
    # заголовок вердикта и текст
    d.ellipse((px(pad + 24), px(pad + 22), px(pad + 52), px(pad + 50)), fill=OK_SOFT)
    d.line([(px(pad + 31), px(pad + 36)), (px(pad + 37), px(pad + 42)), (px(pad + 46), px(pad + 30))], fill=OK + (255,), width=px(4), joint="curve")
    d.text((px(pad + 64), px(pad + 36)), "Документ в порядке", font=font(SANS_SEMI, 24), fill=OK + (255,), anchor="lm")
    d.text((px(pad + 24), px(pad + 66)), "Справка содержит все данные,", font=font(SANS, 25), fill=INK + (255,), anchor="lm")
    d.text((px(pad + 24), px(pad + 94)), "которые требует", font=font(SANS, 25), fill=INK + (255,), anchor="lm")
    f = font(SANS_SEMI, 21)
    chip = "п. 3.2 Положения"
    cw = d.textlength(chip, font=f) + px(24)
    cx = px(pad + 24) + d.textlength("которые требует ", font=font(SANS, 25))
    d.rounded_rectangle((cx, px(pad + 80), cx + cw, px(pad + 108)), px(14), fill=(232, 136, 58, 45), outline=WARN + (255,), width=px(2))
    d.text((cx + cw / 2, px(pad + 94)), chip, font=f, fill=(0x8A, 0x4A, 0x12, 255), anchor="mm")
    return layer


def check_icon(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    d.ellipse((px(cx - r), px(cy - r), px(cx + r), px(cy + r)), fill=OK_SOFT, outline=OK + (120,), width=px(2))
    pts = [(cx - r * 0.42, cy + r * 0.02), (cx - r * 0.1, cy + r * 0.34), (cx + r * 0.46, cy - r * 0.3)]
    d.line([(px(x), px(y)) for x, y in pts], fill=OK + (255,), width=px(r * 0.26), joint="curve")
    for x, y in (pts[0], pts[-1]):
        rr = r * 0.13
        d.ellipse((px(x - rr), px(y - rr), px(x + rr), px(y + rr)), fill=OK + (255,))


def build() -> Image.Image:
    img = background().convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")

    # левая колонка: знак, название, три главные возможности
    img.alpha_composite(_tile_on_layer(draw_mark()))
    d = ImageDraw.Draw(img, "RGBA")
    d.text((px(80), px(240)), "Онлайн-деканат", font=font(SERIF_BOLD, 74), fill=INK, anchor="ls")
    d.text((px(83), px(286)), "ЦИФРОВОЙ СЕКРЕТАРЬ", font=font(SANS_SEMI, 24), fill=INK3, anchor="ls")
    items = ["Проверю справку по фото или PDF", "Отвечу по Положению со ссылкой на пункт", "Подготовлю заявление — DOCX прямо в чат"]
    y = 374
    for t in items:
        check_icon(d, 102, y, 22)
        size = 31
        f = font(SANS_SEMI, size)
        while d.textlength(t, font=f) > px(640 - 70) and size > 22:
            size -= 1
            f = font(SANS_SEMI, size)
        d.text((px(144), px(y)), t, font=f, fill=INK, anchor="lm")
        y += 72
    d.text((px(80), px(672)), "Предварительная оценка. Решение принимает деканат.", font=font(SANS, 21), fill=INK3, anchor="lm")

    # правая часть: документ с подсветкой, штамп и ответ в чате
    card = document_card()
    cx, cy = 1018, 268
    paste_layer(img, _centered(card, cx, cy), offset=(0, 18), blur=30, alpha=80)
    bubble = chat_bubble()
    paste_layer(img, _centered(bubble, 985, 584), offset=(0, 10), blur=18, alpha=70)
    stamp = stamp_layer("В ПОРЯДКЕ")
    paste_layer(img, _centered(stamp, 1100, 392), with_shadow=False)
    return img


def _centered(layer: Image.Image, cx: float, cy: float) -> Image.Image:
    full = Image.new("RGBA", (px(W), px(H)), (0, 0, 0, 0))
    full.alpha_composite(layer, (px(cx) - layer.width // 2, px(cy) - layer.height // 2))
    return full


def _tile_on_layer(mark: Image.Image) -> Image.Image:
    full = Image.new("RGBA", (px(W), px(H)), (0, 0, 0, 0))
    tile = rounded_tile(mark, px(104), px(30))
    sh = shadow(_centered_alpha(tile, 132, 116), offset=(0, 8), blur=14, alpha=90)
    full.alpha_composite(sh)
    full.alpha_composite(tile, (px(80), px(64)))
    return full


def _centered_alpha(tile: Image.Image, cx: float, cy: float) -> Image.Image:
    full = Image.new("RGBA", (px(W), px(H)), (0, 0, 0, 0))
    full.alpha_composite(tile, (px(cx) - tile.width // 2, px(cy) - tile.height // 2))
    return full


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "docs" / "telegram"
    out.mkdir(parents=True, exist_ok=True)
    big = build().convert("RGB")
    big.resize((1280, 720), Image.LANCZOS).save(out / "bot-welcome@2x.png", optimize=True)
    big.resize((640, 360), Image.LANCZOS).save(out / "bot-welcome.png", optimize=True)
    print(f"Gotovo: {out / 'bot-welcome.png'} (640x360)")


if __name__ == "__main__":
    main()
