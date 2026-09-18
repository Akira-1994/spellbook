from __future__ import annotations

import argparse
from pathlib import Path

from scripts.spellbook_lib import build_database, load_entries


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the canonical SQLite spell database.")
    parser.add_argument("--input", type=Path, default=ROOT / "tmp" / "pdfs" / "az-translated.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "spellbook.sqlite")
    args = parser.parse_args()
    entries = load_entries(args.input)
    build_database(args.output, entries)
    print(f"Built {args.output} with {len(entries)} entries")


if __name__ == "__main__":
    main()
