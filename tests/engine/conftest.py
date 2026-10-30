from collections.abc import Iterator
from pathlib import Path

import pytest

from worst_route.config import settings


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh WORST_ROUTE_DATA for the test."""
    root = tmp_path / "data"
    monkeypatch.setenv("WORST_ROUTE_DATA", str(root))
    settings.cache_clear()
    yield root
    settings.cache_clear()
