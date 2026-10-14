"""Состояние диалога одной сессии. Хранится в БД после КАЖДОГО ответа, поэтому переживает перезапуск сервера."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional

IDLE, SCREENING, REVIEW, DOCUMENT_GENERATION, COMPLETED = "idle", "screening", "review", "document_generation", "completed"
STATES = (IDLE, SCREENING, REVIEW, DOCUMENT_GENERATION, COMPLETED)


@dataclass
class DialogContext:
    state: str = IDLE
    scenario: str = ""  # transfer | enrollment | "" (ещё не известно)
    data: dict = field(default_factory=dict)  # подтверждённые ответы анкеты: ключ -> значение
    skipped: list = field(default_factory=list)  # пропущенные необязательные вопросы
    current: Optional[str] = None  # ключ вопроса, на который ждём ответ
    editing: bool = False  # правим одно уже заданное поле: после ответа вернёмся туда, откуда пришли
    resume: Optional[str] = None  # ключ вопроса, к которому вернуться после правки (None — к обзору)
    pick_fix: bool = False  # ждём номер/название поля, которое нужно исправить
    pending_offer: str = ""  # предложение анкеты, ждущее «да/нет» (transfer | enrollment | statement)
    attempts: dict = field(default_factory=dict)  # неудачные попытки по ключам
    suggestions: dict = field(default_factory=dict)  # подсказки из загруженного документа: ключ анкеты -> значение
    case_id: int = 1  # номер сценария в сессии (для идемпотентной отправки обращения)
    ticket_id: Optional[int] = None  # обращение, отправленное по этой анкете
    ticket_code: str = ""  # код для проверки статуса этого обращения (виден студенту, пока жива сессия)
    generated: list = field(default_factory=list)  # id готовых документов этой анкеты
    gen_cache: dict = field(default_factory=dict)  # отпечаток (шаблон + данные) -> id документа: повторное нажатие не плодит файлы
    version: int = 0  # растёт при каждом изменении (защита от устаревших действий)
    passport_at: float = 0.0  # когда введены паспортные данные: по сроку privacy.passport_ttl_minutes они удаляются из состояния

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "DialogContext":
        if not isinstance(raw, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        ctx = cls(**{k: v for k, v in raw.items() if k in known})
        # повреждённое или устаревшее состояние не должно ронять диалог: неверные типы заменяются пустыми значениями
        for name in ("data", "attempts", "suggestions", "gen_cache"):
            if not isinstance(getattr(ctx, name), dict):
                setattr(ctx, name, {})
        for name in ("skipped", "generated"):
            if not isinstance(getattr(ctx, name), list):
                setattr(ctx, name, [])
        if ctx.state not in STATES:
            ctx.state = IDLE
        if ctx.scenario not in ("", "transfer", "enrollment"):
            ctx.scenario = ""
        if not isinstance(ctx.passport_at, (int, float)):
            ctx.passport_at = 0.0
        return ctx

    def reset_case(self) -> None:
        """Новый сценарий: данные прошлой анкеты стираются, номер сценария растёт."""
        self.state, self.scenario, self.data, self.skipped = IDLE, "", {}, []
        self.current, self.editing, self.resume, self.pick_fix, self.pending_offer = None, False, None, False, ""
        self.attempts, self.ticket_id, self.ticket_code, self.generated, self.gen_cache, self.passport_at = {}, None, "", [], {}, 0.0
        self.case_id += 1

    def public(self) -> dict[str, Any]:
        """Что можно показывать клиенту: без служебных полей."""
        return {"state": self.state, "scenario": self.scenario, "current": self.current, "editing": self.editing, "case_id": self.case_id,
                "answered": [k for k in self.data if k != "debts_details"], "version": self.version}
