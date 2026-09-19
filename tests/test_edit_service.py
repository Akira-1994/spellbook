import hashlib
import sqlite3
from contextlib import closing

import pytest

from spellbook.database import prepare_database
from spellbook.repositories.spell_repository import SpellRepository
from spellbook.services.edit_service import MAX_VERSIONS, EditError, EditService, StaleRevisionError


SPELL_ID = "spl_01M2SPSHAPWQ7MDCM1S9DH8F6M"  # 底棲魔魚詛咒 / Aboleth Curse


@pytest.fixture
def service(paths):
    prepare_database(paths)
    return EditService(SpellRepository(paths.database), paths.seed_database)


def current(service):
    return service.spells.get_spell(SPELL_ID)


def edit(service, **fields):
    return service.update(SPELL_ID, fields, current(service)["revision_hash"])


def seed_digest(paths):
    return hashlib.sha256(paths.seed_database.read_bytes()).hexdigest()


def test_first_run_copies_seed_and_drops_review_tables(paths):
    before = seed_digest(paths)
    prepare_database(paths)
    prepare_database(paths)  # idempotent on later launches
    with closing(sqlite3.connect(paths.database)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert connection.execute("SELECT COUNT(*) FROM spells").fetchone()[0] == 2360
    assert "spell_versions" in tables
    assert not tables & {"review_events", "review_checks", "review_conflicts", "duplicate_decisions"}
    assert seed_digest(paths) == before


def test_edit_updates_content_search_and_records_previous_version(service):
    original = current(service)
    updated = edit(service, name_zh="魔魚之咒", duration="1 天")
    assert updated["name_zh"] == "魔魚之咒" and updated["duration"] == "1 天"
    assert updated["edited_at"] is not None
    versions = service.versions(SPELL_ID)
    assert len(versions) == 1
    assert versions[0]["content"]["name_zh"] == original["name_zh"]
    assert versions[0]["content_saved_at"] is None  # the replaced state was the original
    with closing(service.spells.connect()) as connection:
        hit = connection.execute("SELECT spell_id FROM spell_search WHERE spell_search MATCH '魔魚之咒'").fetchall()
    assert [row[0] for row in hit] == [SPELL_ID]
    assert service.spells.list_spells(edited_only=True)["items"][0]["id"] == SPELL_ID


def test_keeps_only_the_latest_versions(service):
    for index in range(MAX_VERSIONS + 3):
        edit(service, duration=f"{index} 輪")
    versions = service.versions(SPELL_ID)
    assert len(versions) == MAX_VERSIONS
    assert [v["content"]["duration"] for v in versions] == [f"{i} 輪" for i in range(MAX_VERSIONS + 1, 1, -1)]


def test_rollback_restores_version_and_can_itself_be_undone(service):
    edit(service, duration="A")
    edit(service, duration="B")
    target = next(v for v in service.versions(SPELL_ID) if v["content"]["duration"] == "A")
    rolled = service.rollback(SPELL_ID, target["id"], current(service)["revision_hash"])
    assert rolled["duration"] == "A"
    latest = service.versions(SPELL_ID)[0]
    assert latest["replaced_by"] == "rollback" and latest["content"]["duration"] == "B"
    assert service.rollback(SPELL_ID, latest["id"], rolled["revision_hash"])["duration"] == "B"


def test_returning_to_original_content_clears_edited_flag(service):
    original_duration = current(service)["duration"]
    edit(service, duration="changed")
    assert edit(service, duration=original_duration)["edited_at"] is None
    assert service.spells.summary()["edited"] == 0


def test_restore_original_uses_seed_and_clears_edited_flag(service):
    original = current(service)
    edit(service, name_en="Zzz Curse", description_zh="改寫")
    assert current(service)["alphabet"] == "Z"
    restored = service.restore_original(SPELL_ID, current(service)["revision_hash"])
    assert restored["name_en"] == original["name_en"]
    assert restored["description_zh"] == original["description_zh"]
    assert restored["alphabet"] == "A" and restored["edited_at"] is None
    assert service.versions(SPELL_ID)[0]["content"]["name_en"] == "Zzz Curse"


def test_rejects_stale_empty_unknown_and_noop_edits(service):
    stale = current(service)["revision_hash"]
    edit(service, duration="X")
    with pytest.raises(StaleRevisionError):
        service.update(SPELL_ID, {"duration": "Y"}, stale)
    with pytest.raises(EditError):
        edit(service, name_zh="  ")
    with pytest.raises(EditError):
        edit(service, review_status="reviewed")
    with pytest.raises(EditError):
        edit(service, duration="X")
    with pytest.raises(EditError):
        service.rollback(SPELL_ID, 999999, current(service)["revision_hash"])
    assert len(service.versions(SPELL_ID)) == 1
