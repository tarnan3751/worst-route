"""The turn constraints: soft ones penalise the turns they are about and the searches avoid
them where they can; strict ones refuse them, and the searches, the seed and the loop never
take one. Their hooks, the composite the engine runs, and `delta` against a full re-score."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
import pytest
from pydantic import ValidationError

from worst_route.config import TURN_PENALTY_M, TURN_PENALTY_S
from worst_route.engine.constraints import (
    NO_CONSTRAINTS,
    Constraints,
    NoLeft,
    NoRight,
    constrain,
    turn_penalty_for,
)
from worst_route.engine.detour import find_detour
from worst_route.engine.objectives import make_objective
from worst_route.engine.objectives.base import BaseObjective, BoolArray
from worst_route.engine.objectives.composite import Composite
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.route import Metrics, Route, check_route, splice_arrays
from worst_route.engine.search import shortest_path, simple_paths
from worst_route.engine.seed_route import SeedRoute, baseline_route, resolve_stops, seed_route
from worst_route.engine.snap import InfeasibleTurnConstraint, StopUnreachable
from worst_route.engine.target import Target
from worst_route.engine.turns import count_turns, turn_classes
from worst_route.engine.validator import validate_route
from worst_route.engine.worsen import Budget, WorsenResult, run_worsen
from worst_route.graph.backend import TURN_LEFT, TURN_RIGHT, TURN_U
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import Snap, SpatialIndex
from worst_route.graph.testing import edge_id, make_graph

from .graphs import X0, Y0, at_node, edges_of, lattice, route_on, two_rings

GRID = lattice(4, 4)  # node r * 4 + c, row 0 the southmost
SOFT_LEFT, SOFT_RIGHT = Constraints(no_left=True), Constraints(no_right=True)
STRICT_LEFT = Constraints(no_left=True, strict=True)
STRICT_RIGHT = Constraints(no_right=True, strict=True)
STRICT = {"no_left": STRICT_LEFT, "no_right": STRICT_RIGHT}
LEFT, RIGHT = (edge_id(GRID, 0, 1), edge_id(GRID, 1, 5)), (edge_id(GRID, 5, 1), edge_id(GRID, 1, 0))


def lefts(g: GraphData, edges: np.ndarray) -> int:
    return count_turns(g, edges)["left"]


def path(nodes: list[int], g: GraphData = GRID) -> tuple[np.ndarray, np.ndarray]:
    return np.array(nodes, dtype=np.int32), edges_of(g, nodes)


def test_the_request_s_constraints() -> None:
    assert Constraints() == NO_CONSTRAINTS and NO_CONSTRAINTS.kinds == []
    assert NO_CONSTRAINTS.banned_turn_classes == 0 and NO_CONSTRAINTS.note == ""
    assert SOFT_LEFT.kinds == STRICT_LEFT.kinds == [NoLeft]
    assert SOFT_RIGHT.kinds == STRICT_RIGHT.kinds == [NoRight]
    # only a strict constraint refuses turns outright, so only it constrains the seed
    assert SOFT_LEFT.banned_turn_classes == 0 and SOFT_LEFT.note == ""
    assert STRICT_LEFT.banned_turn_classes == TURN_LEFT | TURN_U
    assert STRICT_RIGHT.banned_turn_classes == TURN_RIGHT | TURN_U
    assert STRICT_LEFT.note == "under the strict no-left constraint"
    assert STRICT_RIGHT.note == "under the strict no-right constraint"
    for constraints in (SOFT_LEFT, STRICT_RIGHT):
        assert Constraints.model_validate(constraints.model_dump(mode="json")) == constraints
    for both in ({"no_left": True, "no_right": True}, {"no_left": 1, "no_right": 1, "strict": 1}):
        with pytest.raises(ValidationError, match="straight only is not a route"):
            Constraints.model_validate(both)
    with pytest.raises(ValidationError, match="strict needs no_left or no_right"):
        Constraints.model_validate({"strict": True})
    with pytest.raises(ValidationError):
        Constraints.model_validate({"no_u_turns": True})


def test_a_strict_constraint_refuses_its_turns_and_u_turns() -> None:
    east, on, west = edge_id(GRID, 0, 1), edge_id(GRID, 1, 2), edge_id(GRID, 1, 0)
    no_left, no_right = (
        NoLeft(GRID, strict=True, penalty=0.0),
        NoRight(GRID, strict=True, penalty=0.0),
    )
    assert not no_left.feasible_pair(GRID, *LEFT) and no_right.feasible_pair(GRID, *LEFT)
    assert no_left.feasible_pair(GRID, *RIGHT) and not no_right.feasible_pair(GRID, *RIGHT)
    for constraint in (no_left, no_right):
        assert constraint.feasible_pair(GRID, east, on)  # straight on
        assert not constraint.feasible_pair(GRID, east, west)  # back the way it came
        assert not constraint.has_pair and constraint.avoided_turn_classes == 0
        assert constraint.evaluate(*path([0, 1, 5, 4]))[0] == 0.0  # no terms
    assert no_left.banned_turn_classes == TURN_LEFT | TURN_U


def test_a_soft_constraint_scores_its_turns_and_refuses_none() -> None:
    no_left = NoLeft(GRID, strict=False, penalty=TURN_PENALTY_M)
    assert no_left.has_pair and no_left.banned_turn_classes == 0
    assert no_left.avoided_turn_classes == TURN_LEFT | TURN_U
    assert (
        no_left.pair_term(GRID, *LEFT) == -TURN_PENALTY_M and no_left.pair_term(GRID, *RIGHT) == 0
    )
    assert no_left.feasible_pair(GRID, *LEFT)
    # 0 → 1 → 5 → 4 → 8: east, left north, left west, right north
    assert no_left.evaluate(*path([0, 1, 5, 4, 8]))[0] == -2 * TURN_PENALTY_M
    route = route_on(GRID, [4, 5, 6, 7])
    assert no_left.feasible_window(GRID, route, 1, 2, *path([5, 9, 10, 6]))


def test_a_strict_window_is_checked_at_its_two_boundaries() -> None:
    # 4 → 5 → 6 → 7 along row 1, its window 5..6 replaced by 5 → 9 → 10 → 6: a left into the
    # detour (east, then north), then right, right, and a left out of it (south, then east)
    route = route_on(GRID, [4, 5, 6, 7])
    up_and_over = path([5, 9, 10, 6])
    no_left, no_right = (
        NoLeft(GRID, strict=True, penalty=0.0),
        NoRight(GRID, strict=True, penalty=0.0),
    )
    assert not no_left.feasible_window(GRID, route, 1, 2, *up_and_over)
    assert not no_right.feasible_window(GRID, route, 1, 2, *up_and_over)
    # the same detour from the route's start: no boundary before it, only the turn out of it
    assert not no_left.feasible_window(GRID, route_on(GRID, [5, 6, 7]), 0, 1, *up_and_over)
    # and to the route's end: only the turn into it
    assert not no_left.feasible_window(GRID, route_on(GRID, [4, 5, 6]), 1, 2, *up_and_over)
    assert no_right.feasible_window(GRID, route_on(GRID, [5, 9]), 0, 1, *path([5, 9]))


def test_a_turn_costs_a_kilometre_s_worth_in_the_objective_s_units() -> None:
    def objective(key: str) -> BaseObjective:
        made = make_objective(key, GRID, baseline=Metrics(300.0, 27.0), r_max=1e4)
        assert isinstance(made, BaseObjective)
        return made

    assert turn_penalty_for(objective("distance")) == TURN_PENALTY_M == 1000.0
    assert turn_penalty_for(objective("time")) == TURN_PENALTY_S == 90.0
    for key, penalty in (("distance", TURN_PENALTY_M), ("time", TURN_PENALTY_S)):
        soft = constrain(objective(key), SOFT_LEFT, GRID)
        assert isinstance(soft, Composite)
        assert [part.pair_term(GRID, *LEFT) for part in soft.constraints] == [-penalty]


class Banning(BaseObjective):
    """Bans one edge in every window, and a turn class of its own."""

    key = "banning"
    banned_turn_classes = TURN_RIGHT

    def banned_edges(self, G: GraphData, route: Route, i: int, j: int) -> BoolArray | None:  # noqa: N803
        bans = np.zeros(G.m, dtype=np.bool_)
        bans[3] = True
        return bans


def test_the_composite_sums_the_scores_and_combines_every_hook() -> None:
    objective = make_objective("time", GRID, baseline=Metrics(300.0, 27.0), r_max=1e4)
    assert constrain(objective, NO_CONSTRAINTS, GRID) is objective
    soft = constrain(objective, SOFT_LEFT, GRID)
    assert isinstance(soft, Composite) and soft.key == "time" and soft.has_pair
    assert (soft.banned_turn_classes, soft.avoided_turn_classes) == (0, TURN_LEFT | TURN_U)
    route = route_on(GRID, [0, 1, 2, 6, 10])  # east, east, left north, north
    score, metrics = objective.evaluate(route.nodes, route.edges)
    # under the slowest time a turn costs TURN_PENALTY_S seconds of its score
    assert soft.evaluate(route.nodes, route.edges) == (score - TURN_PENALTY_S, metrics)
    # 1 → 5 → 6 → 10 in place of 1 → 2 → 6 → 10: two lefts (into it, then east → north at 6
    # becomes north → east, a right, and 5 → 6 → 10 east then north, a left) for one
    detour = path([1, 5, 6])
    gain = soft.delta(route, 1, 3, *detour)
    after = soft.evaluate(*splice_arrays(route, 1, 3, *detour))[0]
    assert gain == pytest.approx(after - soft.score(route), abs=1e-6)
    assert gain == pytest.approx(objective.delta(route, 1, 3, *detour) - TURN_PENALTY_S)
    assert soft.feasible_window(GRID, route, 1, 3, *detour)  # soft: taken or not by its score
    assert soft.search_weights(GRID) is objective.search_weights(GRID)
    assert soft.waypoint_pool(GRID, route) is objective.waypoint_pool(GRID, route)
    assert soft.initial_radius(route) == objective.initial_radius(route)
    assert soft.primary(metrics) == objective.primary(metrics) == metrics.time_s + metrics.delay_s
    assert soft.constraint_delta(route, 1, 3, *detour) == -TURN_PENALTY_S
    assert objective.constraint_delta(route, 1, 3, *detour) == 0.0
    assert soft.banned_edges(GRID, route, 0, 1) is None

    strict = constrain(objective, STRICT_LEFT, GRID)
    assert (strict.banned_turn_classes, strict.avoided_turn_classes) == (TURN_LEFT | TURN_U, 0)
    assert strict.evaluate(route.nodes, route.edges) == (score, metrics)
    assert not strict.feasible_window(GRID, route, 1, 3, *detour)

    both = Composite(
        Banning(GRID, baseline=Metrics(0.0, 0.0), r_max=1.0),
        [NoLeft(GRID, strict=True, penalty=0.0)],
    )
    assert both.banned_turn_classes == TURN_LEFT | TURN_RIGHT | TURN_U
    bans = both.banned_edges(GRID, route, 0, 1)
    assert bans is not None and np.flatnonzero(bans).tolist() == [3]
    assert not both.feasible_pair(GRID, *LEFT) and both.feasible_pair(GRID, *RIGHT)


@pytest.mark.parametrize("seed", range(6))
def test_a_soft_composite_s_delta_equals_a_full_rescore(seed: int) -> None:
    g = lattice(6, 6, primary_every=3)
    rng = np.random.Generator(np.random.PCG64(seed))
    backend, spatial = PythonBackend(g), SpatialIndex(g)
    snake = [0, 1, 2, 3, 4, 5, 11, 10, 9, 8, 7, 6, 12, 13, 14, 15, 16, 17]
    if seed % 2:
        snake = [0, 1, 2, 3, 9, 8, 7, 6, 0]  # a round trip
    key = "time" if seed < 3 else "distance"
    primary = make_objective(key, g, baseline=Metrics(500.0, 45.0), r_max=1e4)
    objective = constrain(primary, SOFT_RIGHT if seed % 3 else SOFT_LEFT, g)
    route = route_on(g, snake)
    score, metrics = objective.evaluate(route.nodes, route.edges)
    route = Route.new(
        g, route.nodes, route.edges, route.stop_idx, score=score, metrics=metrics, seed=0
    )
    checked = 0
    for _ in range(150):
        i = int(rng.integers(len(route.edges)))
        j = min(i + int(rng.integers(1, 4)), len(route.edges))
        forbid, banned = route.release(g, i, j)
        found = find_detour(
            backend, spatial, g, route, i, j,
            forbid=forbid, banned=banned, node_mask=np.ones(g.n, dtype=bool),
            radius=300.0, objective=objective, rng=rng,
            entry_edge=int(route.edges[i - 1]) if i > 0 else -1,
            exit_edge=int(route.edges[j]) if j < len(route.edges) else -1,
        )  # fmt: skip
        if found is None:
            continue
        delta = objective.delta(route, i, j, *found)
        score, metrics = objective.evaluate(*splice_arrays(route, i, j, *found))
        assert math.isclose(delta, score - route.score, rel_tol=1e-9, abs_tol=1e-6)
        route = route.splice(g, i, j, *found, score=score, metrics=metrics)
        checked += 1
    assert checked >= 5


def test_a_search_avoids_a_class_when_it_can_and_takes_it_when_it_must() -> None:
    g = lattice(3, 3)
    b, w = PythonBackend(g), g.edge_length_m.astype(np.float64)
    soft = {"avoided_turn_classes": TURN_LEFT | TURN_U}
    found = shortest_path(b, 0, 4, w)
    assert found is not None and found[0].tolist() == [0, 1, 4]  # east, then north: a left
    found = shortest_path(b, 0, 4, w, **soft)
    assert found is not None and found[0].tolist() == [0, 3, 4]  # north, then east: a right
    # continuing 1 → 0 (west), every way out of the corner turns right or back: avoiding
    # rights finds nothing, so the search takes one; refusing them, there is no path
    entry, right = edge_id(g, 1, 0), TURN_RIGHT | TURN_U
    taken = shortest_path(b, 0, 3, w, entry_edge=entry, avoided_turn_classes=right)
    assert taken is not None and taken[0].tolist() == [0, 3]
    assert shortest_path(b, 0, 3, w, entry_edge=entry, banned_turn_classes=right) is None
    paths = simple_paths(b, 0, 8, max_depth=8, max_paths=1000, **soft)
    assert paths and all(lefts(g, p[1]) == 0 for p in paths)
    assert len(paths) < len(simple_paths(b, 0, 8, max_depth=8, max_paths=1000))
    taken_paths = simple_paths(
        b, 0, 3, entry_edge=entry, max_depth=2, max_paths=9, avoided_turn_classes=right
    )
    assert [p[0].tolist() for p in taken_paths] == [[0, 3]]


def test_strict_searches_never_turn_a_banned_way_on_a_build_without_rows() -> None:
    g = lattice(3, 3)
    b, w = PythonBackend(g), g.edge_length_m.astype(np.float64)
    no_left = shortest_path(b, 0, 4, w, banned_turn_classes=STRICT_LEFT.banned_turn_classes)
    assert no_left is not None and no_left[0].tolist() == [0, 3, 4]
    paths = simple_paths(
        b, 0, 8, max_depth=8, max_paths=1000, banned_turn_classes=STRICT_LEFT.banned_turn_classes
    )
    assert paths and all(lefts(g, p[1]) == 0 for p in paths)


def snaps_on(g: GraphData, nodes: Sequence[int]) -> list[Snap]:
    return [
        Snap(node=v, moved_m=0.0, lat=float(g.node_lat[v]), lon=float(g.node_lon[v]), note=None)
        for v in nodes
    ]


def seeded(
    g: GraphData, stops: Sequence[int], constraints: Constraints, seed: int = 0
) -> SeedRoute:
    rng = np.random.Generator(np.random.PCG64(seed))
    return seed_route(
        PythonBackend(g),
        SpatialIndex(g),
        g,
        snaps_on(g, stops),
        np.ones(g.n, dtype=bool),
        np.zeros(g.m, dtype=bool),
        rng,
        r_max=10_000.0,
        constraints=constraints,
    )


@pytest.mark.parametrize("name", ["no_left", "no_right"])
def test_a_strict_seed_s_legs_and_vias_keep_the_constraint(name: str) -> None:
    # node r * 9 + c: from (2, 2) up to the via at (4, 4), then back down to (2, 6)
    g, constraints = lattice(9, 9), STRICT[name]
    found = seeded(g, [20, 40, 24], constraints)
    check_route(g, found.nodes, found.edges, found.stop_idx)
    classes = turn_classes(g, found.edges[:-1], found.edges[1:])
    assert not (classes & constraints.banned_turn_classes).any()  # at the via too
    plain = seeded(g, [20, 40, 24], NO_CONSTRAINTS)
    assert (
        turn_classes(g, plain.edges[:-1], plain.edges[1:]) & constraints.banned_turn_classes
    ).any()
    soft = seeded(g, [20, 40, 24], Constraints(**{name: True}))
    assert soft.edges.tobytes() == plain.edges.tobytes()  # a soft constraint leaves the seed


def test_a_leg_only_a_left_turn_reaches_is_unreachable_under_strict_no_left() -> None:
    # one way from 0 east to 1, then north to 2: a left turn at 1
    g = make_graph([(X0, Y0), (X0 + 100, Y0), (X0 + 100, Y0 + 100)], [(0, 1, 100.0), (1, 2, 100.0)])
    assert seeded(g, [0, 2], STRICT_RIGHT).nodes.tolist() == [0, 1, 2]
    assert seeded(g, [0, 2], SOFT_LEFT).nodes.tolist() == [0, 1, 2]
    with pytest.raises(InfeasibleTurnConstraint) as caught:
        seeded(g, [0, 2], STRICT_LEFT)
    assert str(caught.value) == (
        "No route from stop 1 to stop 2 without repeating a road and with no left turns."
    )
    assert (caught.value.leg, caught.value.stop_index, caught.value.constraint) == (
        (0, 1),
        1,
        "no_left",
    )


def test_a_leg_no_route_covers_even_without_the_constraint_is_plainly_unreachable() -> None:
    # the strict seed fails, and so does the plain one: the constraint is not the reason
    g = two_rings()
    with pytest.raises(StopUnreachable) as refused:
        seeded(g, [0, 12], STRICT_LEFT)
    assert not isinstance(refused.value, InfeasibleTurnConstraint)
    assert str(refused.value) == "No route from stop 1 to stop 2 without repeating a road."


def test_a_strict_round_trip_loop_keeps_the_constraint() -> None:
    g = lattice(8, 8)
    found = seeded(g, [27, 27], STRICT_LEFT, seed=3)
    assert found.nodes[0] == found.nodes[-1] == 27
    assert lefts(g, found.edges) == 0 and count_turns(g, found.edges)["u_turn"] == 0


@dataclass
class Run:
    g: GraphData
    result: WorsenResult
    start: Route


def worsened(
    g: GraphData,
    stops: tuple[int, int],
    constraints: Constraints,
    seed: int,
    backend_type: type[PythonBackend] | type[CsrBackend] = PythonBackend,
    attempts: int = 1500,
) -> Run:
    backend, spatial = backend_type(g), SpatialIndex(g)
    request = RouteRequest(stops=[at_node(g, v) for v in stops], seed=seed)
    masks = request_masks(spatial, g, request)
    rng = np.random.Generator(np.random.PCG64(seed))
    r_max = min(masks.leash_m, masks.extent_m)
    snaps = resolve_stops(g, spatial, backend, request, masks)
    found = seed_route(
        backend, spatial, g, snaps, masks.active, masks.banned, rng, r_max=r_max,
        constraints=constraints,
    )  # fmt: skip
    baseline = baseline_route(backend, g, [s.node for s in snaps], masks.active, masks.banned)
    distance = Distance(g, baseline=baseline.metrics, r_max=r_max)
    objective = constrain(distance, constraints, g)
    score, metrics = objective.evaluate(found.nodes, found.edges)
    start = Route.new(
        g, found.nodes, found.edges, found.stop_idx, score=score, metrics=metrics, seed=seed
    )
    result = run_worsen(
        backend, spatial, g, start, objective, Budget(seconds=120.0, max_iterations=attempts),
        rng, masks.active, r_max=r_max, banned_edges=masks.banned, check_delta=True,
    )  # fmt: skip
    report = validate_route(
        g,
        result.best,
        stop_nodes=[s.node for s in snaps],
        node_mask=masks.active,
        banned_edges=masks.banned,
    )
    assert report.ok, report.lines()
    return Run(g, result, start)


def replayed(run: Run) -> list[Route]:
    """The route the loop started from, then after each splice."""
    routes = [run.start]
    for event in run.result.events:
        s = event.splice
        routes.append(
            routes[-1].splice(
                run.g, s.i, s.j, s.nodes, s.edges, score=event.score, metrics=event.metrics
            )
        )
    assert routes[-1].edges.tobytes() == run.result.best.edges.tobytes()
    return routes


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("name", ["no_left", "no_right"])
def test_the_strict_loop_never_turns_the_way_the_constraint_refuses(name: str, seed: int) -> None:
    # between two inner nodes: a route along the lattice's rim could not be detoured at all
    # (on a planar grid a detour that never turns left needs right turns at both ends of its
    # window, or two inside it, and a corner-to-corner route has one)
    constraints = STRICT[name]
    run = worsened(lattice(10, 10), (22, 77), constraints, seed)
    assert run.result.accepted > 0
    for route in replayed(run):  # every route along the way, not only the last
        classes = turn_classes(run.g, route.edges[:-1], route.edges[1:])
        assert not (classes & constraints.banned_turn_classes).any()
    turns = count_turns(run.g, run.result.best.edges)
    assert turns["u_turn"] == 0 and turns["left" if name == "no_left" else "right"] == 0
    assert turns["right" if name == "no_left" else "left"] > 0


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("name", ["no_left", "no_right"])
def test_the_soft_loop_adds_an_avoided_turn_only_for_more_than_a_kilometre(
    name: str, seed: int
) -> None:
    # the route grows, an avoided turn costs TURN_PENALTY_M metres of it, and the turns it avoids
    # are far rarer than in the plain loop's route, which turns both ways about as often
    g = lattice(10, 10)
    run = worsened(g, (22, 77), Constraints(**{name: True}), seed)
    plain = worsened(g, (22, 77), NO_CONSTRAINTS, seed)
    kind = "left" if name == "no_left" else "right"

    def avoided(route: Route) -> int:
        turns = count_turns(g, route.edges)
        return turns[kind] + turns["u_turn"]

    assert run.result.accepted > 0
    for before, after in pairwise(replayed(run)):
        grew = after.metrics.length_m - before.metrics.length_m
        assert grew > TURN_PENALTY_M * (avoided(after) - avoided(before))
    best = run.result.best
    assert best.metrics.length_m > 3 * run.start.metrics.length_m
    rate = avoided(best) / best.metrics.length_m
    assert rate < avoided(plain.result.best) / plain.result.best.metrics.length_m / 2


@pytest.mark.parametrize("seed", range(3))
def test_a_soft_base_shrunk_toward_a_target_never_adds_an_avoided_turn(seed: int) -> None:
    # shrinking takes a splice only if it moves the length toward the target and adds no net
    # left turn or U-turn: the penalty the constraint gives back is not a loss of length
    g = lattice(10, 10)
    request = RouteRequest(
        stops=[at_node(g, 22), at_node(g, 77)],
        seed=seed,
        max_iterations=3000,
        constraints=SOFT_LEFT,
    )
    worst = plan_route(g, request, backend=PythonBackend(g))
    ratio = worst.route.metrics.length_m / worst.baseline.length_m

    def avoided(route: Route) -> int:
        turns = count_turns(g, route.edges)
        return turns["left"] + turns["u_turn"]

    for share in (0.6, 0.3):
        target = Target(kind="ratio", value=round(ratio * share, 2))
        shrunk = plan_route(
            g,
            request.model_copy(update={"target": target, "seed": seed + 10}),
            backend=PythonBackend(g),
            base=worst.route,
            check_delta=True,
        )
        assert shrunk.run.accepted > 0 and shrunk.report.ok
        route, seen = worst.route, [avoided(worst.route)]
        for event in shrunk.run.events:
            s = event.splice
            route = route.splice(
                g, s.i, s.j, s.nodes, s.edges, score=event.score, metrics=event.metrics
            )
            assert route.metrics.length_m < worst.route.metrics.length_m
            seen.append(avoided(route))
        assert all(b <= a for a, b in pairwise(seen)), seen


def test_the_loop_under_a_constraint_is_the_same_on_both_backends() -> None:
    for g, stops, constraints in (
        (lattice(10, 10), (22, 77), STRICT_LEFT),
        (lattice(10, 10), (22, 77), SOFT_LEFT),
    ):
        one, two = (worsened(g, stops, constraints, 5, t) for t in (PythonBackend, CsrBackend))
        assert one.result.best.edges.tobytes() == two.result.best.edges.tobytes()
        assert one.result.attempts == two.result.attempts
        assert one.result.accepted == two.result.accepted


def test_one_way_towns_with_turn_tables_keep_a_strict_constraint() -> None:
    from .test_worsen import random_town

    planned = 0
    for seed in range(8):
        g = random_town(seed, turns=True)
        scc = np.flatnonzero(PythonBackend(g).largest_scc_mask())
        try:
            run = worsened(g, (int(scc[0]), int(scc[-1])), STRICT_RIGHT, seed, attempts=300)
        except StopUnreachable:
            continue
        planned += 1
        assert count_turns(g, run.result.best.edges)["right"] == 0
        assert run.result.refused_transitions == 0
    assert planned > 0  # most of these towns' legs cannot be built without a right turn
