"""Поиск названий полей («Дата выдачи:», «ФИО студента»...) в строках OCR с допуском на опечатки."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Iterator, Optional

from ..config import DocTypeSpec
from ..models import Row, Word
from ..textutil import STOPWORDS, fix_homoglyphs, normalize, stem, stem_eq

_INITIALS = re.compile(r"^(?:[А-Яа-яЁёA-Za-z]\.){2,}$")
_SEP_WORD = re.compile(r"^[:;\-–—.,|_]+$")


@dataclass
class LabelDef:
    field_id: str
    part_id: Optional[str]
    stems: list[str]
    text: str


@dataclass
class LabelHit:
    field_id: str
    part_id: Optional[str]
    page: int
    row: int
    w0: int  # индексы слов строки [w0, w1)
    w1: int
    n_tokens: int
    text: str


def word_tokens(word: Word) -> list[str]:
    """Основы слов внутри одного слова OCR (аббревиатуры «Ф.И.О.» склеиваются в «фио»)."""
    text = word.text
    if _INITIALS.match(text):
        return [re.sub(r"[.\s]", "", text).lower().replace("ё", "е")]
    toks = re.findall(r"[a-zа-я0-9]+", normalize(text))
    return [stem(fix_homoglyphs(t)) for t in toks if t not in STOPWORDS]


def label_stems(label: str) -> list[str]:
    toks = []
    for raw in label.split():
        if _INITIALS.match(raw):
            toks.append(re.sub(r"[.\s]", "", raw).lower().replace("ё", "е"))
            continue
        for t in re.findall(r"[a-zа-я0-9]+", normalize(raw)):
            if t not in STOPWORDS:
                toks.append(stem(fix_homoglyphs(t)))
    return toks


def compile_labels(doctype: DocTypeSpec) -> list[LabelDef]:
    defs: list[LabelDef] = []
    seen = set()

    def add(field_id: str, part_id: Optional[str], text: str):
        stems = label_stems(text)
        if not stems:
            return
        key = (field_id, part_id, tuple(stems))
        if key in seen:
            return
        seen.add(key)
        defs.append(LabelDef(field_id, part_id, stems, text))

    for f in doctype.fields:
        for lab in [f.title, *f.labels]:
            add(f.id, None, lab)
        for p in f.parts:
            for lab in [*p.labels]:
                add(f.id, p.id, lab)
        for lab in f.start_labels:  # период с датами на отдельных строках: части «start» / «end»
            add(f.id, "start", lab)
        for lab in f.end_labels:
            add(f.id, "end", lab)
    defs.sort(key=lambda d: -len(d.stems))
    return defs


def row_median_height(row: Row) -> float:
    return statistics.median(w.h for w in row.words)


def gap_before(row: Row, w_idx: int) -> float:
    if w_idx <= 0:
        return 1e9
    return row.words[w_idx].x0 - row.words[w_idx - 1].x1


def _match(tokens: list[tuple[str, int]], lab: list[str], max_gap: int = 2) -> Iterator[tuple[int, int]]:
    for i, (s, _) in enumerate(tokens):
        if not stem_eq(s, lab[0]):
            continue
        last = i
        ok = True
        for want in lab[1:]:
            found = False
            for k in range(last + 1, min(len(tokens), last + 2 + max_gap)):
                if stem_eq(tokens[k][0], want):
                    last = k
                    found = True
                    break
            if not found:
                ok = False
                break
        if ok:
            yield i, last


def find_hits(rows: list[Row], labels: list[LabelDef], page: int = 0) -> list[LabelHit]:
    hits: list[LabelHit] = []
    for ri, row in enumerate(rows):
        tokens: list[tuple[str, int]] = []
        for wi, w in enumerate(row.words):
            for t in word_tokens(w):
                tokens.append((t, wi))
        if not tokens:
            continue
        mh = row_median_height(row)
        row_hits: list[LabelHit] = []
        for lab in labels:
            if len(lab.stems) > len(tokens) + 2:
                continue
            for i, last in _match(tokens, lab.stems):
                w0, w1 = tokens[i][1], tokens[last][1] + 1
                prev_txt = row.words[w0 - 1].text if w0 > 0 else ""
                start_ok = w0 == 0 or gap_before(row, w0) > 1.8 * mh or prev_txt.endswith((",", ";", "|")) or _SEP_WORD.match(prev_txt) is not None
                last_txt = row.words[w1 - 1].text
                if not (start_ok or (last_txt.endswith(":") and sum(len(t) for t in lab.stems) >= 4)):
                    continue
                row_hits.append(LabelHit(lab.field_id, lab.part_id, page, ri, w0, w1, len(lab.stems), lab.text))
        # непересекающиеся попадания: длинные метки важнее коротких
        row_hits.sort(key=lambda h: (-h.n_tokens, h.w0))
        taken: list[LabelHit] = []
        for h in row_hits:
            if all(h.w1 <= t.w0 or h.w0 >= t.w1 for t in taken):
                taken.append(h)
        hits.extend(sorted(taken, key=lambda h: (h.row, h.w0)))
    return hits
