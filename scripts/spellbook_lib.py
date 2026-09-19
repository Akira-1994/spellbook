from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


FIELD_LABELS = {
    "等級": "levels",
    "Level": "levels",
    "法術成分": "components",
    "成分": "components",
    "Components": "components",
    "施法時間": "casting_time",
    "Casting Time": "casting_time",
    "距離": "range_text",
    "射程": "range_text",
    "Range": "range_text",
    "目標": "target_text",
    "Target": "target_text",
    "區域": "area_text",
    "範圍": "area_text",
    "影響區域": "area_text",
    "Area": "area_text",
    "效果": "effect_text",
    "Effect": "effect_text",
    "持續時間": "duration",
    "Duration": "duration",
    "豁免檢定": "saving_throw",
    "Saving Throw": "saving_throw",
    "法術抗力": "spell_resistance",
    "法數抗力": "spell_resistance",
    "法術炕力": "spell_resistance",
    "Spell Resistance": "spell_resistance",
    # Alternative wordings used by different translators of the source PDF.
    "法書成分": "components",
    "施展時間": "casting_time",
    "施放時間": "casting_time",
    "施法動作": "casting_time",
    "作用距離": "range_text",
    "作用物件": "target_text",
    "目標或區域": "target_text",
    "作用範圍": "area_text",
    "影響範圍": "area_text",
    "面積": "area_text",
    "時效": "duration",
    "持續": "duration",
    "豁免": "saving_throw",
    "抗力": "spell_resistance",
}

# A few entries use a semicolon instead of a colon, and some labels are typeset
# with spaces between the characters ("法 術 成 分").
FIELD_SEPARATOR = r"[：:；;]"
_LABEL_ALTERNATIVES = "|".join(
    r"\s*".join(map(re.escape, label)) if re.search(r"[\u4e00-\u9fff]", label) else re.escape(label)
    for label in sorted(FIELD_LABELS, key=len, reverse=True)
)
FIELD_PATTERN = re.compile(r"^\s*(" + _LABEL_ALTERNATIVES + r")\s*" + FIELD_SEPARATOR + r"\s*(.*)$", re.IGNORECASE)
# How far past a non-field line to look for the remaining fields, for tables
# the PDF layout placed in the middle of the field block.
FIELD_TABLE_LOOKAHEAD = 30
RANGE_WORDS = re.compile(r"^(接觸|個人|近距|中距|中等|遠距|長距|無限|視線)")
LETTER_PATTERN = re.compile(r"^\s*([A-Z])\s*[：:]\s*$")
PAREN_PATTERN = re.compile(r"[（(]([^()（）]+)[）)]")
SCHOOL_ZH_PATTERN = re.compile(r"^(?P<school>[^\[【（(]+?系)(?:[（(](?P<subschool>[^）)]+)[）)])?(?:\s*[\[【](?P<descriptors>[^\]】]+)[\]】])?\s*$")
SCHOOL_EN_PATTERN = re.compile(r"^(?P<school>[A-Za-z ]+?)(?:\s*[（(](?P<subschool>[^）)]+)[）)])?(?:\s*[\[【](?P<descriptors>[^\]】]+)[\]】])?\s*$")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
ASCII_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9'’\- ,]+$")
ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
SOURCE_HINTS = (
    "大全",
    "之書",
    "書",
    "冒險",
    "奧術",
    "神力",
    "戰力",
    "流氓",
    "巫師",
    "聖鬥士",
    "玩家手冊",
    "族裔",
    "戰役",
    "ECS",
    "PHB",
)


@dataclass
class LineRef:
    text: str
    page: int


@dataclass
class ParsedSpell:
    alphabet: str
    name_zh: str
    name_en: str
    heading: str
    school: str = ""
    subschool: str = ""
    descriptors: list[str] = field(default_factory=list)
    levels: str = ""
    components: str = ""
    casting_time: str = ""
    range_text: str = ""
    target_text: str = ""
    area_text: str = ""
    effect_text: str = ""
    duration: str = ""
    saving_throw: str = ""
    spell_resistance: str = ""
    description_zh: str = ""
    description_en: str = ""
    additional_costs: str = ""
    raw_text: str = ""
    page_start: int = 0
    page_end: int = 0
    sources: list[str] = field(default_factory=list)
    parse_confidence: float = 1.0
    english_only: bool = False
    needs_translation: bool = False
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "ParsedSpell":
        return cls(**value)


def normalize_space(value: str) -> str:
    value = value.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _field_match(line: str):
    return FIELD_PATTERN.match(line.strip())


def field_key(match) -> str:
    label = re.sub(r"\s+", "", match.group(1)).casefold()
    return next(value for key, value in FIELD_LABELS.items() if key.replace(" ", "").casefold() == label or key.casefold() == match.group(1).casefold())


_SPLITTABLE_LABELS = sorted(
    (label for label in FIELD_LABELS if re.search(r"[\u4e00-\u9fff]", label) and len(label) > 1), key=len, reverse=True
)


def _rejoin_split_label(lines: list[str], index: int) -> None:
    """Line wrapping sometimes splits a label: "…腐敗 8 環法" / "術成分：…".

    Move the stranded head of the label from lines[index] back onto the next
    line. Only called inside the field block, so description text is untouched.
    """
    if index + 1 >= len(lines):
        return
    current, following = lines[index].rstrip(), lines[index + 1].lstrip()
    if _field_match(following):
        return
    for label in _SPLITTABLE_LABELS:
        split = next(
            (k for k in range(len(label) - 1, 0, -1)
             if current.endswith(label[:k]) and re.match(re.escape(label[k:]) + r"\s*" + FIELD_SEPARATOR, following)),
            None,
        )
        if split:
            lines[index] = current[:-split]
            lines[index + 1] = label[:split] + following
            return


def _looks_like_table(lines: Sequence[str]) -> bool:
    """Table rows are short cells without sentence endings. Prose, a blank
    line or the next spell's heading (a block can run into the following
    spell) means the description has started instead."""
    texts = [normalize_space(line) for line in lines]
    if not texts or any(not text for text in texts):
        return False
    return not any(text.endswith(("。", "」", "）。")) or _looks_like_heading(text) for text in texts)


def parse_fields(lines: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    """Parse the field block that starts at the level line.

    Returns the fields and the remaining lines, which form the description.
    Lines of a table that the PDF layout placed between fields are moved to
    the start of the description.
    """
    lines = [line.rstrip() for line in lines]
    fields: dict[str, str] = {}
    displaced: list[str] = []
    current_key = ""
    index = 0
    while index < len(lines):
        _rejoin_split_label(lines, index)
        text = normalize_space(lines[index])
        match = _field_match(text)
        if match:
            key = field_key(match)
            value = normalize_space(match.group(2))
            # Some translators use 範圍 for range rather than area.
            if key == "area_text" and "range_text" not in fields:
                if RANGE_WORDS.match(value):
                    key = "range_text"
                elif "area_text" in fields:
                    # "範圍：60 尺" followed by "區域：…": the first one was the range.
                    fields["range_text"] = fields.pop("area_text")
            if key in fields and key != current_key:
                # A field seen earlier appears again: the description has begun
                # (for example a sub-effect with its own duration).
                break
            current_key = key
            fields[current_key] = normalize_space(" ".join(filter(None, [fields.get(current_key, ""), value])))
            index += 1
            continue
        if not text:
            index += 1
            continue

        # A wrapped field value is followed shortly by another recognized field.
        next_field_found = False
        for lookahead in range(index + 1, min(index + 4, len(lines))):
            _rejoin_split_label(lines, lookahead)
            candidate = normalize_space(lines[lookahead])
            if not candidate:
                continue
            next_field_found = bool(_field_match(candidate))
            break
        if current_key and next_field_found:
            fields[current_key] = normalize_space(" ".join(filter(None, [fields.get(current_key, ""), text])))
            index += 1
            continue

        # A table inserted before the last fields: skip it if more, not yet
        # seen fields follow soon. Spell resistance closes the field block.
        if "spell_resistance" not in fields:
            resume = next(
                (j for j in range(index + 1, min(index + FIELD_TABLE_LOOKAHEAD, len(lines)))
                 if (m := _field_match(normalize_space(lines[j]))) and field_key(m) not in fields),
                None,
            )
            if resume is not None and _looks_like_table(lines[index:resume]):
                displaced.extend(lines[index:resume])
                current_key = ""
                index = resume
                continue
        break
    return fields, displaced + list(lines[index:])


def _looks_like_heading(line: str) -> bool:
    text = normalize_space(line)
    if not text or len(text) > 180 or _field_match(text) or LETTER_PATTERN.match(text):
        return False
    if ASCII_NAME_PATTERN.match(re.sub(r"[（(].*$", "", text).strip()):
        return True
    if SCHOOL_ZH_PATTERN.match(text) or SCHOOL_EN_PATTERN.match(text):
        return False
    groups = PAREN_PATTERN.findall(text)
    return bool(groups and any(re.search(r"[A-Za-z]", group) for group in groups))


def _find_header_index(lines: Sequence[LineRef], level_index: int) -> int | None:
    candidates: list[int] = []
    index = level_index - 1
    while index >= 0 and len(candidates) < 7:
        if lines[index].text.strip():
            candidates.append(index)
        index -= 1
    for candidate in candidates:
        if _looks_like_heading(lines[candidate].text):
            return candidate
    return None


def _parse_heading(heading: str) -> tuple[str, str, list[str]]:
    heading = normalize_space(heading)
    prefix = re.split(r"[（(]", heading, maxsplit=1)[0].strip()
    groups = [normalize_space(group) for group in PAREN_PATTERN.findall(heading)]
    has_cjk_prefix = bool(CJK_PATTERN.search(prefix))
    name_zh = prefix if has_cjk_prefix else ""
    name_en = ""
    english_group_index = None

    if not has_cjk_prefix:
        name_en = prefix
    else:
        for index, group in enumerate(groups):
            if re.search(r"[A-Za-z]", group) and not any(hint in group for hint in SOURCE_HINTS):
                name_en = group
                english_group_index = index
                break

    sources: list[str] = []
    for index, group in enumerate(groups):
        if index == english_group_index:
            continue
        if any(hint in group for hint in SOURCE_HINTS):
            sources.append(group)

    return name_zh, name_en, list(dict.fromkeys(sources))


def _parse_school(value: str) -> tuple[str, str, list[str]]:
    text = normalize_space(value)
    match = SCHOOL_ZH_PATTERN.match(text) or SCHOOL_EN_PATTERN.match(text)
    if not match:
        return text, "", []
    school = normalize_space(match.group("school"))
    subschool = normalize_space(match.group("subschool") or "")
    descriptor_text = match.group("descriptors") or ""
    descriptors = [normalize_space(item) for item in re.split(r"[,，、]", descriptor_text) if normalize_space(item)]
    return school, subschool, descriptors


def _mostly_english(value: str) -> bool:
    letters = len(re.findall(r"[A-Za-z]", value))
    cjk = len(CJK_PATTERN.findall(value))
    return letters >= 20 and letters > cjk * 3


def _parse_block(alphabet: str, block: Sequence[LineRef], level_index_in_block: int) -> ParsedSpell:
    heading = normalize_space(block[0].text)
    name_zh, name_en, sources = _parse_heading(heading)

    school_lines = [normalize_space(item.text) for item in block[1:level_index_in_block] if item.text.strip()]
    school, subschool, descriptors = _parse_school(" ".join(school_lines))

    fields, description_source = parse_fields([item.text for item in block[level_index_in_block:]])
    description_lines = [normalize_space(text) for text in description_source if text.strip() and not LETTER_PATTERN.match(text)]
    description = "\n".join(description_lines).strip()
    description_en = description if _mostly_english(description) else ""
    description_zh = "" if description_en else description
    english_only = not name_zh
    needs_translation = english_only or bool(description_en)

    issues: list[str] = []
    if not name_en:
        issues.append("missing_english_name")
    if not name_zh:
        issues.append("missing_chinese_name")
    if not description:
        issues.append("missing_description")
    if not fields.get("levels"):
        issues.append("missing_levels")

    confidence = max(0.0, 1.0 - len(issues) * 0.15)
    raw_text = "\n".join(item.text.rstrip() for item in block).strip()
    return ParsedSpell(
        alphabet=alphabet,
        name_zh=name_zh,
        name_en=name_en,
        heading=heading,
        school=school,
        subschool=subschool,
        descriptors=descriptors,
        levels=fields.get("levels", ""),
        components=fields.get("components", ""),
        casting_time=fields.get("casting_time", ""),
        range_text=fields.get("range_text", ""),
        target_text=fields.get("target_text", ""),
        area_text=fields.get("area_text", ""),
        effect_text=fields.get("effect_text", ""),
        duration=fields.get("duration", ""),
        saving_throw=fields.get("saving_throw", ""),
        spell_resistance=fields.get("spell_resistance", ""),
        description_zh=description_zh,
        description_en=description_en,
        raw_text=raw_text,
        page_start=block[0].page,
        page_end=max(item.page for item in block if item.text.strip()),
        sources=sources,
        parse_confidence=confidence,
        english_only=english_only,
        needs_translation=needs_translation,
        issues=issues,
    )


def parse_pages(pages: Iterable[tuple[int, str]]) -> list[ParsedSpell]:
    lines: list[LineRef] = []
    for page_number, page_text in pages:
        lines.extend(LineRef(line.rstrip(), page_number) for line in page_text.splitlines())

    starts: list[tuple[int, int, str]] = []
    current_letter = ""
    for index, item in enumerate(lines):
        letter_match = LETTER_PATTERN.match(item.text)
        if letter_match:
            current_letter = letter_match.group(1)
            continue
        match = _field_match(item.text)
        if not match or field_key(match) != "levels":
            continue
        header_index = _find_header_index(lines, index)
        if header_index is None:
            continue
        starts.append((header_index, index, current_letter))

    unique_starts: list[tuple[int, int, str]] = []
    seen = set()
    for value in starts:
        if value[0] not in seen:
            unique_starts.append(value)
            seen.add(value[0])

    entries: list[ParsedSpell] = []
    for position, (start, level_index, alphabet) in enumerate(unique_starts):
        end = unique_starts[position + 1][0] if position + 1 < len(unique_starts) else len(lines)
        block = lines[start:end]
        entries.append(_parse_block(alphabet, block, level_index - start))
    return entries


def _encode_crockford(value: int, length: int) -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    chars = []
    for _ in range(length):
        chars.append(alphabet[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    timestamp = int(time.time() * 1000) & ((1 << 48) - 1)
    random_bits = int.from_bytes(secrets.token_bytes(10), "big")
    return _encode_crockford((timestamp << 80) | random_bits, 26)


def _identity_key(entry: ParsedSpell) -> str:
    raw = "|".join(
        [
            entry.alphabet,
            normalize_space(entry.name_en).casefold(),
            str(entry.page_start),
            normalize_space(entry.heading).casefold(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_levels(value: str) -> list[tuple[str, int, str]]:
    result: list[tuple[str, int, str]] = []
    for part in re.split(r"[,，、;；]", value):
        text = normalize_space(part)
        match = re.match(r"^(.+?)\s*(\d+)(?:\s*級)?(?:\s*[（(]([^）)]*)[）)])?$", text)
        if match:
            result.append((normalize_space(match.group(1)), int(match.group(2)), normalize_space(match.group(3) or "")))
    return result


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE spells(
    id TEXT PRIMARY KEY CHECK(id GLOB 'spl_*'),
    source_key TEXT NOT NULL UNIQUE,
    name_zh TEXT NOT NULL CHECK(length(trim(name_zh)) > 0),
    name_en TEXT NOT NULL CHECK(length(trim(name_en)) > 0),
    alphabet TEXT NOT NULL CHECK(length(alphabet) = 1 AND alphabet BETWEEN 'A' AND 'Z'),
    variant_group_id TEXT,
    review_status TEXT NOT NULL CHECK(review_status IN ('unreviewed','needs_review','reviewed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE spell_entries(
    id TEXT PRIMARY KEY CHECK(id GLOB 'ent_*'),
    spell_id TEXT NOT NULL REFERENCES spells(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL UNIQUE,
    heading TEXT NOT NULL,
    school TEXT,
    subschool TEXT,
    components TEXT,
    casting_time TEXT,
    range_text TEXT,
    target_text TEXT,
    area_text TEXT,
    effect_text TEXT,
    duration TEXT,
    saving_throw TEXT,
    spell_resistance TEXT,
    description_zh TEXT NOT NULL,
    description_en TEXT,
    additional_costs TEXT,
    pdf_page_start INTEGER NOT NULL CHECK(pdf_page_start BETWEEN 192 AND 1144),
    pdf_page_end INTEGER NOT NULL CHECK(pdf_page_end BETWEEN pdf_page_start AND 1144),
    raw_text TEXT NOT NULL,
    parse_confidence REAL NOT NULL CHECK(parse_confidence BETWEEN 0 AND 1),
    review_status TEXT NOT NULL CHECK(review_status IN ('unreviewed','needs_review','reviewed')),
    translation_status TEXT NOT NULL CHECK(translation_status IN ('not_required','generated','reviewed')),
    translation_method TEXT
);
CREATE TABLE spell_names(
    id INTEGER PRIMARY KEY,
    spell_id TEXT NOT NULL REFERENCES spells(id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    name TEXT NOT NULL,
    name_type TEXT NOT NULL,
    UNIQUE(spell_id, language, name, name_type)
);
CREATE TABLE classes(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE spell_levels(
    spell_entry_id TEXT NOT NULL REFERENCES spell_entries(id) ON DELETE CASCADE,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    spell_level INTEGER NOT NULL CHECK(spell_level BETWEEN 0 AND 9),
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(spell_entry_id, class_id, spell_level, note)
);
CREATE TABLE sources(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE spell_sources(
    spell_entry_id TEXT NOT NULL REFERENCES spell_entries(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    PRIMARY KEY(spell_entry_id, source_id)
);
CREATE TABLE descriptors(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE spell_descriptors(
    spell_entry_id TEXT NOT NULL REFERENCES spell_entries(id) ON DELETE CASCADE,
    descriptor_id INTEGER NOT NULL REFERENCES descriptors(id),
    PRIMARY KEY(spell_entry_id, descriptor_id)
);
CREATE TABLE extraction_issues(
    id INTEGER PRIMARY KEY,
    spell_entry_id TEXT NOT NULL REFERENCES spell_entries(id) ON DELETE CASCADE,
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('info','warning','error')),
    message TEXT NOT NULL
);
CREATE VIRTUAL TABLE spell_search USING fts5(
    spell_id UNINDEXED,
    name_zh,
    name_en,
    aliases,
    description_zh,
    description_en,
    school,
    descriptors,
    sources,
    tokenize='unicode61'
);
"""


def _existing_ids(path: Path) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    spell_ids: dict[str, tuple[str, str]] = {}
    entry_ids: dict[str, str] = {}
    if not path.exists():
        return spell_ids, entry_ids
    try:
        with closing(sqlite3.connect(path)) as connection:
            spell_ids = {
                row[0]: (row[1], row[2])
                for row in connection.execute("SELECT source_key, id, created_at FROM spells")
            }
            entry_ids = {row[0]: row[1] for row in connection.execute("SELECT source_key, id FROM spell_entries")}
    except sqlite3.Error:
        return {}, {}
    return spell_ids, entry_ids


def build_database(path: Path | str, entries: Sequence[ParsedSpell]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_spells, existing_entries = _existing_ids(path)
    name_counts: dict[str, int] = {}
    for entry in entries:
        normalized_name = normalize_space(entry.name_en).casefold()
        name_counts[normalized_name] = name_counts.get(normalized_name, 0) + 1
    temp_path = path.with_suffix(path.suffix + ".tmp")
    if temp_path.exists():
        temp_path.unlink()

    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(temp_path)) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute("INSERT INTO schema_migrations VALUES(1, ?)", (now,))

        for entry in entries:
            if not entry.name_zh or not entry.name_en:
                raise ValueError(f"Bilingual names required before database build: {entry.heading}")
            source_key = _identity_key(entry)
            existing = existing_spells.get(source_key)
            spell_id = existing[0] if existing else "spl_" + new_ulid()
            created_at = existing[1] if existing else now
            entry_id = existing_entries.get(source_key, "ent_" + new_ulid())
            effective_issues = list(entry.issues)
            if name_counts[normalize_space(entry.name_en).casefold()] > 1:
                effective_issues.append("duplicate_english_name")
            review_status = "needs_review" if effective_issues or entry.needs_translation else "unreviewed"
            translation_status = "generated" if entry.needs_translation else "not_required"
            translation_method = "contextual_glossary" if entry.needs_translation else None

            connection.execute(
                "INSERT INTO spells VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    spell_id,
                    source_key,
                    entry.name_zh,
                    entry.name_en,
                    entry.alphabet,
                    None,
                    review_status,
                    created_at,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO spell_entries VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )""",
                (
                    entry_id,
                    spell_id,
                    source_key,
                    entry.heading,
                    entry.school,
                    entry.subschool,
                    entry.components,
                    entry.casting_time,
                    entry.range_text,
                    entry.target_text,
                    entry.area_text,
                    entry.effect_text,
                    entry.duration,
                    entry.saving_throw,
                    entry.spell_resistance,
                    entry.description_zh,
                    entry.description_en or None,
                    entry.additional_costs,
                    entry.page_start,
                    entry.page_end,
                    entry.raw_text,
                    entry.parse_confidence,
                    review_status,
                    translation_status,
                    translation_method,
                ),
            )

            for class_name, level, note in _parse_levels(entry.levels):
                connection.execute("INSERT OR IGNORE INTO classes(name) VALUES(?)", (class_name,))
                class_id = connection.execute("SELECT id FROM classes WHERE name=?", (class_name,)).fetchone()[0]
                connection.execute(
                    "INSERT OR IGNORE INTO spell_levels VALUES(?,?,?,?)",
                    (entry_id, class_id, level, note),
                )

            for source in entry.sources:
                connection.execute("INSERT OR IGNORE INTO sources(name) VALUES(?)", (source,))
                source_id = connection.execute("SELECT id FROM sources WHERE name=?", (source,)).fetchone()[0]
                connection.execute("INSERT OR IGNORE INTO spell_sources VALUES(?,?)", (entry_id, source_id))

            for descriptor in entry.descriptors:
                connection.execute("INSERT OR IGNORE INTO descriptors(name) VALUES(?)", (descriptor,))
                descriptor_id = connection.execute("SELECT id FROM descriptors WHERE name=?", (descriptor,)).fetchone()[0]
                connection.execute("INSERT OR IGNORE INTO spell_descriptors VALUES(?,?)", (entry_id, descriptor_id))

            for issue in effective_issues:
                connection.execute(
                    "INSERT INTO extraction_issues(spell_entry_id,issue_type,severity,message) VALUES(?,?,?,?)",
                    (entry_id, issue, "warning", issue.replace("_", " ")),
                )

            connection.execute(
                "INSERT INTO spell_search VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    spell_id,
                    entry.name_zh,
                    entry.name_en,
                    "",
                    entry.description_zh,
                    entry.description_en,
                    entry.school,
                    " ".join(entry.descriptors),
                    " ".join(entry.sources),
                ),
            )

        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_issues:
            raise RuntimeError(f"SQLite foreign key check failed: {foreign_key_issues}")
        connection.commit()

    os.replace(temp_path, path)


def save_entries(path: Path | str, entries: Sequence[ParsedSpell]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([entry.to_dict() for entry in entries], ensure_ascii=False, indent=2), encoding="utf-8")


def load_entries(path: Path | str) -> list[ParsedSpell]:
    return [ParsedSpell.from_dict(value) for value in json.loads(Path(path).read_text(encoding="utf-8"))]
