"""Draft one decision table with one approach, one guideline at a time, and save everything.

Run from the project folder:
    PYTHONPATH=src python -m sugar360_cds.pipeline configs/table1_drug_choice.yaml --approach A
    PYTHONPATH=src python -m sugar360_cds.pipeline configs/table1_drug_choice.yaml --approach B
    PYTHONPATH=src python -m sugar360_cds.pipeline --rebuild runs/<run>   # outputs only, no model calls

Two steps:
  1. run(): for each guideline in the config, pick pages (A: BM25 within that
     guideline; B: all its pages), send them to the model in one or more calls
     (batches, if they exceed max_chars_per_call) and save every reply.
  2. build_outputs(): turn the saved replies into rows, coverage, conflicts and
     the review workbook. It makes no model calls, so it can be rerun after
     improving post-processing without paying for new calls.

Each run folder in runs/ contains:
    run.json              model, prompt + vocabulary + extraction checksums, every call's tokens
    config.yaml           copy of the table config
    retrieved_pages.csv   pages sent, per guideline and batch (for A: why they were picked)
    drafting.json         per guideline: pages, calls, failed calls
    prompts/  replies/    exact prompt and raw reply of every call
    rows.yaml             all rows (duplicates merged)
    coverage.json         per guideline: pages, calls, rows, and why 0 rows
    conflicts.json        where guidelines disagree
    Approach_<X>.xlsx     review workbook: Rows, Coverage, Conflicts, Skipped
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import yaml

from .conflicts.compare import compare_by_drug, find_conflicts
from .drafting.gemini import call_gemini, format_pages, format_vocabulary, parse_reply, render_prompt
from .evaluation.metrics import quote_score
from .evaluation.review import export_workbook
from .evaluation.checks import (
    apply_history_rule, apply_inferred_label, apply_scope, if_problems, qualifier_problems, quote_rule_problems,
)
from .evaluation.drugs import DrugNames, drug_from_table
from .evaluation.schema import Vocabulary, merge_duplicates, merge_same_quote, save_rows
from .extraction.pdf_text import MANIFEST_NAME, PageRecord, load_pages, sha256_file
from .retrieval.bm25 import PageIndex, retrieve

PageKey = tuple[str, int]

# Used only to explain a guideline with 0 rows: do the pages sent mention glucose-lowering drugs?
DRUG_TERMS = re.compile(
    r"\b(metformin|sglt2|glp-1|dpp-4|sulfonylurea|sulphonylurea|insulin|pioglitazone|"
    r"thiazolidinedione|glinide|acarbose|tirzepatide|finerenone)",
    re.IGNORECASE,
)
RECOMMEND_WORDS = re.compile(
    r"\b(recommend\w*|should|prefer\w*|consider\w*|use|avoid\w*|start\w*|add\w*|initiat\w*|"
    r"switch\w*|stop\w*|discontinu\w*|first.line|second.line)\b",
    re.IGNORECASE,
)


# Table 1 is drug choice; these rows belong to a dose table (docs/decisions.md D14).
DOSING = re.compile(r"\b(?:doses?|dosage|dosing|titrat\w*|up-?titrat\w*|upitrat\w*)\b|\b\d+(?:\.\d+)?\s?(?:mg|units?|iu)\b",
                    re.IGNORECASE)


def is_dose_row(row) -> bool:
    """reduce_dose rows, rows whose recommendation (as written) is about dose or titration,
    and rows quoting a dosing table (their heading names a dose / dosing table)."""
    return (row.action_type == "reduce_dose" or bool(DOSING.search(row.recommendation))
            or any(DOSING.search(s.heading) for s in row.sources))


def possible_recommendation(item: dict) -> str:
    """'yes' if a skipped item looks like a real drug recommendation (recall loss), for a human to confirm.

    True when the model says it could not express it, or when the text names a
    glucose-lowering drug together with a recommending word.
    """
    text = item.get("text", "")
    looks_like_one = bool(DRUG_TERMS.search(text) and RECOMMEND_WORDS.search(text))
    return "yes" if item.get("category") == "cannot_express" or looks_like_one else "no"


def guideline_pages(config: dict, pages: dict[PageKey, PageRecord]) -> dict[str, list[PageKey]]:
    """Page keys of each guideline, in document and page order. Fails if a document is missing."""
    result = {}
    for g in config["guidelines"]:
        keys = []
        for doc in g["documents"]:
            doc_keys = sorted(k for k in pages if k[0] == doc)
            if not doc_keys:
                raise ValueError(f"{g['id']}: document {doc!r} has no extracted pages")
            keys += doc_keys
        result[g["id"]] = keys
    return result


def select_pages(approach: str, config: dict, pages: dict, by_guideline: dict[str, list[PageKey]]) -> dict:
    """Pages to send per guideline: {guideline_id: [(page_key, info dict), ...]} in page order."""
    if approach == "B":
        return {gid: [(k, {}) for k in keys] for gid, keys in by_guideline.items()}
    if approach != "A":
        raise ValueError(f"unknown approach {approach!r}")

    r = config["retrieval"]
    # One index over all guidelines (word rarity from everything), ranked within each guideline.
    all_keys = [k for keys in by_guideline.values() for k in keys]
    index = PageIndex({k: pages[k].full_text() for k in all_keys})
    selected = {}
    for gid, keys in by_guideline.items():
        hits = retrieve(index, r["queries"], r["pages_per_query"], r["max_pages_per_guideline"], within=set(keys))
        info = {
            (h.doc_id, h.page): {"rank": i, "fused_score": round(h.fused_score, 5),
                                 "queries": " ".join(str(q) for q in h.queries)}
            for i, h in enumerate(hits, start=1)
        }
        selected[gid] = [(k, info[k]) for k in sorted(info, key=keys.index)]
    return selected


def batches(records: list[PageRecord], max_chars: int) -> list[list[PageRecord]]:
    """Split pages, in order, into groups whose text fits max_chars (at least one page each)."""
    groups, current, size = [], [], 0
    for rec in records:
        n = len(rec.full_text())
        if current and size + n > max_chars:
            groups.append(current)
            current, size = [], 0
        current.append(rec)
        size += n
    if current:
        groups.append(current)
    return groups


def coverage_reason(entry: dict, records: list[PageRecord]) -> str:
    """Why a guideline has 0 rows (or that some calls failed). Empty if nothing to explain."""
    if entry["pages_sent"] == 0:
        return "not retrieved: no page of this guideline matched the queries"
    if entry["calls_ok"] == 0:
        return "model call failed: " + "; ".join(entry["errors"])
    if entry["rows_drafted"] == 0:
        terms = sorted({m.lower() for r in records for m in DRUG_TERMS.findall(r.full_text())})
        if terms:
            return f"retrieved but model returned nothing (pages sent mention: {', '.join(terms[:8])})"
        return ("pages sent mention no glucose-lowering drug: the guideline may say nothing on drug "
                "choice in these pages (needs human confirmation)")
    if entry["errors"]:
        return f"partial: {len(entry['errors'])} of {entry['calls']} calls failed: " + "; ".join(entry["errors"])
    return ""


def _sent(gid: str, b: int, batch: list[PageRecord], info: dict) -> list[dict]:
    """Rows for retrieved_pages.csv: which pages went to the model in batch b."""
    return [{"guideline": gid, "batch": b, "doc_id": r.doc_id, "page": r.page, **info[(r.doc_id, r.page)]}
            for r in batch]


def run(config_path: str | Path, approach: str, client, processed_dir="data/processed", runs_dir="runs") -> Path:
    """Step 1: make the model calls and save every reply; then build the outputs."""
    config_path, processed_dir = Path(config_path), Path(processed_dir)
    config = yaml.safe_load(config_path.read_text())
    drafting = config["drafting"]
    vocab_path, prompt_path = Path(config["vocabulary"]), Path(drafting["prompt"])
    vocab = Vocabulary.from_yaml(vocab_path)
    template = prompt_path.read_text()
    max_chars = config["max_chars_per_call"][approach]

    pages = load_pages(processed_dir)
    by_guideline = guideline_pages(config, pages)
    selected = select_pages(approach, config, pages, by_guideline)

    run_dir = Path(runs_dir) / f"{datetime.now():%Y%m%d-%H%M%S}_{config['table_id']}_{approach}"
    (run_dir / "prompts").mkdir(parents=True)
    (run_dir / "replies").mkdir()
    shutil.copy(config_path, run_dir / "config.yaml")

    entries, calls, sent_rows = [], [], []
    for g in config["guidelines"]:
        gid = g["id"]
        records = [pages[k] for k, _ in selected[gid]]
        info = dict(selected[gid])
        entry = {"guideline": gid, "pages_in_guideline": len(by_guideline[gid]), "pages_sent": len(records),
                 "calls": 0, "calls_ok": 0, "errors": [], "split_after_cutoff": 0}
        queue = list(batches(records, max_chars))  # batches still to send, in page order
        b = 0
        while queue:
            batch = queue.pop(0)
            b += 1
            prompt = render_prompt(template, guideline=g["name"], question=config["question"].strip(),
                                   vocabulary=format_vocabulary(vocab), pages=format_pages(batch))
            (run_dir / "prompts" / f"{gid}_{b}.txt").write_text(prompt)
            entry["calls"] += 1
            try:
                reply, stats, usage = call_gemini(client, drafting["model"], prompt, drafting["temperature"])
            except Exception as e:  # record and carry on with the other guidelines
                entry["errors"].append(f"batch {b}: {type(e).__name__}: {str(e)[:200]}")
                sent_rows += _sent(gid, b, batch, info)
                continue
            calls.append({"guideline": gid, "batch": b, **asdict(stats), **usage})
            if "MAX_TOKENS" in usage["finish_reason"] and len(batch) > 1:
                # Reply cut off at the output limit: keep it for the record, ask again in two halves.
                (run_dir / "replies" / f"{gid}_{b}.truncated.json").write_text(reply)
                half = len(batch) // 2
                queue[:0] = [batch[:half], batch[half:]]
                entry["split_after_cutoff"] += 1
                continue
            (run_dir / "replies" / f"{gid}_{b}.json").write_text(reply)
            sent_rows += _sent(gid, b, batch, info)
            entry["calls_ok"] += 1
        entries.append(entry)

    with (run_dir / "retrieved_pages.csv").open("w", newline="") as f:
        fields = ["guideline", "batch", "doc_id", "page", "rank", "fused_score", "queries"]
        writer = csv.DictWriter(f, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(sent_rows)
    (run_dir / "drafting.json").write_text(json.dumps(entries, indent=2) + "\n")
    meta = {
        "table_id": config["table_id"],
        "approach": approach,
        "created": datetime.now().isoformat(timespec="seconds"),
        "model": drafting["model"],
        "temperature": drafting["temperature"],
        "prompt_file": prompt_path.as_posix(),
        "prompt_sha256": sha256_file(prompt_path),
        "vocabulary_file": vocab_path.as_posix(),
        "vocabulary_sha256": sha256_file(vocab_path),
        "extraction_manifest_sha256": sha256_file(processed_dir / MANIFEST_NAME),
        "pages_sent": len(sent_rows),
        "calls": calls,
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    build_outputs(run_dir, processed_dir)
    return run_dir


def _reply_files(run_dir: Path, gid: str) -> list[Path]:
    """Saved replies of one guideline, in batch order (exact id match: "ICMR" never picks up "ICMR_2018")."""
    pattern = re.compile(rf"{re.escape(gid)}_(\d+)\.json")
    found = [(int(m.group(1)), p) for p in (run_dir / "replies").iterdir() if (m := pattern.fullmatch(p.name))]
    return [p for _, p in sorted(found)]


def page_ranges(numbers) -> str:
    """[1, 2, 3, 7, 9, 10] -> "1-3, 7, 9-10"."""
    numbers, parts = sorted(set(numbers)), []
    for n in numbers:
        if parts and n == parts[-1][1] + 1:
            parts[-1][1] = n
        else:
            parts.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in parts)


def _page_coverage(gid: str, all_keys: list[PageKey], sent: list[tuple], pages: dict) -> dict:
    """Which pages of a guideline were read, which never, and how many unread pages name a drug."""
    read = {(d, p) for g, d, p in sent if g == gid}
    unread = [k for k in all_keys if k not in read]
    by_doc = lambda keys: "; ".join(
        f"{doc.split('/')[-1]} p.{page_ranges(p for d, p in keys if d == doc)}"
        for doc in dict.fromkeys(d for d, _ in keys))
    return {
        "pct_pages_read": round(100 * len(read) / len(all_keys)) if all_keys else 0,
        "pages_read": by_doc(sorted(read, key=all_keys.index)),
        "pages_never_read": by_doc(unread),
        "unread_pages_mentioning_drugs": sum(bool(DRUG_TERMS.search(pages[k].full_text())) for k in unread),
    }


def build_outputs(run_dir: str | Path, processed_dir="data/processed") -> list[dict]:
    """Step 2: saved replies -> rows, coverage, conflicts, workbook. No model calls. Returns coverage."""
    run_dir = Path(run_dir)
    config = yaml.safe_load((run_dir / "config.yaml").read_text())
    meta = json.loads((run_dir / "run.json").read_text())
    approach = meta["approach"]
    vocab = Vocabulary.from_yaml(config["vocabulary"])
    drug_names = DrugNames.from_yaml(config["drug_names"]) if config.get("drug_names") else None
    document_scope = config.get("document_scope") or {}
    pages = load_pages(processed_dir)
    all_keys = guideline_pages(config, pages)
    with (run_dir / "retrieved_pages.csv").open() as f:
        sent = [(r["guideline"], r["doc_id"], int(r["page"])) for r in csv.DictReader(f)]

    all_rows, dose_rows, skipped, coverage = [], [], [], []
    for entry in json.loads((run_dir / "drafting.json").read_text()):
        gid = entry["guideline"]
        entry = {**entry, "errors": list(entry["errors"])}
        raw, g_skipped = [], []
        for path in _reply_files(run_dir, gid):
            try:
                rows, items = parse_reply(path.read_text(), f"{approach}-{gid}-", gid, start=len(raw) + 1)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:  # call worked, reply unusable
                entry["errors"].append(f"{path.name}: unreadable reply ({type(e).__name__})")
                entry["calls_ok"] -= 1
                continue
            for r in rows:
                r = vocab.normalize(r)
                if drug_names:
                    r = drug_names.assign(r)
                    if not r.specific_drugs and r.sources and (r.sources[0].doc_id, r.sources[0].page) in pages:
                        found, why = drug_from_table(r, pages[(r.sources[0].doc_id, r.sources[0].page)].tables, drug_names)
                        r = replace(r, specific_drugs=found) if found else r
                        if why:
                            r = replace(r, label="inferred", label_reasons=r.label_reasons + (why,))
                raw.append(apply_scope(r, document_scope))
            g_skipped += [{"guideline": gid, **s, "possible_recommendation": possible_recommendation(s)} for s in items]

        same_quote = merge_same_quote(raw)
        merged = [apply_history_rule(apply_inferred_label(r, vocab)) for r in merge_duplicates(same_quote)]
        merged = [replace(r, row_id=f"{approach}-{gid}-{i}") for i, r in enumerate(merged, start=1)]
        g_dose = [r for r in merged if is_dose_row(r)]
        merged = [r for r in merged if not is_dose_row(r)]
        categories = Counter(s["category"] or "uncategorised" for s in g_skipped)
        entry |= {
            **_page_coverage(gid, all_keys[gid], sent, pages),
            "rows_from_model": len(raw),
            "merged_same_quote": len(raw) - len(same_quote),
            "duplicates_merged": len(same_quote) - len(merged),
            "rows_drafted": len(merged),
            "rows_inferred": sum(r.label == "inferred" for r in merged),
            "rows_specific_drug": sum(bool(r.specific_drugs) for r in merged),
            "rows_with_scope_if": sum(bool(r.scope_inputs) for r in merged),
            "rows_without_if": sum(bool(if_problems(r)) for r in merged),
            "rows_for_dose_table": len(g_dose),
            "skipped": len(g_skipped),
            "skipped_by_category": ", ".join(f"{k}: {n}" for k, n in sorted(categories.items())),
            "possible_recall_loss": sum(s["possible_recommendation"] == "yes" for s in g_skipped),
        }
        records = [pages[(d, p)] for g, d, p in sent if g == gid]
        entry["reason_if_problem"] = coverage_reason(entry, records)
        coverage.append(entry)
        all_rows += merged
        dose_rows += g_dose
        skipped += g_skipped
        print(f"{gid:15} pages {entry['pages_sent']:4} ({entry['pct_pages_read']:3}% read)  calls "
              f"{entry['calls_ok']}/{entry['calls']}  rows {entry['rows_drafted']:3} ({entry['rows_inferred']} inferred)  "
              f"skipped {entry['skipped']} (possible recall loss {entry['possible_recall_loss']})  "
              f"{entry['reason_if_problem']}", flush=True)

    # Checks per row. Quotes and headings are checked against the pages sent for that row's guideline.
    shown = set(sent)
    def score(row, text):
        return min((quote_score(getattr(s, text), pages[(s.doc_id, s.page)].full_text())
                    if (row.guideline, s.doc_id, s.page) in shown else 0.0)
                   for s in row.sources if getattr(s, text)) if any(getattr(s, text) for s in row.sources) else None
    row_checks = {}
    for row in all_rows + dose_rows:
        heading = score(row, "heading")
        rules = quote_rule_problems(row) + ([f"heading not found on page (score {heading:.0f})"]
                                            if heading is not None and heading < 90 else [])
        row_checks[row.row_id] = {"quote_score": score(row, "quote") or 0.0, "quote_rules": rules,
                                  "qualifiers": qualifier_problems(row, vocab), "if_check": if_problems(row)}
    conflicts, conflict_summary = find_conflicts(all_rows, vocab, approach)
    compare_blocks = compare_by_drug(all_rows, vocab, [g["id"] for g in config["guidelines"]])

    save_rows(all_rows, run_dir / "rows.yaml", skipped=skipped)
    save_rows(dose_rows, run_dir / "dose_rows.yaml")
    (run_dir / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    (run_dir / "row_checks.json").write_text(json.dumps(row_checks, indent=2) + "\n")
    (run_dir / "conflicts.json").write_text(json.dumps(
        {"summary": conflict_summary, "conflicts": [asdict(c) for c in conflicts],
         "compare_by_drug": compare_blocks}, indent=2, ensure_ascii=False) + "\n")
    export_workbook(run_dir / f"Approach_{approach}.xlsx", all_rows, vocab, row_checks, coverage, conflicts,
                    compare_blocks, skipped, dose_rows, example_row_id=config.get("example_row_id"))
    meta |= {"rows": len(all_rows), "rows_for_dose_table": len(dose_rows), "conflicts": len(conflicts),
             "compare_blocks": len(compare_blocks),
             "outputs_built": datetime.now().isoformat(timespec="seconds"),
             # the vocabulary file can change after the run (post-processing fixes); record what was used
             "vocabulary_sha256_at_build": sha256_file(config["vocabulary"])}
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    return coverage


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", nargs="?")
    parser.add_argument("--approach", choices=["A", "B"])
    parser.add_argument("--rebuild", metavar="RUN_DIR", help="rebuild outputs from saved replies; no model calls")
    args = parser.parse_args()

    if args.rebuild:
        build_outputs(args.rebuild)
        print(f"outputs rebuilt in {args.rebuild}")
    else:
        if not (args.config and args.approach):
            parser.error("config and --approach are required unless --rebuild is given")
        from dotenv import load_dotenv
        from google import genai

        load_dotenv(".env")
        run_dir = run(args.config, args.approach, genai.Client(api_key=os.environ["GEMINI_API_KEY"]))
        print(f"run saved in {run_dir}")
