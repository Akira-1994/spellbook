from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class GitRepository:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={self.root.as_posix()}", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode:
            raise GitError(result.stderr.strip() or result.stdout.strip() or "Git 操作失敗")
        return result

    def identity(self) -> dict[str, str]:
        return {
            "name": self._run("config", "user.name", check=False).stdout.strip(),
            "email": self._run("config", "user.email", check=False).stdout.strip(),
        }

    def branch(self) -> str:
        return self._run("branch", "--show-current").stdout.strip() or "detached HEAD"

    def staged(self) -> list[str]:
        return [line for line in self._run("diff", "--cached", "--name-only").stdout.splitlines() if line]

    def changed(self) -> list[str]:
        result = self._run("status", "--porcelain=v1", "--untracked-files=all")
        return [line[3:].replace("\\", "/") for line in result.stdout.splitlines() if len(line) > 3]

    def operation_in_progress(self) -> bool:
        for name in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD"):
            path = self._run("rev-parse", "--git-path", name).stdout.strip()
            if path and (self.root / path).exists():
                return True
        return False

    def stage_and_commit(self, paths: list[str], message: str) -> str:
        if not paths:
            raise GitError("沒有可提交的校對檔案")
        self._run("add", "--", *paths)
        try:
            self._run("commit", "-m", message)
        except Exception:
            self._run("reset", "--", *paths, check=False)
            raise
        return self._run("rev-parse", "HEAD").stdout.strip()
