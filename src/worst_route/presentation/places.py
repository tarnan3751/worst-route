"""States and towns a route passes through, from Census cartographic boundaries
(`docs/reference/presentation.md`, **Places**).

`worst-route aux places --region R` runs `write_places`: it reads the Census files a person
fetched with `tools/fetch_census.sh` (`<data>/aux/census/`, `config.CENSUS_VINTAGE`) and writes
three tables under `<data>/aux/places/`, polygons in EPSG:4326 (the Census files are NAD83,
EPSG:4269, taken as equal), each as three files (`table_files`): `<table>.places.json`, its
columns, `<table>.places.npy`, its polygons as WKB one after another in one uint8 array, and
`<table>.places.offsets.npy`, where each polygon starts (int64, a row more than the table, the
last the total length):

    states          STATEFP, STUSPS, NAME: the 5m state polygons, by STATEFP
    states_outline  the same columns from the 20m file, for drawing
    <region>        GEOID, NAME, NAMELSAD, LSAD, STUSPS: every place of every
                    cb_<vintage>_<FIPS>_place_500k.zip present whose polygon meets the region's
                    bbox, CDPs (LSAD 57) included, by GEOID

and then `places.json`, `{"format": 2, "regions": [...]}`, every region written at this format.
The region's bbox is its build's (`params.bbox`), else its own in `config.REGIONS`, else the
bounds of its states' polygons. A state whose polygon meets the bbox without a places file
present is reported as missing, not an error.

`PlaceIndex.load(region)` reads the three tables with NumPy, json and shapely, never pandas or
pyarrow (the desktop app carries neither for it). It returns None when the region has no index (or
a file is missing): callers then know no states or towns and draw no outlines; an index this
version cannot read, a region's format 1 (`<region>.parquet`) or an index `places.json` does not
list at format 2, raises `PlacesFormatError`, which names the command to write it again. The index
answers `states_at`, the state each point lies in (the API's pick of a build for a trip's stops),
`town_points`, each town at a point inside it (the daily route's draw of two towns), and, for a
route's line in lon/lat, `states_crossed` and `towns_passed`: every polygon the line intersects (a
touch counts), ordered by where the route first meets it. That is the first of the route's
segments, in route order, that intersects the polygon, then the point of their intersection
nearest that segment's start (its start itself for a zero-length segment, a point repeated);
polygons met at the same point keep the tables' order. Segment by segment, so a route crossing
itself on an overpass is never located at its other pass. geopandas is imported only to write.

The polygons are prepared (`shapely.prepare`) when the index loads, and each segment is tested
only against the polygons whose bounds it meets: on Portland, a 9,642-point route against the
states and the region's 68 places takes a few milliseconds, where the unprepared polygons took
about a quarter of a second. Prepared polygons are not safe under concurrent use with shapely 2.2
and GEOS 3.14, whether or not they have been used before (sixteen threads asking at once crash
the process), so the index answers one lookup at a time.
"""

import json
import threading
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import numpy as np
import shapely
from numpy.typing import NDArray

from worst_route.config import CENSUS_VINTAGE, REGIONS, STATES, settings
from worst_route.engine.route import IntArray
from worst_route.graph.data import GraphData
from worst_route.graph.geometry import Geometry
from worst_route.presentation.geojson import line_coordinates

STATE_COLUMNS = ("STATEFP", "STUSPS", "NAME")
PLACE_COLUMNS = ("GEOID", "NAME", "NAMELSAD", "LSAD", "STUSPS")
PLACES_FORMAT = 2  # the index's format, which places.json records
MANIFEST = "places.json"

Shapes = NDArray[np.object_]
Columns = dict[str, list[str]]


class PlacesFormatError(ValueError):
    """A region's places index this version cannot read: run `worst-route aux places` again."""


@dataclass(frozen=True)
class State:
    """A state (or DC, or a territory) as the Census names it."""

    fips: str  # STATEFP, "41"
    usps: str  # STUSPS, "OR"
    name: str  # NAME, "Oregon"


@dataclass(frozen=True)
class Town:
    """A Census place: an incorporated place or a census-designated place."""

    name: str  # NAME, "Portland"
    lsad: str  # LSAD: "25" city, "43" town, "47" village, "21" borough, "57" CDP, ...
    usps: str  # STUSPS, "OR"
    geoid: str  # GEOID, "4159000"


@dataclass(frozen=True)
class TownPoint:
    """A town at a point inside it: its polygon's `point_on_surface`, in lon/lat."""

    name: str  # NAME, "Bend"
    usps: str  # STUSPS, "OR"
    lsad: str  # LSAD, "25"
    lon: float
    lat: float


@dataclass(frozen=True)
class PlacesWritten:
    """What `write_places` wrote: each table (its columns' file) and its row count, the places by
    state, and the states the bbox meets whose places file was not given."""

    states: Path
    n_states: int
    outlines: Path
    n_outlines: int
    places: Path
    by_state: dict[str, int]  # STUSPS -> places kept
    missing: list[State]


def census_dir(data_dir: Path | None = None) -> Path:
    """Where `tools/fetch_census.sh` puts the Census files."""
    return (data_dir or settings().data_dir) / "aux" / "census"


def places_dir(data_dir: Path | None = None) -> Path:
    """Where `write_places` writes the places index."""
    return (data_dir or settings().data_dir) / "aux" / "places"


def state_files(directory: Path) -> tuple[Path, Path]:
    """The 5m state polygons and the 20m outlines in `directory`."""
    stem = f"cb_{CENSUS_VINTAGE}_us_state"
    return directory / f"{stem}_5m.zip", directory / f"{stem}_20m.zip"


def place_files(directory: Path) -> list[Path]:
    """Every state's places file in `directory`, in name order."""
    return sorted(directory.glob(f"cb_{CENSUS_VINTAGE}_[0-9][0-9]_place_500k.zip"))


def table_files(directory: Path, table: str) -> tuple[Path, Path, Path]:
    """A table's files: its columns (JSON), its polygons as WKB (uint8) and where each starts."""
    stem = f"{table}.places"
    return directory / f"{stem}.json", directory / f"{stem}.npy", directory / f"{stem}.offsets.npy"


def write_manifest(directory: Path, regions: Iterable[str]) -> Path:
    """`places.json`: the index's format and the regions whose tables it holds."""
    path = directory / MANIFEST
    path.write_text(json.dumps({"format": PLACES_FORMAT, "regions": sorted(regions)}) + "\n")
    return path


def _read(path: Path, columns: Sequence[str]) -> tuple[Columns, Shapes]:
    """A Census file's (or any file's with its columns) columns and polygons."""
    import geopandas as gpd

    source = f"zip://{path}" if path.suffix == ".zip" else str(path)
    frame = gpd.read_file(source, columns=list(columns))
    return {c: frame[c].tolist() for c in columns}, frame.geometry.to_numpy()


def _sorted(table: tuple[Columns, Shapes], key: str) -> tuple[Columns, Shapes]:
    """The table's rows by `key`'s values (unique: FIPS codes, GEOIDs)."""
    columns, shapes = table
    order = sorted(range(len(shapes)), key=columns[key].__getitem__)
    return {c: [v[k] for k in order] for c, v in columns.items()}, shapes[order]


def _rows(columns: Columns, names: Sequence[str]) -> Iterator[tuple[str, ...]]:
    return zip(*(columns[c] for c in names), strict=True)


def _write(directory: Path, table: str, columns: Mapping[str, list[str]], shapes: Shapes) -> Path:
    """A table's three files (`table_files`); its columns' file, the path returned."""
    names, wkb, offsets = table_files(directory, table)
    blobs = shapely.to_wkb(shapes).tolist()
    starts = np.cumsum([0, *map(len, blobs)], dtype=np.int64)
    np.save(wkb, np.frombuffer(b"".join(blobs), dtype=np.uint8))
    np.save(offsets, starts)
    names.write_text(json.dumps(dict(columns)) + "\n")
    return names


def read_table(directory: Path, table: str, region: str) -> tuple[Columns, Shapes]:
    """A table `_write` wrote: its columns and its polygons; `PlacesFormatError`, naming the
    command that writes `region`'s index again, when its three files disagree."""
    names, wkb, offsets = table_files(directory, table)
    columns = json.loads(names.read_bytes())
    blob = np.load(wkb, allow_pickle=False)
    starts = np.load(offsets, allow_pickle=False)
    lengths = {len(v) for v in columns.values()} if isinstance(columns, dict) else set()
    rows = lengths.pop() if len(lengths) == 1 else -1
    if (
        rows < 0
        or blob.dtype != np.uint8
        or blob.ndim != 1
        or starts.dtype != np.int64
        or starts.shape != (rows + 1,)
        or starts[0] != 0
        or starts[-1] != len(blob)
        or (np.diff(starts) < 0).any()
    ):
        raise _unreadable(region, directory, f"has a table, {table}, whose three files disagree")
    blobs = [blob[a:b].tobytes() for a, b in pairwise(starts.tolist())]
    return columns, shapely.from_wkb(np.array(blobs, object))


def _manifest(directory: Path, region: str) -> dict[str, Any] | None:
    """`places.json` in `directory`, None without one; `PlacesFormatError` for one that is not an
    object with a format (and at format 2, a list of regions)."""
    path = directory / MANIFEST
    if not path.exists():
        return None
    try:
        listed = json.loads(path.read_bytes())
    except ValueError:  # empty, or not JSON
        listed = None
    if (
        not isinstance(listed, dict)
        or "format" not in listed
        or (listed["format"] == PLACES_FORMAT and not isinstance(listed.get("regions"), list))
    ):
        raise PlacesFormatError(
            f"{path} is malformed: delete it, then run `worst-route aux places --region {region}` "
            "again, and for each other region whose index is there"
        )
    return listed


def write_places(
    region: str,
    bbox: tuple[float, float, float, float] | None,
    *,
    states: Path,
    outlines: Path,
    places: Sequence[Path],
    out_dir: Path,
) -> PlacesWritten:
    """Write the places index for `region` from the Census files (or any files with their
    columns): the states, the outlines, and the places meeting `bbox` (west, south, east,
    north; None: the bounds of the region's states); then add the region to `places.json`
    (`PlacesFormatError`, before anything is written, for a malformed one)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    listed = _manifest(out_dir, region)
    state_columns, state_shapes = _sorted(_read(states, STATE_COLUMNS), "STATEFP")
    outline_columns, outline_shapes = _sorted(_read(outlines, STATE_COLUMNS), "STATEFP")
    if bbox is None:
        fips = {STATES[state][1] for state in REGIONS[region].states}
        own = [f in fips for f in state_columns["STATEFP"]]
        box = shapely.envelope(shapely.union_all(state_shapes[own]))
    else:
        box = shapely.box(*bbox)
    covered: set[str] = set()
    place_columns: Columns = {c: [] for c in PLACE_COLUMNS}
    kept = []
    for path in places:
        columns, shapes = _read(path, (*PLACE_COLUMNS, "STATEFP"))
        covered.update(columns["STATEFP"])
        meets = shapely.intersects(shapes, box)
        for c in PLACE_COLUMNS:
            place_columns[c] += [v for v, m in zip(columns[c], meets, strict=True) if m]
        kept.append(shapes[meets])
    place_columns, place_shapes = _sorted(
        (place_columns, np.concatenate(kept) if kept else np.empty(0, object)), "GEOID"
    )
    written = (
        _write(out_dir, "states", state_columns, state_shapes),
        _write(out_dir, "states_outline", outline_columns, outline_shapes),
        _write(out_dir, region, place_columns, place_shapes),
    )
    regions = listed["regions"] if listed and listed["format"] == PLACES_FORMAT else []
    write_manifest(out_dir, {region, *regions})
    met = shapely.intersects(state_shapes, box)
    rows = _rows(state_columns, STATE_COLUMNS)
    missing = [State(*r) for r, m in zip(rows, met, strict=True) if m and r[0] not in covered]
    counts = Counter(place_columns["STUSPS"])
    return PlacesWritten(
        states=written[0],
        n_states=len(state_shapes),
        outlines=written[1],
        n_outlines=len(outline_shapes),
        places=written[2],
        by_state={k: counts[k] for k in sorted(counts)},
        missing=missing,
    )


def route_line(data: GraphData, geometry: Geometry, edges: IntArray) -> shapely.LineString:
    """The route along `edges` as a line in lon/lat, along the build's geometry."""
    return shapely.LineString(line_coordinates(data, geometry, edges))


class PlaceIndex:
    """The states and the region's towns, searchable by a route's line, and the state outlines
    to draw."""

    def __init__(
        self,
        states: Sequence[State],
        state_shapes: Shapes,
        outlines: Shapes,
        towns: Sequence[Town],
        town_shapes: Shapes,
    ) -> None:
        self.states = tuple(states)
        self.towns = tuple(towns)
        self.outlines = outlines
        shapely.prepare(state_shapes)
        shapely.prepare(town_shapes)
        self._states = (state_shapes, shapely.STRtree(state_shapes))
        self._towns = (town_shapes, shapely.STRtree(town_shapes))
        self._lock = threading.Lock()  # prepared polygons are unsafe under concurrent use
        self._points: tuple[TownPoint, ...] | None = None

    @classmethod
    def load(cls, region: str, data_dir: Path | None = None) -> "PlaceIndex | None":
        """The index `write_places` wrote for `region`, or None when it has none or a file is
        missing; `PlacesFormatError` for one this version cannot read."""
        directory = places_dir(data_dir)
        listed = _manifest(directory, region)
        reads = f"and this version reads format {PLACES_FORMAT}"
        if listed is not None and listed["format"] != PLACES_FORMAT:
            raise _unreadable(region, directory, f"is at format {listed['format']}, {reads}")
        if listed is None or region not in listed["regions"]:
            if (directory / f"{region}.parquet").exists():
                raise _unreadable(region, directory, f"is at format 1 (parquet), {reads}")
            if table_files(directory, region)[0].exists():
                raise _unreadable(region, directory, f"is not listed in {MANIFEST}, {reads}")
            return None
        tables = ("states", "states_outline", region)
        if not all(f.exists() for t in tables for f in table_files(directory, t)):
            return None
        states, state_shapes = read_table(directory, "states", region)
        _, outlines = read_table(directory, "states_outline", region)
        towns, town_shapes = read_table(directory, region, region)
        return cls(
            [State(*row) for row in _rows(states, STATE_COLUMNS)],
            state_shapes,
            outlines,
            [Town(*row) for row in _rows(towns, ("NAME", "LSAD", "STUSPS", "GEOID"))],
            town_shapes,
        )

    def states_at(self, points: NDArray[np.float64]) -> list[State | None]:
        """The state each (lon, lat) point lies in (a touch counts), None for a point in none (at
        sea, abroad); a point on a border is the first state's, by STATEFP."""
        shapes, tree = self._states
        geoms = cast(Shapes, shapely.points(points))
        with self._lock:
            point, shape = tree.query(geoms)  # the bounds meet; then each pair on the polygon
            inside = shapely.intersects(shapes[shape], geoms[point])
        first: dict[int, int] = {}
        for p, k in zip(point[inside].tolist(), shape[inside].tolist(), strict=True):
            first[p] = min(k, first.get(p, k))
        return [self.states[first[p]] if p in first else None for p in range(len(geoms))]

    def town_points(self) -> tuple[TownPoint, ...]:
        """Each town at a point inside its polygon (`shapely.point_on_surface`), in the towns'
        order (by GEOID), worked out on first use."""
        with self._lock:
            if self._points is None:
                xy = shapely.get_coordinates(shapely.point_on_surface(self._towns[0]))
                self._points = tuple(
                    TownPoint(t.name, t.usps, t.lsad, float(lon), float(lat))
                    for t, (lon, lat) in zip(self.towns, xy.tolist(), strict=True)
                )
            return self._points

    def states_crossed(self, line: shapely.LineString) -> list[State]:
        """The states `line` passes through, in the order it first enters them."""
        with self._lock:
            return [self.states[k] for k in _entered(line, *self._states)]

    def towns_passed(self, line: shapely.LineString) -> list[Town]:
        """The towns `line` passes through, in the order it first enters them."""
        with self._lock:
            return [self.towns[k] for k in _entered(line, *self._towns)]


def _unreadable(region: str, directory: Path, why: str) -> PlacesFormatError:
    return PlacesFormatError(
        f"the places index of {region} in {directory} {why}: run `worst-route aux places "
        f"--region {region}` again"
    )


def _entered(line: shapely.LineString, shapes: Shapes, tree: shapely.STRtree) -> list[int]:
    """The indices of the shapes `line` intersects, by where it first meets each (`shapes`
    prepared)."""
    coords = shapely.get_coordinates(line)
    segments = cast(Shapes, shapely.linestrings(np.stack([coords[:-1], coords[1:]], axis=1)))
    segment, shape = tree.query(segments)  # the bounds meet; then each pair on the prepared shape
    meets = shapely.intersects(shapes[shape], segments[segment])
    segment, shape = segment[meets], shape[meets]
    order = np.lexsort((segment, shape))
    shape, segment = shape[order], segment[order]
    first = np.unique(shape, return_index=True)[1]  # each shape's first segment
    keys = []
    for k, s in zip(segment[first].tolist(), shape[first].tolist(), strict=True):
        offset = 0.0  # a zero-length segment (a point repeated) meets it where it is
        if shapely.length(segments[k]) > 0:
            meeting = shapely.intersection(segments[k], shapes[s])
            points = shapely.points(shapely.get_coordinates(meeting))
            offset = float(shapely.line_locate_point(segments[k], points).min())
        keys.append((k, offset, s))
    return [s for _, _, s in sorted(keys)]
