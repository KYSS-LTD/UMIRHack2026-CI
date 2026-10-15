"""Ошибка сценария ассистента: текст для человека и код, который сайт отдаёт как HTTP-статус (бот показывает только текст)."""

from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail
