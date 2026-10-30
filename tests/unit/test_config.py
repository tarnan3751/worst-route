from collections.abc import Iterator
from pathlib import Path

import pytest

from worst_route.config import (
    ALLEY_SERVICE,
    KEPT_HIGHWAY,
    REGIONS,
    HighwayClass,
    Settings,
    settings,
)


@pytest.fixture(autouse=True)
def fresh_settings() -> Iterator[None]:
    settings.cache_clear()
    yield
    settings.cache_clear()


def test_regions_present() -> None:
    portland = REGIONS["portland"]
    assert portland.name == "portland"
    assert portland.state == "oregon"
    assert portland.bbox == (-123.05, 45.25, -122.35, 45.72)
    assert portland.crs_epsg == 32610

    oregon = REGIONS["oregon"]
    assert oregon.name == "oregon"
    assert oregon.state == "oregon"
    assert oregon.bbox is None
    assert oregon.crs_epsg == 32610


def test_data_dir_defaults_to_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORST_ROUTE_DATA", raising=False)
    assert settings().data_dir == Path("data")


def test_data_dir_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path))
    assert settings().data_dir == tmp_path


def test_data_dir_by_field_name() -> None:
    assert Settings(data_dir=Path("/x")).data_dir == Path("/x")


def test_settings_cached_until_cleared(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path / "first"))
    first = settings()
    monkeypatch.setenv("WORST_ROUTE_DATA", str(tmp_path / "second"))
    assert settings() is first
    settings.cache_clear()
    assert settings().data_dir == tmp_path / "second"


def test_user_agent_names_project_and_contact() -> None:
    assert settings().user_agent == "worst-route/0.1.0 (https://github.com/tarnan3751/worst-route)"


def test_highway_classes_are_the_kept_classes() -> None:
    # The format's class numbers and the pipeline's class strings name the same classes.
    assert {c.name.lower() for c in HighwayClass} == KEPT_HIGHWAY | {ALLEY_SERVICE}
