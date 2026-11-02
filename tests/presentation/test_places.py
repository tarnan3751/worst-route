"""The places index on the fixture (`support.py`): what `write_places` keeps and its files, the
states and towns a route passes through in route order, the state each point lies in, the indexes
it refuses, `aux places`, and the STATES table."""

import json
import zipfile
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import shapely
from typer.testing import CliRunner

from worst_route.cli import app
from worst_route.config import REGIONS, STATES
from worst_route.graph.geometry import straight
from worst_route.presentation import places
from worst_route.presentation.places import (
    PlaceIndex,
    PlacesFormatError,
    State,
    Town,
    route_line,
    table_files,
)

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
    line = route_line(GRID, straight(GRID.m), EAST_THEN_NORTH_EDGES)
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
    line = route_line(GRID, straight(GRID.m), EAST_THEN_NORTH_EDGES)
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


def test_each_point_lies_in_its_state_or_in_none(index: PlaceIndex) -> None:
    points = np.array(
        [
            (-122.68, 45.515),  # Westland
            (-122.65, 45.53),  # Eastland
            (-122.674, 45.52),  # on the border: the first state, by STATEFP
            (-122.7, 45.5),  # Westland's south-west corner: a touch counts
            (-100.0, 40.0),  # in neither
        ]
    )
    assert index.states_at(points) == [WESTLAND, EASTLAND, WESTLAND, WESTLAND, None]
    assert index.states_at(np.empty((0, 2))) == []


def test_each_town_has_a_point_inside_it(index: PlaceIndex) -> None:
    points = index.town_points()
    assert [(p.name, p.usps, p.lsad) for p in points] == [
        (t.name, t.usps, t.lsad) for t in index.towns
    ]
    squares = {  # the fixture's polygons: (west, south, east, north)
        "Westville": (-122.685, 45.512, -122.676, 45.517),
        "Faraway": (-122.699, 45.535, -122.695, 45.539),
        "Middlefield": (-122.673, 45.512, -122.67, 45.517),
        "Eastbrook": (-122.669, 45.518, -122.664, 45.523),
    }
    for p in points:
        west, south, east, north = squares[p.name]
        assert west < p.lon < east and south < p.lat < north
    westville = shapely.point_on_surface(shapely.box(*squares["Westville"]))
    assert (points[0].lon, points[0].lat) == (westville.x, westville.y)
    assert index.town_points() is points  # worked out once


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
    lattice = REGIONS["portland"].model_copy(update={"name": "lattice", "states": ("westland",)})
    monkeypatch.setitem(places.REGIONS, "lattice", lattice)
    monkeypatch.setitem(places.STATES, "westland", ("WL", "91"))
    written = write_fixture_places(tmp_path, bbox=None, states=("91",), region="lattice")
    assert written.by_state == {"WL": 2}  # Middlefield and Eastbrook lie east of Westland
    assert written.missing == [EASTLAND]  # Westland's bounds touch Eastland along the border


def test_without_a_bbox_a_region_of_two_states_takes_both_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    both = REGIONS["portland"].model_copy(
        update={"name": "both", "states": ("westland", "eastland")}
    )
    monkeypatch.setitem(places.REGIONS, "both", both)
    monkeypatch.setitem(places.STATES, "westland", ("WL", "91"))
    monkeypatch.setitem(places.STATES, "eastland", ("EL", "92"))
    written = write_fixture_places(tmp_path, bbox=None, region="both")
    assert written.by_state == {"EL": 2, "WL": 2}
    assert written.missing == []


def test_the_files_have_the_documented_columns(tmp_path: Path) -> None:
    written = write_fixture_places(tmp_path)
    out = tmp_path / "aux" / "places"
    for table, path, columns, rows in [
        ("states", written.states, ["STATEFP", "STUSPS", "NAME"], 2),
        ("states_outline", written.outlines, ["STATEFP", "STUSPS", "NAME"], 2),
        ("portland", written.places, ["GEOID", "NAME", "NAMELSAD", "LSAD", "STUSPS"], 4),
    ]:
        names, wkb, offsets = table_files(out, table)
        assert path == names == out / f"{table}.places.json"
        assert wkb == out / f"{table}.places.npy"
        assert offsets == out / f"{table}.places.offsets.npy"
        table_columns = json.loads(names.read_text())
        assert list(table_columns) == columns
        assert all(len(v) == rows for v in table_columns.values())
        blob, starts = np.load(wkb), np.load(offsets)
        assert blob.dtype == np.uint8 and blob.ndim == 1
        assert starts.dtype == np.int64 and starts.shape == (rows + 1,)
        assert starts[0] == 0 and starts[-1] == len(blob)
        shapes = shapely.from_wkb([blob[a:b].tobytes() for a, b in pairwise(starts)])
        assert shapely.get_type_id(shapes).tolist() == [3] * rows  # polygons
    towns = json.loads(written.places.read_text())
    assert towns["GEOID"] == ["9100100", "9100400", "9200200", "9200300"]
    assert towns["NAMELSAD"][2] == "Middlefield CDP"
    manifest = json.loads((out / "places.json").read_text())
    assert manifest == {"format": 2, "regions": ["portland"]}


def test_the_polygons_are_the_files_polygons(tmp_path: Path) -> None:
    write_fixture_places(tmp_path)
    index = PlaceIndex.load("portland", tmp_path)
    assert index is not None
    towns = gpd.read_file(PLACES["91"]).set_index("GEOID")
    assert shapely.equals_exact(index._towns[0][0], towns.loc["9100100"].geometry, tolerance=0)
    states = gpd.read_file(STATES_FILE).sort_values("STATEFP")
    assert shapely.equals_exact(index._states[0], states.geometry.to_numpy(), tolerance=0).all()
    assert shapely.equals_exact(index.outlines, states.geometry.to_numpy(), tolerance=0).all()


def test_the_manifest_lists_every_region_written(tmp_path: Path) -> None:
    write_fixture_places(tmp_path)
    write_fixture_places(tmp_path, region="lands")
    write_fixture_places(tmp_path, bbox=(-122.69, 45.50, -122.66, 45.53))
    manifest = tmp_path / "aux" / "places" / "places.json"
    assert json.loads(manifest.read_text()) == {"format": 2, "regions": ["lands", "portland"]}
    lands, portland = PlaceIndex.load("lands", tmp_path), PlaceIndex.load("portland", tmp_path)
    assert lands is not None and portland is not None
    assert len(lands.towns) == 4 and len(portland.towns) == 3


def test_a_missing_file_gives_no_index(tmp_path: Path) -> None:
    write_fixture_places(tmp_path)
    assert PlaceIndex.load("elsewhere", tmp_path) is None
    (tmp_path / "aux" / "places" / "states_outline.places.offsets.npy").unlink()
    assert PlaceIndex.load("portland", tmp_path) is None
    assert PlaceIndex.load("portland", tmp_path / "nothing") is None


def test_an_index_this_version_cannot_read_is_refused(tmp_path: Path) -> None:
    write_fixture_places(tmp_path)
    out = tmp_path / "aux" / "places"
    (out / "oregon.parquet").write_bytes(b"PAR1")  # format 1's
    with pytest.raises(PlacesFormatError) as refused:
        PlaceIndex.load("oregon", tmp_path)
    assert str(refused.value) == (
        f"the places index of oregon in {out} is at format 1 (parquet), and this version reads "
        "format 2: run `worst-route aux places --region oregon` again"
    )
    (out / "places.json").unlink()  # format 2's files without the manifest
    with pytest.raises(PlacesFormatError, match="portland .* is not listed in places.json"):
        PlaceIndex.load("portland", tmp_path)
    (out / "places.json").write_text('{"format": 3, "regions": ["portland"]}')
    with pytest.raises(PlacesFormatError, match="portland .* is at format 3, and"):
        PlaceIndex.load("portland", tmp_path)
    for name in out.iterdir():
        name.unlink()
    (out / "portland.parquet").write_bytes(b"PAR1")  # format 1 alone, as it was written
    with pytest.raises(PlacesFormatError, match="at format 1 .*--region portland` again$"):
        PlaceIndex.load("portland", tmp_path)


OFFSET_DAMAGE: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "one offset short": lambda starts: starts[:-1],
    "the last offset past the polygons": lambda starts: np.append(starts[:-1], starts[-1] + 1),
    "the first offset not 0": lambda starts: np.insert(starts[1:], 0, 1),
    "offsets going back": lambda starts: starts[[0, 2, 1, *range(3, len(starts))]],
    "offsets as floats": lambda starts: starts.astype(np.float64),
}


@pytest.mark.parametrize("table", ["states", "states_outline", "portland"])
@pytest.mark.parametrize("damage", [*OFFSET_DAMAGE, "a column short"])
def test_a_table_whose_files_disagree_is_refused(tmp_path: Path, table: str, damage: str) -> None:
    write_fixture_places(tmp_path)
    out = tmp_path / "aux" / "places"
    names, _, offsets = table_files(out, table)
    if damage == "a column short":
        columns = json.loads(names.read_text())
        columns["NAME"] = columns["NAME"][:-1]
        names.write_text(json.dumps(columns))
    else:
        np.save(offsets, OFFSET_DAMAGE[damage](np.load(offsets)))
    with pytest.raises(PlacesFormatError) as refused:
        PlaceIndex.load("portland", tmp_path)
    assert str(refused.value) == (
        f"the places index of portland in {out} has a table, {table}, whose three files "
        "disagree: run `worst-route aux places --region portland` again"
    )


@pytest.mark.parametrize(
    "text", ["", "not json", "[]", '"places"', '{"regions": ["portland"]}', '{"format": 2}']
)
def test_a_malformed_manifest_is_refused_by_the_reader_and_the_writer(
    tmp_path: Path, text: str
) -> None:
    write_fixture_places(tmp_path)
    out = tmp_path / "aux" / "places"
    (out / "places.json").write_text(text)
    message = (
        f"{out / 'places.json'} is malformed: delete it, then run `worst-route aux places --region "
        "portland` again, and for each other region whose index is there"
    )
    with pytest.raises(PlacesFormatError) as refused:
        PlaceIndex.load("portland", tmp_path)
    assert str(refused.value) == message
    files = {path.name: path.read_bytes() for path in out.iterdir()}
    with pytest.raises(PlacesFormatError) as refused:
        write_fixture_places(tmp_path, bbox=(-122.69, 45.50, -122.66, 45.53))
    assert str(refused.value) == message
    assert {path.name: path.read_bytes() for path in out.iterdir()} == files  # nothing written


def test_a_region_without_a_town_loads_and_answers(tmp_path: Path) -> None:
    written = write_fixture_places(tmp_path, bbox=(-100.0, 40.0, -99.9, 40.1))  # far from both
    assert written.by_state == {} and written.missing == []
    index = PlaceIndex.load("portland", tmp_path)
    assert index is not None
    assert index.states == (WESTLAND, EASTLAND) and len(index.outlines) == 2
    assert index.towns == () and index.town_points() == ()
    line = route_line(GRID, straight(GRID.m), EAST_THEN_NORTH_EDGES)
    assert index.towns_passed(line) == []
    assert index.states_crossed(line) == [WESTLAND, EASTLAND]
    assert index.states_at(np.array([(-122.68, 45.515), (-100.0, 40.0)])) == [WESTLAND, None]


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
        f"wrote {out / 'states.places.json'} (2 states)",
        f"wrote {out / 'states_outline.places.json'} (2 outlines)",
        f"wrote {out / 'portland.places.json'} (1 places: WL 1)",
    ]
    assert result.stderr == "missing places for EL: run tools/fetch_census.sh 92\n"
    index = PlaceIndex.load("portland")
    assert index is not None
    assert index.states == (WESTLAND, EASTLAND) and index.towns == (WESTVILLE,)


def test_aux_places_on_a_malformed_manifest_is_one_line(data_dir: Path, tmp_path: Path) -> None:
    census = tmp_path / "census"
    _census_zips(census)
    manifest = data_dir / "aux" / "places" / "places.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("")
    result = CliRunner().invoke(
        app, ["aux", "places", "--region", "portland", "--census-dir", str(census)]
    )
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr == (
        f"{manifest} is malformed: delete it, then run `worst-route aux places --region "
        "portland` again, and for each other region whose index is there\n"
    )
    assert [path.name for path in manifest.parent.iterdir()] == ["places.json"]


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
    assert all(state in STATES for region in REGIONS.values() for state in region.states)
