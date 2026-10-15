"""HTTP-интерфейс аккаунтов: вход на сайте через Telegram, профиль, обращения аккаунта, webhook бота.

Всё необязательно: без входа сайт работает по cookie-сессии, как раньше. Вход появляется, только если администратор включил бота
(`telegram.enabled`) и бот отвечает.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..accounts import AccountError, Accounts, describe_device, public_user
from ..telegram.auth import TelegramAuthError, verify_webapp_init_data
from ..telegram.service import TelegramService
from .assistant import AssistantCore, AttemptLimiter
from .session import COOKIE_SID, WebSessions

log = logging.getLogger("dekanat.account")


class NotifyIn(BaseModel):
    notify: bool


class WebAppIn(BaseModel):
    init_data: str = Field(..., max_length=8192)


class LinkIn(BaseModel):
    id: int = Field(..., ge=1, le=2_000_000_000)
    code: str = Field(..., min_length=4, max_length=24)


def _ticket_public(t: dict) -> dict:
    return {"id": t["id"], "status": t["status"], "topic": t["topic"], "created_at": t["created_at"], "updated_at": t["updated_at"],
            "reply": t.get("reply"), "reply_at": t.get("reply_at")}


def build_account_router(core: AssistantCore, sessions: WebSessions, accounts: Accounts, tg: TelegramService, settings) -> APIRouter:
    router = APIRouter(tags=["account"])
    storage = core.storage
    webapp_failures = AttemptLimiter(limit=10, window=600.0)
    max_age = float(settings.get("telegram.initdata_max_age_hours", 24)) * 3600

    def telegram_info() -> dict:
        return {"enabled": tg.enabled, "login": bool(tg.enabled and tg.username), "bot": tg.username or None, "mini_app": tg.mini_app}

    def current_user(request: Request) -> Optional[dict]:
        cookie = request.cookies.get(COOKIE_SID, "")
        if cookie and accounts.touch_cookie(cookie):
            return accounts.user_of_cookie(cookie)
        return None

    def need_user(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(401, "Вы не вошли. Войдите через Telegram.")
        return user

    def need_telegram() -> None:
        if not tg.enabled:
            raise HTTPException(404, "Вход через Telegram не включён на этом сервере.")

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else ""

    # ------------------------------------------------------------------ кто я
    @router.get("/api/account/me")
    def me(request: Request):
        user = current_user(request)
        return {"user": public_user(user) if user else None, "telegram": telegram_info()}

    # ------------------------------------------------------------------ вход через бота
    @router.post("/api/auth/telegram/start")
    def login_start(request: Request, response: Response):
        need_telegram()
        if not tg.username:
            raise HTTPException(503, "Бот ещё запускается. Попробуйте через минуту.")
        v = sessions.ensure(request, response)  # у запроса входа должна быть своя cookie-сессия: подтверждение выдаётся только ей
        if v.user is not None:
            return {"already": True, "user": public_user(v.user)}
        try:
            token = accounts.start_login(v.cookie, describe_device(request.headers.get("user-agent", ""), client_ip(request)))
        except AccountError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        deep = f"?start=login_{token}"
        return {"token": token, "link": f"https://t.me/{tg.username}{deep}", "app_link": f"tg://resolve?domain={tg.username}&start=login_{token}",
                "expires_in": int(accounts.login_ttl), "bot": tg.username}

    @router.get("/api/auth/telegram/poll")
    def login_poll(token: str, request: Request, response: Response):
        need_telegram()
        cookie = request.cookies.get(COOKIE_SID, "")
        status, profile = accounts.poll_login(token, cookie)
        if status != "confirmed" or profile is None:
            return {"status": status}
        user, new_sid, moved = accounts.login_session(profile, cookie)
        sessions.set_cookie(response, request, new_sid, signed_in=True)
        return {"status": "confirmed", "user": public_user(user), "moved": moved}

    # ------------------------------------------------------------------ вход из Mini App
    @router.post("/api/auth/telegram/webapp")
    def login_webapp(body: WebAppIn, request: Request, response: Response):
        need_telegram()
        ip = client_ip(request)
        if not webapp_failures.allowed(ip):
            raise HTTPException(429, "Слишком много неудачных попыток. Подождите несколько минут.")
        try:
            profile = verify_webapp_init_data(body.init_data, tg.token, max_age)
        except TelegramAuthError as exc:
            webapp_failures.fail(ip)
            raise HTTPException(401, str(exc)) from exc
        cookie = request.cookies.get(COOKIE_SID, "")
        if cookie and not accounts.touch_cookie(cookie):
            cookie = ""
        if not cookie:
            sessions.throttle_new_sessions(request)
        user, new_sid, moved = accounts.login_session(profile, cookie)
        sessions.set_cookie(response, request, new_sid, signed_in=True)
        return {"user": public_user(user), "moved": moved}

    @router.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        accounts.logout(request.cookies.get(COOKIE_SID, ""))
        sessions.clear_cookie(response)
        return {"ok": True}

    # ------------------------------------------------------------------ профиль
    @router.patch("/api/account")
    def update_account(body: NotifyIn, request: Request):
        user = need_user(request)
        storage.users.set_notify(user["id"], body.notify)
        return {"user": public_user(storage.users.get(user["id"]))}

    @router.get("/api/account/tickets")
    def my_tickets(request: Request):
        user = need_user(request)
        return {"tickets": [_ticket_public(t) for t in storage.tickets.list_for_user(user["id"], 50)]}

    @router.post("/api/account/tickets/link")
    def link_ticket(body: LinkIn, request: Request):
        user = need_user(request)
        key = f"acc:{user['id']}"
        if not core.status_limiter.allowed(key):
            raise HTTPException(429, "Слишком много неверных попыток. Подождите несколько минут.")
        try:
            row = accounts.link_ticket(user, body.id, body.code.strip())
        except AccountError as exc:
            if exc.status == 404:
                core.status_limiter.fail(key)
            raise HTTPException(exc.status, exc.message) from exc
        return {"ticket": _ticket_public(storage.tickets.get_for_user(row["id"], user["id"]) or row)}

    @router.get("/api/account/export")
    def export(request: Request):
        """Всё, что сервис хранит об этом человеке: профиль, обращения, карточка документа и анкета. Скачивается самим человеком."""
        user = need_user(request)
        sid = accounts.ensure_data_session(user)
        ctx = core.load_ctx(sid)
        return {"profile": {**public_user(user), "telegram_username": user.get("username")}, "tickets": [_ticket_public(t) for t in storage.tickets.list_for_user(user["id"], 200)],
                "questionnaire": ctx.public(), "document": core.public_doc(core.doc_record(sid))}

    @router.delete("/api/account")
    def delete_account(request: Request, response: Response):
        user = need_user(request)
        accounts.delete_account(user["id"])
        sessions.clear_cookie(response)
        return {"ok": True}

    # ------------------------------------------------------------------ webhook (если выбран режим webhook)
    @router.post("/api/telegram/webhook/{secret}", include_in_schema=False)
    async def webhook(secret: str, request: Request):
        import hmac

        if not (tg.enabled and tg.mode == "webhook") or not hmac.compare_digest(secret, tg.webhook_path.rsplit("/", 1)[-1]):
            raise HTTPException(404, "Не найдено")
        if not tg.webhook_header_ok(request.headers.get("x-telegram-bot-api-secret-token")):
            raise HTTPException(403, "Неверный секретный заголовок")
        try:
            update = await request.json()
        except ValueError:
            raise HTTPException(400, "Не JSON") from None
        if isinstance(update, dict):
            tg.dispatch(update)
        return {"ok": True}

    return router
