"""Правка содержимого администратором: Положение и описания типов документов (прежние адреса /api/doctypes/…)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from ...doctypes import DocTypeError, NEW_TYPE_TEMPLATE
from ...knowledge_base.documents import BASE_ID, KnowledgeBaseError
from ..context import WebContext


def build(ctx: WebContext) -> APIRouter:
    router = APIRouter()
    svc, storage, auth, kbm, type_mgr, max_bytes, allow_edit = ctx.svc, ctx.storage, ctx.auth, ctx.kbm, ctx.type_mgr, ctx.max_bytes, ctx.allow_edit

    # ------------------------------------------------------------------ Положение
    @router.get("/api/regulation")
    def regulation(request: Request):
        return {"name": svc.regulation_name, "clauses": [c.to_dict() for c in svc.clauses], "editable": allow_edit and auth.can_edit(request)}

    @router.put("/api/regulation")
    async def put_regulation(request: Request, file: UploadFile = File(...)):
        """Замена основного Положения (интерфейс «Положение»). Нужен вход администратора; прежняя версия остаётся в истории."""
        if not allow_edit:
            raise HTTPException(403, "Редактирование отключено (server.allow_edit: false)")
        actor = auth.actor_for_edit(request)
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(413, "Файл слишком большой")
        filename = Path(file.filename or "загруженное Положение.txt").name
        try:
            row = kbm.replace(BASE_ID, filename, data, actor)
        except KnowledgeBaseError as exc:
            raise HTTPException(400, str(exc)) from exc
        storage.kb.rename(BASE_ID, filename)
        svc.reload_knowledge_base()
        return {"name": svc.regulation_name, "clauses": row["clauses"]}

    # ------------------------------------------------------------------ типы документов
    # Прежние адреса (текстовое тело, правка из внешних инструментов); кабинет сотрудника работает через /api/admin/doctypes, логика общая.
    def _guard_edit(request: Request) -> str:
        if not allow_edit:
            raise HTTPException(403, "Редактирование отключено (server.allow_edit: false)")
        return auth.actor_for_edit(request)

    def _typed(fn):
        try:
            return fn()
        except DocTypeError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.get("/api/doctypes/template", response_class=PlainTextResponse)
    def template():
        return NEW_TYPE_TEMPLATE

    @router.get("/api/doctypes/{type_id}/yaml", response_class=PlainTextResponse)
    def type_yaml(type_id: str):
        return _typed(lambda: type_mgr.yaml(type_id))

    @router.post("/api/doctypes/validate")
    def validate(body: str = Body(..., media_type="text/plain")):
        return type_mgr.validate(body)

    @router.put("/api/doctypes/{type_id}")
    def save_type(type_id: str, request: Request, body: str = Body(..., media_type="text/plain")):
        actor = _guard_edit(request)
        return _typed(lambda: type_mgr.save(type_id, body, actor))

    @router.delete("/api/doctypes/{type_id}")
    def delete_type(type_id: str, request: Request):
        actor = _guard_edit(request)
        return _typed(lambda: type_mgr.delete(type_id, actor))

    return router
