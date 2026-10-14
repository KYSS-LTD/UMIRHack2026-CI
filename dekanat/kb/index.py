"""Поиск по пунктам Положения: BM25 по основам слов + синонимы + оценка «покрытия» вопроса."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from ..models import Clause
from ..textutil import STOPWORDS, stem, stem_eq, stems, tokenize

# слова-«оболочки» вопроса, которые не несут предметного смысла
QUESTION_FILLERS = frozenset(
    """скажите подскажите подскажи пожалуйста хочу хотел хотела узнать интересует интересно вопрос
    ответьте ответь объясните расскажите мне нам вам ваш мой моя мое моё данный
    эта этот это эти этой этого такая такой такие
    важно важна важен важны
    назови назовите укажи укажите покажи покажите скажи подскажи расскажи объясни напиши напишите помоги помогите
    найди найдите выдели выделите подсвети подсветите отметь отметьте говорит говорится гласит сказано""".split()
)


@dataclass
class Hit:
    clause: Clause
    score: float
    coverage: float
    matched: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    exact: float = 1.0  # доля «веса» слов вопроса, найденных в пункте без помощи синонимов


def load_synonym_groups(path: Optional[Path], key: str = "groups") -> list[set[str]]:
    if not path or not Path(path).exists():
        return []
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    groups: list[set[str]] = []
    for group in raw.get(key, []):
        st = {stem(t) for item in group for t in tokenize(str(item)) if len(t) > 2 and t not in STOPWORDS and " " not in str(item)}
        if len(st) > 1:
            groups.append(st)
    return groups


class ClauseIndex:
    def __init__(self, clauses: list[Clause], synonym_groups: Optional[list[set[str]]] = None):
        self.clauses = clauses
        self.groups = synonym_groups or []
        self._group_of: dict[str, int] = {}
        for gi, g in enumerate(self.groups):
            for s in g:
                self._group_of.setdefault(s, gi)
        self.docs: list[list[str]] = []
        for c in clauses:
            body = stems(c.text, drop_stop=True)
            head = stems(" ".join(c.section), drop_stop=True)
            self.docs.append(body + head)
        self.n = max(1, len(self.docs))
        self.avgdl = (sum(len(d) for d in self.docs) / self.n) or 1.0
        self.df: Counter = Counter()
        for d in self.docs:
            for t in set(d):
                self.df[t] += 1
        self.tf = [Counter(d) for d in self.docs]
        self.vocab = set(self.df)
        self.max_idf = self._idf_raw(0)

    # ------------------------------------------------------------------ вспомогательное
    def _idf_raw(self, df: int) -> float:
        return math.log((self.n - df + 0.5) / (df + 0.5) + 1.0)

    def variants(self, term: str) -> set[str]:
        """Сама основа + синонимы + близкие по написанию основы из словаря Положения."""
        out = {term}
        gi = self._group_of.get(term)
        if gi is not None:
            out |= self.groups[gi]
        if len(term) >= 5:
            out |= {v for v in self.vocab if stem_eq(term, v)}
        return out

    def df_of(self, term: str) -> int:
        return max((self.df.get(v, 0) for v in self.variants(term)), default=0)

    def idf(self, term: str) -> float:
        return self._idf_raw(self.df_of(term))

    def query_terms(self, question: str, extra_drop: frozenset = frozenset()) -> list[str]:
        toks = [t for t in tokenize(question, drop_stop=True) if t not in QUESTION_FILLERS and t not in extra_drop]
        seen, out = set(), []
        for t in toks:
            s = stem(t)
            if s not in seen and (len(s) > 1 or s.isdigit()):
                seen.add(s)
                out.append(s)
        return out

    # ------------------------------------------------------------------ поиск
    def _bm25(self, qterms: list[str], i: int, k1: float = 1.4, b: float = 0.7) -> float:
        tf, dl = self.tf[i], len(self.docs[i])
        score = 0.0
        for q in qterms:
            best = 0.0
            for v in self.variants(q):
                f = tf.get(v, 0)
                if not f:
                    continue
                w = self._idf_raw(self.df.get(v, 0)) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self.avgdl))
                best = max(best, w * (1.0 if v == q else 0.85))
            score += best
        return score

    def coverage(self, qterms: list[str], i: int, ignore: frozenset = frozenset()) -> tuple[float, list[str], list[str]]:
        """Доля «веса» слов вопроса (по IDF), найденных в пункте. Возвращает (покрытие, найденные, ненайденные).

        Слова из ignore (прилагательные-уточнения вроде «максимальный»), если их в пункте нет, не штрафуют покрытие."""
        present = set(self.docs[i])
        total = matched_w = 0.0
        found, missing = [], []
        for q in qterms:
            w = self.idf(q) if self.df_of(q) else self.max_idf
            if any(v in present for v in self.variants(q)):
                total += w
                matched_w += w
                found.append(q)
            elif q in ignore:
                continue
            else:
                total += w
                missing.append(q)
        return (matched_w / total if total else 0.0), found, missing

    def _exact_coverage(self, qterms: list[str], i: int, ignore: frozenset = frozenset()) -> float:
        present = set(self.docs[i])
        total = got = 0.0
        for q in qterms:
            w = self.idf(q) if self.df_of(q) else self.max_idf
            hit = any(stem_eq(q, d) for d in present) if len(q) >= 4 else q in present
            if hit:
                total += w
                got += w
            elif q not in ignore:
                total += w
        return got / total if total else 0.0

    def search(self, question: str, k: int = 4, qterms: Optional[list[str]] = None, ignore: frozenset = frozenset()) -> list[Hit]:
        qterms = qterms if qterms is not None else self.query_terms(question)
        if not qterms:
            return []
        hits = []
        for i, c in enumerate(self.clauses):
            sc = self._bm25(qterms, i)
            if sc <= 0:
                continue
            cov, found, missing = self.coverage(qterms, i, ignore)
            hits.append(Hit(c, sc, cov, found, missing))
            hits[-1].exact = self._exact_coverage(qterms, i, ignore)
        # точное совпадение слов вопроса важнее совпадения через синонимы
        hits.sort(key=lambda h: (-(h.score * (0.4 + h.coverage) * (0.7 + 0.3 * h.exact)), h.clause.number))
        return hits[:k]

    def get(self, number: str) -> Optional[Clause]:
        return next((c for c in self.clauses if c.number == number), None)

    def rare_threshold(self) -> float:
        return 0.75 * self.max_idf
