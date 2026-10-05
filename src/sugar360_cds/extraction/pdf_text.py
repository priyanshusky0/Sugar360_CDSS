"""Turn guideline PDFs into frozen per-page text that Approach A and B both read.

PyMuPDF gives the running text of each page. pdfplumber finds tables and keeps
their rows and columns, which PyMuPDF flattens into a stream of words.

Output, for each PDF under data/raw/documents/:
    data/processed/<publisher>/<name>.jsonl     one JSON object per page
and data/processed/extraction_manifest.json, which records the source
checksums and extractor versions, so every run can say which extraction it used.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from fnmatch import fnmatch
from importlib.metadata import version
from pathlib import Path

import pdfplumber
import pymupdf

MANIFEST_NAME = "extraction_manifest.json"


@dataclass
class PageRecord:
    doc_id: str  # path under data/raw/documents/, e.g. "ada/dc26s009.pdf"
    page: int  # 1-based, matching the page numbers people see in a PDF viewer
    text: str  # running text from PyMuPDF
    tables: list[list[list[str]]]  # tables from pdfplumber: table -> row -> cell
    table_error: str | None = None  # set if pdfplumber failed on this page

    def full_text(self) -> str:
        """The page as both approaches see it: running text, then each table row by row.

        NFKC normalisation turns PDF ligatures back into plain letters
        ("speciﬁc" -> "specific"), so search and quote checks match normal spelling.
        The stored `text` stays exactly as extracted.
        """
        parts = [self.text.strip()]
        for i, table in enumerate(self.tables, start=1):
            rows = "\n".join(" | ".join(cells) for cells in table)
            parts.append(f"[TABLE {i}]\n{rows}")
        return unicodedata.normalize("NFKC", "\n\n".join(p for p in parts if p))


def _clean_cell(cell: str | None) -> str:
    # Cells can be empty (None) or wrap over several lines.
    return " ".join((cell or "").split())


FontFixes = Mapping[str, Mapping[str, str]]  # font name -> {stored char: real char}


def _base_font(name: str) -> str:
    return name.split("+")[-1]  # "HEKAPL+AdvOT8817665d" -> "AdvOT8817665d"


def _page_text(page: pymupdf.Page, font_fixes: FontFixes) -> str:
    """PyMuPDF page text, with characters from known symbol fonts translated.

    Built line by line from spans so each character's font is known. Without
    fixes this gives exactly page.get_text() (checked on KDIGO and ADA pages).
    """
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:  # 0 = text; images have no lines
            continue
        for line in block["lines"]:
            parts = []
            for span in line["spans"]:
                fix = font_fixes.get(_base_font(span["font"]), {})
                parts.append("".join(fix.get(ch, ch) for ch in span["text"]))
            lines.append("".join(parts) + "\n")
    return "".join(lines)


def _fix_table_chars(plumb_page, font_fixes: FontFixes) -> None:
    """Apply the same font fixes to pdfplumber's characters before table detection."""
    for ch in plumb_page.chars:
        fix = font_fixes.get(_base_font(ch["fontname"]))
        if fix and ch["text"] in fix:
            ch["text"] = fix[ch["text"]]


def extract_pdf(
    pdf_path: str | Path, doc_id: str, with_tables: bool = True, font_fixes: FontFixes | None = None
) -> list[PageRecord]:
    font_fixes = font_fixes or {}
    records = []
    with pymupdf.open(pdf_path) as doc, pdfplumber.open(pdf_path) as plumb:
        for i, page in enumerate(doc):
            try:
                tables = []
                if with_tables:
                    _fix_table_chars(plumb.pages[i], font_fixes)
                    tables = [
                        [[_clean_cell(c) for c in row] for row in table]
                        for table in plumb.pages[i].extract_tables()
                    ]
                error = None
            except Exception as e:  # one bad page must not stop a 900-page extraction
                tables, error = [], f"{type(e).__name__}: {e}"
            records.append(PageRecord(doc_id, i + 1, _page_text(page, font_fixes), tables, error))
    return records


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_jsonl(records: list[PageRecord], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def read_jsonl(path: str | Path) -> list[PageRecord]:
    with Path(path).open(encoding="utf-8") as f:
        return [PageRecord(**json.loads(line)) for line in f]


def extract_all(
    raw_dir: str | Path,
    out_dir: str | Path,
    force: bool = False,
    skip_tables_for: Sequence[str] = (),
    font_fixes: FontFixes | None = None,
) -> dict:
    """Extract every PDF under raw_dir. Refuses to overwrite a finished extraction unless force=True.

    skip_tables_for: glob patterns of doc_ids (e.g. "icmr/ICMR_STW_*.pdf") whose
    tables are not extracted. font_fixes: characters to translate in symbol
    fonts. Both come from configs/extraction.yaml.
    """
    font_fixes = font_fixes or {}
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    manifest_path = out_dir / MANIFEST_NAME
    if manifest_path.exists() and not force:
        raise FileExistsError(
            f"{manifest_path} exists: the extraction is frozen. Use force=True (--force) to redo it."
        )

    documents = {}
    for pdf in sorted(raw_dir.rglob("*.pdf")):
        doc_id = pdf.relative_to(raw_dir).as_posix()
        skip = any(fnmatch(doc_id, pattern) for pattern in skip_tables_for)
        records = extract_pdf(pdf, doc_id, with_tables=not skip, font_fixes=font_fixes)
        output = Path(doc_id).with_suffix(".jsonl")
        write_jsonl(records, out_dir / output)
        documents[doc_id] = {
            "sha256": sha256_file(pdf),
            "pages": len(records),
            "output": output.as_posix(),
            "tables_skipped": skip,
            "pages_with_tables": sum(bool(r.tables) for r in records),
            "table_errors": sum(r.table_error is not None for r in records),
        }

    # Written last: if extraction crashes halfway, there is no manifest and it can simply be rerun.
    manifest = {
        "extracted_on": date.today().isoformat(),
        "pymupdf_version": version("pymupdf"),
        "pdfplumber_version": version("pdfplumber"),
        "skip_tables_for": list(skip_tables_for),
        "symbol_font_fixes": {font: dict(fix) for font, fix in font_fixes.items()},
        "documents": documents,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load_pages(out_dir: str | Path) -> dict[tuple[str, int], PageRecord]:
    """All extracted pages, keyed by (doc_id, page)."""
    out_dir = Path(out_dir)
    manifest = json.loads((out_dir / MANIFEST_NAME).read_text())
    pages = {}
    for info in manifest["documents"].values():
        for r in read_jsonl(out_dir / info["output"]):
            pages[(r.doc_id, r.page)] = r
    return pages
