from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from mainhub.core.sqlite_store import SqliteStore

TODAY = date(2026, 3, 15)


@pytest.fixture
def store() -> Iterator[SqliteStore]:
    s = SqliteStore(":memory:")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolated_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let tests read the developer's real .env."""
    monkeypatch.setenv("MAINHUB_ENV_FILE", str(tmp_path / "no.env"))
