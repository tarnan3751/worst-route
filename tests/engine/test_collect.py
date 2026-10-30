import math
from dataclasses import replace

import numpy as np
import pytest

from worst_route.config import COLLECT_ITEM_WEIGHT
from worst_route.engine.detour import find_detour
from worst_route.engine.objectives.collect import ITEMS, Collect, CollectItem, count_items
from worst_route.engine.route import Metrics, Route
from worst_route.engine.validator import validate_route
from worst_route.engine.worsen import Budget, run_worsen
from worst_route.graph.data import EdgeFlag, GraphData, NodeKind
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import Edge, edge_id, make_graph

from .graphs import lattice, route_on

BRIDGE = {"flags": EdgeFlag.BRIDGE}


def town() -> GraphData:
    """A 6 × 6 lattice (node r * 6 + c, 100 m apart) with one of everything: a toll booth on
    0–1, two level crossings on 3–9, bridges on 1–2, 2–3 (one bridge split by a junction) and
    14–15, a roundabout at node 9 and an interchange holding two rings at node 14; the two
    directions of a road are one unit, as in a saved build."""
    g = lattice(
        6,
        6,
        roads={
            (0, 1): {"toll_booths": 1},
            (3, 9): {"level_crossings": 2},
            (1, 2): BRIDGE,
            (2, 3): BRIDGE,
            (14, 15): BRIDGE,
        },
    )
    kind, rings = g.node_kind.copy(), g.node_roundabouts.copy()
    kind[[9, 14]] = NodeKind.ROUNDABOUT, NodeKind.INTERCHANGE
    rings[[9, 14]] = 1, 2
    reverse = [edge_id(g, v, u) for u, v in zip(g.edge_u.tolist(), g.edge_v.tolist(), strict=True)]
    unit = np.minimum(np.arange(g.m), reverse).astype(np.int32)
    g = replace(g, node_kind=kind, node_roundabouts=rings, edge_unit=unit)
    g.validate()
    return g


TOWN = town()
# 0→1 toll, 1→2→3 one bridge, 3→9 two crossings, 9 a ring, 15→14 a bridge, 14 two rings
TOUR = [0, 1, 2, 3, 9, 15, 14, 8]
TOUR_ITEMS = {"toll_booth": 1, "level_crossing": 2, "roundabout": 3, "bridge": 2}


def collect(item: CollectItem, area: np.ndarray | None = None) -> Collect:
    return Collect(TOWN, item, baseline=Metrics(500.0, 45.0), r_max=10_000.0, area=area)


def scored(objective: Collect, route: Route) -> Route:
    score, metrics = objective.evaluate(route.nodes, route.edges)
    return Route.new(
        objective.data,
        route.nodes,
        route.edges,
        route.stop_idx,
        score=score,
        metrics=metrics,
        seed=0,
    )


def test_count_items_counts_every_item_on_a_route() -> None:
    route = route_on(TOWN, TOUR)
    assert count_items(TOWN, route.nodes, route.edges) == TOUR_ITEMS
    assert set(TOUR_ITEMS) == set(ITEMS)


@pytest.mark.parametrize("item", ITEMS)
def test_the_score_is_items_first_then_length(item: CollectItem) -> None:
    route = route_on(TOWN, TOUR)
    objective = collect(item)
    score, metrics = objective.evaluate(route.nodes, route.edges)
    assert metrics.length_m == 700.0
    assert score == TOUR_ITEMS[item] * COLLECT_ITEM_WEIGHT + 700.0
    assert metrics.counters == {"item": item, "items": TOUR_ITEMS}
    assert objective.target_value(TOWN, route) == TOUR_ITEMS[item]
    assert objective.search_weights(TOWN) is objective._lengths
    assert objective.has_pair == (item == "bridge")


@pytest.mark.parametrize(
    ("nodes", "runs"),
    [
        ([1, 2, 3], 1),  # starts on the bridge: the first-edge term counts it
        ([2, 3, 4], 1),  # starts on its second half
        ([0, 1, 2, 3, 4], 1),  # one bridge split by a junction
        ([3, 2, 1, 0], 1),
        ([16, 15, 14, 13], 1),
        ([1, 2, 8, 14, 15], 2),
        ([0, 6, 12], 0),
    ],
)
def test_bridges_count_by_runs(nodes: list[int], runs: int) -> None:
    route = route_on(TOWN, nodes)
    score, metrics = collect("bridge").evaluate(route.nodes, route.edges)
    assert metrics.counters["items"]["bridge"] == runs
    assert score == runs * COLLECT_ITEM_WEIGHT + metrics.length_m


def test_a_roundabout_counts_once_on_a_round_trip_that_starts_there() -> None:
    route = route_on(TOWN, [9, 10, 16, 15, 9])
    score, metrics = collect("roundabout").evaluate(route.nodes, route.edges)
    assert metrics.counters["items"]["roundabout"] == 1
    assert score == COLLECT_ITEM_WEIGHT + 400.0
    passing = route_on(TOWN, [8, 14, 20])  # the interchange's two rings, once
    assert collect("roundabout").score(passing) == 2 * COLLECT_ITEM_WEIGHT + 200.0


def test_a_bridge_run_through_a_round_trip_s_start_counts_once() -> None:
    # Bridge 1=2=3 with a junction at 2, and a loop 3-6-7-1 off its ends: the round trip from 2
    # drives 2-3 first and 1-2 last, one crossing of one bridge, as when it starts at 6.
    nodes = [(0.0, 0.0), (0.0, 100.0), (100.0, 100.0), (200.0, 100.0)]
    nodes += [(0.0, 200.0), (300.0, 0.0), (200.0, 0.0), (100.0, 0.0)]
    roads = [(1, 2, BRIDGE), (2, 3, BRIDGE), (3, 6, {}), (6, 7, {}), (7, 1, {}), (0, 1, {})]
    g = make_graph(nodes, [Edge(u, v, **f) for a, b, f in roads for u, v in ((a, b), (b, a))])
    objective = Collect(g, "bridge", baseline=Metrics(500.0, 45.0), r_max=10_000.0)
    for loop in ([2, 3, 6, 7, 1, 2], [6, 7, 1, 2, 3, 6], [2, 1, 7, 6, 3, 2]):
        route = route_on(g, loop)
        assert count_items(g, route.nodes, route.edges)["bridge"] == 1
        score, metrics = objective.evaluate(route.nodes, route.edges)
        assert score == COLLECT_ITEM_WEIGHT + metrics.length_m
    open_ended = route_on(g, [2, 3, 6, 7, 1])  # not a round trip: its start is a bridge's
    assert count_items(g, open_ended.nodes, open_ended.edges)["bridge"] == 1


def test_a_toll_booth_on_a_two_way_road_counts_in_the_direction_driven() -> None:
    for nodes in ([0, 1], [1, 0]):
        assert collect("toll_booth").score(route_on(TOWN, nodes)) == COLLECT_ITEM_WEIGHT + 100.0


@pytest.mark.parametrize("item", ITEMS)
@pytest.mark.parametrize("seed", range(4))
def test_delta_equals_a_full_rescore(item: CollectItem, seed: int) -> None:
    # Random windows of one route, a third of them at its start (on a bridge) and a third at its
    # end, so the first-edge term changes too; the round trip's start is inside a bridge run.
    rng = np.random.Generator(np.random.PCG64(seed))
    backend, spatial = PythonBackend(TOWN), SpatialIndex(TOWN)
    objective = collect(item)
    nodes = [1, 2, 3, 9, 15, 14, 13, 19, 25]
    if seed % 2:
        nodes = [2, 3, 9, 15, 14, 13, 7, 1, 2]  # a round trip from a bridge
    route = scored(objective, route_on(TOWN, nodes))
    checked = at_start = at_end = 0
    for _ in range(150):
        k, end, where = int(rng.integers(1, 4)), len(route.edges), rng.random()
        if where < 1 / 3:
            i, j = 0, min(k, end)
        elif where < 2 / 3:
            i, j = max(end - k, 0), end
        else:
            i = int(rng.integers(end))
            j = min(i + k, end)
        forbid, banned = route.release(TOWN, i, j)
        found = find_detour(
            backend, spatial, TOWN, route, i, j,
            forbid=forbid, banned=banned, node_mask=np.ones(TOWN.n, dtype=bool),
            radius=300.0, objective=objective, rng=rng, entry_edge=-1, exit_edge=-1,
        )  # fmt: skip
        if found is None:
            continue
        delta = objective.delta(route, i, j, *found)
        after = scored(
            objective, route.splice(TOWN, i, j, *found, score=0.0, metrics=route.metrics)
        )
        assert math.isclose(delta, after.score - route.score, rel_tol=1e-12, abs_tol=1e-6)
        checked += 1
        at_start += i == 0
        at_end += j == end
    assert checked >= 20 and at_start >= 5 and at_end >= 5


def test_the_waypoint_pool_is_the_items_the_route_has_not_collected() -> None:
    objective = collect("bridge")
    route = route_on(TOWN, [0, 1, 2, 3])
    pool = objective.waypoint_pool(TOWN, route)
    assert pool is not None and pool.tolist() == [14, 15]  # the other bridge's ends
    assert objective.waypoint_pool(TOWN, route) is pool  # worked out once per route
    for item, places in (("level_crossing", [9]), ("roundabout", [9, 14])):
        found = collect(item).waypoint_pool(TOWN, route)
        assert found is not None and found.tolist() == places
    assert collect("toll_booth").waypoint_pool(TOWN, route) is None  # collected: any node
    area = np.ones(TOWN.n, dtype=bool)
    area[[14, 15]] = False
    assert collect("bridge", area=area).waypoint_pool(TOWN, route) is None  # none in the area


@pytest.mark.parametrize(
    ("baseline", "r_max", "radius"),
    [(500.0, 10_000.0, 2000.0), (10_000.0, 10_000.0, 5000.0), (10_000.0, 3000.0, 3000.0)],
)
def test_initial_radius(baseline: float, r_max: float, radius: float) -> None:
    objective = Collect(TOWN, "bridge", baseline=Metrics(baseline, 0.0), r_max=r_max)
    assert objective.initial_radius(route_on(TOWN, [0, 1])) == radius


def test_describe_names_the_item_its_count_and_the_area_s() -> None:
    route = route_on(TOWN, TOUR)
    expected = {"toll_booth": 1, "level_crossing": 2, "roundabout": 3, "bridge": 3}
    for item in ITEMS:  # each road once: the toll booth's and the crossings' two directions
        described = collect(item).describe(TOWN, route)
        assert described == {
            "item": item,
            "count": TOUR_ITEMS[item],
            "candidates_in_area": expected[item],
        }
    area = np.zeros(TOWN.n, dtype=bool)
    area[[0, 1, 2, 9]] = True
    assert collect("bridge", area=area).describe(TOWN, route)["candidates_in_area"] == 1
    assert collect("roundabout", area=area).describe(TOWN, route)["candidates_in_area"] == 1


def sprinkled() -> GraphData:
    """A 10 × 10 lattice with items scattered off its bottom row: toll booths, crossings and
    bridges on every fifth road of the rows above, rings at every seventh node."""
    rows = cols = 10
    roads: dict[tuple[int, int], dict[str, int]] = {}
    pairs = [(a, a + 1) for a in range(cols, rows * cols) if a % cols < cols - 1]
    for k, (a, b) in enumerate(pairs):
        if k % 5 == 0:
            roads[(a, b)] = {"toll_booths": 1, "level_crossings": 1, "flags": EdgeFlag.BRIDGE}
    g = lattice(rows, cols, roads=roads)
    kind, rings = g.node_kind.copy(), g.node_roundabouts.copy()
    kind[cols::7], rings[cols::7] = NodeKind.ROUNDABOUT, 1
    return replace(g, node_kind=kind, node_roundabouts=rings)


@pytest.mark.parametrize("item", ITEMS)
def test_a_run_collects_and_never_gives_an_item_back(item: CollectItem) -> None:
    g = sprinkled()
    objective = Collect(g, item, baseline=Metrics(900.0, 81.0), r_max=2000.0)
    route = scored(objective, route_on(g, list(range(10))))
    everything = np.ones(g.n, dtype=bool)
    result = run_worsen(
        PythonBackend(g), SpatialIndex(g), g, route, objective,
        Budget(seconds=600, max_iterations=300), np.random.Generator(np.random.PCG64(3)),
        everything, r_max=2000.0, check_delta=True,
    )  # fmt: skip
    counts = [0] + [e.metrics.counters["items"][item] for e in result.events]
    assert all(b >= a for a, b in zip(counts, counts[1:], strict=False))
    assert counts[-1] >= 3 and result.best.metrics.counters["items"][item] == counts[-1]
    banned = np.zeros(g.m, dtype=bool)
    report = validate_route(
        g, result.best, stop_nodes=[0, 9], node_mask=everything, banned_edges=banned
    )
    assert report.ok
