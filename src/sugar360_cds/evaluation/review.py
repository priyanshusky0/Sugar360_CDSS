"""Review by the research associate: one Excel file per approach.

export_review_files() writes Approach_A.xlsx, Approach_B.xlsx, ... into a
folder. The reviewer reads each table and may fill in a verdict per row
(correct / incorrect / unsupported) and a comment.
read_review_files() collects the verdicts back, per approach.

Files are labelled with the approach name (not blinded) - see docs/decisions.md D6.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd

from .schema import DecisionRow

COLUMNS = ["row_id", "IF", "THEN", "SOURCE", "CHECKS", "verdict", "comment"]


def _describe(row: DecisionRow) -> dict[str, str]:
    return {
        "row_id": row.row_id,
        "IF": " AND ".join(f"{k} = {v}" for k, v in sorted(row.conditions.items())),
        "THEN": row.action,
        "SOURCE": "\n".join(f'{s.doc_id} p.{s.page}: "{s.quote}"' for s in row.sources),
        "CHECKS": "\n".join(row.checks),
        "verdict": "",
        "comment": "",
    }


def export_review_files(
    rows_by_approach: Mapping[str, Sequence[DecisionRow]], out_dir: str | Path
) -> list[Path]:
    """Write one Excel file per approach, e.g. {"A": rows} -> out_dir/Approach_A.xlsx."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for approach, rows in rows_by_approach.items():
        path = out_dir / f"Approach_{approach}.xlsx"
        pd.DataFrame([_describe(r) for r in rows], columns=COLUMNS).to_excel(path, index=False)
        paths.append(path)
    return paths


def read_review_files(out_dir: str | Path) -> dict[str, list[str]]:
    """Return {approach: [verdict, ...]} for every row the reviewer gave a verdict."""
    verdicts = {}
    for path in sorted(Path(out_dir).glob("Approach_*.xlsx")):
        sheet = pd.read_excel(path, dtype=str).fillna("")
        given = sheet["verdict"].str.strip()
        verdicts[path.stem.removeprefix("Approach_")] = given[given != ""].tolist()
    return verdicts
