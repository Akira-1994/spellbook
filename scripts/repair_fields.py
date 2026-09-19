"""Re-parse spell fields from each entry's stored raw text with the current parser.

The first extraction swallowed fields into the description whenever a label
was wrapped across lines, used another wording or a semicolon, or had a table
placed before it. This compares a fresh parse of raw_text with the seed
database and reports, or with --apply writes, the corrected fields.

Only fields and the Chinese description change; levels, names and schools are
left alone (levels are normalized by the taxonomy), and machine-translated
entries are skipped because their description is not the raw text.

    .venv/Scripts/python.exe -m scripts.repair_fields            # report only
    .venv/Scripts/python.exe -m scripts.repair_fields --apply    # update seed + fixes file
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from scripts.spellbook_lib import LETTER_PATTERN, _field_match, field_key, normalize_space, parse_fields


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "spellbook.sqlite"
FIXES = ROOT / "spellbook" / "seed_fixes.json"
REPORT = ROOT / "reports" / "field-repair.csv"
FIELDS = (
    "components", "casting_time", "range_text", "target_text", "area_text",
    "effect_text", "duration", "saving_throw", "spell_resistance",
)
FIX_ID = "2026-09-field-reparse"


def reparse(raw_text: str) -> dict[str, str] | None:
    lines = raw_text.replace("\r", "").split("\n")
    level_index = next((i for i, line in enumerate(lines) if (m := _field_match(line)) and field_key(m) == "levels"), None)
    if level_index is None:
        return None
    fields, description = parse_fields(lines[level_index:])
    result = {field: fields.get(field, "") for field in FIELDS}
    result["description_zh"] = "\n".join(
        normalize_space(line) for line in description if line.strip() and not LETTER_PATTERN.match(line)
    ).strip()
    return result


def compare(connection: sqlite3.Connection) -> list[dict]:
    changes = []
    rows = connection.execute(
        f"""SELECT e.id,s.id AS spell_id,s.name_zh,s.name_en,e.raw_text,e.description_zh,{','.join('e.' + f for f in FIELDS)}
            FROM spell_entries e JOIN spells s ON s.id=e.spell_id
            WHERE e.translation_status='not_required' ORDER BY s.alphabet,upper(s.name_en)"""
    )
    for row in rows:
        fresh = reparse(row["raw_text"])
        if fresh is None:
            continue
        diff = {
            field: [row[field] or "", fresh[field]]
            for field in (*FIELDS, "description_zh")
            if (row[field] or "") != fresh[field]
        }
        if diff and not lost_content(row, fresh):
            changes.append({"entry_id": row["id"], "spell_id": row["spell_id"], "name_zh": row["name_zh"], "name_en": row["name_en"], "diff": diff})
    return changes


def lost_content(row, fresh: dict[str, str]) -> list[str]:
    """Text the re-parse would lose. A value may move between fields (range
    parsed as area before), and description lines may only leave the
    description by becoming field values."""
    squash = lambda value: "".join((value or "").split())
    fresh_fields = squash(" ".join(fresh[field] for field in FIELDS))
    # An old value may hold several fields glued together ("中等（…） 目標或區域：…").
    lost = [
        f"{field}: {row[field]}" for field in FIELDS
        if row[field] and not fresh[field]
        and not all(squash(piece) in fresh_fields for piece in EMBEDDED_LABEL.split(row[field]) if squash(piece))
    ]
    kept = set(squash(line) for line in fresh["description_zh"].split("\n"))
    for line in (row["description_zh"] or "").split("\n"):
        text = squash(line)
        if not text or text in kept:
            continue
        # A removed line must be a field line whose value now lives in a field.
        # Wrapped labels leave fragments such as "法時間：1 分鐘" or "…豁".
        value = PARTIAL_LABEL.sub("", text)
        if value and not any(squash(candidate) in fresh_fields for candidate in (value, value[:-1], value[:-2]) if candidate):
            lost.append(f"description: {line}")
    return lost


PARTIAL_LABEL = re.compile(r"^[一-鿿A-Za-z ]{1,6}[：:；;]")
EMBEDDED_LABEL = re.compile(r"[一-鿿]{1,6}\s*[：:；;]")


def write_report(changes: list[dict]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["法術", "英文名稱", "欄位", "修正前", "修正後"])
        for change in changes:
            for field, (old, new) in change["diff"].items():
                shorten = (lambda v: v[:120] + ("…" if len(v) > 120 else "")) if field == "description_zh" else (lambda v: v)
                writer.writerow([change["name_zh"], change["name_en"], field, shorten(old), shorten(new)])


def apply(connection: sqlite3.Connection, changes: list[dict]) -> None:
    if not changes:  # already repaired: keep the existing fixes file
        return
    for change in changes:
        diff = change["diff"]
        connection.execute(
            f"UPDATE spell_entries SET {','.join(f'{field}=?' for field in diff)} WHERE id=?",
            (*(new for _, new in diff.values()), change["entry_id"]),
        )
        if "description_zh" in diff:
            connection.execute("UPDATE spell_search SET description_zh=? WHERE spell_id=?", (diff["description_zh"][1], change["spell_id"]))
    FIXES.write_text(
        json.dumps({"id": FIX_ID, "entries": {c["entry_id"]: c["diff"] for c in changes}}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="update the seed database and write spellbook/seed_fixes.json")
    args = parser.parse_args()
    with closing(sqlite3.connect(SEED)) as connection:
        connection.row_factory = sqlite3.Row
        changes = compare(connection)
        if changes:  # keep the last report once the seed is already repaired
            write_report(changes)
        if args.apply:
            with connection:
                apply(connection, changes)
    fields = {}
    for change in changes:
        for field in change["diff"]:
            fields[field] = fields.get(field, 0) + 1
    print(f"{len(changes)} entries changed; per field: {fields}")
    print(f"report: {REPORT.relative_to(ROOT)}" + (f"; fixes: {FIXES.relative_to(ROOT)}" if args.apply else " (dry run)"))


if __name__ == "__main__":
    main()
