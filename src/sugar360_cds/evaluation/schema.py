"""What a decision-table row is, and the vocabulary that keeps rows comparable.

A row reads: IF all `conditions` hold THEN `action_type` one of `actions`,
supported by `sources`. Conditions and actions use a fixed vocabulary
(configs/table1_vocab_v2.yaml), so rows from Approach A, Approach B, different
guidelines and the answer key can be compared field by field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

BASES = frozenset({"stated", "inferred"})
NOT_RELEVANT = frozenset({"", "-", "–", "any", "n/a", "na", "not relevant"})


@dataclass(frozen=True)
class Source:
    doc_id: str  # path under data/raw/documents/, e.g. "ada/dc26s009.pdf"
    page: int  # 1-based page number in that PDF
    quote: str  # one continuous passage copied from that page that supports the row
    heading: str = ""  # table title / list heading for a short cell or list item, same page


@dataclass(frozen=True)
class Note:
    """A check or caution, with where it comes from."""

    text: str
    basis: str  # "stated": the source says it; "inferred": follows directly from the source text


@dataclass(frozen=True)
class DecisionRow:
    row_id: str
    conditions: dict[str, str]  # IF: input -> value; all must hold
    actions: tuple[str, ...]  # THEN: drug classes; several = alternatives ("SGLT2i OR GLP1_RA")
    sources: tuple[Source, ...]  # SOURCE
    checks: tuple[Note, ...] = ()  # verify before acting
    cautions: tuple[Note, ...] = ()  # risks / side effects to warn about
    action_type: str = ""  # start, add, switch, continue, reduce_dose, stop, avoid
    strength: str = ""  # recommended, consider
    guideline: str = ""  # e.g. "ADA_2026"
    other_conditions: tuple[str, ...] = ()  # IF conditions the inputs cannot hold, in source words
    recommendation: str = ""  # THEN as the source writes it, e.g. a combination regimen
    label: str = ""  # stated: IF and THEN written in the quote; inferred: something mapped or added
    label_reasons: tuple[str, ...] = ()  # why the row is inferred
    specific_drugs: tuple[str, ...] = ()  # set when the rule is about named drugs, not the whole class
    scope_inputs: tuple[str, ...] = ()  # inputs added from the document's population (e.g. KDIGO: ckd)

    def targets(self) -> tuple[str, ...]:
        """What the rule is about: the specific drugs if named, otherwise the drug classes."""
        return self.specific_drugs or self.actions

    def signature(self) -> tuple:
        """The clinical meaning of the row: who it applies to and what it says to do.

        Two rows with the same signature are the same rule, even if their ids,
        wording of checks, strength or cited pages differ.
        """
        return (self.action_type, frozenset(self.targets()), frozenset(self.conditions.items()),
                frozenset(norm_text(c) for c in self.other_conditions))


def norm_text(text: str) -> str:
    """Lower case, single spaces: for comparing free text."""
    return " ".join(text.lower().split())


def _as_text(value: Any) -> str:
    # YAML reads unquoted yes/no as True/False; turn them back into words.
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _note(item: Any) -> Note:
    if isinstance(item, str):  # v1 rows had plain-text checks with no basis
        return Note(item, "unknown")
    return Note(str(item.get("text", "")), str(item.get("basis", "unknown")))


def _texts(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    return tuple(str(v).strip() for v in value if str(v).strip())


def row_from_dict(data: dict[str, Any]) -> DecisionRow:
    actions = data.get("actions", data.get("action", []))  # v1 rows had a single "action"
    if isinstance(actions, str):
        actions = [actions]
    return DecisionRow(
        row_id=str(data["row_id"]),
        conditions={str(k): _as_text(v) for k, v in (data.get("conditions") or {}).items()},
        actions=tuple(str(a) for a in actions),
        sources=tuple(
            Source(doc_id=s["doc_id"], page=int(s["page"]), quote=s["quote"], heading=str(s.get("heading") or ""))
            for s in data.get("sources") or []
        ),
        checks=tuple(_note(n) for n in data.get("checks") or []),
        cautions=tuple(_note(n) for n in data.get("cautions") or []),
        action_type=str(data.get("action_type", "")),
        strength=str(data.get("strength", "")),
        guideline=str(data.get("guideline", "")),
        other_conditions=_texts(data.get("other_conditions")),
        recommendation=str(data.get("recommendation") or ""),
        label=str(data.get("label") or ""),
        label_reasons=_texts(data.get("label_reasons", data.get("label_reason"))),
        specific_drugs=_texts(data.get("specific_drugs")),
        scope_inputs=_texts(data.get("scope_inputs")),
    )


def row_to_dict(row: DecisionRow) -> dict[str, Any]:
    """The inverse of row_from_dict, using plain lists so it can be saved as YAML."""
    return {
        "row_id": row.row_id,
        "guideline": row.guideline,
        "label": row.label,
        "label_reasons": list(row.label_reasons),
        "recommendation": row.recommendation,
        "conditions": dict(row.conditions),
        "other_conditions": list(row.other_conditions),
        "actions": list(row.actions),
        "specific_drugs": list(row.specific_drugs),
        "scope_inputs": list(row.scope_inputs),
        "action_type": row.action_type,
        "strength": row.strength,
        "checks": [{"text": n.text, "basis": n.basis} for n in row.checks],
        "cautions": [{"text": n.text, "basis": n.basis} for n in row.cautions],
        "sources": [{"doc_id": s.doc_id, "page": s.page, "heading": s.heading, "quote": s.quote}
                    for s in row.sources],
    }


def _unique(items: tuple) -> tuple:
    return tuple(dict.fromkeys(items))


def _combine(base: DecisionRow, row: DecisionRow) -> DecisionRow:
    """`base` with `row`'s drugs, wording, sources, checks and cautions added."""
    wording = [w for w in (base.recommendation, row.recommendation) if w]
    return replace(
        base,
        actions=_unique(base.actions + row.actions),
        recommendation=" OR ".join(dict.fromkeys(wording)),
        sources=_unique(base.sources + row.sources),
        checks=_unique(base.checks + row.checks),
        cautions=_unique(base.cautions + row.cautions),
        label="inferred" if "inferred" in (base.label, row.label) else base.label,
        label_reasons=_unique(base.label_reasons + row.label_reasons),
    )


def _merge(rows: list[DecisionRow], key) -> list[DecisionRow]:
    merged: list[DecisionRow] = []
    position: dict[tuple, int] = {}
    for row in rows:
        k = key(row)
        if k is None or k not in position:
            if k is not None:
                position[k] = len(merged)
            merged.append(row)
        else:
            merged[position[k]] = _combine(merged[position[k]], row)
    return merged


def merge_same_quote(rows: list[DecisionRow]) -> list[DecisionRow]:
    """Rows drawn from the same quote with the same IF, action type and strength are
    one recommendation the model split in two: combine their drugs and wording."""
    def key(r: DecisionRow):
        if not r.sources:
            return None
        s = r.sources[0]
        return (r.guideline, s.doc_id, s.page, norm_text(s.quote), frozenset(r.conditions.items()),
                frozenset(norm_text(c) for c in r.other_conditions), r.action_type, r.strength)
    return _merge(rows, key)


def merge_duplicates(rows: list[DecisionRow]) -> list[DecisionRow]:
    """Merge rows of the same guideline that state the same rule with the same strength.

    The first row is kept; sources, checks and cautions of the others are added
    to it. Rows that differ in strength or other conditions stay separate.
    """
    return _merge(rows, lambda r: (r.guideline, r.signature(), r.strength))


def save_rows(rows: list[DecisionRow], path: str | Path, **extra: Any) -> None:
    data = {**extra, "rows": [row_to_dict(r) for r in rows]}
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))


def load_rows(path: str | Path) -> list[DecisionRow]:
    """Load rows from a YAML file shaped like {rows: [ {row_id, conditions, ...}, ... ]}."""
    data = yaml.safe_load(Path(path).read_text())
    return [row_from_dict(r) for r in data["rows"]]


# ---------- numeric conditions ----------

_COMPARE = re.compile(r"^\s*(>=|<=|>|<|≥|≤)\s*(\d+(?:\.\d+)?)")
_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)")


def _number(text: str) -> str:
    value = float(text)
    return str(int(value)) if value.is_integer() else str(value)


def canonical_number(text: str) -> str | None:
    """One spelling per threshold: "≥ 30 mL/min" -> ">=30", "30 – 45" -> "30-45". None if unreadable.

    Without this, ">=30" and "≥30" would look like different thresholds.
    """
    if m := _COMPARE.match(text):
        op = {"≥": ">=", "≤": "<="}.get(m.group(1), m.group(1))
        return f"{op}{_number(m.group(2))}"
    if m := _RANGE.match(text):
        return f"{_number(m.group(1))}-{_number(m.group(2))}"
    return None


# ---------- vocabulary ----------


@dataclass(frozen=True)
class InputSpec:
    kind: str  # "category" or "number"
    values: frozenset[str] = frozenset()  # allowed values, for categories
    unit: str = ""  # for numbers
    label: str = ""  # human-readable name
    evidence: tuple[str, ...] = ()  # regex: words showing the source states this input


@dataclass(frozen=True)
class Vocabulary:
    inputs: dict[str, InputSpec]
    actions: frozenset[str]
    action_types: frozenset[str] = frozenset()  # empty = not checked (v1 vocabulary)
    strengths: frozenset[str] = frozenset()
    labels: frozenset[str] = frozenset()  # empty = not checked (before v3)
    skip_categories: frozenset[str] = frozenset()
    population_inputs: frozenset[str] = frozenset()  # e.g. "t2d": true of every patient, dropped from IF

    @classmethod
    def from_yaml(cls, path: str | Path) -> Vocabulary:
        data = yaml.safe_load(Path(path).read_text())
        if "factors" in data:  # v1 format: every input is a category
            inputs = {
                k: InputSpec("category", frozenset(_as_text(v) for v in vals), label=k)
                for k, vals in data["factors"].items()
            }
        else:
            inputs = {
                k: InputSpec(
                    kind=s["type"],
                    values=frozenset(_as_text(v) for v in s.get("values", [])),
                    unit=s.get("unit", ""),
                    label=s.get("label", k),
                    evidence=tuple(s.get("evidence", [])),
                )
                for k, s in data["inputs"].items()
            }
        return cls(
            inputs=inputs,
            actions=frozenset(data["actions"]),
            action_types=frozenset(data.get("action_types", [])),
            strengths=frozenset(data.get("strengths", [])),
            labels=frozenset(data.get("labels", [])),
            skip_categories=frozenset(data.get("skip_categories", [])),
            population_inputs=frozenset(n.lower() for n in data.get("population_inputs", [])),
        )

    def _input_name(self, name: str) -> str:
        """Vocabulary name for `name`, matching case-insensitively by name or label ("eGFR" -> "egfr")."""
        wanted = name.strip().lower()
        for key, spec in self.inputs.items():
            if wanted in (key.lower(), spec.label.lower()):
                return key
        return name

    def normalize(self, row: DecisionRow) -> DecisionRow:
        """Drop "not relevant" inputs, use vocabulary spellings and write numbers one way,
        so equal rules compare equal."""
        conditions = {}
        for name, value in row.conditions.items():
            name, value = self._input_name(name), value.strip()
            if value.lower() in NOT_RELEVANT or name.lower() in self.population_inputs:
                continue
            spec = self.inputs.get(name)
            if spec is not None and spec.kind == "number":
                value = canonical_number(value) or value
            elif spec is not None and value.lower() in spec.values:
                value = value.lower()
            conditions[name] = value
        actions_by_lower = {a.lower(): a for a in self.actions}
        return replace(
            row,
            conditions=conditions,
            actions=tuple(actions_by_lower.get(a.strip().lower(), a) for a in row.actions),
            action_type=row.action_type.strip().lower(),
            strength=row.strength.strip().lower(),
            label=row.label.strip().lower(),
            other_conditions=tuple(c for c in row.other_conditions if c.lower() not in NOT_RELEVANT),
        )

    def is_number(self, name: str) -> bool:
        spec = self.inputs.get(name)
        return spec is not None and spec.kind == "number"

    def label(self, name: str) -> str:
        spec = self.inputs.get(name)
        return spec.label if spec and spec.label else name

    def display(self, name: str, value: str) -> str:
        """Value as a reader sees it: ">=30" -> "≥30 mL/min/1.73 m²"."""
        if self.is_number(name):
            shown = value.replace(">=", "≥").replace("<=", "≤")
            return f"{shown} {self.inputs[name].unit}".strip()
        return value

    def condition_problems(self, row: DecisionRow) -> list[str]:
        """Problems that make a row's IF/THEN unreadable (used to decide if it can be compared)."""
        found = []
        if not row.actions:
            found.append(f"{row.row_id}: no action")
        for action in row.actions:
            if action not in self.actions:
                found.append(f"{row.row_id}: unknown action {action!r}")
        for name, value in row.conditions.items():
            spec = self.inputs.get(name)
            if spec is None:
                found.append(f"{row.row_id}: unknown input {name!r}")
            elif spec.kind == "number" and canonical_number(value) is None:
                found.append(f"{row.row_id}: {name}={value!r} is not a number like >=30 or 30-45")
            elif spec.kind == "category" and value not in spec.values:
                found.append(f"{row.row_id}: {name}={value!r} not allowed")
        return found

    def problems(self, row: DecisionRow) -> list[str]:
        """Everything in `row` that is outside the vocabulary or incomplete. Empty list = valid."""
        found = self.condition_problems(row)
        if self.action_types and row.action_type not in self.action_types:
            found.append(f"{row.row_id}: unknown action_type {row.action_type!r}")
        if self.strengths and row.strength not in self.strengths:
            found.append(f"{row.row_id}: unknown strength {row.strength!r}")
        if self.labels and row.label not in self.labels:
            found.append(f"{row.row_id}: label {row.label!r} is not stated/inferred")
        for note in (*row.checks, *row.cautions):
            if note.basis not in BASES:
                found.append(f"{row.row_id}: check/caution without stated/inferred basis: {note.text!r}")
        if not row.sources:
            found.append(f"{row.row_id}: no source")
        return found
