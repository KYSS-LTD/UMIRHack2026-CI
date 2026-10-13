"""Ответ на вопрос по карточке документа и Положению.

Три исхода (как в ТЗ):
  ok         — документ в порядке / ответ найден (со ссылкой на пункт Положения или поле документа);
  missing    — чего-то не хватает (называем что и по какому пункту это обязательно);
  no_answer  — ответа нет ни в документе, ни в Положении (честно говорим об этом).

Ответ строится только из найденных фактов (поля карточки, тексты пунктов): ничего не выдумывается.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from ..config import DocTypeSpec, FieldSpec, PartSpec
from ..extract.labels import label_stems
from ..kb.index import ClauseIndex
from ..kb.requirements import RequirementModel, Requirement, cue_level, unit_id
from ..models import NOT_FOUND, Answer, Box, Clause, FieldResult, Source
from ..textutil import STOPWORDS, normalize, sentences, stem, stem_eq, tokenize
from ..llm import LLMService

log = logging.getLogger(__name__)

_COMPLETE_STRONG = re.compile(
    r"хват|достаточ|подойд|подход|годит|годен|пригод|в порядке|недостает|недостаёт|чего не|что не так|ошибк|ошибок|"
    r"правильно ли|верно ли|корректн|соответству|полност|всё ли|все ли|чего нет|не хватает|"
    r"что (?:мне |нам )?(?:нужно|надо) (?:добавить|исправить|дописать|доделать|поменять|поправить|изменить)|"
    r"(?:верно|правильно|корректно) (?:оформлен|заполнен|составлен)|(?:оформлен|заполнен|составлен)\w*.{0,30}\b(?:верно|правильно|корректно)\b|"
    r"(?:оформил|заполнил|составил|написал|сделал)\w*.{0,30}\b(?:верно|правильно|корректно)\b|\b(?:верно|правильно|корректно)\b.{0,30}(?:оформил|заполнил|составил|написал|сделал)|"
    r"чего[- ]?(?:то|нибудь).{0,30}\bнет\b|как надо|как положено|без ошибок|полн(?:ая|ый|ое|а|о)\b|нормальн|в норме|"
    r"отказ|откаж|вернут(?![ьс])|вернёт|вернет|не примут|не примет|завернут|забракуют|на доработку|"
    r"проверь|проверит|устро(?:ит|ят)|пройд[её]т|засчита|зачт[её]т|зачтут|\bок\b"
)
_COMPLETE_WEAK = re.compile(r"принима|примет|примут|допуст|пропустят|одобр|можно ли|могу ли|получится|смогу")
# Частые темы вне Положения: ответ «нет информации о …» называет тему, как в образце из ТЗ («…о стоимости обучения»)
_NO_ANSWER_TOPICS = [
    (re.compile(r"скидк"), "о скидках"),
    (re.compile(r"^(?=.*(?:стоимост|\bстоит\b|\bцен[аыу]\b|оплат|платн))(?=.*(?:обучени|учеб|учит|семестр|контракт))"), "о стоимости обучения"),
    (re.compile(r"стипенди"), "о стипендии"),
    (re.compile(r"общежити"), "об общежитии"),
    (re.compile(r"\bсесси"), "о сессии"),
    (re.compile(r"проходн\w*\s+балл|\bегэ\b"), "о проходных баллах"),
    (re.compile(r"расписани"), "о расписании"),
    (re.compile(r"столов"), "о столовой"),
]
# «Можно ли подать заявление онлайн?», «Как отправить справку?» — вопрос о порядке подачи, а не проверка загруженного документа
_PROCEDURE = re.compile(r"\b(?:можно ли|могу ли|как|куда|где)\b(?:\s+\w+)?\s+(?:подать|подавать|отправить|прислать|сдать|принести|передать|получить|оформить)\b")
_DOC_REF = re.compile(
    r"\b(?:эт\w+|так(?:ая|ой|ую|ие|им|ого|ому|ом)|мо(?:я|й|ё|е|ю|и|его|ей|им|ему|ем)|данн\w+|приложенн\w+|присланн\w+|загруженн\w+|"
    r"прикреплённ\w+|прикрепленн\w+|наш\w+|свою|своё|моей|ней|нее|неё|ним|него)\b"
)
# существительные в единственном числе, обозначающие загруженный документ («Заявление принимается?», «Справка подойдёт?»)
_DOC_SINGULAR = re.compile(r"\b(?:справка|справку|заявление|документ)\b")
# уточняющие прилагательные («максимальный срок»): если их нет в пункте, вопрос не становится «вне темы»
_QUALIFIERS = frozenset(stem(w) for w in "максимальный минимальный предельный допустимый возможный общий итоговый окончательный точный полный конкретный установленный обычный стандартный".split())
# какие виды полей подходят под вопросительное слово («сколько» не спрашивают про дату)
_WH_KINDS = {
    "кто": {"person", "text"}, "кого": {"person", "text"}, "кому": {"person", "text"}, "чей": {"person", "text"}, "чья": {"person", "text"},
    "когда": {"date", "period", "composite"}, "сколько": {"course", "number", "text", "grade", "composite"},
    "где": {"org", "text"}, "куда": {"org", "text"},
}
_GENERIC_STEMS = frozenset(stem(w) for w in "студент студентка студента студенты обучающийся справка справки документ документы заявление заявления".split())
_WH = frozenset("кто кого кому какой какая какое какие каких какого какую какому каком какими когда сколько где куда чей чья чьё".split())
_ASK_VERB = re.compile(r"\b(?:укажи\w*|назови\w*|покажи\w*|скажи\w*)\b|как зовут")
_DOC_WORDS = frozenset("справка справки справку справке справкой документ документа документы документе заявление заявления заявлению заявлении заявлением бумага бумаги фото снимок скан".split())
_CUE_WORDS = frozenset(
    "хватит хватает достаточно достаточна достаточен подойдет подойдёт подходит годится годен пригодна пригоден порядке примет примут принимается принимают принять ошибки ошибок ошибка верно правильно оформлена оформлено оформлен заполнено заполнена нужно можно".split()
)
# глаголы и слова-«оболочки» фактических вопросов («в каком вузе УЧИТСЯ студент», «какая оценка УКАЗАНА»)
_NEUTRAL_RAW = "указан указана указано указаны написан написана написано значится проходил проходила проходит учится учился училась учиться обучается находится называется зовут есть стоит_нет"
_NEUTRAL = frozenset(stem(w) for w in _NEUTRAL_RAW.split() if w != "стоит_нет")


def join_ru(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " и " + items[-1]


@dataclass
class UnitView:
    uid: str
    field: FieldSpec
    part: Optional[PartSpec]
    present: bool
    value: str
    result: FieldResult
    confidence: float
    boxes: list[Box]

    @property
    def phrase(self) -> str:
        """Название единицы в родительном падеже («даты выдачи») для фразы «Не хватает ...»."""
        if self.part and self.part.missing_phrase:
            return self.part.missing_phrase
        if self.part is None and self.field.missing_phrase:
            return self.field.missing_phrase
        return f"поля «{(self.part.title if self.part else self.field.title)}»"

    @property
    def title(self) -> str:
        return self.part.title if self.part else self.field.title


def build_units(dt: DocTypeSpec, fields: list[FieldResult]) -> dict[str, UnitView]:
    by_id = {f.id: f for f in fields}
    out: dict[str, UnitView] = {}
    for f, p in dt.iter_units():
        res = by_id.get(f.id)
        if res is None:
            continue
        if p is not None:
            pr = next((x for x in res.parts if x.id == p.id), None)
            if pr is None:
                continue
            out[unit_id(f, p)] = UnitView(unit_id(f, p), f, p, pr.found, pr.value, res, pr.confidence, pr.boxes)
        else:
            out[unit_id(f, p)] = UnitView(unit_id(f, p), f, None, res.found, res.value, res, res.confidence, res.boxes)
    return out


class QAEngine:
    _NOT_PLACE = frozenset("находится находятся находился расположен расположена деканат общежитие библиотека".split())

    def __init__(self, doc_types: dict[str, DocTypeSpec], clauses: list[Clause], index: ClauseIndex, requirements: RequirementModel,
                 min_coverage: float = 0.55, max_related: int = 2, llm: Optional[LLMService] = None, field_groups: Optional[list[set[str]]] = None):
        self.doc_types = doc_types
        self.clauses = clauses
        self.index = index
        self.req = requirements
        self.min_coverage = min_coverage
        self.max_related = max_related
        self.llm = llm
        # словарь «документной» лексики: всё, что может быть названием поля/типа документа
        vocab: set[str] = set(stem(w) for w in _DOC_WORDS)
        for dt in doc_types.values():
            for alias in dt.aliases:
                vocab.update(label_stems(alias))
            for f, p in dt.iter_units():
                for ph in [f.title, *f.labels, *f.mention, *(p.labels + p.mention + [p.title] if p else [])]:
                    vocab.update(label_stems(ph))
        self.doc_vocab = vocab
        self._fsyn: dict[str, set[str]] = {}
        for g in field_groups or []:
            for w in g:
                self._fsyn.setdefault(w, set()).update(g)

    # ------------------------------------------------------------------ вспомогательное
    def _clause_source(self, clause: Clause) -> Source:
        return Source("clause", clause.number, clause.label, clause.text)

    @staticmethod
    def _fact_row(u: UnitView) -> dict:
        """Строка «факт из документа» для структурированного ответа: значение, найдено ли, насколько уверенно прочитано."""
        return {"id": u.uid, "title": u.title[:1].upper() + u.title[1:], "value": u.value if u.present else NOT_FOUND, "found": bool(u.present),
                "confidence": round(u.confidence, 2), "doubtful": bool(u.present and u.field.kind != "marks" and u.confidence < 0.5)}

    def _field_source(self, u: UnitView) -> Source:
        return Source("field", u.uid, f"поле «{u.field.title}»" + (f" ({u.part.title})" if u.part else ""), u.value, u.boxes)

    def _no_answer(self, question: str, why: str = "") -> Answer:
        q = question.strip().rstrip("?").strip()
        topic = next((about for rx, about in _NO_ANSWER_TOPICS if rx.search(normalize(q))), None)
        # как в образце из ТЗ: «нет информации о стоимости обучения»; для прочих вопросов — сам вопрос
        text = f"В документе и в Положении нет информации {topic}. Обратитесь в деканат." if topic else \
            f"В документе и в Положении нет информации по вопросу «{q}?». Обратитесь в деканат."
        return Answer("no_answer", text, "no_answer", [], confidence=0.8, notes=[why] if why else [])

    def needs_document(self, question: str) -> bool:
        """Вопрос о самом документе (достаточно ли, хватает ли) — без типа документа на него не ответить."""
        t = normalize(question or "")
        if _COMPLETE_STRONG.search(t):
            return True
        if _PROCEDURE.search(t) and not _DOC_REF.search(t):
            return False
        return bool(_COMPLETE_WEAK.search(t) and (_DOC_REF.search(t) or _DOC_SINGULAR.search(t)))

    # ------------------------------------------------------------------ вопрос о значении поля: что именно спрашивают
    @staticmethod
    def _modifier_stems(question: str) -> frozenset:
        """Прилагательные-уточнения («максимальный срок»): их отсутствие в пункте не делает вопрос «вне темы»."""
        return frozenset(stem(w) for w in tokenize(question, drop_stop=True) if stem(w) in _QUALIFIERS)

    def _unexplained(self, question: str) -> list[str]:
        """Слова вопроса, которых нет ни в лексике документов, ни в Положении (признак вопроса «вне темы»)."""
        out = []
        mods = self._modifier_stems(question)
        for q in self.index.query_terms(question, extra_drop=_DOC_WORDS | _CUE_WORDS):
            if q.isdigit() or q in _NEUTRAL or q in mods:
                continue
            if any(v in self.index.vocab for v in self.index.variants(q)):
                continue
            if any(stem_eq(q, d) for d in self.doc_vocab):
                continue
            out.append(q)
        return out

    def _fact_target(self, question: str, dt: DocTypeSpec, units: dict[str, UnitView]) -> Optional[tuple[str, bool, int, str, float]]:
        """Какое поле документа спрашивают: -> (id единицы, совпал ли заголовок поля, число слов метки, вид поля, доля слов вопроса под названием поля)."""
        raw = tokenize(question)
        if not raw:
            return None
        wh_pos = next((i for i, w in enumerate(raw) if w in _WH), None)
        if wh_pos is None and _ASK_VERB.search(normalize(question)):
            wh_pos = 0
        wh_word = raw[wh_pos] if wh_pos is not None and wh_pos < len(raw) and raw[wh_pos] in _WH else None
        # «с какой даты», «до какой даты»: вопросительное слово «какой» — не «когда», но смысл тот же
        accepted_kinds = _WH_KINDS.get(wh_word) if wh_word else None
        tail = raw[(wh_pos + 1 if wh_pos is not None else 0): (wh_pos + 6 if wh_pos is not None else len(raw))]
        window = [stem(w) for w in tail if w not in STOPWORDS]
        qs = {stem(w) for w in raw if w not in STOPWORDS}

        def covers(q: str, p: str, multiword: bool) -> bool:
            # узкие синонимы полей («начинается» ~ «начало»); общие синонимы Положения тут вредны («дней» ~ «период»)
            return stem_eq(q, p) or p in self._fsyn.get(q, ())

        if wh_word in ("где", "куда") and not any(w in self._NOT_PLACE for w in raw):
            orgs = [unit_id(f, p) for f, p in dt.iter_units() if (p.kind if p else f.kind) == "org" and unit_id(f, p) in units]
            if len(orgs) == 1:
                return orgs[0], True, 2, "org", 1.0
        if re.search(r"как зовут|фамили|имя\b", normalize(question)):
            for f, p in dt.iter_units():
                if f.kind == "person" and unit_id(f, p) in units and f.title.lower().startswith("фио"):
                    return unit_id(f, p), True, 2, f.kind, 1.0
        content = {stem(w) for w in raw if w not in STOPWORDS and w not in _WH and stem(w) not in _NEUTRAL and stem(w) not in _GENERIC_STEMS
                   and stem(w) not in _QUALIFIERS and not w.isdigit() and w not in _CUE_WORDS}
        best: Optional[tuple[tuple, str, bool, int, str, float]] = None
        for f, p in dt.iter_units():
            uid = unit_id(f, p)
            if uid not in units:
                continue
            titles = [f.title] + ([p.title] if p else [])
            phrases = [*titles, *f.labels, *f.mention] + ([*p.labels, *p.mention] if p else [])
            unit_kind = p.kind if p else f.kind
            if accepted_kinds is not None and unit_kind not in accepted_kinds:
                continue
            for ph in phrases:
                ls = label_stems(ph)
                if not ls:
                    continue
                multi = len(ls) > 1
                if not multi and ls[0] in _GENERIC_STEMS:
                    continue  # «студент», «справка» — слишком общие слова, чтобы считать их названием поля
                hit = [l for l in ls if any(covers(q, l, multi) for q in qs)]
                frac = len(hit) / len(ls)
                if not multi:
                    if not hit or not any(covers(q, ls[0], False) for q in window):
                        continue  # одиночное слово («вуз», «срок») — поле, только если на него указывает вопросительное слово
                elif frac < 0.66:
                    continue
                pos = min((i for i, q in enumerate(window) if any(covers(q, l, multi) for l in hit)), default=99)
                is_title = ph in titles
                strength = (3.0 if frac >= 0.99 else 2.0) + (0.5 if is_title else 0) if multi else (1.5 if is_title else 1.0)
                key = (int(pos < 99), strength, -pos)
                if best is None or key > best[0]:
                    cover = (sum(1 for c in content if any(covers(c, l, multi) for l in ls)) / len(content)) if content else 1.0
                    best = (key, uid, is_title, len(ls), f.kind, cover)
        return (best[1], best[2], best[3], best[4], best[5]) if best else None

    # ------------------------------------------------------------------ намерение
    def classify_intent(self, question: str, dt: Optional[DocTypeSpec], units: dict[str, UnitView]) -> tuple[str, Optional[str]]:
        t = normalize(question)
        if dt is None or not units:
            return "rule", None
        if _COMPLETE_STRONG.search(t):
            return "completeness", None
        if _COMPLETE_WEAK.search(t) and (_DOC_REF.search(t) or _DOC_SINGULAR.search(t)):
            return "completeness", None
        has_wh = any(w in _WH for w in tokenize(question)) or bool(_ASK_VERB.search(t))
        target = None if re.search(r"может быть|могут быть|должен быть|должна быть|должны быть|можно быть", t) else self._fact_target(question, dt, units)
        if target and (has_wh or len(tokenize(question, drop_stop=True)) <= 4):
            uid, is_title, n_stems, kind, label_cover = target
            if not self._unexplained(question):
                # совпало не название поля, а его синоним («срок», «основание»), вопрос не говорит о документе, а Положение
                # его хорошо покрывает — это вопрос о правилах («В какой срок принимается решение о переводе?»)
                doc_context = bool(_DOC_REF.search(t)) or "указан" in t or "написан" in t or bool(_DOC_SINGULAR.search(t) and n_stems > 1)
                if not is_title and not doc_context and (n_stems == 1 or kind == "text" or label_cover < 0.6):
                    mods = self._modifier_stems(question)
                    hits = self.index.search(question, 1, self.index.query_terms(question), mods)
                    if hits and hits[0].coverage >= 0.85:
                        return "rule", None
                return "fact", uid
        return "rule", None

    # ------------------------------------------------------------------ главный вход
    def answer(self, question: str, dt: Optional[DocTypeSpec], fields: Optional[list[FieldResult]], ocr_notes: Optional[list[str]] = None) -> Answer:
        question = (question or "").strip()
        units = build_units(dt, fields) if (dt and fields) else {}
        if not question:
            if dt and units:
                question = "Всё ли в порядке с документом?"
            else:
                return Answer("no_answer", "Задайте вопрос по документу или по Положению.", "empty", confidence=1.0)
        intent, uid = self.classify_intent(question, dt, units)
        if intent == "completeness" and dt and units:
            ans = self._completeness(question, dt, units)
        elif intent == "fact" and dt and units and uid:
            ans = self._fact(question, dt, units, uid)
        else:
            ans = self._rule(question)
        ans.intent = intent if intent != "rule" or ans.verdict != "no_answer" else "rule"
        if dt and units:
            ans.notes += self._uncertain_notes(units)
            ans.notes += self._consistency_notes(dt, units)
        if ocr_notes:
            ans.notes += ocr_notes
        return ans

    # ------------------------------------------------------------------ проверка комплектности
    def _completeness(self, question: str, dt: DocTypeSpec, units: dict[str, UnitView]) -> Answer:
        mismatch = self._purpose_mismatch(question, dt)
        if mismatch:
            return mismatch
        req = self.req.for_type(dt.id)
        fallback = not req.found_any
        required: dict[str, Optional[Requirement]] = {}
        if not fallback:
            for uid in units:
                best = req.best(uid)
                if best:
                    required[uid] = best
        else:
            for ref in dt.required_fallback:
                for uid in units:
                    if uid == ref or uid.startswith(ref + "."):
                        required[uid] = None
        missing = [uid for uid in required if not units[uid].present]
        related = self._related(question, exclude={r.clause.number for r in required.values() if r})

        def finish(ans: Answer) -> Answer:
            ans.facts = [self._fact_row(units[u]) for u in required]  # факты, относящиеся к вопросу: обязательные поля и их значения
            return ans

        if missing:
            groups: dict[str, list[str]] = {}
            clause_by_num: dict[str, Clause] = {}
            cue_by_num: dict[str, int] = {}
            for uid in missing:
                r = required[uid]
                key = r.clause.number if r else ""
                groups.setdefault(key, []).append(uid)
                if r:
                    clause_by_num[key] = r.clause
                    cue_by_num[key] = r.cue
            phrases = [units[u].phrase for u in missing]
            if fallback:
                text = f"Не хватает {join_ru(phrases)}. В Положении нет явного перечня требований к этому документу, поэтому поля определены настройками типа документа."
                sources = [self._field_source(units[u]) for u in missing]
            elif len(groups) == 1:
                num = next(iter(groups))
                cl = clause_by_num[num]
                obligatory = cue_by_num[num] >= 3
                many = len(missing) > 1
                tail = ("Эти поля обязательны по" if many else "Это поле обязательно по") if obligatory else ("Эти поля требует" if many else "Это поле требует")
                text = f"Не хватает {join_ru(phrases)}. {tail} {cl.cite}."
                sources = [self._clause_source(cl)]
            else:
                parts, sources = [], []
                for num in sorted(groups, key=lambda n: [int(x) if x.isdigit() else 0 for x in n.split(".")]):
                    cl = clause_by_num[num]
                    parts.append(f"{join_ru([units[u].phrase for u in groups[num]])} — {cl.label}")
                    sources.append(self._clause_source(cl))
                text = f"Не хватает {join_ru(phrases)}. Основания: " + "; ".join(parts) + "."
            miss = [{"unit": u, "title": units[u].title, "phrase": units[u].phrase, "clause": (required[u].clause.number if required[u] else None)} for u in missing]
            return finish(Answer("missing", text, "completeness", sources, missing=miss, related=related, confidence=0.9 if not fallback else 0.6))
        # всё на месте
        if fallback:
            text = f"{dt.short_title} содержит все поля, предусмотренные для документа этого типа. В Положении нет явного перечня требований, окончательное решение примет сотрудник деканата."
            return finish(Answer("ok", text, "completeness", [], related=related, confidence=0.6))
        covered = {num: len(us & set(required)) for num, us in req.clause_units.items()}
        ranked = sorted(covered, key=lambda n: (-covered[n], -max((r.cue for r in required.values() if r and r.clause.number == n), default=0), n))
        primary = self.index.get(ranked[0]) if ranked else None
        sources = [self._clause_source(primary)] if primary else []
        for num in ranked[1:3]:
            c = self.index.get(num)
            if c and covered[num] >= 1:
                sources.append(self._clause_source(c))
        text = f"{dt.short_title} содержит все данные, которые требует {primary.cite if primary else 'Положение'}. Окончательное решение примет сотрудник деканата."
        return finish(Answer("ok", text, "completeness", sources, related=related, confidence=0.9))

    def _purpose_mismatch(self, question: str, dt: DocTypeSpec) -> Optional[Answer]:
        """«Подойдёт ли справка о практике для перевода?» — а Положение для перевода называет справку об обучении."""
        m = re.search(r"\b(?:для|при)\s+(?:оформления\s+|получения\s+|целей\s+)?([а-яё]{4,})", normalize(question))
        if not m:
            return None
        key = stem(m.group(1))
        df = self.index.df_of(key)
        if not (2 <= df <= 0.5 * self.index.n):
            return None
        variants = self.index.variants(key)
        named: dict[str, list[Clause]] = {}
        for i, d in enumerate(self.index.docs):
            if variants & set(d):
                for tid in self.doc_types:
                    if self.req._mentions_type(tid, i):
                        named.setdefault(tid, []).append(self.clauses[i])
        if not named or dt.id in named or sum(len(v) for v in named.values()) < 2:
            return None
        others = [self.doc_types[tid] for tid in named]
        refs = [max(cl, key=lambda c: (cue_level(c.text), -self.clauses.index(c))) for cl in named.values()]
        text = (f"Для этой цели Положение называет другой документ: {join_ru([o.title.lower() for o in others])} ({join_ru([c.label for c in refs])}). "
                f"Загруженный документ — {dt.title.lower()}.")
        return Answer("missing", text, "completeness", [self._clause_source(c) for c in refs],
                      missing=[{"unit": "document_type", "title": "Подходящий тип документа", "phrase": o.genitive, "clause": refs[0].number} for o in others],
                      confidence=0.7)

    def _related(self, question: str, exclude: set[str]) -> list[Source]:
        terms = self.index.query_terms(question, extra_drop=_DOC_WORDS | _CUE_WORDS)
        out: list[Source] = []
        for h in self.index.search(question, 4, terms):
            if h.clause.number in exclude or h.coverage < 0.5:
                continue
            out.append(self._clause_source(h.clause))
            if len(out) >= self.max_related:
                break
        return out

    # ------------------------------------------------------------------ вопрос о значении поля
    def _fact(self, question: str, dt: DocTypeSpec, units: dict[str, UnitView], uid: str) -> Answer:
        u = units[uid]
        res = u.result
        if u.present:
            if u.field.kind == "marks":
                text = f"{u.title[:1].upper()}{u.title[1:]}: есть."
            elif u.part is None:
                text = f"{u.field.title}: {u.value}."
            else:
                text = f"{u.part.title[:1].upper()}{u.part.title[1:]}: {u.value}."
            ans = Answer("ok", text, "fact", [self._field_source(u)], confidence=max(0.5, u.confidence))
            ans.facts = [self._fact_row(u)]
            return ans
        req = self.req.for_type(dt.id).best(uid)
        if req:
            text = f"В документе не найдено: {u.phrase}. Это обязательно по {req.clause.cite}."
            ans = Answer("missing", text, "fact", [self._clause_source(req.clause)],
                         missing=[{"unit": uid, "title": u.title, "phrase": u.phrase, "clause": req.clause.number}], confidence=0.8)
            ans.facts = [self._fact_row(u)]
            return ans
        text = f"В документе {u.phrase} не найдено, а в Положении нет требований к этому полю. Обратитесь в деканат."
        ans = Answer("no_answer", text, "fact", [], confidence=0.7)
        ans.facts = [self._fact_row(u)]
        return ans

    # ------------------------------------------------------------------ вопрос о правилах
    def _best_sentences(self, clause: Clause, qterms: list[str]) -> str:
        sents = sentences(clause.text)
        if len(sents) <= 2 and len(clause.text) <= 420:
            return clause.text
        scored = []
        for i, s in enumerate(sents):
            toks = {stem(t) for t in tokenize(s, drop_stop=True)}
            sc = sum(self.index.idf(q) for q in qterms if self.index.variants(q) & toks)
            scored.append((sc, i, s))
        top = sorted(scored, key=lambda x: -x[0])[:2]
        return " ".join(s for _, _, s in sorted(top, key=lambda x: x[1]))

    def _rule(self, question: str) -> Answer:
        qterms = self.index.query_terms(question)
        if not qterms:
            return self._no_answer(question, "в вопросе нет предметных слов")
        mods = self._modifier_stems(question)
        hits = self.index.search(question, 5, qterms, mods)
        if not hits:
            return self._no_answer(question)
        top = hits[0]
        rare = [m for m in top.unmatched if (self.index.idf(m) if self.index.df_of(m) else self.index.max_idf) >= self.index.rare_threshold()]
        answerable = top.coverage >= self.min_coverage and not (rare and top.coverage < 0.75)
        note = f"покрытие вопроса пунктом {top.clause.label}: {top.coverage:.2f}"
        if self.llm is not None and self.llm.enabled and 0.35 <= top.coverage < 0.9:
            try:
                judged = self.llm.judge_clauses([h.clause for h in hits[:3]], question)
            except Exception as exc:  # любая сбойная модель не должна ронять ответ — работаем на правилах
                log.warning("LLM недоступна: %s", exc)
                judged = None
            if judged is not None:
                answerable = judged.answerable
                if judged.clause_number:
                    top = next((h for h in hits if h.clause.number == judged.clause_number), top)
        if not answerable:
            ans = self._no_answer(question, note)
            ans.debug = {"top": top.clause.number, "coverage": round(top.coverage, 2), "unmatched": top.unmatched}
            return ans
        sentence = self._best_sentences(top.clause, qterms)
        text = f"{sentence} ({top.clause.cite})"
        related = [self._clause_source(h.clause) for h in hits[1:] if h.coverage >= max(0.55, top.coverage - 0.15) and h.score >= 0.6 * top.score][: self.max_related]
        return Answer("ok", text, "rule", [self._clause_source(top.clause)], related=related, confidence=min(0.95, 0.5 + top.coverage / 2),
                      debug={"coverage": round(top.coverage, 2), "score": round(top.score, 2)})

    # ------------------------------------------------------------------ заметки
    @staticmethod
    def _uncertain_notes(units: dict[str, UnitView]) -> list[str]:
        weak = [u.title for u in units.values() if u.present and u.field.kind != "marks" and u.confidence < 0.5]
        if weak:
            return [f"Часть значений прочитана неуверенно ({join_ru(weak)}) — стоит проверить по снимку."]
        return []

    @staticmethod
    def _consistency_notes(dt: DocTypeSpec, units: dict[str, UnitView]) -> list[str]:
        """Проверки из конфига типа документа (сейчас: порядок дат)."""
        notes = []
        for chk in dt.checks:
            first, second = units.get(chk["before"]), units.get(chk["after"])
            if not (first and second and first.present and second.present):
                continue
            a, b = first.result.normalized, second.result.normalized
            if isinstance(a, str) and isinstance(b, str) and b < a:
                notes.append(chk.get("message") or f"Проверьте даты: «{second.title}» раньше, чем «{first.title}».")
        return notes
