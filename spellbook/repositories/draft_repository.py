from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DraftRepository:
    """Stores unfinished browser edits outside the version-controlled database."""

    def __init__(self, database: Path):
        self.database = Path(database)

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS drafts(
                spell_id TEXT PRIMARY KEY,
                revision_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        return connection

    def save(self, spell_id: str, revision_hash: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO drafts(spell_id,revision_hash,payload_json,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(spell_id) DO UPDATE SET
                     revision_hash=excluded.revision_hash,
                     payload_json=excluded.payload_json,
                     updated_at=excluded.updated_at""",
                (
                    spell_id,
                    revision_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def load(self, spell_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision_hash,payload_json,updated_at FROM drafts WHERE spell_id=?",
                (spell_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "revision_hash": row[0],
            "payload": json.loads(row[1]),
            "updated_at": row[2],
        }

    def delete(self, spell_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM drafts WHERE spell_id=?", (spell_id,))
