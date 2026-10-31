# Powell's Books → Reed College on Portland's latest build, read through WORST_ROUTE_DATA: on both
# backends, then the slowest time, the turn constraints and hard mode, collecting each item, a
# pilgrimage to the brand with the most locations, and an alphabet route on both backends. Run on
# demand with `uv run pytest -m data`; skipped when there is no build.
import numpy as np
import pytest

from worst_route.engine.constraints import Constraints, returning
from worst_route.engine.objectives.brand import brand_counts
from worst_route.engine.objectives.collect import ITEMS, CollectItem, count_items
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.engine.route import Route
from worst_route.engine.snap import InfeasibleTurnConstraint
from worst_route.engine.target import Target
from worst_route.engine.turns import count_turns
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.store import read_build
from worst_route.pipeline import layout

pytestmark = pytest.mark.data

POWELLS, REED = (45.5231, -122.6765), (45.4817, -122.6263)


@pytest.fixture(scope="module")
def portland() -> GraphData:
    latest = layout.latest_file("portland")
    if not latest.exists():
        pytest.skip(f"no Portland build under {latest.parent}")
    return read_build(layout.build_dir("portland", latest.read_text().strip()))


def test_both_backends_plan_the_same_route_on_portland(portland: GraphData) -> None:
    # Portland has transition tables, so every search is the transition-aware one: 4,000
    # attempts give the same bytes on both backends, splice for splice
    assert len(portland.trans_node) > 0
    request = RouteRequest(stops=[POWELLS, REED], seed=1, max_iterations=4000, budget_s=120)
    one, two = (
        plan_route(portland, request, backend=b(portland)) for b in (PythonBackend, CsrBackend)
    )
    assert (one.backend, two.backend) == ("python", "csr")
    assert one.run.attempts == two.run.attempts == 4000
    assert one.route.nodes.tobytes() == two.route.nodes.tobytes()
    assert one.route.edges.tobytes() == two.route.edges.tobytes()
    assert one.run.accepted == two.run.accepted > 0
    assert one.run.refused_transitions == two.run.refused_transitions == 0
    assert [e.splice.edges.tobytes() for e in one.run.events] == [
        e.splice.edges.tobytes() for e in two.run.events
    ]
    assert [s.node for s in one.snaps] == [s.node for s in two.snaps]
    assert one.baseline.length_m == two.baseline.length_m


def test_the_slowest_time_reaches_nine_hours_on_portland(portland: GraphData) -> None:
    # on build 506f747d7458 (pipeline version 2), seed 1: 8 h 55 min, 6 h 16 min of driving and
    # 2 h 39 min of delays (24.64x the baseline's 21 min 44 s, 205.63 km)
    nine_hours = Target(kind="duration", value=9 * 3600)
    request = RouteRequest(
        stops=[POWELLS, REED], objective="time", seed=1, budget_s=120, max_iterations=10**6,
        target=nine_hours,
    )  # fmt: skip
    result = plan_route(portland, request)
    assert result.run.stopped_because == "target"
    route = result.route
    reached = result.goal.value(route.score, route.metrics)
    assert reached == route.metrics.time_s + route.metrics.delay_s  # the experienced time
    assert result.goal.reached(reached) and result.report.ok


def test_a_soft_no_left_route_keeps_left_turns_rare_on_portland(portland: GraphData) -> None:
    # on build 506f747d7458, seed 1: 16.77x (129.40 km) with 80 left turns of 1,291, 0.62 a km,
    # where the seed had 10 in 7.71 km, 1.30 a km; converged after 48,152 attempts
    request = RouteRequest(
        stops=[POWELLS, REED], seed=1, budget_s=120, max_iterations=10**6,
        constraints=Constraints(no_left=True),
    )  # fmt: skip
    starts: list[Route] = []
    result = plan_route(portland, request, on_start=lambda *a: starts.append(a[3]))
    route, seed = result.route, starts[0]

    def per_km(r: Route) -> float:
        return count_turns(portland, r.edges)["left"] / r.metrics.length_m * 1000

    assert per_km(route) < per_km(seed) and result.report.ok
    assert route.metrics.length_m > 5 * result.baseline.length_m


def test_strict_no_left_refuses_powells_to_reed_in_one_line(portland: GraphData) -> None:
    request = RouteRequest(
        stops=[POWELLS, REED], seed=1, constraints=Constraints(no_left=True, strict=True)
    )
    with pytest.raises(InfeasibleTurnConstraint) as refused:
        plan_route(portland, request)
    message = str(refused.value)
    assert "\n" not in message and message.endswith("under the strict no-left constraint")
    assert refused.value.constraint == "no_left"


def test_hard_mode_keeps_each_road_one_stretch_on_portland(portland: GraphData) -> None:
    # on builds 506f747d7458 and ffe2e7c881ca, seed 1: 9.68x (74.70 km), 223 roads in as many
    # stretches, converged after 26,710 attempts; on both backends the same, splice for splice
    request = RouteRequest(
        stops=[POWELLS, REED], seed=1, budget_s=120, max_iterations=4000,
        constraints=Constraints(hard=True),
    )  # fmt: skip
    one, two = (
        plan_route(portland, request, backend=b(portland)) for b in (PythonBackend, CsrBackend)
    )
    assert one.route.edges.tobytes() == two.route.edges.tobytes()
    assert one.report.ok and one.report.check("constraints").ok
    assert returning(portland, one.route.edges) is None
    assert one.route.metrics.length_m > 3 * one.baseline.length_m


@pytest.fixture(scope="module")
def distance_items(portland: GraphData) -> dict[str, int]:
    request = RouteRequest(stops=[POWELLS, REED], seed=1, max_iterations=4000, budget_s=120)
    route = plan_route(portland, request, backend=CsrBackend(portland)).route
    return count_items(portland, route.nodes, route.edges)


@pytest.mark.parametrize("item", ITEMS)
def test_collecting_passes_more_of_its_item_than_distance(
    portland: GraphData, distance_items: dict[str, int], item: CollectItem
) -> None:
    # The same 4,000 attempts. Portland's two toll booths are on roads into parks that only
    # those roads reach (cut edges), so no route passes one without a stop beyond it.
    request = RouteRequest(
        stops=[POWELLS, REED],
        seed=1,
        max_iterations=4000,
        budget_s=120,
        objective="collect",
        item=item,
    )
    result = plan_route(portland, request, backend=CsrBackend(portland))
    assert result.report.ok
    collected = result.route.metrics.counters["items"]
    assert collected == count_items(portland, result.route.nodes, result.route.edges)
    if item == "toll_booth":
        assert collected[item] == distance_items[item] == 0
    else:
        assert collected[item] > distance_items[item]


def test_a_collecting_route_reaches_a_duration_target(portland: GraphData) -> None:
    request = RouteRequest(
        stops=[POWELLS, REED],
        seed=1,
        max_iterations=1_000_000,
        budget_s=120,
        objective="collect",
        item="bridge",
        target=Target(kind="duration", value=3 * 3600),
    )
    result = plan_route(portland, request, backend=CsrBackend(portland))
    assert result.run.stopped_because == "target"
    experienced = result.route.metrics.time_s + result.route.metrics.delay_s
    assert 0.98 * 3 * 3600 <= experienced <= 1.02 * 3 * 3600
    assert result.route.metrics.counters["items"]["bridge"] > 0


def test_a_pilgrimage_visits_more_of_its_brand_than_distance(portland: GraphData) -> None:
    # The brand with the most locations, over the same 4,000 attempts as the distance route.
    brand = int(np.argmax(np.diff(portland.brand_indptr)))
    trip = {"stops": [POWELLS, REED], "seed": 1, "max_iterations": 4000, "budget_s": 120}
    distance = plan_route(portland, RouteRequest(**trip), backend=CsrBackend(portland)).route
    request = RouteRequest(**trip, objective="brand", brand=brand)
    result = plan_route(portland, request, backend=CsrBackend(portland))
    assert result.report.ok
    counts = result.route.metrics.counters["brand"]
    assert counts == brand_counts(portland, brand, result.route.nodes, result.masks.active)
    assert counts["visits"] > brand_counts(portland, brand, distance.nodes)["visits"]


def test_an_alphabet_route_is_the_same_on_both_backends_on_portland(portland: GraphData) -> None:
    # on build ffe2e7c881ca, seed 1, 100 restarts: A to I, from SE Ankeny Street to NE Irving Street
    request = RouteRequest(
        stops=[POWELLS, REED], objective="alphabet", seed=1, max_iterations=100, budget_s=120
    )
    one, two = (
        plan_route(portland, request, backend=b(portland)) for b in (PythonBackend, CsrBackend)
    )
    assert one.route.edges.tobytes() == two.route.edges.tobytes()
    assert one.route.metrics.counters == two.route.metrics.counters
    assert one.run.attempts == two.run.attempts == 100
    assert one.run.accepted == two.run.accepted > 0
    assert one.run.refused_transitions == two.run.refused_transitions == 0
    assert two.report.ok and two.report.checks[-1].name == "alphabet"
    letters = two.route.metrics.counters["alphabet"]
    assert letters["letters"] >= 5
    assert letters["sequence"] == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[: letters["letters"]]
