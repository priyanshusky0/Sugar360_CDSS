"""Approach A retrieval: BM25 keyword search over extracted pages.

BM25 scores a page higher when it contains the query's words, especially rare
words (e.g. "SGLT2" counts for more than "patients"), with diminishing returns
for repeating a word many times.

Several short queries are run, and their results merged with reciprocal rank
fusion (RRF): a page's fused score is the sum of 1 / (rrf_k + rank) over the
queries that found it. Pages found by several queries, or ranked high by one,
rise to the top.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

PageKey = tuple[str, int]  # (doc_id, page)

STOPWORDS = frozenset(
    "a an and are as at be been by can for from has have if in into is it its may "
    "not of on or should that the their these this those to was were which with".split()
)
# Words and numbers, keeping inner hyphens and dots: "glp-1", "dpp-4", "7.0".
TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


class PageIndex:
    def __init__(self, pages: Mapping[PageKey, str]):
        self.keys = list(pages)
        self.bm25 = BM25Okapi([tokenize(pages[k]) for k in self.keys])

    def search(self, query: str, k: int, within: Set[PageKey] | None = None) -> list[tuple[PageKey, float]]:
        """Top-k pages for `query`, best first. Pages with score 0 (no shared words) are dropped.

        `within` limits the ranking to some pages (e.g. one guideline) while word
        rarity still comes from the whole index. A one-page guideline indexed on
        its own would give every word zero rarity, and so a score of zero.
        """
        scores = self.bm25.get_scores(tokenize(query))
        candidates = range(len(scores)) if within is None else [
            i for i, key in enumerate(self.keys) if key in within
        ]
        best = sorted(candidates, key=lambda i: scores[i], reverse=True)[:k]
        return [(self.keys[i], float(scores[i])) for i in best if scores[i] > 0]


@dataclass(frozen=True)
class RetrievedPage:
    doc_id: str
    page: int
    fused_score: float
    queries: tuple[int, ...]  # which queries (by position) found this page


def retrieve(
    index: PageIndex,
    queries: Sequence[str],
    pages_per_query: int,
    max_pages: int,
    rrf_k: int = 60,
    within: Set[PageKey] | None = None,
) -> list[RetrievedPage]:
    """Run every query, fuse the rankings, return at most max_pages pages, best first."""
    fused: dict[PageKey, float] = {}
    found_by: dict[PageKey, list[int]] = {}
    for qi, query in enumerate(queries):
        for rank, (key, _) in enumerate(index.search(query, pages_per_query, within), start=1):
            fused[key] = fused.get(key, 0.0) + 1 / (rrf_k + rank)
            found_by.setdefault(key, []).append(qi)
    best = sorted(fused, key=lambda k: fused[k], reverse=True)[:max_pages]
    return [RetrievedPage(k[0], k[1], fused[k], tuple(found_by[k])) for k in best]
