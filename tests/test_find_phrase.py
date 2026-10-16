"""«Где на документе «фраза»?» подсвечивает найденное место на снимке, «выведи весь текст» отдаёт распознанный текст."""

import sys

import numpy as np
import pytest

from conftest import ROOT, needs_ocr
from dekanat.qa.docqa import FULLTEXT_SHOWN, DocumentQA

sys.path.insert(0, str(ROOT / "scripts"))
import check_classify as cc  # noqa: E402

ORDER = """ПРИКАЗ
г. Тестовск
14.02.2025 № 2
О мерах по подготовке к весеннему паводку 2025 года
1. Рекомендовать главам районов городского округа:
1.1. обеспечить очистку водоотводных канав до 01.03.2025;
2. Начальнику отдела связи обеспечить работу системы оповещения населения.
3. Контроль за выполнением приказа оставляю за собой."""

INVOICE = """СЧЁТ НА ОПЛАТУ № 125 от 12.03.2024
Поставщик: ООО «Вектор»
Назначение платежа: оплата за канцелярские товары
Итого к оплате: 15 400,00 руб.
Срок оплаты: до 20.03.2024"""


def qa():
    return DocumentQA([])


def page(text: str):
    return [cc.page_from_text(text)]


# ============================================================================ где на документе
def test_a_quoted_phrase_is_found_and_boxed():
    a = qa().answer("Где на документе «системы оповещения»?", page(ORDER))
    assert a.verdict == "ok" and a.debug["locate"] and a.debug["exact"]
    assert "найдено" in a.text and "оповещения" in a.text  # и строка, где она стоит
    src = a.sources[0]
    assert src.kind == "document" and src.ref == "found" and len(src.boxes) == 1
    box = src.boxes[0]
    assert 180 < box.y0 < 280 and box.x1 > box.x0  # рамка на нужной строке (строка 7 из 8 — не шапка)


@pytest.mark.parametrize("question", [
    "Покажи, где слово оповещения",
    "Где находится «оповещения»?",
    "Найди на документе слово оповещения.",
    "Выдели «системы оповещения» на снимке",
])
def test_other_ways_to_ask_where(question):
    a = qa().answer(question, page(ORDER))
    assert a.verdict == "ok" and a.debug["locate"] and a.sources[0].boxes


def test_every_occurrence_is_shown_and_counted():
    text = "Договор № 1\nПредмет договора\nСтороны договора обязуются\nПодписи сторон по договору"
    a = qa().answer("Где в документе слово «договора»?", page(text))
    assert a.debug["matches"] == 2 and "встречается 2 раза;" in a.text and len(a.sources[0].boxes) == 2  # «Договор» и «по договору» — другие формы слова


def test_a_typo_of_the_ocr_is_forgiven_but_flagged():
    a = qa().answer("Где на документе «системы оповешения»?", page(ORDER))  # в вопросе опечатка: в документе «оповещения»
    assert a.verdict == "ok" and not a.debug["exact"] and "Точного совпадения нет" in a.text and a.confidence < 0.7
    b = qa().answer("Где на документе «система оповещения»?", page(ORDER))  # другая форма слова
    assert b.verdict == "ok" and not b.debug["exact"]


def test_a_missing_phrase_gets_an_honest_answer():
    a = qa().answer("Где на документе «кредитный договор»?", page(ORDER))
    assert a.verdict == "no_answer" and "не нашлось" in a.text and not a.sources


def test_a_phrase_spanning_two_lines_is_found():
    text = "ДОГОВОР ОКАЗАНИЯ\nУСЛУГ № 5\nпредмет договора"
    a = qa().answer("Где «оказания услуг»?", page(text))
    assert a.verdict == "ok" and len(a.sources[0].boxes) == 2  # слова на двух строках — две рамки


def test_questions_about_a_value_are_not_hijacked():
    p = page(INVOICE)
    assert "15 400" in qa().answer("Сколько итого к оплате?", p).text  # обычный вопрос по значению работает по-прежнему
    a = qa().answer("Какая сумма указана в строке «Итого»?", p)
    assert not a.debug.get("locate")
    b = qa().answer("Покажи значение «Срок оплаты»", p)
    assert not b.debug.get("locate")
    c = qa().answer("Какое слово написано в заголовке?", p)
    assert not c.debug.get("locate")


def test_the_box_follows_the_line_on_a_tilted_photo():
    """Снимок под углом: найденное место — наклонный четырёхугольник вдоль строки."""
    pg = cc.page_from_text(ORDER)
    c, s = np.cos(np.radians(12)), np.sin(np.radians(12))
    pg.transform = np.array([[c, s, 0.0], [-s, c, 300.0], [0.0, 0.0, 1.0]])  # исходный снимок -> выпрямленный
    pg.orig_size = (1600, 2000)
    a = qa().answer("Где на документе «Начальнику отдела связи»?", [pg])
    box = a.sources[0].boxes[0]
    assert box.quad is not None and len(box.quad) == 4
    top = box.quad[1][1] - box.quad[0][1]
    assert abs(top) > 5  # верхняя сторона наклонена, а не горизонтальна


# ============================================================================ весь текст
@pytest.mark.parametrize("question", [
    "Выведи весь текст документа",
    "Распознай текст с изображения",
    "Экспортируй текст с картинки и представь его таким, каким он показан.",
    "Дай оттекстовку документа",
    "Покажи распознанный текст",
])
def test_whole_text_is_returned(question):
    a = qa().answer(question, page(ORDER))
    assert a.verdict == "ok" and a.debug["fulltext"]
    assert "ПРИКАЗ" in a.text and "оставляю за собой" in a.text and a.sources[0].ref == "fulltext"


def test_a_long_text_is_cut_with_a_pointer_to_the_panel():
    long = "\n".join(f"Строка номер {i} договора об оказании услуг по перевозке пассажиров" for i in range(60))
    a = qa().answer("Выведи весь текст документа", page(long))
    assert a.debug["chars"] > FULLTEXT_SHOWN and a.text.count("Строка номер") < 60
    assert "Показано начало" in a.text and "«Текст»" in a.text


@pytest.mark.parametrize("question", [
    "Какой текст написан в заголовке?",
    "Выведи только последний абзац текста документа",
    "Покажи текст пункта 1.1",
    "Что за текст в первой строке?",
])
def test_questions_about_a_part_are_not_the_whole_text(question):
    a = qa().answer(question, page(ORDER))
    assert not a.debug.get("fulltext")


# ============================================================================ через весь сервис, на настоящем фото
@needs_ocr
def test_the_phrase_is_found_on_a_tilted_photo_of_a_known_document_type(app, samples):
    """Справка вуза (тип известен, у неё есть поле «Название вуза»): «где фраза» ищет сам текст и подсвечивает его вдоль наклонных строк."""
    data = (samples / "sample_03.jpg").read_bytes()
    res = app.analyze(data, "sample_03.jpg", "Где на документе «государственный университет»?", None, document_first=True)
    a = res.answer
    assert res.doc_type is not None and a.verdict == "ok" and a.intent == "document" and a.debug["locate"]
    assert a.sources[0].ref == "found" and len(a.sources[0].boxes) == 2  # «Южно-Донской государственный университет» стоит в двух строках
    assert all(b.quad is not None for b in a.sources[0].boxes)  # снимок под углом: рамки идут вдоль строк


@needs_ocr
def test_the_whole_text_is_offered_for_a_known_document_type_too(app, samples):
    data = (samples / "sample_03.jpg").read_bytes()
    a = app.analyze(data, "sample_03.jpg", "Выведи весь текст документа", None, document_first=True).answer
    assert a.verdict == "ok" and a.debug["fulltext"] and "СПРАВКА" in a.text.upper()
    # проверка комплектности остаётся проверкой документа
    c = app.analyze(data, "sample_03.jpg", "Хватит ли этой справки для перевода на 2 курс?", None, document_first=True).answer
    assert c.intent == "completeness"


def test_the_count_is_declined_correctly():
    for n, word in [(2, "раза"), (3, "раза"), (4, "раза"), (5, "раз"), (11, "раз"), (12, "раз"), (21, "раз"), (22, "раза")]:
        a = qa().answer("Где в документе слово «договор»?", page("\n".join(["Договор"] * n)))  # n строк с одним словом
        assert a.debug["matches"] == n and f"встречается {n} {word}" in a.text, (n, a.text)  # при n > 8 подсвечены первые 8, но счёт полный


# ============================================================================ подсветка: подписи и подозрительные рамки
def test_a_short_word_never_gets_a_line_wide_box():
    """У слова из двух букв рамка «на всю строку» — сбой границ слова в OCR: такое место не подсвечивается."""
    pg = page(ORDER)[0]
    target = next(w for row in pg.rows for w in row.words if w.text == "по")
    target.x0, target.x1 = 20.0, 1150.0  # OCR «растянул» слово на всю строку
    a = qa().answer("покажи слово по", [pg])
    boxes = [b for s in a.sources for b in s.boxes]
    assert all((b.x1 - b.x0) < 400 for b in boxes), "растянутая рамка не должна попасть на снимок"


def test_a_normal_box_of_the_same_word_is_kept():
    a = qa().answer("покажи слово оповещения", page(ORDER))
    assert a.verdict == "ok" and len(a.sources[0].boxes) == 1 and (a.sources[0].boxes[0].x1 - a.sources[0].boxes[0].x0) < 200


def test_repeated_matches_are_numbered_and_labels_do_not_overlap():
    from dekanat.highlight import draw_overlay, overlay_items
    from dekanat.models import Answer, Box, Source

    boxes = [Box(100 + 60 * i, 200, 150 + 60 * i, 224, 0) for i in range(5)]  # пять мест вплотную: длинные одинаковые подписи слиплись бы
    ans = Answer("ok", "x", "document", [Source("document", "found", "найденное место", "x", boxes)])
    items = overlay_items([], ans)
    assert [label for _, label, _ in items] == ["1", "2", "3", "4", "5"]
    one = overlay_items([], Answer("ok", "x", "document", [Source("document", "found", "найденное место", "x", boxes[:1])]))
    assert [label for _, label, _ in one] == ["источник ответа"]  # одно место — понятная подпись, без номера
    img = draw_overlay(np.full((600, 800, 3), 255, np.uint8), items, 0)
    assert img.shape == (600, 800, 3)


def test_labels_are_not_stacked_on_each_other():
    from dekanat.highlight import PALETTE, draw_overlay
    from dekanat.models import Box

    base = np.full((600, 900, 3), 255, np.uint8)
    # два соседних слова с длинными подписями: без защиты плашки легли бы друг на друга
    items = [(Box(100, 200, 150, 224, 0), "источник ответа", PALETTE["answer"]), (Box(160, 200, 210, 224, 0), "источник ответа", PALETTE["answer"])]
    out = draw_overlay(base, items, 0)
    plate = np.array(PALETTE["answer"], np.uint8)[::-1]  # BGR
    mask = np.all(np.abs(out.astype(int) - plate.astype(int)) < 30, axis=2)
    ys, xs = np.where(mask[:200])  # плашки подписей лежат выше рамок
    # вторая плашка либо перенесена, либо не нарисована: ширина закрашенного над рамками не превышает одной подписи
    assert len(xs) == 0 or (xs.max() - xs.min()) < 400


# ============================================================================ «покажи РФ» — без слова «слово» и кавычек
@pytest.mark.parametrize("question, phrase", [
    ("покажи РФ", "РФ"), ("Покажи Главам", "Главам"), ("найди оповещения", "оповещения"), ("где Челябинская область", "Челябинская область"),
    ("покажи мне на снимке слово закона", "закона"), ("выдели системы оповещения", "системы оповещения"),
])
def test_a_bare_word_after_the_verb_is_the_phrase(question, phrase):
    assert DocumentQA.bare_phrase(question) == phrase


@pytest.mark.parametrize("question", [
    "покажи дату", "покажи весь текст", "покажи текст", "покажи, что в документе", "найди ошибки", "Хватит ли справки для перевода?",
    "покажи сколько стоит обучение", "найди срок оплаты", "покажи", "где",
])
def test_questions_about_meaning_are_not_phrases(question):
    assert DocumentQA.bare_phrase(question) is None


def test_bare_show_finds_the_word_on_the_page():
    a = qa().answer("покажи оповещения", page(ORDER))
    assert a.verdict == "ok" and a.debug.get("locate") and a.debug.get("exact")
    assert len(a.sources[0].boxes) == 1 and "оповещения" in a.text


def test_bare_show_of_a_word_that_is_absent_falls_back_instead_of_a_wrong_claim():
    """Догадка о фразе («покажи X») не должна подменять обычный ответ: нет такого слова — отвечаем как раньше, а не «фразы не нашлось»."""
    a = qa().answer("покажи абракадабру", page(ORDER))
    assert not a.debug.get("locate")
    explicit = qa().answer("покажи слово абракадабру", page(ORDER))  # а явная просьба «слово …» по-прежнему получает честное «не нашлось»
    assert explicit.debug.get("locate") and "не нашлось" in explicit.text


def test_show_field_questions_on_known_documents_still_mean_the_field(samples):
    """У документа известного типа «покажи ФИО» — поле карточки (проверяется в test_document_first); поиск фразы туда не вмешивается."""
    from dekanat.qa.engine import QAEngine

    assert DocumentQA.phrase_of("покажи ФИО") is None  # явной фразы нет — маршрутизация по полю не меняется


def test_answer_items_are_only_the_asked_places_with_short_labels():
    from dekanat.highlight import PALETTE, answer_items
    from dekanat.models import Answer, Box, Source

    stamp = Box(300, 600, 560, 860, 0)
    one = answer_items(Answer("ok", "x", "document", [Source("field", "signature_seal.seal", "поле «Подпись и печать» (печать вуза)", "", [stamp])]))
    assert [(b, label, color) for b, label, color in one] == [(stamp, "печать вуза", PALETTE["answer"])]  # короткая подпись, только то, о чём спросили
    named = answer_items(Answer("ok", "x", "document", [Source("field", "issue_date", "поле «Дата выдачи»", "", [stamp])]))
    assert [label for _, label, _ in named] == ["дата выдачи"]
    many = answer_items(Answer("ok", "x", "document", [Source("document", "found", "найденное место", "", [Box(10, 10, 60, 30, 0), Box(100, 10, 150, 30, 0)])]))
    assert [label for _, label, _ in many] == ["1", "2"]
    assert answer_items(Answer("ok", "x", "document", [Source("clause", "3.4", "п. 3.4", "", [])])) == []  # пункт Положения на снимке не рисуется

