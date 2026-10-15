"""REST API администратора (/api/admin/*). Каждый маршрут, кроме входа, требует действующую сессию администратора — проверка на сервере.

Маршруты разложены по темам: доступ (access), шаблоны DOCX, типы документов, база знаний, обращения, статистика.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends

from . import access, knowledge, stats, tickets
from . import doctypes as doctype_routes
from . import templates as template_routes


@dataclass
class Deps:
    """Всё, что нужно маршрутам: сервисы приложения и проверка прав."""

    svc: Any
    storage: Any
    auth: Any
    kbm: Any
    templates: Any
    max_bytes: int
    notifier: Any = None
    telegram: Any = None
    doctypes: Any = None

    @property
    def admin(self):
        return Depends(self.auth.require)


def build_admin_router(svc, storage, auth, kbm, templates, max_bytes: int, notifier=None, telegram=None, doctypes=None) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])
    ctx = Deps(svc, storage, auth, kbm, templates, max_bytes, notifier, telegram, doctypes)
    for module in (access, template_routes, doctype_routes, knowledge, tickets, stats):
        module.register(router, ctx)
    return router
