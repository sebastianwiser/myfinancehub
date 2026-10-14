from collections.abc import Iterator
from datetime import date

import pytest

from mainhub.core.sqlite_store import SqliteStore

TODAY = date(2026, 3, 15)


@pytest.fixture
def store() -> Iterator[SqliteStore]:
    s = SqliteStore(":memory:")
    yield s
    s.close()
