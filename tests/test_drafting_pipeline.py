"""Tests for drafting and the full per-guideline pipeline, using a fake Gemini client (no API calls)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pymupdf
import pytest
import yaml

from sugar360_cds.drafting.gemini import call_gemini, parse_reply, render_prompt
from sugar360_cds.evaluation.__main__ import evaluate
from sugar360_cds.evaluation.schema import load_rows
from sugar360_cds.extraction.pdf_text import PageRecord, extract_all, load_pages
from sugar360_cds.pipeline import batches, build_outputs, run

ROOT = Path(__file__).parent.parent


class FakeClient:
    """Stands in for google.genai.Client. `reply_for(prompt)` returns a dict, or raises."""

    def __init__(self, reply_for):
        self.reply_for = reply_for
        self.prompts = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, model, contents, config):
        self.prompts.append(contents)
        reply = self.reply_for(contents)
        usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=200, thoughts_token_count=50)
        return SimpleNamespace(text=json.dumps(reply), usage_metadata=usage)


class Busy(Exception):
    def __init__(self, code):
        self.code = code


def flaky_client(failures):
    """A client whose first calls raise the given errors, then succeeds."""
    errors = list(failures)

    def reply_for(_):
        if errors:
            raise errors.pop(0)
        return {"rows": [], "skipped": []}

    return FakeClient(reply_for)


def test_busy_server_is_retried():
    waits = []
    reply, _, _ = call_gemini(flaky_client([Busy(503), Busy(503)]), "m", "p", 0, sleep=waits.append)
    assert json.loads(reply) == {"rows": [], "skipped": []}
    assert waits == [30, 60]


def test_quota_error_is_not_retried():
    waits = []
    with pytest.raises(Busy):
        call_gemini(flaky_client([Busy(429)]), "m", "p", 0, sleep=waits.append)
    assert waits == []


def test_gives_up_after_last_retry():
    waits = []
    with pytest.raises(Busy):
        call_gemini(flaky_client([Busy(503)] * 4), "m", "p", 0, sleep=waits.append)
    assert waits == [30, 60, 120]


def test_render_prompt_fills_markers_and_rejects_mismatch():
    assert render_prompt("Q: {{question}}", question="why?") == "Q: why?"
    with pytest.raises(ValueError):
        render_prompt("Q: {{question}} {{pages}}", question="why?")


def test_parse_reply_numbers_ids_and_sets_guideline():
    reply = json.dumps({"rows": [
        {"conditions": {"on_metformin": True}, "actions": ["SGLT2i"], "sources": []},
        {"conditions": {}, "actions": ["metformin"], "sources": []},
    ], "skipped": ["something"]})
    rows, skipped = parse_reply(reply, "A-ADA-", guideline="ADA", start=4)
    assert [r.row_id for r in rows] == ["A-ADA-4", "A-ADA-5"]
    assert {r.guideline for r in rows} == {"ADA"}
    assert rows[0].conditions == {"on_metformin": "yes"}
    assert skipped == [{"text": "something", "category": "", "doc_id": "", "page": ""}]  # v2 plain string


def test_parse_reply_reads_v3_fields():
    reply = json.dumps({"rows": [{
        "recommendation": "Basal insulin + metformin + another OAD", "conditions": {"hba1c": ">10"},
        "other_conditions": ["if SGLT2i and metformin are insufficient"], "actions": ["insulin"],
        "action_type": "add", "strength": "preferred", "label": "inferred", "label_reason": "regimen, not precondition",
        "sources": [{"doc_id": "d.pdf", "page": 2, "heading": "Table 5", "quote": "Short term insulin"}]}],
        "skipped": [{"text": "HbA1c every 3 months", "category": "not_a_drug_recommendation", "doc_id": "d.pdf", "page": 3}]})
    [row], [skip] = parse_reply(reply, "A-X-", "X")
    assert row.other_conditions == ("if SGLT2i and metformin are insufficient",)
    assert (row.label, row.label_reasons, row.strength) == ("inferred", ("regimen, not precondition",), "preferred")
    assert row.sources[0].heading == "Table 5"
    assert skip["category"] == "not_a_drug_recommendation" and skip["page"] == 3


def test_batches_split_long_page_sets_in_order():
    recs = [PageRecord("d.pdf", i, "x" * 100, []) for i in range(1, 6)]
    assert [[r.page for r in b] for b in batches(recs, max_chars=250)] == [[1, 2], [3, 4], [5]]
    assert len(batches(recs, max_chars=10)) == 5  # a page longer than the limit still goes alone
    assert len(batches(recs, max_chars=10_000)) == 1


# ---------- full pipeline ----------

GUIDELINE_TEXT = {
    "g1/a.pdf": ["In heart failure use an SGLT2 inhibitor.", "Metformin first line for most patients."],
    "g2/b.pdf": ["In heart failure a GLP-1 receptor agonist may be considered.", "Eye screening yearly."],
    "g3/c.pdf": ["Foot examination at every visit."],
    "g4/d.pdf": ["SGLT2 inhibitor and metformin in heart failure and kidney disease."],
}


def make_corpus(tmp_path):
    raw, processed = tmp_path / "raw", tmp_path / "processed"
    for doc_id, page_texts in GUIDELINE_TEXT.items():
        (raw / doc_id).parent.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open()
        for text in page_texts:
            doc.new_page().insert_text((72, 72), text)
        doc.save(raw / doc_id)
    extract_all(raw, processed)
    return processed


def write_config(tmp_path, max_chars_a=100_000):
    config = {
        "table_id": "t",
        "question": "Which drug?",
        "vocabulary": str(ROOT / "configs" / "table1_vocab_v2.yaml"),
        "guidelines": [
            {"id": "G1", "name": "Guideline one", "documents": ["g1/a.pdf"]},
            {"id": "G2", "name": "Guideline two", "documents": ["g2/b.pdf"]},
            {"id": "G3", "name": "Guideline three", "documents": ["g3/c.pdf"]},
            {"id": "G4", "name": "Guideline four", "documents": ["g4/d.pdf"]},
        ],
        "retrieval": {"pages_per_query": 1, "max_pages_per_guideline": 1,
                      "queries": ["heart failure SGLT2 GLP-1", "metformin first line"]},
        "max_chars_per_call": {"A": max_chars_a, "B": 100_000},
        "drafting": {"prompt": str(ROOT / "prompts" / "draft_table_v2.md"), "model": "fake", "temperature": 0},
    }
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def hf_row(drug, doc, quote, strength="recommended"):
    return {"recommendation": f"add {drug}", "conditions": {"heart_failure": "yes", "age": "–"}, "actions": [drug],
            "action_type": "add", "strength": strength, "label": "stated",
            "checks": [{"text": "confirm heart failure", "basis": "inferred"}],
            "cautions": [], "sources": [{"doc_id": doc, "page": 1, "heading": "", "quote": quote}]}


def fake_model(prompt):
    """G1 recommends SGLT2i, G2 only says consider it (a strength conflict), G3 returns nothing, G4 fails."""
    if "Guideline one" in prompt:
        return {"rows": [hf_row("SGLT2i", "g1/a.pdf", "In heart failure use an SGLT2 inhibitor.")], "skipped": []}
    if "Guideline two" in prompt:
        return {"rows": [hf_row("SGLT2i", "g2/b.pdf", "In heart failure a GLP-1 receptor agonist may be considered.",
                                "consider")],
                "skipped": [{"text": "Eye screening yearly.", "category": "other_topic", "doc_id": "g2/b.pdf",
                             "page": 2}]}
    if "Guideline four" in prompt:
        raise Busy(429)
    return {"rows": [], "skipped": []}


def test_full_pipeline_approach_a(tmp_path):
    processed = make_corpus(tmp_path)
    client = FakeClient(fake_model)
    run_dir = run(write_config(tmp_path), "A", client, processed_dir=processed, runs_dir=tmp_path / "runs")

    # One call per guideline with pages (G3 had none retrieved, so no call), each
    # prompt holding only that guideline's pages.
    assert len(client.prompts) == 3
    g1_prompt = next(p for p in client.prompts if "Guideline one" in p)
    assert "g1/a.pdf" in g1_prompt and "g2/b.pdf" not in g1_prompt

    coverage = {c["guideline"]: c for c in json.loads((run_dir / "coverage.json").read_text())}
    assert coverage["G1"]["rows_drafted"] == 1 and coverage["G1"]["reason_if_problem"] == ""
    assert coverage["G3"]["pages_sent"] == 0
    assert coverage["G3"]["reason_if_problem"].startswith("not retrieved")
    assert coverage["G4"]["reason_if_problem"].startswith("model call failed")

    rows = load_rows(run_dir / "rows.yaml")
    assert [(r.row_id, r.guideline) for r in rows] == [("A-G1-1", "G1"), ("A-G2-1", "G2")]
    assert rows[0].conditions == {"heart_failure": "yes"}  # "–" for age was dropped

    out = json.loads((run_dir / "conflicts.json").read_text())
    assert out["conflicts"] == []  # different strength is no longer an automatic conflict (D15)
    [block] = out["compare_by_drug"]  # ...but both guidelines appear side by side
    assert block["drug class"] == "SGLT2i" and "g1/a.pdf" not in block["G2"] and block["G1"] != "–"

    sheets = pd.read_excel(run_dir / "Approach_A.xlsx", sheet_name=None, dtype=str)
    rows_sheet = pd.read_excel(run_dir / "Approach_A.xlsx", sheet_name="Rows", header=1, dtype=str)
    assert len(rows_sheet) == 2 and len(sheets["Coverage"]) == 4
    assert rows_sheet["quote check"].tolist() == ["100 - p.1: short quote (7 words) without heading", "100"]
    assert rows_sheet["stated / inferred"].tolist() == ["stated", "stated"]
    assert sheets["Skipped"]["category"].tolist() == ["other_topic"]
    cov = sheets["Coverage"].set_index("guideline")
    assert cov.loc["G1", "pages_never_read"] == "a.pdf p.2"  # max 1 page per guideline: page 2 unread
    assert cov.loc["G1", "unread_pages_mentioning_drugs"] == "1"  # "Metformin first line..."

    meta = json.loads((run_dir / "run.json").read_text())
    assert len(meta["calls"]) == 2  # G3 had no pages; G4's call failed
    result = evaluate(run_dir, load_pages(processed))
    assert result["citation_validity"] == "2/2 (100%)"
    assert result["rows_per_guideline"] == {"G1": 1, "G2": 1}

    # Rebuilding from the saved replies makes no model calls and gives the same table.
    calls_before = len(client.prompts)
    build_outputs(run_dir, processed)
    assert len(client.prompts) == calls_before
    assert load_rows(run_dir / "rows.yaml") == rows


def test_duplicate_rows_from_model_are_merged(tmp_path):
    processed = make_corpus(tmp_path)
    same = hf_row("SGLT2i", "g1/a.pdf", "In heart failure use an SGLT2 inhibitor.")
    client = FakeClient(lambda p: {"rows": [same, same], "skipped": []} if "Guideline one" in p
                        else {"rows": [], "skipped": []})
    run_dir = run(write_config(tmp_path), "A", client, processed_dir=processed, runs_dir=tmp_path / "runs")
    coverage = {c["guideline"]: c for c in json.loads((run_dir / "coverage.json").read_text())}
    assert coverage["G1"]["rows_from_model"] == 2
    assert coverage["G1"]["merged_same_quote"] == 1 and coverage["G1"]["rows_drafted"] == 1


def test_model_returning_nothing_is_explained(tmp_path):
    processed = make_corpus(tmp_path)
    run_dir = run(write_config(tmp_path), "B", FakeClient(lambda p: {"rows": [], "skipped": []}),
                  processed_dir=processed, runs_dir=tmp_path / "runs")
    coverage = {c["guideline"]: c for c in json.loads((run_dir / "coverage.json").read_text())}
    assert coverage["G1"]["pages_sent"] == 2  # B sends the whole guideline
    assert coverage["G1"]["reason_if_problem"].startswith("retrieved but model returned nothing")
    assert "metformin" in coverage["G1"]["reason_if_problem"]
    assert coverage["G3"]["reason_if_problem"].startswith("pages sent mention no glucose-lowering drug")


def test_cut_off_reply_is_retried_in_halves(tmp_path):
    processed = make_corpus(tmp_path)

    class CuttingClient(FakeClient):
        """Cuts off any reply for more than one page, like a model hitting its output limit."""
        def _generate(self, model, contents, config):
            response = super()._generate(model, contents, config)
            cut = contents.count("=== doc_id:") > 1
            response.candidates = [SimpleNamespace(finish_reason="FinishReason.MAX_TOKENS" if cut else "STOP")]
            if cut:
                response.text = response.text[:10]  # unreadable, like the real cut-off reply
            return response

    client = CuttingClient(lambda p: {"rows": [], "skipped": []})
    config = write_config(tmp_path)
    cfg = yaml.safe_load(config.read_text())
    cfg["retrieval"]["max_pages_per_guideline"] = 2
    config.write_text(yaml.safe_dump(cfg))
    run_dir = run(config, "A", client, processed_dir=processed, runs_dir=tmp_path / "runs")

    drafting = {d["guideline"]: d for d in json.loads((run_dir / "drafting.json").read_text())}
    assert drafting["G1"]["split_after_cutoff"] == 1
    assert drafting["G1"]["calls"] == 3 and drafting["G1"]["calls_ok"] == 2  # 1 cut off + 2 halves
    assert (run_dir / "replies" / "G1_1.truncated.json").exists()
    coverage = {c["guideline"]: c for c in json.loads((run_dir / "coverage.json").read_text())}
    assert coverage["G1"]["errors"] == [] and coverage["G1"]["pages_sent"] == 2


def test_long_guideline_is_split_into_batches(tmp_path):
    processed = make_corpus(tmp_path)
    client = FakeClient(lambda p: {"rows": [], "skipped": []})
    config = write_config(tmp_path, max_chars_a=10)  # every page in its own call
    cfg = yaml.safe_load(config.read_text())
    cfg["retrieval"]["max_pages_per_guideline"] = 2
    config.write_text(yaml.safe_dump(cfg))
    run_dir = run(config, "A", client, processed_dir=processed, runs_dir=tmp_path / "runs")
    coverage = {c["guideline"]: c for c in json.loads((run_dir / "coverage.json").read_text())}
    assert coverage["G1"]["pages_sent"] == 2 and coverage["G1"]["calls"] == 2
