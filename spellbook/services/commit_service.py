from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.export_json import export_database
from spellbook.config import ProjectPaths
from spellbook.repositories.git_repository import GitError, GitRepository


def _allowed(path: str) -> bool:
    return (
        path == "data/spellbook.sqlite"
        or path == "data/export/spells.json"
        or path.startswith("reviews/changes/") and path.endswith(".json")
        or path.startswith("reports/")
    )


class CommitService:
    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self.git = GitRepository(paths.root)

    def preview(self) -> dict[str, object]:
        changed = self.git.changed()
        allowed = sorted(path for path in changed if _allowed(path))
        events = [path for path in allowed if path.startswith("reviews/changes/")]
        return {
            "branch": self.git.branch(),
            "identity": self.git.identity(),
            "files": allowed,
            "event_count": len(events),
            "other_changes": sorted(path for path in changed if path not in allowed),
            "staged": self.git.staged(),
            "operation_in_progress": self.git.operation_in_progress(),
        }

    def commit(self) -> dict[str, object]:
        before = self.preview()
        if before["operation_in_progress"]:
            raise GitError("Git 正在合併、rebase 或 cherry-pick，請先完成該操作")
        if before["staged"]:
            raise GitError("暫存區已有其他檔案；為避免混入，本工具不會建立 commit")
        identity = before["identity"]
        if not identity["name"] or not identity["email"]:
            raise GitError("請先設定 Git user.name 與 user.email")

        export_database(self.paths.database, self.paths.export_json)
        self._write_review_summary()
        with sqlite3.connect(self.paths.database) as connection:
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise GitError("資料庫外鍵驗證失敗")
            if connection.execute("SELECT COUNT(*) FROM spells WHERE record_status='active'").fetchone()[0] != 2360:
                raise GitError("有效法術筆數不是預期的 2,360 筆")

        ready = self.preview()
        files = list(ready["files"])
        count = int(ready["event_count"])
        commit_hash = self.git.stage_and_commit(files, f"Review {count} spell entries")
        return {"commit_hash": commit_hash, "event_count": count, "files": files}

    def _write_review_summary(self) -> None:
        self.paths.reports.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.paths.database) as connection:
            rows = connection.execute(
                "SELECT review_status,COUNT(*) FROM spells WHERE record_status='active' GROUP BY review_status"
            ).fetchall()
        payload = {"spell_count": 2360, "review_counts": dict(rows)}
        (self.paths.reports / "review-summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
