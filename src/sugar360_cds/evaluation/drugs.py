"""Drug-level vs class-level rules (docs/decisions.md D14).

"Glyburide: generally not recommended in CKD" is about glyburide, not about all
sulfonylureas. A row is drug-level when its quote or recommendation names
drugs of one of its classes; the named drugs then go in `specific_drugs`, and
conflicts compare them only with the same drugs. The row stays class-level
when the drugs are only examples of the class ("SGLT2 inhibitors such as
empagliflozin") - a class word used as a label ("The GLP-1 RAs lixisenatide and
exenatide should be avoided") still makes it drug-level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from .schema import DecisionRow

# Words introducing drugs as examples of a class: "SGLT2 inhibitors such as empagliflozin"
EXAMPLE = r"(?:such as|e\.g\.?|eg\.|including|for example|for instance|like|namely)\W+(?:[\w-]+\W+){0,4}?"


@dataclass(frozen=True)
class DrugNames:
    names: dict[str, tuple[str, ...]]  # class -> generic names
    class_words: dict[str, tuple[str, ...]]  # class -> regex meaning "the class"

    @classmethod
    def from_yaml(cls, path: str | Path) -> DrugNames:
        data = yaml.safe_load(Path(path).read_text())["classes"]
        return cls(
            names={c: tuple(n.lower() for n in v.get("names", [])) for c, v in data.items()},
            class_words={c: tuple(v.get("class_words", [])) for c, v in data.items()},
        )

    def specific_drugs(self, row: DecisionRow) -> tuple[str, ...]:
        """Drugs the row is specifically about, in the order of its classes; () if class-level."""
        text = " ".join([row.recommendation, *(s.quote for s in row.sources)]).lower()
        found = []
        for drug_class in row.actions:
            named = [n for n in self.names.get(drug_class, ()) if re.search(rf"\b{re.escape(n)}\b", text)]
            class_named = any(re.search(p, text) for p in self.class_words.get(drug_class, ()))
            only_examples = class_named and all(
                re.search(EXAMPLE + rf"\b{re.escape(n)}\b", text) for n in named)
            if named and not only_examples:
                found += named
        return tuple(found)

    def assign(self, row: DecisionRow) -> DecisionRow:
        return replace(row, specific_drugs=self.specific_drugs(row))


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def drug_from_table(row: DecisionRow, tables: list[list[list[str]]], names: DrugNames) -> tuple[tuple[str, ...], str]:
    """For a row quoting a table cell (it has a heading): the drug named in that table row's first cell.

    Every table row with a cell containing the quote counts; a drug is assigned
    only if exactly one drug is left (a quote equal to one cell may still be the
    start of another, longer cell). Returns ((drug label,), "") when found,
    ((), reason) when the quote fits several table rows, and ((), "") when it is
    not a table cell.
    """
    if not row.sources or not row.sources[0].heading:
        return (), ""
    quote = _norm(row.sources[0].quote)
    all_names = [n for ns in names.names.values() for n in ns]
    chosen = set()  # first cells (drug labels) of table rows containing the quote
    for table in tables:
        for cells in table:
            if not cells or not any(re.search(rf"\b{re.escape(n)}\b", _norm(cells[0])) for n in all_names):
                continue
            if quote and any(quote in _norm(cell) for cell in cells[1:]):
                chosen.add(_norm(cells[0]))
    if len(chosen) == 1:
        return (chosen.pop(),), ""
    if chosen:
        return (), f"drug not identifiable: table cell fits several rows ({', '.join(sorted(chosen))})"
    return (), ""
