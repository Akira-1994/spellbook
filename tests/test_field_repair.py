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
    entries = seed_fixes.data()["entries"]
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
