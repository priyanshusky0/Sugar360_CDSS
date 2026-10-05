"""Smoke test: every library in requirements.txt can be imported."""

import importlib

import pytest

# Import names differ from install names for some packages (e.g. pyyaml -> yaml).
MODULES = [
    "pymupdf",
    "pdfplumber",
    "rank_bm25",
    "rapidfuzz",
    "dotenv",
    "yaml",
    "pandas",
    "openpyxl",
    "google.genai",
    "pytest",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_library_imports(module_name):
    importlib.import_module(module_name)
