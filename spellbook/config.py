from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    database: Path
    source_pdf: Path
    baseline: Path
    events: Path
    export_json: Path
    reports: Path
    local_state: Path

    @classmethod
    def from_root(cls, root: Path | str) -> "ProjectPaths":
        root = Path(root).resolve()
        fallback = root / ".spellbook-local"
        local_root = Path(os.getenv("LOCALAPPDATA", str(fallback))) / "SpellbookReviewer"
        project_hash = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]
        return cls(
            root=root,
            database=root / "data" / "spellbook.sqlite",
            source_pdf=root / "spellbook_doc_v1.1.pdf",
            baseline=root / "data" / "baseline" / "spells-v1.json",
            events=root / "reviews" / "changes",
            export_json=root / "data" / "export" / "spells.json",
            reports=root / "reports",
            local_state=local_root / "projects" / project_hash,
        )


class EditorSettings:
    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self.path = paths.local_state / "settings.json"

    def git_value(self, key: str) -> str:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={self.paths.root.as_posix()}", "config", key],
            cwd=self.paths.root,
            text=True,
            capture_output=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def load(self) -> dict[str, str | bool]:
        saved = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        return {
            "editor_name": saved.get("editor_name") or self.git_value("user.name"),
            "confirmed": bool(saved.get("confirmed")),
            "git_email": self.git_value("user.email"),
        }

    def save(self, editor_name: str) -> None:
        editor_name = editor_name.strip()
        if not editor_name:
            raise ValueError("Editor name is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"editor_name": editor_name, "confirmed": True}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

