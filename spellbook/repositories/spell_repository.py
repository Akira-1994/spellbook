from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from spellbook.database import connect


SPELL_FIELDS = ("name_zh", "name_en")
ENTRY_FIELDS = (
    "school", "subschool", "components", "casting_time", "range_text",
    "target_text", "area_text", "effect_text", "duration", "saving_throw",
    "spell_resistance", "additional_costs", "description_zh", "description_en",
)
EDITABLE_FIELDS = (*SPELL_FIELDS, *ENTRY_FIELDS)
PAGE_LIMIT = 200


def normalize(value: Any) -> str:
    return "" if value is None else str(value)


def snapshot_of(spell: dict[str, Any]) -> dict[str, str]:
    return {field: normalize(spell.get(field)) for field in EDITABLE_FIELDS}


class SpellRepository:
    def __init__(self, database: Path):
        self.database = Path(database)

    def connect(self) -> sqlite3.Connection:
        return connect(self.database)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def summary(self) -> dict[str, int]:
        with closing(self.connect()) as connection:
            total, edited = connection.execute(
                "SELECT COUNT(*),COUNT(edited_at) FROM spells WHERE record_status='active'"
            ).fetchone()
            return {"total": total, "edited": edited}

    def list_spells(
        self,
        *,
        query: str = "",
        letter: str = "",
        edited_only: bool = False,
        limit: int = PAGE_LIMIT,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = ["s.record_status='active'"]
        params: list[Any] = []
        if query:
            where.append("(s.name_zh LIKE ? OR s.name_en LIKE ? OR e.description_zh LIKE ? OR COALESCE(e.description_en,'') LIKE ?)")
            params.extend([f"%{query}%"] * 4)
        if letter:
            where.append("s.alphabet=?")
            params.append(letter.upper())
        if edited_only:
            where.append("s.edited_at IS NOT NULL")
        params.extend([max(1, min(limit, PAGE_LIMIT)), max(0, offset)])
        sql = f"""
            SELECT s.id,s.name_zh,s.name_en,s.alphabet,e.school,
                   s.edited_at IS NOT NULL AS edited
            FROM spells s JOIN spell_entries e ON e.spell_id=s.id
            WHERE {' AND '.join(where)}
            ORDER BY s.alphabet,upper(s.name_en),e.pdf_page_start
            LIMIT ? OFFSET ?
        """
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(sql, params)]

    def get_spell(self, spell_id: str, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        owns_connection = connection is None
        connection = connection or self.connect()
        try:
            row = connection.execute(
                f"""
                SELECT s.id,s.name_zh,s.name_en,s.alphabet,s.updated_at,s.edited_at,
                       e.id AS entry_id,{','.join('e.' + field for field in ENTRY_FIELDS)},
                       e.pdf_page_start,e.pdf_page_end,e.translation_status
                FROM spells s JOIN spell_entries e ON e.spell_id=s.id
                WHERE s.id=? AND s.record_status='active'
                ORDER BY e.pdf_page_start LIMIT 1
                """,
                (spell_id,),
            ).fetchone()
            if row is None:
                raise KeyError(spell_id)
            result = dict(row)
            entry_id = result["entry_id"]
            result["levels"] = [dict(value) for value in connection.execute(
                """SELECT c.name AS class_name,l.spell_level,l.note FROM spell_levels l
                   JOIN classes c ON c.id=l.class_id WHERE l.spell_entry_id=? ORDER BY c.name,l.spell_level""",
                (entry_id,),
            )]
            result["sources"] = [value[0] for value in connection.execute(
                """SELECT so.name FROM spell_sources ss JOIN sources so ON so.id=ss.source_id
                   WHERE ss.spell_entry_id=? ORDER BY so.name""",
                (entry_id,),
            )]
            result["descriptors"] = [value[0] for value in connection.execute(
                """SELECT d.name FROM spell_descriptors sd JOIN descriptors d ON d.id=sd.descriptor_id
                   WHERE sd.spell_entry_id=? ORDER BY d.name""",
                (entry_id,),
            )]
            result["revision_hash"] = self.revision_hash(result)
            return result
        finally:
            if owns_connection:
                connection.close()

    @staticmethod
    def revision_hash(spell: dict[str, Any]) -> str:
        payload = snapshot_of(spell)
        payload["spell_id"] = spell.get("id")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
