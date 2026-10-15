"""Веб-интерфейс и REST API (FastAPI)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .. import __version__
from ..accounts import Accounts
from ..admin import AdminAuth
from ..admin.routes import build_admin_router
from ..assistant import AssistantCore, ServiceError, SessionLocks, remember_document
from ..config import Settings, load_settings
from ..doctypes import NEW_TYPE_TEMPLATE, DocTypeError, DocTypeManager
from ..templates import TemplateStore
from ..export import result_record
from ..kb.documents import BASE_ID, KnowledgeBaseError
from ..kb.manager import KnowledgeBaseManager
from ..ocr import photo as photo_quality
from ..ocr.engine import OcrUnavailable
from ..document_processing.formats import check_format
from ..ocr.preprocess import UnsupportedFile, load_pages
from ..pipeline import Cancelled, Dekanat, page_preview
from ..storage import Storage
from ..storage.topics import classify_topic
from ..telegram.notify import Notifier
from ..telegram.service import TelegramService
from .account import build_account_router
from .assistant import build_assistant_router
from .jobs import JobManager
from .session import COOKIE_SID, WebSessions

log = logging.getLogger("dekanat.web")
STATIC = Path(__file__).parent / "static"

PAGES = Path(__file__).parent / "static" / "pages"
# страница администратора не использует встроенные скрипты и стили, поэтому ей можно задать строгую политику
PAGE_CSP = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
OCR_DOWN_TEXT = "Распознавание текста сейчас недоступно. Попробуйте позже; вопросы по Положению работают и без документа."


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

    # ------------------------------------------------------------------ служебное
    @app.get("/api/health")
    def health(request: Request):
        info = svc.info()
        info.update({
            "version": __version__, "allow_edit": allow_edit, "max_upload_mb": max_bytes // (1024 * 1024), "jobs": True,
            # allow_edit — настройка сервера; can_edit — можно ли именно этому посетителю править (нужен вход администратора)
            "can_edit": allow_edit and auth.can_edit(request),
            "admin": {"protect_edit": auth.protect_edit, "configured": storage.admins.count() > 0, "authenticated": auth.current(request) is not None},
            "delivery": delivery_status(), "assistant": True,
            "telegram": {"enabled": tg.enabled, "login": bool(tg.enabled and tg.username), "bot": tg.username or None, "mini_app": tg.mini_app},
        })
        return info

    @app.get("/api/doctypes")
    def doctypes():
        return [
            {
                "id": d.id, "title": d.title, "short_title": d.short_title, "source": d.source,
                "fields": [{"id": f.id, "title": f.title, "kind": f.kind, "parts": [{"id": p.id, "title": p.title} for p in f.parts]} for f in d.fields],
                "examples": [s["text"] for s in _suggestions(d.id)],
                "suggestions": _suggestions(d.id),
            }
            for d in svc.doc_types.values()
        ]

    def _suggestions(type_id: str) -> list[dict]:
        """Подсказки вопросов трёх видов: проверка документа, значение поля, правила."""
        spec = svc.doc_types[type_id]
        check = {
            "study_certificate": "Хватит ли этой справки для перевода на 2 курс?",
            "academic_leave": "Достаточно ли этого заявления для академического отпуска?",
            "practice_certificate": "Подойдёт ли эта справка о практике?",
        }.get(type_id, "Всё ли в порядке с документом?")
        field_q = {
            "study_certificate": "Какая форма обучения указана в справке?",
            "academic_leave": "С какой даты начинается отпуск?",
            "practice_certificate": "Кто руководитель практики?",
        }.get(type_id)
        if field_q is None:
            f = next((f for f in spec.fields if f.kind not in ("marks", "composite")), None)
            field_q = f"Укажи {f.title if f.title[:2].isupper() else f.title[:1].lower() + f.title[1:]}" if f else "Какие данные указаны в документе?"
        out = [{"kind": "check", "text": check}, {"kind": "check", "text": "Чего не хватает в документе?"}, {"kind": "field", "text": field_q}]
        out += _rule_suggestions()
        return out

    def _rule_suggestions() -> list[dict]:
        if svc.regulation_name == "polozhenie.md":
            return [{"kind": "rules", "text": "Сколько дней рассматривается заявление?"}, {"kind": "rules", "text": "Нужна ли медицинская справка при академическом отпуске?"},
                    {"kind": "rules", "text": "Сколько стоит обучение?"}]
        sections = []
        for c in svc.clauses:
            if c.heading and c.heading not in sections:
                sections.append(c.heading)
        return [{"kind": "rules", "text": f"Что сказано про {h[:1].lower()}{h[1:]}?"} for h in sections[:2]]

    @app.get("/api/suggestions")
    def suggestions():
        return {"rules": _rule_suggestions()}

    # ------------------------------------------------------------------ анализ
    async def _read_upload(file: Optional[UploadFile], question: str) -> tuple[Optional[bytes], str]:
        data, name = None, ""
        if file is not None and file.filename:
            data = await file.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise HTTPException(413, f"Файл больше {max_bytes // (1024 * 1024)} МБ")
            if not data:
                raise HTTPException(400, "Файл пустой")
            name = file.filename
        if data is None and not question.strip():
            raise HTTPException(400, "Загрузите документ или задайте вопрос по Положению")
        return data, name

    def _payload(data, name, question, doc_type, progress=None, sid: str = "", document_first: bool = False) -> dict:
        res = svc.analyze(data, name, question, doc_type, progress=progress, document_first=document_first)
        corrected: list[str] = []
        if data and sid:
            try:  # карточка полей проверенного документа нужна анкете и правке полей на столе; без сессии проверка работает как прежде
                corrected = remember_document(storage, session_locks, sid, res, Path(name or "документ").name, hashlib.sha256(data).hexdigest())
                if corrected:  # тот же файл, поля исправлены пользователем: ответ — по исправленной карточке
                    notes = res.answer.notes
                    res.answer = svc.answer_for_fields(question, res.doc_type.id, res.fields, document_first=document_first)
                    res.answer.notes += [n for n in notes if n not in res.answer.notes]
            except Exception:
                log.exception("Не удалось сохранить документ сессии")
        payload = res.to_dict(preview=True)
        payload["corrected"] = corrected
        payload["export"] = result_record(res, None, name, question)
        # в статистику идут только тема, сценарий и итог: без текста вопроса, OCR и полей документа
        log_event("document_check" if data else "question", classify_topic(question), res.answer.verdict)
        return payload

    @app.post("/api/analyze")
    async def analyze(file: Optional[UploadFile] = File(None), question: str = Form(""), doc_type: str = Form("auto")):
        """Синхронная проверка (для скриптов и тестов). Интерфейс использует /api/jobs — там есть прогресс и отмена."""
        from starlette.concurrency import run_in_threadpool

        data, name = await _read_upload(file, question)

        def work():
            with ocr_slots:
                return _payload(data, name, question, doc_type)

        return await run_in_threadpool(work)

    # ------------------------------------------------------------------ фоновые задачи с прогрессом
    jobs = JobManager(slots=2)

    def describe_error(exc: Exception) -> str:
        """Текст ошибки для посетителя: без названий исключений и программ; подробности — только в журнале сервера."""
        if isinstance(exc, UnsupportedFile):
            return str(exc)
        if isinstance(exc, OcrUnavailable):
            log.error("Распознавание текста недоступно: %s", exc)
            return OCR_DOWN_TEXT
        log.exception("Ошибка при проверке документа")
        return "Не удалось проверить документ. Попробуйте ещё раз или загрузите другой снимок."

    @app.post("/api/jobs")
    async def create_job(request: Request, file: Optional[UploadFile] = File(None), question: str = Form(""), doc_type: str = Form("auto")):
        data, name = await _read_upload(file, question)
        sid = sessions.peek(request)  # у вошедшего документ сохраняется в данных аккаунта — они общие с ботом
        # стол: с документом — ответ по документу, по Положению — если оно упомянуто в вопросе
        job = jobs.submit(lambda progress: _payload(data, name, question, doc_type, progress, sid, document_first=True), describe_error)
        return {"id": job.id}

    @app.post("/api/preview")
    async def preview_first_page(file: UploadFile = File(...)):
        """Первый лист файла для стола: у PDF нет «картинки», пока не прошла проверка, и без этого вместо документа показывалась бы заглушка.
        Те же формат и размер, что у проверки; страница та же, в координатах которой потом придут рамки."""
        data, _ = await _read_upload(file, "")
        name = file.filename or ""

        def work():
            check_format(data, name, max_bytes)  # те же форматы, что у проверки: JPG, JPEG, PNG, PDF — по содержимому, а не по расширению
            with ocr_slots:
                return page_preview(load_pages(data, name, 1)[0])

        try:
            return await run_in_threadpool(work)
        except UnsupportedFile as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/quality")
    async def photo_check(file: UploadFile = File(...)):
        """Быстрая проверка снимка до отправки (без OCR): размытие, темнота, блики, мелкий текст. Файл не сохраняется.
        У PDF пикселей «съёмки» нет — их не проверяем. Итоговая оценка с учётом распознавания приходит вместе с результатом проверки."""
        data, _ = await _read_upload(file, "")
        name = file.filename or ""

        def work():
            check_format(data, name, max_bytes)
            if (data or b"")[:5] == b"%PDF":
                return {**photo_quality.assess_document([]), "skipped": "pdf"}
            return photo_quality.assess_document([photo_quality.assess_image(load_pages(data, name, 1)[0])])

        try:
            return await run_in_threadpool(work)
        except UnsupportedFile as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Задача не найдена (возможно, устарела) — запустите проверку заново")
        return job.snapshot()

    @app.delete("/api/jobs/{job_id}")
    def job_cancel(job_id: str):
        if not jobs.cancel(job_id):
            raise HTTPException(404, "Задача не найдена")
        return {"ok": True}

    # ------------------------------------------------------------------ Положение
    @app.get("/api/regulation")
    def regulation(request: Request):
        return {"name": svc.regulation_name, "clauses": [c.to_dict() for c in svc.clauses], "editable": allow_edit and auth.can_edit(request)}

    @app.put("/api/regulation")
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

    @app.get("/api/doctypes/template", response_class=PlainTextResponse)
    def template():
        return NEW_TYPE_TEMPLATE

    @app.get("/api/doctypes/{type_id}/yaml", response_class=PlainTextResponse)
    def type_yaml(type_id: str):
        return _typed(lambda: type_mgr.yaml(type_id))

    @app.post("/api/doctypes/validate")
    def validate(body: str = Body(..., media_type="text/plain")):
        return type_mgr.validate(body)

    @app.put("/api/doctypes/{type_id}")
    def save_type(type_id: str, request: Request, body: str = Body(..., media_type="text/plain")):
        actor = _guard_edit(request)
        return _typed(lambda: type_mgr.save(type_id, body, actor))

    @app.delete("/api/doctypes/{type_id}")
    def delete_type(type_id: str, request: Request):
        actor = _guard_edit(request)
        return _typed(lambda: type_mgr.delete(type_id, actor))

    # ------------------------------------------------------------------ примеры документов
    samples_dir = settings.path("samples_dir", "data/samples")

    def _manifest() -> list[dict]:
        mf = samples_dir / "manifest.json"
        return json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else []

    @app.get("/api/samples")
    def samples():
        return _manifest()

    @app.get("/api/samples/{name}")
    def sample_file(name: str):
        allowed = {s["file"] for s in _manifest()}
        if name not in allowed:
            raise HTTPException(404, "Пример не найден")
        return FileResponse(samples_dir / name)

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
