from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from scripts.spellbook_lib import parse_pages, save_entries


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract A-Z spell entries from the source PDF.")
    parser.add_argument("--pdf", type=Path, default=ROOT / "spellbook_doc_v1.1.pdf")
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "pdfs" / "az-parsed.json")
    parser.add_argument("--summary", type=Path, default=ROOT / "tmp" / "pdfs" / "az-extraction-summary.json")
    args = parser.parse_args()

    reader = PdfReader(str(args.pdf))
    pages = [(page, reader.pages[page - 1].extract_text() or "") for page in range(192, 1145)]
    entries = parse_pages(pages)
    save_entries(args.output, entries)

    letters = Counter(entry.alphabet for entry in entries)
    issues = Counter(issue for entry in entries for issue in entry.issues)
    summary = {
        "source": str(args.pdf.resolve()),
        "page_start": 192,
        "page_end": 1144,
        "entries": len(entries),
        "letters": dict(sorted(letters.items())),
        "english_or_mixed_entries": sum(entry.needs_translation for entry in entries),
        "issue_counts": dict(sorted(issues.items())),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
