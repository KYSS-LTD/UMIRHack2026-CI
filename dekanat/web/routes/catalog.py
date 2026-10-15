"""Каталог для интерфейса: состояние сервиса, типы документов, подсказки вопросов и примеры документов."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ... import __version__
from ..context import WebContext


def build(ctx: WebContext) -> APIRouter:
    router = APIRouter()
    settings, svc, storage, auth, tg, max_bytes, allow_edit, delivery_status = ctx.settings, ctx.svc, ctx.storage, ctx.auth, ctx.tg, ctx.max_bytes, ctx.allow_edit, ctx.delivery_status

    # ------------------------------------------------------------------ служебное
    @router.get("/api/health")
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

    @router.get("/api/doctypes")
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

    @router.get("/api/suggestions")
    def suggestions():
        return {"rules": _rule_suggestions()}

    # ------------------------------------------------------------------ примеры документов
    samples_dir = settings.path("samples_dir", "data/samples")

    def _manifest() -> list[dict]:
        mf = samples_dir / "manifest.json"
        return json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else []

    @router.get("/api/samples")
    def samples():
        return _manifest()

    @router.get("/api/samples/{name}")
    def sample_file(name: str):
        allowed = {s["file"] for s in _manifest()}
        if name not in allowed:
            raise HTTPException(404, "Пример не найден")
        return FileResponse(samples_dir / name)

    return router
