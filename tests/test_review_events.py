import json
import tempfile
import unittest
from pathlib import Path

from spellbook_reviewer.domain.events import FieldChange, ReviewEvent


class ReviewEventTests(unittest.TestCase):
    def test_stable_round_trip_and_digest(self):
        event = ReviewEvent.create(
            editor_name="Patrick Jin",
            spell_id="spl_01M2SPSHAPWQ7MDCM1S9DH8F6M",
            spell_entry_id="ent_01M2SPSHAPQD7CAHAD5BXFSY9K",
            action="update_fields",
            base_revision_hash="a" * 64,
            changes=[FieldChange("spell.name_zh", "舊名", "新名")],
            note="核對 PDF。",
        )
        loaded = ReviewEvent.from_dict(json.loads(event.to_json()))
        self.assertEqual(event, loaded)
        self.assertEqual(event.digest(), loaded.digest())
        self.assertTrue(event.event_id.startswith("rev_"))

    def test_rejects_unknown_check(self):
        with self.assertRaises(ValueError):
            ReviewEvent.create(
                editor_name="Reviewer",
                spell_id="spl_01M2SPSHAPWQ7MDCM1S9DH8F6M",
                spell_entry_id=None,
                action="set_check",
                base_revision_hash="b" * 64,
                completed_checks=["unknown"],
            )

    def test_can_be_written_as_utf8(self):
        event = ReviewEvent.create(
            editor_name="王小明",
            spell_id="spl_01M2SPSHAPWQ7MDCM1S9DH8F6M",
            spell_entry_id=None,
            action="approve_review",
            base_revision_hash="c" * 64,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.json"
            path.write_text(event.to_json(), encoding="utf-8")
            self.assertEqual(event, ReviewEvent.read(path))


if __name__ == "__main__":
    unittest.main()
