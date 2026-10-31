from dataclasses import replace

import numpy as np
import pytest

from worst_route.api.schemas import RouteMetrics
from worst_route.api.sse import (
    Event,
    path_points,
    route_metrics,
    server_sent,
    snapshot,
    splice_out,
)
from worst_route.engine.route import Metrics
from worst_route.graph.data import GraphData
from worst_route.graph.testing import make_graph

from ..engine.graphs import TO_LONLAT, X0, Y0, edges_of
from .support import GRID

F32 = 1e-5  # degrees: edge coords are float32, about 1 m from the nodes' float64 positions


def lonlat(x: float, y: float) -> list[float]:
    lon, lat = TO_LONLAT.transform(X0 + x, Y0 + y)
    return [float(lon), float(lat)]


def shaped() -> GraphData:
    """Three nodes 100 m apart on a line; 0 → 1 and back bend 5 m off it, with a point on the way
    to the bend."""
    g = make_graph(
        [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0)],
        [(0, 1, 100), (1, 0, 100), (1, 2, 100), (2, 1, 100)],
    )
    bent = [lonlat(0, 0), lonlat(30, 2.5), lonlat(60, 5), lonlat(100, 0)]
    lines = [bent, bent[::-1], [lonlat(100, 0), lonlat(200, 0)], [lonlat(200, 0), lonlat(100, 0)]]
    indptr = np.concatenate(([0], np.cumsum([len(line) for line in lines]))).astype(np.int64)
    coords = np.array([p for line in lines for p in line], dtype=np.float32)
    return replace(g, coord_indptr=indptr, coords=coords)


def test_a_path_keeps_shared_junctions_once_and_indexes_every_node() -> None:
    nodes = [0, 1, 2, 12, 22]
    coords, node_index = path_points(GRID, edges_of(GRID, nodes))
    assert len(coords) == 5 and node_index == [0, 1, 2, 3, 4]
    for k, v in enumerate(nodes):
        assert coords[node_index[k]] == pytest.approx([GRID.node_lon[v], GRID.node_lat[v]], abs=F32)


def test_a_snapshot_simplifies_each_edge_and_keeps_every_node() -> None:
    g = shaped()
    edges = edges_of(g, [0, 1, 2])
    full, node_index = path_points(g, edges)
    assert len(full) == 5 and node_index == [0, 3, 4]
    coords, node_index = path_points(g, edges, simplify_m=1.0)
    assert all(p in full for p in coords)  # the original values, to the last digit
    assert len(coords) == 4 and node_index == [0, 2, 3]  # the point on the way goes
    assert coords[1] == pytest.approx(lonlat(60, 5), abs=F32)  # the bend stays
    coords, node_index = path_points(g, edges, simplify_m=10.0)
    assert node_index == [0, 1, 2]
    for k, v in enumerate([0, 1, 2]):
        assert coords[node_index[k]] == pytest.approx([g.node_lon[v], g.node_lat[v]], abs=F32)
    shot = snapshot(
        g,
        nodes=np.array([0, 1, 2], dtype=np.int32),
        edges=edges,
        stop_idx=(0, 2),
        iteration=4,
        elapsed_s=1.5,
        metrics=RouteMetrics(length_m=200, time_s=18, delay_s=0.0, ratio=1.0),
        simplify_m=1.0,
    )
    assert (shot.node_count, shot.stop_idx, shot.node_index) == (3, [0, 2], [0, 2, 3])
    assert shot.geometry.type == "LineString" and len(shot.geometry.coordinates) == 4


def test_a_splice_carries_its_points_and_node_indices() -> None:
    nodes = np.array([1, 11, 12, 2], dtype=np.int32)
    out = splice_out(GRID, 1, 2, nodes, edges_of(GRID, [1, 11, 12, 2]))
    assert (out.i, out.j, out.node_count, out.node_index) == (1, 2, 4, [0, 1, 2, 3])
    assert out.coords[3] == pytest.approx([GRID.node_lon[2], GRID.node_lat[2]], abs=F32)


def test_metrics_carry_the_delays_the_ratio_and_the_objective_s_counters() -> None:
    measured = Metrics(3000.0, 300.0, {"items": 2.0}, delay_s=40.0)
    m = route_metrics(measured, baseline_length_m=1000.0)
    assert m.model_dump() == {
        "length_m": 3000.0,
        "time_s": 300.0,
        "delay_s": 40.0,
        "ratio": 3.0,
        "items": 2.0,
    }
    assert route_metrics(Metrics(3000.0, 300.0), baseline_length_m=0.0).ratio is None


def test_an_event_is_sent_with_its_id_name_and_data() -> None:
    sent = server_sent(Event(3, "progress", '{"iteration":7}')).encode().decode()
    assert sent.splitlines() == ["id: 3", "event: progress", 'data: {"iteration":7}', ""]
