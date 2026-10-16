"""Ядро ассистента для сайта и Telegram-бота: анкета, документ сессии, DOCX, обращение (см. core.py)."""

from .core import (DOCX_MIME, SCENARIO_TOPICS, STATUS_TITLES, WELCOME, AssistantCore, AttemptLimiter, SessionLocks, clean_contact, placeholder_title,
                   remember_document)
from .errors import OCR_DOWN_TEXT, ServiceError

__all__ = [
    "AssistantCore", "AttemptLimiter", "SessionLocks", "ServiceError", "clean_contact", "placeholder_title", "remember_document",
    "DOCX_MIME", "OCR_DOWN_TEXT", "SCENARIO_TOPICS", "STATUS_TITLES", "WELCOME",
]
