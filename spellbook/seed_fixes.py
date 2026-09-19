"""Carry corrections of the shipped seed data into existing user databases.

A user database is a copy of the seed taken on first run, so later seed fixes
(scripts/repair_fields.py, scripts/add_missing_spells.py) would never reach
it. seed_fixes.json holds the fix sets in the order they were made. Each set
may correct entry fields or spell names ([old, new] pairs; a value changes
only while it still holds the old value, so user edits are kept) and add
spells, which are copied from the seed. Every step is a no-op once applied,
so the whole file is simply applied on every start.
"""
from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from spellbook.config import PACKAGE_ROOT


ENTRY_FIELDS = {
    "heading", "school", "subschool", "components", "casting_time", "range_text", "target_text", "area_text",
    "effect_text", "duration", "saving_throw", "spell_resistance", "description_zh",
}
SPELL_FIELDS = {"name_zh", "name_en"}
SEARCH_COLUMNS = {"description_zh": "description_zh", "school": "school", "name_zh": "name_zh", "name_en": "name_en"}


@lru_cache(maxsize=1)
def data() -> dict[str, Any]:
    path = PACKAGE_ROOT / "seed_fixes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"sets": []}


def apply(connection: sqlite3.Connection, seed_database: Path | None = None) -> int:
    changed = 0
    for fix_set in data()["sets"]:
        changed += _apply_fields(connection, "spell_entries", fix_set.get("entries", {}), ENTRY_FIELDS)
        changed += _apply_fields(connection, "spells", fix_set.get("spells", {}), SPELL_FIELDS)
        if fix_set.get("added_spells") and seed_database is not None:
            changed += _add_spells(connection, seed_database, fix_set["added_spells"])
    return changed


def _apply_fields(connection: sqlite3.Connection, table: str, fixes: dict, allowed: set[str]) -> int:
    updated = 0
    for row_id, diff in fixes.items():
        fields = sorted(field for field in diff if field in allowed)
        row = connection.execute(f"SELECT {','.join(fields)} FROM {table} WHERE id=?", (row_id,)).fetchone()
        if row is None:
            continue
        changes = {field: diff[field][1] for field, value in zip(fields, row) if (value or "") == diff[field][0]}
        if not changes:
            continue
        connection.execute(
            f"UPDATE {table} SET {','.join(f'{field}=?' for field in changes)} WHERE id=?", (*changes.values(), row_id)
        )
        spell_id = row_id if table == "spells" else connection.execute(
            "SELECT spell_id FROM spell_entries WHERE id=?", (row_id,)
        ).fetchone()[0]
        for field, value in changes.items():
            if field in SEARCH_COLUMNS:
                connection.execute(f"UPDATE spell_search SET {SEARCH_COLUMNS[field]}=? WHERE spell_id=?", (value, spell_id))
            if field in SPELL_FIELDS:
                connection.execute(
                    "UPDATE spell_names SET name=? WHERE spell_id=? AND language=? AND name_type='canonical'",
                    (value, spell_id, "zh" if field == "name_zh" else "en"),
                )
        updated += 1
    return updated


def _columns(connection: sqlite3.Connection, table: str, schema: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA {schema}.table_info({table})")]


def _add_spells(connection: sqlite3.Connection, seed_database: Path, spell_ids: list[str]) -> int:
    missing = [sid for sid in spell_ids if not connection.execute("SELECT 1 FROM spells WHERE id=?", (sid,)).fetchone()]
    if not missing:
        return 0
    connection.commit()  # ATTACH is not allowed inside a transaction
    connection.execute("ATTACH DATABASE ? AS seed", (str(Path(seed_database).resolve()),))  # only read
    try:
        marks = ",".join("?" * len(missing))
        entries = f"(SELECT id FROM seed.spell_entries WHERE spell_id IN ({marks}))"
        # Copy columns both schemas share; user-only columns (edited_at) keep defaults.
        for table, where in (("spells", f"id IN ({marks})"), ("spell_entries", f"spell_id IN ({marks})")):
            shared = [c for c in _columns(connection, table, "seed") if c in _columns(connection, table, "main")]
            connection.execute(
                f"INSERT INTO main.{table}({','.join(shared)}) SELECT {','.join(shared)} FROM seed.{table} WHERE {where}", missing
            )
        connection.execute(
            f"INSERT INTO main.spell_names(spell_id,language,name,name_type) SELECT spell_id,language,name,name_type "
            f"FROM seed.spell_names WHERE spell_id IN ({marks})", missing,
        )
        # Lookup tables are matched by name: ids may differ between the databases.
        for lookup, link, column in (
            ("classes", "spell_levels", "class_id"),
            ("sources", "spell_sources", "source_id"),
            ("descriptors", "spell_descriptors", "descriptor_id"),
        ):
            connection.execute(
                f"INSERT OR IGNORE INTO main.{lookup}(name) SELECT s.name FROM seed.{link} l "
                f"JOIN seed.{lookup} s ON s.id=l.{column} WHERE l.spell_entry_id IN {entries}", missing,
            )
            extra = [c for c in _columns(connection, link, "seed") if c not in ("spell_entry_id", column)]
            select_extra = "".join(f",l.{c}" for c in extra)
            connection.execute(
                f"INSERT OR IGNORE INTO main.{link}(spell_entry_id,{column}{''.join(',' + c for c in extra)}) "
                f"SELECT l.spell_entry_id,m.id{select_extra} FROM seed.{link} l JOIN seed.{lookup} s ON s.id=l.{column} "
                f"JOIN main.{lookup} m ON m.name=s.name WHERE l.spell_entry_id IN {entries}", missing,
            )
        connection.execute(
            f"INSERT INTO main.spell_search SELECT * FROM seed.spell_search WHERE spell_id IN ({marks})", missing
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("DETACH DATABASE seed")
    return len(missing)
