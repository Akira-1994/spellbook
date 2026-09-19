from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from spellbook.domain.ids import new_ulid


EVENT_SCHEMA_VERSION = 1
CHECK_TYPES = ("names", "rules", "descriptions", "source")
EVENT_ACTIONS = {
    "update_fields",
    "set_check",
    "approve_review",
    "resolve_conflict",
    "set_variant_group",
    "merge_spell",
    "resolve_duplicate",
    "revert_event",
}


@dataclass(frozen=True)
class FieldChange:
    path: str
    before: Any
    after: Any


@dataclass(frozen=True)
class ReviewEvent:
    event_id: str
    schema_version: int
    editor_name: str
    occurred_at_utc: str
    spell_id: str
    spell_entry_id: str | None
    action: str
    base_revision_hash: str
    changes: tuple[FieldChange, ...] = field(default_factory=tuple)
    completed_checks: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        editor_name: str,
        spell_id: str,
        spell_entry_id: str | None,
        action: str,
        base_revision_hash: str,
        changes: Iterable[FieldChange] = (),
        completed_checks: Iterable[str] = (),
        note: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "ReviewEvent":
        event = cls(
            event_id="rev_" + new_ulid(),
            schema_version=EVENT_SCHEMA_VERSION,
            editor_name=editor_name.strip(),
            occurred_at_utc=datetime.now(timezone.utc).isoformat(),
            spell_id=spell_id,
            spell_entry_id=spell_entry_id,
            action=action,
            base_revision_hash=base_revision_hash,
            changes=tuple(changes),
            completed_checks=tuple(sorted(set(completed_checks))),
            note=note.strip(),
            metadata=dict(metadata or {}),
        )
        event.validate()
        return event

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewEvent":
        event = cls(
            event_id=value["event_id"],
            schema_version=value["schema_version"],
            editor_name=value["editor_name"],
            occurred_at_utc=value["occurred_at_utc"],
            spell_id=value["spell_id"],
            spell_entry_id=value.get("spell_entry_id"),
            action=value["action"],
            base_revision_hash=value["base_revision_hash"],
            changes=tuple(FieldChange(**change) for change in value.get("changes", [])),
            completed_checks=tuple(value.get("completed_checks", [])),
            note=value.get("note", ""),
            metadata=dict(value.get("metadata", {})),
        )
        event.validate()
        return event

    @classmethod
    def read(cls, path: Path) -> "ReviewEvent":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def validate(self) -> None:
        if not self.event_id.startswith("rev_") or len(self.event_id) != 30:
            raise ValueError("Invalid review event ID")
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError("Unsupported review event schema version")
        if not self.editor_name:
            raise ValueError("Editor name is required")
        if not self.spell_id.startswith("spl_"):
            raise ValueError("Invalid spell ID")
        if self.spell_entry_id and not self.spell_entry_id.startswith("ent_"):
            raise ValueError("Invalid spell entry ID")
        if self.action not in EVENT_ACTIONS:
            raise ValueError(f"Unsupported review action: {self.action}")
        if len(self.base_revision_hash) != 64:
            raise ValueError("Invalid base revision hash")
        invalid_checks = set(self.completed_checks) - set(CHECK_TYPES)
        if invalid_checks:
            raise ValueError("Invalid review check types: " + ", ".join(sorted(invalid_checks)))
        if len({change.path for change in self.changes}) != len(self.changes):
            raise ValueError("Duplicate field paths in review event")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()
