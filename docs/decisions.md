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
