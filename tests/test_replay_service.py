import shutil

from spellbook.repositories.event_repository import EventRepository
from spellbook.repositories.spell_repository import SpellRepository
from spellbook.services.replay_service import ReplayService
from spellbook.services.review_service import ReviewService


def repository(path):
    result = SpellRepository(path, "migrations/002_review_workflow.sql")
    result.migrate()
    return result


def test_replay_applies_a_new_event_idempotently(tmp_path):
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    shutil.copy2("data/spellbook.sqlite", source_db)
    shutil.copy2("data/spellbook.sqlite", target_db)
    source = repository(source_db)
    target = repository(target_db)
    events = EventRepository(tmp_path / "events")
    spell_id = source.list_spells(limit=1)[0]["id"]
    current = source.get_spell(spell_id)
    ReviewService(source, events).update_fields(
        spell_id=spell_id,
        changes={"entry.school": "重播測試學派"},
        editor_name="事件甲",
        base_revision_hash=current["revision_hash"],
    )
    replay = ReplayService(target, events)
    assert replay.replay() == {"applied": 1, "conflicts": 0, "skipped": 0}
    assert target.get_spell(spell_id)["school"] == "重播測試學派"
    assert replay.replay() == {"applied": 0, "conflicts": 0, "skipped": 1}


def test_replay_creates_field_conflict_instead_of_overwriting(tmp_path):
    source_db = tmp_path / "source.sqlite"
    target_db = tmp_path / "target.sqlite"
    shutil.copy2("data/spellbook.sqlite", source_db)
    shutil.copy2("data/spellbook.sqlite", target_db)
    source = repository(source_db)
    target = repository(target_db)
    events = EventRepository(tmp_path / "events")
    spell_id = source.list_spells(limit=1)[0]["id"]
    current = source.get_spell(spell_id)
    ReviewService(source, events).update_fields(
        spell_id=spell_id,
        changes={"entry.school": "甲方學派"},
        editor_name="事件甲",
        base_revision_hash=current["revision_hash"],
    )
    with target.transaction() as connection:
        connection.execute("UPDATE spell_entries SET school='乙方學派' WHERE id=?", (current["entry_id"],))
    result = ReplayService(target, events).replay()
    after = target.get_spell(spell_id)
    assert result["conflicts"] == 1
    assert after["school"] == "乙方學派"
    assert after["conflicts"][0]["field_path"] == "entry.school"
    ReviewService(target, events).resolve_conflict(
        spell_id=spell_id,
        conflict_id=after["conflicts"][0]["id"],
        resolution="incoming",
        custom_value=None,
        editor_name="整合者",
        base_revision_hash=after["revision_hash"],
    )
    resolved = target.get_spell(spell_id)
    assert resolved["school"] == "甲方學派"
    assert resolved["conflicts"] == []
