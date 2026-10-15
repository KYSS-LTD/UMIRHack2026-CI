"""Аватар Telegram-бота в стиле сайта: тот же знак (печать с зубчатым краем, лист документа, красная галочка-штамп), но на сплошном квадрате.

Telegram режет аватар по кругу, поэтому фон заливает весь квадрат, а знак целиком умещается в вписанный круг.
Запуск:  python scripts/make_bot_avatar.py [папка_вывода]   → bot-avatar.png (1024×1024), bot-avatar.svg, bot-avatar-preview.png (как увидит Telegram).
В @BotFather: /setuserpic → выберите бота → отправьте bot-avatar.png.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SIZE = 1024
SS = 3  # рисуем в 3 раза крупнее и уменьшаем: гладкие края
U = 40.0  # координаты знака — те же, что в logo.svg (viewBox 0 0 40 40)
SCALE = 1.04  # знак чуть крупнее, чем на сайте: аватар в Telegram маленький

NAVY_A, NAVY_B = (0x27, 0x46, 0x7A), (0x0C, 0x16, 0x29)
AMBER = (0xF6, 0xB2, 0x4C)
PAPER, FOLD, LINES, STAMP = (0xFF, 0xFD, 0xF8), (0xE4, 0xD9, 0xBD), (0xB3, 0xA8, 0x89), (0xEC, 0x4A, 0x31)


def _tf(x: float, y: float) -> tuple[float, float]:
    """Координаты знака (0..40) -> пиксели, с увеличением вокруг центра."""
    k = SIZE * SS / U
    return ((x - 20) * SCALE + 20) * k, ((y - 20) * SCALE + 20) * k


def _rot(x: float, y: float, deg: float = -7.0, cx: float = 20.0, cy: float = 20.0) -> tuple[float, float]:
    a = math.radians(deg)
    return cx + (x - cx) * math.cos(a) - (y - cy) * math.sin(a), cy + (x - cx) * math.sin(a) + (y - cy) * math.cos(a)


def _background(px: int) -> Image.Image:
    yy, xx = np.mgrid[0:px, 0:px].astype(np.float32) / (px - 1)
    t = ((xx + yy) / 2.0)[..., None]  # диагональный градиент, как у плитки на сайте
    base = np.array(NAVY_A, np.float32) * (1 - t) + np.array(NAVY_B, np.float32) * t
    d = np.sqrt((xx - 0.28) ** 2 + (yy - 0.18) ** 2) / 0.95  # тёплое «свечение лампы» слева сверху
    glow = np.clip(1 - d / 0.85, 0, 1)[..., None] * 0.26
    out = base * (1 - glow) + np.array(AMBER, np.float32) * glow
    return Image.fromarray(out.astype(np.uint8), "RGB")


def _round_line(d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], width: float, fill) -> None:
    d.line(pts, fill=fill, width=int(round(width)), joint="curve")
    r = width / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def draw() -> Image.Image:
    px = SIZE * SS
    img = _background(px)
    d = ImageDraw.Draw(img, "RGBA")
    k = px / U

    # зубчатая кайма печати: пунктир 1.5 / 2.5 по окружности r = 16.4
    ring_r, dash, gap = 16.4, 1.5, 2.5
    circ = 2 * math.pi * ring_r
    n = int(round(circ / (dash + gap)))
    step = 2 * math.pi / n
    ring = Image.new("L", (px, px), 0)  # штрихи рисуем непрозрачными на отдельном слое и накладываем целиком: перекрытия не темнеют
    rd = ImageDraw.Draw(ring)
    for i in range(n):
        a0 = i * step
        a1 = a0 + step * dash / (dash + gap)
        r = 1.3 * k * SCALE / 2
        for a in np.linspace(a0, a1, 40):  # штрих — цепочка кружков: сплошной, без швов на стыках
            x, y = _tf(20 + ring_r * math.cos(a), 20 + ring_r * math.sin(a))
            rd.ellipse((x - r, y - r, x + r, y + r), fill=255)
    img.paste(Image.new("RGB", (px, px), AMBER), mask=ring.point(lambda v: int(v * 0.8)))
    d = ImageDraw.Draw(img, "RGBA")

    # лист документа (повёрнут на −7°), загнутый уголок и строки текста
    sheet = [(12.8, 10.2), (22.4, 10.2), (27.6, 15.4), (27.6, 30.6), (12.8, 30.6)]
    d.polygon([_tf(*_rot(*p)) for p in sheet], fill=PAPER)
    d.polygon([_tf(*_rot(*p)) for p in [(22.4, 10.2), (22.4, 15.4), (27.6, 15.4)]], fill=FOLD)
    for a, b in (((15.8, 19.2), (23.6, 19.2)), ((15.8, 22.8), (21.0, 22.8))):
        _round_line(d, [_tf(*_rot(*a)), _tf(*_rot(*b))], 1.5 * k * SCALE, LINES)

    # красная галочка-штамп поверх листа
    _round_line(d, [_tf(16.6, 26.6), _tf(20.6, 30.6), _tf(28.6, 21.0)], 3.3 * k * SCALE, STAMP)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#27467a"/><stop offset="1" stop-color="#0c1629"/></linearGradient>
    <radialGradient id="glow" cx=".28" cy=".18" r=".95"><stop offset="0" stop-color="#f6b24c" stop-opacity=".42"/><stop offset="1" stop-color="#f6b24c" stop-opacity="0"/></radialGradient>
  </defs>
  <rect width="1024" height="1024" fill="url(#bg)"/>
  <rect width="1024" height="1024" fill="url(#glow)"/>
  <g transform="translate(512 512) scale({s}) translate(-512 -512) scale(25.6)">
    <circle cx="20" cy="20" r="16.4" fill="none" stroke="#f6b24c" stroke-opacity=".75" stroke-width="1.3" stroke-dasharray="1.5 2.5" stroke-linecap="round"/>
    <g transform="rotate(-7 20 20)">
      <path d="M12.8 10.2h9.6l5.2 5.2v15.2H12.8z" fill="#fffdf8"/>
      <path d="M22.4 10.2v5.2h5.2z" fill="#e4d9bd"/>
      <path d="M15.8 19.2h7.8M15.8 22.8h5.2" stroke="#b3a889" stroke-width="1.5" stroke-linecap="round"/>
    </g>
    <path d="M16.6 26.6l4 4 8-9.6" fill="none" stroke="#ec4a31" stroke-width="3.3" stroke-linecap="round" stroke-linejoin="round"/>
  </g>
</svg>
""".replace("{s}", str(SCALE))


def preview(img: Image.Image) -> Image.Image:
    """Как аватар выглядит в Telegram: круг на светлой и на тёмной подложке рядом, в трёх размерах."""
    sizes = (256, 96, 40)
    W = sum(sizes) + 40 * (len(sizes) + 1)
    canvas = Image.new("RGB", (W, 2 * 300), (0, 0, 0))
    for row, bg in enumerate(((245, 240, 230), (20, 24, 40))):
        ImageDraw.Draw(canvas).rectangle((0, row * 300, W, row * 300 + 300), fill=bg)
        x = 40
        for s in sizes:
            small = img.resize((s * 3, s * 3), Image.LANCZOS)
            mask = Image.new("L", small.size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, small.width - 1, small.height - 1), fill=255)
            small.putalpha(mask)
            small = small.resize((s, s), Image.LANCZOS)
            canvas.paste(small, (x, row * 300 + (300 - s) // 2), small)
            x += s + 40
    return canvas


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "docs" / "telegram"
    out.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(out / "bot-avatar.png", optimize=True)
    (out / "bot-avatar.svg").write_text(SVG, encoding="utf-8")
    preview(img).save(out / "bot-avatar-preview.png", optimize=True)
    print(f"Готово: {out / 'bot-avatar.png'} ({SIZE}x{SIZE})")


if __name__ == "__main__":
    main()
