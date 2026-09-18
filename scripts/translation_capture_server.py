from __future__ import annotations

import argparse
import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scripts.cloud_translate_entries import NAME_OVERRIDES, normalize_translation
from scripts.spellbook_lib import load_entries


ROOT = Path(__file__).resolve().parents[1]
FIELD_KEYS = (
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
    "description_en",
)
MARKER_RE = re.compile(r"@@S(\d{2})F(\d{2})@@")


def make_batches(entries: list, limit: int = 4700) -> list[list]:
    batches: list[list] = []
    current: list = []
    current_length = 0
    for entry in entries:
        estimate = sum(len(str(getattr(entry, key) or "")) + 14 for key in FIELD_KEYS)
        if current and current_length + estimate > limit:
            batches.append(current)
            current = []
            current_length = 0
        current.append(entry)
        current_length += estimate
    if current:
        batches.append(current)
    return batches


def source_text(batch: list) -> str:
    parts: list[str] = []
    for spell_index, entry in enumerate(batch):
        for field_index, key in enumerate(FIELD_KEYS):
            parts.append(f"@@S{spell_index:02d}F{field_index:02d}@@\n{getattr(entry, key) or ''}")
    return "\n".join(parts)


def parse_translation(value: str) -> dict[tuple[int, int], str]:
    matches = list(MARKER_RE.finditer(value))
    parsed: dict[tuple[int, int], str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        parsed[(int(match.group(1)), int(match.group(2)))] = normalize_translation(
            value[match.end() : end]
        )
    return parsed


class CaptureHandler(BaseHTTPRequestHandler):
    batches: list[list]
    catalog_path: Path

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        batch_index = int(query.get("batch", ["0"])[0])
        if batch_index >= len(self.batches):
            self.send_html(
                "<!doctype html><meta charset='utf-8'><title>完成</title>"
                f"<h1 id='done'>全部完成：{len(self.batches)} 批</h1>"
            )
            return
        batch = self.batches[batch_index]
        names = "、".join(entry.name_en for entry in batch)
        source = html.escape(source_text(batch))
        self.send_html(
            "<!doctype html><meta charset='utf-8'><title>翻譯擷取</title>"
            f"<h1>批次 {batch_index + 1}/{len(self.batches)}</h1>"
            f"<p id='names'>{html.escape(names)}</p>"
            f"<textarea id='source' readonly rows='8' cols='100'>{source}</textarea>"
            f"<form method='post' action='/capture?batch={batch_index}'>"
            "<textarea id='translation' name='translation' rows='8' cols='100'></textarea>"
            "<button id='save' type='submit'>儲存並前往下一批</button></form>"
        )

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)
        batch_index = int(query.get("batch", ["0"])[0])
        if batch_index >= len(self.batches):
            self.send_html("<h1>無效批次</h1>", 400)
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        translated = parse_translation(form.get("translation", [""])[0])
        batch = self.batches[batch_index]
        expected = len(batch) * len(FIELD_KEYS)
        if len(translated) != expected:
            self.send_html(
                f"<h1 id='error'>標記數不符：{len(translated)} / {expected}</h1>", 400
            )
            return
        catalog = (
            json.loads(self.catalog_path.read_text(encoding="utf-8"))
            if self.catalog_path.exists()
            else {}
        )
        for spell_index, entry in enumerate(batch):
            item = catalog.get(entry.name_en, {})
            item["name_zh"] = entry.name_zh or NAME_OVERRIDES[entry.name_en]
            for field_index, key in enumerate(FIELD_KEYS):
                value = translated[(spell_index, field_index)]
                if key == "description_en":
                    item["description_zh"] = value
                elif value and value != (getattr(entry, key) or ""):
                    item[key + "_zh"] = value
            item["provider"] = "google-web"
            item["needs_review"] = True
            catalog[entry.name_en] = item
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_path.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.send_response(303)
        self.send_header("Location", f"/?batch={batch_index + 1}")
        self.end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture marked Google Translate batches locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--input", type=Path, default=ROOT / "tmp" / "pdfs" / "az-parsed.json")
    parser.add_argument(
        "--catalog", type=Path, default=ROOT / "data" / "translations.generated.json"
    )
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8")) if args.catalog.exists() else {}
    remaining = [
        entry
        for entry in load_entries(args.input)
        if entry.needs_translation and entry.name_en not in catalog
    ]
    CaptureHandler.batches = make_batches(remaining)
    CaptureHandler.catalog_path = args.catalog
    server = ThreadingHTTPServer((args.host, args.port), CaptureHandler)
    print(
        json.dumps(
            {"remaining": len(remaining), "batches": len(CaptureHandler.batches), "url": f"http://{args.host}:{args.port}/"},
            ensure_ascii=False,
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
