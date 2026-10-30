"""Graphs and route helpers shared by the engine tests."""

from collections.abc import Sequence
from dataclasses import replace

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
) -> GraphData:
    """`grid_graph`'s two-way lattice (node r * cols + c), with twins and transitions.

    `rounded_lonlat` rounds the inverse projection to 1e-7 degrees, so the build's bytes and
    its graph_version do not depend on PROJ's last bits on this platform. With
    `primary_every` = k, the rows and columns 0, k, 2k, ... are primary roads at 72 km/h (the
    rest residential at 40 km/h), with a 30 s signal where two of them cross and a 10 s stop
    sign at their other nodes.
    """
    xy = [(X0 + c * spacing_m, Y0 + r * spacing_m) for r in range(rows) for c in range(cols)]

    def street(a: int, b: int, primary: bool) -> list[Edge]:
        highway, speed = (
            (HighwayClass.PRIMARY, 72.0) if primary else (HighwayClass.RESIDENTIAL, 40.0)
        )
        return [Edge(a, b, spacing_m, highway, speed), Edge(b, a, spacing_m, highway, speed)]

    def on_primary(k: int) -> bool:
        return primary_every > 0 and k % primary_every == 0

    edges: list[Edge] = []
    for r in range(rows):
        for c in range(cols - 1):
            edges += street(r * cols + c, r * cols + c + 1, on_primary(r))
    for c in range(cols):
        for r in range(rows - 1):
            edges += street(r * cols + c, (r + 1) * cols + c, on_primary(c))
    lonlat = None
    if rounded_lonlat:
        lon, lat = TO_LONLAT.transform([p[0] for p in xy], [p[1] for p in xy])
        lonlat = list(zip(np.round(lon, 7).tolist(), np.round(lat, 7).tolist(), strict=True))
    g = make_graph(xy, edges, twins=twins, transitions=transitions, lonlat=lonlat)
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
