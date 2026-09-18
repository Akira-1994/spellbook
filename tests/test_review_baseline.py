import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.create_review_baseline import build_baseline, stable_json


class ReviewBaselineTests(unittest.TestCase):
    def test_rejects_incomplete_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "spells.json"
            pdf = root / "source.pdf"
            export.write_text(json.dumps({"spell_count": 1, "spells": []}), encoding="utf-8")
            pdf.write_bytes(b"pdf")
            with self.assertRaises(ValueError):
                build_baseline(export, pdf)

    def test_stable_json_uses_sorted_keys(self):
        first = stable_json({"z": 1, "a": {"d": 2, "b": 1}})
        second = stable_json({"a": {"b": 1, "d": 2}, "z": 1})
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(first.encode()).hexdigest(), hashlib.sha256(second.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
