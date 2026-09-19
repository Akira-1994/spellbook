from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spellbook import taxonomy
from spellbook.database import connect
from spellbook.repositories.spell_repository import (
    EDITABLE_FIELDS,
    ENTRY_FIELDS,
    SPELL_FIELDS,
    SpellRepository,
    normalize,
    snapshot_of,
)


MAX_VERSIONS = 5
REQUIRED_FIELDS = ("name_zh", "name_en")
NULLABLE_FIELDS = ("description_en",)
SEARCH_FIELDS = ("name_zh", "name_en", "description_zh", "description_en", "school")


class EditError(ValueError):
    pass


class StaleRevisionError(EditError):
    pass


class EditService:
    """Edits spells while keeping the last MAX_VERSIONS prior states of each one.

    Every change (edit, rollback, restore) first stores the content it replaces,
    so a rollback can itself be rolled back. The pristine seed database is the
    permanent fallback that "restore original" reads from.
    """

    def __init__(self, spells: SpellRepository, seed_database: Path):
        self.spells = spells
        self.seed_database = Path(seed_database)

    def update(self, spell_id: str, fields: dict[str, Any], revision_hash: str) -> dict[str, Any]:
        unknown = sorted(set(fields) - set(EDITABLE_FIELDS))
        if unknown:
            raise EditError(f"不可編輯的欄位：{', '.join(unknown)}")
        return self._replace(spell_id, revision_hash, fields, "edit")

    def versions(self, spell_id: str) -> list[dict[str, Any]]:
        with closing(self.spells.connect()) as connection:
            self.spells.get_spell(spell_id, connection)
            rows = connection.execute(
                """SELECT id,snapshot_json,content_saved_at,replaced_at,replaced_by
                   FROM spell_versions WHERE spell_id=? ORDER BY id DESC""",
                (spell_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "content": json.loads(row["snapshot_json"]),
                "content_saved_at": row["content_saved_at"],
                "replaced_at": row["replaced_at"],
                "replaced_by": row["replaced_by"],
            }
            for row in rows
        ]

    def rollback(self, spell_id: str, version_id: int, revision_hash: str) -> dict[str, Any]:
        with closing(self.spells.connect()) as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM spell_versions WHERE id=? AND spell_id=?",
                (version_id, spell_id),
            ).fetchone()
        if row is None:
            raise EditError("找不到這個版本，可能已超過保留數量")
        return self._replace(spell_id, revision_hash, json.loads(row["snapshot_json"]), "rollback")

    def restore_original(self, spell_id: str, revision_hash: str) -> dict[str, Any]:
        return self._replace(spell_id, revision_hash, self.original(spell_id), "restore_original")

    def original(self, spell_id: str) -> dict[str, str]:
        with closing(connect(self.seed_database, readonly=True)) as connection:
            row = connection.execute(
                f"""SELECT s.name_zh,s.name_en,{','.join('e.' + field for field in ENTRY_FIELDS)}
                    FROM spells s JOIN spell_entries e ON e.spell_id=s.id
                    WHERE s.id=? ORDER BY e.pdf_page_start LIMIT 1""",
                (spell_id,),
            ).fetchone()
        if row is None:
            raise EditError("內建資料中沒有這筆法術的原始內容")
        return snapshot_of(dict(row))

    def _replace(self, spell_id: str, revision_hash: str, requested: dict[str, Any], reason: str) -> dict[str, Any]:
        with self.spells.transaction() as connection:
            try:
                current = self.spells.get_spell(spell_id, connection)
            except KeyError:
                raise EditError("找不到法術") from None
            if current["revision_hash"] != revision_hash:
                raise StaleRevisionError("這筆法術已在其他視窗被修改，請重新載入後再編輯")

            before = snapshot_of(current)
            after = dict(before)
            for field, value in requested.items():
                after[field] = normalize(value).strip() if field in REQUIRED_FIELDS else normalize(value)
            for field in REQUIRED_FIELDS:
                if not after[field]:
                    raise EditError("中文與英文名稱不可留空")
            changed = [field for field in EDITABLE_FIELDS if after[field] != before[field]]
            if not changed:
                raise EditError("內容沒有任何變更")

            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """INSERT INTO spell_versions(spell_id,snapshot_json,content_saved_at,replaced_at,replaced_by)
                   VALUES(?,?,?,?,?)""",
                (spell_id, json.dumps(before, ensure_ascii=False), current["edited_at"], now, reason),
            )
            connection.execute(
                """DELETE FROM spell_versions WHERE spell_id=? AND id NOT IN (
                     SELECT id FROM spell_versions WHERE spell_id=? ORDER BY id DESC LIMIT ?)""",
                (spell_id, spell_id, MAX_VERSIONS),
            )
            # "Edited" means "differs from the original", however it got there.
            restored = reason == "restore_original" or after == self.original(spell_id)
            self._write(connection, current, after, changed, now, restored=restored)
            return self.spells.get_spell(spell_id, connection)

    @staticmethod
    def _write(
        connection: sqlite3.Connection,
        current: dict[str, Any],
        after: dict[str, str],
        changed: list[str],
        now: str,
        *,
        restored: bool,
    ) -> None:
        spell_id, entry_id = current["id"], current["entry_id"]
        stored = {field: (after[field] or None) if field in NULLABLE_FIELDS else after[field] for field in changed}
        entry_changes = [field for field in changed if field in ENTRY_FIELDS]
        if entry_changes:
            connection.execute(
                f"UPDATE spell_entries SET {','.join(f'{field}=?' for field in entry_changes)} WHERE id=?",
                (*(stored[field] for field in entry_changes), entry_id),
            )
        for field in (f for f in changed if f in SPELL_FIELDS):
            connection.execute(f"UPDATE spells SET {field}=? WHERE id=?", (stored[field], spell_id))
            connection.execute(
                "UPDATE spell_names SET name=? WHERE spell_id=? AND language=? AND name_type='canonical'",
                (stored[field], spell_id, "zh" if field == "name_zh" else "en"),
            )
        initial = after["name_en"][:1].upper()
        alphabet = initial if "A" <= initial <= "Z" else current["alphabet"]
        connection.execute(
            "UPDATE spells SET alphabet=?,updated_at=?,edited_at=? WHERE id=?",
            (alphabet, now, None if restored else now, spell_id),
        )
        if "school" in changed:
            taxonomy.set_schools(connection, entry_id, after["school"])
        search_changes = [field for field in changed if field in SEARCH_FIELDS]
        if search_changes:
            connection.execute(
                f"UPDATE spell_search SET {','.join(f'{field}=?' for field in search_changes)} WHERE spell_id=?",
                (*(after[field] for field in search_changes), spell_id),
            )
