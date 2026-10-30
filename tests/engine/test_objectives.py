import math

import numpy as np
import pytest
from pydantic import ValidationError

from worst_route.engine.detour import find_detour
from worst_route.engine.objectives import OBJECTIVES, make_objective
from worst_route.engine.objectives.base import BaseObjective, FloatArray, Objective
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.request import RouteRequest
from worst_route.engine.route import IntArray, Route
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex

from .graphs import lattice, route_on

GRID = lattice(6, 6)


class Busy(BaseObjective):
    """Every kind of term at once, to hold delta to a full re-score."""

    key = "busy"
    has_pair = True
    has_global = True

    def node_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803
        return (np.arange(G.n) % 3).astype(np.float64)

    def edge_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803
        return self._lengths * 0.5

    def pair_term(self, G: GraphData, e_in: int, e_out: int) -> float:  # noqa: N803
        return 7.0 if G.edge_bearing_in[e_in] != G.edge_bearing_out[e_out] else 0.0

    def global_term(self, G: GraphData, nodes: IntArray, edges: IntArray) -> float:  # noqa: N803
        return float(len(nodes)) ** 1.5


def distance(data: GraphData = GRID, baseline: float = 500.0, r_max: float = 10_000.0) -> Distance:
    return Distance(data, baseline_length_m=baseline, r_max=r_max)


def test_distance_scores_the_length() -> None:
    route = route_on(GRID, [0, 1, 2, 8, 14])
    objective = distance()
    score, metrics = objective.evaluate(route.nodes, route.edges)
    assert score == 400.0 == metrics.length_m
    assert metrics.time_s == pytest.approx(400 / (40 / 3.6))
    assert metrics.counters == {}
    assert objective.score(route) == 400.0
    scored = Route.new(
        GRID, route.nodes, route.edges, route.stop_idx, score=score, metrics=metrics, seed=0
    )
    assert objective.target_value(GRID, scored) == 400.0
    assert objective.describe(GRID, scored) == {"length_m": 400.0}


def test_distance_hooks_are_neutral() -> None:
    objective = distance()
    route = route_on(GRID, [0, 1, 2])
    assert objective.node_terms(GRID) is None
    assert np.array_equal(objective.search_weights(GRID), GRID.edge_length_m.astype(np.float64))
    assert objective.pair_term(GRID, 0, 1) == 0.0 and objective.feasible_pair(GRID, 0, 1)
    assert objective.banned_edges(GRID, route, 0, 1) is None
    assert objective.waypoint_pool(GRID, route) is None
    nodes, edges = route.nodes, route.edges
    assert objective.feasible_window(GRID, route, 0, 1, nodes[:2], edges[:1])
    assert objective.search_budget(1000.0) == 8000.0


@pytest.mark.parametrize(
    ("baseline", "r_max", "radius"),
    [
        (500.0, 10_000.0, 1000.0),
        (20_000.0, 10_000.0, 5000.0),
        (20_000.0, 3000.0, 3000.0),
        (0.0, 600.0, 600.0),
    ],
)
def test_initial_radius(baseline: float, r_max: float, radius: float) -> None:
    route = route_on(GRID, [0, 1])
    assert distance(baseline=baseline, r_max=r_max).initial_radius(route) == radius


def test_the_registry_and_the_request_know_distance() -> None:
    assert set(OBJECTIVES) == {"distance"}
    objective: Objective = make_objective("distance", GRID, baseline_length_m=500.0, r_max=9000.0)
    assert isinstance(objective, Distance) and objective.key == "distance"
    with pytest.raises(ValidationError, match="unknown objective"):
        RouteRequest(stops=[(45.5, -122.6), (45.6, -122.5)], objective="slowest", seed=0)


def test_a_round_trip_counts_its_start_once() -> None:
    route = route_on(GRID, [0, 1, 7, 6, 0])
    busy = Busy(GRID, baseline_length_m=0.0, r_max=1e4)
    nodes = [0, 1, 7, 6]
    expected = math.fsum([v % 3 for v in nodes] + [50.0] * 4 + [7.0] * 3 + [5.0**1.5])
    assert busy.score(route) == pytest.approx(expected)


@pytest.mark.parametrize("seed", range(6))
def test_delta_equals_a_full_rescore_on_random_splices(seed: int) -> None:
    rng = np.random.Generator(np.random.PCG64(seed))
    backend, spatial = PythonBackend(GRID), SpatialIndex(GRID)
    snake = [0, 1, 2, 3, 4, 5, 11, 10, 9, 8, 7, 6, 12, 13, 14, 15, 16, 17]
    if seed % 2:
        snake = [0, 1, 2, 3, 9, 8, 7, 6, 0]  # a round trip
    route = route_on(GRID, snake)
    busy = Busy(GRID, baseline_length_m=500.0, r_max=1e4)
    route = Route.new(
        GRID,
        route.nodes,
        route.edges,
        route.stop_idx,
        score=busy.score(route),
        metrics=route.metrics,
        seed=0,
    )
    checked = 0
    for _ in range(150):
        i = int(rng.integers(len(route.edges)))
        j = min(i + int(rng.integers(1, 4)), len(route.edges))
        forbid, banned = route.release(GRID, i, j)
        found = find_detour(
            backend, spatial, GRID, route, i, j,
            forbid=forbid, banned=banned, node_mask=np.ones(GRID.n, dtype=bool),
            radius=300.0, objective=busy, rng=rng, entry_edge=-1, exit_edge=-1,
        )  # fmt: skip
        if found is None:
            continue
        delta = busy.delta(route, i, j, *found)
        after = route.splice(GRID, i, j, *found, score=0.0, metrics=route.metrics)
        assert delta == pytest.approx(busy.score(after) - busy.score(route), abs=1e-9)
        route = Route.new(
            GRID,
            after.nodes,
            after.edges,
            after.stop_idx,
            score=busy.score(after),
            metrics=after.metrics,
            seed=0,
        )
        checked += 1
    assert checked >= 10
