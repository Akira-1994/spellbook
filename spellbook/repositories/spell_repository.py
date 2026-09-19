from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SPELL_FIELDS = ("name_zh", "name_en", "variant_group_id")
ENTRY_FIELDS = (
    "school", "subschool", "components", "casting_time", "range_text",
    "target_text", "area_text", "effect_text", "duration", "saving_throw",
    "spell_resistance", "description_zh", "description_en", "additional_costs",
)


class SpellRepository:
    def __init__(self, database: Path, migration_path: Path):
        self.database = Path(database)
        self.migration_path = Path(migration_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(spells)")}
            if "merged_into_id" not in columns:
                connection.execute("ALTER TABLE spells ADD COLUMN merged_into_id TEXT")
            if "record_status" not in columns:
                connection.execute("ALTER TABLE spells ADD COLUMN record_status TEXT NOT NULL DEFAULT 'active'")
            if "reviewed_revision_hash" not in columns:
                connection.execute("ALTER TABLE spells ADD COLUMN reviewed_revision_hash TEXT")
            connection.executescript(self.migration_path.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(2,?)",
                (datetime.now(timezone.utc).isoformat(),),
            )

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

    def summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            counts = dict(
                connection.execute(
                    "SELECT review_status,COUNT(*) FROM spells WHERE record_status='active' GROUP BY review_status"
                ).fetchall()
            )
            return {
                "total": connection.execute(
                    "SELECT COUNT(*) FROM spells WHERE record_status='active'"
                ).fetchone()[0],
                "unreviewed": counts.get("unreviewed", 0),
                "needs_review": counts.get("needs_review", 0),
                "reviewed": counts.get("reviewed", 0),
                "generated": connection.execute(
                    "SELECT COUNT(*) FROM spell_entries WHERE translation_status='generated'"
                ).fetchone()[0],
                "duplicates": connection.execute(
                    "SELECT COUNT(DISTINCT spell_entry_id) FROM extraction_issues WHERE issue_type='duplicate_english_name'"
                ).fetchone()[0],
                "conflicts": connection.execute(
                    "SELECT COUNT(*) FROM review_conflicts WHERE status='open'"
                ).fetchone()[0],
            }

    def list_spells(
        self,
        *,
        query: str = "",
        status: str = "",
        letter: str = "",
        issue: str = "",
        limit: int = 80,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = ["s.record_status='active'"]
        params: list[Any] = []
        if query:
            where.append("(s.name_zh LIKE ? OR s.name_en LIKE ? OR s.id LIKE ? OR e.description_zh LIKE ? OR COALESCE(e.description_en,'') LIKE ?)")
            term = f"%{query}%"
            params.extend([term] * 5)
        if status:
            where.append("s.review_status=?")
            params.append(status)
        if letter:
            where.append("s.alphabet=?")
            params.append(letter.upper())
        if issue == "generated":
            where.append("e.translation_status='generated'")
        elif issue == "duplicate":
            where.append("EXISTS(SELECT 1 FROM extraction_issues i WHERE i.spell_entry_id=e.id AND i.issue_type='duplicate_english_name')")
        elif issue == "conflict":
            where.append("EXISTS(SELECT 1 FROM review_conflicts c WHERE c.spell_id=s.id AND c.status='open')")
        params.extend([max(1, min(limit, 200)), max(0, offset)])
        sql = f"""
            SELECT s.id,s.name_zh,s.name_en,s.alphabet,s.review_status,
                   e.id AS entry_id,e.pdf_page_start,e.translation_status,
                   CASE WHEN EXISTS(
                     SELECT 1 FROM extraction_issues i
                     WHERE i.spell_entry_id=e.id AND i.issue_type='duplicate_english_name'
                   ) THEN 1 ELSE 0 END AS duplicate_name
            FROM spells s JOIN spell_entries e ON e.spell_id=s.id
            WHERE {' AND '.join(where)}
            ORDER BY s.alphabet,upper(s.name_en),e.pdf_page_start
            LIMIT ? OFFSET ?
        """
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params)]

    def get_spell(self, spell_id: str, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        owns_connection = connection is None
        connection = connection or self.connect()
        try:
            row = connection.execute(
                """
                SELECT s.*,e.id AS entry_id,e.heading,e.school,e.subschool,e.components,
                       e.casting_time,e.range_text,e.target_text,e.area_text,e.effect_text,
                       e.duration,e.saving_throw,e.spell_resistance,e.description_zh,
                       e.description_en,e.additional_costs,e.pdf_page_start,e.pdf_page_end,
                       e.raw_text,e.parse_confidence,e.review_status AS entry_review_status,
                       e.translation_status,e.translation_method
                FROM spells s JOIN spell_entries e ON e.spell_id=s.id
                WHERE s.id=?
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
            result["issues"] = [dict(value) for value in connection.execute(
                "SELECT issue_type,severity,message FROM extraction_issues WHERE spell_entry_id=? ORDER BY id",
                (entry_id,),
            )]
            result["checks"] = [dict(value) for value in connection.execute(
                "SELECT check_type,revision_hash,editor_name,checked_at FROM review_checks WHERE spell_id=? ORDER BY check_type",
                (spell_id,),
            )]
            result["history"] = [dict(value) for value in connection.execute(
                """SELECT event_id,action,editor_name,occurred_at_utc,payload_json
                   FROM review_events WHERE spell_id=? ORDER BY occurred_at_utc DESC LIMIT 50""",
                (spell_id,),
            )]
            result["conflicts"] = [dict(value) for value in connection.execute(
                """SELECT id,field_path,base_value_json,value_a_json,value_b_json,created_at
                   FROM review_conflicts WHERE spell_id=? AND status='open' ORDER BY created_at""",
                (spell_id,),
            )]
            result["revision_hash"] = self.revision_hash(result)
            return result
        finally:
            if owns_connection:
                connection.close()

    @staticmethod
    def revision_hash(spell: dict[str, Any]) -> str:
        payload = {key: spell.get(key) for key in (*SPELL_FIELDS, *ENTRY_FIELDS)}
        payload["spell_id"] = spell.get("id")
        payload["entry_id"] = spell.get("entry_id")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
