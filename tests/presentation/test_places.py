"""The places index on the fixture (`support.py`): what `write_places` keeps, the states and
towns a route passes through in route order, `aux places`, and the STATES table."""

import zipfile
from pathlib import Path

import geopandas as gpd
import pyarrow.parquet as pq
import pytest
import shapely
from typer.testing import CliRunner

from worst_route.cli import app
from worst_route.config import REGIONS, STATES
from worst_route.presentation import places
from worst_route.presentation.places import PlaceIndex, State, Town, route_line

from ..api.support import save_build
from .support import (
    EAST_THEN_NORTH,
    EAST_THEN_NORTH_EDGES,
    GRID,
    PLACES,
    STATES_FILE,
    write_fixture_places,
)

WESTLAND = State("91", "WL", "Westland")
EASTLAND = State("92", "EL", "Eastland")
WESTVILLE = Town("Westville", "25", "WL", "9100100")
MIDDLEFIELD = Town("Middlefield", "57", "EL", "9200200")
EASTBROOK = Town("Eastbrook", "43", "EL", "9200300")
FARAWAY = Town("Faraway", "47", "WL", "9100400")


@pytest.fixture
def index(tmp_path: Path) -> PlaceIndex:
    write_fixture_places(tmp_path)
    loaded = PlaceIndex.load("portland", tmp_path)
    assert loaded is not None
    return loaded


def test_the_index_holds_every_state_outline_and_place(index: PlaceIndex) -> None:
    assert index.states == (WESTLAND, EASTLAND)
    assert len(index.outlines) == 2
    assert index.towns == (WESTVILLE, FARAWAY, MIDDLEFIELD, EASTBROOK)  # by GEOID


def test_states_and_towns_come_in_route_order_cdp_included(index: PlaceIndex) -> None:
    line = route_line(GRID, EAST_THEN_NORTH_EDGES)
    assert len(shapely.get_coordinates(line)) == len(EAST_THEN_NORTH)
    assert index.states_crossed(line) == [WESTLAND, EASTLAND]
    assert index.towns_passed(line) == [WESTVILLE, MIDDLEFIELD, EASTBROOK]
    back = shapely.reverse(line)
    assert index.states_crossed(back) == [EASTLAND, WESTLAND]
    assert index.towns_passed(back) == [EASTBROOK, MIDDLEFIELD, WESTVILLE]


def test_the_polygons_are_prepared_and_answer_as_unprepared_ones(
    index: PlaceIndex, tmp_path: Path
) -> None:
    assert shapely.is_prepared(index._states[0]).all()
    assert shapely.is_prepared(index._towns[0]).all()
    plain = PlaceIndex.load("portland", tmp_path)
    assert plain is not None
    shapely.destroy_prepared(plain._states[0])
    shapely.destroy_prepared(plain._towns[0])
    line = route_line(GRID, EAST_THEN_NORTH_EDGES)
    for route in (line, shapely.reverse(line)):
        assert index.states_crossed(route) == plain.states_crossed(route)
        assert index.towns_passed(route) == plain.towns_passed(route)


def test_the_first_meeting_decides_even_on_a_route_that_comes_back(index: PlaceIndex) -> None:
    # East through Middlefield, north to Eastbrook, then back west through Middlefield's
    # latitude band north of it and down into Westville: each is listed once, where first met.
    line = shapely.LineString(
        [
            (-122.6745, 45.514),
            (-122.668, 45.514),
            (-122.668, 45.520),
            (-122.680, 45.520),
            (-122.680, 45.513),
        ]
    )
    assert index.towns_passed(line) == [MIDDLEFIELD, EASTBROOK, WESTVILLE]
    assert index.states_crossed(line) == [WESTLAND, EASTLAND]  # starts in Westland


def test_a_point_repeated_is_met_where_it_is(index: PlaceIndex) -> None:
    # A zero-length segment first: its offset along itself is 0, never an error.
    a, b = (-122.680, 45.514), (-122.671, 45.514)  # in Westville; in Middlefield
    assert index.towns_passed(shapely.LineString([a, a, b])) == [WESTVILLE, MIDDLEFIELD]
    assert index.states_crossed(shapely.LineString([a, a, b])) == [WESTLAND, EASTLAND]
    assert index.towns_passed(shapely.LineString([a, a])) == [WESTVILLE]
    edge = (-122.673, 45.514)  # on Middlefield's west edge
    assert index.towns_passed(shapely.LineString([edge, edge, b])) == [MIDDLEFIELD]


def test_a_route_touching_nothing_passes_nothing(index: PlaceIndex) -> None:
    line = shapely.LineString([(-100.0, 40.0), (-100.1, 40.1), (-100.2, 40.0)])
    assert index.states_crossed(line) == []
    assert index.towns_passed(line) == []


def test_a_touch_counts(index: PlaceIndex) -> None:
    # Along Middlefield's west edge (-122.673) from south of it to its south-west corner.
    line = shapely.LineString([(-122.673, 45.505), (-122.673, 45.512)])
    assert index.towns_passed(line) == [MIDDLEFIELD]


def test_places_outside_the_bbox_are_left_out(tmp_path: Path) -> None:
    written = write_fixture_places(tmp_path, bbox=(-122.69, 45.50, -122.66, 45.53))
    assert written.by_state == {"EL": 2, "WL": 1}
    index = PlaceIndex.load("portland", tmp_path)
    assert index is not None and FARAWAY not in index.towns


def test_a_state_the_bbox_meets_without_its_places_file_is_missing(tmp_path: Path) -> None:
    written = write_fixture_places(tmp_path, states=("91",))
    assert written.missing == [EASTLAND]
    assert written.by_state == {"WL": 2}
    written = write_fixture_places(tmp_path, bbox=(-122.70, 45.50, -122.68, 45.54), states=("91",))
    assert written.missing == []  # the bbox stays in Westland


def test_without_a_bbox_the_region_s_state_bounds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lattice = REGIONS["portland"].model_copy(update={"name": "lattice", "state": "westland"})
    monkeypatch.setitem(places.REGIONS, "lattice", lattice)
    monkeypatch.setitem(places.STATES, "westland", ("WL", "91"))
    written = write_fixture_places(tmp_path, bbox=None, states=("91",), region="lattice")
    assert written.by_state == {"WL": 2}  # Middlefield and Eastbrook lie east of Westland
    assert written.missing == [EASTLAND]  # Westland's bounds touch Eastland along the border


def test_the_files_have_the_documented_columns(tmp_path: Path) -> None:
    written = write_fixture_places(tmp_path)
    for path, columns in [
        (written.states, ["STATEFP", "STUSPS", "NAME", "geometry"]),
        (written.outlines, ["STATEFP", "STUSPS", "NAME", "geometry"]),
        (written.places, ["GEOID", "NAME", "NAMELSAD", "LSAD", "STUSPS", "geometry"]),
    ]:
        assert pq.read_schema(path).names == columns
        assert gpd.read_parquet(path).crs.to_epsg() == 4326
    towns = gpd.read_parquet(written.places).set_index("GEOID")
    assert towns.loc["9200200", "NAMELSAD"] == "Middlefield CDP"


def test_a_missing_file_gives_no_index(tmp_path: Path) -> None:
    write_fixture_places(tmp_path)
    assert PlaceIndex.load("elsewhere", tmp_path) is None
    (tmp_path / "aux" / "places" / "states_outline.parquet").unlink()
    assert PlaceIndex.load("portland", tmp_path) is None


def _census_zips(directory: Path) -> None:
    """The fixture as the Census ships it: zipped shapefiles under the Census's names."""
    directory.mkdir(parents=True)
    sources = {
        "us_state_5m": STATES_FILE,
        "us_state_20m": STATES_FILE,
        "91_place_500k": PLACES["91"],
    }
    for stem, source in sources.items():
        name = f"cb_2025_{stem}"
        shapes = directory / name
        shapes.mkdir()
        gpd.read_file(source).to_file(shapes / f"{name}.shp")
        with zipfile.ZipFile(directory / f"{name}.zip", "w") as zipped:
            for part in sorted(shapes.iterdir()):
                zipped.write(part, part.name)


def test_aux_places_writes_the_index_from_the_census_zips(data_dir: Path, tmp_path: Path) -> None:
    census = tmp_path / "census"
    _census_zips(census)
    # The latest build's bbox leaves Faraway out.
    save_build(data_dir, GRID, "grid", bbox=[-122.69, 45.50, -122.66, 45.53])
    result = CliRunner().invoke(
        app, ["aux", "places", "--region", "portland", "--census-dir", str(census)]
    )
    assert result.exit_code == 0, result.output
    out = data_dir / "aux" / "places"
    assert result.stdout.splitlines() == [
        f"wrote {out / 'states.parquet'} (2 states)",
        f"wrote {out / 'states_outline.parquet'} (2 outlines)",
        f"wrote {out / 'portland.parquet'} (1 places: WL 1)",
    ]
    assert result.stderr == "missing places for EL: run tools/fetch_census.sh 92\n"
    index = PlaceIndex.load("portland")
    assert index is not None
    assert index.states == (WESTLAND, EASTLAND) and index.towns == (WESTVILLE,)


def test_aux_places_without_the_state_files_is_one_line(data_dir: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["aux", "places", "--region", "portland", "--census-dir", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert result.stderr == (
        f"missing input {tmp_path / 'cb_2025_us_state_5m.zip'}: run `tools/fetch_census.sh` first\n"
    )


def test_states_lists_the_50_states_and_dc() -> None:
    assert len(STATES) == 51
    assert len({usps for usps, _ in STATES.values()}) == len({f for _, f in STATES.values()}) == 51
    assert all(len(fips) == 2 and fips.isdigit() for _, fips in STATES.values())
    assert STATES["oregon"] == ("OR", "41") and STATES["district-of-columbia"] == ("DC", "11")
    assert all(region.state in STATES for region in REGIONS.values())
