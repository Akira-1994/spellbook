"""Carry corrections of the shipped seed data into existing user databases.

A user database is a copy of the seed taken on first run, so later seed fixes
(see scripts/repair_fields.py) would never reach it. seed_fixes.json lists each
corrected field with its old and new value; a field is updated only while it
still holds the old value, so anything the user edited is left alone. Running
it again is a no-op, so it is simply applied on every start.
"""
from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from typing import Any

from spellbook.config import PACKAGE_ROOT


FIELDS = {
    "components", "casting_time", "range_text", "target_text", "area_text", "effect_text",
    "duration", "saving_throw", "spell_resistance", "description_zh",
}


@lru_cache(maxsize=1)
def data() -> dict[str, Any]:
    path = PACKAGE_ROOT / "seed_fixes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"entries": {}}


def apply(connection: sqlite3.Connection) -> int:
    updated = 0
    for entry_id, diff in data()["entries"].items():
        row = connection.execute(
            f"SELECT spell_id,{','.join(sorted(FIELDS))} FROM spell_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if row is None:
            continue
        current = dict(zip(["spell_id", *sorted(FIELDS)], row))
        changes = {field: new for field, (old, new) in diff.items() if field in FIELDS and (current[field] or "") == old}
        if not changes:
            continue
        connection.execute(
            f"UPDATE spell_entries SET {','.join(f'{field}=?' for field in changes)} WHERE id=?",
            (*changes.values(), entry_id),
        )
        if "description_zh" in changes:
            connection.execute(
                "UPDATE spell_search SET description_zh=? WHERE spell_id=?", (changes["description_zh"], current["spell_id"])
            )
        updated += 1
    return updated
