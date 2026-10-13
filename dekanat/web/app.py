"""Веб-интерфейс и REST API (FastAPI)."""

from __future__ import annotations

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

from .. import __version__
from ..admin import AdminAuth
from ..admin.routes import build_admin_router
from ..config import ConfigError, Settings, load_settings, parse_doc_type_yaml
from ..templates import TemplateStore
from ..export import result_record
from ..kb.documents import BASE_ID, KnowledgeBaseError
from ..kb.manager import KnowledgeBaseManager
from ..ocr.engine import OcrUnavailable
from ..ocr.preprocess import UnsupportedFile
from ..pipeline import Cancelled, Dekanat
from ..storage import Storage
from ..storage.topics import classify_topic
from .assistant import COOKIE_SID, SessionLocks, build_assistant_router, remember_document
from .jobs import JobManager

log = logging.getLogger("dekanat.web")
STATIC = Path(__file__).parent / "static"

NEW_TYPE_TEMPLATE = """# Новый тип документа. Опишите поля — код менять не нужно.
id: my_document            # латиница, строчные буквы, цифры и «_»
title: Название документа
short_title: Документ
genitive: документа        # «Не хватает ... документа»
aliases: [название документа]
classify:
  keywords:                # по этим фразам тип определяется автоматически
    - {phrase: название документа, weight: 6}
fields:
  - id: student_name
    title: ФИО студента
    kind: person           # person | org | text | date | number | choice | period | grade | group | course | composite | marks
    labels: [ФИО студента, ФИО, Студент]
    mention: [ФИО студента, ФИО]
    missing_phrase: ФИО студента
  - id: doc_date
    title: Дата
    kind: date
    labels: [Дата, Дата выдачи]
    missing_phrase: даты
"""


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
    templates = TemplateStore(storage.templates_dir, storage.templates)
    _bootstrap_admin(auth, storage)
    try:
        storage.purge()  # срок хранения данных: просроченное удаляется при старте
    except Exception:
        log.exception("Не удалось очистить просроченные данные")

    app = FastAPI(title="Онлайн-деканат", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
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

    def log_event(scenario: str, category: str = "", status: str = "", request: Optional[Request] = None) -> None:
        try:
            sid = request.cookies.get(COOKIE_SID, "") if request is not None else ""
            storage.events.log(scenario, category, status, sid)
        except Exception:  # статистика не должна ломать основной сценарий
            log.exception("Не удалось записать событие статистики")

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

    def _payload(data, name, question, doc_type, progress=None, sid: str = "") -> dict:
        res = svc.analyze(data, name, question, doc_type, progress=progress)
        corrected: list[str] = []
        if data and sid:
            try:  # карточка полей проверенного документа нужна анкете и правке полей на столе; без сессии проверка работает как прежде
                corrected = remember_document(storage, session_locks, sid, res, Path(name or "документ").name, hashlib.sha256(data).hexdigest())
                if corrected:  # тот же файл, поля исправлены пользователем: ответ — по исправленной карточке
                    notes = res.answer.notes
                    res.answer = svc.answer_for_fields(question, res.doc_type.id, res.fields)
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
        sid = request.cookies.get(COOKIE_SID, "")
        job = jobs.submit(lambda progress: _payload(data, name, question, doc_type, progress, sid), describe_error)
        return {"id": job.id}

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
    def _type_path(type_id: str) -> Path:
        spec = svc.doc_types.get(type_id)
        if spec and spec.source:
            return svc.settings.doc_types_dir / spec.source
        return svc.settings.doc_types_dir / f"{type_id}.yaml"

    @app.get("/api/doctypes/template", response_class=PlainTextResponse)
    def template():
        return NEW_TYPE_TEMPLATE

    @app.get("/api/doctypes/{type_id}/yaml", response_class=PlainTextResponse)
    def type_yaml(type_id: str):
        path = _type_path(type_id)
        if type_id not in svc.doc_types or not path.exists():
            raise HTTPException(404, "Тип документа не найден")
        return path.read_text(encoding="utf-8")

    @app.post("/api/doctypes/validate")
    def validate(body: str = Body(..., media_type="text/plain")):
        try:
            spec = parse_doc_type_yaml(body, "редактор")
        except ConfigError as exc:
            return {"ok": False, "error": str(exc)}
        units = sum(len(f.parts) or 1 for f in spec.fields)
        return {"ok": True, "id": spec.id, "title": spec.title, "fields": len(spec.fields), "units": units}

    @app.put("/api/doctypes/{type_id}")
    def save_type(type_id: str, request: Request, body: str = Body(..., media_type="text/plain")):
        if not allow_edit:
            raise HTTPException(403, "Редактирование отключено (server.allow_edit: false)")
        actor = auth.actor_for_edit(request)
        try:
            spec = parse_doc_type_yaml(body, f"{type_id}.yaml")
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        if type_id != "new" and spec.id != type_id:
            raise HTTPException(400, f"id в YAML («{spec.id}») не совпадает с редактируемым типом («{type_id}»)")
        if type_id == "new" and spec.id in svc.doc_types:
            raise HTTPException(400, f"Тип с id «{spec.id}» уже существует — измените id")
        path = _type_path(spec.id)
        previous = path.read_text(encoding="utf-8") if path.exists() else None
        path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
        try:
            svc.reload()
        except Exception as exc:  # откатываем, чтобы не сломать сервис
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(previous, encoding="utf-8")
            svc.reload()
            raise HTTPException(400, f"Конфиг не принят: {exc}") from exc
        storage.audit.log(actor, "doctype.save", "doctype", spec.id, {"sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "created": previous is None})
        return {"ok": True, "id": spec.id, "title": spec.title}

    @app.delete("/api/doctypes/{type_id}")
    def delete_type(type_id: str, request: Request):
        if not allow_edit:
            raise HTTPException(403, "Редактирование отключено (server.allow_edit: false)")
        actor = auth.actor_for_edit(request)
        if type_id not in svc.doc_types:
            raise HTTPException(404, "Тип документа не найден")
        if len(svc.doc_types) <= 1:
            raise HTTPException(400, "Нельзя удалить последний тип документа")
        _type_path(type_id).unlink(missing_ok=True)
        svc.reload()
        storage.audit.log(actor, "doctype.delete", "doctype", type_id)
        return {"ok": True}

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
    app.include_router(build_assistant_router(svc, storage, templates, ocr_slots, max_bytes, delivery_status, cookie_secure, session_locks))
    app.include_router(build_admin_router(svc, storage, auth, kbm, templates, max_bytes))

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
