"""Add the 32 summon spells from the summon appendix (PDF pages 1149-1162).

The appendix holds four families: summon monster I-IX, summon nature's ally
I-IX, summon undead I-V (死者之書) and summon desert ally I-IX (沙暴之書).
Levels II and up only restate what differs from level I ("和一級召喚怪物術一
樣…"), and each family ends with its creature lists, which the extractor
attaches to the family's last spell. This script:

- takes the unstated fields of higher levels from level I,
- moves each level's creature list (with its footnotes) into that spell,
- normalizes names, school lines and one level spelling,

then inserts the spells into the seed and records them in seed_fixes.json so
existing user databases gain them too.

    .venv/Scripts/python.exe -m scripts.add_summon_spells [--apply]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from scripts.add_missing_spells import SEED, insert_spell, save_fixes
from scripts.spellbook_lib import ParsedSpell, _identity_key, _parse_levels, normalize_space, parse_pages


ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "spellbook_doc_v1.1.pdf"
PAGES = range(1149, 1163)
FIX_ID = "2026-09-summon-appendix"
NUMERALS = "一二三四五六七八九"
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9}
FAMILY = re.compile(r"^Summon (Monster|Nature\s*’?'?\s*s Ally|Undead|Desert Ally) ([IVX]+)$")
INHERITED = ("components", "casting_time", "range_text", "target_text", "area_text", "effect_text",
             "duration", "saving_throw", "spell_resistance")
# Headings of the creature lists that the extractor leaves in the last spell.
LIST_START = re.compile(r"^(召喚怪物|召喚自然盟友|召喚不死生物列表|Summon Desert Ally\s+沙漠盟友列表)")
LIST_TITLES = {"Monster": "召喚怪物列表", "Nature's Ally": "召喚自然盟友列表",
               "Undead": "召喚不死生物列表", "Desert Ally": "召喚沙漠盟友列表"}
# The undead table is a five-column grid that text extraction scrambles; this
# is its content by column, as in the Spell Compendium list it cites.
UNDEAD_LISTS = {
    1: ["人類戰士骷髏（Human warrior skeleton, MM 226）", "狗頭人殭屍（Kobold zombie, MM 266）"],
    2: ["梟熊骷髏（Owlbear skeleton, MM 226）", "熊地精殭屍（Bugbear zombie, MM 267）"],
    3: ["食屍鬼（Ghoul, MM 118）", "巨魔骷髏（Troll skeleton, MM 227）", "食人魔殭屍（Ogre zombie, MM 267）"],
    4: ["怨魂（Allip, MM 10）", "妖鬼（Ghast, MM 119）", "翼龍殭屍（Wyvern zombie, MM 267）"],
    5: ["木乃伊（Mummy, MM 190）", "幽影（Shadow, MM 221）", "屍妖（Wight, MM 255）", "吸血衍體（Vampire spawn, MM 253）"],
}
UNDEAD_NOTE = "（原書註：由於死者之書列表曖昧不清，故使用萬法大全列表。）"
WATER_NOTE = "¹ 只能在有水的環境中召喚。"
DESERT_NOTE = "＊ 新怪物，描述詳見《沙暴之書》第六章。"
# The level-1 nature's ally list is typeset in simplified characters.
SIMPLIFIED = str.maketrans({"动": "動", "鳄": "鱷", "鱼": "魚", "鹰": "鷹", "猫": "貓", "头": "頭", "凶": "兇"})
LEVEL_OVERRIDES = {"Summon Monster IX": "混亂 9，牧師 9，邪惡 9，善良 9，秩序 9，術士/法師 9"}  # 守序 = Law domain


def family_of(entry: ParsedSpell) -> tuple[str, int]:
    match = FAMILY.match(normalize_space(entry.name_en))
    if not match:
        raise SystemExit(f"Unexpected spell in the appendix: {entry.heading}")
    family = match.group(1)
    family = "Nature's Ally" if family.startswith("Nature") else family
    return family, ROMAN[match.group(2)]


def split_creature_lists(family: str, lines: list[str]) -> tuple[list[str], dict[int, list[str]], list[str]]:
    """Return (description lines before the lists, {level: items}, footnotes)."""
    start = next((i for i, line in enumerate(lines) if LIST_START.match(line)), None)
    if start is None:
        return lines, {}, []
    if family == "Undead":
        return lines[:start], UNDEAD_LISTS, [UNDEAD_NOTE]
    lists: dict[int, list[str]] = {}
    footnotes: list[str] = []
    level = None
    pending = ""
    for raw in lines[start + 1:]:
        line = normalize_space(raw)
        if not line:
            continue
        if heading := re.match(r"^(\d)(?:st|nd|rd|th)?[-\s]*(?:Level)?\s*(\d)?\s*[級级]", line):
            level = int(heading.group(1))
            lists[level] = []
            continue
        if line == "1":  # footnote marker split onto its own line
            pending += "¹"
            continue
        if re.match(r"^1\s*只能在有水", line):
            footnotes.append(WATER_NOTE)
            continue
        if line.startswith("新怪物描述"):
            footnotes.append(DESERT_NOTE)
            continue
        if level is None:
            continue
        if line.startswith("*"):  # e.g. "*不可施展迷舞" belongs to this level's list
            lists[level].append(line)
            continue
        if family == "Desert Ally":
            # "Baboon 狒狒" / "Jackal? 豺狼": English then Chinese.
            match = re.match(r"^(?P<en>[A-Za-z][A-Za-z ,'\-]*?)(?P<new>\?)?\s+(?P<zh>[一-鿿].*)$", line)
            if match:
                lists[level].append(f"{match['zh']}（{match['en']}）{'＊' if match['new'] else ''}")
            continue
        if re.search(r"[）)]", line) and not re.search(r"[（(]", line) and lists[level]:
            joiner = " " if re.search(r"[A-Za-z]$", lists[level][-1]) and re.match(r"^[A-Za-z]", line) else ""
            lists[level][-1] += joiner + line  # the end of an item wrapped onto the next line
            continue
        if not re.search(r"[（(]", line):  # a name whose bracketed part follows the "1" marker
            pending = line
            continue
        if pending:
            line = pending + line
            pending = ""
        line = re.sub(r"(?<=[A-Za-z)])1(?=[）)\s])", "", line)  # footnote digit glued to the English name
        lists[level].append(line.translate(SIMPLIFIED))
    return lines[:start], lists, footnotes


def build(entries: list[ParsedSpell]) -> list[tuple[ParsedSpell, str]]:
    families: dict[str, dict[int, ParsedSpell]] = {}
    for entry in entries:
        family, level = family_of(entry)
        families.setdefault(family, {})[level] = entry
    result = []
    for family, levels in families.items():
        top = max(levels)
        body, lists, footnotes = split_creature_lists(family, levels[top].description_zh.split("\n"))
        levels[top] = replace(levels[top], description_zh="\n".join(body).strip())
        base = levels[1]
        for level, entry in sorted(levels.items()):
            name_en = re.sub(r"\s*’\s*|\s*'\s*", "'", normalize_space(entry.name_en))
            name_zh = re.sub(r"^(\d)\s*級", lambda m: NUMERALS[int(m.group(1)) - 1] + "級", entry.name_zh)
            school_line = normalize_space(entry.school + ("（" + entry.subschool + "）" if entry.subschool else ""))
            school_match = re.match(r"^(\S+?系)\s*[（(\[【]?\s*([^）)\]】\s]*)", school_line)
            school = school_match.group(1) if school_match else entry.school
            subschool = entry.subschool or (school_match.group(2) if school_match else "") or "召喚"
            descriptors = [d for d in entry.descriptors if not d.startswith("見") and d != subschool]
            description = entry.description_zh
            own = {f: getattr(entry, f) for f in INHERITED}
            if not own["effect_text"] and description.startswith("一個或多個"):  # effect value wrapped to the next line
                own["effect_text"], _, description = description.partition("\n")
            fields = {f: own[f] or (getattr(base, f) if level > 1 else "") for f in INHERITED}
            items = lists.get(level, [])
            if items:
                marked = "".join(items)
                notes = [n for n in footnotes if n not in (WATER_NOTE, DESERT_NOTE)]
                notes += [note for mark, note in (("¹", WATER_NOTE), ("＊", DESERT_NOTE)) if mark in marked]
                description = "\n".join([description, "", f"{LIST_TITLES[family]}（{level} 級）：", *items, *notes]).strip()
            fixed = replace(entry, name_zh=name_zh, name_en=name_en, school=school, subschool=subschool,
                            descriptors=descriptors, description_zh=description, alphabet="S", **fields)
            result.append((fixed, LEVEL_OVERRIDES.get(name_en, entry.levels)))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    reader = PdfReader(str(PDF))
    entries = parse_pages([(page, reader.pages[page - 1].extract_text() or "") for page in PAGES])
    spells = build(entries)
    with closing(sqlite3.connect(SEED)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        existing = {row[0] for row in connection.execute("SELECT source_key FROM spells")}
        pending = [(entry, levels) for entry, levels in spells if _identity_key(entry) not in existing]
        for entry, levels in spells:
            parsed = _parse_levels(levels)
            print(f"{entry.name_zh} / {entry.name_en} | {entry.school}（{entry.subschool}） | {levels} | "
                  f"{len(entry.description_zh.splitlines())} lines{' (exists)' if (entry, levels) not in pending else ''}")
            if not parsed:
                raise SystemExit(f"no levels for {entry.name_zh}")
        if args.apply and pending:
            from spellbook.database import widen_page_range
            widen_page_range(connection)
            now = datetime.now(timezone.utc).isoformat()
            with connection:
                added = [insert_spell(connection, entry, levels, now) for entry, levels in pending]
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise SystemExit("foreign key check failed")
            save_fixes({"id": FIX_ID, "added_spells": added})
            print(f"added {len(added)} spells")


if __name__ == "__main__":
    main()
