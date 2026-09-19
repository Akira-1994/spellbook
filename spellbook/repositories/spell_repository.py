from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from spellbook import taxonomy
from spellbook.database import connect


SPELL_FIELDS = ("name_zh", "name_en")
ENTRY_FIELDS = (
    "school", "subschool", "components", "casting_time", "range_text",
    "target_text", "area_text", "effect_text", "duration", "saving_throw",
    "spell_resistance", "additional_costs", "description_zh", "description_en",
)
EDITABLE_FIELDS = (*SPELL_FIELDS, *ENTRY_FIELDS)
PAGE_LIMIT = 200
SCHOOL_ORDER = {school["key"]: index for index, school in enumerate(taxonomy.schools())}


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

    @staticmethod
    def _filters(
        query: str,
        letter: str,
        edited_only: bool,
        schools: list[str],
        class_id: int | None,
        levels: list[int],
    ) -> tuple[str, list[Any]]:
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
        if schools:
            where.append(f"EXISTS(SELECT 1 FROM spell_schools ss WHERE ss.spell_entry_id=e.id AND ss.school IN ({','.join('?' * len(schools))}))")
            params.extend(schools)
        if class_id is not None or levels:
            # Class and level describe the same row: "wizard 3" must not match a
            # spell that is wizard 5 and cleric 3.
            conditions = ["cl.spell_entry_id=e.id"]
            if class_id is not None:
                conditions.append("cl.class_id=?")
                params.append(class_id)
            if levels:
                conditions.append(f"cl.level IN ({','.join('?' * len(levels))})")
                params.extend(levels)
            where.append(f"EXISTS(SELECT 1 FROM spell_class_levels cl WHERE {' AND '.join(conditions)})")
        return " AND ".join(where), params

    def list_spells(
        self,
        *,
        query: str = "",
        letter: str = "",
        edited_only: bool = False,
        schools: list[str] | None = None,
        class_id: int | None = None,
        levels: list[int] | None = None,
        limit: int = PAGE_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        schools, levels = schools or [], levels or []
        where, params = self._filters(query, letter, edited_only, schools, class_id, levels)
        level_column = "NULL"
        level_params: list[Any] = []
        if class_id is not None:
            level_filter = f" AND level IN ({','.join('?' * len(levels))})" if levels else ""
            level_column = f"(SELECT MIN(level) FROM spell_class_levels WHERE spell_entry_id=e.id AND class_id=?{level_filter})"
            level_params = [class_id, *levels]
        sql = f"""
            SELECT s.id,s.name_zh,s.name_en,s.alphabet,
                   s.edited_at IS NOT NULL AS edited,
                   (SELECT GROUP_CONCAT(school) FROM spell_schools WHERE spell_entry_id=e.id) AS schools,
                   {level_column} AS class_level
            FROM spells s JOIN spell_entries e ON e.spell_id=s.id
            WHERE {where}
            ORDER BY s.alphabet,upper(s.name_en),e.pdf_page_start
            LIMIT ? OFFSET ?
        """
        page = [max(1, min(limit, PAGE_LIMIT)), max(0, offset)]
        with closing(self.connect()) as connection:
            items = [dict(row) for row in connection.execute(sql, [*level_params, *params, *page])]
            total = connection.execute(
                f"SELECT COUNT(*) FROM spells s JOIN spell_entries e ON e.spell_id=s.id WHERE {where}", params
            ).fetchone()[0]
        for item in items:
            item["schools"] = sorted((item["schools"] or "").split(","), key=SCHOOL_ORDER.get) if item["schools"] else []
        return {"items": items, "total": total}

    def taxonomy(self) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            school_counts = dict(connection.execute(
                """SELECT ss.school,COUNT(DISTINCT s.id) FROM spell_schools ss
                   JOIN spell_entries e ON e.id=ss.spell_entry_id JOIN spells s ON s.id=e.spell_id
                   WHERE s.record_status='active' GROUP BY ss.school"""
            ).fetchall())
            classes = [dict(row) for row in connection.execute(
                """SELECT c.id,c.name,c.kind,c.name_en,COUNT(DISTINCT s.id) AS count
                   FROM class_catalog c
                   JOIN spell_class_levels l ON l.class_id=c.id
                   JOIN spell_entries e ON e.id=l.spell_entry_id JOIN spells s ON s.id=e.spell_id
                   WHERE s.record_status='active'
                   GROUP BY c.id ORDER BY count DESC,c.sort_order"""
            )]
        schools = [dict(school, count=school_counts.get(school["key"], 0)) for school in taxonomy.schools()]
        return {"schools": [s for s in schools if s["count"] or s["key"] != taxonomy.UNCLASSIFIED], "classes": classes}

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
                """SELECT c.id AS class_id,c.name AS class_name,c.kind,l.level AS spell_level,l.note
                   FROM spell_class_levels l JOIN class_catalog c ON c.id=l.class_id
                   WHERE l.spell_entry_id=? ORDER BY c.kind='domain',c.kind='other',c.sort_order,l.level""",
                (entry_id,),
            )]
            result["schools"] = sorted(
                (row[0] for row in connection.execute("SELECT school FROM spell_schools WHERE spell_entry_id=?", (entry_id,))),
                key=SCHOOL_ORDER.get,
            )
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
