import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from worst_route.config import COLLECT_ITEM_WEIGHT
from worst_route.engine.detour import find_detour
from worst_route.engine.io import read_route, route_document
from worst_route.engine.objectives.brand import Brand, BrandRefused, brand_counts, brand_problem
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.route import Metrics, Route
from worst_route.engine.target import Target
from worst_route.engine.validator import validate_route
from worst_route.engine.worsen import Budget, run_worsen
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex

from .graphs import at_node, lattice, route_on

# A 6 × 6 lattice (node r * 6 + c, 100 m apart) with two brands: 0 Acme at five nodes, 1 Zed at
# two.
TOWN = lattice(6, 6, brands={"Zed": [3, 20], "Acme": [1, 9, 14, 22, 33]})
ACME = 0
TOUR = [0, 1, 2, 3, 9, 15, 14, 8]  # Acme at 1, 9 and 14; Zed at 3
EVERYWHERE = np.ones(TOWN.n, dtype=bool)
NOWHERE = np.zeros(TOWN.m, dtype=bool)


def pilgrimage(area: np.ndarray | None = None, brand: int = ACME) -> Brand:
    return Brand(TOWN, brand, baseline=Metrics(500.0, 45.0), r_max=10_000.0, area=area)


def scored(objective: Brand, route: Route) -> Route:
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


def test_brand_counts_count_each_visited_node_once() -> None:
    route = route_on(TOWN, TOUR)
    assert brand_counts(TOWN, ACME, route.nodes) == {
        "id": 0,
        "name": "Acme",
        "visits": 3,
        "of": 5,
    }
    assert brand_counts(TOWN, 1, route.nodes) == {"id": 1, "name": "Zed", "visits": 1, "of": 2}
    area = EVERYWHERE.copy()
    area[[22, 33]] = False
    assert brand_counts(TOWN, ACME, route.nodes, area)["of"] == 3


def test_the_score_is_visits_first_then_length() -> None:
    route = route_on(TOWN, TOUR)
    objective = pilgrimage()
    score, metrics = objective.evaluate(route.nodes, route.edges)
    assert metrics.length_m == 700.0
    assert score == 3 * COLLECT_ITEM_WEIGHT + 700.0
    counts = {"id": 0, "name": "Acme", "visits": 3, "of": 5}
    assert metrics.counters == {"brand": counts}
    assert objective.target_value(TOWN, route) == 3
    assert objective.describe(TOWN, route) == counts
    assert objective.search_weights(TOWN) is objective._lengths
    assert not objective.has_pair and not objective.has_global
    assert objective.primary(metrics) == 700.0


def test_a_round_trip_s_start_counts_once() -> None:
    route = route_on(TOWN, [1, 2, 8, 7, 1])
    score, metrics = pilgrimage().evaluate(route.nodes, route.edges)
    assert metrics.counters["brand"]["visits"] == 1
    assert score == COLLECT_ITEM_WEIGHT + 400.0
    assert brand_counts(TOWN, ACME, route.nodes)["visits"] == 1


def test_the_metrics_are_brand_counts_in_the_area() -> None:
    area = EVERYWHERE.copy()
    area[33] = False
    route = route_on(TOWN, [8, 14, 20, 21, 22])
    counters = pilgrimage(area).evaluate(route.nodes, route.edges)[1].counters
    assert counters == {"brand": brand_counts(TOWN, ACME, route.nodes, area)}
    assert counters["brand"]["visits"] == 2 and counters["brand"]["of"] == 4


@pytest.mark.parametrize("brand", [-1, 2])
def test_an_unknown_brand_is_refused(brand: int) -> None:
    with pytest.raises(ValueError, match=f"unknown brand {brand}"):
        pilgrimage(brand=brand)


@pytest.mark.parametrize("seed", range(4))
def test_delta_equals_a_full_rescore(seed: int) -> None:
    # Random windows of one route, a third of them at its start (on a brand node) and a third at
    # its end; the round trip's start is one of the brand's nodes.
    rng = np.random.Generator(np.random.PCG64(seed))
    backend, spatial = PythonBackend(TOWN), SpatialIndex(TOWN)
    objective = pilgrimage()
    nodes = [1, 2, 3, 9, 15, 14, 13, 19, 25]
    if seed % 2:
        nodes = [9, 15, 14, 13, 7, 1, 2, 3, 9]  # a round trip from an Acme node
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
            forbid=forbid, banned=banned, node_mask=EVERYWHERE,
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


def test_the_waypoint_pool_is_the_brand_s_nodes_the_route_has_not_visited() -> None:
    objective = pilgrimage()
    route = route_on(TOWN, [0, 1, 2, 3])
    pool = objective.waypoint_pool(TOWN, route)
    assert pool is not None and pool.tolist() == [9, 14, 22, 33]
    assert objective.waypoint_pool(TOWN, route) is pool  # worked out once per route
    area = EVERYWHERE.copy()
    area[[22, 33]] = False
    found = pilgrimage(area).waypoint_pool(TOWN, route)
    assert found is not None and found.tolist() == [9, 14]
    zed = pilgrimage(brand=1)
    assert zed.waypoint_pool(TOWN, route_on(TOWN, [2, 3, 4, 10, 16, 22, 21, 20])) is None
    area[[9, 14]] = False
    assert pilgrimage(area).waypoint_pool(TOWN, route) is None  # none in the area


@pytest.mark.parametrize(
    ("baseline", "r_max", "radius"),
    [(500.0, 10_000.0, 2000.0), (10_000.0, 10_000.0, 5000.0), (10_000.0, 3000.0, 3000.0)],
)
def test_initial_radius(baseline: float, r_max: float, radius: float) -> None:
    objective = Brand(TOWN, ACME, baseline=Metrics(baseline, 0.0), r_max=r_max)
    assert objective.initial_radius(route_on(TOWN, [0, 1])) == radius


def scattered() -> GraphData:
    """A 10 × 10 lattice with one brand at every seventh node off its bottom row."""
    return lattice(10, 10, brands={"Pilgrim Coffee": list(range(10, 100, 7))})


def test_a_pilgrimage_never_gives_a_visit_back_and_runs_the_same_on_both_backends() -> None:
    g = scattered()
    everything = np.ones(g.n, dtype=bool)
    best: list[Route] = []
    for backend_type in (PythonBackend, CsrBackend):
        objective = Brand(g, 0, baseline=Metrics(900.0, 81.0), r_max=2000.0, area=everything)
        route = scored(objective, route_on(g, list(range(10))))
        result = run_worsen(
            backend_type(g), SpatialIndex(g), g, route, objective,
            Budget(seconds=600, max_iterations=300), np.random.Generator(np.random.PCG64(3)),
            everything, r_max=2000.0, check_delta=True,
        )  # fmt: skip
        visits = [0] + [e.metrics.counters["brand"]["visits"] for e in result.events]
        assert all(b >= a for a, b in zip(visits, visits[1:], strict=False))
        assert visits[-1] >= 5 and result.best.metrics.counters["brand"]["visits"] == visits[-1]
        report = validate_route(
            g,
            result.best,
            stop_nodes=[0, 9],
            node_mask=everything,
            banned_edges=np.zeros(g.m, dtype=bool),
        )
        assert report.ok
        best.append(result.best)
    first, second = best
    assert first.nodes.tobytes() == second.nodes.tobytes()
    assert first.edges.tobytes() == second.edges.tobytes()


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"visits": 4}, "recounted"),
        ({"of": 4}, "recounted"),
        ({"name": "Zed"}, "recounted"),
        ({"id": 1}, "recounted"),
        ({"id": 2}, "no such brand"),
        ({"id": "0"}, "no such brand"),
        ({"id": True}, "no such brand"),  # a bool, though True == 1
        ({"id": None}, "no such brand"),
    ],
)
def test_the_validator_recounts_a_reported_brand(change: dict[str, Any], detail: str) -> None:
    objective = pilgrimage()
    route = scored(objective, route_on(TOWN, TOUR))

    def validated(r: Route) -> Any:
        return validate_route(
            TOWN, r, stop_nodes=[0, 8], node_mask=EVERYWHERE, banned_edges=NOWHERE
        ).check("metrics")

    assert validated(route).ok
    reported = {**route.metrics.counters["brand"], **change}
    tampered = replace(route, metrics=replace(route.metrics, counters={"brand": reported}))
    check = validated(tampered)
    assert not check.ok and detail in check.detail
    area = EVERYWHERE.copy()
    area[33] = False  # the request's area holds four of Acme's five nodes, not the reported five
    report = validate_route(TOWN, route, stop_nodes=[0, 8], node_mask=area, banned_edges=NOWHERE)
    assert not report.check("metrics").ok


def test_a_request_plans_a_pilgrimage_the_same_on_both_backends(tmp_path: Path) -> None:
    g = scattered()
    request = RouteRequest(
        stops=[at_node(g, 0), at_node(g, 9)], seed=5, objective="brand", brand=0, max_iterations=400
    )
    documents = []
    for backend_type in (PythonBackend, CsrBackend):
        result = plan_route(g, request, backend=backend_type(g), check_delta=True)
        assert result.report.ok
        documents.append(route_document(g, result))
    first, second = documents
    assert first["edges"] == second["edges"] and first["metrics"] == second["metrics"]
    assert first["request"]["brand"] == 0
    brand = first["metrics"]["brand"]
    assert brand["name"] == "Pilgrim Coffee" and brand["of"] == 13 and brand["visits"] >= 5
    assert brand == brand_counts(g, 0, np.array(first["nodes"]))

    path = tmp_path / "route.json"
    path.write_text(json.dumps(first))
    saved = read_route(path)
    assert saved.metrics.counters == {"items": first["metrics"]["items"], "brand": brand}
    masks = request_masks(SpatialIndex(g), g, saved.request)

    def check(route: Any) -> bool:
        report = validate_route(
            g, route, stop_nodes=[0, 9], node_mask=masks.active, banned_edges=masks.banned
        )
        return report.ok

    assert check(saved)
    path.write_text(
        json.dumps(
            {
                **first,
                "metrics": {**first["metrics"], "brand": {**brand, "visits": brand["visits"] - 1}},
            }
        )
    )
    assert not check(read_route(path))


def test_a_brand_the_build_does_not_have_or_the_area_does_not_hold_is_refused() -> None:
    g = scattered()
    stops = [at_node(g, 0), at_node(g, 2)]
    unknown = RouteRequest(stops=stops, seed=1, objective="brand", brand=1)
    with pytest.raises(BrandRefused, match="^unknown brand 1$") as refused:
        plan_route(g, unknown, backend=PythonBackend(g))
    assert refused.value.field == "brand"
    # a 50 m leash along the bottom row: none of the brand's nodes, which lie above it
    narrow = RouteRequest(stops=stops, seed=1, objective="brand", brand=0, leash_km=0.05)
    with pytest.raises(BrandRefused, match="^no Pilgrim Coffee within reach$"):
        plan_route(g, narrow, backend=PythonBackend(g))
    assert brand_problem(g, 0, None) is None and brand_problem(g, -1) == "unknown brand -1"


def test_a_pilgrimage_reaches_a_duration_target() -> None:
    g = scattered()
    request = RouteRequest(
        stops=[at_node(g, 0), at_node(g, 9)],
        seed=2,
        objective="brand",
        brand=0,
        target=Target(kind="duration", value=600),  # the baseline drives 900 m in 81 s
    )
    result = plan_route(g, request, backend=PythonBackend(g), check_delta=True)
    assert result.run.stopped_because == "target"
    assert 588 <= result.route.metrics.time_s <= 612
    assert result.route.metrics.counters["brand"]["visits"] > 0
