"""Нормализация, токенизация и нечёткое сравнение русского текста (устойчивое к ошибкам OCR)."""

from __future__ import annotations

import re
from functools import lru_cache

import snowballstemmer
from rapidfuzz import fuzz

_stemmer = snowballstemmer.stemmer("russian")

# Латинские буквы, которые OCR путает с кириллическими (и наоборот).
_LAT2CYR = str.maketrans(
    {
        "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
        "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
    }
)

STOPWORDS = frozenset(
    """и в во на о об обо по от до с со к ко из за для при про над под без у а но или же ли бы
    то это этот эта эти тот та те как что чтобы который которая которые которое
    не ни да нет есть быть был была были будет может могут можно нужно надо
    я мы вы он она оно они мой наш ваш свой его ее их
    какой какая какие какое каков какого какому каким какими каких каком какую каково кто где когда куда откуда почему зачем сколько скольким скольки скольких сколькими
    которого которому которой которых которым которую
    ещё еще уже также только лишь именно очень через около примерно приблизительно вообще пока""".split()
)

_WORD_RE = re.compile(r"[a-zа-я0-9]+")
_CYR_RE = re.compile(r"[а-я]")
_LAT_RE = re.compile(r"[a-z]")


def normalize(text: str) -> str:
    """Нижний регистр, ё->е, схлопывание пробелов."""
    text = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text).strip()


def fix_homoglyphs(token: str) -> str:
    """Токен из смеси кириллицы и латиницы -> всё кириллицей (типичная ошибка OCR)."""
    if _CYR_RE.search(token) and _LAT_RE.search(token):
        return token.translate(_LAT2CYR)
    return token


@lru_cache(maxsize=50000)
def stem(token: str) -> str:
    if token.isdigit() or len(token) <= 2:
        return token
    return _stemmer.stemWord(token)


def tokenize(text: str, drop_stop: bool = False) -> list[str]:
    """Слова (нормализованные, без пунктуации)."""
    out = []
    for tok in _WORD_RE.findall(normalize(text)):
        tok = fix_homoglyphs(tok)
        if drop_stop and tok in STOPWORDS:
            continue
        out.append(tok)
    return out


def stems(text: str, drop_stop: bool = True) -> list[str]:
    return [stem(t) for t in tokenize(text, drop_stop=drop_stop)]


def stem_eq(a: str, b: str) -> bool:
    """Равенство основ с допуском на опечатки OCR."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if min(la, lb) < 4:
        return False
    short, long_ = (a, b) if la <= lb else (b, a)
    # Snowball даёт разные основы у «подпись» (подп) и «подписи» (подпис): префикс считаем совпадением
    if long_.startswith(short) and len(long_) - len(short) <= 2:
        return True
    if abs(la - lb) > 2:
        return False
    return fuzz.ratio(a, b) >= 84


def clean_value(text: str) -> str:
    """Убирает хвостовую пунктуацию, подчёркивания-заполнители и лишние пробелы."""
    text = re.sub(r"[_…]{2,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" \t:;,-–—|·•*")


def is_blank_value(text: str) -> bool:
    """Пусто или только заполнители вида «______», «...»."""
    return not re.sub(r"[\s_.…\-–—:;|/\\]+", "", text)


def title_case_name(text: str) -> str:
    """ИВАНОВ ИВАН -> Иванов Иван; сохраняет дефисные части."""
    parts = []
    for word in text.split():
        pieces = [p[:1].upper() + p[1:].lower() if p else p for p in word.split("-")]
        parts.append("-".join(pieces))
    return " ".join(parts)


def sentences(text: str) -> list[str]:
    """Грубое деление на предложения (с защитой сокращений «п.», «пп.», «т.д.»)."""
    protected = re.sub(r"\b(п|пп|ст|гл|т\.д|т\.п|т\.е|им|г|гг|руб|тыс)\.", lambda m: m.group(0).replace(".", "§"), text)
    parts = re.split(r"(?<=[.!?;])\s+(?=[А-ЯЁA-Z0-9«\"(])", protected)
    return [p.replace("§", ".").strip() for p in parts if p.strip()]
