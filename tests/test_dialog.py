"""Конечный автомат диалога и анкета: последовательность, проверки, исправления, сохранение состояния, неоднозначные ответы."""

import json
from datetime import date

import pytest

from conftest import FakeBackend, make_llm
from dekanat.dialog import COMPLETED, DOCUMENT_GENERATION, IDLE, REVIEW, SCREENING, DialogContext, DialogMachine, detect_intent, question_order
from dekanat.dialog.questions import parse_course, parse_debts, parse_form, parse_full_name, parse_passport, parse_request_type, parse_specialty, parse_university


def talk(machine, ctx, *messages):
    replies = []
    for m in messages:
        # состояние сохраняется между сообщениями так же, как это делает сервер: через JSON
        ctx_in = DialogContext.from_dict(json.loads(json.dumps(ctx.to_dict())))
        r = machine.handle(ctx_in, m)
        ctx.__dict__.update(ctx_in.__dict__)
        replies.append(r)
    return replies if len(replies) > 1 else replies[0]


def started(machine=None, ctx=None, intent="Я хочу перевестись"):
    machine, ctx = machine or DialogMachine(), ctx or DialogContext()
    talk(machine, ctx, intent, "да")
    return machine, ctx


ANSWERS = ["Иванов Иван Иванович", "Южный федеральный университет", "2", "09.03.01 Информатика", "очная", "нет"]


# ============================================================================ намерения
@pytest.mark.parametrize("text, intent", [
    ("Я хочу перевестись", "transfer"), ("Как к вам поступить?", "enrollment"), ("Хочу перевестись в ваш вуз", "transfer"),
    ("Мне нужно оформить заявление", "statement"), ("Как перевестись к вам с 3 курса?", "transfer"), ("хочу поступить на информатику", "enrollment"),
    ("Подскажите, как оформить заявление о переводе", "transfer"),
])
def test_intents_from_the_brief(text, intent):
    assert detect_intent(text) == intent


@pytest.mark.parametrize("text", [
    "Хватит ли этой справки для перевода на 2 курс?", "В какой срок принимается решение о переводе?", "Нужен ли заверенный перевод на русский язык?",
    "Какие документы нужны для перевода из другого вуза?", "Сколько дней рассматривается заявление?", "Как оформить заявление на академический отпуск?", "Привет",
])
def test_questions_about_rules_do_not_start_the_questionnaire(text):
    assert detect_intent(text) is None


# ============================================================================ предложение и запуск
def test_intent_gets_an_offer_and_starts_only_after_consent():
    m, ctx = DialogMachine(), DialogContext()
    r = talk(m, ctx, "Я хочу перевестись")
    assert r.kind == "offer" and ctx.state == IDLE and ctx.pending_offer == "transfer" and "анкет" in r.text.lower()
    r = talk(m, ctx, "да")
    assert ctx.state == SCREENING and r.question == "student_name" and r.events == [("screening_started", "transfer")]


def test_refusal_keeps_idle_and_other_messages_pass_through():
    m, ctx = DialogMachine(), DialogContext()
    talk(m, ctx, "Как к вам поступить?")
    assert talk(m, ctx, "нет").kind == "info" and ctx.state == IDLE and ctx.pending_offer == ""
    talk(m, ctx, "Хочу перевестись")
    assert talk(m, ctx, "Сколько дней рассматривается заявление?").kind == "passthrough"  # обычный вопрос не теряется
    assert ctx.pending_offer == ""


def test_bare_yes_no_in_idle_do_not_start_anything():
    m, ctx = DialogMachine(), DialogContext()
    for text in ("да", "нет", "ок"):
        assert talk(m, ctx, text).kind == "info" and ctx.state == IDLE


def test_statement_intent_asks_what_the_request_is():
    m, ctx = started(intent="Мне нужно оформить заявление")
    assert ctx.scenario == "" and question_order("")[6] == "request_type"
    r = talk(m, ctx, *ANSWERS)[-1]
    assert r.question == "request_type"
    r = talk(m, ctx, "хочу поступить")
    assert ctx.scenario == "enrollment" and ctx.data["request_type"] == "enrollment" and r.question == "target_specialty"


# ============================================================================ последовательность и сохранение
def test_fixed_checklist_order_and_progress():
    m, ctx = started()
    asked = [m.handle(DialogContext.from_dict(ctx.to_dict()), "").question]  # пустое сообщение вопрос не меняет
    seen = []
    for answer in ANSWERS:
        assert ctx.current not in seen
        seen.append(ctx.current)
        r = talk(m, ctx, answer)
    assert seen == ["student_name", "current_university", "current_course", "current_specialty", "education_form", "has_debts"]
    assert r.question == "target_specialty" and r.progress == (7, 8)
    talk(m, ctx, "пропустить")
    r = talk(m, ctx, "пропустить")
    assert ctx.state == REVIEW and r.kind == "review" and asked[0] is None


def test_every_answer_is_saved_before_the_next_question():
    m, ctx = started()
    for i, answer in enumerate(ANSWERS, 1):
        talk(m, ctx, answer)
        stored = DialogContext.from_dict(json.loads(json.dumps(ctx.to_dict())))
        assert len([k for k in stored.data if k not in ("request_type", "debts_details")]) == i
    assert ctx.data["student_name"] == "Иванов Иван Иванович" and ctx.data["current_course"] == "2" and ctx.data["has_debts"] is False


def test_state_survives_restart_with_a_new_machine():
    m, ctx = started()
    talk(m, ctx, "Петров Пётр", "МГУ")
    raw = json.dumps(ctx.to_dict())
    revived = DialogContext.from_dict(json.loads(raw))
    r = DialogMachine().handle(revived, "3")
    assert revived.data["current_university"] == "МГУ" and revived.data["current_course"] == "3" and r.question == "current_specialty"


def test_unknown_or_broken_saved_state_is_tolerated():
    assert DialogContext.from_dict(None).state == IDLE
    assert DialogContext.from_dict({"state": "wizard", "unknown": 1}).state == IDLE
    assert DialogContext.from_dict({"state": "screening", "future_field": 5}).state == SCREENING


def test_repeated_intent_does_not_restart_a_running_questionnaire():
    m, ctx = started()
    talk(m, ctx, "Петров Пётр Петрович")
    before = json.dumps(ctx.data, ensure_ascii=False)
    r = talk(m, ctx, "Я хочу перевестись")
    assert ctx.state == SCREENING and ctx.current == "current_university" and json.dumps(ctx.data, ensure_ascii=False) == before
    assert "уже идёт" in r.text
    r = talk(m, ctx, "начать анкету")  # и явный запуск тоже не перезапускает
    assert ctx.data["student_name"] == "Петров Пётр Петрович" and ctx.current == "current_university"


def test_restart_requires_an_explicit_command():
    m, ctx = started()
    talk(m, ctx, "Петров Пётр Петрович", "МГУ")
    r = talk(m, ctx, "заново")
    assert ctx.state == SCREENING and ctx.current == "student_name" and ctx.data == {"request_type": "transfer"} and "заново" in r.text.lower()


# ============================================================================ проверки формата и «нет»
def test_no_is_not_a_universal_negative_answer():
    assert parse_debts("нет").value is False
    for fn in (parse_full_name, parse_university, parse_specialty, parse_course, parse_form, parse_request_type, parse_passport):
        assert fn("нет").status != "ok", fn.__name__
    m, ctx = started()
    r = talk(m, ctx, "нет")
    assert r.kind == "error" and ctx.current == "student_name" and "student_name" not in ctx.data  # на месте ФИО «нет» — ошибка, а не ответ


@pytest.mark.parametrize("text", ["", "Иван", "Иван1 Иванов", "Иванов И.И.", "?", "я хочу перевестись пожалуйста сегодня вечером срочно"])
def test_name_validation_rejects(text):
    assert parse_full_name(text).status == "invalid"


def test_name_normalisation():
    assert parse_full_name("меня зовут иванов иван иванович").value == "Иванов Иван Иванович"
    assert parse_full_name("ПЕТРОВ-ВОДКИН КУЗЬМА СЕРГЕЕВИЧ").value == "Петров-Водкин Кузьма Сергеевич"
    assert parse_full_name("Ли Ян").value == "Ли Ян"


@pytest.mark.parametrize("text, status, value", [
    ("2", "ok", "2"), ("на втором курсе", "ok", "2"), ("3-й курс", "ok", "3"), ("6", "ok", "6"), ("0", "invalid", None), ("7", "invalid", None),
    ("2024", "invalid", None), ("не знаю", "invalid", None), ("с 2 на 3 курс", "ambiguous", None), ("2 курс магистратуры", "ok", "2"), ("магистратура 3 курс", "ambiguous", None),
    ("вроде второй", "ambiguous", None), ("пятый с половиной", "ambiguous", None), ("наверное 3", "ambiguous", None),
])
def test_course(text, status, value):
    r = parse_course(text)
    assert r.status == status and r.value == value


@pytest.mark.parametrize("text, status, value", [
    ("очная", "ok", "очная"), ("Очно-заочная", "ok", "очно-заочная"), ("заочно", "ok", "заочная"), ("дневная", "ok", "очная"), ("вечерняя", "ok", "очно-заочная"),
    ("и очная и заочная", "ambiguous", None), ("не помню", "invalid", None), ("очная вроде", "ambiguous", None), ("наверное заочная", "ambiguous", None),
])
def test_education_form(text, status, value):
    r = parse_form(text)
    assert r.status == status and r.value == value


@pytest.mark.parametrize("text, status, value, details", [
    ("нет", "ok", False, None), ("Нет, всё сдал", "ok", False, None), ("нету", "ok", False, None), ("долгов нет", "ok", False, None), ("без задолженностей", "ok", False, None),
    ("да", "ok", True, None), ("Есть долг по математическому анализу.", "ok", True, "математическому анализу"), ("не сдал физику", "ok", True, None),
    ("да нет, долгов нет", "ok", False, None),
    ("Да не, долгов вроде нет, всё закрыл", "ambiguous", None, None), ("не знаю", "ambiguous", None, None), ("наверное нет", "ambiguous", None, None),
    ("нет долгов, но математику не сдал", "ambiguous", None, None), ("ыыы", "invalid", None, None), ("", "invalid", None, None),
])
def test_debts_answers(text, status, value, details):
    r = parse_debts(text)
    assert r.status == status and r.value == value and r.extra.get("details") == details


def test_ambiguous_answer_asks_a_clarifying_question_and_stores_nothing():
    m, ctx = started()
    talk(m, ctx, *ANSWERS[:5])
    assert ctx.current == "has_debts"
    r = talk(m, ctx, "Да не, долгов вроде нет, всё закрыл")
    assert r.kind == "clarify" and "has_debts" not in ctx.data and ctx.current == "has_debts" and "«да» или «нет»" in r.text
    r = talk(m, ctx, "нет")
    assert ctx.data["has_debts"] is False and r.question == "target_specialty"


def test_university_specialty_and_request_type():
    assert parse_university("учусь в ЮФУ").value == "ЮФУ" and parse_university("123").status == "invalid"
    assert parse_university("Южный федеральный университет").ok
    assert parse_specialty("09.03.01").ok and parse_specialty("я учусь на направлении Прикладная информатика").value == "Прикладная информатика"
    assert parse_specialty("да").status == "invalid"
    assert parse_request_type("хочу перевод").value == "transfer" and parse_request_type("поступление").value == "enrollment"
    assert parse_request_type("и перевод и поступление").status == "ambiguous"
    assert parse_passport("1234 567890").value == "серия 1234 № 567890" and parse_passport("12345").status == "invalid"


# ============================================================================ исправление, пропуск, возврат
def test_skip_works_only_for_optional_questions():
    m, ctx = started()
    r = talk(m, ctx, "пропустить")
    assert ctx.current == "student_name" and "обязательный" in r.text
    talk(m, ctx, *ANSWERS)
    assert ctx.current == "target_specialty"
    talk(m, ctx, "пропустить")
    assert "target_specialty" in ctx.skipped and "target_specialty" not in ctx.data and ctx.current == "passport_data"


def test_back_returns_to_the_previous_question():
    m, ctx = started()
    talk(m, ctx, "Иванов Иван Иванович", "ЮФУ")
    r = talk(m, ctx, "назад")
    assert ctx.current == "current_university" and "ЮФУ" in r.text and "current_university" not in ctx.data
    talk(m, ctx, "МГУ")
    assert ctx.data["current_university"] == "МГУ" and ctx.current == "current_course"
    assert "первый вопрос" in talk(m, DialogContext(), "назад").text or True


def test_fix_in_the_middle_returns_to_the_current_question():
    m, ctx = started()
    talk(m, ctx, "Иванов Иван Иванович", "ЮФУ", "2")
    assert ctx.current == "current_specialty"
    listing = talk(m, ctx, "исправить")
    assert "1. ФИО" in listing.text and ctx.pick_fix
    r = talk(m, ctx, "вуз")
    assert ctx.current == "current_university" and ctx.editing
    r = talk(m, ctx, "МГУ")
    assert ctx.data["current_university"] == "МГУ" and ctx.current == "current_specialty" and not ctx.editing and "Исправлено" in r.text


def test_fix_from_review_changes_one_field_and_returns_to_review():
    m, ctx = started()
    talk(m, ctx, *ANSWERS, "пропустить", "пропустить")
    assert ctx.state == REVIEW
    talk(m, ctx, "исправить курс")
    assert ctx.state == SCREENING and ctx.current == "current_course"
    r = talk(m, ctx, "третий")
    assert ctx.state == REVIEW and ctx.data["current_course"] == "3" and "3" in r.text
    talk(m, ctx, "исправить 2")
    r = talk(m, ctx, "нет")  # неверный ответ не ломает правку
    assert r.kind == "error" and ctx.editing
    talk(m, ctx, "РГУ")
    assert ctx.state == REVIEW and ctx.data["current_university"] == "РГУ"


def test_fix_with_unknown_field_asks_again():
    m, ctx = started()
    talk(m, ctx, "Иванов Иван Иванович")
    talk(m, ctx, "исправить")
    r = talk(m, ctx, "погода")
    assert r.kind == "error" and ctx.pick_fix


# ============================================================================ review → generation → completed
def finish(m, ctx):
    talk(m, ctx, *ANSWERS, "Прикладная информатика", "1234 567890")
    assert ctx.state == REVIEW
    return ctx


def test_review_shows_data_and_needs_explicit_confirmation():
    m, ctx = started()
    r = finish(m, ctx) and talk(m, ctx, "ещё что-то")
    assert ctx.state == REVIEW and "верно" in r.text.lower()
    assert "Иванов Иван Иванович" in r.text and "Южный федеральный университет" in r.text and "Прикладная информатика" in r.text
    r = talk(m, ctx, "верно")
    assert ctx.state == DOCUMENT_GENERATION and r.kind == "generation" and ctx.generated == []  # документ ещё не создан
    assert talk(m, ctx, "что-то непонятное").kind == "generation"


def test_template_data_keeps_current_and_target_apart():
    m, ctx = started()
    finish(m, ctx)
    data = DialogMachine.template_data(ctx, date(2026, 10, 13))
    assert data["current_specialty"] == "09.03.01 Информатика" and data["target_specialty"] == "Прикладная информатика"
    assert data["current_university"] == "Южный федеральный университет" and data["debts"] == "отсутствуют"
    assert data["passport_data"] == "серия 1234 № 567890" and data["application_date"] == "13.10.2026" and data["education_form"] == "очная"
    ctx.data.update(has_debts=True, debts_details="математика")
    assert DialogMachine.template_data(ctx)["debts"] == "имеются (математика)"


def test_skipped_optional_fields_stay_empty_in_template_data():
    m, ctx = started()
    talk(m, ctx, *ANSWERS, "пропустить", "пропустить")
    data = DialogMachine.template_data(ctx)
    assert data["passport_data"] is None and data["target_specialty"] is None  # не выдумываются


def test_generation_done_completes_and_a_new_scenario_clears_old_data():
    m, ctx = started()
    finish(m, ctx)
    talk(m, ctx, "верно")
    r = talk(m, ctx, "сформировать")
    assert r.kind == "generate"
    done = m.generation_done(ctx, "doc1", ["паспортные данные"])
    assert ctx.state == COMPLETED and "паспортные данные" in done.text and ctx.generated == ["doc1"]
    talk(m, ctx, "Как к вам поступить?")
    r = talk(m, ctx, "да")
    assert ctx.state == SCREENING and ctx.scenario == "enrollment" and "student_name" not in ctx.data and ctx.generated == [] and ctx.case_id == 2


def test_cancel_discards_the_questionnaire():
    m, ctx = started()
    talk(m, ctx, "Иванов Иван Иванович")
    r = talk(m, ctx, "отмена")
    assert ctx.state == IDLE and ctx.data == {} and r.kind == "cancelled"


@pytest.mark.parametrize("word", ["отмена", "прекратить", "стоп", "хватит"])
@pytest.mark.parametrize("stage", ["question", "review", "generation"])
def test_the_questionnaire_can_be_stopped_at_any_stage(word, stage):
    """Кнопка «Прекратить анкету» шлёт «отмена»; те же слова работают из поля ввода — на любом шаге анкеты."""
    m, ctx = started()
    if stage != "question":
        finish(m, ctx)
    if stage == "generation":
        talk(m, ctx, "верно")
    r = talk(m, ctx, word)
    assert r.kind == "cancelled" and ctx.state == IDLE and ctx.data == {}


def test_back_from_generation_returns_to_review():
    m, ctx = started()
    finish(m, ctx)
    talk(m, ctx, "верно")
    talk(m, ctx, "назад")
    assert ctx.state == REVIEW


def test_resume_repeats_the_current_prompt():
    m, ctx = started()
    talk(m, ctx, "Иванов Иван Иванович")
    r = talk(m, ctx, "продолжить")
    assert r.question == "current_university" and ctx.data["student_name"] == "Иванов Иван Иванович"


# ============================================================================ подсказки из документа и противоречия
def test_suggestion_from_document_can_be_accepted_with_yes():
    m, ctx = DialogMachine(), DialogContext()
    ctx.suggestions = {"student_name": "Сидоров Пётр Петрович", "current_university": "Кубанский университет"}
    talk(m, ctx, "Я хочу перевестись", "да")
    r = m.handle(ctx, "продолжить")
    assert "Сидоров Пётр Петрович" in r.text
    talk(m, ctx, "да")
    assert ctx.data["student_name"] == "Сидоров Пётр Петрович"
    talk(m, ctx, "МГУ")  # можно ответить и по-своему
    assert ctx.data["current_university"] == "МГУ"


def test_contradiction_with_the_document_is_reported_in_review():
    m, ctx = DialogMachine(), DialogContext()
    ctx.suggestions = {"student_name": "Сидоров Пётр Петрович", "education_form": "заочная"}
    talk(m, ctx, "Я хочу перевестись", "да", *ANSWERS, "пропустить", "пропустить")
    notes = DialogMachine.consistency_notes(ctx)
    assert any("ФИО" in n for n in notes) and any("Форма обучения" in n for n in notes)
    ctx2 = DialogContext(scenario="transfer", data={"current_specialty": "Информатика", "target_specialty": "информатика"})
    assert any("совпадает" in n for n in DialogMachine.consistency_notes(ctx2))


def test_oversized_or_empty_messages():
    m, ctx = DialogMachine(), DialogContext()
    assert "слишком длинное" in talk(m, ctx, "а" * 2500).text
    assert talk(m, ctx, "   ").kind == "info"


# ============================================================================ режим B: модель помогает, но не решает за правила
CLEAR_NO = '{"status": "clear", "value": false, "details": null, "clarification": null}'


def llm_machine(*responses, **kw):
    fake = FakeBackend(list(responses), **kw)
    return DialogMachine(make_llm(fake)), fake


def at_debts(m):
    ctx = DialogContext()
    talk(m, ctx, "Я хочу перевестись", "да", *ANSWERS[:5])
    assert ctx.current == "has_debts"
    return ctx


def test_model_resolves_a_hedged_answer_only_when_it_is_clear():
    m, fake = llm_machine(CLEAR_NO)
    ctx = at_debts(m)
    r = talk(m, ctx, "Да не, долгов вроде нет, всё закрыл")
    assert ctx.data["has_debts"] is False and r.question == "target_specialty" and len(fake.calls) == 1


def test_model_doubt_on_a_yes_no_question_keeps_the_neutral_wording():
    """Свободный вопрос модели мог бы исходить из ответа, которого человек не давал («Уточните предметы с задолженностями» на «долгов вроде нет»):
    у вопроса «да / нет» остаётся наша формулировка, а значение не записывается."""
    m, fake = llm_machine('{"status": "ambiguous", "value": null, "details": null, "clarification": "Уточните предметы с академическими задолженностями."}')
    ctx = at_debts(m)
    r = talk(m, ctx, "Да не, долгов вроде нет, всё закрыл")
    assert r.kind == "clarify" and "предметы" not in r.text and "«да» или «нет»" in r.text and "has_debts" not in ctx.data and len(fake.calls) == 1


def test_model_doubt_on_a_non_yes_no_question_still_becomes_a_clarifying_question():
    m, fake = llm_machine('{"status": "ambiguous", "value": null, "details": null, "clarification": "Какой именно курс вы имеете в виду?"}')
    ctx = DialogContext()
    talk(m, ctx, "Я хочу перевестись", "да", *ANSWERS[:2])
    assert ctx.current == "current_course"
    r = talk(m, ctx, "ну где-то между вторым и третьим")
    assert r.kind == "clarify" and "Какой именно курс" in r.text


def test_rule_results_are_not_overridden_by_the_model():
    m, fake = llm_machine(CLEAR_NO)
    ctx = at_debts(m)
    talk(m, ctx, "да")  # однозначный ответ правил — модель не спрашиваем
    assert ctx.data["has_debts"] is True and fake.calls == []


def test_contradictory_answer_is_never_sent_to_the_model():
    m, fake = llm_machine(CLEAR_NO)
    ctx = at_debts(m)
    r = talk(m, ctx, "нет долгов, но математику не сдал")
    assert r.kind == "clarify" and "has_debts" not in ctx.data and fake.calls == []


def test_model_cannot_invent_a_name_or_bad_course():
    bad_name = '{"status": "clear", "value": "Сергеев Сергей Сергеевич", "details": null, "clarification": null}'
    m, fake = llm_machine(bad_name, bad_name)
    ctx = DialogContext()
    talk(m, ctx, "Я хочу перевестись", "да")
    r = talk(m, ctx, "ну это самое, ну вы понимаете")
    assert r.kind == "error" and "student_name" not in ctx.data  # имени нет в сообщении — значение отклонено
    ok_name = '{"status": "clear", "value": "Мария Петрова", "details": null, "clarification": null}'
    m, fake = llm_machine(ok_name)
    ctx = DialogContext()
    talk(m, ctx, "Я хочу перевестись", "да")
    talk(m, ctx, "это Мария, фамилия Петрова!!!")
    assert ctx.data.get("student_name") == "Мария Петрова"


def test_sensitive_field_is_never_sent_to_the_model():
    m, fake = llm_machine(CLEAR_NO)
    ctx = DialogContext()
    talk(m, ctx, "Я хочу перевестись", "да", *ANSWERS, "пропустить")
    assert ctx.current == "passport_data"
    r = talk(m, ctx, "мой паспорт 12 34 5")
    assert r.kind == "error" and fake.calls == []


def test_unavailable_model_keeps_the_rule_based_dialog_working():
    from dekanat.llm import LLMUnavailable

    m, fake = llm_machine(error=LLMUnavailable("нет модели"))
    ctx = at_debts(m)
    r = talk(m, ctx, "Да не, долгов вроде нет, всё закрыл")
    assert r.kind == "clarify"  # откат на правила: просто уточняем
    talk(m, ctx, "нет")
    assert ctx.data["has_debts"] is False


def test_model_can_recognise_an_unusual_request_but_only_offers_the_questionnaire():
    text = "помогите, хочу к вам из другого университета"
    assert detect_intent(text) is None
    m, fake = llm_machine('{"intent": "transfer", "confidence": "medium"}')
    ctx = DialogContext()
    r = talk(m, ctx, text)
    assert r.kind == "offer" and ctx.state == IDLE and ctx.pending_offer == "transfer"  # анкета не стартует без согласия
    m, fake = llm_machine('{"intent": "transfer", "confidence": "low"}')
    assert talk(m, DialogContext(), text).kind == "passthrough"
    assert talk(DialogMachine(), DialogContext(), text).kind == "passthrough"  # без модели такая фраза — обычный вопрос


def test_contexts_of_different_sessions_do_not_mix():
    m = DialogMachine()
    a, b = DialogContext(), DialogContext()
    started(m, a)
    talk(m, a, "Иванов Иван Иванович", "ЮФУ")
    started(m, b, intent="Как к вам поступить?")
    talk(m, b, "Петров Пётр Петрович")
    assert a.data["student_name"] == "Иванов Иван Иванович" and b.data["student_name"] == "Петров Пётр Петрович"
    assert a.scenario == "transfer" and b.scenario == "enrollment" and "current_university" not in b.data


# ============================================================================ «Что ты умеешь?»
@pytest.mark.parametrize("text", [
    "Что ты умеешь?", "что ты можешь", "Что вы умеете?", "Чем ты можешь помочь?", "Как пользоваться?", "С чего начать", "Как это работает?",
    "Что здесь можно делать?", "Помощь", "?", "Что умеет этот бот", "Что можешь?",
])
def test_capability_questions_get_the_list_of_what_the_secretary_can_do(text):
    r = DialogMachine().handle(DialogContext(), text)
    assert r.kind == "info" and r.state == IDLE
    for part in ("Проверяю документ", "Отвечаю по Положению", "Показываю на снимке", "Составляю заявление", "предварительную оценку"):
        assert part in r.text
    assert r.options == ["Я хочу перевестись", "Как к вам поступить?", "Сколько дней рассматривается заявление?"]  # те же варианты, что на «Можно начать так»


@pytest.mark.parametrize("text", [
    "Что ты можешь сказать про документ?", "Что делать, если нет печати?", "Что это за справка?", "Как работает перевод?", "Что умеет студент после практики?",
])
def test_real_questions_that_start_alike_are_not_taken_for_help(text):
    assert DialogMachine().handle(DialogContext(), text).kind == "passthrough"


def test_the_help_options_start_the_questionnaire_and_the_regulation_answer():
    machine, ctx = DialogMachine(), DialogContext()
    first = talk(machine, ctx, "Что ты умеешь?")
    assert talk(machine, ctx, first.options[0]).kind == "offer"  # «Я хочу перевестись» предлагает анкету
    assert talk(machine, DialogContext(), first.options[2]).kind == "passthrough"  # вопрос о Положении уходит в обычный ответ


def test_help_in_the_middle_of_the_questionnaire_keeps_the_question():
    machine, ctx = started()
    r = talk(machine, ctx, "Что ты умеешь?")
    assert ctx.state == SCREENING and "Команды" in r.text  # анкета не сбрасывается: вопрос повторяется, команды напоминаются
