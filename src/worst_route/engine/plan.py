"""Planning a route end to end: the engine's entry point (`docs/reference/engine.md`, **Planning**).

`plan_route(data, request, ...)` computes the request's masks (`request_masks`) and, for a
pilgrimage, refuses a brand the build does not have or the area does not hold (`BrandRefused`,
`objectives.brand.brand_problem`); with `reorder`, refuses more stops than the build allows
(`request.reorder_problem`, a `ValueError` the CLI and the API refuse first) and puts the stops in
the worst order (`seed_route.choose_order`: the longest seed of every order of the stops between
the ends, the given order's errors raised as without it), the request planned from then on being
the reordered one, with its own masks, so everything after (the result's request, the baseline,
the validator) sees the stops in the order the route visits them; makes one
`numpy.random.Generator(PCG64(request.seed))` for every random draw that follows, settles the
stops (`resolve_stops`: snapping and its fallback, which may move stops), builds the seed route
and the baseline on them, makes the request's objective (the distance for the alphabet route)
with the baseline's metrics and
the radius bound r_max = min(leash, the active area's extent), a collecting one also with the
request's item and active nodes, a pilgrimage with its brand and active nodes, with the
request's constraints (`constraints.constrain`; a strict turn constraint and hard mode
constrain the seed too), and its goal (`target.Goal`, the request's target against that
baseline), and runs the worsen loop toward that goal within the request's budget,
handing each accepted splice and the baseline to `on_progress`; for a max goal the loop anneals
once it converges (`worsen`), from `ANNEAL_T0_FRACTION` of the objective's primary quantity of
the baseline, of the route it starts from when that is 0 (a round trip;
`anneal_temperature`). The alphabet route runs its own search instead
(`alphabet_search.alphabet_route`, from the seed scored with its letters), whose
events each replace the whole route by a better one, and is validated with its `alphabet` check;
it cannot be continued from a base route (`ValueError`). A request with `certified` runs the
certified driver instead (`exact.certified_route`, CP-SAT, from the seed): its leash is first
chosen (`exact.certified_request`: with the automatic leash, the widest whose area a certificate
allows), so the request planned from then on carries the leash as a number, its area is worked
out from the resolved stops and refused over the caps (`exact.AreaTooLarge`), the solver gets the
time left of the request's budget less `CERTIFIED_MARGIN_S` (at least a tenth of the budget), each
better route it finds replaces the whole route in `on_progress`, its bound goes to `on_bound`
between them, and the result carries the `Certificate`; it cannot be continued from a base route
either (`ValueError`). It searches with the backend it
is given, else `make_backend`'s default (`config.DEFAULT_BACKEND`); every backend gives the same
route, and the result records which one searched. The best
route is then validated against the request's masks and the resolved stops; a route that fails
raises `ValidationFailed` and is never returned. Errors a caller should expect: `SnapError` (no
node near a stop can play its role) and `StopUnreachable` (a leg no route covers without
repeating a road). The seed checks every leg itself, so a `ValueError` or `IllegalSplice` from
seeding would be a bug; it is still reported as `StopUnreachable` for the leg at fault, one
line from the CLI rather than a traceback.

Two hooks serve the API (`docs/reference/api.md`). `on_start(snaps, masks, baseline, route,
order)` is called once, after the route the loop starts from is made and before the loop
(`order`, each stop's position among those given, is the identity without `reorder`). `base`,
a route planned earlier on this build for the same stops, tier and leash, is what the loop
starts from instead of the seed ("make it worse"; with a target, a base past it shrinks toward
it), in the base's order (with `reorder` set it is a `ValueError`). `check_base` refuses it
with `BaseRouteMismatch` (a `ValueError` naming the field at fault) unless its stop nodes are
the ones `resolve_stops` gives, every node lies in the request's active mask, none of its edges
is one the request bans, under a strict turn constraint none of its turns is one the constraint
refuses (a soft one takes any base), and under hard mode no road it leaves comes back;
`Route.new` then checks it as it checks any route (repeats, twins, transitions). Nothing is
drawn from the rng before the loop then, so the run is fixed by the base, the request and its
seed. Without them, planning is exactly as above.
"""

import time
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from worst_route.config import ANNEAL_T0_FRACTION, CERTIFIED_MARGIN_S
from worst_route.engine.alphabet_search import alphabet_route, alphabet_scored, edge_letters
from worst_route.engine.constraints import Constraints, constrain, returning
from worst_route.engine.exact import (
    Certificate,
    OnBound,
    certified_area,
    certified_request,
    certified_route,
    too_large,
)
from worst_route.engine.objectives import make_objective
from worst_route.engine.objectives.base import Objective
from worst_route.engine.objectives.brand import BrandRefused, brand_problem
from worst_route.engine.request import RequestMasks, RouteRequest, reorder_problem, request_masks
from worst_route.engine.route import IllegalSplice, Route
from worst_route.engine.seed_route import (
    Baseline,
    baseline_route,
    choose_order,
    resolve_stops,
    seed_route,
)
from worst_route.engine.snap import StopUnreachable
from worst_route.engine.target import Goal
from worst_route.engine.turns import turn_classes
from worst_route.engine.validator import (
    RouteLike,
    ValidationFailed,
    ValidationReport,
    validate_route,
)
from worst_route.engine.worsen import Budget, ProgressEvent, WorsenResult, run_worsen
from worst_route.graph.backend import GraphBackend
from worst_route.graph.backends import make_backend
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import Snap, SpatialIndex

# `plan_route`'s `on_start(snaps, masks, baseline, route, order)`.
OnStart = Callable[[tuple[Snap, ...], RequestMasks, Baseline, Route, tuple[int, ...]], None]


@dataclass(frozen=True, eq=False)
class PlanResult:
    """A validated route and everything that went into it: the request as planned (with
    `reorder`, its stops in the order the route visits them) and `order`, each stop's position
    among those asked for (the identity without `reorder`)."""

    request: RouteRequest
    masks: RequestMasks
    snaps: tuple[Snap, ...]
    baseline: Baseline
    route: Route
    run: WorsenResult
    report: ValidationReport
    backend: str  # the name of the backend that searched
    goal: Goal
    order: tuple[int, ...]
    certified: Certificate | None = None  # a certified route's (`exact.certified_route`)


def plan_route(
    data: GraphData,
    request: RouteRequest,
    *,
    backend: GraphBackend | None = None,
    spatial: SpatialIndex | None = None,
    base: RouteLike | None = None,
    on_start: OnStart | None = None,
    on_progress: Callable[[ProgressEvent, Baseline], None] | None = None,
    on_bound: OnBound | None = None,
    check_delta: bool = False,
) -> PlanResult:
    """Plan, worsen and validate the route `request` asks for on this build (from `base` if
    given)."""
    started = time.monotonic()
    alphabet = request.objective == "alphabet"
    if alphabet and base is not None:
        raise ValueError("the alphabet route cannot be continued")
    if request.reorder and base is not None:
        raise ValueError("a continuation keeps its base route's order: reorder must be off")
    if request.certified and base is not None:
        raise ValueError("a certified route is planned from its stops: no base route")
    backend = backend if backend is not None else make_backend(data)
    spatial = spatial if spatial is not None else SpatialIndex(data)
    if request.certified:
        request = certified_request(data, spatial, request)
    masks = request_masks(spatial, data, request)
    if request.brand is not None:
        problem = brand_problem(data, request.brand, masks.active)
        if problem is not None:
            raise BrandRefused(problem)
    given = order = tuple(range(len(request.stops)))
    if request.reorder:
        refused = reorder_problem(len(request.stops), data)
        if refused is not None:
            raise ValueError(refused)
        request, order = choose_order(data, spatial, backend, request)
        if order != given:
            masks = request_masks(spatial, data, request)
    rng = np.random.Generator(np.random.PCG64(request.seed))
    r_max = min(masks.leash_m, masks.extent_m)

    snaps = resolve_stops(data, spatial, backend, request, masks)
    stop_nodes = [s.node for s in snaps]
    area = certified_area(data, masks, stop_nodes) if request.certified else None
    if area is not None and (oversized := too_large(area, round(masks.leash_m, 1))) is not None:
        raise oversized
    if base is None:
        try:
            seed = seed_route(
                backend,
                spatial,
                data,
                snaps,
                masks.active,
                masks.banned,
                rng,
                r_max=r_max,
                constraints=request.constraints,
            )
        except (ValueError, IllegalSplice) as error:
            raise _refused(error, None) from error
        nodes, edges, stop_idx = seed.nodes, seed.edges, seed.stop_idx
    else:
        check_base(base, masks, stop_nodes, data=data, constraints=request.constraints)
        nodes, edges, stop_idx = base.nodes, base.edges, base.stop_idx
    baseline = baseline_route(backend, data, stop_nodes, masks.active, masks.banned)
    objective = constrain(
        make_objective(
            "distance" if alphabet else request.objective,
            data,
            baseline=baseline.metrics,
            r_max=r_max,
            item=request.item,
            brand=request.brand,
            area=masks.active,
        ),
        request.constraints,
        data,
    )
    goal = Goal(request.target, data, primary=objective.primary, baseline=baseline.metrics)
    letters = edge_letters(data) if alphabet else None  # the build's, once for the run
    if letters is not None:
        score, metrics = alphabet_scored(objective, data, letters, nodes, edges, 0)
    else:
        score, metrics = objective.evaluate(nodes, edges)
    try:
        route = Route.new(
            data,
            nodes,
            edges,
            stop_idx,
            score=score,
            metrics=metrics,
            seed=request.seed,
            extra_banned=masks.banned,
        )
    except (ValueError, IllegalSplice) as error:
        if base is not None:
            raise
        raise _refused(error, stop_idx) from error
    if on_start is not None:
        on_start(tuple(snaps), masks, baseline, route, order)

    def progress(event: ProgressEvent) -> None:
        if on_progress is not None:
            on_progress(event, baseline)

    budget = Budget(seconds=request.budget_s, max_iterations=request.max_iterations)
    certificate = None
    if request.certified:
        assert area is not None  # the stops resolve, so a walk joins each to the next
        left = request.budget_s - (time.monotonic() - started) - CERTIFIED_MARGIN_S
        run, certificate = certified_route(
            data,
            route,
            area,
            objective,
            Budget(max(left, request.budget_s / 10), request.max_iterations),
            leash_m=round(masks.leash_m, 1),
            banned_edges=masks.banned,
            on_event=progress,
            on_bound=on_bound,
        )
    elif alphabet:
        run = alphabet_route(
            backend,
            data,
            route,
            objective,
            budget,
            rng,
            masks.active,
            r_max=r_max,
            banned_edges=masks.banned,
            letters=letters,
            on_event=progress,
        )
    else:
        run = run_worsen(
            backend,
            spatial,
            data,
            route,
            objective,
            budget,
            rng,
            masks.active,
            r_max=r_max,
            banned_edges=masks.banned,
            goal=goal,
            check_delta=check_delta,
            on_event=progress,
            anneal_t0=anneal_temperature(objective, baseline, route) if goal.maximise else None,
        )
    report = validate_route(
        data,
        run.best,
        stop_nodes=stop_nodes,
        node_mask=masks.active,
        banned_edges=masks.banned,
        banned_turn_classes=request.constraints.banned_turn_classes,
        hard=request.constraints.hard,
        alphabet=alphabet,
    )
    if not report.ok:
        raise ValidationFailed(report)
    return PlanResult(
        request,
        masks,
        tuple(snaps),
        baseline,
        run.best,
        run,
        report,
        backend.name,
        goal,
        order,
        certificate,
    )


class BaseRouteMismatch(ValueError):  # noqa: N818 (the API error it becomes)
    """A base route the request cannot continue; `field` is `"stops"` or `"route"`."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def check_base(
    base: RouteLike,
    masks: RequestMasks,
    stop_nodes: Sequence[int],
    *,
    data: GraphData,
    constraints: Constraints,
) -> None:
    """Raise `BaseRouteMismatch` unless `base` may be continued under these masks, stops and
    constraints."""
    nodes, edges = np.asarray(base.nodes), np.asarray(base.edges)
    if [int(nodes[k]) for k in base.stop_idx] != list(stop_nodes):
        message = "the base route's stops are not the ones the request resolves to"
        raise BaseRouteMismatch("stops", message)
    outside = np.flatnonzero(~masks.active[nodes])
    if len(outside):
        message = f"the base route leaves the request's area at node {nodes[outside[0]]}"
        raise BaseRouteMismatch("route", message)
    banned = np.flatnonzero(masks.banned[edges])
    if len(banned):
        message = f"the base route drives edge {edges[banned[0]]}, which the request bans"
        raise BaseRouteMismatch("route", message)
    classes = turn_classes(data, edges[:-1], edges[1:]) & constraints.banned_turn_classes
    refused = np.flatnonzero(classes)
    if len(refused):
        node, label = nodes[refused[0] + 1], constraints.kinds[0].label
        message = f"the base route turns at node {node} as the strict {label} constraint refuses"
        raise BaseRouteMismatch("route", message)
    found = returning(data, edges) if constraints.hard else None
    if found is not None:
        again = found[1][0]
        road = data.strings.identities[data.edge_identity[edges[again]]]
        message = f"the base route comes back to {road} at edge {again}, which hard mode refuses"
        raise BaseRouteMismatch("route", message)


def anneal_temperature(objective: Objective, baseline: Baseline, route: Route) -> float:
    """The temperature a max run's reheats start at: `ANNEAL_T0_FRACTION` of the objective's
    primary quantity of the baseline, or of `route`'s when that is 0 (a round trip)."""
    scale = objective.primary(baseline.metrics) or objective.primary(route.metrics)
    return ANNEAL_T0_FRACTION * scale


def _refused(error: ValueError | IllegalSplice, stop_idx: Sequence[int] | None) -> StopUnreachable:
    """A seed refused after its own checks passed, as the leg at fault (the first if unknown)."""
    k = 0
    if isinstance(error, IllegalSplice) and stop_idx is not None:
        k = min(max(bisect_right(stop_idx, error.index) - 1, 0), len(stop_idx) - 2)
    return StopUnreachable((k, k + 1), f"the seed route was refused: {error}")
