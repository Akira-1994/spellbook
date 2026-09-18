from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from spellbook_reviewer.domain.events import ReviewEvent
from spellbook_reviewer.domain.ids import new_ulid
from spellbook_reviewer.repositories.event_repository import EventRepository
from spellbook_reviewer.repositories.spell_repository import ENTRY_FIELDS, SPELL_FIELDS, SpellRepository


class ReplayError(RuntimeError):
    pass


class ReplayService:
    """Projects immutable events merged by Git into the local SQLite database."""

    def __init__(self, spells: SpellRepository, events: EventRepository):
        self.spells = spells
        self.events = events

    def replay(self) -> dict[str, int]:
        applied = conflicts = skipped = 0
        for event in self.events.events():
            with self.spells.connect() as connection:
                existing = connection.execute(
                    "SELECT event_hash FROM review_events WHERE event_id=?", (event.event_id,)
                ).fetchone()
            if existing:
                if existing[0] != event.digest():
                    raise ReplayError(f"事件內容遭變更：{event.event_id}")
                skipped += 1
                continue
            with self.spells.transaction() as connection:
                connection.execute(
                    """INSERT INTO review_events(event_id,spell_id,spell_entry_id,action,editor_name,
                       occurred_at_utc,base_revision_hash,event_hash,payload_json,applied_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event.event_id, event.spell_id, event.spell_entry_id, event.action,
                        event.editor_name, event.occurred_at_utc, event.base_revision_hash,
                        event.digest(), event.to_json(), datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conflicts += self._apply(connection, event)
            applied += 1
        return {"applied": applied, "conflicts": conflicts, "skipped": skipped}

    def _apply(self, connection: sqlite3.Connection, event: ReviewEvent) -> int:
        if event.action in {"update_fields", "resolve_conflict"}:
            conflicts = self._apply_fields(connection, event)
            conflict_id = event.metadata.get("conflict_id")
            if event.action == "resolve_conflict" and conflict_id:
                connection.execute(
                    "UPDATE review_conflicts SET status='resolved',resolution_event_id=?,resolved_at=? WHERE id=?",
                    (event.event_id, event.occurred_at_utc, conflict_id),
                )
            return conflicts
        current = self.spells.get_spell(event.spell_id, connection)
        if event.action == "set_check":
            if current["revision_hash"] != event.base_revision_hash:
                return 0
            connection.execute("DELETE FROM review_checks WHERE spell_id=?", (event.spell_id,))
            for check_type in event.completed_checks:
                connection.execute(
                    """INSERT INTO review_checks(spell_id,check_type,revision_hash,editor_name,checked_at,event_id)
                       VALUES(?,?,?,?,?,?)""",
                    (event.spell_id, check_type, event.base_revision_hash, event.editor_name, event.occurred_at_utc, event.event_id),
                )
        elif event.action == "approve_review" and current["revision_hash"] == event.base_revision_hash:
            connection.execute(
                "UPDATE spells SET review_status='reviewed',reviewed_revision_hash=?,updated_at=? WHERE id=?",
                (event.base_revision_hash, event.occurred_at_utc, event.spell_id),
            )
            connection.execute(
                """UPDATE spell_entries SET review_status='reviewed',
                   translation_status=CASE WHEN translation_status='generated' THEN 'reviewed' ELSE translation_status END
                   WHERE id=?""",
                (event.spell_entry_id,),
            )
        elif event.action in {"merge_spell", "set_variant_group"}:
            self._apply_relation_changes(connection, event)
        if event.action in {"merge_spell", "set_variant_group", "resolve_duplicate"}:
            decision = event.metadata.get("decision") or ({"merge_spell": "merge", "set_variant_group": "variant"}.get(event.action))
            if decision:
                connection.execute(
                    """INSERT OR REPLACE INTO duplicate_decisions
                       (spell_id,decision,related_spell_id,editor_name,decided_at,event_id,note)
                       VALUES(?,?,?,?,?,?,?)""",
                    (event.spell_id, decision, event.metadata.get("related_spell_id"), event.editor_name, event.occurred_at_utc, event.event_id, event.note),
                )
        return 0

    def _apply_fields(self, connection: sqlite3.Connection, event: ReviewEvent) -> int:
        current = self.spells.get_spell(event.spell_id, connection)
        conflict_count = 0
        changed = False
        for change in event.changes:
            if "." not in change.path:
                continue
            scope, field = change.path.split(".", 1)
            allowed = SPELL_FIELDS if scope == "spell" else ENTRY_FIELDS if scope == "entry" else ()
            if field not in allowed:
                continue
            actual = current.get(field)
            if actual == change.after:
                continue
            if actual != change.before:
                prior = connection.execute(
                    "SELECT event_id FROM review_events WHERE spell_id=? AND event_id<>? ORDER BY occurred_at_utc DESC LIMIT 1",
                    (event.spell_id, event.event_id),
                ).fetchone()
                connection.execute(
                    """INSERT INTO review_conflicts(id,spell_id,spell_entry_id,field_path,base_value_json,
                       event_a_id,event_b_id,value_a_json,value_b_json,status,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,'open',?)""",
                    (
                        "cnf_" + new_ulid(), event.spell_id, event.spell_entry_id, change.path,
                        json.dumps(change.before, ensure_ascii=False), prior[0] if prior else "baseline",
                        event.event_id, json.dumps(actual, ensure_ascii=False),
                        json.dumps(change.after, ensure_ascii=False), event.occurred_at_utc,
                    ),
                )
                conflict_count += 1
                continue
            table = "spells" if scope == "spell" else "spell_entries"
            key = event.spell_id if scope == "spell" else event.spell_entry_id
            connection.execute(f"UPDATE {table} SET {field}=? WHERE id=?", (change.after, key))
            changed = True
        if changed:
            connection.execute(
                "UPDATE spells SET review_status='needs_review',reviewed_revision_hash=NULL,updated_at=? WHERE id=?",
                (event.occurred_at_utc, event.spell_id),
            )
            connection.execute("UPDATE spell_entries SET review_status='needs_review' WHERE id=?", (event.spell_entry_id,))
            connection.execute("DELETE FROM review_checks WHERE spell_id=?", (event.spell_id,))
        return conflict_count

    @staticmethod
    def _apply_relation_changes(connection: sqlite3.Connection, event: ReviewEvent) -> None:
        for change in event.changes:
            if change.path == "spell.record_status":
                connection.execute("UPDATE spells SET record_status=? WHERE id=?", (change.after, event.spell_id))
            elif change.path == "spell.merged_into_id":
                connection.execute("UPDATE spells SET merged_into_id=? WHERE id=?", (change.after, event.spell_id))
            elif change.path == "spell.variant_group_id":
                connection.execute("UPDATE spells SET variant_group_id=? WHERE id=?", (change.after, event.spell_id))
