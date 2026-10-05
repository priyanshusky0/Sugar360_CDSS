"""What a decision-table row is, and the vocabulary that keeps rows comparable.

A row reads: IF all `conditions` hold THEN `action`, supported by `sources`.
Conditions and actions use a fixed vocabulary (configs/table1_vocab.yaml), so a
row drafted by Approach A can be compared field by field with one from
Approach B or from the doctors' answer key.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Source:
    doc_id: str  # path under data/raw/documents/, e.g. "ada/dc26s009.pdf"
    page: int  # 1-based page number in that PDF
    quote: str  # text copied from that page that supports the row


@dataclass(frozen=True)
class DecisionRow:
    row_id: str
    conditions: dict[str, str]  # IF: factor -> value; all must hold
    action: str  # THEN: one drug class from the vocabulary
    sources: tuple[Source, ...]  # SOURCE
    checks: tuple[str, ...] = ()  # CHECKS: safety checks before acting

    def signature(self) -> tuple[str, frozenset[tuple[str, str]]]:
        """The clinical meaning of the row: who it applies to and what it says to do.

        Two rows with the same signature are the same rule, even if their ids,
        wording of checks, or cited pages differ.
        """
        return (self.action, frozenset(self.conditions.items()))


def _as_text(value: Any) -> str:
    # YAML reads unquoted yes/no as True/False; turn them back into words.
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def row_from_dict(data: dict[str, Any]) -> DecisionRow:
    return DecisionRow(
        row_id=str(data["row_id"]),
        conditions={str(k): _as_text(v) for k, v in data["conditions"].items()},
        action=str(data["action"]),
        sources=tuple(
            Source(doc_id=s["doc_id"], page=int(s["page"]), quote=s["quote"])
            for s in data.get("sources", [])
        ),
        checks=tuple(data.get("checks", [])),
    )


def load_rows(path: str | Path) -> list[DecisionRow]:
    """Load rows from a YAML file shaped like {rows: [ {row_id, conditions, ...}, ... ]}."""
    data = yaml.safe_load(Path(path).read_text())
    return [row_from_dict(r) for r in data["rows"]]


@dataclass(frozen=True)
class Vocabulary:
    factors: dict[str, frozenset[str]]  # factor name -> allowed values
    actions: frozenset[str]  # allowed drug classes

    @classmethod
    def from_yaml(cls, path: str | Path) -> Vocabulary:
        data = yaml.safe_load(Path(path).read_text())
        return cls(
            factors={k: frozenset(_as_text(v) for v in vals) for k, vals in data["factors"].items()},
            actions=frozenset(data["actions"]),
        )

    def problems(self, row: DecisionRow) -> list[str]:
        """Everything in `row` that is outside the vocabulary. Empty list = valid."""
        found = []
        if row.action not in self.actions:
            found.append(f"{row.row_id}: unknown action {row.action!r}")
        for factor, value in row.conditions.items():
            if factor not in self.factors:
                found.append(f"{row.row_id}: unknown factor {factor!r}")
            elif value not in self.factors[factor]:
                found.append(f"{row.row_id}: {factor}={value!r} not allowed")
        if not row.sources:
            found.append(f"{row.row_id}: no source")
        return found
