"""Генератор синтетических студенческих документов с эталонной разметкой.

Нужен для тестов и оценки качества: организаторские данные в проект не входят, поэтому
мы сами рисуем справки/заявления трёх типов в трёх вёрстках, добавляем «дефекты»
(нет даты, печати, подписи...) и портим картинку под фото на телефон.

    python -m dekanat synth --out data/synthetic --n 6 --levels 0,1,2 --seed 7
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

PAGE_W, PAGE_H = 1240, 1754
MARGIN = 118

_FONT_DIRS = [
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/TTF"),
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
]
_FONT_FAMILIES = [
    ("arial.ttf", "arialbd.ttf"),
    ("times.ttf", "timesbd.ttf"),
    ("tahoma.ttf", "tahomabd.ttf"),
    ("verdana.ttf", "verdanab.ttf"),
    ("calibri.ttf", "calibrib.ttf"),
    ("segoeui.ttf", "segoeuib.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf"),
    ("Arial.ttf", "Arial Bold.ttf"),
]


def find_font_families() -> list[tuple[Path, Path]]:
    found = []
    for reg, bold in _FONT_FAMILIES:
        for d in _FONT_DIRS:
            if (d / reg).exists() and (d / bold).exists():
                found.append((d / reg, d / bold))
                break
    if not found:
        raise RuntimeError("Не найден ни один TrueType-шрифт с кириллицей (Arial, DejaVu, Liberation...)")
    return found


# ----------------------------------------------------------------------------- данные

SURNAMES = ["Иванов", "Петров", "Сидоров", "Смирнов", "Кузнецов", "Попов", "Васильев", "Соколов", "Михайлов", "Новиков",
            "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров", "Павлов", "Козлов", "Степанов",
            "Николаев", "Орлов", "Андреев", "Макаров", "Никитин", "Захаров", "Зайцев", "Соловьёв", "Борисов", "Яковлев"]
MALE_NAMES = ["Александр", "Дмитрий", "Максим", "Иван", "Артём", "Михаил", "Даниил", "Кирилл", "Андрей", "Никита", "Егор", "Илья"]
FEMALE_NAMES = ["Анна", "Мария", "Екатерина", "Ольга", "Елена", "Дарья", "Полина", "Виктория", "Алина", "Ксения", "София", "Татьяна"]
MALE_PATR = ["Александрович", "Дмитриевич", "Сергеевич", "Иванович", "Андреевич", "Николаевич", "Владимирович", "Алексеевич", "Михайлович", "Павлович"]
FEMALE_PATR = ["Александровна", "Дмитриевна", "Сергеевна", "Ивановна", "Андреевна", "Николаевна", "Владимировна", "Алексеевна", "Михайловна", "Павловна"]

UNIVERSITIES = [
    "Приволжский государственный технический университет",
    "Южно-Донской государственный университет",
    "Северо-Кавказский федеральный университет имени академика Ломова",
    "Волжский государственный университет экономики и сервиса",
    "Уральский политехнический университет имени Н. Н. Орлова",
    "Сибирский государственный аграрный университет",
    "Балтийский университет информационных технологий",
    "Кубанский институт управления и права",
]
DIRECTIONS = [
    "09.03.01 Информатика и вычислительная техника", "38.03.01 Экономика", "40.03.01 Юриспруденция",
    "01.03.02 Прикладная математика и информатика", "15.03.04 Автоматизация технологических процессов и производств",
    "38.03.02 Менеджмент", "42.03.01 Реклама и связи с общественностью", "09.03.03 Прикладная информатика",
    "03.03.02 Физика", "13.03.02 Электроэнергетика и электротехника",
]
STUDY_FORMS = ["очная", "заочная", "очно-заочная"]
ORGS = ["ООО «Вектор»", "АО «ТехноСофт»", "ПАО «ЮгСтрой»", "ООО «Ромашка Плюс»", "ГБУЗ «Городская больница №3»",
        "МБОУ «Лицей №12»", "АО «Севернефть»", "ООО «Дон-Логистик»", "ИП Кузьмин А. В."]
GRADES = ["отлично", "хорошо", "удовлетворительно", "зачтено"]
REASONS = ["по состоянию здоровья", "по семейным обстоятельствам", "в связи с призывом на военную службу",
           "в связи с рождением ребёнка"]
GROUP_PREFIXES = ["ИВТ", "ЭК", "ЮР", "ПМИ", "АТП", "МН", "РК", "ПИ", "ФЗ", "ЭЭ"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]

MARK_COLORS = [(25, 55, 160), (45, 40, 150), (110, 40, 140), (170, 30, 40)]  # синие, фиолетовые, красные чернила


@dataclass
class Person:
    surname: str
    name: str
    patronymic: str
    female: bool

    @property
    def fio(self) -> str:
        return f"{self.surname} {self.name} {self.patronymic}"

    @property
    def short(self) -> str:
        return f"{self.surname} {self.name[0]}.{self.patronymic[0]}."

    @property
    def short_rev(self) -> str:
        return f"{self.name[0]}.{self.patronymic[0]}. {self.surname}"


def make_person(rng: random.Random, female: Optional[bool] = None) -> Person:
    female = rng.random() < 0.5 if female is None else female
    surname = rng.choice(SURNAMES)
    if female:
        surname += "а"
        return Person(surname, rng.choice(FEMALE_NAMES), rng.choice(FEMALE_PATR), True)
    return Person(surname, rng.choice(MALE_NAMES), rng.choice(MALE_PATR), False)


def fmt_date(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def fmt_date_long(d: dt.date) -> str:
    return f"«{d.day:02d}» {MONTHS_GEN[d.month - 1]} {d.year} г."


def rand_date(rng: random.Random, start: dt.date, end: dt.date) -> dt.date:
    return start + dt.timedelta(days=rng.randint(0, (end - start).days))


# ----------------------------------------------------------------------------- холст


class Canvas:
    def __init__(self, rng: random.Random, fonts: tuple[Path, Path], size: int):
        self.rng = rng
        self.img = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
        self.draw = ImageDraw.Draw(self.img)
        self.reg_path, self.bold_path = fonts
        self.size = size
        self._cache: dict = {}
        self.ink = rng.choice([(15, 15, 15), (25, 25, 30), (35, 35, 35), (0, 0, 0)])

    def font(self, size: Optional[int] = None, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size or self.size, bold)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(str(self.bold_path if bold else self.reg_path), key[0])
        return self._cache[key]

    def text(self, x: float, y: float, s: str, bold: bool = False, size: Optional[int] = None, fill=None, anchor: str = "la"):
        self.draw.text((x, y), s, font=self.font(size, bold), fill=fill or self.ink, anchor=anchor)

    def width(self, s: str, bold: bool = False, size: Optional[int] = None) -> float:
        return self.font(size, bold).getlength(s)

    def center(self, y: float, s: str, bold: bool = False, size: Optional[int] = None):
        self.text(PAGE_W / 2, y, s, bold, size, anchor="ma")

    def wrap(self, s: str, max_w: float, bold: bool = False, size: Optional[int] = None) -> list[str]:
        lines, cur = [], ""
        for word in s.split():
            trial = f"{cur} {word}".strip()
            if self.width(trial, bold, size) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    @property
    def lh(self) -> float:
        return self.size * 1.55

    def para(self, x: float, y: float, s: str, max_w: float, bold: bool = False, indent: float = 0, size: Optional[int] = None) -> float:
        first = True
        for line in self.wrap(s, max_w - (indent if first else 0), bold, size):
            self.text(x + (indent if first else 0), y, line, bold, size)
            y += (size or self.size) * 1.55
            first = False
        return y


def draw_stamp(img: Image.Image, cx: int, cy: int, radius: int, rng: random.Random, org: str, kind: str = "round"):
    """Печать: круглая (двойное кольцо, текст по кругу, звезда) или прямоугольная."""
    color = rng.choice(MARK_COLORS)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    alpha = rng.randint(185, 235)
    ink = color + (alpha,)
    if kind == "round":
        d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=ink, width=4)
        r2 = radius - 14
        d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], outline=ink, width=2)
        r3 = int(radius * 0.52)
        d.ellipse([cx - r3, cy - r3, cx + r3, cy + r3], outline=ink, width=2)
        fonts = find_font_families()[0]
        font = ImageFont.truetype(str(fonts[1]), max(15, radius // 6))
        text = (org.upper() + " * ").replace("«", "").replace("»", "")
        text = (text * 3)[: max(24, int(2 * math.pi * (radius - 36) / (font.size * 0.62)))]
        n = len(text)
        rr = radius - 34
        start_ang = rng.uniform(0, 360)
        for i, ch in enumerate(text):
            ang = math.radians(start_ang + 360.0 * i / n)
            gx, gy = cx + rr * math.cos(ang), cy + rr * math.sin(ang)
            tile = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((30, 30), ch, font=font, fill=ink, anchor="mm")
            tile = tile.rotate(-math.degrees(ang) - 90, resample=Image.BICUBIC)
            layer.alpha_composite(tile, (int(gx - 30), int(gy - 30)))
        # звезда в центре
        pts = []
        for k in range(10):
            rad = r3 * 0.8 if k % 2 == 0 else r3 * 0.34
            a = math.radians(-90 + 36 * k)
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        d.polygon(pts, outline=ink, width=2)
    else:
        w, h = int(radius * 2.1), int(radius * 1.35)
        d.rectangle([cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2], outline=ink, width=4)
        d.rectangle([cx - w // 2 + 8, cy - h // 2 + 8, cx + w // 2 - 8, cy + h // 2 - 8], outline=ink, width=2)
        fonts = find_font_families()[0]
        name = org.replace("«", "").replace("»", "")
        size = max(15, radius // 5)
        f1 = ImageFont.truetype(str(fonts[1]), size)
        while f1.getlength(name) > w - 30 and size > 9:  # название должно умещаться в рамке печати
            size -= 1
            f1 = ImageFont.truetype(str(fonts[1]), size)
        if f1.getlength(name) > w - 30:
            words = name.split()
            name = " ".join(words[:2]) + "…"
        d.text((cx, cy - h // 6), name, font=f1, fill=ink, anchor="mm")
        d.text((cx, cy + h // 6), "ИНН 6100" + str(rng.randint(100000, 999999)), font=f1, fill=ink, anchor="mm")
    layer = layer.rotate(rng.uniform(-14, 14), center=(cx, cy), resample=Image.BICUBIC)
    # «шероховатость» оттиска
    arr = np.array(layer)
    noise = np.random.default_rng(rng.randint(0, 10**6)).random(arr.shape[:2])
    arr[..., 3] = (arr[..., 3] * np.where(noise < 0.07, 0.35, 1.0)).astype(np.uint8)
    img.paste(Image.fromarray(arr), (0, 0), Image.fromarray(arr))


def draw_signature(img: Image.Image, x0: int, y0: int, w: int, h: int, rng: random.Random):
    """Рукописная подпись: сглаженная случайная кривая."""
    color = rng.choice([(20, 30, 120), (10, 10, 10), (30, 30, 140), (60, 60, 60)])
    n = rng.randint(7, 11)
    xs = np.linspace(x0, x0 + w, n) + np.array([rng.uniform(-w * 0.05, w * 0.05) for _ in range(n)])
    ys = np.array([y0 + h * rng.uniform(0.05, 0.95) for _ in range(n)])
    pts = np.stack([xs, ys], axis=1).astype(np.float32)
    # сплайн Катмулла–Рома
    curve = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for t in np.linspace(0, 1, 18):
            curve.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    d = ImageDraw.Draw(img)
    width = rng.choice([2, 3, 3, 4])
    d.line([tuple(p) for p in curve], fill=color, width=width, joint="curve")
    # росчерк-подчёркивание
    d.line([(x0 + w * 0.1, y0 + h * 1.02), (x0 + w * 1.0, y0 + h * rng.uniform(0.85, 1.0))], fill=color, width=max(2, width - 1))


# ----------------------------------------------------------------------------- содержимое документов


@dataclass
class DocSpec:
    doc_type: str
    layout: str
    units: dict  # эталон: "field" / "field.part" -> значение | bool | None
    present: dict  # "field" / "field.part" -> присутствует ли в документе
    meta: dict = field(default_factory=dict)


def _pick_defects(rng: random.Random, units: list[str]) -> set[str]:
    r = rng.random()
    if r < 0.38:
        return set()
    weights = {u: (3 if u.split(".")[-1] in ("date", "stamp", "signature", "number") else 1) for u in units}
    pool = [u for u in units for _ in range(weights[u])]
    first = rng.choice(pool)
    out = {first}
    if r > 0.82:
        second = rng.choice([u for u in pool if u != first])
        out.add(second)
    return out


STUDY_UNITS = ["student_name", "university", "direction", "study_form", "study_period", "issue.number", "issue.date", "seal_signature.signature", "seal_signature.stamp"]
LEAVE_UNITS = ["student_name", "group_course.group", "group_course.course", "reason", "start_date", "end_date", "application_date", "student_signature.signature"]
PRACTICE_UNITS = ["student_name", "organization", "period", "supervisor", "grade", "issue.number", "issue.date", "seal_signature.signature", "seal_signature.stamp"]
ALL_UNITS = {"study_certificate": STUDY_UNITS, "academic_leave": LEAVE_UNITS, "practice_certificate": PRACTICE_UNITS}
DOC_TITLES = {"study_certificate": "Справка об обучении", "academic_leave": "Заявление на академический отпуск", "practice_certificate": "Справка о практике"}


def make_spec(rng: random.Random, doc_type: str, layout: str, absent_override: Optional[set] = None) -> DocSpec:
    person = make_person(rng)
    absent = set(absent_override) if absent_override is not None else _pick_defects(rng, ALL_UNITS[doc_type])
    units: dict = {}
    meta: dict = {"person": person.fio, "absent": sorted(absent)}
    today = dt.date(2024, 1, 10)
    if doc_type == "study_certificate":
        y = rng.choice([2020, 2021, 2022, 2023])
        start, end = dt.date(y, 9, 1), dt.date(y + rng.choice([4, 4, 5]), rng.choice([6, 7]), 30)
        issue = rand_date(rng, dt.date(2024, 1, 10), dt.date(2025, 6, 30))
        units = {
            "student_name": person.fio,
            "university": rng.choice(UNIVERSITIES),
            "direction": rng.choice(DIRECTIONS),
            "study_form": rng.choice(STUDY_FORMS),
            "study_period": f"{fmt_date(start)} – {fmt_date(end)}",
            "issue.number": str(rng.randint(10, 9999)),
            "issue.date": fmt_date(issue),
            "seal_signature.signature": True,
            "seal_signature.stamp": True,
        }
        meta["head"] = make_person(rng, False)
    elif doc_type == "academic_leave":
        start = rand_date(rng, dt.date(2024, 2, 1), dt.date(2025, 8, 30))
        end = start + dt.timedelta(days=rng.choice([120, 180, 270, 365]) - 1)
        units = {
            "student_name": person.fio,
            "group_course.group": f"{rng.choice(GROUP_PREFIXES)}-{rng.randint(1, 4)}{rng.randint(1, 4)}",
            "group_course.course": str(rng.randint(1, 4)),
            "reason": rng.choice(REASONS),
            "start_date": fmt_date(start),
            "end_date": fmt_date(end),
            "application_date": fmt_date(start - dt.timedelta(days=rng.randint(3, 40))),
            "student_signature.signature": True,
        }
        meta["head"] = make_person(rng, False)
    else:
        start = rand_date(rng, dt.date(2023, 6, 1), dt.date(2025, 6, 30))
        end = start + dt.timedelta(days=rng.choice([13, 20, 27, 41]))
        units = {
            "student_name": person.fio,
            "organization": rng.choice(ORGS),
            "period": f"{fmt_date(start)} – {fmt_date(end)}",
            "supervisor": make_person(rng).fio,
            "grade": rng.choice(GRADES),
            "issue.number": str(rng.randint(10, 9999)),
            "issue.date": fmt_date(end + dt.timedelta(days=rng.randint(1, 20))),
            "seal_signature.signature": True,
            "seal_signature.stamp": True,
        }
        meta["head"] = make_person(rng, False)
    present = {u: (u not in absent) for u in units}
    # составное поле ради совместимости считается присутствующим, если есть хоть одна часть
    for u in absent:
        units[u] = None if not isinstance(units[u], bool) else False
    for u in list(units):
        if isinstance(units[u], bool):
            units[u] = present[u]
    meta["person_obj"] = person
    return DocSpec(doc_type, layout, units, present, meta)


def _issue_strings(spec: DocSpec, rng: random.Random):
    num = spec.units.get("issue.number")
    date = spec.units.get("issue.date")
    return num, date


def _rows_for(spec: DocSpec, rng: random.Random) -> list[tuple[str, str]]:
    """Строки «поле: значение» для вёрстки form/table."""
    u = spec.units
    rows: list[tuple[str, str]] = []

    def add(label: str, key: str, value: Optional[str] = None):
        v = value if value is not None else u.get(key)
        if v is None or v is False:
            if rng.random() < 0.5:
                rows.append((label, "_" * rng.randint(10, 18)))
            return
        rows.append((label, str(v)))

    if spec.doc_type == "study_certificate":
        add("ФИО студента", "student_name")
        add("Название вуза", "university")
        add("Направление подготовки", "direction")
        add("Форма обучения", "study_form")
        add("Период обучения", "study_period", ("с " + u["study_period"].replace(" – ", " по ")) if u.get("study_period") and rng.random() < 0.5 else None)
    elif spec.doc_type == "academic_leave":
        add("ФИО студента", "student_name")
        g, c = u.get("group_course.group"), u.get("group_course.course")
        if g and c:
            rows.append(("Группа и курс", f"{g}, {c} курс"))
        elif g:
            rows.append(("Группа и курс", f"{g}"))
        elif c:
            rows.append(("Группа и курс", f"{c} курс"))
        add("Причина отпуска", "reason")
        add("Дата начала отпуска", "start_date")
        add("Дата окончания отпуска", "end_date")
        add("Дата заявления", "application_date")
    else:
        add("ФИО студента", "student_name")
        add("Организация", "organization")
        add("Сроки практики", "period", ("с " + u["period"].replace(" – ", " по ")) if u.get("period") and rng.random() < 0.5 else None)
        add("Руководитель практики", "supervisor")
        add("Оценка", "grade")
    if spec.doc_type != "academic_leave":
        num, date = u.get("issue.number"), u.get("issue.date")
        if rng.random() < 0.55:
            parts = []
            if num:
                parts.append(f"№ {num}")
            if date:
                parts.append(f"от {date}" if num else date)
            if parts:
                rows.append(("Номер и дата выдачи", " ".join(parts)))
            elif rng.random() < 0.5:
                rows.append(("Номер и дата выдачи", "_" * 14))
        else:
            if num:
                rows.append(("Номер справки", num))
            if date:
                rows.append(("Дата выдачи", date))
    return rows


def _prose_for(spec: DocSpec, rng: random.Random) -> tuple[str, list[str]]:
    """Заголовок и абзацы для «текстовой» вёрстки."""
    u = spec.units
    parts_issue = []
    if u.get("issue.number"):
        parts_issue.append(f"№ {u['issue.number']}")
    if u.get("issue.date"):
        parts_issue.append(("от " if u.get("issue.number") else "") + u["issue.date"])
    issue = " ".join(parts_issue)
    paras: list[str] = []
    if spec.doc_type == "study_certificate":
        title = "СПРАВКА" + (f" {issue}" if issue else "")
        name = u.get("student_name") or "______________________"
        univ = u.get("university") or "______________________"
        s = f"Настоящая справка подтверждает, что студент {name} действительно обучается в {univ}"
        if u.get("direction"):
            s += f" по направлению подготовки {u['direction']}"
        if u.get("study_form"):
            form = {"очная": "очной", "заочная": "заочной", "очно-заочная": "очно-заочной"}[u["study_form"]]
            s += f" на {form} форме обучения"
        s += "."
        paras.append(s)
        if u.get("study_period"):
            paras.append("Период обучения: с " + u["study_period"].replace(" – ", " по ") + ".")
        paras.append("Справка выдана для предоставления по месту требования.")
    elif spec.doc_type == "practice_certificate":
        title = "СПРАВКА О ПРАКТИКЕ" + (f" {issue}" if issue else "")
        name = u.get("student_name") or "______________________"
        s = f"Настоящим подтверждается, что студент {name} проходил практику"
        if u.get("organization"):
            s += f" в {u['organization']}"
        if u.get("period"):
            s += " в период " + "с " + u["period"].replace(" – ", " по ")
        s += "."
        paras.append(s)
        if u.get("supervisor"):
            paras.append(f"Руководитель практики: {u['supervisor']}.")
        if u.get("grade"):
            paras.append(f"Оценка за практику: {u['grade']}.")
    else:
        title = "ЗАЯВЛЕНИЕ"
        s = "Прошу предоставить мне академический отпуск"
        if u.get("start_date") and u.get("end_date"):
            s += f" с {u['start_date']} по {u['end_date']}"
        elif u.get("start_date"):
            s += f" с {u['start_date']}"
        elif u.get("end_date"):
            s += f" по {u['end_date']}"
        if u.get("reason"):
            s += f" {u['reason']}" if u["reason"].startswith("в связи") or u["reason"].startswith("по ") else f" {u['reason']}"
        s += "."
        paras.append(s)
    return title, paras


def render(spec: DocSpec, rng: random.Random, fonts: tuple[Path, Path]) -> Image.Image:
    size = rng.choice([26, 27, 28, 30])
    c = Canvas(rng, fonts, size)
    u = spec.units
    org_name = u.get("university") or u.get("organization") or rng.choice(UNIVERSITIES)
    y = 120.0
    # шапка
    if spec.doc_type == "study_certificate" and spec.layout != "form":
        for line in c.wrap(u.get("university") or "", PAGE_W - 2 * MARGIN, bold=True, size=size + 4) if u.get("university") else []:
            c.center(y, line, bold=True, size=size + 4)
            y += (size + 4) * 1.4
        y += 14
    elif spec.doc_type == "practice_certificate" and spec.layout != "form":
        c.center(y, "Министерство науки и высшего образования", size=size - 4)
        y += size * 1.5
        c.center(y, "Учебный отдел · практика", bold=True, size=size)
        y += size * 1.8
    elif spec.doc_type == "academic_leave" and spec.layout == "prose":
        x = PAGE_W * 0.48
        y2 = y
        for line in ["Декану факультета информационных технологий", f"{spec.meta['head'].surname} {spec.meta['head'].name[0]}.{spec.meta['head'].patronymic[0]}."]:
            for wl in c.wrap(line, PAGE_W - MARGIN - x):
                c.text(x, y2, wl)
                y2 += c.lh
        g, co = u.get("group_course.group"), u.get("group_course.course")
        who = "от студента"
        if co:
            who += f" {co} курса"
        if g:
            who += f" группы {g}"
        for wl in c.wrap(who, PAGE_W - MARGIN - x):
            c.text(x, y2, wl)
            y2 += c.lh
        if u.get("student_name"):
            for wl in c.wrap(u["student_name"], PAGE_W - MARGIN - x, bold=True):
                c.text(x, y2, wl, bold=True)
                y2 += c.lh
        y = y2 + 40
    title_text = {"study_certificate": "СПРАВКА ОБ ОБУЧЕНИИ", "academic_leave": "ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУСК", "practice_certificate": "СПРАВКА О ПРАКТИКЕ"}[spec.doc_type]

    stamp_pos = None
    if spec.layout in ("form", "table"):
        if spec.doc_type == "study_certificate" and spec.layout == "form":
            for line in c.wrap(org_name, PAGE_W - 2 * MARGIN, bold=True, size=size + 2):
                c.center(y, line.upper() if rng.random() < 0.4 else line, bold=True, size=size + 2)
                y += (size + 2) * 1.4
            y += 10
        c.center(y, title_text, bold=True, size=size + 8)
        y += (size + 8) * 2.0
        rows = _rows_for(spec, rng)
        if spec.layout == "form":
            for label, value in rows:
                c.text(MARGIN, y, f"{label}:", bold=rng.random() < 0.25)
                lx = MARGIN + c.width(f"{label}:") + 18
                lines = c.wrap(value, PAGE_W - MARGIN - lx)
                for i, line in enumerate(lines):
                    c.text(lx, y, line, bold=value.strip("_") != "" and rng.random() < 0.1)
                    y += c.lh
                if not lines:
                    y += c.lh
                y += 8
        else:
            col = int(PAGE_W * 0.4)
            x0, x1 = MARGIN - 20, PAGE_W - MARGIN + 20
            top = y - 8
            for label, value in rows:
                vlines = c.wrap(value, x1 - x0 - col - 30)
                h = max(1, len(vlines)) * c.lh + 18
                c.draw.rectangle([x0, y - 9, x1, y - 9 + h], outline=(40, 40, 40), width=2)
                c.draw.line([(x0 + col, y - 9), (x0 + col, y - 9 + h)], fill=(40, 40, 40), width=2)
                c.text(x0 + 16, y, label, bold=True)
                yy = y
                for line in vlines:
                    c.text(x0 + col + 16, yy, line)
                    yy += c.lh
                y += h
            y += 10
    else:
        title, paras = _prose_for(spec, rng)
        if spec.doc_type == "academic_leave":
            c.center(y, title, bold=True, size=size + 8)
            y += (size + 8) * 2.0
        else:
            c.center(y, title, bold=True, size=size + 6)
            y += (size + 6) * 2.0
        for p in paras:
            y = c.para(MARGIN, y, p, PAGE_W - 2 * MARGIN, indent=60)
            y += 12

    # блок подписи
    y += rng.randint(70, 150)
    if spec.doc_type == "academic_leave":
        date_txt = u.get("application_date")
        if date_txt and spec.layout == "prose":
            d = dt.datetime.strptime(date_txt, "%d.%m.%Y").date()
            c.text(MARGIN, y, fmt_date_long(d))
        elif spec.layout == "prose":
            c.text(MARGIN, y, "«___» __________ 20__ г.")
        sx = int(PAGE_W * 0.58)
        c.draw.line([(sx, y + c.size + 4), (sx + 250, y + c.size + 4)], fill=c.ink, width=2)
        c.text(sx + 262, y, f"/ {spec.meta['person_obj'].short_rev} /", size=size - 2)
        c.text(sx + 70, y + c.size + 10, "(подпись)", size=max(16, size - 10))
        if u.get("student_signature.signature"):
            draw_signature(c.img, sx + 10, int(y - 22), 220, 52, rng)
    else:
        head = "Декан факультета" if spec.doc_type == "study_certificate" else "Руководитель практики"
        c.text(MARGIN, y, head)
        sx = int(MARGIN + c.width(head) + 50)
        c.draw.line([(sx, y + c.size + 4), (sx + 230, y + c.size + 4)], fill=c.ink, width=2)
        c.text(sx + 80, y + c.size + 10, "(подпись)", size=max(16, size - 10))
        who = spec.meta["head"].short_rev if spec.doc_type == "study_certificate" else (u.get("supervisor") or spec.meta["head"].fio).split()
        if isinstance(who, list):
            who = f"{who[1][0]}.{who[2][0]}. {who[0]}" if len(who) >= 3 else " ".join(who)
        c.text(sx + 245, y, f"/ {who} /", size=size - 2)
        c.text(MARGIN, y + 90, "М.П.")
        if u.get("seal_signature.signature"):
            draw_signature(c.img, sx + 8, int(y - 24), 205, 54, rng)
        if u.get("seal_signature.stamp"):
            kind = "round" if rng.random() < 0.8 else "rect"
            draw_stamp(c.img, int(MARGIN + rng.randint(150, 330)), int(y + rng.randint(15, 70)), rng.randint(92, 122), rng, org_name, kind)
    # лёгкий «скан»: нет изменений для чистой версии
    return c.img


# ----------------------------------------------------------------------------- порча под фото


def degrade(img: Image.Image, level: int, rng: random.Random) -> Image.Image:
    """level 0 — чистый скан; 1 — лёгкое фото; 2 — тяжёлое (наклон до 30°, тень, блик, низкое разрешение)."""
    if level <= 0:
        return img
    nr = np.random.default_rng(rng.randint(0, 10**9))
    arr = np.array(img.convert("RGB"))
    h, w = arr.shape[:2]
    pad = int(0.09 * w)
    bg = np.array(rng.choice([(96, 72, 52), (120, 120, 118), (60, 70, 80), (150, 130, 100)]), dtype=np.uint8)
    canvas = np.empty((h + 2 * pad, w + 2 * pad, 3), np.uint8)
    canvas[:] = bg
    canvas = np.clip(canvas.astype(np.int16) + nr.normal(0, 8, canvas.shape[:2])[..., None], 0, 255).astype(np.uint8)
    canvas[pad:pad + h, pad:pad + w] = arr
    H, W = canvas.shape[:2]
    angle = rng.uniform(-30, 30) if level >= 2 else rng.uniform(-7, 7)
    M = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(H * sin + W * cos), int(H * cos + W * sin)
    M[0, 2] += nw / 2 - W / 2
    M[1, 2] += nh / 2 - H / 2
    out = cv2.warpAffine(canvas, M, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=tuple(int(x) for x in bg))
    # лёгкая перспектива
    j = 0.035 if level >= 2 else 0.012
    src = np.float32([[0, 0], [nw, 0], [nw, nh], [0, nh]])
    dst = src + np.float32([[nr.uniform(-j, j) * nw, nr.uniform(-j, j) * nh] for _ in range(4)])
    out = cv2.warpPerspective(out, cv2.getPerspectiveTransform(src, dst), (nw, nh), borderMode=cv2.BORDER_REPLICATE)
    f = out.astype(np.float32)
    # тень: линейный градиент + пятно
    yy, xx = np.mgrid[0:nh, 0:nw].astype(np.float32)
    ang = rng.uniform(0, 2 * math.pi)
    grad = (xx * math.cos(ang) + yy * math.sin(ang))
    grad = (grad - grad.min()) / (grad.max() - grad.min() + 1e-6)
    strength = rng.uniform(0.25, 0.5) if level >= 2 else rng.uniform(0.1, 0.25)
    f *= (1 - strength * grad)[..., None]
    if level >= 2:
        cx, cy = rng.uniform(0.2, 0.8) * nw, rng.uniform(0.2, 0.8) * nh
        rad = rng.uniform(0.15, 0.3) * nw
        blob = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * rad ** 2)))
        f *= (1 - rng.uniform(0.25, 0.45) * blob)[..., None]
        # блик
        gx, gy = rng.uniform(0.25, 0.75) * nw, rng.uniform(0.2, 0.7) * nh
        r1, r2 = rng.uniform(0.06, 0.12) * nw, rng.uniform(0.04, 0.09) * nw
        glare = np.exp(-(((xx - gx) / r1) ** 2 + ((yy - gy) / r2) ** 2))
        f += (glare * rng.uniform(110, 190))[..., None]
    f = np.clip(f, 0, 255).astype(np.uint8)
    sigma = rng.uniform(0.6, 1.5) if level >= 2 else rng.uniform(0.3, 0.8)
    f = cv2.GaussianBlur(f, (0, 0), sigma)
    noise = nr.normal(0, 6 if level >= 2 else 3, f.shape).astype(np.int16)
    f = np.clip(f.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    target_w = rng.randint(820, 1000) if level >= 2 else rng.randint(1300, 1700)
    scale = target_w / nw
    f = cv2.resize(f, (target_w, int(nh * scale)), interpolation=cv2.INTER_AREA)
    return Image.fromarray(f)


# ----------------------------------------------------------------------------- вопросы

# какой пункт Положения (из data/regulations/polozhenie.md) ожидаем в качестве источника
EXPECTED_CLAUSE = {
    "study_certificate": {"issue.number": "3.4", "issue.date": "3.4", "seal_signature.signature": "3.4", "seal_signature.stamp": "3.4", "_default": "3.2"},
    "academic_leave": {"student_signature.signature": "4.3", "_default": "4.2"},
    "practice_certificate": {"seal_signature.signature": "5.3", "seal_signature.stamp": "5.3", "_default": "5.2"},
}
OK_CLAUSE = {"study_certificate": "3.2", "academic_leave": "4.2", "practice_certificate": "5.2"}

COMPLETENESS_Q = {
    "study_certificate": ["Хватит ли этой справки для перевода на 2 курс?", "Подойдёт ли справка для перевода из другого вуза?",
                          "Достаточно ли этой справки для перевода?", "Принимается ли такая справка для перевода на второй курс?",
                          "Всё ли в порядке со справкой об обучении?", "Можно ли по этой справке перевестись на второй курс?",
                          "Проверьте, пожалуйста, мою справку для перевода", "Годится ли эта справка для перевода?", "Что не так со справкой?",
                          "Справка оформлена правильно?", "Мне хватит этой справки, чтобы перевестись на 2 курс?", "Нет ли ошибок в справке?"],
    "academic_leave": ["Достаточно ли этого заявления для оформления академического отпуска?", "Всё ли верно оформлено в заявлении?",
                       "Примет ли деканат моё заявление на академ?", "Чего не хватает в заявлении?", "Заявление на академ оформлено правильно?",
                       "Можно ли с таким заявлением уйти в академ?", "Проверьте, пожалуйста, моё заявление", "Подойдёт ли это заявление?",
                       "Нет ли ошибок в заявлении?", "Заявление принимается к рассмотрению?"],
    "practice_certificate": ["Подойдёт ли эта справка о практике?", "Всё ли указано в справке о практике?",
                             "Достаточно ли этой справки о практике?", "Чего не хватает в справке?", "Засчитают ли практику по этой справке?",
                             "Справка о практике оформлена правильно?", "Проверьте, пожалуйста, справку о практике", "Принимается ли такая справка о практике?",
                             "Нет ли ошибок в справке?"],
}
FACT_Q = {
    "study_certificate": [("study_form", ["Какая форма обучения указана в справке?", "Какая у студента форма обучения?"]),
                          ("university", ["В каком вузе учится студент?", "Какой вуз указан в справке?"]),
                          ("direction", ["На каком направлении подготовки обучается студент?", "Какое направление подготовки указано в справке?"]),
                          ("study_period", ["Какой период обучения указан в справке?", "Какой период обучения у студента?"]),
                          ("student_name", ["Как зовут студента в справке?", "Какое ФИО студента указано в справке?"])],
    "academic_leave": [("reason", ["Какая причина отпуска указана в заявлении?", "Какая причина отпуска?"]),
                       ("start_date", ["С какой даты начинается отпуск?", "Какая дата начала отпуска указана?"]),
                       ("end_date", ["Какая дата окончания отпуска указана?", "Когда заканчивается отпуск?"]),
                       ("group_course.course", ["На каком курсе учится студент?", "Какой курс указан в заявлении?"])],
    "practice_certificate": [("supervisor", ["Кто руководитель практики?", "Кто указан руководителем практики?"]),
                             ("grade", ["Какая оценка указана за практику?", "Какая оценка за практику?"]),
                             ("organization", ["В какой организации студент проходил практику?", "Какая организация указана в справке?"]),
                             ("period", ["В какие сроки проходила практика?", "Какие сроки практики указаны?"])],
}
RULE_Q = [
    ("В течение какого времени деканат рассматривает заявления студентов?", "6.1"), ("Сколько дней рассматривается заявление?", "6.1"),
    ("На какой срок предоставляется академический отпуск?", "4.1"), ("Какой максимальный срок академического отпуска?", "4.1"),
    ("Сколько дней действительна справка об обучении?", "3.3"), ("Как долго действует справка об обучении?", "3.3"),
    ("Какие основания есть для академического отпуска?", "4.4"), ("По каким причинам предоставляют академический отпуск?", "4.4"),
    ("За сколько рабочих дней выдают справку об обучении?", "3.5"), ("За какой срок выдаётся справка об обучении?", "3.5"),
    ("Как студента уведомят о результатах рассмотрения заявления?", "6.2"),
    ("Нужна ли медицинская справка при академическом отпуске по здоровью?", "4.4"),
    ("В какой срок принимается решение о переводе?", "7.3"), ("Сколько дней занимает решение о переводе?", "7.3"),
    ("По какой системе выставляется оценка за практику?", "5.4"),
    ("Принимаются ли документы на иностранном языке?", "2.3"), ("Нужен ли перевод документов на русский язык?", "2.3"),
    ("Принимает ли деканат фотографии и сканы документов?", "1.2"),
    ("Кто принимает окончательное решение о принятии документа?", "1.3"),
]
NO_ANSWER_Q = [
    "Сколько стоит обучение в вузе?", "Когда начинается следующая сессия?", "Предоставляется ли студентам общежитие?",
    "Какой размер стипендии у студентов?", "Как оформить пропуск в библиотеку?", "Где находится деканат и когда он работает?",
    "Можно ли перевестись с платного обучения на бюджет?", "Какой телефон у приёмной комиссии?", "Как получить военный билет?",
    "Есть ли в вузе столовая?", "Сколько стоит справка об обучении?", "Какая стоимость обучения на заочной форме?", "Когда будет защита диплома?",
    "Как записаться на пересдачу экзамена?", "Дают ли скидку на оплату обучения?", "Какой проходной балл на направление?",
    "Можно ли получить повторно диплом?", "Где взять расписание занятий?", "Выдают ли справку-вызов на сессию?", "Как восстановиться после отчисления?",
]


def make_questions(spec: DocSpec, rng: random.Random, doc_id: str) -> list[dict]:
    qs = []
    absent = [u for u in ALL_UNITS[spec.doc_type] if not spec.present[u]]
    if absent:
        clauses = []
        for u in absent:
            cl = EXPECTED_CLAUSE[spec.doc_type].get(u, EXPECTED_CLAUSE[spec.doc_type]["_default"])
            if cl not in clauses:
                clauses.append(cl)
        qs.append({"kind": "completeness", "question": rng.choice(COMPLETENESS_Q[spec.doc_type]), "expected": {"verdict": "missing", "missing": absent, "sources": clauses}})
    else:
        qs.append({"kind": "completeness", "question": rng.choice(COMPLETENESS_Q[spec.doc_type]), "expected": {"verdict": "ok", "sources": [OK_CLAUSE[spec.doc_type]]}})
    unit, variants = rng.choice(FACT_Q[spec.doc_type])
    if spec.present[unit] and spec.units.get(unit):
        qs.append({"kind": "fact", "question": rng.choice(variants), "expected": {"verdict": "ok", "unit": unit, "value": spec.units[unit]}})
    r = rng.random()
    if r < 0.4:
        q, cl = rng.choice(RULE_Q)
        qs.append({"kind": "rule", "question": q, "expected": {"verdict": "ok", "sources": [cl]}})
    elif r < 0.8:
        qs.append({"kind": "no_answer", "question": rng.choice(NO_ANSWER_Q), "expected": {"verdict": "no_answer"}})
    for i, item in enumerate(qs):
        item["id"] = f"{doc_id}-q{i + 1}"
    return qs


# ----------------------------------------------------------------------------- сборка набора


def generate(out: Path, n_per_type: int = 4, levels: tuple[int, ...] = (0, 1, 2), seed: int = 7, pdf_share: float = 0.1) -> dict:
    rng = random.Random(seed)
    fonts = find_font_families()
    out = Path(out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    manifest = []
    questions = []
    counter = 0
    for doc_type in ALL_UNITS:
        for i in range(n_per_type):
            for level in levels:
                counter += 1
                layout = ["form", "table", "prose"][(i + level) % 3] if len(levels) > 1 else ["form", "table", "prose"][i % 3]
                doc_id = f"{doc_type}_{counter:03d}"
                spec = make_spec(rng, doc_type, layout)
                page = render(spec, rng, rng.choice(fonts))
                img = degrade(page, level, rng)
                as_pdf = level == 0 and rng.random() < pdf_share
                ext = "pdf" if as_pdf else ("png" if level == 0 else "jpg")
                path = out / "images" / f"{doc_id}.{ext}"
                if as_pdf:
                    img.save(path, "PDF", resolution=150)
                elif ext == "png":
                    img.save(path)
                else:
                    img.save(path, "JPEG", quality=rng.randint(45, 72) if level >= 2 else rng.randint(78, 92))
                qs = make_questions(spec, rng, doc_id)
                label = {
                    "id": doc_id, "image": f"images/{path.name}", "doc_type": doc_type, "layout": layout, "level": level,
                    "units": spec.units, "present": spec.present, "absent": spec.meta["absent"], "questions": qs,
                }
                (out / "labels" / f"{doc_id}.json").write_text(json.dumps(label, ensure_ascii=False, indent=2), encoding="utf-8")
                manifest.append({k: label[k] for k in ("id", "image", "doc_type", "layout", "level")})
                for q in qs:
                    questions.append({"id": q["id"], "doc_id": doc_id, "image": label["image"], "doc_type": doc_type, "question": q["question"], "kind": q["kind"], "expected": q["expected"]})
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "questions.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"documents": len(manifest), "questions": len(questions), "out": str(out)}


# показательные документы для веб-интерфейса: (тип, вёрстка, уровень порчи, чего нет, подпись, вопрос)
SAMPLE_CASES = [
    ("study_certificate", "form", 0, set(), "Справка об обучении: всё в порядке", "Хватит ли этой справки для перевода на 2 курс?"),
    ("study_certificate", "table", 0, {"issue.date", "seal_signature.stamp"}, "Справка об обучении: нет даты выдачи и печати", "Хватит ли этой справки для перевода на 2 курс?"),
    ("study_certificate", "prose", 1, set(), "Справка об обучении: фото под наклоном", "Подойдёт ли справка для перевода из другого вуза?"),
    ("academic_leave", "form", 0, set(), "Заявление на академ: всё в порядке", "Достаточно ли этого заявления для оформления академического отпуска?"),
    ("academic_leave", "prose", 1, {"student_signature.signature"}, "Заявление на академ: нет подписи (фото)", "Примет ли деканат моё заявление на академ?"),
    ("practice_certificate", "table", 0, set(), "Справка о практике: всё в порядке", "Подойдёт ли эта справка о практике?"),
    ("practice_certificate", "form", 1, {"seal_signature.signature"}, "Справка о практике: нет подписи", "Достаточно ли этой справки о практике?"),
    ("study_certificate", "form", 0, set(), "Вопрос, ответа на который нет", "Сколько стоит обучение?"),
]


def render_order(rng: random.Random, fonts: tuple[Path, Path]) -> Image.Image:
    """Вымышленный приказ органа власти — документ НЕ из вуза (проверка режима «другой документ»):
    шапка, заголовок, город, дата и номер, тема, нумерованные пункты 1., 1.1., 1.2."""
    reg, bold = fonts
    img = Image.new("RGB", (PAGE_W, int(PAGE_H * 1.12)), (253, 253, 251))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(str(reg), 29)
    fb = ImageFont.truetype(str(bold), 30)
    fs = ImageFont.truetype(str(reg), 24)
    y = 110
    # герб-заглушка
    d.ellipse([PAGE_W // 2 - 38, y, PAGE_W // 2 + 38, y + 76], outline=(120, 120, 120), width=3)
    d.text((PAGE_W // 2, y + 38), "★", font=ImageFont.truetype(str(bold), 40), fill=(120, 120, 120), anchor="mm")
    y += 110
    for line, font in (("АДМИНИСТРАЦИЯ ГОРОДСКОГО ОКРУГА ТЕСТОВСК", fs), ("УПРАВЛЕНИЕ ПО ЧРЕЗВЫЧАЙНЫМ СИТУАЦИЯМ", fs)):
        d.text((PAGE_W / 2, y), line, font=font, fill=(40, 70, 130), anchor="ma")
        y += 34
    d.text((PAGE_W / 2, y + 8), "НАЧАЛЬНИК УПРАВЛЕНИЯ", font=fb, fill=(20, 20, 20), anchor="ma")
    y += 80
    title = "П Р И К А З"
    d.text((PAGE_W / 2, y), title, font=ImageFont.truetype(str(bold), 40), fill=(10, 10, 10), anchor="ma")
    y += 62
    d.text((PAGE_W / 2, y), "г. Тестовск", font=fs, fill=(30, 30, 30), anchor="ma")
    y += 52
    hand = None
    for cand in ("C:/Windows/Fonts/Inkfree.ttf", "C:/Windows/Fonts/segoesc.ttf"):
        if Path(cand).exists():
            hand = ImageFont.truetype(cand, 36)
            break
    d.text((MARGIN, y), "14.02.2025", font=hand or f, fill=(40, 50, 140))
    d.text((PAGE_W - MARGIN - 160, y), "№", font=f, fill=(20, 20, 20))
    d.line([(PAGE_W - MARGIN - 130, y + 40), (PAGE_W - MARGIN, y + 40)], fill=(20, 20, 20), width=2)
    d.text((PAGE_W - MARGIN - 80, y - 2), "2", font=hand or f, fill=(40, 50, 140))
    y += 130
    subject = "О мерах по подготовке к весеннему паводку 2025 года на территории городского округа"
    for line in Canvas(rng, fonts, 29).wrap(subject, 520):
        d.text((MARGIN, y), line, font=f, fill=(20, 20, 20))
        y += 42
    y += 40
    para = ("В соответствии с Федеральным законом от 21.12.1994 № 68-ФЗ «О защите населения и территорий от чрезвычайных ситуаций» "
            "и в целях предупреждения угрозы подтопления жилых домов в период весеннего паводка 2025 года приказываю:")
    c = Canvas(rng, fonts, 29)
    first = True
    for line in c.wrap(para, PAGE_W - 2 * MARGIN - (50 if True else 0)):
        d.text((MARGIN + (50 if first else 0), y), line, font=f, fill=(20, 20, 20))
        first = False
        y += 42
    y += 14
    items = [
        ("1.", "Рекомендовать главам районов городского округа:"),
        ("1.1.", "обеспечить очистку водоотводных канав и ливневой канализации до 01.03.2025, уделив особое внимание улицам, подверженным подтоплению;"),
        ("1.2.", "организовать дежурство аварийных бригад в период паводка, обеспечить запас мешков с песком и средств откачки воды не менее 500 штук на район;"),
        ("1.3.", "довести до жителей информацию о порядке действий при угрозе подтопления через официальный сайт и социальные сети администрации;"),
        ("2.", "Начальнику отдела связи обеспечить бесперебойную работу системы оповещения населения до 30.04.2025."),
        ("3.", "Контроль за выполнением настоящего приказа оставляю за собой."),
    ]
    for num, text in items:
        lines = c.wrap(text, PAGE_W - 2 * MARGIN - 120)
        d.text((MARGIN, y), num, font=f, fill=(20, 20, 20))
        for line in lines:
            d.text((MARGIN + 120, y), line, font=f, fill=(20, 20, 20))
            y += 42
    y += 40
    d.text((MARGIN, y), "Начальник управления", font=f, fill=(20, 20, 20))
    d.text((PAGE_W - MARGIN, y), "И. И. Тестов", font=f, fill=(20, 20, 20), anchor="ra")
    return img


def generate_samples(out: Path, seed: int = 2024) -> int:
    """Небольшой набор показательных документов (data/samples) для демонстрации в веб-интерфейсе."""
    rng = random.Random(seed)
    fonts = find_font_families()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, (doc_type, layout, level, absent, title, question) in enumerate(SAMPLE_CASES, 1):
        spec = make_spec(rng, doc_type, layout, set(absent))
        page = render(spec, rng, fonts[i % len(fonts)])
        img = degrade(page, level, rng)
        name = f"sample_{i:02d}.{'png' if level == 0 else 'jpg'}"
        if level == 0:
            img.save(out / name)
        else:
            img.save(out / name, "JPEG", quality=82)
        manifest.append({"file": name, "title": title, "question": question, "doc_type": doc_type})
    # документ не из вуза: показывает режим «другой документ» (ответ по тексту самого документа)
    img = degrade(render_order(rng, fonts[0]), 1, rng)
    img.save(out / "sample_09.jpg", "JPEG", quality=84)
    manifest.append({"file": "sample_09.jpg", "title": "Приказ (не из вуза): вопрос по тексту документа", "question": "Что приказано в п. 1.2?", "doc_type": "generic"})
    manifest.append(bad_photo_sample(out))
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(manifest)


def bad_photo_sample(out: Path) -> dict:
    """Справка о практике (sample_06), снятая плохо: мелко, не в фокусе и в полутьме. Показывает, как сервис советует переснять."""
    from PIL import ImageEnhance, ImageFilter

    img = Image.open(out / "sample_06.png").convert("RGB")
    img = img.resize((1600, round(1600 * img.height / img.width)), Image.BICUBIC).filter(ImageFilter.GaussianBlur(3.4))
    ImageEnhance.Brightness(img).enhance(0.5).save(out / "sample_10.jpg", "JPEG", quality=60)
    return {"file": "sample_10.jpg", "title": "Справка о практике: снимок размытый и тёмный", "question": "Подойдёт ли эта справка о практике?", "doc_type": "practice_certificate"}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Генератор синтетических студенческих документов")
    ap.add_argument("--out", default="data/synthetic")
    ap.add_argument("--n", type=int, default=4, help="документов каждого типа на каждый уровень порчи")
    ap.add_argument("--levels", default="0,1,2", help="0 — чистый скан, 1 — лёгкое фото, 2 — тяжёлое фото")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--samples", action="store_true", help="сгенерировать показательные документы для интерфейса (папка --out)")
    args = ap.parse_args(argv)
    if args.samples:
        n = generate_samples(Path(args.out), args.seed if args.seed != 7 else 2024)
        print(f"Готово: {n} примеров -> {args.out}")
        return 0
    info = generate(Path(args.out), args.n, tuple(int(x) for x in args.levels.split(",")), args.seed)
    print(f"Готово: {info['documents']} документов, {info['questions']} вопросов -> {info['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
