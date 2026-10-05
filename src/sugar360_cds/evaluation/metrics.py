"""The six measurements used to decide whether Approach A or B is better.

1. recall             - did it find the rules in the answer key?
2. precision          - are its rules correct? (answer-key agreement + doctor review)
3. citation_validity  - does each quoted SOURCE really appear on the cited page?
4. retrieval_recall   - did BM25 retrieve the pages the answer key cites? (Approach A)
5. cost               - tokens, time and money per table
6. run_agreement      - does the approach give the same rules when run again?
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from rapidfuzz import fuzz

from .schema import DecisionRow, Source


@dataclass(frozen=True)
class Score:
    """A ratio that keeps its counts: 7/9 says more than 0.78."""

    hits: int
    total: int

    @property
    def value(self) -> float | None:
        return self.hits / self.total if self.total else None  # None = nothing to measure

    def __str__(self) -> str:
        return f"{self.hits}/{self.total}" + (f" ({self.value:.0%})" if self.total else "")


# ---------- 1 & 2: matching drafted rows against the answer key ----------


def match_rows(drafted: Sequence[DecisionRow], gold: Sequence[DecisionRow]) -> list[tuple[int, int]]:
    """Pair drafted rows with answer-key rows that have the same signature.

    Returns (drafted index, gold index) pairs. Positions, not the row objects,
    so two identical drafted rows are still told apart.
    One-to-one: a drafted row can satisfy only one answer-key row, so drafting
    the same rule twice does not count twice.
    """
    unused = list(range(len(drafted)))
    pairs = []
    for gi, g in enumerate(gold):
        for di in unused:
            if drafted[di].signature() == g.signature():
                pairs.append((di, gi))
                unused.remove(di)
                break
    return pairs


def recall(drafted: Sequence[DecisionRow], gold: Sequence[DecisionRow]) -> Score:
    """Share of answer-key rows that the approach found."""
    return Score(len(match_rows(drafted, gold)), len(gold))


def key_agreement(drafted: Sequence[DecisionRow], gold: Sequence[DecisionRow]) -> Score:
    """Share of drafted rows that are in the answer key.

    Not true precision: a drafted row missing from the key may still be correct
    (the key can be incomplete). Those rows go to doctor review instead.
    """
    return Score(len(match_rows(drafted, gold)), len(drafted))


def unmatched(drafted: Sequence[DecisionRow], gold: Sequence[DecisionRow]) -> list[DecisionRow]:
    """Drafted rows not in the answer key - the ones a doctor must judge."""
    matched = {di for di, _ in match_rows(drafted, gold)}
    return [d for i, d in enumerate(drafted) if i not in matched]


VERDICTS = {"correct", "incorrect", "unsupported"}


def reviewed_precision(verdicts: Iterable[str]) -> Score:
    """Share of doctor-reviewed rows judged 'correct'."""
    verdicts = [v.strip().lower() for v in verdicts]
    bad = set(verdicts) - VERDICTS
    if bad:
        raise ValueError(f"unknown verdicts {sorted(bad)}; use {sorted(VERDICTS)}")
    return Score(verdicts.count("correct"), len(verdicts))


# ---------- 3: are the citations real? ----------


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def quote_found(quote: str, page_text: str, threshold: float = 90.0) -> bool:
    """True if `quote` appears in `page_text`, allowing small extraction differences.

    partial_ratio finds the best-matching stretch of the page (0-100). A
    threshold below 100 tolerates hyphenation and spacing noise from PDF text.
    """
    q = _normalise(quote)
    return bool(q) and fuzz.partial_ratio(q, _normalise(page_text)) >= threshold


def citation_validity(
    rows: Iterable[DecisionRow],
    pages: Mapping[tuple[str, int], str],
    threshold: float = 90.0,
) -> tuple[Score, list[tuple[str, Source]]]:
    """Check every source quote against the extracted text of its cited page.

    `pages` maps (doc_id, page) -> extracted text. A citation to a page that
    does not exist counts as invalid. Returns the score and the failures
    (row_id, source) so they can be inspected.
    """
    hits, total, failures = 0, 0, []
    for row in rows:
        for src in row.sources:
            total += 1
            text = pages.get((src.doc_id, src.page))
            if text is not None and quote_found(src.quote, text, threshold):
                hits += 1
            else:
                failures.append((row.row_id, src))
    return Score(hits, total), failures


# ---------- 4: did retrieval bring the right pages? (Approach A) ----------


def retrieval_recall(retrieved: Iterable[tuple[str, int]], gold: Sequence[DecisionRow]) -> Score:
    """Share of answer-key rows with at least one cited page among the retrieved pages.

    If this is low, Approach A's misses are retrieval's fault, not the LLM's.
    """
    retrieved = set(retrieved)
    hits = sum(any((s.doc_id, s.page) in retrieved for s in g.sources) for g in gold)
    return Score(hits, len(gold))


# ---------- 5: cost ----------


@dataclass(frozen=True)
class CallStats:
    input_tokens: int
    output_tokens: int
    seconds: float


def cost(
    calls: Iterable[CallStats],
    usd_per_million_input: float,
    usd_per_million_output: float,
) -> dict[str, float]:
    """Totals for one table. Prices come from configs, from the provider's price page."""
    calls = list(calls)
    inp = sum(c.input_tokens for c in calls)
    out = sum(c.output_tokens for c in calls)
    return {
        "calls": len(calls),
        "input_tokens": inp,
        "output_tokens": out,
        "seconds": sum(c.seconds for c in calls),
        "usd": inp / 1e6 * usd_per_million_input + out / 1e6 * usd_per_million_output,
    }


# ---------- 6: stability across repeated runs ----------


def run_agreement(runs: Sequence[Sequence[DecisionRow]]) -> float:
    """Average overlap of rules between every pair of runs (1.0 = identical every time).

    Overlap of two runs = shared rules / all rules in either (Jaccard index).
    """
    if len(runs) < 2:
        raise ValueError("need at least 2 runs to measure agreement")
    sets = [{r.signature() for r in run} for run in runs]
    pair_scores = [len(a & b) / len(a | b) if a | b else 1.0 for a, b in combinations(sets, 2)]
    return sum(pair_scores) / len(pair_scores)
