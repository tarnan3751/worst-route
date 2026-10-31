"""The share image on the API tests' lattice: a 1200 × 630 PNG, the same bytes for the same
inputs, with and without the places index's outlines, for a round trip, and its network: the
roads a full decode finds, without decoding them all, thinned when too many would show."""

import math
import struct
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from worst_route.graph.data import GraphData
from worst_route.graph.geometry import Geometry, edge_points, edge_xy, encode, straight
from worst_route.graph.testing import edge_id, make_graph
from worst_route.presentation import share_image
from worst_route.presentation.places import PlaceIndex
from worst_route.presentation.share_image import headline, render_share_image

from ..engine.graphs import edges_of
from .support import EAST_THEN_NORTH, EAST_THEN_NORTH_EDGES, GRID, write_fixture_places

NODES = np.array(EAST_THEN_NORTH, dtype=np.int32)
STRAIGHT = straight(GRID.m)
BASELINE = edges_of(GRID, [10, 20, 30, 40, 50, 60, 70, 80, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99])
METRICS = {"length_m": 1700.0, "baseline_length_m": 1700.0, "ratio": 1.0}
LINE = "Longer than the Golden Gate Bridge, which is 1.7 mi (2.7 km)."
Bbox = tuple[float, float, float, float]


def size_of(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR"
    width, height = struct.unpack(">II", png[16:24])
    return width, height


@pytest.fixture
def places(tmp_path: Path) -> PlaceIndex:
    write_fixture_places(tmp_path)
    index = PlaceIndex.load("portland", tmp_path)
    assert index is not None
    return index


def render(places: PlaceIndex | None, **metrics: float | None) -> bytes:
    return render_share_image(
        GRID,
        STRAIGHT,
        NODES,
        EAST_THEN_NORTH_EDGES,
        {**METRICS, **metrics},
        places,
        LINE,
        baseline_edges=BASELINE,
    )


def test_the_image_is_a_1200_by_630_png_and_the_same_every_time(places: PlaceIndex) -> None:
    plain, outlined = render(None), render(places)
    assert size_of(plain) == size_of(outlined) == (1200, 630)
    assert render(None) == plain and render(places) == outlined
    assert outlined != plain  # the border between the fixture's states crosses the map
    assert b"Software" not in plain


def test_a_round_trip_shows_its_length(places: PlaceIndex) -> None:
    png = render(places, ratio=None, baseline_length_m=0.0)
    assert size_of(png) == (1200, 630) and png != render(places)


def test_the_headline_follows_the_objective() -> None:
    m = {"length_m": 481_585.4, "ratio": 62.43, "time_ratio": 48.07}
    assert headline(m, "distance") == ("62.4×", "less efficient than\nthe sensible route")
    assert headline(m, "time") == ("48.1×", "slower than\nthe sensible route")
    assert headline({**m, "ratio": 12_345.67}, "distance")[0] == "12,345.7×"
    trip = {"length_m": 481_585.4, "ratio": None, "time_ratio": None}  # a round trip
    for objective in ("distance", "time"):
        assert headline(trip, objective) == ("299 mi", "to end up where\nit started")
    assert headline({**trip, "length_m": 8000.0}, "time")[0] == "5.0 mi"


def test_a_slowest_time_route_draws_its_own_headline() -> None:
    time_route = render(None, time_ratio=2.5)
    assert time_route == render(None)  # a distance route ignores its time ratio
    slow = render_share_image(
        GRID,
        STRAIGHT,
        NODES,
        EAST_THEN_NORTH_EDGES,
        {**METRICS, "time_ratio": 2.5},
        None,
        LINE,
        baseline_edges=BASELINE,
        objective="time",
    )
    assert size_of(slow) == (1200, 630) and slow != time_route


def test_without_a_baseline_it_still_draws() -> None:
    png = render_share_image(GRID, STRAIGHT, NODES, EAST_THEN_NORTH_EDGES, METRICS, None, LINE)
    assert size_of(png) == (1200, 630) and png != render(None)


def test_the_extent_holds_the_route_with_its_margin_at_the_map_s_shape() -> None:
    points = np.array([[-122.68, 45.51], [-122.66, 45.52]])
    west, east, south, north = share_image._extent(points, 744 / 630)
    scale = math.cos(math.radians(45.515))
    assert (east - west) * scale / (north - south) == pytest.approx(744 / 630)
    assert west == pytest.approx(-122.68 - 0.15 * 0.02)  # the wider side: margin only
    assert east == pytest.approx(-122.66 + 0.15 * 0.02)
    assert south < 45.51 - 0.15 * 0.01 and north > 45.52 + 0.15 * 0.01  # widened to the shape
    assert (west + east) / 2 == pytest.approx(-122.67) and (south + north) / 2 == pytest.approx(
        45.515
    )
    # A route going nowhere still gets at least MIN_SPAN_M of map.
    west, east, south, north = share_image._extent(np.array([[-122.68, 45.51]] * 2), 1.0)
    assert (north - south) * share_image.METRES_PER_DEGREE >= share_image.MIN_SPAN_M


def test_the_network_is_one_line_per_road_unit_and_thins_when_too_many(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    everywhere = (-123.0, 45.0, -122.0, 46.0)
    # the builder: a unit per edge
    assert len(share_image._network(GRID, STRAIGHT, everywhere)) == GRID.m
    # As a build pairs them: each street's two directions one unit, its lower edge id.
    pairs = zip(GRID.edge_u.tolist(), GRID.edge_v.tolist(), strict=True)
    unit = np.minimum(np.arange(GRID.m), [edge_id(GRID, v, u) for u, v in pairs])
    paired = replace(GRID, edge_unit=unit.astype(np.int32))
    assert len(share_image._network(paired, STRAIGHT, everywhere)) == GRID.m // 2 == 180
    assert share_image._network(GRID, STRAIGHT, (-100.0, 40.0, -99.0, 41.0)) == []
    monkeypatch.setattr(share_image, "MAX_NETWORK_EDGES", 100)
    # only T0-T2: no residential street
    assert share_image._network(GRID, STRAIGHT, everywhere) == []


def test_the_network_follows_the_build_s_geometry() -> None:
    # Two roads of the lattice's south row, the first bending 30 m north half-way: drawn with
    # its bend, and only the road units whose bounds meet the map.
    x0, y0 = GRID.node_x[0], GRID.node_y[0]
    g = make_graph([(x0, y0), (x0 + 100, y0), (x0 + 200, y0)], [(0, 1, 100.0), (1, 2, 100.0)])
    points = np.array(
        [(x0, y0), (x0 + 50, y0 + 30), (x0 + 100, y0), (x0 + 100, y0), (x0 + 200, y0)]
    )
    bent = encode(g, points, np.array([0, 3, 5]))
    lines = share_image._network(g, bent, (-123.0, 45.0, -122.0, 46.0))
    assert [len(line) for line in lines] == [3, 2]
    assert lines[0][1, 1] > lines[0][0, 1]  # the bend, north
    east = float(lines[1][0, 0]) + 1e-6  # only the second road: east of node 1
    assert len(share_image._network(g, bent, (east, 45.0, -122.0, 46.0))) == 1


def by_full_decode(data: GraphData, geometry: Geometry, bbox: Bbox) -> list[NDArray[np.float64]]:
    """The network's lines, never thinned, as the image found them before: every road unit
    decoded, those whose bounds meet the map's in the build's CRS kept, then in lon/lat."""
    units = np.flatnonzero(data.edge_unit == np.arange(data.m))
    xy, counts = edge_xy(data, geometry, units)
    near = units[share_image._meets(xy, counts, share_image._outline_box(data, bbox))]
    points, counts = edge_points(data, geometry, near)
    inside = share_image._meets(points, counts, bbox)
    starts = np.cumsum(counts) - counts
    return [points[a : a + c] for a, c in zip(starts[inside], counts[inside], strict=True)]


def bent_lattice() -> tuple[GraphData, Geometry]:
    """The lattice with every road bent through two points up to 150 m off its straight line,
    its length the bent line's, as a build measures it."""
    xy = np.column_stack([GRID.node_x, GRID.node_y])
    a, b = xy[GRID.edge_u], xy[GRID.edge_v]
    across = (b - a)[:, ::-1] * [1.0, -1.0] / np.linalg.norm(b - a, axis=1)[:, None]
    off = np.random.default_rng(5).uniform(-150.0, 150.0, (GRID.m, 2, 1))
    bends = [a + (b - a) / 3 + across * off[:, 0], a + (b - a) * 2 / 3 + across * off[:, 1]]
    lines = np.stack([a, *bends, b], axis=1)
    length = np.linalg.norm(np.diff(lines, axis=1), axis=2).sum(axis=1)
    data = replace(GRID, edge_length_m=length.astype(np.float32))
    return data, encode(data, lines.reshape(-1, 2), np.arange(0, 4 * GRID.m + 1, 4))


def test_the_network_finds_the_roads_a_full_decode_finds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, geometry = bent_lattice()
    decoded: list[int] = []
    real = share_image.edge_xy

    def counted(*args: Any) -> tuple[NDArray[np.float64], NDArray[np.int64]]:
        decoded.append(len(args[2]))
        return real(*args)

    monkeypatch.setattr(share_image, "edge_xy", counted)
    # Maps of three sizes over the lattice and around it, from 30 m across to 460 m.
    west, east = GRID.node_lon.min() - 0.003, GRID.node_lon.max() + 0.003
    south, north = GRID.node_lat.min() - 0.002, GRID.node_lat.max() + 0.002
    between = 0  # roads met only between their ends
    for lon in np.linspace(west, east, 9):
        for lat in np.linspace(south, north, 9):
            for half in (0.0002, 0.001, 0.003):
                bbox = (lon - half, lat - half * 0.7, lon + half, lat + half * 0.7)
                lines = share_image._network(data, geometry, bbox)
                full = by_full_decode(data, geometry, bbox)
                assert len(lines) == len(full)
                assert all(map(np.array_equal, lines, full))
                ends = np.array([[line[0], line[-1]] for line in lines]).reshape(-1, 2, 2)
                within = (ends >= bbox[:2]) & (ends <= bbox[2:])
                between += int((~within.all(axis=2).any(axis=1)).sum())
    assert between > 0
    # A hairpin 400 m north and back, met only by its tip, half its length from both its ends.
    x0, y0 = GRID.node_x[0], GRID.node_y[0]
    line = np.array([(x0, y0), (x0 + 5, y0 + 400), (x0 + 10, y0)])
    length = float(np.linalg.norm(np.diff(line, axis=0), axis=1).sum())
    hairpin = make_graph([(x0, y0), (x0 + 10, y0)], [(0, 1, length)])
    bent = encode(hairpin, line, np.array([0, 3]))
    lon, lat = edge_points(hairpin, bent, np.array([0]))[0][1]
    bbox = (lon - 0.001, lat - 0.00002, lon + 0.001, lat + 0.001)  # its south side 2 m below
    lines = share_image._network(hairpin, bent, bbox)
    assert len(lines) == 1 and np.array_equal(lines[0], by_full_decode(hairpin, bent, bbox)[0])
    # A map holding every road, or far from all of them, decodes none to tell.
    decoded.clear()
    assert len(share_image._network(data, geometry, (-123.0, 45.0, -122.0, 46.0))) == GRID.m
    assert share_image._network(data, geometry, (-100.0, 40.0, -99.0, 41.0)) == []
    assert decoded == [0, 0]
