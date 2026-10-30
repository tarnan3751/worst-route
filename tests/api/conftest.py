"""Fixtures for the API tests: anyio on asyncio for the async tests (`@pytest.mark.anyio`), and a
data directory holding two lattice builds. The helpers are in `support.py`."""

from pathlib import Path

import pytest

from .support import BIG, GRID, save_build


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def data(tmp_path: Path) -> Path:
    """A data directory: the 10 × 10 lattice as portland/grid (latest), 20 × 20 as portland/big."""
    root = tmp_path / "data"
    save_build(root, BIG, "big", latest=False)
    save_build(root, GRID, "grid")
    return root
