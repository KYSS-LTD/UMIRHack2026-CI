"""Статистика, журнал изменений и очистка просроченных данных."""

from __future__ import annotations

from fastapi import APIRouter

def register(router: APIRouter, ctx) -> None:
    storage, admin = ctx.storage, ctx.admin
    @router.get("/stats")
    def stats(days: int = 30, who: dict = admin):
        return storage.events.stats(max(1, min(365, days)))

    @router.get("/audit")
    def audit(object_type: str = "", object_id: str = "", limit: int = 100, offset: int = 0, who: dict = admin):
        return storage.audit.list(object_type or None, object_id or None, limit, offset)

    @router.post("/purge")
    def purge(who: dict = admin):
        result = storage.purge()
        storage.audit.log(who["username"], "storage.purge", "storage", "", result)
        return result

    return router
