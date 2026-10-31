from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from worst_route.config import (
    ALLEY_SERVICE,
    KEPT_HIGHWAY,
    REGIONS,
    STATES,
    HighwayClass,
    Region,
    Settings,
    display_name,
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
    assert portland.states == ("oregon",)
    assert portland.bbox == (-123.05, 45.25, -122.35, 45.72)
    assert portland.crs_epsg == 32610

    oregon = REGIONS["oregon"]
    assert oregon.name == "oregon"
    assert oregon.states == ("oregon",)
    assert oregon.bbox is None
    assert oregon.crs_epsg == 32610


def test_a_region_of_two_states() -> None:
    region = REGIONS["oregon-washington"]
    assert region.name == "oregon-washington"
    assert region.states == ("oregon", "washington")
    assert region.bbox is None
    assert region.crs_epsg == 32610


def test_the_national_t1_region() -> None:
    region = REGIONS["us-t1"]
    assert len(region.states) == 49 and len(set(region.states)) == 49
    assert "alaska" not in region.states and "hawaii" not in region.states
    assert "district-of-columbia" in region.states
    assert region.bbox is None and region.crs_epsg == 5070
    assert region.classes == "T1"
    assert region.road_classes == {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
    }
    assert REGIONS["oregon-washington-t1"].road_classes == region.road_classes
    assert REGIONS["oregon-washington"].road_classes is None


def test_the_all_roads_national_region_and_its_intermediate_scale() -> None:
    us, west = REGIONS["us"], REGIONS["west-coast"]
    assert us.states == REGIONS["us-t1"].states and us.classes is None
    assert us.road_classes is None and us.bbox is None and us.crs_epsg == 5070
    assert west.states == ("california", "oregon", "washington")
    assert west.classes is None and west.bbox is None and west.crs_epsg == 5070


def test_every_contiguous_state_and_dc_has_an_all_roads_region() -> None:
    conus = [state for state in STATES if state not in ("alaska", "hawaii")]
    assert len(conus) == 49 and "district-of-columbia" in conus
    for state in conus:
        region = REGIONS[state]
        assert region.name == state and region.states == (state,)
        assert region.bbox is None and region.classes is None
        assert region.crs_epsg == (32610 if state == "oregon" else 5070)  # Oregon keeps its own
        assert region.display == display_name(state) and region.fips == {STATES[state][1]}
    assert "alaska" not in REGIONS and "hawaii" not in REGIONS
    for name in ("portland", "us-t1", "us", "west-coast", "oregon-washington"):
        assert name in REGIONS
    assert REGIONS["oregon-washington-t1"].classes == "T1"


def test_a_region_names_a_state() -> None:
    with pytest.raises(ValidationError):
        Region(name="nowhere", states=(), crs_epsg=32610)


def test_a_region_s_display_name_and_states() -> None:
    generated = {name for name in STATES if name != "oregon"}  # the state regions, named by default
    names = {name: region.display for name, region in REGIONS.items() if name not in generated}
    assert names == {
        "portland": "Portland",
        "oregon": "Oregon",
        "oregon-washington": "Oregon and Washington",
        "oregon-washington-t1": "Oregon and Washington",
        "us-t1": "United States",
        "us": "United States",
        "west-coast": "West Coast",
    }
    # By default its name as words, "of" and "and" left lowercase after the first.
    assert (
        Region(name="district-of-columbia", states=("district-of-columbia",), crs_epsg=1).display
        == "District of Columbia"
    )
    assert Region(name="new-york", states=("new-york",), crs_epsg=1).display == "New York"
    assert REGIONS["oregon"].fips == frozenset({"41"}) and len(REGIONS["us-t1"].fips) == 49
    assert REGIONS["portland"].fips == frozenset({"41"})  # clipped to its bbox all the same


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


def test_contact_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORST_ROUTE_CONTACT", raising=False)
    assert settings().contact is None
    settings.cache_clear()
    monkeypatch.setenv("WORST_ROUTE_CONTACT", "ops@example.org")
    assert settings().contact == "ops@example.org"


def test_highway_classes_are_the_kept_classes() -> None:
    # The format's class numbers and the pipeline's class strings name the same classes.
    assert {c.name.lower() for c in HighwayClass} == KEPT_HIGHWAY | {ALLEY_SERVICE}
