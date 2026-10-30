from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pytest

from worst_route.config import (
    CONVERGED_EMPTY_CYCLES,
    CONVERGED_FAILS,
    SEGMENT_RUN_MAX,
    UNUSED_GRID_MAX_CELLS,
    HighwayClass,
)
from worst_route.engine.objectives import make_objective
from worst_route.engine.objectives.base import Objective
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RequestMasks, RouteRequest, request_masks
from worst_route.engine.route import IllegalSplice, Route
from worst_route.engine.seed_route import baseline_route, resolve_stops, seed_route
from worst_route.engine.snap import StopUnreachable
from worst_route.engine.validator import validate_route
from worst_route.engine.worsen import (
    Budget,
    ProgressEvent,
    UnusedNodes,
    pick_segment,
    run_worsen,
    worsen,
)
from worst_route.graph.backend import GraphBackend
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import Edge, EdgePair, make_graph

from .graphs import X0, Y0, at_node, lattice, route_on

GRID = lattice(20, 20)
BACKEND, SPATIAL = PythonBackend(GRID), SpatialIndex(GRID)
CORNERS = [at_node(GRID, 0), at_node(GRID, 399)]


@dataclass
class Start:
    """What plan_route builds before the loop, laid out so a test can drive the loop."""

    data: GraphData
    route: Route
    objective: Objective
    masks: RequestMasks
    rng: np.random.Generator
    r_max: float
    stop_nodes: list[int]


def start(data: GraphData, stops: Sequence[tuple[float, float]], seed: int) -> Start:
    backend, spatial = PythonBackend(data), SpatialIndex(data)
    request = RouteRequest(stops=list(stops), seed=seed)
    masks = request_masks(spatial, data, request)
    rng = np.random.Generator(np.random.PCG64(seed))
    r_max = min(masks.leash_m, masks.extent_m)
    snaps = resolve_stops(data, spatial, backend, request, masks)
    found = seed_route(backend, spatial, data, snaps, masks.active, masks.banned, rng, r_max=r_max)
    stop_nodes = [s.node for s in snaps]
    baseline = baseline_route(backend, data, stop_nodes, masks.active, masks.banned)
    objective = make_objective("distance", data, baseline_length_m=baseline.length_m, r_max=r_max)
    score, metrics = objective.evaluate(found.nodes, found.edges)
    route = Route.new(
        data, found.nodes, found.edges, found.stop_idx, score=score, metrics=metrics, seed=seed
    )
    return Start(data, route, objective, masks, rng, r_max, stop_nodes)


def run(s: Start, budget: Budget, check_delta: bool = True):
    return run_worsen(
        PythonBackend(s.data) if s.data is not GRID else BACKEND,
        SpatialIndex(s.data) if s.data is not GRID else SPATIAL,
        s.data,
        s.route,
        s.objective,
        budget,
        s.rng,
        s.masks.active,
        r_max=s.r_max,
        banned_edges=s.masks.banned,
        check_delta=check_delta,
    )


@pytest.mark.parametrize("seed", range(4))
def test_the_loop_only_improves_and_every_event_replays(seed: int) -> None:
    s = start(GRID, CORNERS, seed)
    result = run(s, Budget(seconds=600, max_iterations=250))
    assert result.accepted == len(result.events) > 5
    scores = [s.route.score] + [e.score for e in result.events]
    assert all(b > a for a, b in zip(scores, scores[1:], strict=False))
    assert [e.iteration for e in result.events] == sorted({e.iteration for e in result.events})

    replay = s.route
    for event in result.events:
        sp = event.splice
        replay = replay.splice(
            GRID, sp.i, sp.j, sp.nodes, sp.edges, score=event.score, metrics=event.metrics
        )
    assert replay.nodes.tobytes() == result.best.nodes.tobytes()
    assert result.best.score == scores[-1] == s.objective.score(result.best)

    report = validate_route(
        GRID,
        result.best,
        stop_nodes=s.stop_nodes,
        node_mask=s.masks.active,
        banned_edges=s.masks.banned,
    )
    assert report.ok, report.lines()


def test_a_budget_of_iterations_is_kept() -> None:
    result = run(start(GRID, CORNERS, 0), Budget(seconds=600, max_iterations=37))
    assert result.attempts == 37 and result.stopped_because == "budget"
    nothing = run(start(GRID, CORNERS, 0), Budget(seconds=600, max_iterations=0))
    assert (nothing.attempts, nothing.accepted, nothing.events) == (0, 0, ())


def test_a_budget_of_seconds_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    # no cycle ever ends, so only the clock can stop the run, however fast the machine
    monkeypatch.setattr("worst_route.engine.worsen.CONVERGED_FAILS", 10**9)
    g = lattice(30, 30)
    result = run(
        start(g, [at_node(g, 0), at_node(g, 899)], 0),
        Budget(seconds=0.25, max_iterations=10**9),
        check_delta=False,
    )
    assert result.stopped_because == "budget"
    assert 0.25 <= result.elapsed_s < 0.25 + 2.0  # one attempt may run past the deadline


def test_a_small_town_converges() -> None:
    g = lattice(5, 5)
    result = run(
        start(g, [at_node(g, 0), at_node(g, 24)], 0), Budget(seconds=600, max_iterations=10**6)
    )
    assert result.stopped_because == "converged"
    assert result.attempts > 400 and result.accepted > 0


def test_new_cycles_start_until_enough_in_a_row_accept_nothing() -> None:
    # a cycle ends after more than CONVERGED_FAILS failures in a row at the smallest radius;
    # here a splice comes after the rest of one cycle and two empty ones, so the run went on
    # past empty cycles
    g = lattice(12, 12)
    result = run(
        start(g, [at_node(g, 0), at_node(g, 143)], 2), Budget(seconds=600, max_iterations=10**6)
    )
    iterations = [0] + [e.iteration for e in result.events]
    gaps = [b - a for a, b in zip(iterations, iterations[1:], strict=False)]
    assert max(gaps) > 2 * (CONVERGED_FAILS + 1)
    # it stops after CONVERGED_EMPTY_CYCLES empty cycles in a row, which follow the rest of the
    # cycle the last splice was in
    assert result.stopped_because == "converged"
    assert result.attempts - iterations[-1] == (1 + CONVERGED_EMPTY_CYCLES) * (CONVERGED_FAILS + 1)


def test_the_generator_yields_events_and_returns_the_result() -> None:
    s = start(GRID, CORNERS, 1)
    loop = worsen(
        BACKEND,
        SPATIAL,
        GRID,
        s.route,
        s.objective,
        Budget(600, 60),
        s.rng,
        s.masks.active,
        r_max=s.r_max,
    )
    events: list[ProgressEvent] = []
    while True:
        try:
            events.append(next(loop))
        except StopIteration as stop:
            result = stop.value
            break
    assert result.events == tuple(events) and result.attempts == 60


def plan(
    seed: int,
    iterations: int = 300,
    data: GraphData = GRID,
    stops=None,
    backend: GraphBackend | None = None,
):
    request = RouteRequest(
        stops=stops or CORNERS, seed=seed, max_iterations=iterations, budget_s=120
    )
    return plan_route(
        data,
        request,
        backend=backend if backend is not None else BACKEND if data is GRID else None,
        spatial=SPATIAL if data is GRID else None,
        check_delta=True,
    )


@pytest.mark.parametrize("backend", [BACKEND, CsrBackend(GRID)], ids=["python", "csr"])
def test_the_same_seed_gives_the_same_route(backend: GraphBackend) -> None:
    one, two = plan(5, backend=backend), plan(5, backend=backend)
    assert one.route.nodes.tobytes() == two.route.nodes.tobytes()
    assert one.route.edges.tobytes() == two.route.edges.tobytes()
    assert one.run.attempts == two.run.attempts == 300


@pytest.mark.parametrize("seed", range(4))
def test_both_backends_plan_the_same_route(seed: int) -> None:
    # every search the loop makes returns the same bytes, so the whole run does: on the grid
    # and on a one-way town with twins
    g = random_town(seed)
    scc = np.flatnonzero(PythonBackend(g).largest_scc_mask())
    for data, stops in [(GRID, CORNERS), (g, [at_node(g, int(scc[0])), at_node(g, int(scc[-1]))])]:
        one, two = (
            plan(seed, data=data, stops=stops, backend=b(data)) for b in (PythonBackend, CsrBackend)
        )
        assert one.route.nodes.tobytes() == two.route.nodes.tobytes()
        assert one.route.edges.tobytes() == two.route.edges.tobytes()
        assert (one.run.attempts, one.run.accepted) == (two.run.attempts, two.run.accepted)
        assert one.run.accepted > 0
        assert [e.splice.edges.tobytes() for e in one.run.events] == [
            e.splice.edges.tobytes() for e in two.run.events
        ]


def test_different_seeds_give_different_routes() -> None:
    edge_sets = [set(plan(seed).route.edges.tolist()) for seed in (1, 2, 3)]
    for k, a in enumerate(edge_sets):
        for b in edge_sets[k + 1 :]:
            assert len(a & b) / len(a | b) < 0.7


def test_plan_route_validates_and_reports_progress() -> None:
    seen: list[tuple[int, float]] = []
    request = RouteRequest(stops=CORNERS, seed=3, max_iterations=120, budget_s=120)
    result = plan_route(
        GRID,
        request,
        backend=BACKEND,
        spatial=SPATIAL,
        on_progress=lambda e, b: seen.append((e.iteration, b.length_m)),
    )
    assert result.report.ok
    assert [it for it, _ in seen] == [e.iteration for e in result.run.events]
    assert {length for _, length in seen} == {result.baseline.length_m} == {3800.0}
    assert [s.node for s in result.snaps] == [0, 399]
    assert result.route is result.run.best


def test_pick_segment_never_spans_a_stop() -> None:
    route = route_on(GRID, list(range(20)) + [39, 38, 37], stop_idx=(0, 5, 6, 19, 22))
    rng = np.random.Generator(np.random.PCG64(0))
    sizes = Counter()
    for _ in range(3000):
        i, j = pick_segment(route, rng)
        assert 0 <= i < j <= len(route.edges)
        assert not any(i < s < j for s in route.stop_idx)
        sizes[j - i] += 1
    # 4 of the 22 edges start right before a stop, where a run is cut to one edge
    assert sizes[1] / 3000 == pytest.approx(0.8 + 0.2 * 4 / 22, abs=0.03)
    assert set(sizes) <= set(range(1, SEGMENT_RUN_MAX + 1)) and max(sizes) > 4


def test_pick_segment_starts_at_an_anchor_at_the_bias_rate() -> None:
    route = route_on(GRID, list(range(20)) + [39, 38, 37], stop_idx=(0, 5, 6, 19, 22))
    anchors = np.array([2, 7, 15], dtype=np.intp)
    rng = np.random.Generator(np.random.PCG64(1))
    starts = Counter()
    for _ in range(4000):
        i, j = pick_segment(route, rng, anchors)
        assert 0 <= i < j <= len(route.edges)
        assert not any(i < s < j for s in route.stop_idx)
        starts[i] += 1
    at_anchors = sum(starts[a] for a in anchors.tolist()) / 4000
    assert at_anchors == pytest.approx(0.3 + 0.7 * 3 / 22, abs=0.03)


def test_the_anchors_are_the_edges_with_the_most_unused_nodes_near() -> None:
    # a route along the bottom row of the 20 × 20 grid, the west half of the grid inactive:
    # the edges near the east end see the most unused nodes
    route = route_on(GRID, list(range(20)))
    mask = np.zeros(GRID.n, dtype=bool)
    mask[[r * 20 + c for r in range(20) for c in range(10, 20)]] = True
    unused = UnusedNodes(GRID, mask)
    anchors = unused.anchors(route, unused.table(route), radius=100.0)
    assert len(anchors) == 5 and (anchors >= 11).all()
    assert unused.table(route)[-1, -1] == mask.sum() - 10  # the route uses 10 active nodes


def test_the_unused_node_grid_stays_within_its_cap() -> None:
    # 100 m cells on a city; over 300 km × 300 km they would be 9 million, so they grow
    assert UnusedNodes(GRID, np.ones(GRID.n, dtype=bool)).cell_m == 100.0
    xy = [(X0, Y0), (X0 + 300_000.0, Y0 + 300_000.0)]
    wide = make_graph(xy, [(0, 1, 424_264.0), (1, 0, 424_264.0)])
    unused = UnusedNodes(wide, np.ones(wide.n, dtype=bool))
    assert unused.cell_m > 200.0
    assert unused.rows * unused.cols <= UNUSED_GRID_MAX_CELLS
    assert unused.table(route_on(wide, [0, 1]))[-1, -1] == 0  # both nodes on the route


def random_town(seed: int, *, turns: bool = False) -> GraphData:
    """A 7 × 7 street grid, a third of the streets one-way, and random twins; with `turns`,
    a transition table at a third of the junctions, each allowing a random part of its turns."""
    rng = np.random.default_rng(seed)
    xy = [(X0 + 100.0 * c, Y0 + 100.0 * r) for r in range(7) for c in range(7)]
    edges: list[Edge] = []
    streets = [(a, a + 1) for a in range(49) if a % 7 != 6] + [(a, a + 7) for a in range(42)]
    for a, b in streets:
        kind = rng.integers(3)
        if kind != 1:
            edges.append(Edge(a, b, 100.0))
        if kind != 2:
            edges.append(Edge(b, a, 100.0))
    g = make_graph(xy, edges)
    picks = rng.choice(g.m, size=(8, 2), replace=False)
    u, v = g.edge_u, g.edge_v
    twins = [((int(u[a]), int(v[a])), (int(u[b]), int(v[b]))) for a, b in picks]
    table: list[EdgePair] = []
    for node in rng.permutation(g.n)[: g.n // 3].tolist() if turns else []:
        ins = g.in_edges[g.in_indptr[node] : g.in_indptr[node + 1]].tolist()
        outs = g.out_edges[g.out_indptr[node] : g.out_indptr[node + 1]].tolist()
        pairs = [(a, b) for a in ins for b in outs]
        keep = rng.random(len(pairs)) < 0.6
        keep[:1] = True  # a table cannot be empty
        for (a, b), kept in zip(pairs, keep.tolist(), strict=True):
            if kept:
                table.append(((int(u[a]), node), (node, int(v[b]))))
    return make_graph(xy, edges, twins=twins, transitions=table)


@pytest.mark.parametrize("seed", range(10))
def test_random_towns_always_give_valid_routes(seed: int) -> None:
    g = random_town(seed)
    scc = np.flatnonzero(PythonBackend(g).largest_scc_mask())
    stops = [at_node(g, int(scc[0])), at_node(g, int(scc[-1]))]
    result = plan(seed, 200, data=g, stops=stops)  # plan_route raises if the route is invalid
    scores = [e.score for e in result.run.events]
    assert scores == sorted(scores)
    assert len(set(result.route.nodes.tolist())) == len(result.route.nodes)


def test_random_towns_with_turn_tables_always_give_valid_routes() -> None:
    planned = accepted = 0
    for seed in range(12):
        g = random_town(seed, turns=True)
        scc = np.flatnonzero(PythonBackend(g).largest_scc_mask())
        stops = [at_node(g, int(scc[0])), at_node(g, int(scc[-1]))]
        try:
            # raises if the route is invalid; the CSR kernels cannot search turn tables yet
            result = plan(seed, 200, data=g, stops=stops, backend=PythonBackend(g))
        except StopUnreachable:
            continue
        planned += 1
        accepted += result.run.accepted
        assert result.run.refused_transitions == 0
    assert planned >= 8 and accepted > 0


def test_a_tier_bans_a_lower_class_shortcut_between_its_own_nodes() -> None:
    # a 10 km primary road 0 - 1 - ... - 10 and a residential shortcut 4 ↔ 6, far from both stops:
    # both ends are primary nodes, so only the request's edge bans keep T1 off it
    xy = [(X0 + 1000.0 * k, Y0) for k in range(11)]
    edges = []
    for k in range(10):
        edges += [
            Edge(k, k + 1, highway=HighwayClass.PRIMARY),
            Edge(k + 1, k, highway=HighwayClass.PRIMARY),
        ]
    edges += [Edge(4, 6, 1500.0), Edge(6, 4, 1500.0)]
    g = make_graph(xy, edges)
    stops = [at_node(g, 0), at_node(g, 10)]
    shortcut = g.edge_highway == HighwayClass.RESIDENTIAL

    tiered = plan_route(
        g, RouteRequest(stops=stops, tier="T1", seed=0, max_iterations=100, budget_s=120)
    )
    assert tiered.masks.banned[shortcut].all()
    assert tiered.baseline.length_m == 10_000.0
    assert not shortcut[tiered.route.edges].any()

    everything = plan_route(
        g, RouteRequest(stops=stops, tier="T4", seed=0, max_iterations=100, budget_s=120)
    )
    assert everything.baseline.length_m == 9500.0


def test_a_point_to_point_request_never_becomes_a_round_trip() -> None:
    # A ↔ X two-way and Y → B one-way: start A, end near B; nothing that can end a trip lies
    # near B but A itself, and A is the start
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 300, Y0), (X0 + 6300, Y0)]  # B, Y, A, X
    g = make_graph(xy, [(1, 0, 100.0), (2, 3, 6000.0), (3, 2, 6000.0)])
    request = RouteRequest(stops=[at_node(g, 2), at_node(g, 0)], leash_km=10, seed=0)
    with pytest.raises(StopUnreachable) as caught:
        plan_route(g, request)
    assert caught.value.leg == (0, 1) and caught.value.stop_index == 1


def test_identical_start_and_end_points_plan_a_loop() -> None:
    g = lattice(12, 12)
    here = at_node(g, 66)
    result = plan_route(g, RouteRequest(stops=[here, here], seed=2, max_iterations=80, budget_s=60))
    assert result.route.is_round_trip and result.route.nodes[0] == 66
    assert result.report.ok and result.baseline.length_m == 0.0


@pytest.mark.parametrize("error", [ValueError("boom"), IllegalSplice(3, "boom")])
def test_a_seed_refused_unexpectedly_is_one_stop_unreachable(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    from worst_route.engine import plan as plan_module

    def refuse(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(plan_module, "seed_route", refuse)
    with pytest.raises(StopUnreachable) as caught:
        plan_route(GRID, RouteRequest(stops=CORNERS, seed=0, max_iterations=5))
    assert caught.value.leg == (0, 1)
    assert "the seed route was refused: boom" in str(caught.value)
