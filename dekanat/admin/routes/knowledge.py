"""База знаний: документы Положения, переиндексация, контрольные вопросы."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ...knowledge_base.check import load_golden, run_check
from ...knowledge_base.documents import KnowledgeBaseError

class KbPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    active: Optional[bool] = None


def register(router: APIRouter, ctx) -> None:
    svc, storage, kbm, max_bytes, admin = ctx.svc, ctx.storage, ctx.kbm, ctx.max_bytes, ctx.admin
    def _kb_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KnowledgeBaseError as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "Документ базы знаний не найден") from exc

    @router.get("/kb")
    def list_kb(who: dict = admin):
        return kbm.list()

    @router.post("/kb/check")
    def check_kb(who: dict = admin):
        """Контрольные вопросы (config/golden_questions.yaml) -> ответы секретаря по текущей базе знаний: что сходится с ожидаемым, а что сломалось."""
        items = load_golden(Path(svc.settings.root) / "config" / "golden_questions.yaml")
        if not items:
            raise HTTPException(404, "Контрольных вопросов нет: добавьте их в config/golden_questions.yaml.")
        result = run_check(lambda question: svc.analyze(None, "", question).answer, items)
        storage.audit.log(who["username"], "kb.check", "kb", "", {"passed": result["passed"], "total": result["total"]})
        return result

    @router.post("/kb")
    async def add_kb(file: UploadFile = File(...), name: str = Form(""), who: dict = admin):
        data = await file.read(max_bytes + 1)
        return _kb_call(kbm.add, name, Path(file.filename or "документ.txt").name, data, who["username"])

    @router.get("/kb/{doc_id}")
    def get_kb(doc_id: str, who: dict = admin):
        row = kbm.get(doc_id)
        if row is None:
            raise HTTPException(404, "Документ базы знаний не найден")
        return {**row, "clause_list": _kb_call(kbm.clauses, doc_id)}

    @router.put("/kb/{doc_id}")
    async def replace_kb(doc_id: str, file: UploadFile = File(...), who: dict = admin):
        data = await file.read(max_bytes + 1)
        return _kb_call(kbm.replace, doc_id, Path(file.filename or "документ.txt").name, data, who["username"])

    @router.patch("/kb/{doc_id}")
    def patch_kb(doc_id: str, body: KbPatch, who: dict = admin):
        return _kb_call(kbm.update, doc_id, who["username"], name=body.name, active=body.active)

    @router.delete("/kb/{doc_id}")
    def delete_kb(doc_id: str, who: dict = admin):
        return {"ok": True, "action": _kb_call(kbm.delete, doc_id, who["username"])}

    @router.post("/reindex")
    def reindex(who: dict = admin):
        return kbm.reindex(who["username"])

    @router.get("/kb/{doc_id}/history")
    def kb_history(doc_id: str, who: dict = admin):
        return {"versions": kbm.history(doc_id), "log": storage.audit.list("kb", doc_id, limit=100)}
