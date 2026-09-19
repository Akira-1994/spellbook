from pathlib import Path

import pytest

from spellbook.config import AppPaths


SEED = Path(__file__).resolve().parents[1] / "data" / "spellbook.sqlite"


@pytest.fixture
def paths(tmp_path) -> AppPaths:
    return AppPaths(seed_database=SEED, data_dir=tmp_path / "userdata")
