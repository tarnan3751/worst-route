from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest

from worst_route.config import settings


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline tests never reach the network: a real fetch fails the test."""

    def refuse(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError(f"network access in a test: {args}")

    monkeypatch.setattr(httpx, "stream", refuse)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh WORST_ROUTE_DATA for the test."""
    root = tmp_path / "data"
    monkeypatch.setenv("WORST_ROUTE_DATA", str(root))
    settings.cache_clear()
    yield root
    settings.cache_clear()
