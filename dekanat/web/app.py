"""Веб-интерфейс и REST API (FastAPI)."""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..accounts import Accounts
from ..admin import AdminAuth
from ..admin.routes import build_admin_router
from ..assistant import OCR_DOWN_TEXT, AssistantCore, ServiceError, SessionLocks
from ..config import load_settings, Settings
from ..doctypes import DocTypeManager
from ..knowledge_base.manager import KnowledgeBaseManager
from ..ocr.engine import OcrUnavailable
from ..ocr.preprocess import UnsupportedFile
from ..pipeline import Dekanat
from ..storage import Storage
from ..telegram.notify import Notifier
from ..telegram.service import TelegramService
from ..templates import TemplateStore
from .account import build_account_router
from .assistant import build_assistant_router
from .context import WebContext
from .routes import catalog, checks, content
from .session import COOKIE_SID, WebSessions

log = logging.getLogger("dekanat.web")
STATIC = Path(__file__).parent / "static"

PAGES = Path(__file__).parent / "static" / "pages"
# страница администратора не использует встроенные скрипты и стили, поэтому ей можно задать строгую политику
PAGE_CSP = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _bootstrap_admin(auth: AdminAuth, storage: Storage) -> None:
    """Первый администратор из переменных окружения (удобно для контейнера). Пароль в репозитории и в конфиге не хранится."""
    username, password = os.environ.get("DEKANAT_ADMIN_USERNAME"), os.environ.get("DEKANAT_ADMIN_PASSWORD")
    if not (username and password) or storage.admins.count() > 0:
        return
    try:
        auth.create_admin(username, password)
        log.warning("Создан администратор «%s» из переменных окружения. Уберите DEKANAT_ADMIN_PASSWORD из окружения после первого запуска.", username)
    except ValueError as exc:
        log.error("Администратор из переменных окружения не создан: %s", exc)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    storage = Storage(settings)
    svc = Dekanat(settings, storage=storage)
    settings = svc.settings
    max_bytes = int(settings.get("server.max_upload_mb", 25)) * 1024 * 1024
    allow_edit = bool(settings.get("server.allow_edit", True))
    ocr_slots = threading.Semaphore(2)  # одновременно не больше двух OCR: CPU ноутбука не резиновый
    session_locks = SessionLocks()  # общие для ассистента и проверок со стола: запись документа сессии не теряет правку полей

    auth = AdminAuth(storage, settings)
    kbm = KnowledgeBaseManager(svc, storage, max_bytes)
    type_mgr = DocTypeManager(svc, storage)
    templates = TemplateStore(storage.templates_dir, storage.templates)
    _bootstrap_admin(auth, storage)
    try:
        storage.purge()  # срок хранения данных: просроченное удаляется при старте
    except Exception:
        log.exception("Не удалось очистить просроченные данные")

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.get("telegram.run_in_server", True):
            tg.start()  # бот работает в процессе сайта и использует те же очереди распознавания и блокировки сессий
        try:
            yield
        finally:
            tg.stop()

    app = FastAPI(title="Онлайн-деканат", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.storage, app.state.svc, app.state.auth = storage, svc, auth

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # защита от подделки запросов с чужих сайтов: изменяющий запрос должен приходить с этого же адреса
        if request.method not in _SAFE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin is not None and urlparse(origin).netloc != request.headers.get("host", ""):
                return JSONResponse({"detail": "Запрос отклонён: чужой источник (Origin)"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path == "/" and tg.mini_app:
            # Mini App показывает сайт внутри Telegram; в веб-версии Telegram это iframe — разрешаем только его
            response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self' https://web.telegram.org https://webk.telegram.org https://webz.telegram.org")
        else:
            response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/") and request.url.path != "/api/docs" and request.url.path != "/api/openapi.json":
            response.headers.setdefault("Cache-Control", "no-store")
        elif request.url.path.startswith("/static/pages/"):
            # имена файлов страниц без хэша: после обновления браузер должен перепроверить их (ETag), а не взять старую версию из кеша
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    def delivery_status() -> dict:
        channel = str(settings.get("delivery.channel", "none")).lower()
        if channel == "queue":
            if storage.admins.count() == 0:
                return {"channel": "queue", "available": False, "reason": "в системе нет администратора, который мог бы читать очередь обращений"}
            return {"channel": "queue", "available": True, "reason": ""}
        return {"channel": "none", "available": False, "reason": "канал отправки обращений не настроен (delivery.channel)"}

    def cookie_secure(request: Request) -> bool:
        mode = str(settings.get("server.cookie_secure", "auto")).lower()
        return mode in ("true", "1", "yes", "on") or (mode == "auto" and request.url.scheme == "https")

    accounts = Accounts(storage, settings)
    sessions = WebSessions(storage, accounts, settings, cookie_secure)
    notifier = Notifier(storage, settings)
    core = AssistantCore(svc, storage, templates, ocr_slots, max_bytes, delivery_status, session_locks, notifier)
    tg = TelegramService(settings, storage, svc, core, accounts, notifier, ocr_slots, session_locks)
    app.state.accounts, app.state.sessions, app.state.core, app.state.telegram, app.state.notifier = accounts, sessions, core, tg, notifier

    def log_event(scenario: str, category: str = "", status: str = "", request: Optional[Request] = None) -> None:
        try:
            sid = request.cookies.get(COOKIE_SID, "") if request is not None else ""
            storage.events.log(scenario, category, status, sid)
        except Exception:  # статистика не должна ломать основной сценарий
            log.exception("Не удалось записать событие статистики")

    @app.exception_handler(ServiceError)
    async def _service_error(_, exc):  # noqa: ANN001
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)  # ядро ассистента не знает про HTTP: статус отдаёт сайт

    @app.exception_handler(UnsupportedFile)
    async def _unsupported(_, exc):  # noqa: ANN001
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(OcrUnavailable)
    async def _ocr_down(_, exc):  # noqa: ANN001
        log.error("Распознавание текста недоступно: %s", exc)
        return JSONResponse({"detail": OCR_DOWN_TEXT}, status_code=503)

    ctx = WebContext(settings, svc, storage, auth, kbm, type_mgr, sessions, tg, session_locks, ocr_slots, max_bytes, allow_edit, delivery_status, log_event)
    for routes_module in (catalog, checks, content):
        app.include_router(routes_module.build(ctx))

    # ------------------------------------------------------------------ ассистент и администратор
    app.include_router(build_assistant_router(core, sessions))
    app.include_router(build_account_router(core, sessions, accounts, tg, settings))
    app.include_router(build_admin_router(svc, storage, auth, kbm, templates, max_bytes, notifier, tg, type_mgr))

    def _page(name: str) -> FileResponse:
        path = PAGES / name
        if not path.exists():
            raise HTTPException(404, "Страница не найдена")
        return FileResponse(path, headers={"Content-Security-Policy": PAGE_CSP, "Cache-Control": "no-cache"})

    @app.get("/assistant", include_in_schema=False)
    def assistant_page():
        # возможности ассистента (анкета, правка полей, DOCX, отправка в деканат) встроены в «Стол декана» на главной
        return RedirectResponse("/", status_code=307)

    @app.get("/admin")
    def admin_page():
        return _page("admin.html")

    # ------------------------------------------------------------------ статика
    @app.get("/")
    def index(request: Request):
        log_event("visit", "desk")
        page = STATIC / "app" / "index.html"
        if not page.exists():
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>Онлайн-деканат</title><h1>Интерфейс не собран</h1>"
                "<p>Выполните: <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code> — "
                "или используйте API: <a href=\"/api/docs\">/api/docs</a>.</p>", status_code=503)
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app
