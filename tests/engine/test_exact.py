"""The certified worst (`engine/exact.py`): the provably longest legal route in a small area.

The oracle is enumeration: networkx's simple paths between the stops within the area, each
checked by `check_route` (the rules `Route.splice` and `Route.new` apply), their longest equal
to the certified route's length, on the board, on a lattice whose longest path a twin and a
turn table forbid, and on fixtures built through the pipeline: turn restrictions (where the
rules forbid the longest paths of most pairs), a divided road's twins, and a diamond's,
a cloverleaf's and a roundabout's super-junction (each pair there joined by a path or two,
so the oracle is checked rather than stretched). Lengths and legality are asserted, never a
route's bytes: the route among equally long ones is the solver's choice."""

import math
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

from tests.pipeline.chain import (
    FIXTURES,
    OSM_FIXTURES,
    cleaned,
    fixture_build,
    poi_table,
    restrictions_of,
    tier_nodes,
)
from tests.pipeline.copies import META
from worst_route.engine import exact
from worst_route.engine.exact import AreaTooLarge, Incumbent, certified_area, certified_request
from worst_route.engine.io import route_document
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.route import IllegalSplice, Route, check_route
from worst_route.engine.validator import validate_route
from worst_route.engine.worsen import ProgressEvent
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import GRID_ORIGIN, Edge, grid_graph, make_graph
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.save import graph_data

from .exact_parity import check_areas
from .graphs import at_node, board, lattice

X0, Y0 = GRID_ORIGIN
BOARD = board(parallel=True)


def certified(g: GraphData, stops: Sequence[int], **fields: object) -> RouteRequest:
    values: dict[str, object] = {"seed": 1, "budget_s": 30, **fields}
    return RouteRequest(stops=[at_node(g, v) for v in stops], certified=True, **values)


def longest(g: GraphData, request: RouteRequest, stops: Sequence[int]) -> tuple[float, float, int]:
    """The longest legal route's length and the longest simple path's, among the simple paths
    from the first stop to the last within the request's area that pass the stops in order, and
    how many of them the rules refuse."""
    masks = request_masks(SpatialIndex(g), g, request)
    area = certified_area(g, masks, stops)
    assert area is not None
    graph = nx.MultiDiGraph()
    for e in area.edges.tolist():
        graph.add_edge(int(g.edge_u[e]), int(g.edge_v[e]), key=e)
    legal = anything = 0.0
    refused = 0
    for path in nx.all_simple_edge_paths(graph, stops[0], stops[-1]):
        edges = np.array([e for _, _, e in path], dtype=np.int32)
        nodes = np.array([stops[0], *(v for _, v, _ in path)], dtype=np.int32)
        if not all(v in nodes for v in stops):
            continue
        idx = [int(np.flatnonzero(nodes == v)[0]) for v in stops]
        if idx != sorted(idx):
            continue
        length = math.fsum(g.edge_length_m[edges].astype(np.float64).tolist())
        anything = max(anything, length)
        try:
            check_route(g, nodes, edges, idx, masks.banned)
        except IllegalSplice:
            refused += 1
            continue
        legal = max(legal, length)
    return legal, anything, refused


def proven(g: GraphData, request: RouteRequest) -> float:
    """The certified route's length, proven, validated, its stops where they were asked."""
    result = plan_route(g, request, backend=PythonBackend(g))
    assert result.report.ok
    assert result.certified is not None and result.certified.status == "optimal"
    assert result.run.stopped_because == "optimal" and result.certified.gap == 0.0
    assert result.certified.bound_m == result.route.metrics.length_m
    return result.route.metrics.length_m


@pytest.mark.parametrize(
    "stops", [[0, 24], [0, 4], [20, 4], [6, 18], [24, 0], [0, 1], [0, 12, 24], [20, 7, 4]]
)
def test_the_board_s_certified_length_is_its_longest_legal_route(stops: list[int]) -> None:
    request = certified(BOARD, stops)
    legal, _, refused = longest(BOARD, request, stops)
    assert refused > 0  # the twins, the one-ways' U-turns and node 12's straight ways bite
    assert proven(BOARD, request) == pytest.approx(legal, abs=1e-6)


def ruled_lattice(rules: bool) -> GraphData:
    """A 3 × 4 two-way lattice (node r * 4 + c) of streets 100-149 m long; with `rules`, the
    second and fourth edges of the longest path from 0 to 11 without them are twins, and its
    turn from its fifth edge into its sixth is the one its node's table leaves out."""
    rng = np.random.default_rng(7)
    xy = [(X0 + 100.0 * c, Y0 + 100.0 * r) for r in range(3) for c in range(4)]
    streets = [(a, a + 1) for a in range(12) if (a + 1) % 4]
    streets += [(a, a + 4) for a in range(8)]
    edges: list[Edge] = []
    for a, b in streets:
        length = 100.0 + float(rng.integers(50))
        edges += [Edge(a, b, length), Edge(b, a, length)]
    plain = make_graph(xy, edges)
    if not rules:
        return plain
    graph = nx.MultiDiGraph()
    for e in range(plain.m):
        graph.add_edge(int(plain.edge_u[e]), int(plain.edge_v[e]), key=e)
    best = max(
        nx.all_simple_edge_paths(graph, 0, 11),
        key=lambda p: sum(float(plain.edge_length_m[e]) for _, _, e in p),
    )
    pairs = [(u, v) for u, v, _ in best]
    via = pairs[4][1]
    allowed = [
        ((a, via), (via, b))
        for a in range(12)
        for b in range(12)
        if (a, via) in [(e.u, e.v) for e in edges]
        and (via, b) in [(e.u, e.v) for e in edges]
        and a != b
        and ((a, via), (via, b)) != (pairs[4], pairs[5])
    ]
    return make_graph(xy, edges, twins=[(pairs[1], pairs[3])], transitions=allowed)


def test_a_twin_and_a_turn_table_forbid_the_longest_path_and_the_certificate_honours_them() -> None:
    plain, ruled = ruled_lattice(False), ruled_lattice(True)
    unruled = proven(plain, certified(plain, [0, 11]))
    request = certified(ruled, [0, 11])
    legal, anything, refused = longest(ruled, request, [0, 11])
    assert anything == pytest.approx(unruled) and legal < anything and refused > 0
    assert proven(ruled, request) == pytest.approx(legal, abs=1e-6)


def built(name: str, tmp_path: Path) -> GraphData:
    """A fixture through cleanup (with its turn restrictions), enrich and save's arrays."""
    source = FIXTURES / f"{name}.osm"
    table = restrictions_of(source, tmp_path) if name == "restrictions" else None
    graph = cleaned(source, restrictions=table, allow_fragmented=True)
    run_enrich(graph, poi_table([]))
    return graph_data(graph, tier_nodes(graph), META)


@pytest.mark.parametrize(
    ("name", "pairs"),
    [
        ("restrictions", [(23, 14), (23, 12), (13, 14), (9, 14), (12, 19), (19, 11)]),
        ("divided", [(14, 13), (13, 4), (0, 9)]),
        ("diamond", [(2, 0), (4, 0), (1, 4)]),
        ("cloverleaf", [(5, 2), (7, 3), (3, 1)]),
        ("roundabout", [(3, 2), (1, 2)]),
    ],
)
def test_on_built_fixtures_the_certified_length_is_the_longest_legal_route(
    name: str, pairs: list[tuple[int, int]], tmp_path: Path
) -> None:
    g = built(name, tmp_path)
    shorter = 0
    for s, t in pairs:
        request = certified(g, [s, t])
        legal, anything, _ = longest(g, request, [s, t])
        assert proven(g, request) == pytest.approx(legal, abs=1e-6)
        shorter += legal < anything
    if name == "restrictions":  # the rules forbid the longest path of most of these pairs
        assert shorter >= 4


def test_every_route_the_solver_hands_on_is_legal_and_longer_than_the_last() -> None:
    g = lattice(5, 6)
    seen: list[ProgressEvent] = []
    result = plan_route(
        g, certified(g, [0, 29]), backend=PythonBackend(g), on_progress=lambda e, _: seen.append(e)
    )
    assert seen and all(isinstance(e, Incumbent) for e in seen)
    lengths = [e.metrics.length_m for e in seen]
    assert lengths == sorted(set(lengths)) and lengths[-1] == result.route.metrics.length_m
    for e in seen:
        assert isinstance(e, Incumbent) and e.splice.i == 0
        route = Route.new(
            g, e.splice.nodes, e.splice.edges, e.stop_idx, score=e.score, metrics=e.metrics, seed=1
        )
        report = validate_route(
            g,
            route,
            stop_nodes=[0, 29],
            node_mask=result.masks.active,
            banned_edges=result.masks.banned,
        )
        assert report.ok, report.lines()
        assert e.bound_m >= e.metrics.length_m - 1e-6 and e.gap >= 0
    assert result.run.accepted == len(seen) and result.run.attempts >= len(seen)


def test_a_better_route_whose_bound_lies_below_it_carries_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ortools.sat.python import cp_model

    # CP-SAT's bound never lies below a solution it reports; were it to, the event says nothing
    below = property(lambda _: 0.0)
    monkeypatch.setattr(cp_model.CpSolverSolutionCallback, "best_objective_bound", below)
    g = lattice(5, 6)
    seen: list[ProgressEvent] = []
    result = plan_route(
        g, certified(g, [0, 29]), backend=PythonBackend(g), on_progress=lambda e, _: seen.append(e)
    )
    assert seen and result.report.ok
    assert all(isinstance(e, Incumbent) and (e.bound_m, e.gap) == (None, None) for e in seen)


def test_the_area_holds_only_what_a_walk_from_the_start_to_the_end_can_pass() -> None:
    # 0 → 1 → 2 → 3 (the end), 1 → 4 a dead end, 3 → 5 → 3 behind the end, 6 → 1 nowhere from
    # the start, and 2 → 0 back into the start
    xy = [(X0 + 100.0 * k, Y0) for k in range(4)] + [(X0 + 100, Y0 + 100)]
    xy += [(X0 + 300, Y0 + 100), (X0 + 100, Y0 - 100)]
    edges = [(0, 1), (1, 2), (2, 3), (1, 4), (3, 5), (5, 3), (6, 1), (2, 0)]
    g = make_graph(xy, [Edge(a, b, None) for a, b in edges])
    masks = request_masks(SpatialIndex(g), g, certified(g, [0, 3]))
    area = certified_area(g, masks, [0, 3])
    assert area is not None
    assert np.flatnonzero(area.nodes).tolist() == [0, 1, 2, 3]
    pairs = [(int(g.edge_u[e]), int(g.edge_v[e])) for e in area.edges]
    assert pairs == [(0, 1), (1, 2), (2, 3)]
    assert certified_area(g, masks, [3, 0]) is None  # nothing leads back


@pytest.mark.slow
def test_the_areas_and_automatic_leashes_are_the_ones_scipy_s_reach_gives() -> None:
    # 200 random requests on a 20 × 20 lattice and on one with one-way rows and columns (two or
    # three stops within 800 m, a leash up to 500 m or the automatic one, too wide for the whole
    # lattice), then 20 on each OSM fixture's build: each area node for node and edge for edge,
    # each automatic leash
    ways = "+-." * 6 + "+-"
    oneway = grid_graph(20, 20, 100.0, oneway_rows=ways, oneway_cols=ways[::-1])
    joined = check_areas(lattice(20, 20), 100, seed=1, near_m=800.0, leash_m=500.0)
    joined += check_areas(oneway, 100, seed=2, near_m=800.0, leash_m=500.0)
    assert joined > 100
    for k, name in enumerate(OSM_FIXTURES):
        check_areas(fixture_build(name), 20, seed=10 + k, near_m=400.0, leash_m=300.0)


def test_an_explicit_leash_over_the_caps_is_refused_with_the_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    g = lattice(6, 6)
    monkeypatch.setattr(exact, "CERTIFIED_MAX_EDGES", 40)
    with pytest.raises(AreaTooLarge) as refused:
        plan_route(g, certified(g, [0, 35], leash_km=1.0), backend=PythonBackend(g))
    error = refused.value
    # all 120 edges but the 2 into the start and the 2 out of the end
    assert (error.nodes, error.edges, error.max_edges, error.leash_m) == (36, 116, 40, 1000.0)
    assert error.field == "leash" and error.max_nodes == exact.CERTIFIED_MAX_NODES
    assert str(error) == (
        "This area is too big to prove. It holds 36 junctions and 116 roads; a proof allows 300 "
        "and 40. Shorten the corridor or bring the stops closer."
    )


def test_the_automatic_leash_is_the_widest_whose_area_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    g = lattice(10, 10)
    monkeypatch.setattr(exact, "CERTIFIED_AUTO_MAX_EDGES", 100)
    spatial = SpatialIndex(g)
    request = certified(g, [33, 66])
    leashed = certified_request(g, spatial, request)
    assert isinstance(leashed.leash_km, float)
    leash_m = round(leashed.leash_km * 1000)
    assert leash_m % 10 == 0 and leash_m >= 50

    def edges_at(m: float) -> int:
        masks = request_masks(spatial, g, request.model_copy(update={"leash_km": m / 1000}))
        area = certified_area(g, masks, [33, 66])
        return 0 if area is None else area.edge_count

    assert 0 < edges_at(leash_m) <= 100 < edges_at(leash_m + 10)
    result = plan_route(g, request, backend=PythonBackend(g))
    assert result.request.leash_km == leashed.leash_km
    assert result.masks.leash_m == pytest.approx(leash_m)
    assert result.certified is not None and result.certified.leash_m == leash_m
    assert result.certified.edges == edges_at(leash_m)
    explicit = request.model_copy(update={"leash_km": 0.25})
    assert certified_request(g, spatial, explicit) is explicit


def test_with_no_small_area_joining_the_stops_the_narrowest_within_the_caps_is_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    g = lattice(10, 10)
    spatial = SpatialIndex(g)
    request = certified(g, [0, 99])
    monkeypatch.setattr(exact, "CERTIFIED_AUTO_MAX_EDGES", 5)  # no leash joins them that small
    leashed = certified_request(g, spatial, request)
    assert isinstance(leashed.leash_km, float)
    masks = request_masks(spatial, g, leashed)
    area = certified_area(g, masks, [0, 99])
    narrower = request.model_copy(update={"leash_km": (round(leashed.leash_km * 1000) - 10) / 1000})
    assert area is not None
    assert certified_area(g, request_masks(spatial, g, narrower), [0, 99]) is None
    monkeypatch.setattr(exact, "CERTIFIED_MAX_EDGES", area.edge_count - 1)
    with pytest.raises(AreaTooLarge, match="Even the narrowest corridor that joins the stops"):
        certified_request(g, spatial, request)


def test_the_length_is_the_same_whatever_the_seed_when_proven() -> None:
    lengths = {proven(BOARD, certified(BOARD, [20, 4], seed=seed)) for seed in (1, 2, 3, 2**40)}
    assert len(lengths) == 1


def test_a_short_budget_never_states_a_false_bound() -> None:
    # 0 → 5 on a 6 × 6 lattice: the longest legal route visits all 36 nodes, 3.5 km
    g = lattice(6, 6)
    assert proven(g, certified(g, [0, 5])) == pytest.approx(3500)
    for budget_s in (1e-9, 0.02, 0.05):
        result = plan_route(g, certified(g, [0, 5], budget_s=budget_s), backend=PythonBackend(g))
        assert result.report.ok and result.certified is not None
        assert result.route.metrics.length_m >= 500  # the seed's
        certificate = result.certified
        if certificate.status == "optimal":
            continue
        assert result.run.stopped_because == "budget"
        assert (certificate.bound_m is None) == (certificate.gap is None)
        if certificate.bound_m is not None:  # a bound on every legal route, never the route itself
            assert certificate.bound_m >= 3500 - 1e-6 and certificate.gap is not None
            assert certificate.gap >= (3500 - result.route.metrics.length_m) / 3500 - 1e-9


def test_a_solve_ended_before_any_solution_keeps_the_seed_with_no_bound() -> None:
    # the limit comes before CP-SAT takes in the hint: UNKNOWN, no route of its own, no bound
    g = lattice(6, 6)
    result = plan_route(g, certified(g, [0, 5], budget_s=1e-9), backend=PythonBackend(g))
    assert result.report.ok and result.route.metrics.length_m == 500  # the seed
    assert result.certified is not None
    assert (result.certified.status, result.certified.bound_m, result.certified.gap) == (
        "feasible",
        None,
        None,
    )
    assert (result.run.stopped_because, result.run.accepted) == ("budget", 0)
    doc = route_document(g, result)
    assert doc["run"]["certified"]["bound_m"] is None and doc["run"]["certified"]["gap"] is None


def test_a_model_the_solver_finds_infeasible_is_a_bug_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = exact._model

    def contradicted(data: GraphData, area: exact.Area, route: Route) -> exact._Model:
        built = real(data, area, route)
        x = built.model.new_bool_var("x")
        built.model.add(x == 1)
        built.model.add(x == 0)
        return built

    monkeypatch.setattr(exact, "_model", contradicted)
    with pytest.raises(RuntimeError, match="CP-SAT ended INFEASIBLE on a model with a solution"):
        plan_route(BOARD, certified(BOARD, [0, 24]), backend=PythonBackend(BOARD))


def test_the_route_file_carries_the_certificate_and_the_leash_used() -> None:
    result = plan_route(BOARD, certified(BOARD, [0, 24]), backend=PythonBackend(BOARD))
    doc = route_document(BOARD, result)
    assert doc["request"]["certified"] is True and doc["request"]["leash_km"] == 3.0
    assert doc["run"]["stopped_because"] == "optimal"
    certificate = doc["run"]["certified"]
    assert certificate == {
        "status": "optimal",
        "bound_m": doc["metrics"]["length_m"],
        "gap": 0.0,
        "nodes": 25,
        "edges": 69,
        "leash_m": 3000.0,
        "solve_s": certificate["solve_s"],
        "workers": exact.CERTIFIED_WORKERS,
    }
    plain = plan_route(BOARD, RouteRequest(stops=[at_node(BOARD, 0), at_node(BOARD, 24)], seed=1))
    assert "certified" not in route_document(BOARD, plain)["run"]


def test_a_certified_route_has_no_base_route() -> None:
    result = plan_route(BOARD, certified(BOARD, [0, 24]), backend=PythonBackend(BOARD))
    with pytest.raises(ValueError, match="no base route"):
        plan_route(BOARD, certified(BOARD, [0, 24]), base=result.route)


def test_planning_without_certified_never_imports_or_tools() -> None:
    code = (
        "import sys, worst_route.api.worker, worst_route.engine.plan, worst_route.cli; "
        "print('ortools' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
