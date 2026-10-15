"""Этап «проверка формата»: до OCR файл проверяется по содержимому, а не по расширению.

Поддерживаются ровно JPG, JPEG, PNG и PDF. Пустые, слишком большие и неподдерживаемые файлы отклоняются понятной ошибкой;
файл никогда не исполняется — он только читается как изображение или PDF.
"""

from __future__ import annotations

from typing import Optional

from ..ocr.preprocess import UnsupportedFile

SUPPORTED_FORMATS = ("JPG", "JPEG", "PNG", "PDF")
UNSUPPORTED_TEXT = "Не удалось прочитать файл: поддерживаются только JPG, JPEG, PNG и PDF."


def detect_format(data: bytes) -> Optional[str]:
    """jpeg | png | pdf по сигнатуре содержимого; None — формат не поддерживается."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if b"%PDF-" in data[:1024]:  # по стандарту заголовок PDF может стоять не в самом начале файла
        return "pdf"
    return None


def check_format(data: Optional[bytes], filename: str = "", max_bytes: int = 0) -> str:
    """Возвращает формат файла или бросает UnsupportedFile (пустой, слишком большой, неподдерживаемый)."""
    if not data:
        raise UnsupportedFile("Файл пустой")
    if max_bytes and len(data) > max_bytes:
        raise UnsupportedFile(f"Файл больше {max_bytes // (1024 * 1024)} МБ")
    fmt = detect_format(data)
    if fmt is None:
        raise UnsupportedFile(UNSUPPORTED_TEXT)
    return fmt
