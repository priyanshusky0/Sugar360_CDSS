"""Tests for the automatic conflict flags and the Compare-by-drug layout (docs/decisions.md D15).

All rows here are synthetic test data, NOT clinical guidance.
"""

from pathlib import Path

from sugar360_cds.conflicts.compare import (
    AVOID, THRESHOLD, compare_by_drug, different_cutoff, find_conflicts, interval, overlap,
)
from sugar360_cds.evaluation.schema import DecisionRow, Source, Vocabulary

VOCAB = Vocabulary.from_yaml(Path(__file__).parent.parent / "configs" / "table1_vocab_v3.yaml")


def row(guideline, *actions, action_type="start", strength="recommended", other=(), drugs=(), **conditions):
    return DecisionRow(f"{guideline}-{action_type}-{'-'.join(actions)}", conditions, actions,
                       (Source(f"{guideline}.pdf", 1, "quote"),), action_type=action_type,
                       strength=strength, guideline=guideline, other_conditions=tuple(other),
                       specific_drugs=tuple(drugs))


def types(rows):
    return [c.conflict_type for c in find_conflicts(rows, VOCAB)[0]]


# ---------- (a) different threshold ----------

def test_same_decision_different_cutoff_is_flagged():
    rows = [row("ADA", "insulin", hba1c=">10"), row("ICMR", "insulin", hba1c=">9")]
    conflicts = find_conflicts(rows, VOCAB)[0]
    assert [c.conflict_type for c in conflicts] == [THRESHOLD]
    assert conflicts[0].note == "Both say start insulin, at different cut-offs: ADA HbA1c >10 %; ICMR HbA1c >9 %."


def test_do_not_start_cutoffs_compared_with_each_other():
    rows = [row("ADA", "metformin", action_type="avoid", egfr="<45"),
            row("ICMR", "metformin", action_type="avoid", egfr="<30")]
    assert types(rows) == [THRESHOLD]


def test_adjacent_and_nested_ranges_are_not_flagged():
    for other in ("45-60", ">=60"):
        assert types([row("ADA", "metformin", action_type="continue", egfr="<45"),
                      row("ICMR", "metformin", action_type="continue", egfr=other)]) == []
    assert types([row("ADA", "metformin", action_type="continue", egfr=">=30"),
                  row("ICMR", "metformin", action_type="continue", egfr="45-60")]) == []


# ---------- (b) recommend vs avoid ----------

def test_start_versus_do_not_start_overlapping_range():
    rows = [row("ADA", "metformin", action_type="start", egfr=">=30"),
            row("ICMR", "metformin", action_type="avoid", egfr="<45")]
    conflicts = find_conflicts(rows, VOCAB)[0]
    assert [c.conflict_type for c in conflicts] == [AVOID]
    assert "ADA says start metformin" in conflicts[0].note and "ICMR says do not start (avoid) metformin" in conflicts[0].note


def test_continue_versus_stop_overlapping_range():
    rows = [row("ADA", "metformin", action_type="continue", egfr="30-45"),
            row("ICMR", "metformin", action_type="stop", egfr="<45")]
    assert types(rows) == [AVOID]


def test_complementary_ranges_are_not_flagged():
    rows = [row("KDIGO", "metformin", action_type="continue", egfr=">=30"),
            row("RSSDI", "metformin", action_type="stop", egfr="<30")]
    assert types(rows) == []


# ---------- never compared ----------

def test_do_not_start_and_stop_are_different_decisions():
    # "don't start below 45" and "stop below 30" must never be compared.
    rows = [row("ADA", "metformin", action_type="avoid", egfr="<45"),
            row("KDIGO", "metformin", action_type="stop", egfr="<30")]
    assert types(rows) == []


def test_start_and_continue_are_different_decisions():
    rows = [row("ADA", "metformin", action_type="start", egfr=">=45"),
            row("KDIGO", "metformin", action_type="continue", egfr=">=30")]
    assert types(rows) == []


def test_different_drugs_and_strengths_are_not_flagged():
    assert types([row("ADA", "GLP1_RA", strength="preferred", ascvd="yes"),
                  row("RSSDI", "DPP4i", strength="preferred", ascvd="yes")]) == []
    assert types([row("ADA", "SGLT2i", strength="preferred", ckd="yes"),
                  row("RSSDI", "SGLT2i", strength="consider", ckd="yes")]) == []


def test_specific_drug_is_not_compared_with_its_class():
    rows = [row("ADA", "GLP1_RA", action_type="avoid", drugs=["exenatide"], egfr="<=30"),
            row("KDIGO", "GLP1_RA", action_type="start", egfr=">=15")]
    assert types(rows) == []


def test_specific_drug_is_compared_with_the_same_drug():
    rows = [row("ADA", "sulfonylurea", action_type="avoid", drugs=["glyburide"], ckd="yes"),
            row("ICMR", "sulfonylurea", action_type="start", drugs=["glyburide"], ckd="yes")]
    assert types(rows) == [AVOID]


def test_reduce_dose_is_never_compared():
    rows = [row("A", "insulin", action_type="reduce_dose", egfr="<30"),
            row("B", "insulin", action_type="start", egfr="<30")]
    assert types(rows) == []


def test_different_situations_are_not_compared():
    rows = [row("ADA", "metformin", action_type="avoid", heart_failure="unstable_or_hospitalized"),
            row("RSSDI", "metformin", action_type="continue", heart_failure="stable")]
    assert types(rows) == []


def test_ranges():
    assert overlap(interval(">=30"), interval("30-45"))
    assert not overlap(interval("<45"), interval("45-60"))
    assert different_cutoff(interval(">9"), interval(">10"))
    assert not different_cutoff(interval("<45"), interval(">=60"))


# ---------- Compare by drug ----------

def test_compare_by_drug_lays_out_each_guideline_side_by_side():
    rows = [row("ADA", "metformin", action_type="avoid", egfr="<45"),
            row("KDIGO", "metformin", action_type="stop", egfr="<30"),
            row("ICMR", "sulfonylurea", action_type="avoid", drugs=["glyburide"], ckd="yes")]
    blocks = compare_by_drug(rows, VOCAB, ["ICMR", "ADA", "KDIGO"])
    keys = [(b["drug class"], b["decision"]) for b in blocks]
    assert keys == [("metformin", "do not start (avoid)"), ("metformin", "stop"), ("sulfonylurea", "do not start (avoid)")]
    first = blocks[0]
    assert "eGFR <45" in first["ADA"] and first["KDIGO"] == "–" and first["ICMR"] == "–"
    assert "[glyburide]" in blocks[2]["ICMR"]
    assert "verdict" not in " ".join(blocks[0])  # no automatic verdict column


def test_one_disagreement_is_one_conflict_listing_all_rows():
    rows = [row("ADA", "metformin", action_type="avoid", egfr="<45"),
            DecisionRow("K1", {"egfr": ">=30"}, ("metformin",), (Source("k.pdf", 20, "q1"),), action_type="start",
                        strength="recommended", guideline="KDIGO"),
            DecisionRow("K2", {"egfr": ">=30"}, ("metformin",), (Source("k.pdf", 22, "q2"),), action_type="start",
                        strength="recommended", guideline="KDIGO")]
    [conflict] = find_conflicts(rows, VOCAB)[0]
    assert conflict.conflict_type == AVOID
    assert "[K1]" in conflict.says_2 and "[K2]" in conflict.says_2
