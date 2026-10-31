"""States and towns a route passes through, from Census cartographic boundaries
(`docs/reference/presentation.md`, **Places**).

`worst-route aux places --region R` runs `write_places`: it reads the Census files a person
fetched with `tools/fetch_census.sh` (`<data>/aux/census/`, `config.CENSUS_VINTAGE`) and writes
three GeoParquet files under `<data>/aux/places/`, geometry as WKB in EPSG:4326 (the Census
files are NAD83, EPSG:4269, taken as equal):

    states.parquet          STATEFP, STUSPS, NAME: the 5m state polygons, by STATEFP
    states_outline.parquet  the same columns from the 20m file, for drawing
    <region>.parquet        GEOID, NAME, NAMELSAD, LSAD, STUSPS: every place of every
                            cb_<vintage>_<FIPS>_place_500k.zip present whose polygon meets the
                            region's bbox, CDPs (LSAD 57) included, by GEOID

The region's bbox is its build's (`params.bbox`), else its own in `config.REGIONS`, else its
state's polygon's bounds. A state whose polygon meets the bbox without a places file present is
reported as missing, not an error.

`PlaceIndex.load(region)` reads the three files (None when one is absent: callers then know no
states or towns and draw no outlines) and answers, for a route's line in lon/lat,
`states_crossed` and `towns_passed`: every polygon the line intersects (a touch counts), ordered
by where the route first meets it. That is the first of the route's segments, in route order,
that intersects the polygon, then the point of their intersection nearest that segment's start
(its start itself for a zero-length segment, a point repeated); polygons met at the same point
keep the files' order. Segment by segment, so a route crossing itself on an overpass is never
located at its other pass. geopandas is imported only to write; the index reads with pyarrow
and shapely.

The polygons are prepared (`shapely.prepare`) when the index loads, and each segment is tested
only against the polygons whose bounds it meets: on Portland, a 9,642-point route against the
states and the region's 68 places takes a few milliseconds, where the unprepared polygons took
about a quarter of a second. Prepared polygons are not safe under concurrent use with shapely 2.2
and GEOS 3.14, whether or not they have been used before (sixteen threads asking at once crash
the process), so the index answers one lookup at a time.
"""

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow.parquet as pq
import shapely
from numpy.typing import NDArray

from worst_route.config import CENSUS_VINTAGE, REGIONS, STATES, settings
from worst_route.engine.io import line_coordinates
from worst_route.engine.route import IntArray
from worst_route.graph.data import GraphData

STATE_COLUMNS = ("STATEFP", "STUSPS", "NAME")
PLACE_COLUMNS = ("GEOID", "NAME", "NAMELSAD", "LSAD", "STUSPS")

Shapes = NDArray[np.object_]


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
class PlacesWritten:
    """What `write_places` wrote: each file and its row count, the places by state, and the
    states the bbox meets whose places file was not given."""

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


def _read(path: Path, columns: Sequence[str]) -> Any:
    import geopandas as gpd

    source = f"zip://{path}" if path.suffix == ".zip" else str(path)
    frame = gpd.read_file(source, columns=list(columns))
    frame = frame.set_crs(4326, allow_override=True)
    return frame[[*columns, "geometry"]]


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
    north; None: the bounds of the region's state)."""
    import pandas as pd

    out_dir.mkdir(parents=True, exist_ok=True)
    state_frame = _read(states, STATE_COLUMNS).sort_values("STATEFP", ignore_index=True)
    outline_frame = _read(outlines, STATE_COLUMNS).sort_values("STATEFP", ignore_index=True)
    if bbox is None:
        fips = STATES[REGIONS[region].state][1]
        box = shapely.envelope(state_frame.geometry[state_frame["STATEFP"] == fips].iloc[0])
    else:
        box = shapely.box(*bbox)
    covered: set[str] = set()
    kept = []
    for path in places:
        frame = _read(path, (*PLACE_COLUMNS, "STATEFP"))
        covered.update(frame["STATEFP"])
        kept.append(frame[frame.intersects(box)][[*PLACE_COLUMNS, "geometry"]])
    place_frame = (
        pd.concat(kept, ignore_index=True).sort_values("GEOID", ignore_index=True)
        if kept
        else _empty_places(state_frame)
    )
    paths = (out_dir / "states.parquet", out_dir / "states_outline.parquet")
    state_frame.to_parquet(paths[0], index=False)
    outline_frame.to_parquet(paths[1], index=False)
    place_path = out_dir / f"{region}.parquet"
    place_frame.to_parquet(place_path, index=False)
    met = state_frame[state_frame.intersects(box)]
    missing = [
        State(r.STATEFP, r.STUSPS, r.NAME) for r in met.itertuples() if r.STATEFP not in covered
    ]
    counts = place_frame["STUSPS"].value_counts()
    return PlacesWritten(
        states=paths[0],
        n_states=len(state_frame),
        outlines=paths[1],
        n_outlines=len(outline_frame),
        places=place_path,
        by_state={str(k): int(counts[k]) for k in sorted(counts.index)},
        missing=missing,
    )


def _empty_places(like: Any) -> Any:
    import geopandas as gpd

    return gpd.GeoDataFrame({c: [] for c in PLACE_COLUMNS}, geometry=[], crs=like.crs)


def route_line(data: GraphData, edges: IntArray) -> shapely.LineString:
    """The route along `edges` as a line in lon/lat."""
    return shapely.LineString(line_coordinates(data, edges))


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

    @classmethod
    def load(cls, region: str, data_dir: Path | None = None) -> "PlaceIndex | None":
        """The index `write_places` wrote for `region`, or None when a file is missing."""
        directory = places_dir(data_dir)
        files = [directory / n for n in ("states.parquet", "states_outline.parquet")]
        files.append(directory / f"{region}.parquet")
        if not all(f.exists() for f in files):
            return None
        state_rows, state_shapes = _load(files[0], STATE_COLUMNS)
        _, outlines = _load(files[1], STATE_COLUMNS)
        town_rows, town_shapes = _load(files[2], PLACE_COLUMNS)
        return cls(
            [State(fips, usps, name) for fips, usps, name in state_rows],
            state_shapes,
            outlines,
            [Town(name, lsad, usps, geoid) for geoid, name, _, lsad, usps in town_rows],
            town_shapes,
        )

    def states_crossed(self, line: shapely.LineString) -> list[State]:
        """The states `line` passes through, in the order it first enters them."""
        with self._lock:
            return [self.states[k] for k in _entered(line, *self._states)]

    def towns_passed(self, line: shapely.LineString) -> list[Town]:
        """The towns `line` passes through, in the order it first enters them."""
        with self._lock:
            return [self.towns[k] for k in _entered(line, *self._towns)]


def _load(path: Path, columns: Sequence[str]) -> tuple[list[tuple[str, ...]], Shapes]:
    table = pq.read_table(path, columns=[*columns, "geometry"])
    rows = list(zip(*(table.column(c).to_pylist() for c in columns), strict=True))
    shapes = shapely.from_wkb(np.array(table.column("geometry").to_pylist(), dtype=object))
    return rows, shapes


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
