"""Какие поля документа требует Положение — выводится из самого текста Положения.

Идея: пункт Положения «относится» к типу документа, если называет его (или стоит в разделе с таким
названием). Поле «требуется» этим пунктом, если пункт называет поле и сформулирован обязывающе
(«должна содержать», «обязательны», «не принимается»). Так работает любое Положение со стандартной
лексикой, а новые типы документов добавляются конфигом без кода.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..config import DocTypeSpec, FieldSpec, PartSpec
from ..models import Clause
from ..textutil import normalize, stem_eq, stems
from ..extract.labels import label_stems

_CUE_STRONG = re.compile(r"обязательн|обязан|не принима|не допуска|не рассматрива|без (?:них|этих|нее|неё|него)")
_CUE_MID = re.compile(r"должн|необходим|требу|подлежит|следует|надлежит")
_CUE_WEAK = re.compile(r"содерж|указыва|включа|предоставля|представля|прилага|приложен|состо|фиксир")


_NOT_REQUIRED = re.compile(r"не\s+(?:требу|нужн|обязательн)")


def cue_level(text: str) -> int:
    # «Печать … для проверки не требуется» — пункт говорит, что поле НЕ нужно, а не требует его;
    # оговорку вырезаем, остальные слова-сигналы («но обязательна подпись») по-прежнему считаются
    t = _NOT_REQUIRED.sub(" ", normalize(text))
    if _CUE_STRONG.search(t):
        return 3
    if _CUE_MID.search(t):
        return 2
    if _CUE_WEAK.search(t):
        return 1
    return 0


def phrase_in(tokens: list[str], phrase_stems: list[str], max_gap: int = 2) -> bool:
    """Все основы фразы встречаются в пределах окна (допускаются вставки: «даты начала и окончания»)."""
    if not phrase_stems:
        return False
    for i, t in enumerate(tokens):
        if not stem_eq(t, phrase_stems[0]):
            continue
        last, ok = i, True
        for want in phrase_stems[1:]:
            for k in range(last + 1, min(len(tokens), last + 2 + max_gap)):
                if stem_eq(tokens[k], want):
                    last = k
                    break
            else:
                ok = False
                break
        if ok:
            return True
    return False


@dataclass
class Requirement:
    unit: str  # «issue.date»
    clause: Clause
    cue: int
    breadth: int  # сколько единиц проверки названо в этом пункте
    scope: int


@dataclass
class TypeRequirements:
    by_unit: dict[str, list[Requirement]] = field(default_factory=dict)
    clause_units: dict[str, set[str]] = field(default_factory=dict)  # номер пункта -> единицы
    found_any: bool = False

    def best(self, unit: str) -> Optional[Requirement]:
        reqs = self.by_unit.get(unit)
        return max(reqs, key=lambda r: (r.cue, r.breadth, r.scope)) if reqs else None


def unit_id(f: FieldSpec, p: Optional[PartSpec]) -> str:
    return f"{f.id}.{p.id}" if p else f.id


def mention_phrases(f: FieldSpec, p: Optional[PartSpec]) -> list[list[str]]:
    """Фразы, по которым пункт Положения «называет» единицу проверки."""
    def default(title: str, labels: list[str]) -> list[str]:
        out = [title]
        out += [l for l in labels if len(label_stems(l)) >= 2]
        return out

    phrases = list(p.mention) if (p and p.mention) else (default(p.title, p.labels) if p else [])
    if p is None:
        phrases += list(f.mention) if f.mention else default(f.title, f.labels)
    else:
        # название составного поля целиком подразумевает все его части («номер и дата выдачи»)
        phrases += list(f.mention) if f.mention else default(f.title, f.labels)
    seen, out = set(), []
    for ph in phrases:
        st = tuple(label_stems(ph))
        if st and st not in seen:
            seen.add(st)
            out.append(list(st))
    return out


class RequirementModel:
    def __init__(self, doc_types: dict[str, DocTypeSpec], clauses: list[Clause]):
        self.doc_types = doc_types
        self.clauses = clauses
        self.ctoks = [stems(c.text, drop_stop=True) for c in clauses]
        self.htoks = [stems(" ".join(c.section), drop_stop=True) for c in clauses]
        self._alias = {tid: [label_stems(a) for a in dt.aliases] for tid, dt in doc_types.items()}
        self._cache: dict[str, TypeRequirements] = {}

    def _mentions_type(self, tid: str, i: int) -> bool:
        toks = self.ctoks[i] + self.htoks[i]
        return any(phrase_in(toks, a, 1) for a in self._alias[tid])

    def scope(self, tid: str, i: int) -> int:
        if self._mentions_type(tid, i):
            return 2
        if any(self._mentions_type(o, i) for o in self.doc_types if o != tid):
            return -1
        return 1 if any(stem_eq(t, "документ") or stem_eq(t, "документов") for t in self.ctoks[i]) else 0

    def other_types_in(self, i: int, exclude: str) -> list[str]:
        return [o for o in self.doc_types if o != exclude and self._mentions_type(o, i)]

    def for_type(self, tid: str) -> TypeRequirements:
        if tid in self._cache:
            return self._cache[tid]
        dt = self.doc_types[tid]
        res = TypeRequirements()
        units = [(f, p) for f, p in dt.iter_units()]
        phr = {unit_id(f, p): mention_phrases(f, p) for f, p in units}
        for i, c in enumerate(self.clauses):
            scope = self.scope(tid, i)
            if scope < 1:
                continue
            cue = cue_level(c.text)
            toks = self.ctoks[i]
            named = {u for u, phs in phr.items() if any(phrase_in(toks, ph) for ph in phs)}
            if not named:
                continue
            breadth = len(named)
            if not (cue >= 2 or (cue == 1 and breadth >= 3)):
                continue
            res.clause_units[c.number] = named
            for u in named:
                res.by_unit.setdefault(u, []).append(Requirement(u, c, cue, breadth, scope))
        res.found_any = bool(res.by_unit)
        self._cache[tid] = res
        return res
