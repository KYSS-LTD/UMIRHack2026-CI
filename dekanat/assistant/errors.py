"""Ошибка сценария ассистента: текст для человека и код, который сайт отдаёт как HTTP-статус (бот показывает только текст); общий текст про недоступное распознавание."""

from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# Один текст для сайта и бота: что сказать, когда Tesseract недоступен
OCR_DOWN_TEXT = "Распознавание текста сейчас недоступно. Попробуйте позже; вопросы по Положению работают и без документа."
