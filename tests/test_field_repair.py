import sqlite3
from contextlib import closing

from scripts.repair_fields import SEED, compare
from scripts.spellbook_lib import parse_fields
from spellbook import seed_fixes
from spellbook.database import prepare_database


def parse(text):
    fields, description = parse_fields(text.strip("\n").split("\n"))
    return fields, [line for line in description if line.strip()]


def test_semicolon_separator_and_alternative_labels():
    fields, description = parse("""
等級：心靈 6
作用距離：個人
施展時間：一個標準動作
法術抗力；可
你可以瞭解到目標所有的記憶。
""")
    assert fields["range_text"] == "個人" and fields["casting_time"] == "一個標準動作"
    assert fields["spell_resistance"] == "可"
    assert description == ["你可以瞭解到目標所有的記憶。"]


def test_labels_split_across_lines_are_rejoined():
    fields, description = parse("""
等級：牧師 8 環，腐敗 8 環法
術成分：語言，姿勢，材料施
法時間：1 分鐘
目標：具有骨骼的生物持
續時間：專注豁
免檢定：強韌過則無效
法術抗力：可
施法者令水腐化。
""")
    assert fields["levels"] == "牧師 8 環，腐敗 8 環"
    assert fields["components"] == "語言，姿勢，材料"
    assert fields["casting_time"] == "1 分鐘"
    assert fields["target_text"] == "具有骨骼的生物"
    assert fields["duration"] == "專注"
    assert fields["saving_throw"] == "強韌過則無效"
    assert description == ["施法者令水腐化。"]


def test_range_written_as_fan_wei():
    fields, _ = parse("等級：牧師 1\n範圍：接觸\n區域：100 尺\n法術抗力：否\n正文。")
    assert fields["range_text"] == "接觸" and fields["area_text"] == "100 尺"
    fields, _ = parse("等級：牧師 1\n範圍：60 尺\n區域：扇形\n法術抗力：否\n正文。")
    assert fields["range_text"] == "60 尺" and fields["area_text"] == "扇形"


def test_table_between_fields_moves_to_description():
    fields, description = parse("""
等級：術士/法師 4
距離：中距
1d8 顏色 傷害類型
1 紅色 火焰
2 橙色 酸液
持續時間：專注
法術抗力：可
一道彩光。
""")
    assert fields["duration"] == "專注" and fields["spell_resistance"] == "可"
    assert description == ["1d8 顏色 傷害類型", "1 紅色 火焰", "2 橙色 酸液", "一道彩光。"]


def test_does_not_take_fields_from_prose_or_the_next_spell():
    fields, description = parse("""
等級：巡林客 1
持續時間：24 小時
白色的薄霧覆蓋在你的雙眼之前。

吸血之星（Bloodstar）（死者之書）
咒法（創造）
效果：一顆吸血之星
持續時間：1 輪每等級
""")
    assert "effect_text" not in fields and fields["duration"] == "24 小時"
    assert description[0] == "白色的薄霧覆蓋在你的雙眼之前。"
    assert "效果：一顆吸血之星" in description


def test_seed_is_fully_repaired():
    with closing(sqlite3.connect(f"file:{SEED.as_posix()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        assert compare(connection) == []


def test_existing_user_databases_get_fixes_but_keep_user_edits(paths):
    prepare_database(paths)
    entries = seed_fixes.data()["sets"][0]["entries"]
    fixed_id, fixed = next((e, d) for e, d in entries.items() if "spell_resistance" in d and "duration" in d)
    with closing(sqlite3.connect(paths.database)) as connection, connection:
        # Simulate a database copied from the old seed, where the user edited one field.
        connection.execute(
            "UPDATE spell_entries SET spell_resistance=?, duration=? WHERE id=?",
            (fixed["spell_resistance"][0], "使用者自己寫的", fixed_id),
        )
    prepare_database(paths)
    with closing(sqlite3.connect(paths.database)) as connection:
        resistance, duration = connection.execute(
            "SELECT spell_resistance, duration FROM spell_entries WHERE id=?", (fixed_id,)
        ).fetchone()
    assert resistance == fixed["spell_resistance"][1]
    assert duration == "使用者自己寫的"


def test_previously_missed_spells_are_present_with_levels_and_schools(paths):
    from spellbook.repositories.spell_repository import SpellRepository

    prepare_database(paths)
    repo = SpellRepository(paths.database)
    by_name = {}
    for name in ("Acid Splash", "Heart Of Earth", "Death Ward, Mass", "Fire Stride", "Shadow Evocation"):
        item = next(i for i in repo.list_spells(query=name, limit=50)["items"] if i["name_en"] == name)
        by_name[name] = repo.get_spell(item["id"])
    assert by_name["Acid Splash"]["name_zh"] == "酸液飛濺" and by_name["Acid Splash"]["schools"] == ["conjuration"]
    levels = {(l["class_name"], l["spell_level"]) for l in by_name["Heart Of Earth"]["levels"]}
    assert {("德魯伊", 4), ("術士", 4), ("法師", 4), ("巫覡", 4)} <= levels
    assert by_name["Death Ward, Mass"]["target_text"]
    assert by_name["Fire Stride"]["name_zh"] == "火焰步履" and by_name["Fire Stride"]["schools"] == ["transmutation"]
    assert by_name["Shadow Evocation"]["name_zh"] == "幽影塑能術"
    host = next(i for i in repo.list_spells(query="Acid Sheath")["items"] if i["name_en"] == "Acid Sheath")
    assert "酸液飛濺" not in repo.get_spell(host["id"])["description_zh"]


def test_user_database_from_old_seed_gains_missed_spells_and_renames(paths):
    prepare_database(paths)
    fix_set = next(s for s in seed_fixes.data()["sets"] if s.get("added_spells"))
    added, renamed_id = fix_set["added_spells"], next(iter(fix_set["spells"]))
    old_name = fix_set["spells"][renamed_id]["name_zh"][0]
    with closing(sqlite3.connect(paths.database)) as connection, connection:
        # Recreate the old state: missed spells absent, misnamed spell still misnamed.
        connection.execute("PRAGMA foreign_keys = ON")
        marks = ",".join("?" * len(added))
        connection.execute(f"DELETE FROM spell_search WHERE spell_id IN ({marks})", added)
        connection.execute(f"DELETE FROM spells WHERE id IN ({marks})", added)
        connection.execute("UPDATE spells SET name_zh=? WHERE id=?", (old_name, renamed_id))
    prepare_database(paths)
    with closing(sqlite3.connect(paths.database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM spells").fetchone()[0] == 2373
        assert connection.execute("SELECT COUNT(*) FROM spell_search").fetchone()[0] == 2373
        restored = connection.execute(f"SELECT COUNT(*) FROM spell_levels l JOIN spell_entries e ON e.id=l.spell_entry_id WHERE e.spell_id IN ({marks})", added).fetchone()[0]
        assert restored >= len(added)
        assert connection.execute("SELECT name_zh FROM spells WHERE id=?", (renamed_id,)).fetchone()[0] == fix_set["spells"][renamed_id]["name_zh"][1]
    prepare_database(paths)  # idempotent


def test_extraction_finds_spells_without_level_labels_and_school_lines_with_english():
    from scripts.spellbook_lib import parse_pages

    page = """
防死結界（Death Ward）
死靈系
等級：牧師 4
距離：接觸
目標：被接觸的生物
持續時間：1 分鐘/每等級
你用手接觸目標。
群體防死結界（Death Ward，Mass）（死者之書）
死靈系
牧師 8，德魯伊 9
作用距離：近距（25 尺+5 尺/2 等級）
作用目標：1 個生物每等級
除以上外，均與防死結界相同。
幽影塑能術（Shadow Evocation）
幻術系（幽影幻術） （Shadow）
等級：吟游詩人 5，術士/法師 5
距離：見說明
你從幽影位面吸取能量。
"""
    entries = parse_pages([(400, page)])
    assert [e.name_en for e in entries] == ["Death Ward", "Death Ward，Mass", "Shadow Evocation"]
    ward, mass, shadow = entries
    assert "群體" not in ward.description_zh
    assert mass.levels == "牧師 8，德魯伊 9" and mass.target_text == "1 個生物每等級"
    assert shadow.name_zh == "幽影塑能術" and shadow.levels == "吟游詩人 5，術士/法師 5"
