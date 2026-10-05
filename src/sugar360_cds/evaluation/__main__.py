"""Evaluate one or more runs.

    PYTHONPATH=src python -m sugar360_cds.evaluation runs/<run> [runs/<run> ...] [--answer-key FILE]

Always measured: vocabulary validity, citation validity, cost.
With --answer-key: recall, answer-key agreement, retrieval recall.
With 2+ runs of the same approach: run agreement (stability).
Results are printed and saved as evaluation.json in each run folder.
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import yaml

from ..extraction.pdf_text import load_pages
from .metrics import (
    CallStats,
    Score,
    citation_validity,
    cost,
    key_agreement,
    recall,
    retrieval_recall,
    run_agreement,
)
from .checks import qualifier_problems, quote_rule_problems
from .schema import Vocabulary, load_rows


def evaluate(run_dir: Path, all_pages: dict, gold=None) -> dict:
    meta = json.loads((run_dir / "run.json").read_text())
    config = yaml.safe_load((run_dir / "config.yaml").read_text())
    rows = load_rows(run_dir / "rows.yaml")
    with (run_dir / "retrieved_pages.csv").open() as f:
        sent = [(r["doc_id"], int(r["page"])) for r in csv.DictReader(f)]

    vocab = Vocabulary.from_yaml(config["vocabulary"])
    problems = [p for r in rows for p in vocab.problems(r)]
    bad_rows = {p.split(":")[0] for p in problems}

    # Quotes are checked only against pages the model was shown.
    shown = {k: all_pages[k].full_text() for k in sent}
    citations, failures = citation_validity(rows, shown)

    calls = meta.get("calls") or [meta["call"]]  # early runs stored a single "call"
    stats = [CallStats(c["input_tokens"], c["output_tokens"], c["seconds"]) for c in calls]
    prices = config.get("pricing_usd_per_million") or {}
    result = {
        "run": run_dir.name,
        "approach": meta["approach"],
        "rows": len(rows),
        "rows_per_guideline": {g: sum(r.guideline == g for r in rows) for g in sorted({r.guideline for r in rows})},
        "vocabulary_valid_rows": str(Score(len(rows) - len(bad_rows), len(rows))),
        "vocabulary_problems": problems,
        "citation_validity": str(citations),
        "citation_failures": [{"row_id": rid, **vars(src)} for rid, src in failures],
        "rows_inferred": str(Score(sum(r.label == "inferred" for r in rows), len(rows))),
        "rows_with_quote_rule_problems": str(Score(sum(bool(quote_rule_problems(r)) for r in rows), len(rows))),
        "rows_with_qualifier_flags": str(Score(sum(bool(qualifier_problems(r, vocab)) for r in rows), len(rows))),
        "cost": cost(stats, prices.get("input"), prices.get("output")),
    }
    if gold is not None:
        result["recall"] = str(recall(rows, gold))
        result["key_agreement"] = str(key_agreement(rows, gold))
        result["retrieval_recall"] = str(retrieval_recall(sent, gold))
    (run_dir / "evaluation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--answer-key", type=Path)
    parser.add_argument("--processed", default="data/processed")
    args = parser.parse_args()

    pages = load_pages(args.processed)
    gold = load_rows(args.answer_key) if args.answer_key else None
    by_approach = defaultdict(list)
    for run_dir in args.runs:
        result = evaluate(run_dir, pages, gold)
        by_approach[result["approach"]].append(load_rows(run_dir / "rows.yaml"))
        print(json.dumps(result, indent=2, ensure_ascii=False))
    for approach, runs in by_approach.items():
        if len(runs) >= 2:
            print(f"run agreement, approach {approach} ({len(runs)} runs): {run_agreement(runs):.2f}")
