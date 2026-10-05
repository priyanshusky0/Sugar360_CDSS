"""Tests for the evaluation code.

All rows here are synthetic test data, NOT clinical guidance.
"""

from pathlib import Path

import pandas as pd
import pytest

from sugar360_cds.evaluation.metrics import (
    CallStats,
    Score,
    citation_validity,
    cost,
    key_agreement,
    recall,
    retrieval_recall,
    reviewed_precision,
    run_agreement,
    unmatched,
)
from sugar360_cds.evaluation.review import export_review_files, read_review_files
from sugar360_cds.evaluation.schema import DecisionRow, Source, Vocabulary, row_from_dict

VOCAB_PATH = Path(__file__).parent.parent / "configs" / "table1_vocab.yaml"


def make_row(row_id, action, page=1, quote="add an sglt2 inhibitor", **conditions):
    return DecisionRow(
        row_id=row_id,
        conditions=conditions,
        action=action,
        sources=(Source("ada/test.pdf", page, quote),),
    )


GOLD = [
    make_row("K1", "SGLT2i", page=3, on_metformin="yes", hba1c_vs_target="above", heart_failure="yes"),
    make_row("K2", "GLP1_RA", page=4, on_metformin="yes", hba1c_vs_target="above", ascvd="yes"),
    make_row("K3", "sulfonylurea", page=5, on_metformin="yes", hba1c_vs_target="above", cost_constraint="yes"),
]


# ---------- schema ----------


def test_yaml_booleans_become_yes_no():
    row = row_from_dict({"row_id": "X", "conditions": {"on_metformin": True}, "action": "SGLT2i"})
    assert row.conditions == {"on_metformin": "yes"}


def test_vocabulary_accepts_valid_row_and_flags_invalid_one():
    vocab = Vocabulary.from_yaml(VOCAB_PATH)
    assert vocab.problems(GOLD[0]) == []

    bad = make_row("B1", "magic_pill", hba1c_vs_target="very_high", mood="happy")
    problems = vocab.problems(bad)
    assert len(problems) == 3  # unknown action, bad value, unknown factor


# ---------- 1 & 2: recall and precision ----------


def test_perfect_draft_scores_full_recall_and_agreement():
    assert recall(GOLD, GOLD) == Score(3, 3)
    assert key_agreement(GOLD, GOLD) == Score(3, 3)


def test_extra_condition_makes_a_different_rule():
    # Same drug, but one extra condition -> applies to different patients -> no match.
    drafted = [make_row("D1", "SGLT2i", on_metformin="yes", hba1c_vs_target="above",
                        heart_failure="yes", albuminuria="yes")]
    assert recall(drafted, GOLD) == Score(0, 3)


def test_ids_and_condition_order_do_not_matter():
    drafted = [make_row("whatever", "GLP1_RA", ascvd="yes", hba1c_vs_target="above", on_metformin="yes")]
    assert recall(drafted, GOLD) == Score(1, 3)


def test_duplicate_drafts_count_once():
    drafted = [GOLD[0], GOLD[0]]
    assert recall(drafted, GOLD) == Score(1, 3)
    assert key_agreement(drafted, GOLD) == Score(1, 2)
    assert len(unmatched(drafted, GOLD)) == 1  # the duplicate goes to doctor review


def test_unmatched_rows_are_sent_for_review():
    extra = make_row("D9", "insulin", hba1c_vs_target="above")
    assert unmatched([GOLD[0], extra], GOLD) == [extra]


def test_reviewed_precision_counts_correct_verdicts():
    assert reviewed_precision(["correct", "Correct ", "incorrect", "unsupported"]) == Score(2, 4)


def test_reviewed_precision_rejects_typos():
    with pytest.raises(ValueError):
        reviewed_precision(["corect"])


def test_empty_score_has_no_value():
    assert key_agreement([], GOLD).value is None


# ---------- 3: citations ----------


def test_citation_found_despite_pdf_spacing_noise():
    pages = {("ada/test.pdf", 1): "If HbA1c is above target, ADD  an SGLT2\ninhibitor with proven benefit."}
    score, failures = citation_validity([make_row("D1", "SGLT2i")], pages)
    assert score == Score(1, 1) and failures == []


def test_invented_quote_and_missing_page_fail():
    pages = {("ada/test.pdf", 1): "Metformin remains first-line therapy."}
    rows = [make_row("D1", "SGLT2i", page=1), make_row("D2", "SGLT2i", page=99)]
    score, failures = citation_validity(rows, pages)
    assert score == Score(0, 2)
    assert [row_id for row_id, _ in failures] == ["D1", "D2"]


# ---------- 4: retrieval ----------


def test_retrieval_recall_counts_rows_whose_page_was_retrieved():
    retrieved = [("ada/test.pdf", 3), ("ada/test.pdf", 5), ("ada/test.pdf", 8)]
    assert retrieval_recall(retrieved, GOLD) == Score(2, 3)  # page 4 (K2) was missed


# ---------- 5: cost ----------


def test_cost_adds_up_calls():
    calls = [CallStats(1_000_000, 10_000, 2.0), CallStats(500_000, 10_000, 1.0)]
    result = cost(calls, usd_per_million_input=1.0, usd_per_million_output=10.0)
    assert result["input_tokens"] == 1_500_000
    assert result["seconds"] == 3.0
    assert result["usd"] == pytest.approx(1.5 + 0.2)


# ---------- 6: stability ----------


def test_identical_runs_agree_fully():
    assert run_agreement([GOLD, GOLD, GOLD]) == 1.0


def test_partially_different_runs():
    # Run 1 has K1,K2; run 2 has K1,K3 -> share 1 of 3 distinct rules.
    assert run_agreement([GOLD[:2], [GOLD[0], GOLD[2]]]) == pytest.approx(1 / 3)


def test_agreement_needs_two_runs():
    with pytest.raises(ValueError):
        run_agreement([GOLD])


# ---------- review files ----------


def test_review_files_round_trip(tmp_path):
    paths = export_review_files({"A": GOLD[:2], "B": GOLD[2:]}, tmp_path)
    assert [p.name for p in paths] == ["Approach_A.xlsx", "Approach_B.xlsx"]

    # Simulate the reviewer: judge both rows in A, leave B's row blank.
    a = pd.read_excel(tmp_path / "Approach_A.xlsx", dtype=str)
    a["verdict"] = ["correct", "incorrect"]
    a.to_excel(tmp_path / "Approach_A.xlsx", index=False)

    verdicts = read_review_files(tmp_path)
    assert verdicts == {"A": ["correct", "incorrect"], "B": []}
    assert reviewed_precision(verdicts["A"]) == Score(1, 2)
