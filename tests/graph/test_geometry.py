"""The build's geometry (`graph/geometry.py`): the steps' round trip within the metre's rounding,
long steps split, the ends from the nodes, its files and version, and its checks."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st

from worst_route.graph.data import GraphData
from worst_route.graph.geometry import (
    MAX_STEP_M,
    Geometry,
    edge_points,
    edge_xy,
    encode,
    geometry_version,
    read_geometry,
    straight,
)
from worst_route.graph.loader import load_build
from worst_route.graph.store import MissingArray, graph_version, read_build, write_build
from worst_route.graph.testing import GRID_ORIGIN, make_graph

X0, Y0 = GRID_ORIGIN
HALF = 0.5  # a point's rounding to the metre, on each axis
ROUNDING = 0.5 * 2**0.5


def two_nodes(far: tuple[float, float] = (1000.0, 0.0)) -> GraphData:
    """A one-way edge each way between two nodes, `far` metres apart."""
    xy = [(X0, Y0), (X0 + far[0], Y0 + far[1])]
    return make_graph(xy, [(0, 1, 1.0), (1, 0, 1.0)])


def lines_of(g: GraphData, interiors: list[list[tuple[float, float]]]) -> tuple[np.ndarray, ...]:
    """Each edge's line: its tail, its interior points (offsets from the origin), its head."""
    xy = np.column_stack([g.node_x, g.node_y])
    lines = [
        [xy[g.edge_u[e]], *[(X0 + x, Y0 + y) for x, y in inner], xy[g.edge_v[e]]]
        for e, inner in enumerate(interiors)
    ]
    points = np.array([p for line in lines for p in line], dtype=np.float64)
    indptr = np.concatenate(([0], np.cumsum([len(line) for line in lines]))).astype(np.int64)
    return points, indptr


offset = st.floats(-60_000.0, 60_000.0, allow_nan=False)
near = st.floats(-15_000.0, 15_000.0, allow_nan=False)  # steps within MAX_STEP_M


@settings(max_examples=200, deadline=None)
@given(st.lists(st.lists(st.tuples(near, near), max_size=6), min_size=2, max_size=2))
def test_short_steps_round_trip_to_the_metre(interiors: list[list[tuple[float, float]]]) -> None:
    g = two_nodes()
    points, indptr = lines_of(g, interiors)
    geometry = encode(g, points, indptr)
    geometry.validate(g.m)
    xy, counts = edge_xy(g, geometry, np.arange(g.m))
    assert counts.tolist() == np.diff(indptr).tolist()
    assert (np.abs(xy - points) <= HALF).all()  # the ends exactly, each point to the metre
    starts = np.cumsum(counts) - counts
    assert (xy[starts] == points[indptr[:-1]]).all()
    assert (xy[starts + counts - 1] == points[indptr[1:] - 1]).all()


@settings(max_examples=200, deadline=None)
@given(st.lists(st.tuples(offset, offset), min_size=1, max_size=5))
def test_a_long_step_is_split_on_its_segment(inner: list[tuple[float, float]]) -> None:
    g = two_nodes((90_000.0, -70_000.0))
    points, indptr = lines_of(g, [inner, []])
    geometry = encode(g, points, indptr)
    assert np.abs(geometry.geom_dxy.astype(np.int64)).max(initial=0) <= MAX_STEP_M + 1
    xy, counts = edge_xy(g, geometry, np.array([0]))
    original = points[: indptr[1]]
    line = shapely.LineString(original)
    # every drawn point within the rounding of the line, every original point drawn
    assert (shapely.distance(shapely.points(xy), line) <= ROUNDING + 1e-6).all()
    for p in original:
        assert (np.abs(xy - p) <= HALF).all(axis=1).any()


def test_the_ends_are_the_nodes_and_lonlat_is_theirs() -> None:
    g = two_nodes()
    points, indptr = lines_of(g, [[(300.0, 40.0), (700.0, -40.0)], [(500.0, 10.0)]])
    geometry = encode(g, points, indptr)
    lonlat, counts = edge_points(g, geometry, np.array([1, 0]))
    assert counts.tolist() == [3, 4]
    assert lonlat[0].tolist() == [g.node_lon[1], g.node_lat[1]]  # bit for bit
    assert lonlat[2].tolist() == [g.node_lon[0], g.node_lat[0]]
    assert lonlat[-1].tolist() == [g.node_lon[1], g.node_lat[1]]
    assert geometry.geom_indptr.tolist() == [0, 2, 3]
    assert geometry.geom_dxy.tolist() == [[300, 40], [400, -80], [-500, 10]]  # steps
    xy, _ = edge_xy(g, geometry, np.array([], dtype=np.int64))
    assert xy.shape == (0, 2)


def test_straight_edges_are_their_two_nodes() -> None:
    g = two_nodes()
    xy, counts = edge_xy(g, straight(g.m), np.array([0, 1]))
    assert counts.tolist() == [2, 2]
    assert xy.tolist() == [[X0, Y0], [X0 + 1000, Y0], [X0 + 1000, Y0], [X0, Y0]]


def test_a_line_that_does_not_end_at_its_nodes_is_refused() -> None:
    g = two_nodes()
    points, indptr = lines_of(g, [[(500.0, 0.0)], []])
    points[0, 0] += 0.25
    with pytest.raises(ValueError, match="edge 0: its line does not run from its tail"):
        encode(g, points, indptr)
    with pytest.raises(ValueError, match="2 lines of at least two points"):
        encode(g, points[:3], indptr[:2])


@pytest.mark.parametrize(
    ("geometry", "message"),
    [
        (Geometry(np.zeros(3, dtype=np.int64), np.zeros((0, 2), dtype=np.int16)), "geom_indptr"),
        (Geometry(np.zeros(4, dtype=np.int32), np.zeros((0, 2), dtype=np.int16)), "fit 2 edges"),
        (Geometry(np.zeros(3, dtype=np.int32), np.zeros((0, 3), dtype=np.int16)), "fit 2 edges"),
        (Geometry(np.array([0, 1, 1], dtype=np.int32), np.zeros((2, 2), dtype=np.int16)), "run"),
        (Geometry(np.array([0, 2, 1], dtype=np.int32), np.zeros((1, 2), dtype=np.int16)), "run"),
    ],
)
def test_a_malformed_geometry_is_refused(geometry: Geometry, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        geometry.validate(2)


def test_the_build_writes_its_geometry_beside_its_arrays(tmp_path: Path) -> None:
    g = two_nodes()
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b1"})
    points, indptr = lines_of(g, [[(250.0, 30.0)], []])
    geometry = encode(g, points, indptr)
    path = tmp_path / "graphs" / "portland" / "b1"
    assert write_build(g, path, geometry) == graph_version(g)  # the geometry is not in it
    meta = json.loads((path / "meta.json").read_text())
    assert meta["geometry_version"] == geometry_version(geometry)
    assert geometry_version(geometry) != geometry_version(straight(g.m))
    back = read_geometry(path, g.m)
    assert isinstance(back.geom_dxy, np.memmap) and not back.geom_dxy.flags.writeable
    assert back.geom_indptr.tolist() == [0, 1, 1] and back.geom_dxy.tolist() == [[250, 30]]
    assert read_build(path).meta["geometry_version"] == meta["geometry_version"]


def test_geometry_version_is_pinned() -> None:
    # Freezes the geometry's digest (docs/reference/graph.md): it changes only under a tier A
    # brief that changes the format.
    g = two_nodes()
    geometry = encode(g, *lines_of(g, [[(250.0, 30.0), (600.0, -20.0)], []]))
    expected = "182048ec5c7f926381a085eaf3ad45cc817a04810cd4f0128c272a5f592eee54"
    assert geometry_version(geometry) == expected


def test_a_build_read_without_its_geometry_plans_and_names_the_remedy(tmp_path: Path) -> None:
    g = two_nodes()
    g = replace(g, meta={**g.meta, "region": "portland", "build_id": "b2"})
    path = tmp_path / "graphs" / "portland" / "b2"
    write_build(g, path)
    (path / "geometry" / "geom_dxy.npy").unlink()
    loaded = load_build(tmp_path, "portland", "b2", backend="python")  # reads no geometry
    assert loaded.data.m == 2
    with pytest.raises(MissingArray, match=r"lacks its geometry \(geom_dxy\): rebuild it with "):
        _ = loaded.geometry
    (path / "geometry" / "geom_indptr.npy").unlink()
    with pytest.raises(MissingArray, match="geom_indptr"):
        read_geometry(path, 2)
