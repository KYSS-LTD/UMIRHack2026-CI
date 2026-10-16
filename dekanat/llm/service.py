"""LLMService — единственная точка входа к языковой модели во всём приложении.

* `USE_LOCAL_LLM=False` (по умолчанию): `enabled` ложно, backend не создаётся, сеть не трогается, каждая задача сразу возвращает None.
* `USE_LOCAL_LLM=True`: разрешены только пять задач из ТЗ (арбитр по Положению, намерение, нормализация ответа анкеты,
  извлечение полей из OCR-текста, пересказ уже проверенного ответа). Каждый ответ проверяется в schemas.py.
* Любая ошибка (модель не запущена, тайм-аут, невалидный JSON) -> None; вызывающий код продолжает на правилах Python.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence, TypeVar

from . import prompts
from .backend import CircuitBreaker, LLMBackend, LLMConfig, LLMError, LLMUnavailable, make_backend
from .schemas import (
    ExtractedField,
    IntentResult,
    Judgement,
    NormalizedAnswer,
    SchemaError,
    ground_extraction,
    is_label_or_mark,
    label_keys,
    parse_json_object,
    validate_explanation,
    validate_extraction,
    validate_intent,
    validate_judgement,
    validate_normalized,
)

log = logging.getLogger(__name__)
T = TypeVar("T")

TASKS = ("judge_clauses", "detect_intent", "normalize_answer", "extract_fields", "explain_answer")


@dataclass
class LLMStats:
    calls: int = 0        # запросов к модели
    ok: int = 0           # принятых (валидных) ответов
    invalid: int = 0      # ответов, отвергнутых проверкой
    errors: int = 0       # ошибок связи/инференса
    skipped: int = 0      # задач, пропущенных из-за недоступности модели

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class LLMService:
    def __init__(self, cfg: LLMConfig, backend: Optional[LLMBackend] = None, backend_factory: Optional[Callable[[], Optional[LLMBackend]]] = None):
        self.cfg = cfg
        self._backend = backend
        self._factory = backend_factory
        self._breaker = CircuitBreaker(2, cfg.cooldown_seconds)
        self._lock = threading.Lock()
        self._cpu = threading.Semaphore(1)  # один запрос к модели за раз: CPU ноутбука не резиновый
        self._avail: tuple[float, bool] = (0.0, False)
        self.stats = LLMStats()
        self.task_counts: dict[str, int] = {}

    # ------------------------------------------------------------------ фабрики
    @classmethod
    def disabled(cls) -> "LLMService":
        return cls(LLMConfig(enabled=False))

    @classmethod
    def from_settings(cls, settings) -> "LLMService":
        cfg = LLMConfig.from_settings(settings)
        if not cfg.enabled:
            return cls(cfg)  # backend не создаётся вообще
        return cls(cfg, backend_factory=lambda: make_backend(cfg))

    # ------------------------------------------------------------------ состояние
    @property
    def enabled(self) -> bool:
        """Главный переключатель (USE_LOCAL_LLM). Ложь — ни одного обращения к модели."""
        return self.cfg.enabled

    def _get_backend(self) -> Optional[LLMBackend]:
        if self._backend is None and self._factory is not None:
            with self._lock:
                if self._backend is None:
                    self._backend = self._factory()
        return self._backend

    def available(self) -> bool:
        """Модель включена, не «остывает» после ошибок и отвечает. Проверка кешируется на короткое время."""
        if not self.enabled or not self._breaker.allow():
            return False
        now = time.monotonic()
        ts, val = self._avail
        if now - ts < (30 if val else 10):
            return val
        backend = self._get_backend()
        val = bool(backend and backend.available())
        self._avail = (now, val)
        return val

    def status(self) -> dict:
        if not self.enabled:
            return {"backend": "none", "enabled": False, "use_local_llm": False}
        return {"backend": self.cfg.backend, "model": self.cfg.model, "enabled": True, "use_local_llm": True, "available": self.available(),
                "cpu_only": self.cfg.cpu_only, "stats": self.stats.as_dict()}

    # ------------------------------------------------------------------ ядро
    def _ask(self, task: str, prompt: str, validate: Callable[[dict], T], max_tokens: Optional[int] = None) -> Optional[T]:
        if not self.enabled:
            return None  # режим без LLM: никаких скрытых вызовов
        if not self.available():
            self.stats.skipped += 1
            return None
        backend = self._get_backend()
        if backend is None:
            self.stats.skipped += 1
            return None
        if not self._cpu.acquire(timeout=self.cfg.timeout):
            self.stats.skipped += 1
            return None
        try:
            current = prompt
            for attempt in range(1 + self.cfg.max_retries):
                self.stats.calls += 1
                self.task_counts[task] = self.task_counts.get(task, 0) + 1
                started = time.monotonic()
                try:
                    raw = backend.complete(prompts.SYSTEM_BASE, current, max_tokens=max_tokens or self.cfg.max_output_tokens, json_mode=True)
                except LLMUnavailable as exc:
                    self._breaker.fail()
                    self._avail = (0.0, False)
                    self.stats.errors += 1
                    log.warning("LLM [%s]: модель недоступна: %s", task, exc)
                    return None
                except LLMError as exc:
                    self.stats.errors += 1
                    log.warning("LLM [%s]: ошибка инференса: %s", task, exc)
                    current = prompt + prompts.retry_suffix(str(exc))
                    continue
                self._breaker.ok()
                try:
                    result = validate(parse_json_object(raw))
                except SchemaError as exc:
                    self.stats.invalid += 1
                    log.info("LLM [%s]: ответ отклонён проверкой (%s), попытка %d", task, exc, attempt + 1)
                    current = prompt + prompts.retry_suffix(str(exc))
                    continue
                self.stats.ok += 1
                log.info("LLM [%s]: принят за %.1f с", task, time.monotonic() - started)
                return result
            return None  # попытки исчерпаны -> безопасный fallback у вызывающего
        finally:
            self._cpu.release()

    # ------------------------------------------------------------------ задача 1: арбитр по Положению
    def judge_clauses(self, clauses: Sequence[Any], question: str) -> Optional[Judgement]:
        """clauses: объекты с .number и .text. Решает только «есть ли прямой ответ в этих пунктах и в каком»."""
        if not self.enabled or not clauses:
            return None
        pairs = [(c.number, c.text) for c in clauses]
        prompt = prompts.judge_prompt(question, pairs, self.cfg.max_input_chars)
        numbers = [n for n, _ in pairs]
        return self._ask("judge_clauses", prompt, lambda d: validate_judgement(d, numbers), max_tokens=80)

    # ------------------------------------------------------------------ задача 2: намерение
    def detect_intent(self, message: str, state: str = "idle") -> Optional[IntentResult]:
        if not self.enabled or not message.strip():
            return None
        return self._ask("detect_intent", prompts.intent_prompt(message, state), validate_intent, max_tokens=60)

    # ------------------------------------------------------------------ задача 3: нормализация ответа анкеты
    def normalize_answer(self, question_text: str, message: str, kind: str, choices: Sequence[str] = (), value_hint: str = "") -> Optional[NormalizedAnswer]:
        if not self.enabled or not message.strip():
            return None
        prompt = prompts.normalize_prompt(question_text, message, kind, choices, value_hint)
        return self._ask("normalize_answer", prompt, lambda d: validate_normalized(d, kind, choices), max_tokens=150)

    # ------------------------------------------------------------------ задача 4: извлечение полей из OCR-текста
    def extract_fields(self, ocr_text: str, doc_title: str, fields: Sequence[tuple[str, str]], labels: Iterable[str] = ()) -> dict[str, ExtractedField]:
        """fields: [(id, название)] — только те, что правила не нашли. Возвращает лишь подтверждённые текстом поля.
        labels: подписи полей документов («Форма обучения», «Дата окончания практики»...): ответ, который оказался такой подписью,
        строкой «подпись: значение» или пометкой вроде «М.П.», отбрасывается — поле остаётся «не найдено»."""
        if not self.enabled or not fields or not ocr_text.strip():
            return {}
        ids = [fid for fid, _ in fields]
        prompt = prompts.extract_prompt(ocr_text, doc_title, fields, self.cfg.max_input_chars)
        got = self._ask("extract_fields", prompt, lambda d: validate_extraction(d, ids), max_tokens=self.cfg.max_output_tokens + 200)
        if not got:
            return {}
        keys = label_keys(labels)
        accepted: dict[str, ExtractedField] = {}
        for fid, item in got.items():
            if item.value and is_label_or_mark(item.value, keys):
                log.info("LLM [extract_fields]: ответ для поля %s отклонён — это подпись или пометка документа, а не значение", fid)
            elif ground_extraction(item, ocr_text):
                accepted[fid] = item
        return accepted

    # ------------------------------------------------------------------ задача 5: пересказ проверенного ответа
    def explain_answer(self, question: str, verdict: str, answer_text: str, facts: Sequence[str], clauses: Sequence[tuple[str, str]]) -> Optional[str]:
        if not self.enabled:
            return None
        prompt = prompts.explain_prompt(question, verdict, answer_text, facts, clauses, self.cfg.max_input_chars)
        grounding = " ".join([answer_text, question, *facts, *[f"{n} {t}" for n, t in clauses]])
        numbers = [n for n, _ in clauses]
        return self._ask("explain_answer", prompt, lambda d: validate_explanation(d, numbers, grounding), max_tokens=300)
