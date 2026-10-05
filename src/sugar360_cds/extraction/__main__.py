"""Extract all guideline PDFs.

Run from the project folder:
    PYTHONPATH=src python -m sugar360_cds.extraction            # first time
    PYTHONPATH=src python -m sugar360_cds.extraction --force    # deliberately redo
"""

import argparse
from pathlib import Path

import yaml

from .pdf_text import extract_all

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--raw", default="data/raw/documents")
parser.add_argument("--out", default="data/processed")
parser.add_argument("--config", default="configs/extraction.yaml")
parser.add_argument("--force", action="store_true", help="overwrite an existing (frozen) extraction")
args = parser.parse_args()

config = yaml.safe_load(Path(args.config).read_text())
manifest = extract_all(
    args.raw,
    args.out,
    force=args.force,
    skip_tables_for=config["skip_tables_for"],
    font_fixes=config.get("symbol_font_fixes", {}),
)
for doc_id, info in manifest["documents"].items():
    tables = "tables skipped" if info["tables_skipped"] else f"{info['pages_with_tables']:3} with tables"
    errors = f"  table errors: {info['table_errors']}" if info["table_errors"] else ""
    print(f"{doc_id:55} {info['pages']:4} pages  {tables}{errors}")
print(f"{len(manifest['documents'])} documents -> {args.out}")
