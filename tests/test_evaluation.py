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
    quote_score,
    recall,
    retrieval_recall,
    reviewed_precision,
    run_agreement,
    unmatched,
)
from sugar360_cds.evaluation.review import export_workbook, read_review_files
from sugar360_cds.evaluation.schema import (
    DecisionRow,
    Note,
    Source,
    Vocabulary,
    canonical_number,
    load_rows,
    merge_duplicates,
    row_from_dict,
    save_rows,
)

CONFIGS = Path(__file__).parent.parent / "configs"
VOCAB = Vocabulary.from_yaml(CONFIGS / "table1_vocab_v2.yaml")


def make_row(row_id, *actions, page=1, quote="add an sglt2 inhibitor", action_type="add",
             strength="recommended", guideline="G1", **conditions):
    return DecisionRow(
        row_id=row_id,
        conditions=conditions,
        actions=actions,
        sources=(Source("ada/test.pdf", page, quote),),
        action_type=action_type,
        strength=strength,
        guideline=guideline,
    )


GOLD = [
    make_row("K1", "SGLT2i", page=3, on_metformin="yes", hba1c_above_target="yes", heart_failure="yes"),
    make_row("K2", "GLP1_RA", page=4, on_metformin="yes", hba1c_above_target="yes", ascvd="yes"),
    make_row("K3", "sulfonylurea", page=5, on_metformin="yes", hba1c_above_target="yes", cost_constraint="yes"),
]


# ---------- schema ----------


def test_yaml_booleans_become_yes_no():
    row = row_from_dict({"row_id": "X", "conditions": {"on_metformin": True}, "actions": ["SGLT2i"]})
    assert row.conditions == {"on_metformin": "yes"}


def test_v1_rows_still_load():
    row = row_from_dict({"row_id": "X", "conditions": {}, "action": "SGLT2i", "checks": ["check eGFR"]})
    assert row.actions == ("SGLT2i",)
    assert row.checks == (Note("check eGFR", "unknown"),)


def test_rows_survive_save_and_load(tmp_path):
    row = DecisionRow("R1", {"egfr": ">=30"}, ("SGLT2i", "GLP1_RA"), (Source("d.pdf", 2, "q"),),
                      checks=(Note("check eGFR", "inferred"),), cautions=(Note("DKA risk", "stated"),),
                      action_type="add", strength="consider", guideline="ADA_2026")
    save_rows([row], tmp_path / "rows.yaml")
    assert load_rows(tmp_path / "rows.yaml") == [row]


@pytest.mark.parametrize("text, expected", [
    (">=30", ">=30"), ("≥ 30 mL/min/1.73 m²", ">=30"), ("<45", "<45"), ("≤7.0", "<=7"),
    ("30-45", "30-45"), ("30 – 45", "30-45"), ("7.5", None), ("high", None),
])
def test_canonical_number(text, expected):
    assert canonical_number(text) == expected


def test_normalize_drops_not_relevant_and_canonicalises_numbers():
    row = make_row("R", "metformin", egfr="≥ 30", age="–", on_metformin="yes")
    assert VOCAB.normalize(row).conditions == {"egfr": ">=30", "on_metformin": "yes"}


def test_normalize_fixes_spelling_of_inputs_actions_and_types():
    row = DecisionRow("R", {"eGFR": "≥30", "ASCVD": "Yes"}, ("sglt2i",), (), action_type="Add", strength="Consider")
    norm = VOCAB.normalize(row)
    assert norm.conditions == {"egfr": ">=30", "ascvd": "yes"}
    assert norm.actions == ("SGLT2i",)
    assert (norm.action_type, norm.strength) == ("add", "consider")


def test_duplicates_are_merged_keeping_all_sources():
    a = make_row("A1", "SGLT2i", page=3, ascvd="yes")
    b = make_row("A2", "SGLT2i", page=7, ascvd="yes")
    weaker = make_row("A3", "SGLT2i", page=9, strength="consider", ascvd="yes")
    other_guideline = make_row("B1", "SGLT2i", guideline="G2", ascvd="yes")
    merged = merge_duplicates([a, b, weaker, other_guideline])
    assert [r.row_id for r in merged] == ["A1", "A3", "B1"]  # different strength / guideline stay
    assert [s.page for s in merged[0].sources] == [3, 7]


def test_population_input_is_dropped():
    vocab3 = Vocabulary.from_yaml(CONFIGS / "table1_vocab_v3.yaml")
    row = DecisionRow("R", {"T2D": "yes", "ckd": "yes"}, ("SGLT2i",), ())
    assert vocab3.normalize(row).conditions == {"ckd": "yes"}


def test_display_adds_units():
    assert VOCAB.display("egfr", ">=30") == "≥30 mL/min/1.73 m²"
    assert VOCAB.display("ascvd", "yes") == "yes"


def test_vocabulary_accepts_valid_row():
    row = make_row("R", "SGLT2i", "GLP1_RA", egfr=">=20", ascvd="yes")
    row = DecisionRow(**{**row.__dict__, "checks": (Note("check eGFR", "inferred"),)})
    assert VOCAB.problems(row) == []


def test_vocabulary_flags_every_kind_of_problem():
    bad = DecisionRow("B1", {"egfr": "normal", "mood": "happy", "ascvd": "maybe"}, ("magic_pill",),
                      sources=(), checks=(Note("x", "general knowledge"),), action_type="pray", strength="strong")
    problems = " | ".join(VOCAB.problems(bad))
    for expected in ["unknown action 'magic_pill'", "not a number", "unknown input 'mood'",
                     "ascvd='maybe' not allowed", "unknown action_type", "unknown strength",
                     "without stated/inferred basis", "no source"]:
        assert expected in problems


def test_alternatives_are_one_rule_in_any_order():
    a = make_row("A", "SGLT2i", "GLP1_RA", ascvd="yes")
    b = make_row("B", "GLP1_RA", "SGLT2i", ascvd="yes")
    assert a.signature() == b.signature()


# ---------- 1 & 2: recall and precision ----------


def test_perfect_draft_scores_full_recall_and_agreement():
    assert recall(GOLD, GOLD) == Score(3, 3)
    assert key_agreement(GOLD, GOLD) == Score(3, 3)


def test_extra_condition_makes_a_different_rule():
    drafted = [make_row("D1", "SGLT2i", on_metformin="yes", hba1c_above_target="yes",
                        heart_failure="yes", ckd="yes")]
    assert recall(drafted, GOLD) == Score(0, 3)


def test_ids_condition_order_and_strength_do_not_matter():
    drafted = [make_row("whatever", "GLP1_RA", strength="consider",
                        ascvd="yes", hba1c_above_target="yes", on_metformin="yes")]
    assert recall(drafted, GOLD) == Score(1, 3)


def test_duplicate_drafts_count_once():
    drafted = [GOLD[0], GOLD[0]]
    assert recall(drafted, GOLD) == Score(1, 3)
    assert key_agreement(drafted, GOLD) == Score(1, 2)
    assert len(unmatched(drafted, GOLD)) == 1  # the duplicate goes to review


def test_unmatched_rows_are_sent_for_review():
    extra = make_row("D9", "insulin", hba1c_above_target="yes")
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


def test_quote_score_is_100_for_exact_and_lower_for_invented():
    page = "Metformin remains first-line therapy for most people."
    assert quote_score("metformin remains first-line", page) == 100
    assert quote_score("insulin is always first-line", page) < 90


def test_stitched_quote_scores_low():
    # One quote must be one continuous passage (D13): pieces joined with or without "..." fail.
    page = "Several options can be considered during intensification. In patients on basal insulin, add GLP-1 analogs."
    assert quote_score("Several options can be considered during intensification.", page) == 100
    assert quote_score("Several options can be considered ... add GLP-1 analogs", page) < 90
    assert quote_score("Several options can be considered add GLP-1 analogs", page) < 90


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


def test_cost_without_prices_is_not_guessed():
    assert cost([CallStats(10, 10, 1.0)], None, None)["usd"] is None


# ---------- 6: stability ----------


def test_identical_runs_agree_fully():
    assert run_agreement([GOLD, GOLD, GOLD]) == 1.0


def test_partially_different_runs():
    # Run 1 has K1,K2; run 2 has K1,K3 -> share 1 of 3 distinct rules.
    assert run_agreement([GOLD[:2], [GOLD[0], GOLD[2]]]) == pytest.approx(1 / 3)


def test_agreement_needs_two_runs():
    with pytest.raises(ValueError):
        run_agreement([GOLD])


# ---------- review workbook ----------


def test_workbook_has_all_sheets_and_reads_back_verdicts(tmp_path):
    import openpyxl

    rows = [make_row("A1", "SGLT2i", "GLP1_RA", egfr=">=30", ascvd="yes"), make_row("A2", "metformin")]
    checks = {"A1": {"quote_score": 100, "quote_rules": [], "qualifiers": []},
              "A2": {"quote_score": 72, "quote_rules": ["p.1: stitched quote (contains '...')"],
                     "qualifiers": ["'unstable' in quote not reflected in IF"]}}
    path = export_workbook(tmp_path / "Approach_A.xlsx", rows, VOCAB, checks,
                           coverage=[{"guideline": "G1", "rows_drafted": 2}], conflicts=[],
                           compare_blocks=[{"drug class": "metformin", "decision": "start", "G1": "...", "ra_notes": ""}],
                           skipped=[{"guideline": "G1", "text": "something", "category": "other_topic"}],
                           dose_rows=[make_row("D1", "GLP1_RA")])
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["How to read", "Rows", "Coverage", "Conflicts", "Compare by drug",
                             "For dose table", "Skipped"]

    ws = wb["Rows"]
    assert ws.freeze_panes == "C3"  # header rows + row_id and guideline stay visible
    headers = [c.value for c in ws[2]]
    assert headers[:2] == ["row_id", "guideline"]
    groups = [c.value for c in ws[1] if c.value]
    assert groups == ["IF", "THEN", "SOURCE", "AUTO CHECKS", "REVIEW"]
    # Each group header is merged across its columns, in this order.
    merged = sorted((r.min_col, ws.cell(row=1, column=r.min_col).value) for r in ws.merged_cells.ranges)
    assert [g for _, g in merged if g] == ["IF", "THEN", "SOURCE", "AUTO CHECKS", "REVIEW"]

    col = {h: i + 1 for i, h in enumerate(headers)}
    letter = lambda h: openpyxl.utils.get_column_letter(col[h])
    assert ws.column_dimensions[letter("Age (years)")].hidden  # no row uses age
    assert not ws.column_dimensions[letter("eGFR (mL/min/1.73 m²)")].hidden
    cell = lambda r, h: ws.cell(row=r, column=col[h]).value
    assert cell(3, "THEN") == "ADD an SGLT2 inhibitor OR a GLP-1 RA"
    assert cell(3, "eGFR (mL/min/1.73 m²)") == "≥30 mL/min/1.73 m²"
    assert cell(3, "other conditions") == "–"
    assert [cell(3, "quote check"), cell(4, "quote check")] == ["100", "72 - p.1: stitched quote (contains '...')"]
    assert cell(4, "qualifier check") == "'unstable' in quote not reflected in IF"
    for column in ["stated / inferred", "inferred because", "heading", "quote", "specific drug", "strength",
                   "IF check", "THEN check", "ra_verdict", "ra_comment", "doctor_verdict", "doctor_comment"]:
        assert column in col
    assert "recommendation (as written)" not in col and "IF from guideline scope" not in col
    assert "doctor_decision" in [c.value for c in wb["Conflicts"][1]]
    assert wb["How to read"]["A1"].value == "How to read this decision table"

    # Simulate the research associate judging A1 only.
    ws.cell(row=3, column=col["ra_verdict"], value="correct")
    wb.save(path)
    assert read_review_files(tmp_path, who="ra") == {"A": ["correct"]}
    assert read_review_files(tmp_path, who="doctor") == {"A": []}


def test_scope_condition_is_shown_in_its_own_input_column():
    row = DecisionRow("K1", {"ckd": "yes", "egfr": ">=20"}, ("SGLT2i",), (Source("k.pdf", 3, "q"),),
                      action_type="start", scope_inputs=("ckd",))
    from sugar360_cds.evaluation.review import rule_columns
    cols = rule_columns(row, VOCAB, {})
    assert cols["IF"]["CKD"] == "yes (from guideline scope)"
