# Decision log

Each entry records what we decided, why, and when. Add new entries at the
bottom; do not rewrite old ones. If a decision changes, add a new entry that
supersedes the old one.

---

## D1. Approach B receives extracted text, not the PDF

- **Date:** 2026-10-05
- **Decision:** Approach B sends the whole guideline's *extracted text* to the
  LLM, not the PDF file.
- **Why:** Both approaches then share one frozen extraction in
  `data/processed/`. Any difference in results comes from retrieval (BM25 page
  selection vs. whole document), not from different PDF readers. The
  comparison isolates retrieval.

## D2. Guideline PDFs are not committed

- **Date:** 2026-10-05
- **Decision:** PDFs are git-ignored. `data/raw/MANIFEST.md` records each file's
  name, source URL, version, download date and SHA-256 checksum.
- **Why:** The guidelines are copyrighted. The manifest still lets anyone obtain
  and verify the exact same files.

## D3. No patient data in this repository

- **Date:** 2026-10-05
- **Decision:** No patient data, real or de-identified, ever enters this
  repository.
- **Why:** Patient privacy. This project works only with published guideline
  text and doctor-written reference answers.

## D4. Guideline PDFs live in data/raw/documents/, organised by publisher

- **Date:** 2026-10-05
- **Decision:** PDFs are stored as real files (not symlinks) in
  `data/raw/documents/{ada,icmr,kdigo,rssdi}/`. Paths in `data/raw/MANIFEST.md`
  are relative to `data/raw/`.
- **Why:** `data/raw/` holds the untouched originals. Real copies keep the
  project self-contained; a symlink would let files outside the project change
  our inputs silently. Copies were verified identical to the downloads by
  SHA-256.

## D5. All 24 downloaded documents are in scope

- **Date:** 2026-10-05
- **Decision:** Scope covers all 24 PDFs in the manifest, not only the five
  core guidelines (ICMR STW 2022, ICMR 2018, RSSDI 2022, ADA 2026, KDIGO 2022).
  Additions: ICMR STWs (CKD, diabetic foot, retinopathy, hypertension), KDIGO
  2024 CKD, RSSDI topic documents (2019–2024). ADA 2026 is stored as individual
  section files plus the summary of revisions.
- **Why:** Diabetes care decisions involve these related conditions
  (kidney, eye, foot, heart, blood pressure, lipids).

## D6. Review: one Excel file per approach, labelled (not blinded)

- **Date:** 2026-10-05
- **Decision:** The research associate receives `Approach_A.xlsx` and
  `Approach_B.xlsx` and judges which is more relevant. Each row has optional
  `verdict` (correct / incorrect / unsupported) and `comment` columns; filled
  verdicts give a precision score per approach.
- **Note:** The reviewer knows which approach produced each file, so reviewer
  bias cannot be ruled out. State this when reporting results.

## D7. No table detection on the one-page ICMR STW flowcharts

- **Date:** 2026-10-05
- **Decision:** For `icmr/ICMR_STW_*.pdf` (5 files), pdfplumber table detection
  is skipped; only PyMuPDF text is kept. Configured in `configs/extraction.yaml`
  and recorded per document in `data/processed/extraction_manifest.json`.
- **Why:** pdfplumber mistook the flowchart box borders for table grids and
  produced garbled cells (e.g. "CO-MORBIDIT", "EXAMIN" / "ATION").
- **Limitation:** The PyMuPDF text is readable but loses the flowchart arrows
  (which step leads to which). Rules drafted from STWs need extra checking of
  step order.

## D8. Text is Unicode-normalised (NFKC) before search and drafting

- **Date:** 2026-10-05
- **Decision:** `PageRecord.full_text()` applies NFKC normalisation. Stored
  extracted text is left unchanged.
- **Why:** 311 of 913 pages contained PDF ligature characters ("speciﬁc" with
  a single "ﬁ" character), which would not match "specific" in BM25 search or
  in quote checks. Applied to both approaches equally.

## D9. Drafting model: gemini-3.5-flash, temperature 0

- **Date:** 2026-10-05
- **Decision:** Both approaches draft with `gemini-3.5-flash` (exact version
  name, not a "-latest" alias), temperature 0.
- **Why:** `gemini-2.5-pro` is refused for new API users (404);
  `gemini-3.1-pro-preview` returned 429 (quota) on this key; `gemini-3.8-flash`
  was overloaded (503). The model's drafting quality has not been assessed; the
  comparison stays fair because A and B use the same model. Revisit if Pro
  access becomes available.

## D10. KDIGO symbol-font characters translated during extraction

- **Date:** 2026-10-05
- **Decision:** In font `AdvOT8817665d` (KDIGO 2022 and 2024), "$" is read as
  "≥" and "#" as "≤". Configured in `configs/extraction.yaml`; other fonts are
  untouched.
- **Why:** The font stores these symbols under the wrong character codes, so
  "eGFR ≥20" was extracted as "eGFR $20". Meanings confirmed from clinical
  context ("urine ACR $30 mg/g", "ejection fraction #40%"). ADA "$" characters
  are real US dollar prices in normal fonts and must stay. Rebuilding page text
  without fixes was verified identical to the previous extraction.
- **Known leftover:** one "$300 mg/g" in RSSDI CPR 2022 is in a normal font and
  is not fixed automatically.
- **Consequence:** Approach A run `20261005-203817` used the pre-fix extraction
  and is superseded.

## D11. Table 1 v2: one guideline at a time, vocabulary v2, conflicts sheet

- **Date:** 2026-10-05
- **Decision:**
  - Table 1 is drafted from five guidelines, each separately: ICMR STW 2022,
    ICMR 2018, RSSDI 2022 (CPR T2DM), ADA 2026 (sections 6, 9, 10, 11), KDIGO
    2022. RSSDI Heart Failure 2021 and Insulin Therapy 2019 are no longer used
    for this table.
  - Approach A: BM25 top pages per guideline (word rarity from all five,
    ranking within each). Approach B: whole guideline, one call each (largest,
    RSSDI, is ~318k tokens). Long page sets are split into batches by
    `max_chars_per_call`.
  - Vocabulary v2 (`configs/table1_vocab_v2.yaml`, DRAFT pending doctor review)
    and prompt v2: numeric inputs with units, alternatives in one row, action
    type, strength, checks and cautions labelled stated/inferred.
  - Conflicts are found by fixed rules in `src/sugar360_cds/conflicts/compare.py`
    and never resolved by code; doctors decide.
- **Why:** The first table had uneven coverage (ICMR 0 rows) because the top
  20 pages across all documents were dominated by long guidelines, and the v1
  vocabulary distorted thresholds and dropped OR conditions.
- **Limits:** Rows align only when their category inputs match exactly; a row
  with one extra condition is not compared. "Silent" for Approach A may mean a
  page was not retrieved. Row count alone does not measure correctness.

## D12. Drafting model changed to gemini-2.5-flash (both approaches)

- **Date:** 2026-10-05
- **Decision:** Table 1 v2 runs (A and B) use `gemini-2.5-flash`.
- **Why:** `gemini-3.5-flash` returned 503 "high demand" repeatedly; every
  call in the first v2 Approach A run failed after all retries, and a direct
  test call also failed while `gemini-2.5-flash` answered. A and B use the same
  model, so the comparison stays fair. Drafting quality of the two models has
  not been compared. Supersedes D9 for v2 runs.

## D13. Table 1 v3: keep every condition, one-place quotes, narrower conflicts

- **Date:** 2026-10-05
- **Decision (from research-associate-readiness review):**
  1. Qualifiers: rows never drop a condition. Vocabulary v3 adds heart failure
     status (stable / unstable_or_hospitalized) and metformin_contraindicated;
     anything else goes in a free-text "other conditions" IF column. A check
     flags rows whose quote has a qualifier word (if, unless, unstable,
     hospitalised, moderate, severe, history of, insufficient, contraindicat-)
     not reflected in the IF. Duplicate merging now respects other conditions.
  2. Quotes: one continuous passage on one page, no "..."; at least 8 words, or
     the full cell / list item plus its heading (checked on the same page).
     This reverses the piece-by-piece "..." check introduced earlier.
  3. Conflicts: only (a) recommend vs avoid for the same drug, (b) different
     thresholds for the same drug and action, (c) different strength for the
     same drug, (d) different drugs when both guidelines mark theirs preferred.
     "One guideline silent" moves to its own sheet.
  4. Skipped: the model drafts recommendations that do not fit the inputs (as
     inferred rows with other conditions) and categorises what it skips;
     Coverage reports possible recall loss (model category cannot_express, or
     drug name + recommending word). Needs human confirmation.
  5. Retrieval (A): 8 pages per query, up to 30 pages per guideline; Coverage
     lists pages read and never read, and unread pages that name a drug.
  6. Rows from the same quote with the same IF are merged; rows get a
     stated / inferred label, with a code safety net (evidence words per input
     in the vocabulary) and a free-text "recommendation (as written)".
- **Files:** `configs/table1_vocab_v3.yaml`, `prompts/draft_table_v3.md`,
  `src/sugar360_cds/evaluation/checks.py`, `conflicts/compare.py`, `pipeline.py`.
- **Note:** the Approach B v2 run (`runs/20261005-214641_table1_drug_choice_B`)
  used v2 and is not comparable with A v3; its RSSDI call failed with 429
  (quota) for a ~318k-token request.

## D14. Drug vs class, guideline scope, range-based conflicts, dose rows out

- **Date:** 2026-10-05
- **Decision:**
  1. A row naming specific drugs (e.g. glyburide, saxagliptin, lixisenatide)
     is about those drugs only (`specific_drugs`), unless the drugs are only
     examples of the class ("such as", "e.g.", "including"). Drug names per
     class: `configs/drug_names.yaml` (DRAFT, for pharmacist/doctor review).
  2. `document_scope` adds a document's population to the IF of its rows:
     KDIGO 2022 and ADA section 11 rows get CKD = yes (shown in "IF from
     guideline scope"). Rows with no IF condition are flagged (`if_check`).
  3. Qualifier check also covers: in combination with, together with,
     on <therapy>, after <therapy/time>, if ... insufficient, in patients
     who / individuals with. "on" and "after" count only before a therapy or
     time, because the bare words occur everywhere.
  4. Conflicts read thresholds as ranges. Flagged only: recommend vs avoid with
     overlapping ranges; same action starting at a different cut-off in the
     same direction; recommended/preferred vs consider; both preferred with no
     drug in common. Adjacent or nested ranges are not conflicts. Same drug,
     same action, overlapping ranges -> new Agree sheet. Specific drugs are
     compared only with the same drugs, classes with classes.
  5. Table 1 = drug choice. reduce_dose rows and rows whose recommendation is
     about dose or titration move to a "For dose table" sheet. "continue" stays
     in Table 1 (a use / no-use decision, not dosing).
- **Applied:** to run `20261005-215948_table1_drug_choice_A` by rebuilding from
  its saved replies (no new model calls; free-tier quota). The pre-change
  outputs are kept in that run's `before_D14/` folder.

## D15. Final conflict logic for now: two automatic flags + Compare by drug

- **Date:** 2026-10-05
- **Decision:**
  1. Each action is a decision type with a direction: start/add/switch = start;
     avoid = do not start; continue = continue; stop = do not continue;
     reduce_dose is not compared. Different decision types are never compared
     ("don't start below 45" vs "stop below 30").
  2. Only two automatic conflicts: (a) same drug or class, same decision and
     direction, different cut-off in the same direction; (b) same drug or
     class, same decision, opposite directions, overlapping ranges. One
     disagreement = one conflict listing every row behind it.
  3. Everything else goes to a "Compare by drug" sheet: one block per drug class
     and decision, one column per guideline, no verdict. The Agree and Silent
     sheets (automatic verdicts) are removed.
  4. Vocabulary v3 gains current-medicine inputs on_<class> (yes/no), with
     "in combination with <class>" etc. as evidence; the qualifier check
     counts any on_<class> input as reflecting "on ..." / "in combination with".
  5. Rows quoting a dosing table (heading names dose/dosing) go to the dose
     sheet; the drug is taken from the pdfplumber table row whose cell
     contains the quote, only if exactly one drug fits.
  6. Statements worded as history ("historically", "traditionally") become
     strength "consider", marked inferred.
- **Applied:** to run `20261005-215948_table1_drug_choice_A` by rebuild from
  saved replies; previous outputs in `before_D15/`.

## D16. WHO-style review layout; THEN separates combinations (AND) from alternatives (OR)

- **Date:** 2026-10-05
- **Decision:**
  1. Rows and dose sheets get a merged, coloured group header row:
     IF | THEN | SOURCE | AUTO CHECKS | REVIEW, after row_id and guideline.
     Header rows and the first two columns are frozen; IF columns unused by
     every row in a sheet are hidden. "IF from guideline scope" is folded into
     the matching input column ("yes (from guideline scope)").
     "recommendation (as written)" is no longer a column (the quote shows it;
     it is kept in rows.yaml). Quote rules are folded into "quote check".
  2. THEN is one readable sentence: AND = give together, OR = choose one,
     "one of (...)" = pick one from a list. Read by code from "+", "plus",
     "/" and merged " OR " in the source wording; "/" separates whole options
     when a side names a regimen (combination/therapy/regimen), otherwise means
     "one of". Units (mL/min/1.73), "and/or", SGLT1/2 and GIP/GLP-1 are not
     choices. Every row whose structure was read this way gets a THEN check
     asking the reviewer to confirm (12 of 278 rows in run 215948).
  3. A "How to read" sheet explains the groups and one real row
     (`example_row_id` in the table config; A-ICMR_STW_2022-3).
- **Limit / next:** the same "/" can mean "one of" or "or another regimen";
  code cannot always tell. The next paid run should use a prompt v4 that asks
  the model for THEN as a list of options, each a list of drugs.
