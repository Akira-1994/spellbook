from __future__ import annotations

import os
from pathlib import Path

from spellbook_reviewer.domain.events import ReviewEvent


class EventRepository:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def prepare(self, event: ReviewEvent) -> tuple[Path, Path]:
        self.directory.mkdir(parents=True, exist_ok=True)
        final = self.directory / f"{event.event_id}.json"
        temporary = self.directory / f".{event.event_id}.json.tmp"
        if final.exists():
            raise FileExistsError(final)
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(event.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        return temporary, final

    @staticmethod
    def finalize(temporary: Path, final: Path) -> None:
        os.replace(temporary, final)

    def events(self) -> list[ReviewEvent]:
        if not self.directory.exists():
            return []
        return [ReviewEvent.read(path) for path in sorted(self.directory.glob("rev_*.json"))]

