import shutil

import pytest

from spellbook.domain.events import CHECK_TYPES
from spellbook.repositories.event_repository import EventRepository
from spellbook.repositories.spell_repository import SpellRepository
from spellbook.services.review_service import ReviewError, ReviewService, StaleRevisionError


@pytest.fixture()
def review_workspace(tmp_path):
    database = tmp_path / "spellbook.sqlite"
    shutil.copy2("data/spellbook.sqlite", database)
    spells = SpellRepository(database, "migrations/002_review_workflow.sql")
    spells.migrate()
    service = ReviewService(spells, EventRepository(tmp_path / "events"))
    spell_id = spells.list_spells(limit=1)[0]["id"]
    return spells, service, spell_id, tmp_path / "events"


def test_update_fields_creates_event_and_invalidates_review(review_workspace):
    spells, service, spell_id, events = review_workspace
    before = spells.get_spell(spell_id)
    service.update_fields(
        spell_id=spell_id,
        changes={"spell.name_zh": before["name_zh"] + "（校訂）"},
        editor_name="測試校對者",
        base_revision_hash=before["revision_hash"],
        note="核對 PDF",
    )
    after = spells.get_spell(spell_id)
    assert after["name_zh"].endswith("（校訂）")
    assert after["review_status"] == "needs_review"
    assert after["revision_hash"] != before["revision_hash"]
    assert len(list(events.glob("rev_*.json"))) == 1
    assert after["history"][0]["editor_name"] == "測試校對者"


def test_stale_revision_is_rejected(review_workspace):
    spells, service, spell_id, _ = review_workspace
    current = spells.get_spell(spell_id)
    with pytest.raises(StaleRevisionError):
        service.update_fields(
            spell_id=spell_id,
            changes={"entry.school": "幻術系"},
            editor_name="測試校對者",
            base_revision_hash="0" * 64,
        )


def test_all_checks_are_required_before_approval(review_workspace):
    spells, service, spell_id, _ = review_workspace
    revision = spells.get_spell(spell_id)["revision_hash"]
    for check_type in CHECK_TYPES[:-1]:
        service.set_check(
            spell_id=spell_id,
            check_type=check_type,
            checked=True,
            editor_name="測試校對者",
            base_revision_hash=revision,
        )
    with pytest.raises(ReviewError):
        service.approve(
            spell_id=spell_id,
            editor_name="測試校對者",
            base_revision_hash=revision,
        )
    service.set_check(
        spell_id=spell_id,
        check_type=CHECK_TYPES[-1],
        checked=True,
        editor_name="測試校對者",
        base_revision_hash=revision,
    )
    service.approve(
        spell_id=spell_id,
        editor_name="測試校對者",
        base_revision_hash=revision,
    )
    after = spells.get_spell(spell_id)
    assert after["review_status"] == "reviewed"
    assert after["reviewed_revision_hash"] == revision


def test_duplicate_entries_can_be_marked_as_variants(review_workspace):
    spells, service, _, _ = review_workspace
    duplicate = spells.list_spells(issue="duplicate", limit=1)[0]
    group = service.duplicate_group(duplicate["id"])
    assert len(group) >= 2
    current = next(item for item in group if item["id"] == duplicate["id"])
    related = next(item for item in group if item["id"] != duplicate["id"])
    service.decide_duplicate(
        spell_id=current["id"],
        decision="variant",
        related_spell_id=related["id"],
        editor_name="測試校對者",
        base_revision_hash=current["revision_hash"],
        note="規則欄位不同",
    )
    first = spells.get_spell(current["id"])
    second = spells.get_spell(related["id"])
    assert first["variant_group_id"]
    assert first["variant_group_id"] == second["variant_group_id"]
