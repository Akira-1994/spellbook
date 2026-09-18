from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_baseline(export_path: Path, pdf_path: Path) -> dict:
    dataset = json.loads(export_path.read_text(encoding="utf-8"))
    if dataset.get("spell_count") != len(dataset.get("spells", [])):
        raise ValueError("Export spell count does not match the spell array")
    if dataset.get("spell_count") != 2360:
        raise ValueError(f"Expected 2360 spells, found {dataset.get('spell_count')}")
    ids = [spell.get("id", "") for spell in dataset["spells"]]
    if len(ids) != len(set(ids)) or any(not value.startswith("spl_") for value in ids):
        raise ValueError("Baseline contains invalid or duplicate spell IDs")
    return {
        "baseline_schema_version": 1,
        "source_pdf": pdf_path.name,
        "source_pdf_sha256": sha256_file(pdf_path),
        "source_export_sha256": sha256_file(export_path),
        "dataset": dataset,
    }


def stable_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the immutable review baseline.")
    parser.add_argument("--export", type=Path, default=ROOT / "data" / "export" / "spells.json")
    parser.add_argument("--pdf", type=Path, default=ROOT / "spellbook_doc_v1.1.pdf")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "baseline" / "spells-v1.json")
    args = parser.parse_args()

    content = stable_json(build_baseline(args.export, args.pdf))
    if args.output.exists():
        if args.output.read_text(encoding="utf-8") != content:
            raise SystemExit(f"Refusing to overwrite a different baseline: {args.output}")
        print(f"Baseline already current: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"Created immutable baseline: {args.output}")


if __name__ == "__main__":
    main()
