"""THEN as a readable sentence that separates combinations from alternatives (docs/decisions.md D16).

    AND = give together (a combination)       OR = choose one (alternatives)

Read from the source wording of the recommendation:
  - " OR " (joined by merging) separates options
  - "/" separates options when one side names a whole regimen
    ("... + another OAD / triple OAD combination"); otherwise "/" means
    "one of" inside a combination ("Metformin + SU/TZD/DPP-4i")
  - "+" and "plus" join drugs given together
Without these symbols, the row's drugs (specific drugs, else classes) are
alternatives. Rows whose structure was read from the symbols are flagged for a
reviewer to confirm: the same "/" can mean either, and only reading the
guideline settles it. A future prompt version should ask the model for the
structure directly.
"""

from __future__ import annotations

import re

from .schema import DecisionRow

VERB = {"start": "START", "add": "ADD", "switch": "SWITCH TO", "continue": "CONTINUE",
        "stop": "STOP", "avoid": "DO NOT START", "reduce_dose": "REDUCE DOSE OF"}
CLASS_NAMES = {"SGLT2i": "an SGLT2 inhibitor", "GLP1_RA": "a GLP-1 RA", "GIP_GLP1_RA": "a GIP/GLP-1 RA",
               "DPP4i": "a DPP-4 inhibitor", "sulfonylurea": "a sulfonylurea", "TZD": "a TZD (pioglitazone)",
               "AGI": "an alpha-glucosidase inhibitor", "glinide": "a glinide", "insulin": "insulin",
               "metformin": "metformin", "finerenone": "finerenone"}
REGIMEN = re.compile(r"\b(?:combination|therapy|regimen)\b", re.IGNORECASE)
PLAN_LABEL = re.compile(r"^\s*(?:mono|dual|triple|quadruple)\s*therapy\s*[-–:]?\s*", re.IGNORECASE)
AND_SPLIT = re.compile(r"\s*\+\s*|\s+plus\s+", re.IGNORECASE)
# "/" that is not a choice: units, "and/or", fixed drug names. Protected before reading "/".
NOT_A_CHOICE = re.compile(
    r"\band/or\b|\bsglt1/2\b|\bgip/glp-?1\b|"
    r"\b(?:ml|mg|mmol|kg|μg|µg|g)\s*/\s*(?:min|dl|l|mol|m2|m²|kg|day|d)\b(?:\s*/\s*1\.73(?:\s*m(?:2|²)?)?)?",
    re.IGNORECASE)
SLASH = "\u2215"  # stand-in for a protected "/"
MAX_COMPONENT_WORDS = 8


def _tidy(text: str) -> str:
    return " ".join(text.split()).strip(" ,.;-–")


def _component(text: str) -> str:
    parts = [_tidy(p) for p in text.split("/") if _tidy(p)]
    return parts[0] if len(parts) == 1 else "one of (" + " / ".join(parts) + ")"


def structure(recommendation: str) -> list[list[str]] | None:
    """Options (OR) of components (AND) read from the wording, or None if it has no +, plus, / or OR
    (after protecting units and fixed names), or if a component is a whole sentence."""
    text = PLAN_LABEL.sub("", recommendation or "")
    text = NOT_A_CHOICE.sub(lambda m: m.group(0).replace("/", SLASH), text)
    if not re.search(r"\+|\bplus\b|/| OR ", text, re.IGNORECASE):
        return None
    options = []
    for part in text.split(" OR "):
        pieces = part.split("/")
        # "/" between whole regimens: split into options there; otherwise keep "/" inside a component.
        if len(pieces) > 1 and any(REGIMEN.search(p) for p in pieces[1:]):
            cut = next(i for i, p in enumerate(pieces) if i > 0 and REGIMEN.search(p))
            chunks = ["/".join(pieces[:cut]), *pieces[cut:]]
        else:
            chunks = [part]
        for chunk in chunks:
            raw = [c for c in AND_SPLIT.split(chunk) if _tidy(c)]
            # Each drug piece (between "+" and "/") should be a name, not a sentence.
            if any(len(_tidy(piece).split()) > MAX_COMPONENT_WORDS for c in raw for piece in c.split("/")):
                return None  # wording too complex to read as AND/OR
            if raw:
                options.append([_component(c).replace(SLASH, "/") for c in raw])
    return options or None


def then_sentence(row: DecisionRow) -> tuple[str, str]:
    """(readable THEN, check note). The note is "" when nothing was read from symbols."""
    verb = VERB.get(row.action_type, row.action_type.upper())
    options = structure(row.recommendation)
    if options:
        text = " OR ".join(("(" + " AND ".join(o) + ")") if len(o) > 1 and len(options) > 1 else " AND ".join(o)
                           for o in options)
        return f"{verb} {text}", "AND/OR read by code from '+', '/', 'OR' in the source wording - please confirm"
    drugs = list(row.specific_drugs) or [CLASS_NAMES.get(a, a) for a in row.actions]
    combination = bool(re.search(r"\+|\bplus\b", row.recommendation, re.IGNORECASE))
    note = "source says '+' or 'plus' but its wording was too complex to read - check AND/OR" \
        if combination and len(drugs) > 1 else ""
    return f"{verb} {' OR '.join(drugs)}", note
