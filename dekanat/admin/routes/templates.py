"""Шаблоны DOCX: список, добавление, замена, переименование, скачивание, удаление, история."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...assistant import DOCX_MIME
from ...templates import TemplateError, TemplateExists, TemplateNotFound
from ...templates.docx_templates import MAX_TEMPLATE_BYTES

class NamePatch(BaseModel):
    name: str = Field(max_length=200)


def register(router: APIRouter, ctx) -> None:
    storage, templates, admin = ctx.storage, ctx.templates, ctx.admin
    def _template_dict(row: dict) -> dict:
        return {k: row[k] for k in ("id", "name", "original_name", "version", "placeholders", "created_at", "updated_at", "created_by")}

    def _upload_to_template(file: UploadFile, data: bytes, **kwargs) -> dict:
        if len(data) > MAX_TEMPLATE_BYTES:
            raise HTTPException(413, f"Файл больше {MAX_TEMPLATE_BYTES // (1024 * 1024)} МБ")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows может ещё держать файл после ошибки чтения: это не должно давать 500
            path = Path(tmp) / "upload.docx"  # имя файла от пользователя на диск не попадает
            path.write_bytes(data)
            original = Path(file.filename or "шаблон.docx").name
            if Path(original).suffix.lower() != ".docx":
                raise HTTPException(400, "Шаблон должен быть файлом .docx")
            kwargs["name"] = (kwargs.get("name") or Path(original).stem).strip()
            try:
                return templates.add(path, **kwargs)
            except TemplateExists as exc:
                raise HTTPException(409, str(exc)) from exc
            except TemplateError as exc:
                raise HTTPException(400, str(exc)) from exc

    @router.get("/templates")
    def list_templates(who: dict = admin):
        return [_template_dict(t) for t in storage.templates.list()]

    @router.post("/templates")
    async def add_template(file: UploadFile = File(...), name: str = Form(""), template_id: str = Form(""), replace: bool = Form(False), who: dict = admin):
        data = await file.read(MAX_TEMPLATE_BYTES + 1)
        info = _upload_to_template(file, data, name=name or None, template_id=template_id.strip() or None, overwrite=bool(replace), actor=who["username"])
        storage.audit.log(who["username"], "template.replace" if (replace and info.version > 1) else "template.create", "template", info.template_id,
                          {"name": info.name, "version": info.version, "placeholders": info.keys, "sha256": info.sha256})
        return info.to_dict()

    @router.put("/templates/{template_id}")
    async def replace_template(template_id: str, file: UploadFile = File(...), who: dict = admin):
        if storage.templates.get(template_id) is None:
            raise HTTPException(404, "Шаблон не найден")
        data = await file.read(MAX_TEMPLATE_BYTES + 1)
        current = storage.templates.get(template_id)
        info = _upload_to_template(file, data, template_id=template_id, name=current["name"], overwrite=True, actor=who["username"], note="замена файла")
        storage.audit.log(who["username"], "template.replace", "template", template_id, {"version": info.version, "placeholders": info.keys, "sha256": info.sha256})
        return info.to_dict()

    @router.patch("/templates/{template_id}")
    def rename_template(template_id: str, body: NamePatch, who: dict = admin):
        if storage.templates.get(template_id) is None:
            raise HTTPException(404, "Шаблон не найден")
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Название не может быть пустым")
        storage.templates.rename(template_id, name)
        storage.audit.log(who["username"], "template.rename", "template", template_id, {"name": name})
        return _template_dict(storage.templates.get(template_id))

    @router.get("/templates/{template_id}")
    def get_template(template_id: str, who: dict = admin):
        row = storage.templates.get(template_id)
        if row is None:
            raise HTTPException(404, "Шаблон не найден")
        return {**_template_dict(row), "history": storage.templates.versions(template_id)}

    @router.get("/templates/{template_id}/download")
    def download_template(template_id: str, who: dict = admin):
        try:
            path = templates.path_of(template_id)
        except TemplateNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        row = storage.templates.get(template_id)
        return FileResponse(path, media_type=DOCX_MIME, filename=f"{row['name']}.docx")

    @router.delete("/templates/{template_id}")
    def delete_template(template_id: str, who: dict = admin):
        row = storage.templates.get(template_id)
        if row is None:
            raise HTTPException(404, "Шаблон не найден")
        try:
            templates.delete(template_id)
        except TemplateNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        storage.audit.log(who["username"], "template.delete", "template", template_id, {"name": row["name"], "version": row["version"]})
        return {"ok": True}

    @router.get("/templates/{template_id}/history")
    def template_history(template_id: str, who: dict = admin):
        return {"versions": storage.templates.versions(template_id), "log": storage.audit.list("template", template_id, limit=100)}
