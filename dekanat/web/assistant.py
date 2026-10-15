"""Ассистент на сайте (/api/assistant/*): HTTP-обёртка над `dekanat.assistant.AssistantCore`.

Здесь только то, что относится к HTTP: модели запросов, определение сессии по cookie и ответ файлом. Вся логика — в ядре,
поэтому анкета, начатая в боте, продолжается на сайте и наоборот.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..assistant import DOCX_MIME, AssistantCore
from .session import COOKIE_SID, WebSessions


class MessageIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    template_id: Optional[str] = Field(default=None, max_length=80)
    # сообщение не про анкету: не отвечать здесь, клиент сам запустит обычную проверку (/api/jobs, с прогрессом и снимком)
    defer: bool = False


class FieldIn(BaseModel):
    unit: str = Field(max_length=120)
    value: str = Field(default="", max_length=400)
    question: str = Field(default="", max_length=2000)  # вопрос, на который пересчитать ответ по исправленной карточке (пусто — общая проверка)


class GenerateIn(BaseModel):
    template_id: Optional[str] = Field(default=None, max_length=80)


class SubmitIn(BaseModel):
    confirm: bool = False
    include_passport: bool = False
    contact: str = Field(default="", max_length=120)  # как связаться (по желанию): почта, телефон или Telegram


class StatusIn(BaseModel):
    id: int = Field(ge=1, le=10**9)
    code: str = Field(max_length=40)


def build_assistant_router(core: AssistantCore, sessions: WebSessions) -> APIRouter:
    """HTTP-обёртка над AssistantCore: определяет сессию по cookie, остальное делает ядро."""
    router = APIRouter(prefix="/api/assistant", tags=["assistant"])
    storage = core.storage

    @router.get("/state")
    def state(request: Request, response: Response):
        v = sessions.ensure(request, response)
        return core.state(v.sid, v.status)

    @router.post("/message")
    def message(body: MessageIn, request: Request, response: Response):
        v = sessions.ensure(request, response)
        return core.message(v.sid, body.text, body.template_id, body.defer, v.status)

    @router.post("/generate")
    def generate(body: GenerateIn, request: Request):
        v = sessions.require(request)
        return core.generate(v.sid, body.template_id)

    @router.get("/download/{doc_id}")
    def download(doc_id: str, request: Request):
        v = sessions.require(request)
        row = core.download_row(v.sid, doc_id)
        return FileResponse(row["path"], media_type=DOCX_MIME, filename=row["filename"], headers={"Cache-Control": "no-store"})

    @router.get("/templates")
    def list_templates():
        return core.available_templates()

    # ---- документ сессии
    @router.post("/document")
    async def upload_document(request: Request, response: Response, file: UploadFile = File(...), doc_type: str = Form("auto")):
        v = sessions.ensure(request, response)
        data = await file.read(core.max_bytes + 1)
        return await run_in_threadpool(core.upload_document, v.sid, data, file.filename or "документ", doc_type, v.status)

    @router.get("/document")
    def get_document(request: Request, response: Response):
        v = sessions.ensure(request, response)
        return {"session": v.status, "document": core.public_doc(core.doc_record(v.sid))}

    @router.delete("/document")
    def delete_document(request: Request):
        v = sessions.require(request)
        core.delete_document(v.sid)
        return {"ok": True}

    @router.patch("/document/fields")
    def patch_field(body: FieldIn, request: Request):
        v = sessions.require(request)
        return core.patch_field(v.sid, body.unit, body.value, body.question)

    # ---- отправка обращения
    @router.get("/submission")
    def submission_preview(request: Request, include_passport: bool = False):
        v = sessions.require(request)
        return core.submission_preview(v.sid, include_passport)

    @router.post("/submission")
    def submit(body: SubmitIn, request: Request):
        v = sessions.require(request)
        return core.submit(v.sid, body.confirm, body.include_passport, body.contact, "web")

    @router.get("/next-steps")
    def next_steps(request: Request):
        v = sessions.require(request)
        return core.next_steps(v.sid)

    # ---- статус обращения по номеру и коду (без входа и без сессии)
    @router.post("/status")
    def ticket_status(body: StatusIn, request: Request):
        return core.ticket_status(body.id, body.code, request.client.host if request.client else "?")

    # ---- удаление данных и новая анкета
    @router.delete("/data")
    def delete_data(request: Request, response: Response):
        cookie = request.cookies.get(COOKIE_SID, "")
        user = sessions.accounts.user_of_cookie(cookie) if cookie else None
        if user:
            # вошедший: стираются данные аккаунта (в боте тоже), сам вход и аккаунт остаются
            storage.delete_user_data(sessions.accounts.ensure_data_session(user))
            return {"ok": True, "message": "Данные вашего аккаунта удалены: анкета, загруженный документ и готовые файлы — и на сайте, и в боте."}
        if cookie:
            storage.delete_session(cookie)
        sessions.clear_cookie(response)
        return {"ok": True, "message": "Ваши данные удалены: анкета, загруженный документ и готовые файлы."}

    return router
