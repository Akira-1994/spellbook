import unittest

from scripts.spellbook_lib import parse_pages


class ParserTests(unittest.TestCase):
    def test_parses_chinese_entries_and_page_boundaries(self):
        pages = [
            (
                192,
                """A：
底棲魔魚詛咒（Aboleth Curse）（風暴之書）
死靈系
等級：術士/法師 4
法術成分：V, S, M
施法時間：標準動作
距離：接觸
目標：被接觸的活物
持續時間：永久
豁免檢定：強韌通過無效
法術抗力：可
第一段效果。
武器吸收（Absorb Weapon）（萬法大全）（完美冒險）
變化系
等級：刺客 2
法術成分：V、S
施法時間：標準動作
距離：接觸
持續時間：一小時/每等級
法術抗力：可
第二段效果。""",
            ),
            (193, "第二段跨頁效果。"),
        ]

        entries = parse_pages(pages)

        self.assertEqual(2, len(entries))
        self.assertEqual("底棲魔魚詛咒", entries[0].name_zh)
        self.assertEqual("Aboleth Curse", entries[0].name_en)
        self.assertEqual(["風暴之書"], entries[0].sources)
        self.assertEqual(192, entries[0].page_start)
        self.assertEqual(192, entries[0].page_end)
        self.assertEqual(193, entries[1].page_end)
        self.assertIn("跨頁效果", entries[1].description_zh)

    def test_parses_english_only_entry(self):
        pages = [
            (
                922,
                """S：
SHADOW MASK（萬法大全）
Illusion（Shadow）
Level: Sorcerer/wizard 2
Components: V, S, M
Casting Time: 1 standard action
Range: Personal
Target: You
Duration: 10 minutes/level
Saving Throw: None
Spell Resistance: No
You draw raw energy from the Plane of Shadow.
Material Component: A mask of black cloth.""",
            )
        ]

        entries = parse_pages(pages)

        self.assertEqual(1, len(entries))
        self.assertEqual("SHADOW MASK", entries[0].name_en)
        self.assertEqual("", entries[0].name_zh)
        self.assertTrue(entries[0].english_only)
        self.assertEqual("Illusion", entries[0].school)
        self.assertEqual("Shadow", entries[0].subschool)
        self.assertIn("Plane of Shadow", entries[0].description_en)


if __name__ == "__main__":
    unittest.main()
