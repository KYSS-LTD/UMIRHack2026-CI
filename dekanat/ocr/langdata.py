"""Языковые данные Tesseract: скачивание в папку проекта без прав администратора.

Установщик Tesseract для Windows по умолчанию ставит только английский, а секретарю нужен русский (`rus`). Команда
`python -m dekanat fetch-lang` кладёт rus, eng и osd в data/tessdata; приложение подхватывает эту папку само (см. Dekanat.__init__).
Файлы берутся из официального репозитория tessdata_fast (лицензия Apache 2.0); больше сервис ничего из сети не скачивает.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Optional

BASE_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata"
MANUAL_PAGE = "https://github.com/tesseract-ocr/tessdata_fast"
MIN_BYTES = 40_000  # настоящий traineddata — мегабайты; меньше — значит, пришла страница ошибки
DEFAULT_LANGS = ("rus", "eng", "osd")


class LangDataError(RuntimeError):
    pass


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=90) as res:  # noqa: S310 — адрес фиксирован выше, схема https
        return res.read()


def fetch_languages(dest: "str | Path", langs: Iterable[str] = DEFAULT_LANGS, force: bool = False, download: Optional[Callable[[str], bytes]] = None) -> dict:
    """Скачивает недостающие файлы <язык>.traineddata в dest. Возвращает {язык: что произошло}. Исключение — если файл получить не удалось."""
    get = download or _download
    folder = Path(dest)
    folder.mkdir(parents=True, exist_ok=True)
    result: dict = {}
    for lang in langs:
        if not re.fullmatch(r"[A-Za-z_]{2,16}", lang):
            raise LangDataError(f"Недопустимое имя языка: {lang!r}")
        target = folder / f"{lang}.traineddata"
        if target.exists() and target.stat().st_size >= MIN_BYTES and not force:
            result[lang] = "уже есть"
            continue
        try:
            data = get(BASE_URL.format(lang=lang))
        except OSError as exc:  # нет сети, блокировка, ошибка сервера
            raise LangDataError(f"Не удалось скачать «{lang}»: {exc}") from exc
        if len(data) < MIN_BYTES or data[:1] == b"<":
            raise LangDataError(f"Вместо файла языка «{lang}» пришёл не тот ответ ({len(data)} байт). Скачайте {lang}.traineddata вручную со страницы {MANUAL_PAGE}.")
        part = target.with_suffix(".part")
        part.write_bytes(data)
        part.replace(target)  # файл появляется целиком или не появляется
        result[lang] = f"скачан ({len(data) // 1024} КБ)"
    return result
