"""Проверка документа: синхронная (/api/analyze), фоновая с прогрессом и отменой (/api/jobs), предпросмотр и быстрая оценка снимка."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from ...assistant import OCR_DOWN_TEXT, remember_document
from ...export import result_record
from ...ocr import photo as photo_quality
from ...ocr.engine import OcrUnavailable
from ...ocr.formats import check_format
from ...ocr.preprocess import load_pages, UnsupportedFile
from ...pipeline import page_preview
from ...storage.topics import classify_topic
from ..context import WebContext
from ..jobs import JobManager


log = logging.getLogger("dekanat.web")


def build(ctx: WebContext) -> APIRouter:
    router = APIRouter()
    svc, storage, sessions, session_locks, ocr_slots, max_bytes, log_event = ctx.svc, ctx.storage, ctx.sessions, ctx.session_locks, ctx.ocr_slots, ctx.max_bytes, ctx.log_event

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

    @router.post("/api/analyze")
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

    @router.post("/api/jobs")
    async def create_job(request: Request, file: Optional[UploadFile] = File(None), question: str = Form(""), doc_type: str = Form("auto")):
        data, name = await _read_upload(file, question)
        sid = sessions.peek(request)  # у вошедшего документ сохраняется в данных аккаунта — они общие с ботом
        # стол: с документом — ответ по документу, по Положению — если оно упомянуто в вопросе
        job = jobs.submit(lambda progress: _payload(data, name, question, doc_type, progress, sid, document_first=True), describe_error)
        return {"id": job.id}

    @router.post("/api/preview")
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

    @router.post("/api/quality")
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

    @router.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Задача не найдена (возможно, устарела) — запустите проверку заново")
        return job.snapshot()

    @router.delete("/api/jobs/{job_id}")
    def job_cancel(job_id: str):
        if not jobs.cancel(job_id):
            raise HTTPException(404, "Задача не найдена")
        return {"ok": True}

    return router
