import sqlite3
from contextlib import closing

import pytest

from spellbook import taxonomy
from spellbook.database import prepare_database
from spellbook.repositories.spell_repository import SpellRepository
from spellbook.services.edit_service import EditService


@pytest.fixture
def repo(paths):
    prepare_database(paths)
    return SpellRepository(paths.database)


def names(result):
    return {item["name_en"] for item in result["items"]}


def class_id(repo, name):
    return next(c["id"] for c in repo.taxonomy()["classes"] if c["name"] == name)


def test_every_raw_class_spelling_is_mapped(paths):
    data = taxonomy.data()
    catalog = {c["name"] for c in data["classes"]}
    with closing(sqlite3.connect(paths.seed_database)) as seed:
        raw = {row[0] for row in seed.execute("SELECT name FROM classes")}
    fixed_spellings = raw - set(data["class_aliases"]) - set(data["dropped_class_aliases"])
    assert fixed_spellings and len(fixed_spellings) < 20  # only the merged "等級修正" spellings
    assert all(name in catalog for names in data["class_aliases"].values() for name in names)
    assert all(name in catalog for levels in data["level_fixes"].values() for name, _ in levels)


def test_every_spell_gets_a_school_and_known_mistranslations_are_fixed(repo):
    assert "unclassified" not in {s["key"] for s in repo.taxonomy()["schools"]}
    assert taxonomy.school_keys("x", "招魂") == ["evocation"]
    assert taxonomy.school_keys("x", "咒法系/死靈系") == ["conjuration", "necromancy"]
    assert taxonomy.school_keys("x", "隨便寫") == [taxonomy.UNCLASSIFIED]


def test_level_fixes_replace_merged_rows(repo):
    spell = repo.list_spells(query="偵測探知")["items"][0]
    levels = {(l["class_name"], l["spell_level"]) for l in repo.get_spell(spell["id"])["levels"]}
    assert levels == {("吟遊詩人", 4), ("術士", 4), ("法師", 4)}


def test_school_filter_matches_any_selected_school(repo):
    evocation = repo.list_spells(schools=["evocation"], limit=200)
    both = repo.list_spells(schools=["evocation", "illusion"], limit=200)
    assert evocation["total"] == repo.taxonomy()["schools"][4]["count"]
    assert both["total"] > evocation["total"]
    assert all("evocation" in item["schools"] for item in evocation["items"])
    assert "Fireball" in names(repo.list_spells(schools=["evocation"], query="Fireball"))


def test_class_and_level_must_match_the_same_row(repo):
    wizard = class_id(repo, "法師")
    wizard_three = repo.list_spells(class_id=wizard, levels=[3], limit=200)
    assert "Fireball" in names(repo.list_spells(class_id=wizard, levels=[3], query="Fireball"))
    assert all(item["class_level"] == 3 for item in wizard_three["items"])
    # Cure Light Wounds is a cleric 1 spell but not a wizard spell at any level.
    assert not repo.list_spells(class_id=wizard, levels=[1], query="Cure Light Wounds")["items"]
    any_level_one = repo.list_spells(levels=[1], query="治療輕傷")
    assert any_level_one["total"] >= 1
    multi = repo.list_spells(class_id=wizard, levels=[1, 2], limit=1)["total"]
    assert multi == repo.list_spells(class_id=wizard, levels=[1], limit=1)["total"] + repo.list_spells(
        class_id=wizard, levels=[2], limit=1
    )["total"] - _both_levels(repo, wizard, 1, 2)


def _both_levels(repo, wizard, a, b):
    with closing(repo.connect()) as connection:
        return connection.execute(
            """SELECT COUNT(*) FROM spell_entries e WHERE
               EXISTS(SELECT 1 FROM spell_class_levels WHERE spell_entry_id=e.id AND class_id=? AND level=?) AND
               EXISTS(SELECT 1 FROM spell_class_levels WHERE spell_entry_id=e.id AND class_id=? AND level=?)""",
            (wizard, a, wizard, b),
        ).fetchone()[0]


def test_editing_school_text_recomputes_tags(repo, paths):
    spell = next(i for i in repo.list_spells(query="Fireball", schools=["evocation"])["items"] if i["name_en"] == "Fireball")
    current = repo.get_spell(spell["id"])
    edited = EditService(repo, paths.seed_database).update(spell["id"], {"school": "幻術系"}, current["revision_hash"])
    assert edited["schools"] == ["illusion"]
    assert spell["id"] not in {item["id"] for item in repo.list_spells(query="Fireball", schools=["evocation"])["items"]}


def test_upgrading_a_v2_database_builds_taxonomy(paths):
    prepare_database(paths)
    with closing(sqlite3.connect(paths.database)) as connection, connection:
        for table in ("spell_class_levels", "spell_schools", "class_catalog"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migrations WHERE version=4")
    prepare_database(paths)
    with closing(sqlite3.connect(paths.database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM spell_schools").fetchone()[0] >= 2360
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 4
