CREATE TABLE IF NOT EXISTS review_events(
    event_id TEXT PRIMARY KEY CHECK(event_id GLOB 'rev_*'),
    spell_id TEXT NOT NULL REFERENCES spells(id),
    spell_entry_id TEXT REFERENCES spell_entries(id),
    action TEXT NOT NULL,
    editor_name TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    base_revision_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_checks(
    spell_id TEXT NOT NULL REFERENCES spells(id),
    check_type TEXT NOT NULL CHECK(check_type IN ('names','rules','descriptions','source')),
    revision_hash TEXT NOT NULL,
    editor_name TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES review_events(event_id),
    PRIMARY KEY(spell_id, check_type)
);

CREATE TABLE IF NOT EXISTS review_conflicts(
    id TEXT PRIMARY KEY CHECK(id GLOB 'cnf_*'),
    spell_id TEXT NOT NULL REFERENCES spells(id),
    spell_entry_id TEXT REFERENCES spell_entries(id),
    field_path TEXT NOT NULL,
    base_value_json TEXT,
    event_a_id TEXT NOT NULL,
    event_b_id TEXT NOT NULL,
    value_a_json TEXT,
    value_b_json TEXT,
    status TEXT NOT NULL CHECK(status IN ('open','resolved')) DEFAULT 'open',
    resolution_event_id TEXT REFERENCES review_events(event_id),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS duplicate_decisions(
    spell_id TEXT PRIMARY KEY REFERENCES spells(id),
    decision TEXT NOT NULL CHECK(decision IN ('merge','variant','name_error','uncertain')),
    related_spell_id TEXT REFERENCES spells(id),
    editor_name TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES review_events(event_id),
    note TEXT NOT NULL DEFAULT ''
);
