"""Planning a route end to end: the engine's entry point (`docs/reference/engine.md`, **Planning**).

`plan_route(data, request, ...)` computes the request's masks (`request_masks`), makes one
`numpy.random.Generator(PCG64(request.seed))` for every random draw that follows, settles the
stops (`resolve_stops`: snapping and its fallback, which may move stops), builds the seed route
and the baseline on them, makes the request's objective with the baseline length and the radius
bound r_max = min(leash, the active area's extent), and runs the worsen loop within the
request's budget, handing each accepted splice and the baseline to `on_progress`. It searches
with the backend it is given, else `make_backend`'s default (`config.DEFAULT_BACKEND`); every
backend gives the same route, and the result records which one searched. The best
route is then validated against the request's masks and the resolved stops; a route that fails
raises `ValidationFailed` and is never returned. Errors a caller should expect: `SnapError` (no
node near a stop can play its role) and `StopUnreachable` (a leg no route covers without
repeating a road). The seed checks every leg itself, so a `ValueError` or `IllegalSplice` from
seeding would be a bug; it is still reported as `StopUnreachable` for the leg at fault, one
line from the CLI rather than a traceback.
"""

from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from worst_route.engine.objectives import make_objective
from worst_route.engine.request import RequestMasks, RouteRequest, request_masks
from worst_route.engine.route import IllegalSplice, Route
from worst_route.engine.seed_route import Baseline, baseline_route, resolve_stops, seed_route
from worst_route.engine.snap import StopUnreachable
from worst_route.engine.validator import ValidationFailed, ValidationReport, validate_route
from worst_route.engine.worsen import Budget, ProgressEvent, WorsenResult, run_worsen
from worst_route.graph.backend import GraphBackend
from worst_route.graph.backends import make_backend
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import Snap, SpatialIndex


@dataclass(frozen=True, eq=False)
class PlanResult:
    """A validated route and everything that went into it."""

    request: RouteRequest
    masks: RequestMasks
    snaps: tuple[Snap, ...]
    baseline: Baseline
    route: Route
    run: WorsenResult
    report: ValidationReport
    backend: str  # the name of the backend that searched


def plan_route(
    data: GraphData,
    request: RouteRequest,
    *,
    backend: GraphBackend | None = None,
    spatial: SpatialIndex | None = None,
    on_progress: Callable[[ProgressEvent, Baseline], None] | None = None,
    check_delta: bool = False,
) -> PlanResult:
    """Plan, worsen and validate the route `request` asks for on this build."""
    backend = backend if backend is not None else make_backend(data)
    spatial = spatial if spatial is not None else SpatialIndex(data)
    masks = request_masks(spatial, data, request)
    rng = np.random.Generator(np.random.PCG64(request.seed))
    r_max = min(masks.leash_m, masks.extent_m)

    snaps = resolve_stops(data, spatial, backend, request, masks)
    stop_nodes = [s.node for s in snaps]
    try:
        seed = seed_route(
            backend, spatial, data, snaps, masks.active, masks.banned, rng, r_max=r_max
        )
    except (ValueError, IllegalSplice) as error:
        raise _refused(error, None) from error
    baseline = baseline_route(backend, data, stop_nodes, masks.active, masks.banned)
    objective = make_objective(
        request.objective, data, baseline_length_m=baseline.length_m, r_max=r_max
    )
    score, metrics = objective.evaluate(seed.nodes, seed.edges)
    try:
        route = Route.new(
            data,
            seed.nodes,
            seed.edges,
            seed.stop_idx,
            score=score,
            metrics=metrics,
            seed=request.seed,
            extra_banned=masks.banned,
        )
    except (ValueError, IllegalSplice) as error:
        raise _refused(error, seed.stop_idx) from error

    def progress(event: ProgressEvent) -> None:
        if on_progress is not None:
            on_progress(event, baseline)

    run = run_worsen(
        backend,
        spatial,
        data,
        route,
        objective,
        Budget(seconds=request.budget_s, max_iterations=request.max_iterations),
        rng,
        masks.active,
        r_max=r_max,
        banned_edges=masks.banned,
        check_delta=check_delta,
        on_event=progress,
    )
    report = validate_route(
        data,
        run.best,
        stop_nodes=stop_nodes,
        node_mask=masks.active,
        banned_edges=masks.banned,
    )
    if not report.ok:
        raise ValidationFailed(report)
    return PlanResult(request, masks, tuple(snaps), baseline, run.best, run, report, backend.name)


def _refused(error: ValueError | IllegalSplice, stop_idx: Sequence[int] | None) -> StopUnreachable:
    """A seed refused after its own checks passed, as the leg at fault (the first if unknown)."""
    k = 0
    if isinstance(error, IllegalSplice) and stop_idx is not None:
        k = min(max(bisect_right(stop_idx, error.index) - 1, 0), len(stop_idx) - 2)
    return StopUnreachable((k, k + 1), f"the seed route was refused: {error}")
