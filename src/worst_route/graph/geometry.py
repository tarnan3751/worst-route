"""A build's geometry: the shape of each edge, for drawing (`docs/reference/graph.md`,
**Geometry**).

The geometry is not among the arrays the engine reads. It is `geometry/` beside a build's
`arrays/`, two files read only where a route is drawn (the API's route documents, GeoJSON,
events and share image; the CLI's GeoJSON and a route's places; qa's twin layer), never by the
engine, the validator or a job's worker, none of which imports this module:

    geom_indptr  int32[m+1]   edge e's interior points: geom_dxy[geom_indptr[e]:geom_indptr[e + 1]]
                              (at most MAX_POINTS in all)
    geom_dxy     int16[*, 2]  whole-metre steps (dx, dy) in the build's CRS

An edge's line runs from its tail's position to its head's, which are its ends and are not
stored. Its interior points are stored as steps, the first from the tail, each next from the
point before it, every step the difference of two points' offsets from the tail rounded to the
metre: adding the steps up gives each point's rounded offset, without drift, so a point decodes
to within half a metre of where it was on each axis (0.71 m). A step longer than `MAX_STEP_M` on
either axis is split by points placed evenly along it, so every step fits an int16. `save`
simplifies each line first (Douglas–Peucker, `config.GEOMETRY_SIMPLIFY_M`).

`encode(data, points, indptr)` turns each edge's line, `points[indptr[e]:indptr[e + 1]]` in the
build's projected coordinates from its tail's position to its head's, into the geometry;
`straight(m)` is the geometry of edges without interior points (the test builders'). `edge_xy`
decodes the lines of some edges in projected metres and `edge_points` in lon/lat, ends included
(the nodes' `node_lon`, `node_lat`, bit for bit); `lonlat_to_xy` projects lon/lat into the
build's CRS. `geometry_version(geometry)` is the sha256 over each array's name, dtype, shape and
bytes, as `graph_version` hashes the arrays; `write_build` puts it in `meta.json`.
`read_geometry(path, m)` memory-maps the files of the build at `path` and checks their dtypes
and shapes; a missing file raises `MissingArray`.
"""

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from worst_route.graph.data import GraphData
from worst_route.graph.store import MissingArray

if TYPE_CHECKING:
    from pyproj import Transformer

MAX_STEP_M = 32_000  # a longer step is split: a rounded step stays within the int16 range
MAX_POINTS = 2**31 - 1  # geom_indptr is int32

# name -> (dtype, shape), in the order the files are written and hashed ("m+1", "*": data.py's)
GEOMETRY_SPECS: dict[str, tuple[np.dtype[Any], tuple[str | int, ...]]] = {
    "geom_indptr": (np.dtype(np.int32), ("m+1",)),
    "geom_dxy": (np.dtype(np.int16), ("*", 2)),
}

IntArray = NDArray[np.int64]
EdgeIds = NDArray[np.integer[Any]]
Points = NDArray[np.float64]


@dataclass(frozen=True, eq=False)
class Geometry:
    """The interior points of every edge, as steps; the fields are `GEOMETRY_SPECS` in order."""

    geom_indptr: NDArray[np.int32]
    geom_dxy: NDArray[np.int16]

    def validate(self, m: int, *, deep: bool = True) -> None:
        """Raise `ValueError` if the geometry does not fit a build of `m` edges; `deep=False`
        checks only dtypes and shapes."""
        for name, (dtype, shape) in GEOMETRY_SPECS.items():
            a = getattr(self, name)
            if not isinstance(a, np.ndarray) or a.dtype != dtype or a.ndim != len(shape):
                raise ValueError(f"{name}: expected a {len(shape)}-dimensional {dtype} array")
        if len(self.geom_indptr) != m + 1 or self.geom_dxy.shape[1] != 2:
            raise ValueError(
                f"geometry: shapes {self.geom_indptr.shape} and {self.geom_dxy.shape} "
                f"do not fit {m} edges"
            )
        if deep and (
            self.geom_indptr[0] != 0
            or self.geom_indptr[-1] != len(self.geom_dxy)
            or (np.diff(self.geom_indptr) < 0).any()
        ):
            raise ValueError(f"geom_indptr: must run from 0 to {len(self.geom_dxy)}, ascending")


def straight(m: int) -> Geometry:
    """The geometry of `m` straight edges: none has an interior point."""
    return _read_only(Geometry(np.zeros(m + 1, dtype=np.int32), np.zeros((0, 2), dtype=np.int16)))


def encode(data: GraphData, points: Points, indptr: IntArray) -> Geometry:
    """The geometry of each edge's line, `points[indptr[e]:indptr[e + 1]]`, from its tail's
    position to its head's (`ValueError` otherwise), in projected metres."""
    counts = np.diff(indptr)
    if len(counts) != data.m or (counts < 2).any():
        raise ValueError(f"encode: {data.m} lines of at least two points expected")
    xy = np.column_stack([data.node_x, data.node_y])
    ends = (points[indptr[:-1]] != xy[data.edge_u]) | (points[indptr[1:] - 1] != xy[data.edge_v])
    bad = np.flatnonzero(ends.any(axis=1))
    if len(bad):
        raise ValueError(f"edge {bad[0]}: its line does not run from its tail to its head")

    # The steps: from each point but an edge's last to the next one but the head.
    edge_of = np.repeat(np.arange(data.m), counts)
    last = np.zeros(len(points), dtype=np.bool_)
    last[indptr[1:] - 1] = True
    origin = np.flatnonzero(~last)  # each step starts here and ends at origin + 1
    origin = origin[~last[origin + 1]]  # ... unless it ends at the head
    a, b = points[origin], points[origin + 1]
    parts = np.maximum(1, np.ceil(np.abs(b - a).max(axis=1, initial=0.0) / MAX_STEP_M))
    parts = parts.astype(np.int64)
    step_of = np.repeat(np.arange(len(origin)), parts)
    j = np.arange(len(step_of)) - np.repeat(np.cumsum(parts) - parts, parts) + 1
    whole = j == parts[step_of]  # the step's own end, kept exactly
    fraction = (j / parts[step_of])[:, None]
    interior = np.where(
        whole[:, None], b[step_of], a[step_of] + (b[step_of] - a[step_of]) * fraction
    )
    owner = edge_of[origin][step_of]

    rounded = np.rint(interior - xy[data.edge_u[owner]]).astype(np.int64)
    per_edge = np.bincount(owner, minlength=data.m)
    if per_edge.sum() > MAX_POINTS:
        raise ValueError(f"encode: {per_edge.sum()} interior points, at most {MAX_POINTS}")
    first = np.ones(len(owner), dtype=np.bool_)
    first[1:] = owner[1:] != owner[:-1]
    steps = rounded.copy()
    steps[~first] -= rounded[np.flatnonzero(~first) - 1]
    geometry = Geometry(
        np.concatenate(([0], np.cumsum(per_edge))).astype(np.int32),
        steps.astype(np.int16).reshape(-1, 2),
    )
    return _read_only(geometry)


def edge_xy(data: GraphData, geometry: Geometry, edges: EdgeIds) -> tuple[Points, IntArray]:
    """The points along each of `edges` in projected metres, its ends included, and how many
    each edge has: edge k's are `points[offsets[k]:offsets[k] + counts[k]]`, offsets the
    running sum of counts."""
    edges = np.asarray(edges, dtype=np.int64)
    starts = geometry.geom_indptr[edges].astype(np.int64)
    inner = geometry.geom_indptr[edges + 1].astype(np.int64) - starts
    within = np.arange(inner.sum()) - np.repeat(np.cumsum(inner) - inner, inner)
    steps = geometry.geom_dxy[np.repeat(starts, inner) + within].astype(np.int64)
    sums = np.cumsum(steps, axis=0)
    before = np.zeros((len(edges), 2), dtype=np.int64)  # the sum before each edge's run
    opens = np.cumsum(inner) - inner
    has = inner > 0
    before[has & (opens > 0)] = sums[opens[has & (opens > 0)] - 1]
    offsets = sums - np.repeat(before, inner, axis=0)

    counts = inner + 2
    total = int(counts.sum())
    out = np.empty((total, 2), dtype=np.float64)
    first = np.cumsum(counts) - counts
    u, v = data.edge_u[edges], data.edge_v[edges]
    out[first, 0], out[first, 1] = data.node_x[u], data.node_y[u]
    out[first + counts - 1, 0], out[first + counts - 1, 1] = data.node_x[v], data.node_y[v]
    at = np.repeat(first + 1, inner) + within
    out[at, 0] = np.repeat(data.node_x[u], inner) + offsets[:, 0]
    out[at, 1] = np.repeat(data.node_y[u], inner) + offsets[:, 1]
    return out, counts


def edge_points(data: GraphData, geometry: Geometry, edges: EdgeIds) -> tuple[Points, IntArray]:
    """`edge_xy`'s points as (lon, lat), and how many each edge has."""
    xy, counts = edge_xy(data, geometry, edges)
    lon, lat = _to_lonlat(int(data.meta["crs_epsg"])).transform(xy[:, 0], xy[:, 1])
    return np.column_stack([lon, lat]).astype(np.float64), counts


def lonlat_to_xy(data: GraphData, lon: Points, lat: Points) -> tuple[Points, Points]:
    """Points given in lon/lat, projected into the build's CRS."""
    x, y = _to_xy(int(data.meta["crs_epsg"])).transform(lon, lat)
    return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)


def geometry_version(geometry: Geometry) -> str:
    """The sha256 of the geometry's arrays, as a hex string."""
    digest = hashlib.sha256()
    for name in GEOMETRY_SPECS:
        a = np.ascontiguousarray(getattr(geometry, name))
        digest.update(f"{name}|{a.dtype.str}|{list(a.shape)}\n".encode())
        digest.update(a)
    return digest.hexdigest()


def read_geometry(path: Path, m: int, mmap: bool = True) -> Geometry:
    """The geometry of the build at `path`, memory-mapped read-only (`mmap=False`: copies)."""
    arrays: dict[str, Any] = {}
    for name in GEOMETRY_SPECS:
        file = path / "geometry" / f"{name}.npy"
        if not file.exists():
            region = path.parent.name
            raise MissingArray(
                f"the build at {path} lacks its geometry ({name}): rebuild it with "
                f"`worst-route pipeline save --force --region {region}`"
            )
        arrays[name] = np.load(file, mmap_mode="r" if mmap else None, allow_pickle=False)
    geometry = _read_only(Geometry(**arrays))
    geometry.validate(m, deep=False)
    return geometry


def _read_only(geometry: Geometry) -> Geometry:
    for name in GEOMETRY_SPECS:
        getattr(geometry, name).flags.writeable = False
    return geometry


@cache
def _to_lonlat(crs_epsg: int) -> "Transformer":
    from pyproj import Transformer  # on first use, not with the module (the API imports it)

    return Transformer.from_crs(f"EPSG:{crs_epsg}", "EPSG:4326", always_xy=True)


@cache
def _to_xy(crs_epsg: int) -> "Transformer":
    from pyproj import Transformer  # on first use, not with the module (the API imports it)

    return Transformer.from_crs("EPSG:4326", f"EPSG:{crs_epsg}", always_xy=True)
