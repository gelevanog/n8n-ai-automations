"""The support knowledge base: Markdown articles, ranked with BM25 (no embedding model, nothing runs locally).

`coverage` (the share of the question's content words found in an article) is the gate the FAQ workflow uses: it is
on a fixed 0-1 scale, unlike BM25 scores, so one threshold works for short and long questions."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

STOPWORDS = frozenset(
    """a about above after again all also am an and any are as at be been before being below between both but by can
    could did do does doing down during each few for from further had has have having he her here hers him his how i
    if in into is it its itself just me more most my no nor not now of off on once only or other our ours out over own
    please same she should so some such than that the their them then there these they this those through to too
    under until up very was we were what when where which while who whom why will with would you your yours hi hello
    thanks thank us get got usually still much many need want like know tell one let im dont cant""".split()
)
_WORD = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)?")


def stem(word: str) -> str:
    for suffix in ("ings", "ing", "ies", "es", "s", "ed"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def tokens(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 1]


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    text: str


@dataclass(frozen=True)
class Hit:
    article: Article
    score: float
    coverage: float


class KnowledgeBase:
    def __init__(self, articles: list[Article], k1: float = 1.4, b: float = 0.6) -> None:
        self.articles = articles
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(f"{a.title} {a.title} {a.text}")) for a in articles]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.avg = sum(self.lengths) / max(1, len(self.lengths))
        df: Counter[str] = Counter()
        for doc in self.docs:
            df.update(doc.keys())
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    @classmethod
    def load(cls, directory: Path) -> KnowledgeBase:
        articles = []
        for path in sorted(directory.glob("*.md")):
            raw = path.read_text(encoding="utf-8").strip()
            first, _, rest = raw.partition("\n")
            articles.append(Article(path.stem, first.lstrip("# ").strip(), rest.strip()))
        return cls(articles)

    def get(self, article_id: str) -> Article | None:
        return next((a for a in self.articles if a.id == article_id), None)

    def search(self, query: str, k: int = 4) -> list[Hit]:
        terms = tokens(query)
        unique = set(terms)
        hits: list[Hit] = []
        for article, doc, length in zip(self.articles, self.docs, self.lengths, strict=True):
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if tf:
                    norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.avg))
                    score += self.idf.get(term, 0.0) * norm
            coverage = len([t for t in unique if t in doc]) / len(unique) if unique else 0.0
            if score > 0:
                hits.append(Hit(article, round(score, 3), round(coverage, 3)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]


@lru_cache(maxsize=4)
def load_kb(directory: Path) -> KnowledgeBase:
    return KnowledgeBase.load(directory)
