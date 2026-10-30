"""`plan_route`'s hooks for the API: `on_start` before the loop, and `base` to continue a route."""

from dataclasses import dataclass, field, replace

import numpy as np
import pytest
from numpy.typing import NDArray

from worst_route.engine.plan import BaseRouteMismatch, PlanResult, check_base, plan_route
from worst_route.engine.request import RequestMasks, RouteRequest, request_masks
from worst_route.engine.route import Route, Splice
from worst_route.engine.seed_route import Baseline
from worst_route.engine.worsen import ProgressEvent
from worst_route.graph.backend import GraphBackend
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import Snap, SpatialIndex

from .graphs import at_node, lattice

GRID = lattice(8, 8, rounded_lonlat=True)
PYTHON = PythonBackend(GRID)


@dataclass
class Seen:
    """What the hooks saw: each start (with the splices seen before it) and every splice."""

    starts: list[tuple[int, tuple[Snap, ...], RequestMasks, Baseline, Route]] = field(
        default_factory=list
    )
    splices: list[Splice] = field(default_factory=list)


def request(stops: list[int], seed: int, attempts: int) -> RouteRequest:
    return RouteRequest(
        stops=[at_node(GRID, v) for v in stops], seed=seed, max_iterations=attempts, budget_s=120
    )


def plan(
    req: RouteRequest, base: Route | None = None, backend: GraphBackend = PYTHON
) -> tuple[PlanResult, Seen]:
    seen = Seen()

    def start(
        snaps: tuple[Snap, ...], masks: RequestMasks, baseline: Baseline, route: Route
    ) -> None:
        seen.starts.append((len(seen.splices), snaps, masks, baseline, route))

    def progress(event: ProgressEvent, baseline: Baseline) -> None:
        seen.splices.append(event.splice)

    result = plan_route(GRID, req, backend=backend, base=base, on_start=start, on_progress=progress)
    return result, seen


def replay(route: Route, splices: list[Splice]) -> bytes:
    """The bytes of the route's nodes after the splices, in order."""
    nodes: NDArray[np.int32] = route.nodes
    for s in splices:
        nodes = np.concatenate((nodes[: s.i], s.nodes, nodes[s.j + 1 :]))
    return nodes.tobytes()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_on_start_comes_once_before_the_loop_with_the_route_it_starts_from(seed: int) -> None:
    result, seen = plan(request([0, 27, 63], seed, 200))
    [(before, snaps, masks, baseline, start)] = seen.starts
    assert before == 0 and seen.splices
    assert snaps == result.snaps and masks is result.masks and baseline is result.baseline
    assert start.stop_idx[0] == 0 and [int(start.nodes[k]) for k in start.stop_idx] == [0, 27, 63]
    assert replay(start, seen.splices) == result.route.nodes.tobytes()


def test_without_hooks_the_route_is_the_same() -> None:
    req = request([0, 27, 63], 4, 200)
    hooked, _ = plan(req)
    plain = plan_route(GRID, req, backend=PYTHON)
    assert plain.route.nodes.tobytes() == hooked.route.nodes.tobytes()


def test_a_base_route_is_continued_and_never_scores_less() -> None:
    first, _ = plan(request([0, 27, 63], 1, 60))
    result, seen = plan(request([0, 27, 63], 9, 400), base=first.route)
    start = seen.starts[0][4]
    assert start.nodes.tobytes() == first.route.nodes.tobytes()
    assert start.score == first.route.score
    assert result.route.score >= first.route.score and result.report.ok
    assert [int(result.route.nodes[k]) for k in result.route.stop_idx] == [0, 27, 63]
    assert replay(start, seen.splices) == result.route.nodes.tobytes()


@pytest.mark.parametrize("backend", [PYTHON, CsrBackend(GRID)], ids=["python", "csr"])
def test_a_continuation_is_fixed_by_the_base_and_the_seed(backend: GraphBackend) -> None:
    base = plan(request([0, 63], 1, 40))[0].route
    one = plan(request([0, 63], 5, 300), base=base, backend=backend)[0].route
    two = plan(request([0, 63], 5, 300), base=base, backend=PYTHON)[0].route
    other = plan(request([0, 63], 6, 300), base=base, backend=backend)[0].route
    assert one.nodes.tobytes() == two.nodes.tobytes()
    assert one.edges.tobytes() == two.edges.tobytes()
    assert one.nodes.tobytes() != other.nodes.tobytes()


def test_a_base_route_for_other_stops_is_refused() -> None:
    base = plan(request([0, 63], 1, 40))[0].route
    with pytest.raises(BaseRouteMismatch, match="base route's stops") as refused:
        plan_route(GRID, request([0, 62], 2, 40), backend=PYTHON, base=base)
    assert refused.value.field == "stops" and isinstance(refused.value, ValueError)


def test_a_base_route_outside_the_request_s_area_is_refused_before_the_loop() -> None:
    base = plan(request([0, 63], 1, 400))[0].route
    narrow = request([0, 63], 2, 40).model_copy(update={"leash_km": 0.15})
    seen: list[object] = []
    with pytest.raises(BaseRouteMismatch, match="leaves the request's area") as refused:
        plan_route(GRID, narrow, backend=PYTHON, base=base, on_start=lambda *a: seen.append(a))
    assert refused.value.field == "route" and not seen


def test_check_base_refuses_a_node_outside_the_mask_and_an_edge_the_request_bans() -> None:
    base = plan(request([0, 63], 1, 100))[0].route
    stops = [int(base.nodes[0]), int(base.nodes[-1])]
    masks = request_masks(SpatialIndex(GRID), GRID, request([0, 63], 1, 100))
    check_base(base, masks, stops)
    active = masks.active.copy()
    active[base.nodes[3]] = False
    with pytest.raises(BaseRouteMismatch, match=f"at node {base.nodes[3]}"):
        check_base(base, replace(masks, active=active), stops)
    banned = masks.banned.copy()
    banned[base.edges[5]] = True
    with pytest.raises(BaseRouteMismatch, match=f"drives edge {base.edges[5]}"):
        check_base(base, replace(masks, banned=banned), stops)
