import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.spellbook_lib import ParsedSpell, build_database


class DatabaseTests(unittest.TestCase):
    def sample_entry(self):
        return ParsedSpell(
            alphabet="A",
            name_zh="底棲魔魚詛咒",
            name_en="Aboleth Curse",
            heading="底棲魔魚詛咒（Aboleth Curse）（風暴之書）",
            school="死靈系",
            levels="術士/法師 4",
            page_start=192,
            page_end=192,
            description_zh="效果正文。",
            raw_text="原始條目。",
            sources=["風暴之書"],
        )

    def test_database_constraints_and_stable_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spellbook.sqlite"
            build_database(path, [self.sample_entry()])
            with closing(sqlite3.connect(path)) as connection:
                first_id = connection.execute("SELECT id FROM spells").fetchone()[0]
                self.assertRegex(first_id, r"^spl_[0-9A-HJKMNP-TV-Z]{26}$")
                self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
                self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())

            build_database(path, [self.sample_entry()])
            with closing(sqlite3.connect(path)) as connection:
                second_id = connection.execute("SELECT id FROM spells").fetchone()[0]
                self.assertEqual(first_id, second_id)
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM spells").fetchone()[0])

    def test_duplicate_english_names_are_preserved_and_flagged(self):
        first = self.sample_entry()
        second = self.sample_entry()
        second.name_zh = "底棲魔魚之咒"
        second.page_start = 193
        second.page_end = 193
        second.raw_text = "另一個來源版本。"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spellbook.sqlite"
            build_database(path, [first, second])
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM spells").fetchone()[0])
                self.assertEqual(
                    2,
                    connection.execute(
                        "SELECT COUNT(*) FROM extraction_issues WHERE issue_type='duplicate_english_name'"
                    ).fetchone()[0],
                )
                self.assertEqual(
                    2,
                    connection.execute(
                        "SELECT COUNT(*) FROM spells WHERE review_status='needs_review'"
                    ).fetchone()[0],
                )


if __name__ == "__main__":
    unittest.main()
