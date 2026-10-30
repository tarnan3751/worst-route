"""Targets and the goal a run pursues: the target's bounds, the goal's rules on values, and the
loop toward a target on lattices."""

import math

import numpy as np
import pytest
from pydantic import ValidationError

from worst_route.config import CONVERGED_FAILS, TARGET_MAX_DURATION_S, TARGET_TOLERANCE
from worst_route.engine.objectives import make_objective
from worst_route.engine.plan import PlanResult, plan_route
from worst_route.engine.request import RouteRequest
from worst_route.engine.route import Metrics, Route, splice_arrays
from worst_route.engine.target import Goal, Target
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.testing import Edge, make_graph

from .graphs import X0, Y0, at_node, edges_of, lattice

GRID = lattice(4, 4)


def goal(kind: str, value: float | None = None, baseline_length_m: float = 1000.0) -> Goal:
    return Goal(Target(kind=kind, value=value), GRID, baseline_length_m=baseline_length_m)


def test_a_target_is_max_by_default_and_round_trips() -> None:
    assert Target() == Target(kind="max", value=None)
    for target in (Target(), Target(kind="ratio", value=50), Target(kind="duration", value=9e3)):
        assert Target.model_validate(target.model_dump(mode="json")) == target


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "max", "value": 2},
        {"kind": "ratio"},
        {"kind": "ratio", "value": 1},
        {"kind": "ratio", "value": 0.5},
        {"kind": "ratio", "value": math.inf},
        {"kind": "ratio", "value": math.nan},
        {"kind": "duration"},
        {"kind": "duration", "value": 0},
        {"kind": "duration", "value": TARGET_MAX_DURATION_S + 1},
        {"kind": "slowly", "value": 2},
        {"kind": "ratio", "value": 2, "seconds": 3},
    ],
)
def test_a_target_out_of_bounds_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Target.model_validate(fields)


def test_the_bounds_themselves_are_allowed() -> None:
    assert Target(kind="ratio", value=1.0001).value == 1.0001
    assert Target(kind="duration", value=TARGET_MAX_DURATION_S).value == TARGET_MAX_DURATION_S


def test_the_value_is_the_score_the_ratio_or_the_time() -> None:
    metrics = Metrics(length_m=5000.0, time_s=450.0)
    assert goal("max").value(7.5, metrics) == 7.5
    assert goal("ratio", 20).value(7.5, metrics) == 5.0
    assert goal("duration", 600).value(7.5, metrics) == 450.0


def test_max_grows_forever_and_takes_any_gain() -> None:
    g = goal("max")
    assert g.phase(1e12) == "grow" and not g.reached(1e12)
    assert g.better(2.0, 1.0) and not g.better(1.0, 1.0) and not g.better(1.0, 2.0)
    assert g.accepts(10.0, 1e-9, math.nan) and not g.accepts(10.0, 0.0, 11.0)
    assert not g.accepts(10.0, -1.0, 9.0)
    assert not g.overshoots(10.0, 5.0, 1e12)
    assert g.describe(3.0) is None


def test_the_band_and_the_phases() -> None:
    g = goal("ratio", 10)
    low, high = 10 * (1 - TARGET_TOLERANCE), 10 * (1 + TARGET_TOLERANCE)
    assert (g.low, g.high) == (low, high)
    assert g.phase(math.nextafter(low, 0)) == "grow" and not g.reached(math.nextafter(low, 0))
    assert g.phase(low) == g.phase(10) == g.phase(high) == "done"
    assert g.reached(low) and g.reached(high)
    assert g.phase(math.nextafter(high, math.inf)) == "shrink"


def test_closer_is_better_and_a_tie_goes_to_the_later_route() -> None:
    g = goal("duration", 3600)
    assert g.better(3500, 3000) and g.better(3700, 3000) and not g.better(4000, 3700)
    assert g.better(3500, 3700) and g.better(3700, 3500)  # equally far: the later one, a


def test_growing_takes_a_gain_up_to_the_band_s_top_and_no_further() -> None:
    g = goal("ratio", 10)
    assert g.accepts(5.0, 100.0, 8.0) and g.accepts(5.0, 100.0, g.high)
    assert not g.accepts(5.0, 100.0, math.nextafter(g.high, math.inf))
    assert g.overshoots(5.0, 100.0, math.nextafter(g.high, math.inf))
    assert not g.overshoots(5.0, 100.0, g.high)
    assert not g.accepts(5.0, -100.0, 4.0) and not g.overshoots(5.0, -100.0, 20.0)


def test_shrinking_takes_a_loss_down_to_the_band_s_bottom_and_no_further() -> None:
    g = goal("ratio", 10)
    assert g.accepts(15.0, -100.0, 12.0) and g.accepts(15.0, -100.0, g.low)
    assert not g.accepts(15.0, -100.0, math.nextafter(g.low, 0))
    assert not g.accepts(15.0, 100.0, 16.0)
    assert not g.overshoots(15.0, 100.0, 30.0) and not g.overshoots(15.0, -100.0, 1.0)


def test_inside_the_band_nothing_is_taken() -> None:
    g = goal("ratio", 10)
    assert not g.accepts(10.0, 1.0, 10.01) and not g.accepts(10.0, -1.0, 9.99)
    assert not g.overshoots(10.0, 100.0, 30.0)


def test_a_duration_moves_only_one_way_whatever_the_length_does() -> None:
    # a longer detour (gain > 0) that is faster (its time falls) is not taken while growing
    g = goal("duration", 3600)
    assert not g.accepts(1000.0, 50.0, 990.0) and g.accepts(1000.0, 50.0, 1010.0)
    # and a shorter one (gain < 0) that is slower is not taken while shrinking
    assert not g.accepts(5000.0, -50.0, 5010.0) and g.accepts(5000.0, -50.0, 4990.0)


def test_describe_reports_the_target_and_what_was_achieved() -> None:
    g = goal("duration", 32400)
    assert g.describe(32000.0) == {
        "kind": "duration",
        "value": 32400.0,
        "achieved": 32000.0,
        "within_tolerance": True,
    }
    assert g.describe(30000.0)["within_tolerance"] is False  # type: ignore[index]


def town() -> GraphData:
    """A 3 × 3 lattice of 100 m streets: its top row (nodes 6, 7, 8) at 90 km/h, the rest 30."""
    xy = [(X0 + c * 100, Y0 + r * 100) for r in range(3) for c in range(3)]
    edges: list[Edge] = []
    for r in range(3):
        for c in range(2):
            a, kmh = r * 3 + c, 90.0 if r == 2 else 30.0
            edges += [Edge(a, a + 1, 100.0, speed_kmh=kmh), Edge(a + 1, a, 100.0, speed_kmh=kmh)]
    for c in range(3):
        for r in range(2):
            a = r * 3 + c
            edges += [Edge(a, a + 3, 100.0, speed_kmh=30.0), Edge(a + 3, a, 100.0, speed_kmh=30.0)]
    return make_graph(xy, edges)


@pytest.mark.parametrize(("kind", "value"), [("max", None), ("ratio", 3.0), ("duration", 60.0)])
@pytest.mark.parametrize("detour", [[3, 6, 7, 8], [3, 6, 7, 4, 5, 8]])
def test_a_candidate_s_value_is_the_value_after_the_splice(
    kind: str, value: float | None, detour: list[int]
) -> None:
    # 0 -> 3 -> 4 -> 5 -> 8, its window 3..8 (i = 1, j = 4) replaced by a detour as long and
    # faster (along the top row), or longer
    g = town()
    objective = make_objective("distance", g, baseline_length_m=400.0, r_max=1000.0)
    nodes = np.array([0, 3, 4, 5, 8], dtype=np.int32)
    score, metrics = objective.evaluate(nodes, edges_of(g, nodes.tolist()))
    route = Route.new(
        g, nodes, edges_of(g, nodes.tolist()), (0, 4), score=score, metrics=metrics, seed=0
    )
    path, edges = np.array(detour, dtype=np.int32), edges_of(g, detour)
    gain = objective.delta(route, 1, 4, path, edges)
    target = Goal(Target(kind=kind, value=value), g, baseline_length_m=400.0)
    after = target.value(*objective.evaluate(*splice_arrays(route, 1, 4, path, edges)))
    assert target.candidate(route, 1, 4, edges, gain) == pytest.approx(after, rel=1e-12)
    if len(detour) == 4:  # as long, and faster: only the duration's value moves (down)
        before = target.value(score, metrics)
        assert gain == 0 and (after < before if kind == "duration" else after == before)


# The loop toward a target ------------------------------------------------------------------

LOOP_GRID = lattice(20, 20, rounded_lonlat=True)
TOWN = lattice(5, 5, rounded_lonlat=True)


def plan(
    g: GraphData,
    stops: tuple[int, int],
    target: Target,
    seed: int,
    *,
    base: Route | None = None,
    attempts: int = 10**6,
    backend_type: type[PythonBackend] | type[CsrBackend] = PythonBackend,
) -> PlanResult:
    request = RouteRequest(
        stops=[at_node(g, v) for v in stops],
        seed=seed,
        max_iterations=attempts,
        budget_s=120,
        target=target,
    )
    return plan_route(g, request, backend=backend_type(g), base=base, check_delta=True)


def values(result: PlanResult, start: Route) -> list[float]:
    """The goal's value of the route the loop started from, then after each splice."""
    goal = result.goal
    return [goal.value(start.score, start.metrics)] + [
        goal.value(e.score, e.metrics) for e in result.run.events
    ]


def replay(start: Route, result: PlanResult) -> Route:
    route = start
    for e in result.run.events:
        s = e.splice
        route = route.splice(
            LOOP_GRID, s.i, s.j, s.nodes, s.edges, score=e.score, metrics=e.metrics
        )
    return route


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize(
    "target", [Target(kind="ratio", value=5), Target(kind="duration", value=1500)], ids=str
)
def test_a_target_is_approached_from_below_and_the_run_stops_within_it(
    target: Target, seed: int
) -> None:
    result = plan(LOOP_GRID, (0, 399), target, seed)
    assert result.run.stopped_because == "target"
    goal, route = result.goal, result.route
    assert goal.reached(goal.value(route.score, route.metrics))
    seen = [goal.value(e.score, e.metrics) for e in result.run.events]
    assert all(b > a for a, b in zip(seen, seen[1:], strict=False))  # growing: never falls
    assert result.run.events[-1].metrics == route.metrics  # the last route is the best


def test_a_base_past_the_target_shrinks_into_it() -> None:
    worst = plan(LOOP_GRID, (0, 399), Target(), 1)
    assert worst.run.stopped_because == "converged"
    for ratio in (7, 5):
        result = plan(LOOP_GRID, (0, 399), Target(kind="ratio", value=ratio), 2, base=worst.route)
        assert result.run.stopped_because == "target"
        seen = values(result, worst.route)
        assert seen[0] > result.goal.high and result.goal.reached(seen[-1])
        assert all(b < a for a, b in zip(seen, seen[1:], strict=False))  # shrinking: never rises
        assert all(e.score < worst.route.score for e in result.run.events)
        assert replay(worst.route, result).nodes.tobytes() == result.route.nodes.tobytes()


def test_a_route_already_within_the_band_stops_before_its_first_attempt() -> None:
    first = plan(LOOP_GRID, (0, 399), Target(), 3, attempts=60)
    ratio = first.route.metrics.length_m / first.baseline.length_m
    result = plan(
        LOOP_GRID, (0, 399), Target(kind="ratio", value=ratio * 1.01), 3, base=first.route
    )
    assert (result.run.stopped_because, result.run.attempts, result.run.events) == ("target", 0, ())
    assert result.route.nodes.tobytes() == first.route.nodes.tobytes()


@pytest.mark.parametrize("seed", range(3))
def test_an_unreachable_target_converges_with_the_closest_route(seed: int) -> None:
    result = plan(TOWN, (0, 24), Target(kind="ratio", value=1000), seed)
    assert result.run.stopped_because == "converged"
    seen = [result.goal.value(e.score, e.metrics) for e in result.run.events]
    assert seen[-1] < result.goal.low
    assert all(b > a for a, b in zip(seen, seen[1:], strict=False))
    assert result.route.metrics == result.run.events[-1].metrics


@pytest.mark.parametrize("seed", range(3))
def test_an_overshoot_comes_after_a_cycle_that_found_nothing_smaller(seed: int) -> None:
    # every corner-to-corner path of the 5 × 5 town is 800 m + a multiple of 200 m, and 2.1 ×
    # 800 m ± 2 % (1,646-1,714 m) holds none: the run grows to 1,600 m, finds only detours
    # past the band for a whole cycle, takes the smallest (1,800 m), then cannot shrink into
    # the band, so it converges; the route it returns is the closer one, 1,600 m
    result = plan(TOWN, (0, 24), Target(kind="ratio", value=2.1), seed)
    run = result.run
    assert run.stopped_because == "converged"
    lengths = [e.metrics.length_m for e in run.events]
    assert lengths[-2:] == [1600.0, 1800.0]
    gap = run.events[-1].iteration - run.events[-2].iteration
    assert gap > CONVERGED_FAILS + 1  # it waited for a whole cycle with nothing smaller
    assert result.route.metrics.length_m == 1600.0  # the closest, not the last
    assert result.route.metrics == run.events[-2].metrics
    # a run cut at the overshoot's attempt is the prefix that ends with it
    cut = plan(
        TOWN, (0, 24), Target(kind="ratio", value=2.1), seed, attempts=run.events[-1].iteration
    )
    assert cut.run.stopped_because == "budget"
    assert [e.splice.edges.tobytes() for e in cut.run.events] == [
        e.splice.edges.tobytes() for e in run.events
    ]
    assert cut.route.metrics.length_m == 1600.0


@pytest.mark.parametrize(
    "target", [Target(kind="ratio", value=5), Target(kind="duration", value=1500)], ids=str
)
def test_a_target_run_is_the_same_bytes_on_both_backends_and_twice(target: Target) -> None:
    routes = [
        plan(LOOP_GRID, (0, 399), target, 5, backend_type=t).route
        for t in (PythonBackend, CsrBackend, PythonBackend)
    ]
    assert len({r.nodes.tobytes() for r in routes}) == 1
    assert len({r.edges.tobytes() for r in routes}) == 1
    worst = plan(LOOP_GRID, (0, 399), Target(), 1).route
    shrunk = [
        plan(LOOP_GRID, (0, 399), Target(kind="ratio", value=6), 4, base=worst, backend_type=t)
        for t in (PythonBackend, CsrBackend)
    ]
    assert shrunk[0].route.nodes.tobytes() == shrunk[1].route.nodes.tobytes()
    assert shrunk[0].run.attempts == shrunk[1].run.attempts
