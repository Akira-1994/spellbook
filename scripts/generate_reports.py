from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate extraction and translation review reports.")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "spellbook.sqlite")
    parser.add_argument(
        "--catalog", type=Path, default=ROOT / "data" / "translations.generated.json"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "reports")
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    with closing(sqlite3.connect(args.database)) as connection:
        connection.row_factory = sqlite3.Row
        spells = connection.execute(
            """
            SELECT s.id, s.name_zh, s.name_en, s.alphabet, s.review_status,
                   e.id AS entry_id, e.pdf_page_start, e.pdf_page_end,
                   e.parse_confidence, e.translation_status, e.translation_method,
                   group_concat(i.issue_type, ';') AS issues
            FROM spells s
            JOIN spell_entries e ON e.spell_id = s.id
            LEFT JOIN extraction_issues i ON i.spell_entry_id = e.id
            GROUP BY s.id, e.id
            ORDER BY s.alphabet, upper(s.name_en), e.pdf_page_start
            """
        ).fetchall()
        fts_count = connection.execute("SELECT COUNT(*) FROM spell_search").fetchone()[0]
        class_count = connection.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
        source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        descriptor_count = connection.execute("SELECT COUNT(*) FROM descriptors").fetchone()[0]

    review_rows = [
        {
            "spell_id": row["id"],
            "name_zh": row["name_zh"],
            "name_en": row["name_en"],
            "pdf_pages": (
                str(row["pdf_page_start"])
                if row["pdf_page_start"] == row["pdf_page_end"]
                else f"{row['pdf_page_start']}-{row['pdf_page_end']}"
            ),
            "parse_confidence": f"{row['parse_confidence']:.3f}",
            "translation_status": row["translation_status"],
            "issues": row["issues"] or ("generated_translation" if row["translation_status"] == "generated" else ""),
        }
        for row in spells
        if row["review_status"] == "needs_review"
    ]
    write_csv(
        args.output / "needs-review.csv",
        [
            "spell_id",
            "name_zh",
            "name_en",
            "pdf_pages",
            "parse_confidence",
            "translation_status",
            "issues",
        ],
        review_rows,
    )

    by_name = {row["name_en"]: row for row in spells}
    translation_rows = []
    for name_en, item in sorted(catalog.items(), key=lambda pair: pair[0].upper()):
        row = by_name[name_en]
        translation_rows.append(
            {
                "spell_id": row["id"],
                "name_zh": item["name_zh"],
                "name_en": name_en,
                "pdf_pages": (
                    str(row["pdf_page_start"])
                    if row["pdf_page_start"] == row["pdf_page_end"]
                    else f"{row['pdf_page_start']}-{row['pdf_page_end']}"
                ),
                "provider": item.get("provider", "unknown"),
                "needs_review": str(bool(item.get("needs_review", True))).lower(),
            }
        )
    write_csv(
        args.output / "translation-review.csv",
        ["spell_id", "name_zh", "name_en", "pdf_pages", "provider", "needs_review"],
        translation_rows,
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_pdf": "spellbook_doc_v1.1.pdf",
        "included_pdf_pages": [
            {"start": 192, "end": 1144, "section": "A-Z spells"},
            {"start": 1149, "end": 1162, "section": "summon appendix (scripts/add_summon_spells.py)"},
        ],
        "excluded_pdf_pages": [
            {"start": 1145, "end": 1148, "reason": "curse appendix (rules, not spells)"},
        ],
        "spell_count": len(spells),
        "fts_row_count": fts_count,
        "alphabet_counts": dict(sorted(Counter(row["alphabet"] for row in spells).items())),
        "generated_translation_count": len(catalog),
        "needs_review_count": len(review_rows),
        "class_count": class_count,
        "source_count": source_count,
        "descriptor_count": descriptor_count,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "extraction-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
