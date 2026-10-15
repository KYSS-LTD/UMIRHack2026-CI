"""Общий контекст маршрутов сайта: сервисы приложения и несколько функций, нужных нескольким темам."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from ..config import Settings

OCR_DOWN_TEXT = "Распознавание текста сейчас недоступно. Попробуйте позже; вопросы по Положению работают и без документа."


@dataclass
class WebContext:
    settings: Settings
    svc: Any
    storage: Any
    auth: Any
    kbm: Any
    type_mgr: Any
    sessions: Any
    tg: Any
    session_locks: Any
    ocr_slots: threading.Semaphore
    max_bytes: int
    allow_edit: bool
    delivery_status: Callable[[], dict]
    log_event: Callable[..., None]
