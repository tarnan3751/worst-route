"""The worsen loop (`docs/PLAN.md` §3.4; `docs/reference/engine.md`, **The loop**).

`worsen(backend, spatial, data, route, objective, budget, rng, node_mask, r_max=, ...)` is a
generator: each attempt picks a window of the route, asks `find_detour` for a replacement
inside the masks, and splices it in when the objective's `delta` is strictly positive and its
`feasible_window` agrees; every accepted splice yields a `ProgressEvent` (the attempt number,
the elapsed time, the new score and metrics, the `Splice` that made it). The generator returns
a `WorsenResult`: the best route by `objective.better`, every event, the attempts and accepted
splices, why it stopped, and the time it took. `run_worsen` drives it to the end.

- The window is one edge (j = i + 1) with probability `SINGLE_EDGE_PROBABILITY`, else a run
  of 2 to 4 edges, cut short at the next stop so no stop is ever inside it.
- The search masks are the route's own once the window is released (`Route.release`), with
  the request's `banned_edges` and the objective's bans for the window; those two are also
  what the splice is told to refuse.
- The ambition radius starts at `objective.initial_radius`, grows ×1.5 on every accepted splice
  up to `r_max`, and halves after every 20 failures in a row down to `RADIUS_MIN_M` (or r_max
  when that is smaller). A failure is no detour, a gain of zero or less, or an infeasible window.
- The run stops when `budget.max_iterations` attempts are made or `budget.seconds` have passed
  ("budget"), or after more than 400 failures in a row at the smallest radius ("converged").

Every random choice is `rng`'s, in a fixed order, and candidate lists are sorted arrays, so a
seed gives the same sequence of splices on any machine. The clock decides only when to stop: a
run that stops on time is a prefix of that sequence, and one that stops on iterations is
reproduced exactly. `check_delta` (for tests) asserts on every accepted splice that `delta`
equals the full re-score.
"""

import math
import time
from bisect import bisect_right
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from worst_route.config import (
    CONVERGED_FAILS,
    RADIUS_GROWTH,
    RADIUS_HALVE_EVERY,
    RADIUS_MIN_M,
    RUN_EDGES,
    SINGLE_EDGE_PROBABILITY,
)
from worst_route.engine.detour import find_detour
from worst_route.engine.objectives.base import Objective
from worst_route.engine.route import Metrics, Route, Splice, splice_arrays
from worst_route.graph.backend import GraphBackend
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex

BoolArray = NDArray[np.bool_]
StopReason = Literal["converged", "budget"]


@dataclass(frozen=True)
class Budget:
    """How long a run may go: wall-clock seconds and attempts."""

    seconds: float
    max_iterations: int


@dataclass(frozen=True, eq=False)
class ProgressEvent:
    """One accepted splice: after `iteration` attempts, the route scores `score`."""

    iteration: int
    elapsed_s: float
    score: float
    metrics: Metrics
    splice: Splice


@dataclass(frozen=True, eq=False)
class WorsenResult:
    """The best route of a run, its events, and how the run went."""

    best: Route
    events: tuple[ProgressEvent, ...]
    attempts: int
    accepted: int
    stopped_because: StopReason
    elapsed_s: float


def pick_segment(route: Route, rng: np.random.Generator) -> tuple[int, int]:
    """A window (i, j) of the route: one edge mostly, sometimes a short run, no stop inside."""
    count = len(route.edges)
    if rng.random() < SINGLE_EDGE_PROBABILITY:
        i = int(rng.integers(count))
        return i, i + 1
    low, high = RUN_EDGES
    k = int(rng.integers(low, high + 1))
    i = int(rng.integers(count))
    next_stop = route.stop_idx[bisect_right(route.stop_idx, i)]
    return i, min(i + k, next_stop)


def worsen(
    backend: GraphBackend,
    spatial: SpatialIndex,
    data: GraphData,
    route: Route,
    objective: Objective,
    budget: Budget,
    rng: np.random.Generator,
    node_mask: BoolArray,
    *,
    r_max: float,
    banned_edges: BoolArray | None = None,
    check_delta: bool = False,
) -> Generator[ProgressEvent, None, WorsenResult]:
    """Make `route` worse, one accepted splice per event; returns the run's result."""
    start = time.monotonic()
    r_min = min(RADIUS_MIN_M, r_max)
    radius = objective.initial_radius(route)
    best = route
    attempts = accepted = fails = 0
    events: list[ProgressEvent] = []
    while True:
        if attempts >= budget.max_iterations or time.monotonic() - start >= budget.seconds:
            stopped: StopReason = "budget"
            break
        if fails > CONVERGED_FAILS and radius == r_min:
            stopped = "converged"
            break
        attempts += 1
        i, j = pick_segment(route, rng)
        forbid, banned = route.release(data, i, j)
        extra = _either(banned_edges, objective.banned_edges(data, route, i, j))
        if extra is not None:
            banned |= extra
        found = find_detour(
            backend,
            spatial,
            data,
            route,
            i,
            j,
            forbid=forbid,
            banned=banned,
            node_mask=node_mask,
            radius=radius,
            objective=objective,
            rng=rng,
            entry_edge=int(route.edges[i - 1]) if i > 0 else -1,
            exit_edge=int(route.edges[j]) if j < len(route.edges) else -1,
        )
        gain = 0.0 if found is None else objective.delta(route, i, j, *found)
        if found is None or gain <= 0 or not objective.feasible_window(data, route, i, j, *found):
            fails += 1
            if fails % RADIUS_HALVE_EVERY == 0:
                radius = max(radius / 2, r_min)
            continue

        score, metrics = objective.evaluate(*splice_arrays(route, i, j, *found))
        if check_delta and not math.isclose(gain, score - route.score, rel_tol=1e-9, abs_tol=1e-6):
            raise AssertionError(f"delta {gain} != re-score {score - route.score} at ({i}, {j})")
        route = route.splice(data, i, j, *found, score=score, metrics=metrics, extra_banned=extra)
        accepted += 1
        fails = 0
        radius = min(radius * RADIUS_GROWTH, r_max)
        if objective.better(route, best):
            best = route
        event = ProgressEvent(
            iteration=attempts,
            elapsed_s=time.monotonic() - start,
            score=route.score,
            metrics=route.metrics,
            splice=Splice(i, j, *found),
        )
        events.append(event)
        yield event
    return WorsenResult(best, tuple(events), attempts, accepted, stopped, time.monotonic() - start)


def run_worsen(
    backend: GraphBackend,
    spatial: SpatialIndex,
    data: GraphData,
    route: Route,
    objective: Objective,
    budget: Budget,
    rng: np.random.Generator,
    node_mask: BoolArray,
    *,
    r_max: float,
    banned_edges: BoolArray | None = None,
    check_delta: bool = False,
    on_event: Callable[[ProgressEvent], None] | None = None,
) -> WorsenResult:
    """Run `worsen` to the end, handing each event to `on_event`; return its result."""
    run = worsen(
        backend,
        spatial,
        data,
        route,
        objective,
        budget,
        rng,
        node_mask,
        r_max=r_max,
        banned_edges=banned_edges,
        check_delta=check_delta,
    )
    while True:
        try:
            event = next(run)
        except StopIteration as stop:
            result: WorsenResult = stop.value
            return result
        if on_event is not None:
            on_event(event)


def _either(a: BoolArray | None, b: BoolArray | None) -> BoolArray | None:
    if a is None or b is None:
        return a if b is None else b
    return a | b
