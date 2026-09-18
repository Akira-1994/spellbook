from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.spellbook_lib import load_entries, save_entries


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply reviewed or generated Chinese translations to parsed entries.")
    parser.add_argument("--input", type=Path, default=ROOT / "tmp" / "pdfs" / "az-parsed.json")
    parser.add_argument("--catalog", type=Path, default=ROOT / "data" / "translations.generated.json")
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "pdfs" / "az-translated.json")
    parser.add_argument("--template", type=Path, default=ROOT / "reports" / "translation-source.json")
    args = parser.parse_args()

    entries = load_entries(args.input)
    candidates = [entry for entry in entries if entry.needs_translation]
    args.template.parent.mkdir(parents=True, exist_ok=True)
    args.template.write_text(
        json.dumps(
            [
                {
                    "name_en": entry.name_en,
                    "existing_name_zh": entry.name_zh,
                    "page_start": entry.page_start,
                    "school": entry.school,
                    "subschool": entry.subschool,
                    "levels": entry.levels,
                    "components": entry.components,
                    "casting_time": entry.casting_time,
                    "range_text": entry.range_text,
                    "target_text": entry.target_text,
                    "area_text": entry.area_text,
                    "effect_text": entry.effect_text,
                    "duration": entry.duration,
                    "saving_throw": entry.saving_throw,
                    "spell_resistance": entry.spell_resistance,
                    "description_en": entry.description_en,
                }
                for entry in candidates
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if not args.catalog.exists():
        raise SystemExit(f"Translation catalog not found: {args.catalog}")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    missing = []
    for entry in candidates:
        translation = catalog.get(entry.name_en)
        if not translation:
            missing.append(entry.name_en)
            continue
        entry.name_zh = translation.get("name_zh") or entry.name_zh
        entry.description_zh = translation.get("description_zh") or entry.description_zh
        for key in (
            "school",
            "subschool",
            "levels",
            "components",
            "casting_time",
            "range_text",
            "target_text",
            "area_text",
            "effect_text",
            "duration",
            "saving_throw",
            "spell_resistance",
            "additional_costs",
        ):
            translated_key = key + "_zh"
            if translation.get(translated_key):
                setattr(entry, key, translation[translated_key])
        entry.issues = [issue for issue in entry.issues if issue != "missing_chinese_name"]
        if not entry.name_zh or not entry.description_zh:
            missing.append(entry.name_en)

    if missing:
        raise SystemExit("Incomplete translations: " + ", ".join(sorted(set(missing))))
    save_entries(args.output, entries)
    print(json.dumps({"entries": len(entries), "translated": len(candidates)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
