"""Graphs and route helpers shared by the engine tests."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import numpy as np
from pyproj import Transformer

from worst_route.config import HighwayClass
from worst_route.engine.route import Metrics, Route
from worst_route.graph.data import GraphData
from worst_route.graph.store import graph_version
from worst_route.graph.testing import GRID_ORIGIN, Edge, EdgePair, edge_id, make_graph

X0, Y0 = GRID_ORIGIN
TO_LONLAT = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)
NO_METRICS = Metrics(length_m=0.0, time_s=0.0)


def lattice(
    rows: int,
    cols: int,
    spacing_m: float = 100.0,
    *,
    twins: Sequence[EdgePair] = (),
    transitions: Sequence[EdgePair] = (),
    rounded_lonlat: bool = False,
    primary_every: int = 0,
    roads: Mapping[tuple[int, int], Mapping[str, int | str | None]] | None = None,
    brands: Mapping[str, Sequence[int]] | None = None,
    named: bool = False,
) -> GraphData:
    """`grid_graph`'s two-way lattice (node r * cols + c), with twins and transitions.

    `rounded_lonlat` rounds the inverse projection to 1e-7 degrees, so the build's bytes and
    its graph_version do not depend on PROJ's last bits on this platform. With
    `primary_every` = k, the rows and columns 0, k, 2k, ... are primary roads at 72 km/h (the
    rest residential at 40 km/h), with a 30 s signal where two of them cross and a 10 s stop
    sign at their other nodes. `roads` gives `Edge` fields (`flags`, `toll_booths`,
    `level_crossings`, `name`) to both directions of the road a–b, keyed by (a, b) with a < b.
    `brands` maps a brand's name to its nodes (`make_graph`'s). `named` names the rows "Row r"
    and the columns "Col c", which are then their identities (hard mode); unnamed, no edge has
    one.
    """
    xy = [(X0 + c * spacing_m, Y0 + r * spacing_m) for r in range(rows) for c in range(cols)]
    fields = roads or {}

    def street(a: int, b: int, primary: bool, name: str) -> list[Edge]:
        highway, speed = (
            (HighwayClass.PRIMARY, 72.0) if primary else (HighwayClass.RESIDENTIAL, 40.0)
        )
        extra: dict[str, Any] = {"name": name} if named else {}
        extra.update(fields.get((a, b), {}))
        return [
            Edge(a, b, spacing_m, highway, speed, **extra),
            Edge(b, a, spacing_m, highway, speed, **extra),
        ]

    def on_primary(k: int) -> bool:
        return primary_every > 0 and k % primary_every == 0

    edges: list[Edge] = []
    for r in range(rows):
        for c in range(cols - 1):
            edges += street(r * cols + c, r * cols + c + 1, on_primary(r), f"Row {r}")
    for c in range(cols):
        for r in range(rows - 1):
            edges += street(r * cols + c, (r + 1) * cols + c, on_primary(c), f"Col {c}")
    lonlat = None
    if rounded_lonlat:
        lon, lat = TO_LONLAT.transform([p[0] for p in xy], [p[1] for p in xy])
        lonlat = list(zip(np.round(lon, 7).tolist(), np.round(lat, 7).tolist(), strict=True))
    g = make_graph(xy, edges, twins=twins, transitions=transitions, lonlat=lonlat, brands=brands)
    if primary_every == 0:
        return g
    delays = [
        30.0 if on_primary(r) and on_primary(c) else 10.0 if on_primary(r) or on_primary(c) else 0.0
        for r in range(rows)
        for c in range(cols)
    ]
    g = replace(g, node_delay_s=np.array(delays, dtype=np.float32), meta=dict(g.meta))
    g.node_delay_s.flags.writeable = False
    g.meta["graph_version"] = graph_version(g)
    return g


# One tree per letter, A to Z: names for the alphabet route's lattices.
# fmt: off
TREES = (
    "Alder", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Ginkgo", "Hawthorn", "Ivy", "Juniper",
    "Kauri", "Larch", "Maple", "Nutmeg", "Oak", "Pine", "Quince", "Rowan", "Spruce", "Tamarack",
    "Upas", "Viburnum", "Willow", "Xylosma", "Yew", "Zelkova",
)
# fmt: on


def named_roads(
    rows: int, cols: int, row_names: Sequence[str | None], col_names: Sequence[str | None]
) -> dict[tuple[int, int], dict[str, str]]:
    """`lattice`'s `roads` naming its row r `row_names[r]` and its column c `col_names[c]` (None
    leaves one unnamed)."""
    roads: dict[tuple[int, int], dict[str, str]] = {}
    for r, name in enumerate(row_names):
        if name is not None:
            for c in range(cols - 1):
                roads[(r * cols + c, r * cols + c + 1)] = {"name": name}
    for c, name in enumerate(col_names):
        if name is not None:
            for r in range(rows - 1):
                roads[(r * cols + c, (r + 1) * cols + c)] = {"name": name}
    return roads


def avenue(k: int) -> str:
    """The k-th numbered avenue, a free connector: "1st Avenue", "2nd Avenue", ..."""
    suffix = "th" if 10 <= k % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(k % 10, "th")
    return f"{k}{suffix} Avenue"


def edges_of(data: GraphData, nodes: Sequence[int]) -> np.ndarray:
    """The edges along a node path, each the one edge between consecutive nodes."""
    pairs = zip(nodes[:-1], nodes[1:], strict=True)
    return np.array([edge_id(data, u, v) for u, v in pairs], dtype=np.int32)


def route_on(data: GraphData, nodes: Sequence[int], stop_idx: Sequence[int] | None = None) -> Route:
    """A Route along `nodes` (stops at both ends unless given), score and metrics zero."""
    stops = stop_idx if stop_idx is not None else (0, len(nodes) - 1)
    path = np.array(nodes, dtype=np.int32)
    return Route.new(
        data, path, edges_of(data, nodes), stops, score=0.0, metrics=NO_METRICS, seed=0
    )


def at_node(data: GraphData, v: int) -> tuple[float, float]:
    """(lat, lon) of node v."""
    return float(data.node_lat[v]), float(data.node_lon[v])


def at(x: float, y: float) -> tuple[float, float]:
    """(lat, lon) of a point given in metres from GRID_ORIGIN."""
    lon, lat = TO_LONLAT.transform(X0 + x, Y0 + y)
    return float(lat), float(lon)


def left_only(spacing_m: float = 100.0, arm: int = 12) -> GraphData:
    """A one-way stem north from node 0 to a junction J (node 2), whose ways on are a right turn
    east to one node and a left turn west along `arm` nodes (3, 4, ...): a trip to the far end
    of the arm turns left at J, and so does a trip to any of the nearer nodes the re-snap
    fallback tries."""
    xy = [(X0, Y0), (X0, Y0 + spacing_m), (X0, Y0 + 2 * spacing_m)]
    xy += [(X0 - k * spacing_m, Y0 + 2 * spacing_m) for k in range(1, arm + 1)]
    xy.append((X0 + spacing_m, Y0 + 2 * spacing_m))
    edges = [Edge(0, 1, spacing_m), Edge(1, 2, spacing_m), Edge(2, 3, spacing_m)]
    edges += [Edge(k, k + 1, spacing_m) for k in range(3, arm + 2)]
    edges.append(Edge(2, arm + 3, spacing_m))
    return make_graph(xy, edges)


def two_rings(radius_m: float = 300.0, apart_m: float = 2000.0) -> GraphData:
    """Two one-way rings of 12 nodes, `apart_m` between their centres and no road between them:
    nodes 0-11 round the first (node 0 at (radius_m, 0)), 12-23 round the second (node 12 at
    (apart_m + radius_m, 0)). No route joins them, whatever the turns."""
    xy: list[tuple[float, float]] = []
    edges: list[Edge] = []
    for cx in (0.0, apart_m):
        first = len(xy)
        for k in range(12):
            angle = 2 * np.pi * k / 12
            xy.append((X0 + cx + radius_m * np.cos(angle), Y0 + radius_m * np.sin(angle)))
        edges += [Edge(first + k, first + (k + 1) % 12, None) for k in range(12)]
    return make_graph(xy, edges)
