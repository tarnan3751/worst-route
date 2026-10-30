"""The worsen loop (`docs/PLAN.md` §3.4; `docs/reference/engine.md`, **The loop**).

`worsen(backend, spatial, data, route, objective, budget, rng, node_mask, r_max=, goal=, ...)`
is a generator: each attempt picks a window of the route, asks `find_detour` for a replacement
inside the masks, and splices it in when the goal accepts it (`target.Goal.accepts`: for `max`,
the default, the objective's `delta` strictly positive) and the objective's `feasible_window`
agrees; every accepted splice yields a `ProgressEvent` (the attempt number, the elapsed time,
the new score and metrics, the `Splice` that made it). The generator returns a `WorsenResult`:
the best route by `goal.better` (for `max` the highest score, for a target the closest),
every event, the attempts and accepted splices, why it stopped, the time it took, and how many
candidates the splice refused for a turn a transition table forbids (the searches see the
tables, so this should stay near zero). `run_worsen` drives it to the end.

A target run (ratio or duration) has phases, by the route's value (`Goal.phase`): below the
band it grows, taking detours that gain without passing the band's top; above it, it shrinks,
taking shorter replacements that do not pass the band's bottom, with the radius held at its
floor, longer windows (`SHRINK_RUN_EDGES_MEAN`, at most `SHRINK_SEGMENT_RUN_MAX`) and the
local-exhaustive paths ranked by how close they bring the route to the band; inside it, the
run stops ("target"), before its first attempt too. Growing, the smallest candidate that would
pass the band's top is kept (from the last accepted splice on); when a whole cycle accepts
nothing, it is spliced in as one more attempt, and the run goes on shrinking.

- The window is one edge (j = i + 1) with probability `SINGLE_EDGE_PROBABILITY`, else a run
  of 1 + a geometric number of edges (`RUN_EDGES_MEAN` on average, at most `SEGMENT_RUN_MAX`),
  cut short at the next stop so no stop is ever inside it. With `SEGMENT_BIAS_PROBABILITY` it
  starts at one of the `SEGMENT_BIAS_TOP` route edges with the most unused active nodes near
  their midpoint (`UnusedNodes`: a grid of counts, its summed-area table rebuilt on every
  accepted splice, read in a square of half-side `WAYPOINT_BALL_FACTOR` × the radius), else
  at a uniform edge.
- The search masks are the route's own once the window is released (`Route.release`), with
  the request's `banned_edges` and the objective's bans for the window; those two are also
  what the splice is told to refuse.
- The ambition radius starts at `objective.initial_radius`, grows ×1.5 on every accepted splice
  up to `r_max`, and halves after every 20 failures in a row down to `RADIUS_MIN_M` (or r_max
  when that is smaller); shrinking, it stays at that floor. A failure is no detour, one the
  goal does not accept (for `max`, a gain of zero or less), or an infeasible window.
- The run goes in cycles. A cycle ends after more than `CONVERGED_FAILS` (400) failures in a
  row at the smallest radius; the radius goes back to `objective.initial_radius` (the floor,
  shrinking) and a new cycle starts.
- The run stops when the goal is reached ("target"), when `budget.max_iterations` attempts are
  made or `budget.seconds` have passed ("budget"), or when `CONVERGED_EMPTY_CYCLES` cycles in a
  row accept nothing ("converged").

Every random choice is `rng`'s, in a fixed order, and candidate lists are sorted arrays, so a
seed gives the same sequence of splices on any machine. The clock decides only when to stop: a
run that stops on time is a prefix of that sequence, and one that stops on iterations is
reproduced exactly. `check_delta` (for tests) asserts on every accepted splice that `delta`
equals the full re-score.
"""

import math
import time
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from worst_route.config import (
    CONVERGED_EMPTY_CYCLES,
    CONVERGED_FAILS,
    RADIUS_GROWTH,
    RADIUS_HALVE_EVERY,
    RADIUS_MIN_M,
    RUN_EDGES_MEAN,
    SEGMENT_BIAS_CELL_M,
    SEGMENT_BIAS_PROBABILITY,
    SEGMENT_BIAS_TOP,
    SEGMENT_RUN_MAX,
    SHRINK_RUN_EDGES_MEAN,
    SHRINK_SEGMENT_RUN_MAX,
    SINGLE_EDGE_PROBABILITY,
    UNUSED_GRID_MAX_CELLS,
    WAYPOINT_BALL_FACTOR,
)
from worst_route.engine.detour import find_detour
from worst_route.engine.objectives.base import Objective
from worst_route.engine.route import (
    IllegalSplice,
    Metrics,
    Route,
    Splice,
    TransitionForbidden,
    splice_arrays,
)
from worst_route.engine.target import Goal, Target
from worst_route.graph.backend import GraphBackend, Path
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex

BoolArray = NDArray[np.bool_]
StopReason = Literal["converged", "budget", "target"]


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
    refused_transitions: int


@dataclass(frozen=True, eq=False)
class _Overshoot:
    """A detour for nodes[i..j] that would carry a growing route to `value`, past the band."""

    i: int
    j: int
    path: Path
    gain: float
    value: float
    extra: BoolArray | None


def pick_segment(
    route: Route,
    rng: np.random.Generator,
    anchors: NDArray[np.intp] | None = None,
    *,
    shrinking: bool = False,
) -> tuple[int, int]:
    """A window (i, j) of the route: one edge mostly, sometimes a short run (a longer one when
    `shrinking`), no stop inside; starting, with `SEGMENT_BIAS_PROBABILITY`, at one of the
    `anchors` (route edge positions)."""
    count = len(route.edges)
    biased = anchors is not None and rng.random() < SEGMENT_BIAS_PROBABILITY

    def first() -> int:
        if biased and anchors is not None:
            return int(anchors[rng.integers(len(anchors))])
        return int(rng.integers(count))

    if rng.random() < SINGLE_EDGE_PROBABILITY:
        i = first()
        return i, i + 1
    mean, most = (
        (SHRINK_RUN_EDGES_MEAN, SHRINK_SEGMENT_RUN_MAX)
        if shrinking
        else (RUN_EDGES_MEAN, SEGMENT_RUN_MAX)
    )
    k = min(1 + int(rng.geometric(1 / (mean - 1))), most)
    i = first()
    next_stop = route.stop_idx[bisect_right(route.stop_idx, i)]
    return i, min(i + k, next_stop)


class UnusedNodes:
    """The active nodes a route does not use, counted on a grid of square cells, and the route
    edges with the most of them near their midpoint. A cell is `SEGMENT_BIAS_CELL_M` wide, or
    wider when the active area needs it to keep the grid within `UNUSED_GRID_MAX_CELLS`."""

    def __init__(self, data: GraphData, node_mask: BoolArray) -> None:
        self.data = data
        self.nodes = np.flatnonzero(node_mask)
        x, y = data.node_x[self.nodes], data.node_y[self.nodes]
        self.x0, self.y0 = float(x.min()), float(y.min())
        width, height = float(x.max()) - self.x0, float(y.max()) - self.y0
        cell = max(SEGMENT_BIAS_CELL_M, math.sqrt(width * height / UNUSED_GRID_MAX_CELLS))
        while (width // cell + 1) * (height // cell + 1) > UNUSED_GRID_MAX_CELLS:
            cell *= 1.01
        self.cell_m = cell
        col = ((x - self.x0) // cell).astype(np.intp)
        row = ((y - self.y0) // cell).astype(np.intp)
        self.cols, self.rows = int(col.max()) + 1, int(row.max()) + 1
        self.cell = row * self.cols + col

    def table(self, route: Route) -> NDArray[np.int64]:
        """The summed-area table of the unused nodes per cell: [r, c] counts rows < r, cols < c."""
        free = self.cell[~route.used_nodes[self.nodes]]
        counts = np.bincount(free, minlength=self.rows * self.cols)
        table = np.zeros((self.rows + 1, self.cols + 1), dtype=np.int64)
        table[1:, 1:] = counts.reshape(self.rows, self.cols).cumsum(0).cumsum(1)
        return table

    def anchors(self, route: Route, table: NDArray[np.int64], radius: float) -> NDArray[np.intp]:
        """The `SEGMENT_BIAS_TOP` route edges whose midpoint's square of half-side
        `WAYPOINT_BALL_FACTOR` × radius holds the most unused nodes, ties to the earlier edge."""
        d = self.data
        mx = (d.node_x[route.nodes[:-1]] + d.node_x[route.nodes[1:]]) / 2
        my = (d.node_y[route.nodes[:-1]] + d.node_y[route.nodes[1:]]) / 2
        half = WAYPOINT_BALL_FACTOR * radius
        c0 = np.clip((mx - half - self.x0) // self.cell_m, 0, self.cols).astype(np.intp)
        c1 = np.clip((mx + half - self.x0) // self.cell_m + 1, 0, self.cols).astype(np.intp)
        r0 = np.clip((my - half - self.y0) // self.cell_m, 0, self.rows).astype(np.intp)
        r1 = np.clip((my + half - self.y0) // self.cell_m + 1, 0, self.rows).astype(np.intp)
        near = table[r1, c1] - table[r0, c1] - table[r1, c0] + table[r0, c0]
        top: NDArray[np.intp] = np.argsort(-near, kind="stable")[:SEGMENT_BIAS_TOP]
        return top


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
    goal: Goal | None = None,
    check_delta: bool = False,
) -> Generator[ProgressEvent, None, WorsenResult]:
    """Make `route` worse, one accepted splice per event, toward `goal` (default: max); returns
    the run's result."""
    start = time.monotonic()
    if goal is None:  # max: the value is the score, and the baseline is not read
        goal = Goal(Target(), data, primary=objective.primary, baseline=Metrics(0.0, 0.0))
    r_min = min(RADIUS_MIN_M, r_max)
    value = goal.value(route.score, route.metrics)
    shrinking = goal.phase(value) == "shrink"
    radius = r_min if shrinking else objective.initial_radius(route)
    best, best_value = route, value
    attempts = accepted = fails = 0
    cycle_accepted = False
    empty_cycles = 0
    # growing toward a target: the smallest detour past the band's top since the last splice
    overshoot: _Overshoot | None = None
    unused = UnusedNodes(data, node_mask)
    table = unused.table(route)
    anchors_for: tuple[int, float] | None = None
    anchors = np.zeros(0, dtype=np.intp)
    events: list[ProgressEvent] = []
    refused: Counter[type[IllegalSplice]] = Counter()
    while True:
        if goal.reached(value):
            stopped: StopReason = "target"
            break
        if attempts >= budget.max_iterations or time.monotonic() - start >= budget.seconds:
            stopped = "budget"
            break
        taken = None
        if fails > CONVERGED_FAILS and radius == r_min:
            if not cycle_accepted and overshoot is not None:
                taken = overshoot  # nothing smaller in a whole cycle: pass the band, then shrink
            else:
                empty_cycles = 0 if cycle_accepted else empty_cycles + 1
                if empty_cycles == CONVERGED_EMPTY_CYCLES:
                    stopped = "converged"
                    break
                radius = r_min if shrinking else objective.initial_radius(route)
                fails, cycle_accepted = 0, False
        attempts += 1
        if taken is not None:
            i, j, found, gain, extra = taken.i, taken.j, taken.path, taken.gain, taken.extra
        else:
            if anchors_for != (accepted, radius):
                anchors, anchors_for = unused.anchors(route, table, radius), (accepted, radius)
            i, j = pick_segment(route, rng, anchors, shrinking=shrinking)
            forbid, banned = route.release(data, i, j)
            extra = _either(banned_edges, objective.banned_edges(data, route, i, j))
            if extra is not None:
                banned |= extra
            detour = find_detour(
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
                refused=refused,
                shrink=goal if shrinking else None,
            )
            gain = 0.0 if detour is None else objective.delta(route, i, j, *detour)
            after = 0.0 if detour is None else goal.candidate(route, i, j, detour[1], gain)
            if (
                detour is None
                or not goal.accepts(value, gain, after)
                or not objective.feasible_window(data, route, i, j, *detour)
            ):
                if (
                    detour is not None
                    and goal.overshoots(value, gain, after)
                    and (overshoot is None or after < overshoot.value)
                    and objective.feasible_window(data, route, i, j, *detour)
                ):
                    overshoot = _Overshoot(i, j, detour, gain, after, extra)
                fails += 1
                if fails % RADIUS_HALVE_EVERY == 0:
                    radius = max(radius / 2, r_min)
                continue
            found = detour

        score, metrics = objective.evaluate(*splice_arrays(route, i, j, *found))
        if check_delta and not math.isclose(gain, score - route.score, rel_tol=1e-9, abs_tol=1e-6):
            raise AssertionError(f"delta {gain} != re-score {score - route.score} at ({i}, {j})")
        route = route.splice(data, i, j, *found, score=score, metrics=metrics, extra_banned=extra)
        table = unused.table(route)
        accepted += 1
        fails = 0
        cycle_accepted = True
        overshoot = None
        value = goal.value(score, metrics)
        shrinking = goal.phase(value) == "shrink"
        radius = r_min if shrinking else min(radius * RADIUS_GROWTH, r_max)
        if goal.better(value, best_value):
            best, best_value = route, value
        event = ProgressEvent(
            iteration=attempts,
            elapsed_s=time.monotonic() - start,
            score=route.score,
            metrics=route.metrics,
            splice=Splice(i, j, *found),
        )
        events.append(event)
        yield event
    elapsed = time.monotonic() - start
    return WorsenResult(
        best, tuple(events), attempts, accepted, stopped, elapsed, refused[TransitionForbidden]
    )


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
    goal: Goal | None = None,
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
        goal=goal,
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
