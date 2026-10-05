"""Tests for the per-row checks, using the rows the reviewer named (synthetic copies).

All rows here are test data, NOT clinical guidance.
"""

from pathlib import Path

from sugar360_cds.evaluation.checks import (
    apply_inferred_label,
    inferred_reasons,
    qualifier_problems,
    quote_rule_problems,
)
from sugar360_cds.evaluation.schema import DecisionRow, Source, Vocabulary, merge_same_quote

VOCAB = Vocabulary.from_yaml(Path(__file__).parent.parent / "configs" / "table1_vocab_v3.yaml")


def row(quote, *actions, heading="", other=(), label="stated", **conditions):
    return DecisionRow("R", conditions, actions or ("metformin",), (Source("d.pdf", 1, quote, heading),),
                       action_type="avoid", strength="recommended", guideline="G",
                       other_conditions=tuple(other), label=label)


# ---------- qualifiers (point 1) ----------

def test_dropped_unstable_heart_failure_is_flagged():  # like ADA-44
    quote = "but should be avoided in unstable or hospitalized individuals with heart failure."
    assert qualifier_problems(row(quote, heart_failure="yes"), VOCAB) == [
        "'unstable' in quote not reflected in IF", "'hospitalized' in quote not reflected in IF"]
    assert qualifier_problems(row(quote, heart_failure="unstable_or_hospitalized"), VOCAB) == []


def test_moderate_kidney_disease_needs_number_or_other_condition():  # like ICMR_2018-12
    quote = "AGI are contraindicated in moderate to end stage renal disease"
    assert len(qualifier_problems(row(quote, "AGI", ckd="yes"), VOCAB)) == 2  # 'contraindicat', 'moderate'
    fixed = row(quote, "AGI", other=["moderate to end-stage renal disease"], ckd="yes")
    assert qualifier_problems(fixed, VOCAB) == []


def test_qualifier_in_heading_counts_too():  # like KDIGO-11
    flagged = row("Genital infection risk", "SGLT2i", heading="Potential contraindications", ckd="yes")
    assert qualifier_problems(flagged, VOCAB) == ["'contraindications' in quote not reflected in IF"]
    ok = row("Genital infection risk", "SGLT2i", heading="Potential contraindications",
             other=["genital infection risk"], ckd="yes")
    assert qualifier_problems(ok, VOCAB) == []


def test_if_is_fine_when_the_row_has_a_condition():
    assert qualifier_problems(row("Metformin may be given if eGFR is at least 30", egfr=">=30"), VOCAB) == []
    assert qualifier_problems(row("Metformin may be given if tolerated by the patient"), VOCAB) == [
        "'if' in quote not reflected in IF"]


# ---------- quote rules (point 2) ----------

def test_stitched_and_short_quotes_are_flagged():
    assert quote_rule_problems(row("Several options can be considered ... add GLP-1 analogs today")) == [
        "p.1: stitched quote (contains '...')"]
    assert quote_rule_problems(row("Moderate to End Stage Renal Disease")) == [
        "p.1: short quote (6 words) without heading"]


def test_short_cell_with_heading_is_allowed():
    assert quote_rule_problems(row("Very high: Semaglutide, tirzepatide", heading="Table 9.2 Efficacy")) == []


# ---------- inferred label (point 6) ----------

def test_on_metformin_not_in_regimen_quote_makes_row_inferred():  # like STW rows 3/4
    quote = "HbA1c > 10%: Basal Insulin+ Metformin + another OAD / triple OAD combination"
    r = row(quote, "insulin", hba1c=">10", on_metformin="yes")
    assert inferred_reasons(r, VOCAB) == ["'On metformin' not written in the quote"]
    labelled = apply_inferred_label(r, VOCAB)
    assert labelled.label == "inferred"
    assert "'On metformin' not written in the quote" in labelled.label_reasons


def test_stated_condition_keeps_label():
    r = row("In people on metformin with HbA1c above target, add an SGLT2 inhibitor",
            on_metformin="yes", hba1c_above_target="yes")
    assert apply_inferred_label(r, VOCAB).label == "stated"


def test_same_quote_rows_are_merged_into_one_recommendation():  # STW rows 3 and 4
    quote = "HbA1c > 10%: Basal Insulin+ Metformin + another OAD / triple OAD combination"
    a = DecisionRow("3", {"hba1c": ">10"}, ("insulin", "SGLT2i"), (Source("stw.pdf", 1, quote),), action_type="add",
                    strength="recommended", guideline="STW", recommendation="Basal insulin + metformin + another OAD")
    b = DecisionRow("4", {"hba1c": ">10"}, ("SGLT2i", "DPP4i"), (Source("stw.pdf", 1, quote),), action_type="add",
                    strength="recommended", guideline="STW", recommendation="Triple OAD combination")
    [merged] = merge_same_quote([a, b])
    assert merged.actions == ("insulin", "SGLT2i", "DPP4i")
    assert merged.recommendation == "Basal insulin + metformin + another OAD OR Triple OAD combination"


def test_rows_with_different_other_conditions_are_not_merged():  # KDIGO-11 hid four conditions
    from sugar360_cds.evaluation.schema import merge_duplicates
    rows = [row("Potential contraindications: genital infection risk, diabetic ketoacidosis", "SGLT2i",
                other=[c], ckd="yes") for c in ("genital infection risk", "diabetic ketoacidosis")]
    assert len(merge_duplicates(rows)) == 2


# ---------- D14: drug vs class, scope, no-IF, new qualifiers, dose rows ----------

from sugar360_cds.evaluation.checks import apply_scope, if_problems  # noqa: E402
from sugar360_cds.evaluation.drugs import DrugNames  # noqa: E402
from sugar360_cds.pipeline import is_dose_row  # noqa: E402

DRUGS = DrugNames.from_yaml(Path(__file__).parent.parent / "configs" / "drug_names.yaml")


def test_named_drug_without_class_word_is_drug_level():  # ADA-29, ICMR_2018-15
    assert DRUGS.specific_drugs(row("Glyburide: generally not recommended in CKD", "sulfonylurea")) == ("glyburide",)
    assert DRUGS.specific_drugs(row("Saxagliptin should be avoided in cardiac failure", "DPP4i")) == ("saxagliptin",)


def test_class_statement_stays_class_level():  # KDIGO-49
    quote = "DPP-4 inhibitors lower blood glucose ... should not be used in combination with GLP-1 RA."
    assert DRUGS.specific_drugs(row(quote, "DPP4i")) == ()
    assert DRUGS.specific_drugs(row("SGLT2 inhibitors such as empagliflozin reduce risk", "SGLT2i")) == ()


def test_kdigo_rows_get_ckd_from_scope_and_are_not_inferred_for_it():
    scope = {"kdigo.pdf": {"ckd": "yes"}}
    r = DecisionRow("R", {}, ("SGLT2i",), (Source("kdigo.pdf", 3, "SGLT2i should be initiated when eGFR is at least 20"),))
    scoped = apply_scope(r, scope)
    assert scoped.conditions == {"ckd": "yes"} and scoped.scope_inputs == ("ckd",)
    assert inferred_reasons(scoped, VOCAB) == []
    assert if_problems(r) == ["no IF condition: row would apply to every patient"] and if_problems(scoped) == []


def test_new_qualifier_phrases():
    q = "In patients who are already on metformin and insulin, add a GLP-1 RA in combination with basal insulin"
    flags = qualifier_problems(row(q, "GLP1_RA", hba1c_above_target="yes"), VOCAB)
    assert any("on metformin" in f for f in flags)
    ok = row(q, "GLP1_RA", "insulin", other=["on metformin and insulin"], hba1c_above_target="yes")
    assert qualifier_problems(ok, VOCAB) == []
    assert qualifier_problems(row("Results were based on the evidence for most adults reviewed"), VOCAB) == []


def test_dose_rows_are_recognised():  # C015 / RSSDI-54
    def r(rec, action_type="start"):
        return DecisionRow("R", {}, ("GLP1_RA",), (), action_type=action_type, recommendation=rec)
    assert is_dose_row(r("Starting dose is 0.75 mg once weekly, titrated to 1.5 mg"))
    assert is_dose_row(r("start with a low dose and titrate up slowly"))
    assert is_dose_row(r("reduce sulfonylurea", action_type="reduce_dose"))
    assert not is_dose_row(r("Add a GLP-1 RA if HbA1c is above target"))


def test_class_word_as_label_is_still_drug_level():  # ADA-62 / C008
    quote = "The GLP-1 RAs lixisenatide and exenatide, which require the kidneys for elimination, should be avoided"
    assert DRUGS.specific_drugs(row(quote, "GLP1_RA")) == ("exenatide", "lixisenatide")
    assert DRUGS.specific_drugs(row("GLP-1 RAs (e.g., liraglutide, semaglutide) reduce CV events", "GLP1_RA")) == ()


# ---------- D15: table drug names, history, current-medicine inputs ----------

from sugar360_cds.evaluation.checks import apply_history_rule  # noqa: E402
from sugar360_cds.evaluation.drugs import drug_from_table  # noqa: E402

FIG29 = [[["GLP-1RA", "Dose", "CKD adjustment"],
          ["Exenatide", "10 μg twice daily", "Use with CrCl >30 ml/min"],
          ["Exenatide extended-release", "2 mg once weekly", "Use with eGFR >45 ml/min per 1.73 m2"],
          ["Liraglutide", "1.2 mg", "No dosage adjustment Limited data for severe CKD"],
          ["Lixisenatide", "10 μg", "No dosage adjustment Limited data for severe CKD Not recommended with eGFR <15"]]]


def test_drug_taken_from_the_table_row():  # KDIGO p.88
    r = row("Use with CrCl >30 ml/min", "GLP1_RA", heading="Figure 29| Dosing for available GLP-1 RA")
    assert drug_from_table(r, FIG29, DRUGS) == (("exenatide",), "")
    r = row("Not recommended with eGFR <15", "GLP1_RA", heading="Figure 29")
    assert drug_from_table(r, FIG29, DRUGS) == (("lixisenatide",), "")


def test_table_cell_fitting_several_rows_gets_no_drug():
    r = row("No dosage adjustment Limited data for severe CKD", "GLP1_RA", heading="Figure 29")
    drugs, why = drug_from_table(r, FIG29, DRUGS)
    assert drugs == () and why.startswith("drug not identifiable")


def test_dosing_table_heading_moves_row_to_dose_sheet():
    r = DecisionRow("R", {"egfr": ">30"}, ("GLP1_RA",), (Source("k.pdf", 88, "Use with CrCl >30 ml/min",
                    "Figure 29| Dosing for available GLP-1 RA"),), action_type="start", recommendation="Use with CrCl >30")
    assert is_dose_row(r)


def test_historical_statement_becomes_consider():  # ADA-30
    r = DecisionRow("R", {}, ("metformin",), (Source("ada.pdf", 14, "Metformin is a commonly used medication that "
                    "historically has been the first-line treatment for type 2 diabetes"),), strength="preferred",
                    label="inferred")
    out = apply_history_rule(r)
    assert out.strength == "consider" and "worded as history" in out.label_reasons[0]
    assert apply_history_rule(row("Metformin should be started at diagnosis in most people")).strength == "recommended"


def test_on_glp1_input_reflects_in_combination_with():  # KDIGO-50
    q = "DPP-4 inhibitors should not be used in combination with GLP-1 RA in people with CKD"
    assert qualifier_problems(row(q, "DPP4i", ckd="yes"), VOCAB) != []
    fixed = row(q, "DPP4i", ckd="yes", on_GLP1_RA="yes")
    assert qualifier_problems(fixed, VOCAB) == [] and VOCAB.problems(fixed) == []
    assert inferred_reasons(fixed, VOCAB) == []  # 'in combination with GLP-1' is evidence for on_GLP1_RA


# ---------- D16: THEN as a sentence, AND vs OR ----------

from sugar360_cds.evaluation.then_text import then_sentence  # noqa: E402


def _then(rec, actions, action_type="start", drugs=()):
    return then_sentence(DecisionRow("R", {}, tuple(actions), (), action_type=action_type, recommendation=rec,
                                     specific_drugs=tuple(drugs)))


def test_two_options_each_a_combination():  # A-ICMR_STW_2022-3
    text, note = _then("Basal Insulin+ Metformin + another OAD / triple OAD combination", ["insulin", "metformin"])
    assert text == "START (Basal Insulin AND Metformin AND another OAD) OR triple OAD combination"
    assert note.startswith("AND/OR read by code")


def test_combination_with_one_of_a_list():  # A-ICMR_STW_2022-2
    text, _ = _then("Dual therapy- Metformin + SU’s/TZD/ DPPIVi/SGLT2i /AGI/GLP-1RA", ["metformin", "TZD"])
    assert text == "START Metformin AND one of (SU’s / TZD / DPPIVi / SGLT2i / AGI / GLP-1RA)"


def test_plain_and_in_a_list_stays_alternatives():
    text, note = _then("Preferred medications are GLP-1 RAs and SGLT2 inhibitors", ["GLP1_RA", "SGLT2i"])
    assert text == "START a GLP-1 RA OR an SGLT2 inhibitor" and note == ""


def test_specific_drug_and_verb():
    assert _then("Glyburide: not recommended in CKD", ["sulfonylurea"], "avoid", ["glyburide"])[0] == \
        "DO NOT START glyburide"


def test_units_and_fixed_names_are_not_choices():
    text, note = _then("Metformin can be continued when eGFR is 30-45 ml/min/1.73 m2", ["metformin"], "continue")
    assert text == "CONTINUE metformin" and note == ""
    text, note = _then("Use an SGLT2i and/or GLP-1 RA with proven benefit", ["SGLT2i", "GLP1_RA"])
    assert text == "START an SGLT2 inhibitor OR a GLP-1 RA" and note == ""
    assert _then("An SGLT1/2 inhibitor is recommended", ["SGLT2i"])[1] == ""


def test_long_sentence_falls_back_to_drug_list():
    text, note = _then("It is advisable to stop metformin at least 24 hours before elective major surgery "
                       "or use of radiocontrast/ iodinated contrast", ["metformin"], "stop")
    assert text == "STOP metformin" and note == ""
