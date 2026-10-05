"""Flag clear conflicts between guidelines; lay out everything else for human review.

This module never resolves a conflict. Notes are built from fixed templates (no
LLM), so every flag can be traced to the rule below that produced it.

Each row's action is a decision type with a direction (docs/decisions.md D15):
    start, add, switch -> start, for        avoid -> start, against ("do not start")
    continue           -> continue, for     stop  -> continue, against ("do not continue")
    reduce_dose        -> not compared (dose table)
Different decision types are never compared: "do not start below eGFR 45" and
"stop below eGFR 30" are different decisions.

Rows are compared only within the same patient situation (same category inputs
and values, same other conditions, same numeric inputs) and target by target:
a specific drug with the same drug, a class with the same class. Numbers are
ranges (">=30" is [30, inf)). Only two things are flagged automatically:
  (a) different threshold: same target, decision type and direction; both
      ranges open the same way but starting at different cut-offs (>9 vs >10)
  (b) recommend vs avoid: same target and decision type, opposite directions,
      overlapping ranges (touching at one point is not overlap)
Everything else goes to the "Compare by drug" layout, with no verdict.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from ..evaluation.schema import DecisionRow, Vocabulary, norm_text

DECISION = {"start": ("start", "for"), "add": ("start", "for"), "switch": ("start", "for"),
            "avoid": ("start", "against"), "continue": ("continue", "for"), "stop": ("continue", "against")}
WORDS = {("start", "for"): "start", ("start", "against"): "do not start (avoid)",
         ("continue", "for"): "continue", ("continue", "against"): "stop"}

THRESHOLD = "different threshold"
AVOID = "recommend vs avoid (same drug)"
ORDER = {AVOID: 0, THRESHOLD: 1}


@dataclass(frozen=True)
class Conflict:
    conflict_id: str
    situation: str
    guideline_1: str
    says_1: str
    guideline_2: str
    says_2: str
    conflict_type: str
    note: str


SituationKey = tuple[frozenset[tuple[str, str]], frozenset[str], frozenset[str]]
Interval = tuple[float, float, bool, bool]  # low, high, low included, high included


def situation_key(row: DecisionRow, vocab: Vocabulary) -> SituationKey:
    categories = frozenset((k, v) for k, v in row.conditions.items() if not vocab.is_number(k))
    numbers = frozenset(k for k in row.conditions if vocab.is_number(k))
    other = frozenset(norm_text(c) for c in row.other_conditions)
    return categories, numbers, other


def describe_situation(key: SituationKey, vocab: Vocabulary) -> str:
    categories, numbers, other = key
    parts = [f"{vocab.label(k)} = {v}" for k, v in sorted(categories)]
    parts += [f"{vocab.label(k)}: threshold given" for k in sorted(numbers)]
    parts += [f"other: {c}" for c in sorted(other)]
    return "; ".join(parts) or "any patient (no conditions)"


# ---------- numbers as ranges ----------

def interval(value: str) -> Interval:
    """">=30" -> [30, inf); "<45" -> (-inf, 45); "30-45" -> [30, 45]. Canonical values only."""
    if m := re.fullmatch(r"(>=|<=|>|<)(\d+(?:\.\d+)?)", value):
        op, x = m.group(1), float(m.group(2))
        return {">=": (x, math.inf, True, False), ">": (x, math.inf, False, False),
                "<=": (-math.inf, x, False, True), "<": (-math.inf, x, False, False)}[op]
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", value):
        return float(m.group(1)), float(m.group(2)), True, True
    raise ValueError(f"not a canonical number: {value!r}")


def overlap(a: Interval, b: Interval) -> bool:
    """True if the ranges share more than a single point (adjacent ranges do not overlap)."""
    return max(a[0], b[0]) < min(a[1], b[1])


def _direction(i: Interval) -> str:
    return "above" if i[1] == math.inf and i[0] > -math.inf else "below" if i[0] == -math.inf else "band"


def different_cutoff(a: Interval, b: Interval) -> bool:
    """Both open in the same direction but starting at different cut-offs, e.g. >9 vs >10."""
    d = _direction(a)
    if d != _direction(b) or d == "band":
        return False
    return (a[0], a[2]) != (b[0], b[2]) if d == "above" else (a[1], a[3]) != (b[1], b[3])


def _ranges(row: DecisionRow, vocab: Vocabulary) -> dict[str, Interval]:
    return {k: interval(v) for k, v in row.conditions.items() if vocab.is_number(k)}


def numbers_text(row: DecisionRow, vocab: Vocabulary) -> str:
    return ", ".join(f"{vocab.label(k)} {vocab.display(k, v)}" for k, v in sorted(row.conditions.items())
                     if vocab.is_number(k))


# ---------- the two automatic flags ----------

def relation(r1: DecisionRow, r2: DecisionRow, vocab: Vocabulary) -> str | None:
    """THRESHOLD, AVOID, or None. Rows must already share a situation and a target."""
    d1, d2 = DECISION.get(r1.action_type), DECISION.get(r2.action_type)
    if d1 is None or d2 is None or d1[0] != d2[0]:
        return None  # different decision types are never compared
    n1, n2 = _ranges(r1, vocab), _ranges(r2, vocab)
    if d1[1] != d2[1]:
        return AVOID if all(overlap(n1[k], n2[k]) for k in n1) else None
    differing = [k for k in n1 if n1[k] != n2[k]]
    if differing and all(different_cutoff(n1[k], n2[k]) for k in differing):
        return THRESHOLD
    return None


def says(row: DecisionRow, vocab: Vocabulary) -> str:
    """What a row says: decision, recommendation, drug, numbers, strength, page and quote."""
    nums = numbers_text(row, vocab)
    src = row.sources[0] if row.sources else None
    where = f'\n{src.doc_id} p.{src.page}: "{src.quote}"' if src else ""
    decision = WORDS.get(DECISION.get(row.action_type), row.action_type)
    drugs = f" [drug: {', '.join(row.specific_drugs)}]" if row.specific_drugs else ""
    then = row.recommendation or " OR ".join(row.actions)
    return f"[{row.row_id}] {decision}: {then}{drugs}{' when ' + nums if nums else ''} ({row.strength}){where}"


def _note(kind: str, target: str, g1: str, a: DecisionRow, g2: str, b: DecisionRow, vocab: Vocabulary) -> str:
    w1, w2 = WORDS[DECISION[a.action_type]], WORDS[DECISION[b.action_type]]
    n1, n2 = numbers_text(a, vocab) or "no threshold", numbers_text(b, vocab) or "no threshold"
    if kind == THRESHOLD:
        return f"Both say {w1} {target}, at different cut-offs: {g1} {n1}; {g2} {n2}."
    return f"For overlapping patients, {g1} says {w1} {target} ({n1}) but {g2} says {w2} {target} ({n2})."


def find_conflicts(rows: Sequence[DecisionRow], vocab: Vocabulary, approach: str = "") -> tuple[list[Conflict], dict]:
    """The two automatic conflict types. Rows the vocabulary cannot read are left out and counted."""
    comparable = [r for r in rows if not vocab.condition_problems(r)]
    situations: dict[SituationKey, list[DecisionRow]] = defaultdict(list)
    for row in comparable:
        situations[situation_key(row, vocab)].append(row)

    # One disagreement = one conflict, however many rows state each side: rows are pooled
    # by (situation, guidelines, type, target, each side's decision and numbers).
    found: dict[tuple, tuple[list[DecisionRow], list[DecisionRow]]] = {}
    for key, members in sorted(situations.items(), key=lambda kv: describe_situation(kv[0], vocab)):
        situation = describe_situation(key, vocab)
        by_guideline: dict[str, list[DecisionRow]] = defaultdict(list)
        for row in members:
            by_guideline[row.guideline].append(row)
        for g1, g2 in combinations(sorted(by_guideline), 2):
            for r1 in by_guideline[g1]:
                for r2 in by_guideline[g2]:
                    kind = relation(r1, r2, vocab)
                    for target in sorted(set(r1.targets()) & set(r2.targets())) if kind else []:
                        side1 = (DECISION[r1.action_type], numbers_text(r1, vocab))
                        side2 = (DECISION[r2.action_type], numbers_text(r2, vocab))
                        a, b = found.setdefault((situation, g1, g2, kind, target, side1, side2), ([], []))
                        a.append(r1) if r1 not in a else None
                        b.append(r2) if r2 not in b else None

    items = sorted(found.items(), key=lambda kv: (ORDER[kv[0][3]], kv[0][0], kv[0][4]))
    conflicts = [Conflict(f"C{i:03d}", s, g1, "\n\n".join(says(r, vocab) for r in a),
                          g2, "\n\n".join(says(r, vocab) for r in b), kind,
                          _note(kind, target, g1, a[0], g2, b[0], vocab))
                 for i, ((s, g1, g2, kind, target, _, _), (a, b)) in enumerate(items, start=1)]
    summary = {
        "rows_compared": len(comparable),
        "rows_not_comparable": len(rows) - len(comparable),
        "situations": len(situations),
        "situations_with_2plus_guidelines": sum(len({r.guideline for r in m}) >= 2 for m in situations.values()),
        "conflicts_by_type": {t: sum(c.conflict_type == t for c in conflicts) for t in ORDER},
    }
    return conflicts, summary


# ---------- everything else: side by side, no verdict ----------

def compare_by_drug(rows: Sequence[DecisionRow], vocab: Vocabulary, guidelines: Sequence[str]) -> list[dict]:
    """One block per drug class and decision, one column per guideline, listing what each says.

    Drug-level rows appear in their class's block, marked with the drug name.
    No verdict: this is for the research associate to read.
    """
    def if_text(r: DecisionRow) -> str:
        parts = [f"{vocab.label(k)} {vocab.display(k, v)}" if vocab.is_number(k) else f"{vocab.label(k)} = {v}"
                 for k, v in sorted(r.conditions.items())]
        return "; ".join(parts + list(r.other_conditions)) or "no condition"

    cells: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        decision = WORDS.get(DECISION.get(r.action_type), r.action_type)
        src = r.sources[0] if r.sources else None
        drug = f" [{', '.join(r.specific_drugs)}]" if r.specific_drugs else ""
        line = (f"[{r.row_id}] IF {if_text(r)} -> {r.recommendation or ' OR '.join(r.actions)}{drug} ({r.strength})"
                + (f'\np.{src.page}: "{src.quote}"' if src else ""))
        for drug_class in r.actions:
            cells[(drug_class, decision)][r.guideline].append(line)

    order = ["start", "do not start (avoid)", "continue", "stop"]
    blocks = []
    for (drug_class, decision) in sorted(cells, key=lambda k: (k[0].lower(), order.index(k[1]) if k[1] in order else 9)):
        by_g = cells[(drug_class, decision)]
        blocks.append({"drug class": drug_class, "decision": decision,
                       "guidelines speaking": len(by_g),
                       **{g: "\n\n".join(by_g.get(g, [])) or "–" for g in guidelines},
                       "ra_notes": ""})
    return blocks
