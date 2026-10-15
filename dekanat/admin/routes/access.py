"""Вход, выход, пароль и сводка кабинета."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ..auth import COOKIE_ADMIN

class LoginIn(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=300)


class PasswordIn(BaseModel):
    current: str = Field(max_length=300)
    new: str = Field(max_length=300)


def register(router: APIRouter, ctx) -> None:
    svc, storage, auth, kbm, telegram, admin = ctx.svc, ctx.storage, ctx.auth, ctx.kbm, ctx.telegram, ctx.admin
    @router.post("/login")
    def login(body: LoginIn, request: Request, response: Response):
        token, info = auth.login(request, body.username, body.password)
        auth.set_cookie(response, request, token)
        return info

    @router.post("/logout")
    def logout(request: Request, response: Response):
        auth.logout(request)
        auth.clear_cookie(response)
        return {"ok": True}

    @router.get("/me")
    def me(who: dict = admin):
        return {"username": who["username"], "role": who["role"]}

    @router.post("/password")
    def password(body: PasswordIn, request: Request, who: dict = admin):
        auth.change_password(who, body.current, body.new, keep_token=request.cookies.get(COOKIE_ADMIN, ""))
        return {"ok": True}

    @router.get("/info")
    def info(who: dict = admin):
        tickets_new = storage.tickets.list(status="new", limit=1)[1]
        return {
            "username": who["username"], "llm": svc.llm.status(), "regulation": {"name": svc.regulation_name, "clauses": len(svc.clauses)},
            "templates": len(storage.templates.list()), "kb_documents": len(kbm.list()), "tickets_new": tickets_new,
            "delivery": delivery_status(), "active_sessions": storage.sessions.count_active(), "ocr": svc.engine.info(),
            "telegram": {**(telegram.status() if telegram else {"enabled": False}), "users": storage.users.count(), "web_linked": storage.users.count_web_linked()},
        }

    def delivery_status() -> dict:
        channel = str(svc.settings.get("delivery.channel", "none")).lower()
        if channel == "queue":
            if storage.admins.count() == 0:
                return {"channel": "queue", "available": False, "reason": "нет ни одного администратора, который мог бы читать очередь"}
            return {"channel": "queue", "available": True, "reason": ""}
        return {"channel": "none", "available": False, "reason": "канал отправки обращений не настроен (delivery.channel)"}
