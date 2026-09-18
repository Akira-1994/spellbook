from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from spellbook_reviewer.domain.events import CHECK_TYPES, FieldChange, ReviewEvent
from spellbook_reviewer.domain.ids import new_ulid
from spellbook_reviewer.repositories.event_repository import EventRepository
from spellbook_reviewer.repositories.spell_repository import ENTRY_FIELDS, SPELL_FIELDS, SpellRepository


class ReviewError(ValueError):
    pass


class StaleRevisionError(ReviewError):
    pass


class ReviewService:
    def __init__(self, spells: SpellRepository, events: EventRepository):
        self.spells = spells
        self.events = events

    @staticmethod
    def _assert_editor(editor_name: str) -> str:
        editor_name = editor_name.strip()
        if not editor_name:
            raise ReviewError("請先確認校對者名稱")
        return editor_name

    @staticmethod
    def _assert_revision(current: dict[str, Any], expected: str) -> None:
        if current["revision_hash"] != expected:
            raise StaleRevisionError("內容已被其他事件更新，請重新載入後比較變更")

    def _persist(
        self,
        event: ReviewEvent,
        apply: Callable[[sqlite3.Connection], None],
    ) -> ReviewEvent:
        temporary, final = self.events.prepare(event)
        finalized = False
        try:
            with self.spells.transaction() as connection:
                connection.execute(
                    """INSERT INTO review_events(
                         event_id,spell_id,spell_entry_id,action,editor_name,
                         occurred_at_utc,base_revision_hash,event_hash,payload_json,applied_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event.event_id,
                        event.spell_id,
                        event.spell_entry_id,
                        event.action,
                        event.editor_name,
                        event.occurred_at_utc,
                        event.base_revision_hash,
                        event.digest(),
                        event.to_json(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                apply(connection)
                self.events.finalize(temporary, final)
                finalized = True
            return event
        except Exception:
            if not finalized:
                temporary.unlink(missing_ok=True)
            raise

    def update_fields(
        self,
        *,
        spell_id: str,
        changes: dict[str, Any],
        editor_name: str,
        base_revision_hash: str,
        note: str = "",
    ) -> ReviewEvent:
        editor_name = self._assert_editor(editor_name)
        with self.spells.connect() as connection:
            current = self.spells.get_spell(spell_id, connection)
        self._assert_revision(current, base_revision_hash)

        normalized: dict[str, Any] = {}
        field_changes: list[FieldChange] = []
        for path, after in changes.items():
            if "." not in path:
                raise ReviewError(f"無效欄位：{path}")
            scope, field = path.split(".", 1)
            allowed = SPELL_FIELDS if scope == "spell" else ENTRY_FIELDS if scope == "entry" else ()
            if field not in allowed:
                raise ReviewError(f"不可編輯欄位：{path}")
            if isinstance(after, str):
                after = after.strip()
            if field in ("name_zh", "name_en") and not after:
                raise ReviewError("中英文名稱不可留空")
            before = current.get(field)
            if before != after:
                normalized[path] = after
                field_changes.append(FieldChange(path=path, before=before, after=after))
        if not field_changes:
            raise ReviewError("沒有可儲存的變更")

        event = ReviewEvent.create(
            editor_name=editor_name,
            spell_id=spell_id,
            spell_entry_id=current["entry_id"],
            action="update_fields",
            base_revision_hash=base_revision_hash,
            changes=field_changes,
            note=note,
        )

        def apply(connection: sqlite3.Connection) -> None:
            stamp = event.occurred_at_utc
            for path, value in normalized.items():
                scope, field = path.split(".", 1)
                if scope == "spell":
                    connection.execute(f"UPDATE spells SET {field}=?,updated_at=? WHERE id=?", (value, stamp, spell_id))
                    if field in ("name_zh", "name_en"):
                        language = "zh" if field == "name_zh" else "en"
                        connection.execute(
                            "UPDATE spell_names SET name=? WHERE spell_id=? AND language=? AND name_type='canonical'",
                            (value, spell_id, language),
                        )
                else:
                    connection.execute(
                        f"UPDATE spell_entries SET {field}=? WHERE id=?",
                        (value, current["entry_id"]),
                    )
                fts_field = {
                    "spell.name_zh": "name_zh",
                    "spell.name_en": "name_en",
                    "entry.description_zh": "description_zh",
                    "entry.description_en": "description_en",
                    "entry.school": "school",
                }.get(path)
                if fts_field:
                    connection.execute(
                        f"UPDATE spell_search SET {fts_field}=? WHERE spell_id=?",
                        (value or "", spell_id),
                    )
            connection.execute(
                "UPDATE spells SET review_status='needs_review',reviewed_revision_hash=NULL WHERE id=?",
                (spell_id,),
            )
            connection.execute(
                "UPDATE spell_entries SET review_status='needs_review' WHERE id=?",
                (current["entry_id"],),
            )
            connection.execute("DELETE FROM review_checks WHERE spell_id=?", (spell_id,))

        return self._persist(event, apply)

    def duplicate_group(self, spell_id: str) -> list[dict[str, Any]]:
        with self.spells.connect() as connection:
            current = self.spells.get_spell(spell_id, connection)
            rows = connection.execute(
                """SELECT s.id FROM spells s
                   WHERE s.record_status='active' AND s.name_en=? COLLATE NOCASE
                   ORDER BY s.id""",
                (current["name_en"],),
            ).fetchall()
            return [self.spells.get_spell(row[0], connection) for row in rows]

    def decide_duplicate(
        self,
        *,
        spell_id: str,
        decision: str,
        related_spell_id: str | None,
        editor_name: str,
        base_revision_hash: str,
        note: str = "",
    ) -> ReviewEvent:
        editor_name = self._assert_editor(editor_name)
        if decision not in {"merge", "variant", "name_error", "uncertain"}:
            raise ReviewError("未知的同名判定")
        with self.spells.connect() as connection:
            current = self.spells.get_spell(spell_id, connection)
            related = self.spells.get_spell(related_spell_id, connection) if related_spell_id else None
        self._assert_revision(current, base_revision_hash)
        if decision in {"merge", "variant"}:
            if not related or related["id"] == spell_id:
                raise ReviewError("合併或版本關係必須選擇另一筆同名法術")
            if related["name_en"].casefold() != current["name_en"].casefold():
                raise ReviewError("只能處理英文名稱相同的條目")

        changes: list[FieldChange] = []
        action = "resolve_duplicate"
        group_id: str | None = None
        if decision == "merge":
            action = "merge_spell"
            changes.extend([
                FieldChange("spell.record_status", current.get("record_status", "active"), "merged"),
                FieldChange("spell.merged_into_id", current.get("merged_into_id"), related_spell_id),
            ])
        elif decision == "variant":
            action = "set_variant_group"
            group_id = current.get("variant_group_id") or related.get("variant_group_id") or ("var_" + new_ulid())
            changes.append(FieldChange("spell.variant_group_id", current.get("variant_group_id"), group_id))
        event = ReviewEvent.create(
            editor_name=editor_name,
            spell_id=spell_id,
            spell_entry_id=current["entry_id"],
            action=action,
            base_revision_hash=base_revision_hash,
            changes=changes,
            note=note,
            metadata={"decision": decision, "related_spell_id": related_spell_id},
        )

        def apply(connection: sqlite3.Connection) -> None:
            if decision == "merge":
                connection.execute(
                    "UPDATE spells SET record_status='merged',merged_into_id=?,updated_at=? WHERE id=?",
                    (related_spell_id, event.occurred_at_utc, spell_id),
                )
            elif decision == "variant":
                connection.execute(
                    "UPDATE spells SET variant_group_id=?,review_status='needs_review',reviewed_revision_hash=NULL,updated_at=? WHERE id IN (?,?)",
                    (group_id, event.occurred_at_utc, spell_id, related_spell_id),
                )
                connection.execute("DELETE FROM review_checks WHERE spell_id IN (?,?)", (spell_id, related_spell_id))
            connection.execute(
                """INSERT INTO duplicate_decisions(spell_id,decision,related_spell_id,editor_name,decided_at,event_id,note)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(spell_id) DO UPDATE SET decision=excluded.decision,
                     related_spell_id=excluded.related_spell_id,editor_name=excluded.editor_name,
                     decided_at=excluded.decided_at,event_id=excluded.event_id,note=excluded.note""",
                (spell_id, decision, related_spell_id, editor_name, event.occurred_at_utc, event.event_id, note),
            )

        return self._persist(event, apply)

    def set_check(
        self,
        *,
        spell_id: str,
        check_type: str,
        checked: bool,
        editor_name: str,
        base_revision_hash: str,
    ) -> ReviewEvent:
        editor_name = self._assert_editor(editor_name)
        if check_type not in CHECK_TYPES:
            raise ReviewError("未知的核對類型")
        with self.spells.connect() as connection:
            current = self.spells.get_spell(spell_id, connection)
        self._assert_revision(current, base_revision_hash)
        prior = {item["check_type"] for item in current["checks"] if item["revision_hash"] == base_revision_hash}
        completed = (prior | {check_type}) if checked else (prior - {check_type})
        if (check_type in prior) == checked:
            raise ReviewError("核對狀態沒有變更")
        event = ReviewEvent.create(
            editor_name=editor_name,
            spell_id=spell_id,
            spell_entry_id=current["entry_id"],
            action="set_check",
            base_revision_hash=base_revision_hash,
            changes=(FieldChange(path=f"check.{check_type}", before=not checked, after=checked),),
            completed_checks=completed,
        )

        def apply(connection: sqlite3.Connection) -> None:
            if checked:
                connection.execute(
                    """INSERT INTO review_checks(spell_id,check_type,revision_hash,editor_name,checked_at,event_id)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(spell_id,check_type) DO UPDATE SET
                         revision_hash=excluded.revision_hash,editor_name=excluded.editor_name,
                         checked_at=excluded.checked_at,event_id=excluded.event_id""",
                    (spell_id, check_type, base_revision_hash, editor_name, event.occurred_at_utc, event.event_id),
                )
            else:
                connection.execute(
                    "DELETE FROM review_checks WHERE spell_id=? AND check_type=?",
                    (spell_id, check_type),
                )

        return self._persist(event, apply)

    def approve(
        self,
        *,
        spell_id: str,
        editor_name: str,
        base_revision_hash: str,
        note: str = "",
    ) -> ReviewEvent:
        editor_name = self._assert_editor(editor_name)
        with self.spells.connect() as connection:
            current = self.spells.get_spell(spell_id, connection)
        self._assert_revision(current, base_revision_hash)
        completed = {
            item["check_type"] for item in current["checks"]
            if item["revision_hash"] == base_revision_hash
        }
        missing = set(CHECK_TYPES) - completed
        if missing:
            raise ReviewError("尚未完成核對：" + "、".join(sorted(missing)))
        event = ReviewEvent.create(
            editor_name=editor_name,
            spell_id=spell_id,
            spell_entry_id=current["entry_id"],
            action="approve_review",
            base_revision_hash=base_revision_hash,
            completed_checks=completed,
            note=note,
        )

        def apply(connection: sqlite3.Connection) -> None:
            connection.execute(
                """UPDATE spells SET review_status='reviewed',reviewed_revision_hash=?,updated_at=?
                   WHERE id=?""",
                (base_revision_hash, event.occurred_at_utc, spell_id),
            )
            connection.execute(
                """UPDATE spell_entries SET review_status='reviewed',
                     translation_status=CASE WHEN translation_status='generated' THEN 'reviewed' ELSE translation_status END
                   WHERE id=?""",
                (current["entry_id"],),
            )

        return self._persist(event, apply)

    def resolve_conflict(
        self,
        *,
        spell_id: str,
        conflict_id: str,
        resolution: str,
        custom_value: Any,
        editor_name: str,
        base_revision_hash: str,
        note: str = "",
    ) -> ReviewEvent:
        editor_name = self._assert_editor(editor_name)
        with self.spells.connect() as connection:
            current = self.spells.get_spell(spell_id, connection)
            conflict = connection.execute(
                "SELECT * FROM review_conflicts WHERE id=? AND spell_id=? AND status='open'",
                (conflict_id, spell_id),
            ).fetchone()
        self._assert_revision(current, base_revision_hash)
        if conflict is None:
            raise ReviewError("找不到待處理的衝突")
        conflict = dict(conflict)
        candidates = {
            "current": json.loads(conflict["value_a_json"]),
            "incoming": json.loads(conflict["value_b_json"]),
            "custom": custom_value,
        }
        if resolution not in candidates:
            raise ReviewError("未知的衝突解法")
        value = candidates[resolution]
        path = conflict["field_path"]
        scope, field = path.split(".", 1)
        allowed = SPELL_FIELDS if scope == "spell" else ENTRY_FIELDS if scope == "entry" else ()
        if field not in allowed:
            raise ReviewError("衝突欄位不可編輯")
        event = ReviewEvent.create(
            editor_name=editor_name,
            spell_id=spell_id,
            spell_entry_id=current["entry_id"],
            action="resolve_conflict",
            base_revision_hash=base_revision_hash,
            changes=(FieldChange(path, current.get(field), value),),
            note=note,
            metadata={"conflict_id": conflict_id, "resolution": resolution},
        )

        def apply(connection: sqlite3.Connection) -> None:
            table = "spells" if scope == "spell" else "spell_entries"
            key = spell_id if scope == "spell" else current["entry_id"]
            connection.execute(f"UPDATE {table} SET {field}=? WHERE id=?", (value, key))
            connection.execute(
                "UPDATE review_conflicts SET status='resolved',resolution_event_id=?,resolved_at=? WHERE id=?",
                (event.event_id, event.occurred_at_utc, conflict_id),
            )
            connection.execute(
                "UPDATE spells SET review_status='needs_review',reviewed_revision_hash=NULL,updated_at=? WHERE id=?",
                (event.occurred_at_utc, spell_id),
            )
            connection.execute("UPDATE spell_entries SET review_status='needs_review' WHERE id=?", (current["entry_id"],))
            connection.execute("DELETE FROM review_checks WHERE spell_id=?", (spell_id,))

        return self._persist(event, apply)
