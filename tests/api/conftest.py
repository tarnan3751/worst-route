"""Fixtures for the API tests: anyio on asyncio for the async tests (`@pytest.mark.anyio`), a
data directory holding two lattice builds, and one holding two regions' builds. The helpers are in
`support.py`."""

from pathlib import Path

import pytest

from worst_route.config import REGIONS, STATES, Region

from .support import BIG, GRID, GRID_BBOX, save_build


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def data(tmp_path: Path) -> Path:
    """A data directory: the 10 × 10 lattice as portland/grid (latest), its bbox about 10 km
    around it, and the 20 × 20 one as portland/big."""
    root = tmp_path / "data"
    save_build(root, BIG, "big", latest=False)
    save_build(root, GRID, "grid", bbox=GRID_BBOX)
    return root


@pytest.fixture
def lands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data directory with two regions over the census fixture's two states (`config.REGIONS`
    and `STATES` given them for the test): `westland`, Westland (91) alone, whose build is the
    10 × 10 lattice (its west half in Westland, its east half in Eastland), and `lands`,
    Westland and Eastland (92), the 20 × 20 one; no places index (`write_fixture_places`)."""
    monkeypatch.setitem(STATES, "westland", ("WL", "91"))
    monkeypatch.setitem(STATES, "eastland", ("EL", "92"))
    monkeypatch.setitem(
        REGIONS, "westland", Region(name="westland", states=("westland",), crs_epsg=32610)
    )
    monkeypatch.setitem(
        REGIONS, "lands", Region(name="lands", states=("westland", "eastland"), crs_epsg=32610)
    )
    root = tmp_path / "data"
    save_build(root, GRID, "w1", region="westland")
    save_build(root, BIG, "l1", region="lands")
    return root
