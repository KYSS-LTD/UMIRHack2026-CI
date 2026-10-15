"""Конечный автомат диалога: idle -> screening -> review -> document_generation -> completed.

Автомат не знает про веб, хранилище и OCR: на вход состояние (`DialogContext`) и сообщение, на выход — ответ (`Reply`).
Состояние сохраняет вызывающий код после каждого сообщения. Вопросы идут по фиксированному чек-листу; к следующему вопросу
переход только после того, как ответ на текущий разобран и принят.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from rapidfuzz import fuzz

from ..llm.schemas import is_grounded_in
from . import intents
from .intents import ENROLLMENT, STATEMENT, TRANSFER, detect_intent, looks_ambiguous, parse_command
from .questions import AMBIGUOUS, OK, QUESTIONS, REQUEST_TYPES, ParseResult, Question, ambiguous, format_value, norm, ok, question_order
from .state import COMPLETED, DOCUMENT_GENERATION, IDLE, REVIEW, SCREENING, DialogContext

MAX_MESSAGE = 2000
YES_LIKE = ("yes", "confirm", "start")
DISCLAIMER = "Анкета нужна только для подготовки заявления. Решение о переводе или зачислении принимает деканат."


@dataclass
class Reply:
    text: str
    state: str
    kind: str  # question | clarify | error | offer | info | review | generation | generate | done | cancelled | passthrough
    question: Optional[str] = None
    options: list = field(default_factory=list)
    progress: Optional[tuple] = None
    events: list = field(default_factory=list)  # для статистики: ("screening_started", "transfer") и т. п.

    def to_dict(self) -> dict:
        return {"text": self.text, "state": self.state, "kind": self.kind, "question": self.question, "options": self.options,
                "progress": list(self.progress) if self.progress else None}


class DialogMachine:
    def __init__(self, llm: Any = None):
        self.llm = llm  # LLMService | None; без него (или при USE_LOCAL_LLM=False) разбор только правилами

    # ====================================================================== вход
    def handle(self, ctx: DialogContext, text: str) -> Reply:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        ctx.version += 1
        if not text:
            return self._info(ctx, "Напишите сообщение." if ctx.state == IDLE else "Напишите ответ на вопрос выше.")
        if len(text) > MAX_MESSAGE:
            return self._info(ctx, f"Сообщение слишком длинное (больше {MAX_MESSAGE} символов). Напишите короче.")
        self._repair(ctx)
        cmd = parse_command(text)
        handler = {IDLE: self._idle, COMPLETED: self._idle, SCREENING: self._screening, REVIEW: self._review, DOCUMENT_GENERATION: self._generation}[ctx.state]
        return handler(ctx, text, cmd)

    @staticmethod
    def _repair(ctx: DialogContext) -> None:
        """Состояние из хранилища могло повредиться или устареть (например, вопрос убрали из чек-листа): возвращаем его в согласованный вид."""
        if ctx.state == SCREENING and ctx.current not in QUESTIONS:
            ctx.current = next((k for k in question_order(ctx.scenario) if k not in ctx.data and k not in ctx.skipped), None)
            if ctx.current is None:
                ctx.state = REVIEW
        if ctx.state in (REVIEW, DOCUMENT_GENERATION) and not ctx.data:
            ctx.state = IDLE
            ctx.pick_fix = ctx.editing = False

    @staticmethod
    def expire_sensitive(ctx: DialogContext, ttl_seconds: float, now: Optional[float] = None) -> bool:
        """Паспортные данные не хранятся дольше необходимого: по истечении срока они удаляются из анкеты (поле считается пропущенным)."""
        if ctx.passport_at and "passport_data" in ctx.data and (now if now is not None else time.time()) - ctx.passport_at > ttl_seconds:
            ctx.data.pop("passport_data", None)
            if "passport_data" not in ctx.skipped:
                ctx.skipped.append("passport_data")
            ctx.passport_at = 0.0
            return True
        return False

    # ====================================================================== idle / completed
    def _idle(self, ctx: DialogContext, text: str, cmd: Optional[tuple]) -> Reply:
        if ctx.pending_offer:
            offer, ctx.pending_offer = ctx.pending_offer, ""
            if cmd and cmd[0] in YES_LIKE:
                return self.start(ctx, offer if offer in REQUEST_TYPES else "")
            if cmd and cmd[0] == "no":
                return self._info(ctx, "Хорошо, анкету не запускаю. Если понадобится — напишите «Я хочу перевестись» или «Как к вам поступить?». Вопросы по документам и Положению можно задавать в любой момент.")
            # любое другое сообщение отменяет предложение и обрабатывается как обычное
        if cmd and cmd[0] == "start":
            return self.start(ctx, "")
        intent = detect_intent(text)
        if intent is None and self.llm is not None and getattr(self.llm, "enabled", False) and looks_ambiguous(text):
            res = self.llm.detect_intent(text, ctx.state)
            if res and res.confidence in ("high", "medium") and res.intent in (TRANSFER, ENROLLMENT, STATEMENT):
                intent = res.intent
        if intent:
            ctx.pending_offer = intent
            return Reply(f"Похоже, вы хотите {intents.SCENARIO_TITLES[intent]}. Могу провести короткую анкету (6 основных вопросов) и подготовить заявление в виде DOCX-файла. "
                         f"{DISCLAIMER}\n\nНачнём? Ответьте «да» или «нет».", ctx.state, "offer", options=["Да, начать", "Нет"], events=[("offer", intent)])
        if cmd and cmd[0] == "help":
            return self._info(ctx, "Я могу ответить на вопрос по Положению, проверить загруженный документ или провести анкету для заявления о переводе/зачислении. Напишите, например: «Я хочу перевестись».")
        if cmd and cmd[0] in ("yes", "confirm", "no", "skip", "back", "fix", "generate", "cancel", "restart", "resume"):
            return self._info(ctx, "Сейчас нет активной анкеты. Задайте вопрос или напишите «Я хочу перевестись», чтобы начать анкету.")
        return Reply("", ctx.state, "passthrough")

    # ====================================================================== запуск / сброс
    def start(self, ctx: DialogContext, scenario: str = "") -> Reply:
        """Явный запуск анкеты. Если анкета уже идёт, она не перезапускается (для этого есть команда «заново»)."""
        if ctx.state in (SCREENING, REVIEW, DOCUMENT_GENERATION):
            note = "Анкета уже идёт, продолжим с того же места. Чтобы начать заново, напишите «заново»."
            return self._resume_prompt(ctx, note)
        if ctx.state == COMPLETED:
            ctx.reset_case()
        ctx.pending_offer = ""
        ctx.state = SCREENING
        ctx.scenario = scenario if scenario in REQUEST_TYPES else ""
        ctx.data, ctx.skipped, ctx.attempts = ({"request_type": ctx.scenario} if ctx.scenario else {}), [], {}
        ctx.editing, ctx.resume, ctx.pick_fix = False, None, False
        order = question_order(ctx.scenario)
        ctx.current = order[0]
        intro = ("Начинаем анкету. Я задам несколько коротких вопросов. Можно написать «назад» (вернуться), «исправить» (изменить ответ), "
                 "«пропустить» (для необязательных) или «заново». " + DISCLAIMER)
        reply = self._ask(ctx)
        reply.text = intro + "\n\n" + reply.text
        reply.events = [("screening_started", ctx.scenario or "unknown")]
        return reply

    def _restart(self, ctx: DialogContext) -> Reply:
        scenario = ctx.scenario
        ctx.state = IDLE  # start() не перезапускает идущую анкету сам, поэтому сначала сбрасываем состояние
        ctx.generated = []
        reply = self.start(ctx, scenario)
        reply.text = "Начинаем заново, прежние ответы удалены.\n\n" + reply.text
        return reply

    def _cancel(self, ctx: DialogContext) -> Reply:
        ctx.reset_case()
        return Reply("Анкета отменена, введённые данные удалены. Если понадобится, напишите «Я хочу перевестись».", ctx.state, "cancelled", events=[("screening_cancelled", "")])

    # ====================================================================== вопросы
    def _info(self, ctx: DialogContext, text: str, kind: str = "info") -> Reply:
        return Reply(text, ctx.state, kind)

    def _progress(self, ctx: DialogContext) -> Optional[tuple]:
        order = question_order(ctx.scenario)
        if ctx.current in order:
            return (order.index(ctx.current) + 1, len(order))
        return None

    def _ask(self, ctx: DialogContext) -> Reply:
        q = QUESTIONS[ctx.current]
        text = q.prompt
        options = list(q.options)
        suggestion = ctx.suggestions.get(q.key)
        if suggestion and q.key not in ctx.data:
            text += f"\n\nВ загруженном документе найдено: «{suggestion}». Напишите «да», если это верно, или введите своё значение."
            options = ["Да"] + [o for o in options if o != "Да"]
        if not q.required:
            text += "\n(Необязательный вопрос.)"
        prog = self._progress(ctx)
        label = f"Вопрос {prog[0]} из {prog[1]}. " if prog else ""
        return Reply(label + text, ctx.state, "question", question=q.key, options=options, progress=prog)

    def _resume_prompt(self, ctx: DialogContext, note: str) -> Reply:
        if ctx.state == SCREENING and ctx.current:
            r = self._ask(ctx)
        elif ctx.state == REVIEW:
            r = self._to_review(ctx)
        else:
            r = Reply("Данные подтверждены. Выберите шаблон и нажмите «Сформировать документ».", ctx.state, "generation", options=["Сформировать документ"])
        r.text = note + "\n\n" + r.text
        return r

    def _next(self, ctx: DialogContext) -> Reply:
        for key in question_order(ctx.scenario):
            if key not in ctx.data and key not in ctx.skipped:
                ctx.current = key
                return self._ask(ctx)
        ctx.current = None
        return self._to_review(ctx)

    # ====================================================================== screening
    def _screening(self, ctx: DialogContext, text: str, cmd: Optional[tuple]) -> Reply:
        if cmd:
            name, arg = cmd
            if name != "fix":
                ctx.pick_fix = False  # любая другая команда отменяет «какое поле исправить?»
            if name == "cancel":
                return self._cancel(ctx)
            if name == "restart":
                return self._restart(ctx)
            if name == "back":
                return self._back(ctx)
            if name == "skip":
                return self._skip(ctx)
            if name == "fix":
                return self._fix(ctx, arg)
            if name == "resume":
                return self._resume_prompt(ctx, "Продолжаем с того же места.")
            if name == "start":  # «начать анкету» посреди анкеты — не ответ на вопрос и не перезапуск
                return self._resume_prompt(ctx, "Анкета уже идёт, продолжим. Чтобы начать заново, напишите «заново».")
            if name == "help":
                r = self._ask(ctx)
                r.text += "\n\nКоманды: «назад», «исправить», «пропустить» (для необязательных), «заново», «отмена»."
                return r
        if ctx.pick_fix:
            return self._resolve_fix(ctx, text)
        if ctx.current != "request_type" and detect_intent(text):  # «Я хочу перевестись» посреди анкеты — не повод её перезапускать
            r = self._ask(ctx)
            r.text = "Анкета уже идёт, продолжим. Чтобы начать заново, напишите «заново».\n\n" + r.text
            return r
        return self._answer(ctx, text, cmd)

    def _answer(self, ctx: DialogContext, text: str, cmd: Optional[tuple]) -> Reply:
        key = ctx.current
        q = QUESTIONS[key]
        raw = text
        suggestion = ctx.suggestions.get(key)
        if suggestion and cmd and cmd[0] in ("yes", "confirm"):
            raw = suggestion  # «да» принимает значение, найденное в загруженном документе
        res = q.parse(raw)
        if res.status != OK:
            res = self._llm_help(q, text, res)
        if res.status == OK:
            return self._accept(ctx, q, res)
        ctx.attempts[key] = ctx.attempts.get(key, 0) + 1
        msg = res.message
        if not q.required and ctx.attempts[key] >= 2:
            msg += " Либо напишите «пропустить»."
        return Reply(msg, ctx.state, "clarify" if res.status == AMBIGUOUS else "error", question=key, options=list(q.options), progress=self._progress(ctx))

    def _llm_help(self, q: Question, text: str, rule: ParseResult) -> ParseResult:
        """Модель (если включена) нормализует свободный ответ. Принимается только однозначное значение, прошедшее те же проверки, что и ввод руками."""
        llm = self.llm
        if llm is None or not getattr(llm, "enabled", False) or q.sensitive or rule.extra.get("conflict"):
            return rule
        try:
            res = llm.normalize_answer(q.prompt, text, q.kind, q.choices, q.value_hint)
        except Exception:  # любая сбойная модель — продолжаем на правилах
            return rule
        if res is None:
            return rule
        if res.status != "clear":
            # вопрос «да / нет» уточняется нашей фиксированной формулировкой: свободный вопрос модели мог исходить из ответа, которого человек не давал
            # (на живом прогоне на «долгов вроде нет» Qwen2.5-3B переспросила «Уточните предметы с задолженностями»); для остальных типов вопрос модели остаётся
            if res.status == "ambiguous" and res.clarification and q.kind != "bool":
                return ambiguous(res.clarification)
            return rule
        value = res.value
        if q.kind == "bool":
            details = res.details if (res.details and is_grounded_in(res.details, text)) else None
            return ok(bool(value), details=details if value else None)
        if q.kind == "int":
            checked = q.parse(str(value))
            return checked if checked.status == OK else rule
        if q.kind == "enum":
            if q.key == "request_type":
                return ok(value) if value in q.choices else rule
            checked = q.parse(str(value))
            return checked if checked.status == OK else rule
        if not is_grounded_in(str(value), text):
            return rule  # модель не вправе придумывать названия и имена: значение должно состоять из слов самого сообщения
        checked = q.parse(str(value))
        return checked if checked.status == OK else rule

    def _accept(self, ctx: DialogContext, q: Question, res: ParseResult) -> Reply:
        ctx.data[q.key] = res.value
        if q.key == "has_debts":
            ctx.data["debts_details"] = res.extra.get("details")
        if q.key == "request_type":
            ctx.scenario = res.value
        if q.key == "passport_data":
            ctx.passport_at = time.time()
        if q.key in ctx.skipped:
            ctx.skipped.remove(q.key)
        ctx.attempts.pop(q.key, None)
        if ctx.editing:
            return self._finish_edit(ctx, f"Исправлено: {q.title} — {q.show(res.value, ctx.data)}.")
        return self._next(ctx)

    def _finish_edit(self, ctx: DialogContext, note: str) -> Reply:
        """Правка одного поля закончена (ответом или пропуском): возвращаемся туда, откуда пришли — к вопросу, на котором остановились, либо к обзору."""
        ctx.editing, ctx.pick_fix = False, False
        resume, ctx.resume = ctx.resume, None
        if resume and resume in QUESTIONS and resume not in ctx.data and resume not in ctx.skipped:
            ctx.current = resume
            r = self._ask(ctx)
        elif resume is None:
            ctx.current = None
            r = self._to_review(ctx)
        else:
            return self._next(ctx)
        r.text = note + "\n\n" + r.text
        return r

    def _back(self, ctx: DialogContext) -> Reply:
        order = question_order(ctx.scenario)
        idx = order.index(ctx.current) if ctx.current in order else 0
        if ctx.editing:  # отмена правки: возвращаемся туда, откуда пришли
            resume, ctx.editing, ctx.resume = ctx.resume, False, None
            if resume and resume in QUESTIONS:
                ctx.current = resume
                return self._ask(ctx)
            return self._to_review(ctx)
        if idx == 0:
            return self._info(ctx, "Это первый вопрос, назад вернуться некуда.\n\n" + self._ask(ctx).text, "question")
        prev = order[idx - 1]
        old = ctx.data.pop(prev, None)
        ctx.skipped = [k for k in ctx.skipped if k != prev]
        ctx.current = prev
        r = self._ask(ctx)
        if old is not None:
            r.text = f"Вернулись на шаг назад (прежний ответ: {format_value(prev, old, ctx.data)}).\n\n" + r.text
        return r

    def _skip(self, ctx: DialogContext) -> Reply:
        q = QUESTIONS[ctx.current]
        if not q.required:
            if q.key not in ctx.skipped:
                ctx.skipped.append(q.key)
            ctx.data.pop(q.key, None)
            if q.key == "passport_data":
                ctx.passport_at = 0.0
            if ctx.editing:
                return self._finish_edit(ctx, f"Поле «{q.title}» пропущено.")
            return self._next(ctx)
        r = self._ask(ctx)
        r.text = "Этот вопрос обязательный, пропустить его нельзя.\n\n" + r.text
        return r

    # ====================================================================== исправление ответов
    def _targets(self, ctx: DialogContext) -> list:
        """Поля, которые можно исправить, в порядке чек-листа (и в той же нумерации, что в обзоре)."""
        keys = [k for k in question_order(ctx.scenario) if k in ctx.data or k in ctx.skipped]
        if "request_type" in ctx.data and "request_type" not in keys:  # известен из намерения: правится, но не сбивает основную нумерацию
            keys.insert(keys.index("target_specialty") if "target_specialty" in keys else len(keys), "request_type")
        return keys

    def _fix(self, ctx: DialogContext, arg: str) -> Reply:
        targets = self._targets(ctx)
        if not targets:
            return self._info(ctx, "Пока нечего исправлять — ответов ещё нет.")
        if not arg:
            ctx.pick_fix = True
            lines = "\n".join(f"{i}. {QUESTIONS[k].title}: {self._value_line(ctx, k)}" for i, k in enumerate(targets, 1))
            return Reply("Что нужно исправить? Напишите номер или название поля:\n" + lines, ctx.state, "question",
                         options=[QUESTIONS[k].title for k in targets])
        return self._resolve_fix(ctx, arg)

    def _resolve_fix(self, ctx: DialogContext, text: str) -> Reply:
        targets = self._targets(ctx)
        n = norm(text).strip(" .")
        key: Optional[str] = None
        if n.isdigit() and 1 <= int(n) <= len(targets):
            key = targets[int(n) - 1]
        else:
            best = 0
            for k in targets:
                q = QUESTIONS[k]
                for alias in (norm(q.title), *q.aliases):
                    a = norm(alias)
                    if a and (a == n or a in n) and len(a) > best:
                        key, best = k, len(a)
        if key is None:
            ctx.pick_fix = True
            return Reply("Не понял, какое поле исправить. Напишите номер из списка или название поля, например «курс».", ctx.state, "error",
                         options=[QUESTIONS[k].title for k in targets])
        ctx.pick_fix = False
        return self._edit(ctx, key)

    def _edit(self, ctx: DialogContext, key: str) -> Reply:
        ctx.editing = True
        ctx.resume = ctx.current if ctx.state == SCREENING and ctx.current != key else None
        ctx.current = key
        old = ctx.data.get(key)
        ctx.state = SCREENING if ctx.state == REVIEW else ctx.state
        r = self._ask(ctx)
        if old is not None:
            r.text = f"Сейчас записано: {format_value(key, old, ctx.data)}.\n\n" + r.text
        return r

    def _value_line(self, ctx: DialogContext, key: str) -> str:
        if key in ctx.skipped:
            return "— (пропущено)"
        return QUESTIONS[key].show(ctx.data.get(key), ctx.data)

    # ====================================================================== review
    def _to_review(self, ctx: DialogContext) -> Reply:
        ctx.state = REVIEW
        ctx.current = None
        targets = self._targets(ctx)
        lines = "\n".join(f"{i}. {QUESTIONS[k].title}: {self._value_line(ctx, k)}" for i, k in enumerate(targets, 1))
        notes = self.consistency_notes(ctx)
        text = "Проверьте собранные данные:\n" + lines + f"\nДата заявления: {date.today():%d.%m.%Y} (подставится автоматически)"
        if notes:
            text += "\n\nОбратите внимание:\n" + "\n".join(f"— {n}" for n in notes)
        text += ("\n\nВсё верно? Напишите «верно», и я подготовлю заявление. Чтобы изменить ответ — «исправить 3» или «исправить курс». "
                 "Пока вы не подтвердили, документ не создаётся и ничего никуда не отправляется.")
        return Reply(text, ctx.state, "review", options=["Верно", "Исправить"], events=[("screening_completed", ctx.scenario)])

    def _review(self, ctx: DialogContext, text: str, cmd: Optional[tuple]) -> Reply:
        if cmd:
            name, arg = cmd
            if name != "fix":
                ctx.pick_fix = False
            if name in ("confirm", "yes"):
                return self.confirm(ctx)
            if name == "resume":
                return self._resume_prompt(ctx, "Продолжаем с того же места.")
            if name == "cancel":
                return self._cancel(ctx)
            if name == "restart":
                return self._restart(ctx)
            if name == "fix":
                return self._fix(ctx, arg)
            if name == "no":
                return self._fix(ctx, "")
            if name == "back":
                return self._fix(ctx, "")
        if ctx.pick_fix:
            return self._resolve_fix(ctx, text)
        r = self._to_review(ctx)
        r.text = "Напишите «верно», чтобы подтвердить, или «исправить <поле>».\n\n" + r.text
        r.events = []
        return r

    def confirm(self, ctx: DialogContext) -> Reply:
        if ctx.state != REVIEW:
            return self._info(ctx, "Подтверждать пока нечего: сначала нужно ответить на вопросы анкеты.")
        ctx.state = DOCUMENT_GENERATION
        return Reply("Данные подтверждены. Выберите шаблон и нажмите «Сформировать документ». Готовый файл вы скачаете сами; "
                     "в деканат он автоматически не отправляется.", ctx.state, "generation", options=["Сформировать документ"], events=[("review_confirmed", ctx.scenario)])

    # ====================================================================== document_generation / completed
    def _generation(self, ctx: DialogContext, text: str, cmd: Optional[tuple]) -> Reply:
        if cmd:
            name, arg = cmd
            if name != "fix":
                ctx.pick_fix = False
            if name in ("generate", "confirm", "yes", "start"):
                return Reply("", ctx.state, "generate")
            if name == "resume":
                return self._resume_prompt(ctx, "Продолжаем с того же места.")
            if name == "cancel":
                return self._cancel(ctx)
            if name == "restart":
                return self._restart(ctx)
            if name in ("back", "fix", "no"):
                ctx.state = REVIEW
                return self._fix(ctx, arg) if name == "fix" and arg else self._to_review(ctx)
        return self._info(ctx, "Нажмите «Сформировать документ» (или напишите «сформировать»). Если нужно что-то изменить — напишите «назад».", "generation")

    def generation_done(self, ctx: DialogContext, doc_id: str, unfilled: Optional[list] = None) -> Reply:
        ctx.generated.append(doc_id)
        ctx.state = COMPLETED
        text = "Документ готов — скачайте его и проверьте перед использованием."
        if unfilled:
            text += f" Не заполнены (данных нет): {', '.join(unfilled)} — впишите вручную."
        text += (" Если хотите, можно отправить обращение в деканат: перед отправкой я покажу, какие данные уйдут, и спрошу подтверждение. "
                 "Окончательное решение принимает сотрудник деканата.")
        return Reply(text, ctx.state, "done", events=[("document_generated", ctx.scenario)])

    def generation_failed(self, ctx: DialogContext, message: str) -> Reply:
        return Reply(message, ctx.state, "error", events=[("generation_failed", "")])

    # ====================================================================== данные для шаблона и проверки
    @staticmethod
    def template_data(ctx: DialogContext, today: Optional[date] = None) -> dict:
        d = ctx.data
        if "has_debts" in d:
            debts = "отсутствуют" if d["has_debts"] is False else ("имеются" + (f" ({d['debts_details']})" if d.get("debts_details") else ""))
        else:
            debts = None
        return {
            "student_name": d.get("student_name"), "passport_data": d.get("passport_data"), "current_university": d.get("current_university"),
            "current_course": d.get("current_course"), "current_specialty": d.get("current_specialty"), "target_specialty": d.get("target_specialty"),
            "education_form": d.get("education_form"), "debts": debts, "application_date": (today or date.today()).strftime("%d.%m.%Y"),
        }

    @staticmethod
    def consistency_notes(ctx: DialogContext) -> list:
        """Противоречия между ответами и между ответами и загруженным документом (только данные текущего вуза; целевые не смешиваются)."""
        d, s, notes = ctx.data, ctx.suggestions, []
        if ctx.scenario == TRANSFER and d.get("target_specialty") and d.get("current_specialty") and norm(d["target_specialty"]) == norm(d["current_specialty"]):
            notes.append("Целевое направление совпадает с текущим — проверьте, что вы указали именно то, куда хотите перевестись.")
        if s.get("student_name") and d.get("student_name") and fuzz.token_set_ratio(norm(s["student_name"]), norm(d["student_name"])) < 70:
            notes.append(f"ФИО в анкете отличается от ФИО в загруженном документе («{s['student_name']}»).")
        if s.get("current_university") and d.get("current_university") and fuzz.token_set_ratio(norm(s["current_university"]), norm(d["current_university"])) < 50:
            notes.append(f"Вуз в анкете отличается от вуза в загруженном документе («{s['current_university']}»).")
        if s.get("education_form") and d.get("education_form") and norm(s["education_form"]) != norm(d["education_form"]):
            notes.append(f"Форма обучения в анкете отличается от указанной в загруженном документе («{s['education_form']}»).")
        return notes
