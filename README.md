# sugar360-cds

Clinical decision support for diabetes care at Sugar360.

We turn published diabetes guidelines into **WHO SMART-style decision tables**
(IF / THEN / SOURCE / CHECKS). An LLM drafts each rule from the guideline text;
doctors review and approve every rule before it is used.

## Guidelines in scope

- ICMR Standard Treatment Workflow (STW) 2022
- ICMR Guidelines 2018
- RSSDI 2022
- ADA Standards of Care 2026
- KDIGO 2022 (diabetes in CKD)

Plus related ICMR, RSSDI and KDIGO documents (24 files in total). The full
list, with checksums, is in `data/raw/MANIFEST.md`.

## The comparison

Both approaches read the **same frozen extracted text** (in `data/processed/`),
so the only difference between them is retrieval.

| | How pages reach the LLM |
|---|---|
| **Approach A** | Text extracted with PyMuPDF + pdfplumber; BM25 picks the relevant pages; only those pages are sent. |
| **Approach B** | The whole guideline's extracted text is sent (not the PDF). |

See `docs/decisions.md` for why.

## Project layout

```
configs/              settings (YAML)
prompts/              versioned LLM prompts (see prompts/README.md)
data/raw/documents/   guideline PDFs by publisher: ada/ icmr/ kdigo/ rssdi/ (NOT in git)
data/raw/MANIFEST.md  list of every PDF with source, year and SHA-256
data/processed/       frozen extracted text (NOT in git)
data/answer_keys/     doctor-approved reference answers
src/sugar360_cds/     code: extraction, retrieval, drafting, evaluation, conflicts, export
runs/                 outputs of each run (NOT in git)
tests/  notebooks/  docs/
```

## Setup (macOS)

Requires Python 3.12 (developed on 3.12.14, Homebrew) and git.

```bash
cd sugar360-cds

# 1. Create the virtual environment (once)
/opt/homebrew/bin/python3.12 -m venv .venv

# 2. Activate it (every new terminal window)
source .venv/bin/activate

# 3. Install pinned dependencies
pip install -r requirements.txt

# 4. Add your API key
cp .env.example .env
# then open .env and fill in GEMINI_API_KEY=...  (.env is git-ignored)

# 5. Check everything works
pytest
```

### What activation does

`source .venv/bin/activate` puts `.venv/bin` at the front of your shell's `PATH`,
so `python` and `pip` refer to the project's own Python and libraries instead of
any system-wide copy. Your prompt shows `(.venv)` while it is active. It only
affects the current terminal window. Run `deactivate` to turn it off.

## Rules

- No patient data ever enters this repository.
- Guideline PDFs go in `data/raw/documents/<publisher>/`, are not committed,
  and are recorded in `data/raw/MANIFEST.md`.
- Never commit `.env`.
