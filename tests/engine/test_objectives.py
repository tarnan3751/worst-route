import dataclasses
import math

import numpy as np
import pytest
from pydantic import ValidationError

from worst_route.config import HighwayClass
from worst_route.engine.detour import find_detour
from worst_route.engine.objectives import OBJECTIVES, make_objective
from worst_route.engine.objectives.base import BaseObjective, FloatArray, Objective, route_delay_s
from worst_route.engine.objectives.brand import Brand
from worst_route.engine.objectives.collect import Collect
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.objectives.time import SlowestTime
from worst_route.engine.request import RouteRequest
from worst_route.engine.route import IntArray, Metrics, Route, splice_arrays
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex

from .graphs import edges_of, lattice, route_on

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


class BusyFromTheStart(Busy):
    """Busy, with a term for the route's first edge too."""

    def first_edge_term(self, G: GraphData, e: int, closing: int) -> float:  # noqa: N803
        return float(e % 5) + 0.25 + (closing % 3 if closing >= 0 else 0.0)


def distance(data: GraphData = GRID, baseline: float = 500.0, r_max: float = 10_000.0) -> Distance:
    return Distance(data, baseline=Metrics(baseline, 0.0), r_max=r_max)


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
    assert set(OBJECTIVES) == {"distance", "time", "collect", "brand"}
    objective: Objective = make_objective(
        "distance", GRID, baseline=Metrics(500.0, 45.0), r_max=9000.0
    )
    assert isinstance(objective, Distance) and objective.key == "distance"
    collecting = make_objective(
        "collect", GRID, baseline=Metrics(500.0, 45.0), r_max=9e3, item="bridge"
    )
    assert isinstance(collecting, Collect) and collecting.item == "bridge"
    with pytest.raises(ValueError, match="needs an item"):
        make_objective("collect", GRID, baseline=Metrics(500.0, 45.0), r_max=9000.0)
    branded = lattice(3, 3, brands={"Acme": [4]})
    visiting = make_objective("brand", branded, baseline=Metrics(500.0, 45.0), r_max=9e3, brand=0)
    assert isinstance(visiting, Brand) and visiting.brand == 0
    with pytest.raises(ValueError, match="needs a brand"):
        make_objective("brand", branded, baseline=Metrics(500.0, 45.0), r_max=9000.0)
    with pytest.raises(ValidationError, match="unknown objective"):
        RouteRequest(stops=[(45.5, -122.6), (45.6, -122.5)], objective="slowest", seed=0)


def test_the_metrics_carry_the_delays_whatever_the_mode() -> None:
    g = dataclasses.replace(GRID, node_delay_s=np.arange(GRID.n, dtype=np.float32))
    objective = distance(g)
    score, metrics = objective.evaluate(*_arrays(g, [0, 1, 2, 8, 14]))
    assert score == 400.0 and metrics.delay_s == 0 + 1 + 2 + 8 + 14  # start and end included
    _, metrics = objective.evaluate(*_arrays(g, [0, 1, 7, 6, 0]))
    assert metrics.delay_s == 0 + 1 + 7 + 6 == route_delay_s(g, np.array([0, 1, 7, 6, 0]))


def _arrays(g: GraphData, nodes: list[int]) -> tuple[IntArray, IntArray]:
    return np.array(nodes, dtype=np.int32), edges_of(g, nodes)


def test_a_round_trip_counts_its_start_once() -> None:
    route = route_on(GRID, [0, 1, 7, 6, 0])
    busy = Busy(GRID, baseline=Metrics(0.0, 0.0), r_max=1e4)
    nodes = [0, 1, 7, 6]
    expected = math.fsum([v % 3 for v in nodes] + [50.0] * 4 + [7.0] * 3 + [5.0**1.5])
    assert busy.score(route) == pytest.approx(expected)


def test_the_first_edge_term_counts_once() -> None:
    route = route_on(GRID, [0, 1, 7])
    busy, from_the_start = (
        cls(GRID, baseline=Metrics(0.0, 0.0), r_max=1e4) for cls in (Busy, BusyFromTheStart)
    )
    first = int(route.edges[0])
    assert from_the_start.score(route) == pytest.approx(busy.score(route) + first % 5 + 0.25)
    loop = route_on(GRID, [0, 1, 7, 6, 0])  # a round trip: its last edge closes on its start
    first, last = int(loop.edges[0]), int(loop.edges[-1])
    expected = busy.score(loop) + first % 5 + 0.25 + last % 3
    assert from_the_start.score(loop) == pytest.approx(expected)


@pytest.mark.parametrize("busy_type", [Busy, BusyFromTheStart])
@pytest.mark.parametrize("seed", range(6))
def test_delta_equals_a_full_rescore_on_random_splices(seed: int, busy_type: type[Busy]) -> None:
    rng = np.random.Generator(np.random.PCG64(seed))
    backend, spatial = PythonBackend(GRID), SpatialIndex(GRID)
    snake = [0, 1, 2, 3, 4, 5, 11, 10, 9, 8, 7, 6, 12, 13, 14, 15, 16, 17]
    if seed % 2:
        snake = [0, 1, 2, 3, 9, 8, 7, 6, 0]  # a round trip
    route = route_on(GRID, snake)
    busy = busy_type(GRID, baseline=Metrics(500.0, 45.0), r_max=1e4)
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


# Slowest time ------------------------------------------------------------------------------

TOWN = lattice(7, 7, primary_every=3)  # primary rows and columns 0, 3, 6; signals and stops


def slowest(data: GraphData = TOWN) -> SlowestTime:
    return SlowestTime(data, baseline=Metrics(600.0, 40.0), r_max=10_000.0)


def test_slowest_time_scores_the_driving_time_and_the_delays() -> None:
    # east along primary row 0 (5 s a block), up primary column 3, then west along residential
    # row 2 (9 s a block)
    nodes = [0, 1, 2, 3, 10, 17, 16, 15]
    objective = slowest()
    score, metrics = objective.evaluate(*_arrays(TOWN, nodes))
    times = TOWN.edge_time_s[edges_of(TOWN, nodes)].astype(np.float64)
    assert metrics.time_s == math.fsum(times.tolist()) == pytest.approx(5 * 5 + 2 * 9)
    assert metrics.delay_s == 30 + 10 + 10 + 30 + 10 + 10 + 0 + 0
    assert score == pytest.approx(metrics.time_s + metrics.delay_s)
    assert metrics.length_m == 700.0 and metrics.counters == {}
    route = Route.new(
        TOWN, *_arrays(TOWN, nodes), (0, len(nodes) - 1), score=score, metrics=metrics, seed=0
    )
    assert objective.score(route) == score
    experienced = metrics.time_s + metrics.delay_s
    assert objective.target_value(TOWN, route) == objective.primary(metrics) == experienced
    assert SlowestTime.primary(metrics) == experienced and Distance.primary(metrics) == 700.0
    assert objective.describe(TOWN, route) == {
        "time_s": metrics.time_s,
        "delay_s_total": 100.0,
        "slowest_edge_class_share": 200.0 / 700.0,  # the residential stretch of row 2
    }


def test_slowest_time_searches_slow_roads_and_draws_slow_waypoints() -> None:
    objective = slowest()
    weights = objective.search_weights(TOWN)
    primary = TOWN.edge_highway == HighwayClass.PRIMARY
    assert np.allclose(weights[primary], 100 * 72 / 60) and np.allclose(
        weights[~primary], 100 * 40 / 60
    )
    pool = objective.waypoint_pool(TOWN, route_on(TOWN, [8, 9]))
    assert pool is not None
    # a node qualifies when at least half its out-edges are residential: off the primary roads,
    # and on one where its two side streets are half its four out-edges, not at a crossing of
    # two or on the lattice's rim (two primary out-edges of three, or both of a corner's two)
    rows, cols = np.divmod(np.arange(TOWN.n), 7)
    on_primary = (rows % 3 == 0) | (cols % 3 == 0)
    crossing = (rows % 3 == 0) & (cols % 3 == 0)
    rim = (rows % 6 == 0) | (cols % 6 == 0)
    assert pool.tolist() == np.flatnonzero(~on_primary | (~crossing & ~rim)).tolist()
    assert objective.node_terms(TOWN) is not None and objective.pair_term(TOWN, 0, 1) == 0.0


@pytest.mark.parametrize("seed", range(4))
def test_slowest_time_delta_equals_a_full_rescore(seed: int) -> None:
    rng = np.random.Generator(np.random.PCG64(seed))
    backend, spatial = PythonBackend(TOWN), SpatialIndex(TOWN)
    snake = [0, 1, 2, 3, 4, 5, 6, 13, 12, 11, 10, 9, 8, 7, 14, 15, 16, 17, 18, 19, 20]
    if seed % 2:
        snake = [24, 25, 26, 33, 32, 31, 30, 23, 24]  # a round trip from a stop sign
    objective = slowest()
    route = route_on(TOWN, snake)
    score, metrics = objective.evaluate(route.nodes, route.edges)
    route = Route.new(
        TOWN, route.nodes, route.edges, route.stop_idx, score=score, metrics=metrics, seed=0
    )
    checked = 0
    for _ in range(150):
        i = int(rng.integers(len(route.edges)))
        j = min(i + int(rng.integers(1, 4)), len(route.edges))
        forbid, banned = route.release(TOWN, i, j)
        found = find_detour(
            backend, spatial, TOWN, route, i, j,
            forbid=forbid, banned=banned, node_mask=np.ones(TOWN.n, dtype=bool),
            radius=300.0, objective=objective, rng=rng, entry_edge=-1, exit_edge=-1,
        )  # fmt: skip
        if found is None:
            continue
        delta = objective.delta(route, i, j, *found)
        nodes, edges = splice_arrays(route, i, j, *found)
        score, metrics = objective.evaluate(nodes, edges)
        assert delta == pytest.approx(score - route.score, abs=1e-9)
        route = route.splice(TOWN, i, j, *found, score=score, metrics=metrics)
        checked += 1
    assert checked >= 10
