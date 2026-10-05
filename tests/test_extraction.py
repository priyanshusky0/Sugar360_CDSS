"""Tests for PDF extraction, using small PDFs built on the fly (no guideline files needed)."""

import pymupdf
import pytest

from sugar360_cds.extraction.pdf_text import (
    PageRecord,
    extract_all,
    extract_pdf,
    load_pages,
)


def make_pdf(path, pages):
    """Write a PDF; each item in `pages` is the text for one page."""
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(path)


def make_pdf_with_table(path):
    """One page with a 2x2 table drawn with lines, which pdfplumber can detect."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Table 1. Example")
    x0, y0, w, h = 72, 80, 150, 30
    for r in range(3):
        page.draw_line((x0, y0 + r * h), (x0 + 2 * w, y0 + r * h))
    for c in range(3):
        page.draw_line((x0 + c * w, y0), (x0 + c * w, y0 + 2 * h))
    for (r, c), cell in {(0, 0): "eGFR", (0, 1): "Action", (1, 0): "30-44", (1, 1): "Reduce dose"}.items():
        page.insert_text((x0 + c * w + 5, y0 + r * h + 20), cell)
    doc.save(path)


def test_pages_are_numbered_from_one(tmp_path):
    make_pdf(tmp_path / "a.pdf", ["first page", "second page"])
    records = extract_pdf(tmp_path / "a.pdf", "test/a.pdf")
    assert [r.page for r in records] == [1, 2]
    assert "second page" in records[1].text


def test_table_keeps_rows_and_cells(tmp_path):
    make_pdf_with_table(tmp_path / "t.pdf")
    [record] = extract_pdf(tmp_path / "t.pdf", "test/t.pdf")
    assert record.tables == [[["eGFR", "Action"], ["30-44", "Reduce dose"]]]
    assert "30-44 | Reduce dose" in record.full_text()


def test_full_text_without_tables_is_just_the_text():
    r = PageRecord("x.pdf", 1, "  Metformin first.\n", [])
    assert r.full_text() == "Metformin first."


def test_full_text_undoes_ligatures():
    r = PageRecord("x.pdf", 1, "Standard Treatment Workﬂow is speciﬁc", [])
    assert r.full_text() == "Standard Treatment Workflow is specific"
    assert "ﬁ" in r.text  # stored text is untouched


def test_extract_all_then_load_pages(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "processed"
    (raw / "pub").mkdir(parents=True)
    make_pdf(raw / "pub" / "g.pdf", ["alpha", "beta"])

    manifest = extract_all(raw, out)
    assert manifest["documents"]["pub/g.pdf"]["pages"] == 2
    assert len(manifest["documents"]["pub/g.pdf"]["sha256"]) == 64

    pages = load_pages(out)
    assert set(pages) == {("pub/g.pdf", 1), ("pub/g.pdf", 2)}
    assert "beta" in pages[("pub/g.pdf", 2)].text


def test_symbol_font_fix_changes_only_that_font(tmp_path):
    # "$" in Courier stands in for a symbol font; "$" in Helvetica is a real price.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Price $85", fontname="helv")
    page.insert_text((72, 100), "eGFR $30 and EF #40%", fontname="cour")
    doc.save(tmp_path / "f.pdf")

    [plain] = extract_pdf(tmp_path / "f.pdf", "f.pdf")
    [fixed] = extract_pdf(tmp_path / "f.pdf", "f.pdf", font_fixes={"Courier": {"$": "≥", "#": "≤"}})
    assert "eGFR $30" in plain.text
    assert "Price $85" in fixed.text
    assert "eGFR ≥30 and EF ≤40%" in fixed.text


def test_tables_skipped_only_for_matching_documents(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "processed"
    (raw / "icmr").mkdir(parents=True)
    make_pdf_with_table(raw / "icmr" / "ICMR_STW_X.pdf")
    make_pdf_with_table(raw / "icmr" / "Other.pdf")

    manifest = extract_all(raw, out, skip_tables_for=["icmr/ICMR_STW_*.pdf"])
    assert manifest["documents"]["icmr/ICMR_STW_X.pdf"]["tables_skipped"] is True
    assert manifest["documents"]["icmr/Other.pdf"]["tables_skipped"] is False

    pages = load_pages(out)
    assert pages[("icmr/ICMR_STW_X.pdf", 1)].tables == []
    assert "eGFR" in pages[("icmr/ICMR_STW_X.pdf", 1)].text  # text still extracted
    assert pages[("icmr/Other.pdf", 1)].tables != []


def test_frozen_extraction_is_not_overwritten(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "processed"
    raw.mkdir()
    make_pdf(raw / "g.pdf", ["alpha"])
    extract_all(raw, out)
    with pytest.raises(FileExistsError):
        extract_all(raw, out)
    extract_all(raw, out, force=True)  # allowed when asked for explicitly
