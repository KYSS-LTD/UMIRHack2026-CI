"""Вопрос по тексту произвольного документа (счёт, акт, таблица, письмо — не только справки вуза).

Применяется, когда тип документа не определился или выбран режим «Другой документ». Ответ — только то, что есть в
распознанном тексте: строка или ячейка таблицы, подходящая под вопрос, и значение после метки («ИНН: 6100123456»).
Источник — фрагмент документа с рамкой на снимке. Если подходящего места нет — честно говорим, что ответа нет.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from ..extract.values import find_dates, parse_person
from ..kb.index import ClauseIndex
from ..models import Answer, Box, Clause, OcrPage, Row, Source
from ..textutil import clean_value, is_blank_value, normalize, sentences, stem, tokenize

_DATE_Q = re.compile(r"\b(когда|дата|даты|число|числа|какого числа|срок|до какого|с какого)\b")
_MONEY_Q = re.compile(r"\b(сколько|сумма|суммы|итого|стоимость|цена|цену|всего|количество|кол-во|налог|ндс|скидка)\b")
_ID_Q = re.compile(r"\b(номер|№|инн|кпп|огрн|бик|счёт|счет|индекс|артикул|код)\b")
_WHO_Q = re.compile(r"\b(кто|кого|фио|фамилия|имя|получатель|плательщик|директор|руководитель)\b")
_NUMBER = re.compile(r"(?<![\w.])(?:(?:не\s+(?:менее|более|ранее|позднее)|около|порядка|свыше|от|до)\s+)?\d[\d\s]*(?:[.,]\d+)?(?:\s?(?:руб\w*|₽|р\.|%|шт\w*\.?|кг|г|м|км|чел\w*\.?|дн\w*|мес\w*|лет|год\w*))?", re.IGNORECASE)
_SEP = re.compile(r"\s*[:：–—-]\s+|\s{2,}")

# «Где на документе «предмет договора»?», «Покажи, где написано слово «итого»», «найди слово ИНН»: искомое место, а не смысл вопроса
_LOCATE_VERB = r"(?:где|покаж\w+|показ\w+|найд\w+|найти|выдел\w+|подсвет\w+|отмет\w+)"
_LOCATE_CUE = re.compile(rf"\b{_LOCATE_VERB}\b")
_QUOTED = re.compile(r"[«“\"]([^«»“”\"]{2,120})[»”\"]")
_BARE_PHRASE = re.compile(rf"\b{_LOCATE_VERB}\b[^?!\n]{{0,40}}?\b(?:слово|слова|фразу|словосочетание|надпись|выражение|аббревиатуру)\s+([^\n?!]{{2,80}}?)\s*[?!.]*\s*$", re.IGNORECASE)
# вопрос о значении рядом с цитатой («какая сумма в строке «Итого»?», «покажи значение «ИНН»») — это поиск по смыслу, а не «где слово»
_VALUE_ASK = re.compile(r"\b(?:значени\w+|сколько|как(?:ой|ая|ое|ие|им|ого|ую|ому)|чему|кто|кого|что|сумм\w+|цен\w+|стоимост\w+|номер\w*|дат\w+|срок\w*|размер\w*)\b")
MAX_FOUND = 8
# «Выведи весь текст документа», «распознай текст с картинки», «оттекстовка»: нужен текст целиком, а не поиск по смыслу
_FULLTEXT_Q = re.compile(r"\b(?:весь|целиком|полностью|полный|распознанн\w+)\s+текст\b|\bоттекстовк\w*|\bтекст\w*\s+(?:документа|с\s+(?:этого\s+|данного\s+)?(?:документа|изображени\w+|снимка|фото\w*|картинк\w+|страниц\w+|скана))|\b(?:распознай|прочитай|прочти|экспортируй|транскрибируй)\b[^.?!]{0,40}\b(?:текст|документ|изображени\w+|картинк\w+|фото\w*|снимок)\b")
_NOT_FULLTEXT = re.compile(r"\b(?:пункт\w*|подпункт\w*|абзац\w*|раздел\w*|строк\w*|последн\w+|первы\w+|только|заголов\w+|таблиц\w+|ячейк\w+|слов\w*|цифр\w+|номер\w*|дат\w+)\b")
FULLTEXT_SHOWN = 1500


@dataclass
class Unit:
    page: int
    row: int
    segments: list[tuple[str, list]]  # (текст ячейки, слова)
    text: str
    y0: float = 0.0
    y1: float = 0.0

    @property
    def words(self) -> list:
        return [w for _, ws in self.segments for w in ws]


@dataclass
class Item:
    """Нумерованный пункт самого документа («1.2. принять меры…»), включая строки-продолжения."""

    number: str
    page: int
    units: list[Unit]

    @property
    def text(self) -> str:
        return " ".join(u.text for u in self.units)


_NUM_START = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})$")
_ITEM_REF = re.compile(r"\b(?:п\.?|пункт\w*|подпункт\w*)\s*(\d{1,2}(?:\.\d{1,2}){0,3})")
_ONE_LIKE = frozenset({"l", "I", "|", "!", "le", "Ie", "1e", "1е", "Iе", "lе", "l1", "I1"})
_OVERVIEW_Q = re.compile(r"\b(?:о чем|о чём|что за|что это за|что это|тема|суть|кратко|название|заголовок|перескажи\w*|содержани\w*)\b")
_KIND = re.compile(r"^(постановлени|приказ|распоряжени|заявлени|справк|договор|сч[её]т|акт|письм|решени|протокол|уведомлени|отзыв|характеристик|доверенност|инструкци|положени|предписани|извещени|резолюци|объявлени)")
_HEADER_NUMBER = re.compile(r"(?:№|N[°oº]?\.?)\s*([\w\-/]{1,20})")


def _item_number(first: str, prev: Optional[str]) -> Optional[str]:
    """Номер пункта из первого слова строки с поправкой на типичные ошибки OCR: «11.» вместо «1.1.», «12:» вместо «1.2.», «le» вместо «1.»."""
    t = first.strip(".:;,)(")
    if t in _ONE_LIKE:
        return "1"
    slash = re.fullmatch(r"(\d{1,2})[/\\](\d{1,2})", t)
    if slash:
        return f"{slash.group(1)}.{slash.group(2)}"
    if re.fullmatch(r"\d{2}", t) and prev and (prev == t[0] or prev.startswith(t[0] + ".")):
        return f"{t[0]}.{t[1]}"
    m = _NUM_START.match(t)
    return m.group(1) if m and not re.match(r"^\d{3,}", t) else None


def _segments(row: Row) -> list[tuple[str, list]]:
    """Делит строку на ячейки по большим горизонтальным разрывам (таблицы «метка | значение»)."""
    mh = statistics.median(w.h for w in row.words) or 10
    segs: list[list] = [[row.words[0]]]
    for prev, w in zip(row.words, row.words[1:]):
        if w.x0 - prev.x1 > 1.8 * mh:
            segs.append([w])
        else:
            segs[-1].append(w)
    return [(" ".join(w.text for w in s), s) for s in segs]


def _typed_value(question_n: str, text: str) -> Optional[str]:
    """Достаёт из фрагмента то, о чём спрашивают: дату, число с единицей, ФИО, идентификатор."""
    if _DATE_Q.search(question_n):
        d = find_dates(text)
        if d:
            return d[0].dmy
    if _WHO_Q.search(question_n):
        person = _person(text)
        if person:
            return person
    if _ID_Q.search(question_n):
        m = re.search(r"(?:№|N)?\s*([A-Za-zА-Яа-я]{0,3}[-/]?\d[\d\-/]{3,})", text)
        if m:
            return m.group(1).strip()
    if _MONEY_Q.search(question_n):
        nums = [m.group(0).strip() for m in _NUMBER.finditer(text) if re.search(r"\d", m.group(0)) and len(re.sub(r"\D", "", m.group(0))) >= 1]
        if nums:
            return nums[-1]  # в строках вида «Итого: 15 400,00 руб.» значение — последнее
    return None


_PERSON = re.compile(r"(?<![\w-])([А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{3,}(?:вич|вна|ична|ич|оглы|кызы))?|[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.)(?![\w-])")
_NOT_PERSON = frozenset("общество ответственностью индивидуальный предприниматель федеральное государственное бюджетное акционерное публичное".split())


def _person(text: str) -> Optional[str]:
    """ФИО в тексте: «Фамилия Имя [Отчество]» или «Фамилия И.О.» (организации и должности не считаются)."""
    for m in _PERSON.finditer(text):
        words = m.group(1).lower().split()
        if not any(w in _NOT_PERSON for w in words):
            return m.group(1)
    return None


# слова вопроса, которые сами по себе ничего не ищут («что в документе?», «что написано?»)
GENERIC_WORDS = frozenset("документ документе документа документы документов написано написана сказано указано указан содержится есть текст файл снимок".split())


def _prep(text: str) -> str:
    """«№ 125» -> «номер 125»: знак номера иначе теряется при токенизации."""
    return re.sub(r"(?:№|N[°oº]?\.?)\s*(?=\d)", " номер ", text)


class DocumentQA:
    def __init__(self, groups: Optional[list[set[str]]] = None, min_coverage: float = 0.5):
        self.groups = groups or []
        self.min_coverage = min_coverage

    def answer(self, question: str, pages: list[OcrPage]) -> Answer:
        units = self._units(pages)
        if not units:
            return Answer("no_answer", "В документе не распознан текст — отвечать не по чему. Сфотографируйте документ ровнее и при хорошем освещении.", "document", confidence=1.0)
        q = question.strip()
        items = self._items(units)
        direct = _ITEM_REF.search(q.lower())
        if direct:
            wanted = next((it for it in items if it.number == direct.group(1)), None)
            if wanted is not None:
                return self._item_answer(wanted, pages, 1.0, [])
        found = self._locate(q, pages)
        if found is not None:
            return found
        whole = self._whole_text(q, units)
        if whole is not None:
            return whole
        by_number = {f"{u.page}:{u.row}": u for u in units}
        clauses = [Clause(f"{u.page}:{u.row}", _prep(u.text), [], False) for u in units]
        # окна из двух строк: метка на одной строке, значение на следующей
        for a, b in zip(units, units[1:]):
            if a.page == b.page:
                clauses.append(Clause(f"{a.page}:{a.row}+", _prep(f"{a.text} {b.text}"), [], False))
        item_by_key = {f"i{it.page}:{it.number}": it for it in items}
        for key, it in item_by_key.items():
            clauses.append(Clause(key, _prep(it.text), [], False))
        index = ClauseIndex(clauses, self.groups)
        q = _prep(q)
        qterms = index.query_terms(q, extra_drop=GENERIC_WORDS)
        qlow = q.lower()
        if not qterms or (_OVERVIEW_Q.search(qlow) and not index.query_terms(_OVERVIEW_Q.sub(" ", qlow), extra_drop=GENERIC_WORDS)):
            return self._overview(units, pages)
        # «Какая дата документа?», «Какой номер?» без уточнения — это реквизиты в шапке, а не поиск по смыслу
        bare = not index.query_terms(_ID_Q.sub(" ", _DATE_Q.sub(" ", qlow)), extra_drop=GENERIC_WORDS)
        if bare and (_DATE_Q.search(qlow) or _ID_Q.search(qlow)):
            header = self._header_requisite(qlow, units, pages)
            if header is not None:
                return header
            if "документ" in qlow:
                return self._no_requisite(qlow, units)
        lookup = {**by_number, **{k: it.units[0] for k, it in item_by_key.items()}}
        hits = self._rerank(index.search(q, 8, qterms), q, lookup)
        if not hits or hits[0].coverage < self.min_coverage:
            top = hits[0] if hits else None
            return Answer("no_answer", "В тексте документа не нашлось ответа на этот вопрос. Попробуйте переформулировать его словами, которые есть в документе.", "document",
                          confidence=0.7, debug={"coverage": round(top.coverage, 2) if top else 0.0})
        best = hits[0]
        if best.clause.number in item_by_key:
            others = [h for h in hits[1:] if h.coverage >= max(self.min_coverage, best.coverage - 0.15) and h.clause.number in item_by_key][:2]
            return self._item_answer(item_by_key[best.clause.number], pages, best.coverage, [item_by_key[h.clause.number] for h in others], qterms, index, best.score)
        key = best.clause.number.rstrip("+")
        unit = by_number[key]
        qn = normalize(q)
        value, seg_words = self._value(index, qterms, unit, units, by_number, qn)
        if not value:
            # совпала одна строка нумерованного пункта — отвечаем пунктом целиком и с его номером
            owner = next((it for it in items if any(u is unit for u in it.units)), None)
            if owner is not None:
                return self._item_answer(owner, pages, best.coverage, [], qterms, index, best.score)
        quote = best.clause.text if best.clause.number.endswith("+") else unit.text
        shown = clean_value(value) if value else clean_value(quote)
        if len(shown) > 220:
            shown = shown[:217].rstrip() + "…"
        text = f"{shown}" if value else f"В документе написано: «{shown}»"
        words = seg_words or unit.segments and [w for _, ws in unit.segments for w in ws]
        page = next(p for p in pages if p.index == unit.page)
        merged = self._merge(page, words) if words else []
        src = Source("document", best.clause.number, "фрагмент документа" + (f" (стр. {unit.page + 1})" if len(pages) > 1 else ""), quote, merged)
        related = []
        for h in hits[1:3]:
            if h.coverage >= max(self.min_coverage, best.coverage - 0.15) and h.clause.number.rstrip("+") != key:
                u2 = lookup[h.clause.number.rstrip("+")]
                related.append(Source("document", h.clause.number, "ещё фрагмент", u2.text, []))
        return Answer("ok", text, "document", [src], related=related, confidence=min(0.95, 0.45 + best.coverage / 2),
                      debug={"coverage": round(best.coverage, 2), "score": round(best.score, 2)})

    # ------------------------------------------------------------------ вспомогательное
    @staticmethod
    def _rerank(hits: list, question: str, by_number: dict) -> list:
        """Если спрашивают дату, сумму, номер или имя — сначала строки, где такое значение есть."""
        qn = normalize(question)
        wanted = []
        if _DATE_Q.search(qn):
            wanted.append(lambda t: bool(find_dates(t)))
        if _MONEY_Q.search(qn):
            wanted.append(lambda t: bool(re.search(r"\d[\d\s]*[.,]?\d*\s?(?:руб|₽|р\.|%|шт|кг)", t, re.IGNORECASE)))
        if _ID_Q.search(qn):
            wanted.append(lambda t: bool(re.search(r"\d{4,}", t)))
        if _WHO_Q.search(qn):
            wanted.append(lambda t: _person(t) is not None)
        if not wanted:
            return hits
        pref = [h for h in hits if any(w(by_number[h.clause.number.rstrip("+")].text) for w in wanted)]
        return pref + [h for h in hits if h not in pref] if pref else hits

    @staticmethod
    def _units(pages: list[OcrPage]) -> list[Unit]:
        out = []
        for p in pages:
            for i, row in enumerate(p.rows):
                if row.words and not is_blank_value(row.text) and len(re.sub(r"\W", "", row.text)) >= 2:
                    out.append(Unit(p.index, i, _segments(row), row.text, row.y0, row.y1))
        return out

    @staticmethod
    def _items(units: list[Unit]) -> list[Item]:
        """Собирает нумерованные пункты: строка, начинающаяся с «1.2.», и следующие за ней строки до очередного номера."""
        items: list[Item] = []
        cur: Optional[Item] = None
        prev: Optional[Unit] = None
        for u in units:
            first = u.segments[0][0].split()[0] if u.segments and u.segments[0][0].split() else ""
            number = _item_number(first, cur.number if cur else (items[-1].number if items else None))
            height = max(8.0, u.y1 - u.y0)
            if number and (len(u.segments) > 1 or len(u.text.split()) > 1):
                cur = Item(number, u.page, [u])
                items.append(cur)
            elif cur is not None and prev is not None and u.page == cur.page and u.y0 - prev.y1 < 1.6 * height and len(u.text) > 2:
                cur.units.append(u)
            else:
                cur = None
            prev = u
        return items

    def _value(self, index: ClauseIndex, qterms: list[str], unit: Unit, units: list[Unit], by_number: dict, qn: str):
        """Значение, которое стоит рядом с найденной меткой: ячейка справа, часть после двоеточия, строка ниже."""
        stems_q = set(qterms)

        def score(text: str) -> float:
            toks = set(index.query_terms(text))
            return sum(1.0 for t in stems_q if index.variants(t) & toks)

        # 1. таблица: ячейка с меткой -> соседняя справа
        if len(unit.segments) > 1:
            scores = [score(t) for t, _ in unit.segments]
            k = max(range(len(scores)), key=lambda i: scores[i])
            if scores[k] > 0 and k + 1 < len(unit.segments):
                cell_text, words = unit.segments[k + 1]
                typed = _typed_value(qn, cell_text)
                return (typed or cell_text), words
        # 2. «метка: значение»
        parts = re.split(_SEP, unit.text, maxsplit=1)
        if len(parts) == 2 and score(parts[0]) >= score(parts[1]) and parts[1].strip():
            value = parts[1].strip()
            typed = _typed_value(qn, value)
            words = [w for _, ws in unit.segments for w in ws]
            return (typed or value), words
        # 3. значение в следующей строке
        if score(unit.text) > 0 and unit.text.rstrip().endswith(":"):
            nxt = by_number.get(f"{unit.page}:{unit.row + 1}")
            if nxt:
                typed = _typed_value(qn, nxt.text)
                return (typed or nxt.text), [w for _, ws in nxt.segments for w in ws]
        # 4. типизированное значение из самой строки
        typed = _typed_value(qn, unit.text)
        if typed:
            return typed, [w for _, ws in unit.segments for w in ws]
        return None, []

    def _item_answer(self, item: Item, pages: list[OcrPage], coverage: float, others: list[Item], qterms: Optional[list[str]] = None,
                     index: Optional[ClauseIndex] = None, score: float = 0.0) -> Answer:
        text = clean_value(item.text)
        first, _, rest = text.partition(" ")
        if rest and _item_number(first, None) is not None or first.strip(".:;,)(") in _ONE_LIKE or re.fullmatch(r"\d{2}[.:;]?", first):
            text = rest.strip() or text
        shown = text
        if len(text) > 320 and index is not None and qterms:
            sents = sentences(text)
            scored = sorted(((sum(index.idf(t) for t in qterms if index.variants(t) & {stem(w) for w in tokenize(sn, drop_stop=True)}), i, sn) for i, sn in enumerate(sents)), reverse=True)[:2]
            shown = " ".join(sn for _, _, sn in sorted(scored, key=lambda x: x[1])) or text
        if len(shown) > 420:
            shown = shown[:417].rstrip() + "…"
        page = next(p for p in pages if p.index == item.page)
        boxes = self._merge(page, [w for u in item.units for w in u.words])
        label = f"п. {item.number} документа" + (f" (стр. {item.page + 1})" if len(pages) > 1 else "")
        src = Source("document", f"i{item.page}:{item.number}", label, text, boxes)
        related = [Source("document", f"i{o.page}:{o.number}", f"п. {o.number} документа", clean_value(o.text), []) for o in others]
        return Answer("ok", f"{shown} ({label})", "document", [src], related=related, confidence=min(0.95, 0.45 + coverage / 2),
                      debug={"coverage": round(coverage, 2), "score": round(score, 2), "item": item.number})

    @staticmethod
    def _no_requisite(qlow: str, units: list[Unit]) -> Answer:
        """Дата или номер в шапке не прочитаны (чаще всего они вписаны от руки): говорим об этом, а не подсовываем случайную строку."""
        if _DATE_Q.search(qlow):
            text = "Дату документа в шапке прочитать не удалось: возможно, она написана от руки, а рукописный текст сервис читает плохо. Сверьтесь со снимком."
            others = []
            for u in units:
                others += [d.dmy for d in find_dates(u.text)]
            if others:
                text += f" В тексте встречаются другие даты ({', '.join(dict.fromkeys(others[:3]))}), но это не обязательно дата самого документа."
        else:
            text = "Номер документа в шапке прочитать не удалось: возможно, он написан от руки. Сверьтесь со снимком."
        return Answer("no_answer", text, "document", confidence=0.6, debug={"requisite": True})

    def _header_requisite(self, qlow: str, units: list[Unit], pages: list[OcrPage]) -> Optional[Answer]:
        """Дата или номер документа из первых строк (шапка): там они стоят у приказов, постановлений, писем."""
        head: list[Unit] = []
        for u in units[:12]:
            if len(u.text.split()) >= 8 and head:  # дальше пошёл основной текст: даты оттуда (ссылки на законы) — не дата документа
                break
            head.append(u)
        for u in head:
            value = None
            note = ""
            if _DATE_Q.search(qlow):
                found = find_dates(u.text)
                value = found[0].dmy if found else None
                if not value:
                    m = re.search(r"(?<!\d)\d{2}[.,]\d{2}[.,](?:19|20)\d{2}(?!\d)", u.text)
                    if m:
                        value, note = m.group(0), " (цифры распознаны неуверенно — сверьте со снимком)"
                what = "Дата документа"
            else:
                m = _HEADER_NUMBER.search(u.text)
                value = m.group(1).strip(".,;:") if m and re.search(r"\d", m.group(1)) else None
                what = "Номер документа"
            if value:
                page = next(p for p in pages if p.index == u.page)
                boxes = self._merge(page, u.words)
                src = Source("document", f"{u.page}:{u.row}", "шапка документа", u.text, boxes)
                return Answer("ok", f"{what}: {value}{note}", "document", [src], confidence=0.7 if not note else 0.4, debug={"requisite": True})
        return None

    @staticmethod
    def phrase_of(question: str) -> Optional[str]:
        """Искомая фраза из вопроса: цитата в кавычках или «слово X» после «где / покажи / найди»; иначе None."""
        low = question.lower()
        if not _LOCATE_CUE.search(low):
            return None
        quoted = _QUOTED.findall(question)
        if quoted:
            rest = _QUOTED.sub(" ", low)
            if re.search(r"\bгде\b", rest) or not _VALUE_ASK.search(rest):
                return quoted[0].strip()
            return None
        m = _BARE_PHRASE.search(question.strip())
        return m.group(1).strip(" «»“”\"'.,") if m else None

    @staticmethod
    def _tok(text: str) -> str:
        return re.sub(r"[^\w]+", "", text.lower().replace("ё", "е"))

    def _locate(self, question: str, pages: list[OcrPage]) -> Optional[Answer]:
        """«Где на документе «фраза»?» -> рамки найденных слов на снимке. Слова ищутся в порядке чтения, точно или с запасом на опечатки OCR."""
        phrase = self.phrase_of(question)
        if not phrase:
            return None
        target = [t for t in (self._tok(w) for w in phrase.split()) if t]
        if not target:
            return None
        hits: list[tuple[OcrPage, list, float, int]] = []  # (страница, слова, сходство, позиция)
        for page in pages:
            words = [w for row in page.rows for w in row.words]
            toks = [self._tok(w.text) for w in words]
            n = len(target)
            i = 0
            while i <= len(words) - n:
                chunk = toks[i:i + n]
                if chunk == target:
                    hits.append((page, words[i:i + n], 100.0, i))
                    i += n
                else:
                    i += 1
        exact = bool(hits)
        if not hits:  # опечатки OCR: сравниваем склеенную фразу с окном слов
            joined = "".join(target)
            for page in pages:
                words = [w for row in page.rows for w in row.words]
                toks = [self._tok(w.text) for w in words]
                n = len(target)
                best: list[tuple[float, int, int]] = []
                for size in {max(1, n - 1), n, n + 1}:
                    for i in range(0, max(1, len(words) - size + 1)):
                        chunk = "".join(toks[i:i + size])
                        if len(chunk) < 3:
                            continue
                        sc = fuzz.ratio(joined, chunk)
                        if sc >= (84 if len(joined) >= 6 else 90):
                            best.append((sc, i, size))
                best.sort(reverse=True)
                taken: list[tuple[int, int]] = []
                for sc, i, size in best:
                    if all(i + size <= a or i >= b for a, b in taken):
                        taken.append((i, i + size))
                        hits.append((page, words[i:i + size], sc, i))
        label = f"«{phrase}»"
        if not hits:
            return Answer("no_answer", f"Фразы {label} в распознанном тексте документа не нашлось. Проверьте написание или попробуйте часть фразы; возможно, этот фрагмент плохо читается на снимке.", "document",
                          confidence=0.7, debug={"locate": True, "matches": 0})
        hits.sort(key=lambda h: (-h[2], h[0].index, h[3]))
        shown = sorted(hits[:MAX_FOUND], key=lambda h: (h[0].index, h[3]))
        boxes: list[Box] = []
        for page, ws, _, _ in shown:
            boxes += self._merge(page, ws)
        first_page, first_words, score, _ = shown[0]
        row_text = next((r.text for r in first_page.rows if first_words[0] in r.words), "")
        where = f" на странице {first_page.index + 1}" if len(pages) > 1 else ""
        if len(hits) == 1:
            text = f"{label} — найдено{where}" + (f", строка: «{clean_value(row_text)[:160]}»" if row_text else "")
        else:
            more = "" if len(hits) <= MAX_FOUND else f" (показаны первые {MAX_FOUND})"
            raz = "раза" if len(hits) % 10 in (2, 3, 4) and len(hits) % 100 not in (12, 13, 14) else "раз"
            text = f"{label} встречается {len(hits)} {raz}{more}; все места подсвечены на снимке."
        if not exact:
            text += " Точного совпадения нет — показано самое похожее место (другая форма слова или неточно прочитанный текст); сверьте со снимком."
        src = Source("document", "found", "найденное место", row_text or phrase, boxes)
        return Answer("ok", text, "document", [src], confidence=0.9 if exact else 0.55, debug={"locate": True, "matches": len(hits), "exact": exact})

    @staticmethod
    def wants_whole_text(question: str) -> bool:
        """«Выведи весь текст документа», «распознай текст с картинки»: нужен текст целиком, а не его часть."""
        low = question.lower()
        return bool(_FULLTEXT_Q.search(low)) and not _NOT_FULLTEXT.search(_FULLTEXT_Q.sub(" ", low))

    @staticmethod
    def _whole_text(question: str, units: list[Unit]) -> Optional[Answer]:
        """«Выведи весь текст документа» -> распознанный текст (начало; целиком — в панели «Распознанный текст» на столе)."""
        if not DocumentQA.wants_whole_text(question):
            return None
        text = "\n".join(u.text for u in units)
        shown = text if len(text) <= FULLTEXT_SHOWN else text[:FULLTEXT_SHOWN].rsplit(" ", 1)[0].rstrip() + "…"
        tail = "" if len(text) <= FULLTEXT_SHOWN else f"\n\nПоказано начало ({len(shown)} из {len(text)} знаков). Весь текст можно открыть, скопировать или скачать кнопкой «Текст» под снимком."
        return Answer("ok", "Распознанный текст документа:\n" + shown + tail, "document", [Source("document", "fulltext", "весь текст документа", shown, [])], confidence=0.6,
                      debug={"fulltext": True, "chars": len(text)})

    @staticmethod
    def _merge(page: OcrPage, words: list) -> list[Box]:
        """Слова одной строки -> одна рамка. Слова объединяются в координатах выпрямленного листа и переносятся на снимок один раз:
        на наклонном снимке рамка идёт вдоль строки, а не раздувается до осевого прямоугольника."""
        # строку определяет сама строка OCR: округление высоты «центр / высота слова» на перспективном фото раскалывает одну строку надвое
        row_of = {id(w): i for i, row in enumerate(page.rows) for w in row.words}
        rows: dict = {}
        for w in words:
            key = row_of.get(id(w))
            if key is None:
                key = ("y", round((w.y0 + w.y1) / 2 / max(w.y1 - w.y0, 1)))
            rows.setdefault(key, []).append(w)
        return [page.map_box(min(w.x0 for w in ws), min(w.y0 for w in ws), max(w.x1 for w in ws), max(w.y1 for w in ws)) for ws in rows.values()]

    @staticmethod
    def _kind_title(units: list[Unit]) -> Optional[str]:
        """Вид документа из шапки: известное слово («ПОСТАНОВЛЕНИЕ», «ПРИКАЗ») или самая крупная строка заглавными буквами."""
        best: Optional[Unit] = None
        for u in units[:14]:
            compact = re.sub(r"\s+", "", u.text)
            if _KIND.match(compact.lower()) and len(u.text.split()) <= 4:
                return u.text.strip(" .,:;“”\"'-—")
            letters = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", u.text)
            if len(letters) >= 6 and letters.upper() == letters and len(u.text.split()) <= 3 and (best is None or u.y1 - u.y0 > best.y1 - best.y0):
                best = u
        return best.text.strip(" .,:;“”\"'-—") if best else None

    @staticmethod
    def _overview(units: list[Unit], pages: list[OcrPage]) -> Answer:
        """«Что в документе?» — вид документа и строка-тема («О…», «Об…») с продолжением; иначе начало текста."""
        kind = DocumentQA._kind_title(units)
        subject: list[str] = []
        for i, u in enumerate(units[:30]):
            if re.match(r"^(О|Об|О\s+том)\s+\S", u.text):
                # тема заканчивается там, где начинается абзац с отступом: берём строки, пока вертикальный шаг обычный
                taken = [u.text]
                for prev, nxt in zip(units[i:i + 6], units[i + 1:i + 7]):
                    if nxt.y0 - prev.y1 > 1.4 * max(8.0, prev.y1 - prev.y0) or nxt.page != prev.page:
                        break
                    taken.append(nxt.text)
                subject = taken
                break
        parts = [*([kind] if kind else []), *([" ".join(subject)] if subject else [])]
        if not parts:
            parts = [u.text for u in units[:4]]
        quote = "\n".join(u.text for u in units[:10])
        return Answer("ok", "Начало документа: " + " — ".join(parts), "document", [Source("document", "overview", "начало документа", quote, [])], confidence=0.5)
