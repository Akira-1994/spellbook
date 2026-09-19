"""Add spells the first extraction missed and fix the entries that hid them.

Spells whose level line had no label, a wrapped label or the wording 法術等級
were not detected; their text stayed at the end of the previous spell. Where
the school line carried English in brackets, it was taken for the heading and
the spell got the name 變化系 / 幻術系. A fresh extraction with the current
parser (scripts/extract_pdf.py) finds them all; this compares it with the seed
database and applies the difference:

- inserts the missing spells,
- renames the misnamed spells (keeping their ids),
- trims the swallowed text from the previous spells' descriptions,

and records the same changes in spellbook/seed_fixes.json for user databases.

    .venv/Scripts/python.exe -m scripts.extract_pdf --output tmp/az.json
    .venv/Scripts/python.exe -m scripts.add_missing_spells tmp/az.json [--apply]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from scripts.spellbook_lib import ParsedSpell, _identity_key, _parse_levels, load_entries, new_ulid, normalize_space


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "spellbook.sqlite"
FIXES = ROOT / "spellbook" / "seed_fixes.json"
TAXONOMY = ROOT / "spellbook" / "taxonomy.json"
FIX_ID = "2026-09-missing-spells"

# Level lines the parser cannot split ("術士 4/法師 4 巫覡 4（地）"), written with
# class spellings the taxonomy already maps.
LEVEL_OVERRIDES = {
    "Black Sand": "牧師 3，沙領域 2",
    "Heart Of Air": "德魯伊 2，術士/法師 2，巫覡 2（氣）",
    "Heart Of Earth": "德魯伊 4，術士 4，法師 4，巫覡 4（地）",
    "Heart Of Fire": "德魯伊 5，術士 5，法師 5，巫覡 5（火）",
    "Heart Of Water": "德魯伊 3，術士 3，法師 3，巫覡 3（水）",
}
# The misnamed entries' school lines also hold a translator's note.
SCHOOL_WITH_NOTE = re.compile(r"^(?P<school>\S+?系)\s*[（(](?P<subschool>[^）)]+)[）)]")


def clean_name_en(value: str) -> str:
    return normalize_space(value.replace("，", ", ").replace("）", "").replace(")", ""))


def plan(connection: sqlite3.Connection, entries: list[ParsedSpell]) -> dict:
    seed = {row["source_key"]: row for row in connection.execute(
        """SELECT e.*, s.name_zh, s.name_en, s.alphabet FROM spell_entries e JOIN spells s ON s.id=e.spell_id"""
    )}
    fresh = {_identity_key(entry): entry for entry in entries}
    unmatched_seed = [row for key, row in seed.items() if key not in fresh]
    new_entries = [entry for key, entry in fresh.items() if key not in seed]

    renames, added = [], []
    for entry in new_entries:
        # A misnamed seed entry holds this spell's own description.
        # (It may also carry the next spell's heading at its end.)
        twin = next((row for row in unmatched_seed if row["pdf_page_start"] == entry.page_start
                     and entry.description_zh and (row["description_zh"] or "").startswith(entry.description_zh)), None)
        if twin is not None:
            renames.append((twin, entry))
            unmatched_seed.remove(twin)
        else:
            added.append(entry)
    if unmatched_seed:
        raise SystemExit(f"Seed entries missing from the fresh extraction: {[r['name_zh'] for r in unmatched_seed]}")

    trims = []
    for key, row in seed.items():
        entry = fresh.get(key)
        if entry is None or row["translation_status"] != "not_required":
            continue
        old = row["description_zh"] or ""
        if old != entry.description_zh:
            if not old.startswith(entry.description_zh):
                raise SystemExit(f"{row['name_zh']}: description changed beyond a trim")
            trims.append((row, entry))
    return {"renames": renames, "added": added, "trims": trims}


def check_levels(added: list[ParsedSpell]) -> None:
    aliases = json.loads(TAXONOMY.read_text(encoding="utf-8"))["class_aliases"]
    for entry in added:
        levels = _parse_levels(LEVEL_OVERRIDES.get(entry.name_en, entry.levels))
        unknown = [name for name, _, _ in levels if name not in aliases]
        if not levels or unknown:
            raise SystemExit(f"{entry.name_zh}: levels need attention: {entry.levels!r} unknown={unknown}")


def apply(connection: sqlite3.Connection, work: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    fixes = {"id": FIX_ID, "entries": {}, "spells": {}, "added_spells": []}

    for row, entry in work["trims"]:
        connection.execute(
            "UPDATE spell_entries SET description_zh=?, raw_text=?, pdf_page_end=? WHERE id=?",
            (entry.description_zh, entry.raw_text, entry.page_end, row["id"]),
        )
        connection.execute("UPDATE spell_search SET description_zh=? WHERE spell_id=?", (entry.description_zh, row["spell_id"]))
        fixes["entries"][row["id"]] = {"description_zh": [row["description_zh"] or "", entry.description_zh]}

    for row, entry in work["renames"]:
        key = _identity_key(entry)
        match = SCHOOL_WITH_NOTE.match(entry.school)
        school, subschool = (match["school"], match["subschool"]) if match else (entry.school, entry.subschool)
        name_en = clean_name_en(entry.name_en)
        connection.execute("UPDATE spells SET name_zh=?, name_en=?, source_key=?, updated_at=? WHERE id=?",
                           (entry.name_zh, name_en, key, now, row["spell_id"]))
        connection.execute(
            "UPDATE spell_entries SET heading=?, school=?, subschool=?, source_key=?, raw_text=?, description_zh=? WHERE id=?",
            (entry.heading, school, subschool, key, entry.raw_text, entry.description_zh, row["id"]),
        )
        for language, name in (("zh", entry.name_zh), ("en", name_en)):
            connection.execute("UPDATE spell_names SET name=? WHERE spell_id=? AND language=? AND name_type='canonical'",
                               (name, row["spell_id"], language))
        connection.execute("UPDATE spell_search SET name_zh=?, name_en=?, school=?, description_zh=? WHERE spell_id=?",
                           (entry.name_zh, name_en, school, entry.description_zh, row["spell_id"]))
        fixes["spells"][row["spell_id"]] = {"name_zh": [row["name_zh"], entry.name_zh], "name_en": [row["name_en"], name_en]}
        entry_diff = {"heading": [row["heading"], entry.heading], "school": [row["school"] or "", school],
                      "subschool": [row["subschool"] or "", subschool]}
        if (row["description_zh"] or "") != entry.description_zh:
            entry_diff["description_zh"] = [row["description_zh"] or "", entry.description_zh]
        fixes["entries"][row["id"]] = entry_diff

    for entry in work["added"]:
        spell_id, entry_id, key = "spl_" + new_ulid(), "ent_" + new_ulid(), _identity_key(entry)
        name_en = clean_name_en(entry.name_en)
        connection.execute(
            """INSERT INTO spells(id,source_key,name_zh,name_en,alphabet,variant_group_id,review_status,created_at,updated_at)
               VALUES(?,?,?,?,?,NULL,'unreviewed',?,?)""",
            (spell_id, key, entry.name_zh, name_en, entry.alphabet, now, now),
        )
        connection.execute(
            """INSERT INTO spell_entries(id,spell_id,source_key,heading,school,subschool,components,casting_time,range_text,
                 target_text,area_text,effect_text,duration,saving_throw,spell_resistance,description_zh,description_en,
                 additional_costs,pdf_page_start,pdf_page_end,raw_text,parse_confidence,review_status,translation_status,translation_method)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,'',?,?,?,1.0,'unreviewed','not_required',NULL)""",
            (entry_id, spell_id, key, entry.heading, entry.school, entry.subschool, entry.components, entry.casting_time,
             entry.range_text, entry.target_text, entry.area_text, entry.effect_text, entry.duration, entry.saving_throw,
             entry.spell_resistance, entry.description_zh, entry.page_start, entry.page_end, entry.raw_text),
        )
        for language, name in (("zh", entry.name_zh), ("en", name_en)):
            connection.execute("INSERT INTO spell_names(spell_id,language,name,name_type) VALUES(?,?,?,'canonical')",
                               (spell_id, language, name))
        for class_name, level, note in _parse_levels(LEVEL_OVERRIDES.get(entry.name_en, entry.levels)):
            class_id = connection.execute("SELECT id FROM classes WHERE name=?", (class_name,)).fetchone()[0]
            connection.execute("INSERT OR IGNORE INTO spell_levels VALUES(?,?,?,?)", (entry_id, class_id, level, note))
        for table, link, values in (("sources", "spell_sources", entry.sources), ("descriptors", "spell_descriptors", entry.descriptors)):
            for value in values:
                connection.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES(?)", (value,))
                value_id = connection.execute(f"SELECT id FROM {table} WHERE name=?", (value,)).fetchone()[0]
                connection.execute(f"INSERT OR IGNORE INTO {link} VALUES(?,?)", (entry_id, value_id))
        connection.execute(
            "INSERT INTO spell_search VALUES(?,?,?,?,?,?,?,?,?)",
            (spell_id, entry.name_zh, name_en, "", entry.description_zh, "", entry.school,
             " ".join(entry.descriptors), " ".join(entry.sources)),
        )
        fixes["added_spells"].append(spell_id)
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise SystemExit("foreign key check failed")
    return fixes


def save_fixes(fix_set: dict) -> None:
    data = json.loads(FIXES.read_text(encoding="utf-8"))
    data["sets"] = [s for s in data["sets"] if s["id"] != fix_set["id"]] + [fix_set]
    FIXES.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("extraction", type=Path, help="output of scripts.extract_pdf")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    entries = load_entries(args.extraction)
    with closing(sqlite3.connect(SEED)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        work = plan(connection, entries)
        check_levels(work["added"])
        print(f"add {len(work['added'])}: {', '.join(e.name_zh for e in work['added'])}")
        renamed = ", ".join(f"{row['name_zh']} → {entry.name_zh}" for row, entry in work["renames"])
        print(f"rename {len(work['renames'])}: {renamed}")
        print(f"trim {len(work['trims'])}: {', '.join(r[0]['name_zh'] for r in work['trims'])}")
        if args.apply and any(work.values()):
            with connection:
                fixes = apply(connection, work)
            save_fixes(fixes)
            print(f"applied; recorded in {FIXES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
