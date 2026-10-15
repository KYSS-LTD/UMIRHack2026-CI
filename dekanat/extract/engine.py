"""Заполнение карточки полей по OCR-тексту.

Для каждого поля пробуем по очереди:
  1. метка («Дата выдачи: ...», значение справа или строкой ниже) — для форм и таблиц;
  2. регулярные выражения из конфига (для документов, написанных связным текстом);
  3. запасные детекторы (название вуза в шапке, «ничейная» дата внизу листа).
Подпись и печать определяются по изображению (marks.py).
"""

from __future__ import annotations

import bisect
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from ..config import DocTypeSpec, FieldSpec, PartSpec, expand_macros
from ..models import Box, FieldResult, OcrPage, PartResult, Row, Word
from ..textutil import is_blank_value
from .labels import LabelHit, compile_labels, find_hits, row_median_height
from .marks import MarkDetection, detect_marks
from .values import Parsed, find_dates, parse_value, repair_text

_SEP_ONLY = re.compile(r"^[:;\-–—.,|_]+$")
_ORG_WORDS = re.compile(r"университет|институт|академи|колледж|техникум|училищ|\bвуз\b|филиал|\bооо\b|\bао\b|\bпао\b|\bип\b|школ|лице|больниц|центр|предприяти|компани", re.IGNORECASE)
_TITLE_ROW = re.compile(r"^\s*(справка|заявление|отзыв|характеристика)\b", re.IGNORECASE)
_STOP_CONT = re.compile(r"подпис|декан|руководител|м\.\s?п|^\(|\bдата\b|№", re.IGNORECASE)


@dataclass
class Cand:
    value: str
    normalized: Any
    words: list[Word]
    page: int
    method: str
    factor: float = 1.0
    evidence: str = ""


@dataclass
class PageCtx:
    page: OcrPage
    rows: list[Row]
    hits: list[LabelHit]
    by_row: dict[int, list[LabelHit]]
    text: str
    starts: list[int]
    spans: list[tuple[int, int, Word]]
    word_row: dict[int, int]
    right_edge: float = 0.0
    marks: Optional[dict] = None  # результат поиска подписи/печати (лениво, на страницу)
    same_row_style: bool = False  # значения стоят в одной строке с метками (форма/таблица)


def _join(words: list[Word]) -> tuple[str, list[int]]:
    parts, offsets, pos = [], [], 0
    for w in words:
        offsets.append(pos)
        parts.append(w.text)
        pos += len(w.text) + 1
    return " ".join(parts), offsets


class FieldExtractor:
    def __init__(self, doctype: DocTypeSpec, marks_enabled: bool = True):
        self.dt = doctype
        self.marks_enabled = marks_enabled
        self.labels = compile_labels(doctype)
        self._rx: dict[tuple, list[re.Pattern]] = {}
        for f in doctype.fields:
            self._rx[(f.id, None)] = [re.compile(expand_macros(p)) for p in f.patterns]
            for p in f.parts:
                self._rx[(f.id, p.id)] = [re.compile(expand_macros(x)) for x in p.patterns]

    # ------------------------------------------------------------------ контекст страницы
    def _ctx(self, page: OcrPage) -> PageCtx:
        rows = page.rows
        hits = find_hits(rows, self.labels, page.index)
        by_row: dict[int, list[LabelHit]] = {}
        for h in hits:
            by_row.setdefault(h.row, []).append(h)
        pieces, spans, word_row, pos = [], [], {}, 0
        for ri, row in enumerate(rows):
            for w in row.words:
                pieces.append(w.text)
                spans.append((pos, pos + len(w.text), w))
                word_row[id(w)] = ri
                pos += len(w.text) + 1
        text = repair_text(" ".join(pieces))
        starts = [s for s, _, _ in spans]
        right = max((r.x1 for r in rows), default=0.0)
        ctx = PageCtx(page, rows, hits, by_row, text, starts, spans, word_row, right)
        filled = sum(1 for h in hits if self._remainder(ctx, h))
        ctx.same_row_style = bool(hits) and filled / len(hits) >= 0.6
        return ctx

    @staticmethod
    def _words_in_span(ctx: PageCtx, start: int, end: int) -> list[Word]:
        i = max(0, bisect.bisect_right(ctx.starts, start) - 1)
        out = []
        while i < len(ctx.spans) and ctx.spans[i][0] < end:
            s, e, w = ctx.spans[i]
            if e > start:
                out.append(w)
            i += 1
        return out

    # ------------------------------------------------------------------ значения «справа от метки»
    def _remainder(self, ctx: PageCtx, hit: LabelHit) -> list[Word]:
        row = ctx.rows[hit.row]
        nxt = next((h for h in ctx.by_row.get(hit.row, []) if h.w0 >= hit.w1), None)
        stop = nxt.w0 if nxt else len(row.words)
        words = list(row.words[hit.w1:stop])
        while words and _SEP_ONLY.match(words[0].text):
            words.pop(0)
        if words and words[0].text.startswith((":", ";")):
            first = words[0]
            words[0] = Word(first.text.lstrip(":;"), first.conf, first.x0, first.y0, first.x1, first.y1)
            if not words[0].text:
                words.pop(0)
        return words

    def _below(self, ctx: PageCtx, hit: LabelHit) -> list[Word]:
        if ctx.same_row_style:
            return []  # пустое значение в форме — это «не заполнено», а не значение из соседней строки
        ri = hit.row + 1
        if ri >= len(ctx.rows):
            return []
        row, cur = ctx.rows[ri], ctx.rows[hit.row]
        if row.y0 - cur.y1 > 2.3 * row_median_height(cur):
            return []
        hs = ctx.by_row.get(ri, [])
        if hs and hs[0].w0 == 0:
            return []
        stop = hs[0].w0 if hs else len(row.words)
        return list(row.words[:stop])

    def _continuation(self, ctx: PageCtx, ri: int, x_left: float) -> list[Word]:
        """Строки-продолжения многострочного значения: перенос у правого края или выравнивание по колонке значения."""
        out: list[Word] = []
        for _ in range(2):
            if ri + 1 >= len(ctx.rows):
                break
            row, nxt = ctx.rows[ri], ctx.rows[ri + 1]
            mh = row_median_height(row)
            aligned = abs(nxt.x0 - x_left) <= 1.6 * mh
            filled = row.x1 >= 0.80 * ctx.right_edge and not row.text.rstrip().endswith((".", ";"))
            if not (aligned or filled):
                break
            if nxt.y0 - row.y1 > 1.6 * mh or ctx.by_row.get(ri + 1) or _STOP_CONT.search(nxt.text):
                break
            if nxt.x0 < x_left - 2.5 * mh:
                break
            out.extend(nxt.words)
            ri += 1
        return out

    def _label_cands(self, ctx: PageCtx, field_id: str, part_id: Optional[str], kind: str, choices: dict, multiline: bool) -> Iterator[Cand]:
        mine = [h for h in ctx.hits if h.field_id == field_id and h.part_id == part_id]
        for hit in sorted(mine, key=lambda h: (-h.n_tokens, h.row, h.w0)):
            words = self._remainder(ctx, hit)
            text, offs = _join(words)
            parsed = parse_value(kind, text, choices) if words else None
            used, via = words, "label"
            if parsed is None:
                below = self._below(ctx, hit)
                if below:
                    text, offs = _join(below)
                    parsed = parse_value(kind, text, choices)
                    used, via = below, "label-below"
            if parsed is None:
                continue
            if parsed.span is not None and kind not in ("text", "org"):
                used = [w for w, o in zip(used, offs) if o < parsed.span[1] and o + len(w.text) > parsed.span[0]] or used
            value = parsed.value
            if multiline and kind in ("text", "org") and used:
                ri = ctx.word_row.get(id(used[-1]), hit.row)
                extra = self._continuation(ctx, ri, used[0].x0)
                if extra:
                    t2, _ = _join(used + extra)
                    p2 = parse_value(kind, t2, choices)
                    if p2:
                        value, used = p2.value, used + extra
            yield Cand(value, parsed.normalized, used, hit.page, via, 1.0, ctx.rows[hit.row].text)

    # ------------------------------------------------------------------ значения по шаблонам
    def _pattern_cands(self, ctx: PageCtx, key: tuple, kind: str, choices: dict, group: str = "value") -> Iterator[Cand]:
        for rx in self._rx.get(key, []):
            for m in rx.finditer(ctx.text):
                if group not in rx.groupindex or m.group(group) is None:
                    continue
                s, e = m.span(group)
                parsed = parse_value(kind, m.group(group), choices)
                if parsed is None:
                    continue
                if parsed.span and kind not in ("text", "org"):
                    e = s + parsed.span[1]
                    s = s + parsed.span[0]
                words = self._words_in_span(ctx, s, e)
                if words:
                    yield Cand(parsed.value, parsed.normalized, words, ctx.page.index, "pattern", 0.9, ctx.text[max(0, m.start() - 10):m.end() + 10].strip())

    # ------------------------------------------------------------------ результат
    @staticmethod
    def _boxes(ctxs: dict[int, PageCtx], words: list[Word], page: int) -> list[Box]:
        ctx = ctxs[page]
        by_row: dict[int, list[Word]] = {}
        for w in words:
            by_row.setdefault(ctx.word_row.get(id(w), -1), []).append(w)
        boxes = []
        for _, ws in sorted(by_row.items()):
            boxes.append(ctx.page.map_box(min(w.x0 for w in ws), min(w.y0 for w in ws), max(w.x1 for w in ws), max(w.y1 for w in ws)))
        return boxes

    def _result_from_cand(self, f: FieldSpec, c: Cand, ctxs: dict[int, PageCtx]) -> FieldResult:
        conf = (statistics.fmean(w.conf for w in c.words) / 100.0 if c.words else 0.5) * c.factor
        return FieldResult(f.id, f.title, f.kind, True, c.value, c.normalized, min(conf, 0.99), self._boxes(ctxs, c.words, c.page), c.method, c.evidence)

    def _missing(self, f: FieldSpec) -> FieldResult:
        return FieldResult(f.id, f.title, f.kind, False, "", None, 0.0, [], "", "")

    # ------------------------------------------------------------------ запасные детекторы
    def _header_org(self, ctxs: dict[int, PageCtx]) -> Optional[Cand]:
        ctx = ctxs[min(ctxs)]
        best = None
        for ri, row in enumerate(ctx.rows[:9]):
            if _TITLE_ROW.match(row.text) or ctx.by_row.get(ri):
                continue
            n = len(_ORG_WORDS.findall(row.text))
            if n and re.search(r"[А-Яа-я]{4}", row.text) and (best is None or n > best[0]):
                best = (n, ri)
        if best is None:
            return None
        ri = best[1]
        words = list(ctx.rows[ri].words)
        row = ctx.rows[ri]
        # перенос названия на следующую строку: строка заполняет ширину листа
        if ri + 1 < len(ctx.rows) and row.x1 >= 0.85 * ctx.right_edge and not row.text.rstrip().endswith("."):
            nxt = ctx.rows[ri + 1]
            if not _TITLE_ROW.match(nxt.text) and not ctx.by_row.get(ri + 1) and nxt.y0 - row.y1 < 1.5 * row_median_height(row) and not re.search(r"\d{2}", nxt.text):
                words += nxt.words
        text, _ = _join(words)
        parsed = parse_value("org", text)
        if not parsed:
            return None
        return Cand(parsed.value, None, words, ctx.page.index, "header", 0.75, row.text)

    def _unclaimed_date(self, ctxs: dict[int, PageCtx], claimed: set[int]) -> Optional[Cand]:
        found = []
        for pi, ctx in ctxs.items():
            for d in find_dates(ctx.text):
                words = self._words_in_span(ctx, d.start, d.end)
                if words and not any(id(w) in claimed for w in words):
                    found.append((pi, words[0].y0, d, words))
        if not found:
            return None
        pi, _, d, words = max(found, key=lambda t: (t[0], t[1]))
        return Cand(d.dmy, d.iso, words, pi, "fallback", 0.7, ctxs[pi].rows[ctxs[pi].word_row[id(words[0])]].text)

    # ------------------------------------------------------------------ составные поля
    def _composite(self, f: FieldSpec, ctxs: dict[int, PageCtx], claimed: set[int]) -> FieldResult:
        got: dict[str, Cand] = {}

        def take(part: PartSpec, cand: Cand):
            if part.id not in got:
                got[part.id] = cand

        # 1. метка всего поля: «Номер и дата выдачи: № 458 от 15.05.2024»
        for ctx in ctxs.values():
            for hit in ctx.hits:
                if hit.field_id != f.id or hit.part_id is not None:
                    continue
                words = self._remainder(ctx, hit) or self._below(ctx, hit)
                if not words:
                    continue
                text, offs = _join(words)
                work = text
                for part in f.parts:
                    parsed = parse_value(part.kind, work)
                    if parsed is None:
                        continue
                    span = parsed.span or (0, len(text))
                    used = [w for w, o in zip(words, offs) if o < span[1] and o + len(w.text) > span[0]] or words
                    work = work[:span[0]] + " " * (span[1] - span[0]) + work[span[1]:]
                    take(part, Cand(parsed.value, parsed.normalized, used, ctx.page.index, "label", 1.0, ctx.rows[hit.row].text))
        # 2. метки частей
        for part in f.parts:
            if part.id in got:
                continue
            for ctx in ctxs.values():
                c = next(self._label_cands(ctx, f.id, part.id, part.kind, {}, False), None)
                if c:
                    take(part, c)
                    break
        # 3. шаблоны
        if len(got) < len(f.parts):
            for ctx in ctxs.values():
                for rx in self._rx.get((f.id, None), []):
                    for m in rx.finditer(ctx.text):
                        for part in f.parts:
                            if part.id in got or part.id not in rx.groupindex or m.group(part.id) is None:
                                continue
                            s, e = m.span(part.id)
                            parsed = parse_value(part.kind, m.group(part.id))
                            if parsed is None:
                                continue
                            words = self._words_in_span(ctx, s, e)
                            if words:
                                take(part, Cand(parsed.value, parsed.normalized, words, ctx.page.index, "pattern", 0.9, ctx.text[max(0, m.start() - 8):m.end() + 8]))
                for part in f.parts:
                    if part.id in got:
                        continue
                    c = next(self._pattern_cands(ctx, (f.id, part.id), part.kind, {}), None)
                    if c:
                        take(part, c)
        parts: list[PartResult] = []
        all_boxes: list[Box] = []
        confs = []
        for part in f.parts:
            c = got.get(part.id)
            if c:
                boxes = self._boxes(ctxs, c.words, c.page)
                conf = (statistics.fmean(w.conf for w in c.words) / 100.0 if c.words else 0.5) * c.factor
                parts.append(PartResult(part.id, part.title, True, c.value, c.normalized, min(conf, 0.99), boxes, part.missing_phrase))
                all_boxes += boxes
                confs.append(conf)
                if part.kind == "date":
                    claimed.update(id(w) for w in c.words)
            else:
                parts.append(PartResult(part.id, part.title, False, "", None, 0.0, [], part.missing_phrase))
        found_parts = [p for p in parts if p.found]
        if f.format and len(found_parts) == len(parts):
            value = f.format.format(**{p.id: p.value for p in parts})
        else:
            value = "; ".join(f"{p.title}: {p.value}" for p in found_parts)
        method = ",".join(sorted({c.method for c in got.values()}))
        evidence = next((c.evidence for c in got.values()), "")
        return FieldResult(
            f.id, f.title, "composite", bool(found_parts), value,
            {p.id: p.normalized for p in found_parts} or None, min(confs) if confs else 0.0, all_boxes, method, evidence, parts,
        )

    def _period_by_labels(self, f: FieldSpec, ctxs: dict[int, PageCtx]) -> Optional[FieldResult]:
        """Период, у которого начало и окончание подписаны отдельно («Начало периода обучения: …», «Окончание периода обучения: …»).
        Каждая дата берётся только по своей подписи: пустая подпись начала не подменяется датой окончания. Найден период, если есть обе даты;
        одна дата остаётся в normalized (start или end), поле при этом считается ненайденным."""
        got: dict[str, Cand] = {}
        for ctx in ctxs.values():
            for hit in ctx.hits:
                if hit.field_id != f.id or hit.part_id not in ("start", "end") or hit.part_id in got:
                    continue
                words = self._remainder(ctx, hit) or self._below(ctx, hit)
                if not words:
                    continue
                text, offs = _join(words)
                parsed = parse_value("date", text)
                if parsed is None:
                    continue
                span = parsed.span or (0, len(text))
                used = [w for w, o in zip(words, offs) if o < span[1] and o + len(w.text) > span[0]] or words
                got[hit.part_id] = Cand(parsed.value, parsed.normalized, used, ctx.page.index, "label", 1.0, ctx.rows[hit.row].text)
        if not got:
            return None
        cands = [got[k] for k in ("start", "end") if k in got]
        words = [w for c in cands for w in c.words]
        conf = statistics.fmean(w.conf for w in words) / 100.0 if words else 0.5
        boxes = [b for c in cands for b in self._boxes(ctxs, c.words, c.page)]
        value = " – ".join(c.value for c in cands)
        norm = {k: got[k].normalized for k in got}
        return FieldResult(f.id, f.title, f.kind, len(got) == 2, value, norm, min(conf, 0.99), boxes, "label", cands[0].evidence)

    # ------------------------------------------------------------------ подпись и печать
    @staticmethod
    def _marks(ctx: PageCtx) -> dict[str, MarkDetection]:
        if ctx.marks is None:
            ctx.marks = detect_marks(ctx.page)
        return ctx.marks

    def _marks_field(self, f: FieldSpec, ctxs: dict[int, PageCtx]) -> FieldResult:
        explicit: Optional[bool] = None
        ev = ""
        for ctx in ctxs.values():
            for hit in ctx.hits:
                if hit.field_id == f.id and hit.part_id is None:
                    text = " ".join(w.text for w in self._remainder(ctx, hit)).lower()
                    if re.search(r"\b(есть|имеется|имеются|присутству\w+|да|заверен\w*)\b", text):
                        explicit, ev = True, ctx.rows[hit.row].text
                    elif re.search(r"\b(нет|отсутству\w+|не\s+имеется)\b", text):
                        explicit, ev = False, ctx.rows[hit.row].text
        parts: list[PartResult] = []
        boxes_all: list[Box] = []
        for part in f.parts:
            present, conf, boxes, how = False, 0.0, [], "image"
            if explicit is not None:
                present, conf, how = explicit, 0.9, "text"
            elif self.marks_enabled:
                best = None
                for pi, ctx in ctxs.items():
                    det = self._marks(ctx).get(part.mark or "")
                    if det and (best is None or (det.present, det.confidence) > (best[1].present, best[1].confidence)):
                        best = (pi, det)
                if best:
                    pi, det = best
                    present, conf = det.present, det.confidence
                    if det.present and det.box:
                        boxes = [ctxs[pi].page.map_box(*det.box)]
            parts.append(PartResult(part.id, part.title, present, "есть" if present else "нет", present, conf, boxes, part.missing_phrase, "нет"))
            boxes_all += boxes
        n = sum(p.found for p in parts)
        value = "есть" if n == len(parts) else "нет"
        evid = "; ".join(f"{p.title}: {'есть' if p.found else 'нет'}" for p in parts) if len(parts) > 1 else ev
        return FieldResult(
            f.id, f.title, "marks", n == len(parts), value, {p.id: p.found for p in parts}, min((p.confidence for p in parts), default=0.0),
            boxes_all, "text" if explicit is not None else "image", evid or ev, parts,
        )

    # ------------------------------------------------------------------ основной вход
    def extract(self, pages: list[OcrPage]) -> list[FieldResult]:
        ctxs = {p.index: self._ctx(p) for p in pages}
        claimed: set[int] = set()
        out: dict[str, FieldResult] = {}
        late = [f for f in self.dt.fields if f.fallback == "unclaimed_date_last"]
        for f in [*[x for x in self.dt.fields if x not in late], *late]:
            if f.kind == "marks":
                out[f.id] = self._marks_field(f, ctxs)
                continue
            if f.kind == "composite":
                out[f.id] = self._composite(f, ctxs, claimed)
                continue
            if f.kind == "period" and (f.start_labels or f.end_labels):
                labeled = self._period_by_labels(f, ctxs)
                if labeled is not None:
                    out[f.id] = labeled
                    continue
            cand = None
            for ctx in ctxs.values():
                cand = next(self._label_cands(ctx, f.id, None, f.kind, f.choices, f.multiline), None)
                if cand:
                    break
            if cand is None:
                for ctx in ctxs.values():
                    cand = next(self._pattern_cands(ctx, (f.id, None), f.kind, f.choices), None)
                    if cand:
                        break
            if cand is None and f.fallback == "header_org":
                cand = self._header_org(ctxs)
            if cand is None and f.fallback == "unclaimed_date_last":
                cand = self._unclaimed_date(ctxs, claimed)
            if cand is None:
                out[f.id] = self._missing(f)
                continue
            if f.kind in ("date", "period"):
                claimed.update(id(w) for w in cand.words)
            out[f.id] = self._result_from_cand(f, cand, ctxs)
        for f in self.dt.fields:
            out[f.id].export_title = f.export_title
        return [out[f.id] for f in self.dt.fields]
