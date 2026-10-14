"""Вопросы анкеты и их разбор правилами Python (режим USE_LOCAL_LLM=False). Модель, если включена, лишь помогает разобрать неоднозначный ответ,
а результат всё равно проходит эти же проверки.

Ответ «нет» не универсален: его смысл зависит от вопроса («нет задолженностей» — ответ; «нет» вместо ФИО — ошибка).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

OK, INVALID, AMBIGUOUS = "ok", "invalid", "ambiguous"

EDUCATION_FORMS = ("очная", "заочная", "очно-заочная")
REQUEST_TYPES = {"transfer": "перевод", "enrollment": "зачисление"}


@dataclass
class ParseResult:
    status: str
    value: Any = None
    message: str = ""
    extra: dict = field(default_factory=dict)  # например, details у задолженностей

    @property
    def ok(self) -> bool:
        return self.status == OK


def ok(value: Any, **extra: Any) -> ParseResult:
    return ParseResult(OK, value, "", dict(extra))


def invalid(message: str) -> ParseResult:
    return ParseResult(INVALID, None, message)


def ambiguous(message: str, **extra: Any) -> ParseResult:
    return ParseResult(AMBIGUOUS, None, message, dict(extra))


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()


# слова, которыми отвечают «мимо»: в роли названия/ФИО/специальности они недопустимы
NON_ANSWERS = {"нет", "не", "да", "нету", "знаю", "не знаю", "ничего", "хз", "пропустить", "пропусти", "-", "—", "..", "...", "?", "ок", "ладно", "потом"}
_QUOTES = "«»\"'“”„ \t"


def _strip(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().strip(_QUOTES)).strip(" .,;:")


def _non_answer(text: str) -> bool:
    n = norm(text)
    return n in NON_ANSWERS or all(w in NON_ANSWERS for w in n.split())


# ---------------------------------------------------------------- ФИО
_NAME_PREFIX = re.compile(r"^(?:меня зовут|мое имя|мои фио|мои данные|фио|зовут|имя|это|я)\s*[:\-—]?\s+", re.IGNORECASE)
_NAME_WORD = re.compile(r"^[A-Za-zА-Яа-яЁё]{2,}(?:[-'’][A-Za-zА-Яа-яЁё]{1,})*$")


def _title_name(word: str) -> str:
    return "-".join(p[:1].upper() + p[1:].lower() for p in word.split("-"))


def parse_full_name(text: str) -> ParseResult:
    t = _strip(text.replace("ё", "ё"))
    t = _NAME_PREFIX.sub("", t).replace(",", " ")
    words = t.split()
    hint = "Напишите фамилию, имя и отчество полностью, например: Иванов Иван Иванович."
    if not words or _non_answer(t):
        return invalid(hint)
    if any(re.search(r"\d", w) for w in words):
        return invalid("В ФИО не должно быть цифр. " + hint)
    if len(words) < 2:
        return invalid("Нужны хотя бы фамилия и имя. " + hint)
    if len(words) > 4:
        return invalid("Слишком много слов для ФИО. " + hint)
    if not all(_NAME_WORD.match(w) for w in words):
        return invalid("ФИО должно состоять из букв (инициалы без расшифровки не подходят). " + hint)
    return ok(" ".join(_title_name(w) if (w.islower() or w.isupper()) else w for w in words))


# ---------------------------------------------------------------- вуз
_UNI_PREFIX = re.compile(
    r"^(?:я\s+)?(?:учусь|обучаюсь|учился|училась|являюсь\s+студент\w*|студент\w*)\s+(?:в|во|на)\s+|^(?:мой\s+вуз|вуз|из|в)\s*[:\-—]?\s+", re.IGNORECASE)


def parse_university(text: str) -> ParseResult:
    t = _UNI_PREFIX.sub("", _strip(text))
    t = _strip(t)
    if not t or _non_answer(t):
        return invalid("Напишите название вуза, в котором вы учитесь сейчас, например: Южный федеральный университет.")
    if not re.search(r"[A-Za-zА-Яа-я]{2}", t) or re.fullmatch(r"[\d\W]+", t):
        return invalid("Название вуза должно содержать буквы. Напишите его словами или аббревиатурой (например, ЮФУ).")
    if len(t) > 150:
        return invalid("Слишком длинное название. Напишите короче — только название вуза.")
    return ok(t)


# ---------------------------------------------------------------- курс
_COURSE_WORDS = {"перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6}


def parse_course(text: str) -> ParseResult:
    n = norm(text)
    nums = {int(x) for x in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", n)}
    words = {v for k, v in _COURSE_WORDS.items() if re.search(rf"\b{k}\w*", n)}
    found = nums | words
    if not found:
        return invalid("Напишите номер курса цифрой от 1 до 6, например: 2.")
    bad = {x for x in found if not 1 <= x <= 6}
    if bad and not (found - bad):
        return invalid("Курс должен быть числом от 1 до 6.")
    found -= bad
    if len(found) > 1:
        return ambiguous(f"Вы назвали несколько курсов ({', '.join(str(x) for x in sorted(found))}). На каком курсе вы учитесь сейчас?")
    course = next(iter(found))
    if "магистр" in n and course > 2:
        return ambiguous(f"Вы написали про магистратуру и {course} курс — в магистратуре обычно 1–2 курс. Уточните, пожалуйста, курс.")
    if _HEDGE.search(n) or "половин" in n:  # «вроде второй», «пятый с половиной» — не записываем догадку
        return ambiguous(f"Уточните, пожалуйста: вы учитесь на {course} курсе? Напишите номер курса цифрой.")
    return ok(str(course))


# ---------------------------------------------------------------- направление / специальность
_SPEC_PREFIX = re.compile(
    r"^(?:я\s+)?(?:учусь|обучаюсь)\s+(?:на|по)\s+(?:направлени\w+|специальност\w+)?\s*|^(?:направление(?:\s+подготовки)?|специальность|напр\.?)\s*[:\-—]?\s*", re.IGNORECASE)


def parse_specialty(text: str) -> ParseResult:
    t = _strip(_SPEC_PREFIX.sub("", _strip(text)))
    if not t or _non_answer(t):
        return invalid("Напишите направление подготовки или специальность, например: 09.03.01 Информатика и вычислительная техника.")
    if len(re.findall(r"[A-Za-zА-Яа-я0-9]", t)) < 3:
        return invalid("Слишком коротко. Напишите направление подготовки или специальность словами или кодом (например, 09.03.01).")
    if len(t) > 200:
        return invalid("Слишком длинный ответ. Напишите только название направления или специальности.")
    return ok(t)


# ---------------------------------------------------------------- форма обучения
def parse_form(text: str) -> ParseResult:
    n = norm(text)
    mixed = bool(re.search(r"очно[\s-]*заочн\w*|вечерн\w*", n))
    rest = re.sub(r"очно[\s-]*заочн\w*|вечерн\w*", " ", n)
    kinds = []
    if re.search(r"\bочн\w*|дневн\w*", rest):
        kinds.append("очная")
    if re.search(r"заочн\w*|\bзаоч\b|дистанцион\w*", rest):
        kinds.append("заочная")
    if mixed:
        kinds.append("очно-заочная")
    if len(kinds) == 1:
        if _HEDGE.search(n):  # «очная вроде», «наверное заочная» — переспрашиваем, а не записываем догадку
            return ambiguous(f"Уточните, пожалуйста: у вас {kinds[0]} форма обучения? Ответьте одним словом: очная, заочная или очно-заочная.")
        return ok(kinds[0])
    if len(kinds) > 1:
        return ambiguous("Вы назвали несколько форм обучения. Какая у вас сейчас: очная, заочная или очно-заочная?")
    return invalid("Выберите форму обучения: очная, заочная или очно-заочная.")


# ---------------------------------------------------------------- академические задолженности
_HEDGE = re.compile(
    r"\bвроде\b|кажется|наверн\w*|возможно|не\s+уверен\w*|не\s+помню|не\s+знаю|может\s+быть|скорее\s+всего|как\s+бы|\bтипа\b|пожалуй|вряд\s+ли|"
    r"надеюсь|сомнева\w+|не\s+совсем|почти|частично|не\s+точно|примерно|\bесли\s+не\b|\bвозможн\w*")
_NEG = re.compile(
    r"\bнет\b|\bнету\b|\bне\s+(?:имею|было|бывает)\b|без\s+(?:долгов|задолженност\w*|хвостов)|"
    r"(?:долгов|задолженност\w*|хвостов|академ\w*\s+долгов)\s+(?:нет|нету|не\s+(?:имею|было))|отсутству\w+|"
    r"(?:все|всю)\s+(?:сдал\w*|закрыл\w*|закрыт\w*|сдан\w*)|(?:закрыл\w*|сдал\w*)\s+(?:все|всю)|чист\w+\s+(?:зачетк\w*|без)|никаких")
_POS = re.compile(
    r"\bда\b|\bесть\b|\bимеется\b|\bимею\b|\bимеют\w*|не\s+(?:сдал\w*|закрыл\w*|сдан\w*|закрыт\w*|сдавал\w*)|"
    r"\bхвост\w*|(?:долг\w*|задолженност\w*)\s+по\b|\bпересдач\w*|\b(?:один|два|три|пара|несколько|\d+)\s+(?:долг\w*|хвост\w*|задолженност\w*)")
_DETAILS = re.compile(r"\bпо\s+([а-яa-z0-9][а-яa-z0-9 ,\-и]{2,80}?)(?=[.!?;]|$)")


def parse_debts(text: str) -> ParseResult:
    n = norm(text)
    if not n:
        return invalid("Ответьте «да» или «нет»: есть ли у вас академические задолженности?")
    n1 = re.sub(r"^да\s*,?\s*(?=не\b|нет\b|нету\b)", "", n)  # «да не», «да нет» — это отрицание
    neg, pos, hedge = bool(_NEG.search(n1)), bool(_POS.search(n1)), bool(_HEDGE.search(n1))
    if neg and pos:
        return ambiguous("В ответе есть и «нет», и упоминание задолженностей. Уточните, пожалуйста: академические задолженности у вас есть — да или нет?", conflict=True)
    if hedge:
        return ambiguous("Не уверена, что поняла правильно. Ответьте коротко: есть ли у вас академические задолженности — «да» или «нет»?")
    if neg:
        return ok(False)
    if pos:
        m = _DETAILS.search(n1)
        return ok(True, details=_strip(m.group(1)) if m else None)
    return invalid("Ответьте «да» или «нет»: есть ли у вас академические задолженности?")


# ---------------------------------------------------------------- направление обращения, целевая специальность, паспорт
def parse_request_type(text: str) -> ParseResult:
    n = norm(text)
    transfer = bool(re.search(r"перевод\w*|перевест\w*|перейти|переход\w*", n))
    enroll = bool(re.search(r"поступ\w*|зачисл\w*", n))
    if transfer and enroll:
        return ambiguous("Вы назвали и перевод, и зачисление. Что вам нужно: перевод из другого вуза или зачисление?")
    if transfer:
        return ok("transfer")
    if enroll:
        return ok("enrollment")
    return invalid("Напишите, что вам нужно: «перевод» из другого вуза или «зачисление».")


def parse_target_specialty(text: str) -> ParseResult:
    res = parse_specialty(text)
    if not res.ok:
        return invalid(res.message.replace("направление подготовки или специальность", "направление, на которое хотите перевестись или поступить") + " Это поле можно пропустить.")
    return res


def parse_passport(text: str) -> ParseResult:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 10:
        return ok(f"серия {digits[:4]} № {digits[4:]}")
    return invalid("Паспорт РФ: 4 цифры серии и 6 цифр номера, например 1234 567890. Это поле можно пропустить — тогда вы впишете данные вручную в готовый документ.")


# ====================================================================== описание вопросов
@dataclass(frozen=True)
class Question:
    key: str
    title: str  # название поля в обзоре
    prompt: str
    parse: Callable[[str], ParseResult]
    required: bool = True
    kind: str = "text"  # тип значения для разбора моделью: text | int | enum | bool
    choices: tuple = ()
    options: tuple = ()  # быстрые ответы
    aliases: tuple = ()  # как пользователь называет поле в команде «исправить …»
    sensitive: bool = False  # никогда не передаётся модели
    value_hint: str = ""

    def show(self, value: Any, data: dict) -> str:
        return format_value(self.key, value, data)


def format_value(key: str, value: Any, data: Optional[dict] = None) -> str:
    if key == "has_debts":
        if value is False:
            return "нет"
        details = (data or {}).get("debts_details")
        return f"есть ({details})" if details else "есть"
    if key == "request_type":
        return REQUEST_TYPES.get(value, str(value))
    return "" if value is None else str(value)


QUESTIONS: dict[str, Question] = {q.key: q for q in (
    Question("student_name", "ФИО", "Как вас зовут? Напишите фамилию, имя и отчество полностью.", parse_full_name,
             aliases=("фио", "имя", "фамилия", "фамилию", "имя фамилия")),
    Question("current_university", "Текущий вуз", "В каком вузе вы учитесь сейчас? (Это ваш текущий вуз, а не тот, куда вы хотите перевестись.)", parse_university,
             aliases=("вуз", "университет", "текущий вуз", "институт")),
    Question("current_course", "Текущий курс", "На каком курсе вы учитесь? Напишите цифрой, например: 2.", parse_course, kind="int",
             options=("1", "2", "3", "4"), aliases=("курс", "текущий курс")),
    Question("current_specialty", "Направление / специальность", "Какое у вас направление подготовки или специальность? Можно с кодом, например: 09.03.01 Информатика.", parse_specialty,
             aliases=("направление", "специальность", "направление подготовки", "текущее направление")),
    Question("education_form", "Форма обучения", "Какая у вас форма обучения: очная, заочная или очно-заочная?", parse_form, kind="enum", choices=EDUCATION_FORMS,
             options=EDUCATION_FORMS, aliases=("форма", "форма обучения")),
    Question("has_debts", "Академические задолженности", "Есть ли у вас академические задолженности? Ответьте «да» или «нет» (можно добавить, по каким предметам).", parse_debts, kind="bool",
             options=("Нет", "Да"), aliases=("задолженности", "долги", "задолженность", "хвосты", "академические задолженности"),
             value_hint="true — у студента ЕСТЬ академические задолженности, false — задолженностей НЕТ"),
    Question("request_type", "Направление обращения", "Что вам нужно: перевод из другого вуза или зачисление (поступление)?", parse_request_type, kind="enum",
             choices=("transfer", "enrollment"), options=("Перевод", "Зачисление"), aliases=("обращение", "цель", "перевод", "зачисление", "направление обращения")),
    Question("target_specialty", "Целевое направление", "На какое направление или специальность вы хотите перевестись/поступить к нам? (Можно пропустить, но тогда обращение в деканат отправить не получится — документ вы всё равно скачаете.)", parse_target_specialty, required=False,
             options=("Пропустить",), aliases=("целевое", "целевое направление", "целевая специальность", "куда")),
    Question("passport_data", "Паспортные данные", "Паспортные данные (серия и номер) нужны только для текста заявления. Можно пропустить и вписать их вручную в готовый документ.", parse_passport,
             required=False, options=("Пропустить",), aliases=("паспорт", "паспортные данные"), sensitive=True),
)}

CORE_KEYS = ("student_name", "current_university", "current_course", "current_specialty", "education_form", "has_debts")


def question_order(scenario: str) -> list[str]:
    """Фиксированный чек-лист из шести вопросов, затем (при необходимости) направление обращения и необязательные поля."""
    keys = list(CORE_KEYS)
    if scenario not in REQUEST_TYPES:
        keys.append("request_type")
    keys += ["target_specialty", "passport_data"]
    return keys
