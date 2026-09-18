from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def rows(connection, sql, parameters=()):
    return [dict(row) for row in connection.execute(sql, parameters)]


def export_database(database: Path, output: Path) -> int:
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        result = []
        for spell in rows(connection, "SELECT * FROM spells ORDER BY alphabet, name_en COLLATE NOCASE, id"):
            spell_id = spell["id"]
            entries = rows(connection, "SELECT * FROM spell_entries WHERE spell_id=? ORDER BY pdf_page_start,id", (spell_id,))
            for entry in entries:
                entry_id = entry["id"]
                entry["levels"] = rows(
                    connection,
                    """SELECT c.name AS class_name, sl.spell_level, sl.note
                       FROM spell_levels sl JOIN classes c ON c.id=sl.class_id
                       WHERE sl.spell_entry_id=? ORDER BY sl.spell_level,c.name""",
                    (entry_id,),
                )
                entry["sources"] = [
                    row["name"]
                    for row in rows(
                        connection,
                        """SELECT s.name FROM spell_sources ss JOIN sources s ON s.id=ss.source_id
                           WHERE ss.spell_entry_id=? ORDER BY s.name""",
                        (entry_id,),
                    )
                ]
                entry["descriptors"] = [
                    row["name"]
                    for row in rows(
                        connection,
                        """SELECT d.name FROM spell_descriptors sd JOIN descriptors d ON d.id=sd.descriptor_id
                           WHERE sd.spell_entry_id=? ORDER BY d.name""",
                        (entry_id,),
                    )
                ]
                entry["issues"] = rows(
                    connection,
                    "SELECT issue_type,severity,message FROM extraction_issues WHERE spell_entry_id=? ORDER BY id",
                    (entry_id,),
                )
            spell["aliases"] = rows(
                connection,
                "SELECT language,name,name_type FROM spell_names WHERE spell_id=? ORDER BY language,name",
                (spell_id,),
            )
            spell["entries"] = entries
            result.append(spell)

    payload = {"schema_version": 1, "spell_count": len(result), "spells": result}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export canonical SQLite spell data as nested JSON.")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "spellbook.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "export" / "spells.json")
    args = parser.parse_args()
    count = export_database(args.database, args.output)
    print(f"Exported {count} spells to {args.output}")


if __name__ == "__main__":
    main()
