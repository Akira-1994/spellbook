from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ID_PATTERN = re.compile(r"^spl_[0-9A-HJKMNP-TV-Z]{26}$")


def validate_schema(value: object, schema: dict, path: str = "$") -> list[str]:
    """Validate the JSON Schema keywords used by this project's export schema."""
    errors: list[str] = []
    expected = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "null": type(None),
    }
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(
            isinstance(value, type_map[name]) and not (name == "integer" and isinstance(value, bool))
            for name in allowed
        ):
            return [f"{path}: expected type {expected}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value not in enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string shorter than minLength")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: string does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value above maximum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: array shorter than minItems")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(validate_schema(item, schema["items"], f"{path}[{index}]"))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key}")
        for key, item_schema in schema.get("properties", {}).items():
            if key in value:
                errors.extend(validate_schema(value[key], item_schema, f"{path}.{key}"))
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the canonical database and JSON export.")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "spellbook.sqlite")
    parser.add_argument("--json", type=Path, default=ROOT / "data" / "export" / "spells.json")
    parser.add_argument(
        "--schema", type=Path, default=ROOT / "data" / "export" / "spells.schema.json"
    )
    parser.add_argument(
        "--catalog", type=Path, default=ROOT / "data" / "translations.generated.json"
    )
    args = parser.parse_args()

    errors = []
    with closing(sqlite3.connect(args.database)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            errors.append("SQLite integrity_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            errors.append("SQLite foreign_key_check failed")
        records = connection.execute("SELECT id,name_zh,name_en,alphabet FROM spells").fetchall()
        db_ids = {row[0] for row in records}
        if any(not ID_PATTERN.match(row[0]) for row in records):
            errors.append("Invalid spell ID format")
        if any(not row[1].strip() or not row[2].strip() for row in records):
            errors.append("Missing bilingual spell names")
        letters = Counter(row[3] for row in records)
        missing_letters = sorted(set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") - set(letters))
        if missing_letters:
            errors.append("Missing alphabet sections: " + ",".join(missing_letters))
        orphan_spells = connection.execute(
            "SELECT id FROM spells WHERE NOT EXISTS(SELECT 1 FROM spell_entries e WHERE e.spell_id=spells.id)"
        ).fetchall()
        if orphan_spells:
            errors.append("Spells without entries")
        empty_raw = connection.execute("SELECT COUNT(*) FROM spell_entries WHERE trim(raw_text)='' ").fetchone()[0]
        if empty_raw:
            errors.append(f"Entries without raw text: {empty_raw}")
        empty_descriptions = connection.execute(
            "SELECT COUNT(*) FROM spell_entries WHERE trim(description_zh)=''"
        ).fetchone()[0]
        if empty_descriptions:
            errors.append(f"Entries without Chinese descriptions: {empty_descriptions}")
        entry_count = connection.execute("SELECT COUNT(*) FROM spell_entries").fetchone()[0]
        fts_count = connection.execute("SELECT COUNT(*) FROM spell_search").fetchone()[0]
        if entry_count != len(records):
            errors.append(f"Spell entry count differs from spell count: {entry_count} != {len(records)}")
        if fts_count != len(records):
            errors.append(f"FTS row count differs from spell count: {fts_count} != {len(records)}")
        generated_count = connection.execute(
            "SELECT COUNT(*) FROM spell_entries WHERE translation_status='generated'"
        ).fetchone()[0]

    payload = json.loads(args.json.read_text(encoding="utf-8"))
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    schema_errors = validate_schema(payload, schema)
    if schema_errors:
        errors.extend("JSON Schema: " + message for message in schema_errors[:20])
    json_ids = {spell["id"] for spell in payload["spells"]}
    if payload["spell_count"] != len(payload["spells"]):
        errors.append("JSON spell_count mismatch")
    if db_ids != json_ids:
        errors.append("SQLite and JSON spell ID sets differ")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if generated_count != len(catalog):
        errors.append(
            f"Generated translation count differs from catalog: {generated_count} != {len(catalog)}"
        )

    result = {
        "valid": not errors,
        "spell_count": len(db_ids),
        "letters": dict(sorted(letters.items())),
        "entry_count": entry_count,
        "fts_row_count": fts_count,
        "generated_translation_count": generated_count,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
