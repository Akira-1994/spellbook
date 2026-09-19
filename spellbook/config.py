from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
APP_DIR_NAME = "Spellbook"


def bundle_root() -> Path:
    """Directory holding bundled read-only data: the repo root in development,
    the unpacked app directory in the Nuitka build (data files keep their
    repo-relative paths there)."""
    return PACKAGE_ROOT.parent


def default_data_dir() -> Path:
    override = os.getenv("SPELLBOOK_DATA_DIR")
    if override:
        return Path(override)
    local = os.getenv("LOCALAPPDATA")
    if local:
        return Path(local) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


@dataclass(frozen=True)
class AppPaths:
    # Pristine extracted data shipped with the app. Never written; it is the
    # source for "restore original" and for creating the user's database.
    seed_database: Path
    # The user's own editable copy, kept outside the app so upgrades keep edits.
    data_dir: Path

    @property
    def database(self) -> Path:
        return self.data_dir / "spellbook.sqlite"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "spellbook.lock"

    @property
    def server_file(self) -> Path:
        return self.data_dir / "server.json"

    @classmethod
    def default(cls) -> "AppPaths":
        return cls(
            seed_database=bundle_root() / "data" / "spellbook.sqlite",
            data_dir=default_data_dir(),
        )
