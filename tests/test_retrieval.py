"""Tests for BM25 retrieval (Approach A)."""

from sugar360_cds.retrieval.bm25 import PageIndex, retrieve, tokenize

PAGES = {
    ("g.pdf", 1): "Metformin is the preferred initial pharmacologic agent.",
    ("g.pdf", 2): "In heart failure, an SGLT2 inhibitor reduces hospitalization.",
    ("g.pdf", 3): "A GLP-1 receptor agonist has cardiovascular benefit in ASCVD.",
    ("g.pdf", 4): "Diet and exercise counselling for all patients.",
    ("g.pdf", 5): "Pages about eye screening and retinal photography.",
}


def test_tokenize_keeps_drug_names_and_drops_stopwords():
    assert tokenize("The GLP-1 RA and DPP-4 inhibitors, HbA1c 7.0%") == [
        "glp-1", "ra", "dpp-4", "inhibitors", "hba1c", "7.0",
    ]


def test_search_finds_the_matching_page():
    index = PageIndex(PAGES)
    [(best, _), *_] = index.search("SGLT2 heart failure", k=3)
    assert best == ("g.pdf", 2)


def test_pages_without_query_words_are_not_returned():
    hits = PageIndex(PAGES).search("SGLT2", k=5)
    assert [key for key, _ in hits] == [("g.pdf", 2)]


def test_retrieve_merges_queries_and_caps_pages():
    index = PageIndex(PAGES)
    hits = retrieve(index, ["metformin initial", "heart failure SGLT2", "GLP-1 cardiovascular"],
                    pages_per_query=2, max_pages=2)
    assert len(hits) == 2


def test_search_within_one_guideline_uses_rarity_from_all():
    # A one-page "guideline" indexed on its own would score 0 for every word.
    alone = PageIndex({("stw.pdf", 1): "Start metformin; add SGLT2 inhibitor in heart failure."})
    assert alone.search("SGLT2 heart failure", k=1) == []

    pages = {**PAGES, ("stw.pdf", 1): "Start metformin; add SGLT2 inhibitor in heart failure."}
    index = PageIndex(pages)
    hits = index.search("SGLT2 heart failure", k=3, within={("stw.pdf", 1)})
    assert [key for key, _ in hits] == [("stw.pdf", 1)]


def test_retrieve_within_returns_only_that_guideline():
    index = PageIndex(PAGES)
    hits = retrieve(index, ["metformin", "SGLT2"], pages_per_query=5, max_pages=5,
                    within={("g.pdf", 1), ("g.pdf", 4)})
    assert {(h.doc_id, h.page) for h in hits} <= {("g.pdf", 1), ("g.pdf", 4)}


def test_page_found_by_two_queries_ranks_first():
    index = PageIndex(PAGES)
    hits = retrieve(index, ["SGLT2 heart failure", "hospitalization", "metformin"], pages_per_query=1, max_pages=3)
    assert (hits[0].doc_id, hits[0].page) == ("g.pdf", 2)
    assert hits[0].queries == (0, 1)
