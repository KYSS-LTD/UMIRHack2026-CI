"""Типы документов: проверка, добавление, изменение и удаление описаний."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...doctypes import DocTypeError, NEW_TYPE_TEMPLATE

class DocTypeIn(BaseModel):
    yaml: str = Field(max_length=400_000)


def register(router: APIRouter, ctx) -> None:
    svc, doctypes, admin = ctx.svc, ctx.doctypes, ctx.admin
    def _type_call(fn, *args, write: bool = False):
        if write and not svc.settings.get("server.allow_edit", True):
            raise HTTPException(403, "Редактирование отключено (server.allow_edit: false)")
        try:
            return fn(*args)
        except DocTypeError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.get("/doctypes")
    def list_doctypes(who: dict = admin):
        return doctypes.list()

    @router.get("/doctypes/template")
    def doctype_template(who: dict = admin):
        return {"yaml": NEW_TYPE_TEMPLATE}

    @router.post("/doctypes/validate")
    def validate_doctype(body: DocTypeIn, who: dict = admin):
        return doctypes.validate(body.yaml)

    @router.get("/doctypes/{type_id}/yaml")
    def doctype_yaml(type_id: str, who: dict = admin):
        return {"id": type_id, "yaml": _type_call(doctypes.yaml, type_id)}

    @router.post("/doctypes")
    def add_doctype(body: DocTypeIn, who: dict = admin):
        return _type_call(doctypes.save, "new", body.yaml, who["username"], write=True)

    @router.put("/doctypes/{type_id}")
    def replace_doctype(type_id: str, body: DocTypeIn, who: dict = admin):
        return _type_call(doctypes.save, type_id, body.yaml, who["username"], write=True)

    @router.delete("/doctypes/{type_id}")
    def delete_doctype(type_id: str, who: dict = admin):
        return _type_call(doctypes.delete, type_id, who["username"], write=True)
