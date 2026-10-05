"""Checks on each drafted row, shown as columns in the review workbook.

quote_rule_problems  one quote = one continuous passage on one page; at least
                     8 words, or a short cell/list item with its heading
qualifier_problems   a qualifier word in the quote (if, unless, unstable, ...)
                     that the row's IF does not reflect - a dropped condition
inferred_reasons     inputs used in the IF whose evidence words are not in the
                     quote - the condition was mapped or assumed, not stated

These are heuristics that point reviewers at likely problems; a row that passes
them is not proven correct.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .schema import DecisionRow, Vocabulary, norm_text

MIN_QUOTE_WORDS = 8
STITCHED = re.compile(r"\.\.\.|…")


def _source_text(row: DecisionRow) -> str:
    return norm_text(" ".join(f"{s.heading} {s.quote}" for s in row.sources))


def quote_rule_problems(row: DecisionRow) -> list[str]:
    problems = []
    for s in row.sources:
        where = f"p.{s.page}"
        if STITCHED.search(s.quote):
            problems.append(f"{where}: stitched quote (contains '...')")
        if len(s.quote.split()) < MIN_QUOTE_WORDS and not s.heading.strip():
            problems.append(f"{where}: short quote ({len(s.quote.split())} words) without heading")
    return problems


THERAPY = r"(?:metformin|insulin|oads?|ohas?|oral (?:agents?|drugs?)|therapy|treatment|monotherapy|" \
          r"sulfonylureas?|sglt2\w*|glp-?1\w*|dpp-?4\w*|two|three|dual|triple|\d+ (?:months?|weeks?))"


def qualifier_problems(row: DecisionRow, vocab: Vocabulary) -> list[str]:
    """Qualifier words in the quote or heading that nothing in the IF reflects."""
    c, other = row.conditions, bool(row.other_conditions)
    stated = {k: v for k, v in c.items() if k not in row.scope_inputs}  # scope inputs are not in the quote
    has_number = any(vocab.is_number(k) for k in c)
    has_category = any(not vocab.is_number(k) for k in c)
    unstable_hf = c.get("heart_failure") == "unstable_or_hospitalized"
    on_therapy = any(k.startswith("on_") for k in c) or other
    combination = on_therapy or len(row.actions) > 1 or bool(re.search(r"\+|\bplus\b|combination", row.recommendation, re.I))
    # qualifier pattern -> is it reflected in the IF?
    reflected = {
        r"\bif\b": bool(stated) or other,
        r"\bin (?:patients|people|individuals|persons|those) (?:who|with)\b": bool(stated) or other,
        r"\bin combination with\b": combination,
        r"\btogether with\b": combination,
        r"\b(?:already |while |when )?on " + THERAPY: on_therapy,
        r"\bafter (?:\w+ ){0,2}" + THERAPY: on_therapy or "hba1c_above_target" in c,
        r"\bif\b[^.;]{0,80}\binsufficient": other or "hba1c_above_target" in c,
        r"\bunless\b": other or "metformin_contraindicated" in c,
        r"\bunstable\b": unstable_hf or other,
        r"\bhospitali[sz]": unstable_hf or other,
        r"\bmoderate": has_number or other,
        r"\bsevere": has_number or other or "symptomatic_hyperglycemia" in c or "hypoglycemia_risk" in c,
        r"\bhistory of\b": has_category or other,
        r"\binsufficient": other or "hba1c_above_target" in c or "hba1c" in c,
        r"\bcontraindicat": other or "metformin_contraindicated" in c,
    }
    text = _source_text(row)
    return [
        f"'{m.group(0)}' in quote not reflected in IF"  # the whole word as written, e.g. 'hospitalized'
        for pattern, ok in reflected.items()
        if not ok and (m := re.search(pattern + r"\w*", text))
    ]


def inferred_reasons(row: DecisionRow, vocab: Vocabulary) -> list[str]:
    """Inputs in the IF with none of their evidence words in the quote or heading."""
    text = _source_text(row)
    reasons = []
    for name in row.conditions:
        if name in row.scope_inputs:  # from the document's population, not expected in the quote
            continue
        spec = vocab.inputs.get(name)
        if spec and spec.evidence and not any(re.search(p, text, re.IGNORECASE) for p in spec.evidence):
            reasons.append(f"'{spec.label}' not written in the quote")
    return reasons


HISTORY = re.compile(r"\b(?:historically|traditionally)\b", re.IGNORECASE)


def apply_history_rule(row: DecisionRow) -> DecisionRow:
    """A statement worded as history ("historically has been first-line") is not a current
    recommendation: strength becomes 'consider' and the row is marked inferred (D15)."""
    text = " ".join([row.recommendation, *(s.quote for s in row.sources)])
    if not HISTORY.search(text):
        return row
    reason = f"worded as history ('{HISTORY.search(text).group(0)}'), not a current recommendation; strength set to consider"
    return replace(row, strength="consider", label="inferred",
                   label_reasons=tuple(dict.fromkeys(row.label_reasons + (reason,))))


def if_problems(row: DecisionRow) -> list[str]:
    """Every row needs at least one IF condition; otherwise it would fire for every patient."""
    return [] if row.conditions or row.other_conditions else ["no IF condition: row would apply to every patient"]


def apply_scope(row: DecisionRow, document_scope: dict[str, dict[str, str]]) -> DecisionRow:
    """Add the document's population (e.g. KDIGO 2022: CKD = yes) to the IF of every row it sources."""
    added = {}
    for s in row.sources[:1]:  # the first (main) source decides
        for name, value in document_scope.get(s.doc_id, {}).items():
            if name not in row.conditions:
                added[name] = str(value)
    if not added:
        return row
    return replace(row, conditions={**row.conditions, **added}, scope_inputs=tuple(added))


def apply_inferred_label(row: DecisionRow, vocab: Vocabulary) -> DecisionRow:
    """Mark a row inferred when code finds a condition the quote does not state (safety net)."""
    reasons = inferred_reasons(row, vocab)
    if not reasons:
        return row
    return replace(row, label="inferred", label_reasons=tuple(dict.fromkeys(row.label_reasons + tuple(reasons))))
