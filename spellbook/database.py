from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from spellbook import seed_fixes, taxonomy
from spellbook.config import AppPaths


SCHEMA_VERSION = 4

# Tables left over from the retired review workflow. They were never populated
# in shipped data, so dropping them loses nothing.
LEGACY_TABLES = ("review_checks", "review_conflicts", "duplicate_decisions", "review_events")

MIGRATION = """
CREATE TABLE IF NOT EXISTS spell_versions(
    id INTEGER PRIMARY KEY,
    spell_id TEXT NOT NULL REFERENCES spells(id) ON DELETE CASCADE,
    snapshot_json TEXT NOT NULL,
    content_saved_at TEXT,
    replaced_at TEXT NOT NULL,
    replaced_by TEXT NOT NULL CHECK(replaced_by IN ('edit','rollback','restore_original'))
);
CREATE INDEX IF NOT EXISTS idx_spell_versions_spell ON spell_versions(spell_id, id);
"""


def connect(path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=15)
    else:
        connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def prepare_database(paths: AppPaths) -> None:
    """Create the user's database from the seed on first run, then migrate it."""
    if not paths.seed_database.is_file():
        raise RuntimeError(f"找不到內建法術資料：{paths.seed_database}")
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    if not paths.database.exists():
        _copy_seed(paths)
    migrate(paths.database, paths.seed_database)


def _copy_seed(paths: AppPaths) -> None:
    # Copy through the backup API into a temp file and rename it into place, so
    # an interrupted first run never leaves a half-written database behind.
    temporary = paths.database.with_suffix(".sqlite.tmp")
    temporary.unlink(missing_ok=True)
    with closing(connect(paths.seed_database, readonly=True)) as source, closing(sqlite3.connect(temporary)) as target:
        source.backup(target)
    os.replace(temporary, paths.database)


def migrate(database, seed_database=None) -> None:
    with closing(connect(database)) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(spells)")}
        if "edited_at" not in columns:
            connection.execute("ALTER TABLE spells ADD COLUMN edited_at TEXT")
        for table in LEGACY_TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.executescript(MIGRATION)
        seed_fixes.apply(connection, seed_database)
        taxonomy.refresh(connection)
        applied = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)",
            [(version, applied) for version in range(3, SCHEMA_VERSION + 1)],
        )
