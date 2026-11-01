"""The certified worst: the provably longest legal route in a small area (`docs/PLAN.md` §3.12;
`docs/reference/engine.md`, **Certified worst**).

The second driver beside the alphabet route's that is not the worsen loop: it shares the
request's masks, `Route` and the validator with planning (`plan.plan_route` runs it for a
request with `certified`), never the loop, the kernels or the splice. OR-Tools is imported inside
the functions that solve, never at the package's import, so a worker that plans any other route
does not load it.

**The area** (`certified_area(data, masks, stop_nodes)`): the edges every search may use (both
ends `active`, not `banned`, no self-loop) that lie on some walk from a stop to the next one
inside the masks, the other stops blocked, the walk never passing its end before it ends nor
coming back to its start (legality otherwise ignored), and the nodes such walks pass. A leg
whose walk cannot reach its end leaves the stops unjoined (`None`). Its node and edge counts are
what the caps judge: at most `CERTIFIED_MAX_NODES` and `CERTIFIED_MAX_EDGES`, else
`AreaTooLarge` (a `ValueError`, `field` `"leash"`, naming the counts and the caps).

**The leash** (`certified_request(data, spatial, request)`): a request's own leash is used as
given. With the automatic leash it is the widest one, in `CERTIFIED_LEASH_STEP_M` steps from
`CERTIFIED_LEASH_MIN_M` to the automatic leash (at most `LEASH_AUTO_MAX_KM`), whose area holds at
most `CERTIFIED_MAX_NODES` nodes and `CERTIFIED_AUTO_MAX_EDGES` edges, by bisection over request
copies with that leash (their masks and snapped stops, as planning would make them; a leash that
does not join the stops counts as small enough: it is too narrow). When that leash does not join
them, the narrowest one that does is taken if its area is within the hard caps, else
`AreaTooLarge`; when not even the automatic leash joins them, it is kept and the stops' own error
comes from resolving them. The request comes back with the leash as a number, so the route's
request records it and its masks can be made again without a search.

**The model** (CP-SAT): a Boolean per area edge (parallel edges each their own), a self-loop
("skipped") literal on every area node but the stops, and the arc from the end back to the start
fixed true, under `add_circuit`: a single circuit through the nodes not skipped, so a simple path
from the start to the end through every stop. Each twin pair within the area is at most one, and
at a node with transition rows each pair of an area edge in and an area edge out that the rows do
not list is at most one (a visited node has one edge in and one out, so no line graph is needed).
With stops between the ends, each area node has an integer position, the start's 0, each edge
taken giving its head its tail's plus one, and the stops' positions rising in the request's order.
It maximises the length in decimetres (each edge's rounded to the nearest), so "longest" is to the
decimetre on each road. The seed route is the hint, complete.

**The solve** (`certified_route(...)`): `CERTIFIED_WORKERS` threads, `CERTIFIED_SUBSOLVERS` added
to CP-SAT's own, the request's seed (mod 2³¹) as its seed, and the time left of the request's
budget. Each better solution, in a solver thread, is read, chained from the start, scored and made
a route by `Route.new` under the request's bans (a solution `check_route` refused would be a bug,
which stops the search and is raised), and handed on as an `Incumbent`: a `ProgressEvent` whose
splice replaces the whole route, with the stops' positions and the solver's bound then (none when
it lies below the solution's own value). Between them the bound, at most every
`CERTIFIED_BOUND_EVERY_S`, goes to `on_bound(elapsed_s, bound_m, gap)`. An exception from either
hook (a cancelled job, a closed pipe) stops the search and is raised once the solve returns. The
result is the last incumbent, or the seed when none was better, with a `Certificate`: `optimal`
when the solver proved it, else `feasible`; `bound_m`, the longest any legal route in the area can
be (the route's length when proven), and `gap`, the fraction by which it may exceed the route
(`(bound - found) / found` in the model's decimetres; 0 when proven), both null when the solver
ended without a solution of its own (`UNKNOWN`: the time limit came before it took in the hint, the
route then the seed) or with a bound below its route's; the area's counts, the leash, the solve's
time and the threads. A solve that ends `INFEASIBLE` or `MODEL_INVALID` is a bug (the hint is a
legal route), raised, and so is a proof whose value is not the last route's. The run's `attempts`
are the solutions the solver reported, its `accepted` the incumbents, and it stops `optimal` or on
its `budget`.

Reproducibility: a proven route's length is fixed by the build and the request; the route among
equally long ones, and a best found under the time limit, are not, as a budgeted loop's are not.
"""

import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csr_array
from scipy.sparse.csgraph import breadth_first_order

from worst_route.config import (
    CERTIFIED_AUTO_MAX_EDGES,
    CERTIFIED_BOUND_EVERY_S,
    CERTIFIED_LEASH_MIN_M,
    CERTIFIED_LEASH_STEP_M,
    CERTIFIED_MAX_EDGES,
    CERTIFIED_MAX_NODES,
    CERTIFIED_SUBSOLVERS,
    CERTIFIED_WORKERS,
    LEASH_AUTO_MAX_KM,
)
from worst_route.engine.objectives.base import Objective
from worst_route.engine.request import RequestMasks, RouteRequest, request_masks
from worst_route.engine.route import IntArray, Route, Splice, twins_of
from worst_route.engine.snap import snap_stops
from worst_route.engine.worsen import Budget, ProgressEvent, StopReason, WorsenResult
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SnapError, SpatialIndex

if TYPE_CHECKING:
    from ortools.sat.python import cp_model

BoolArray = NDArray[np.bool_]
CertifiedStatus = Literal["optimal", "feasible"]
# `certified_route`'s `on_bound(elapsed_s, bound_m, gap)`.
OnBound = Callable[[float, float, float], None]


@dataclass(frozen=True, eq=False)
class Area:
    """The nodes on some walk from each stop to the next inside a request's masks, and the
    edges such walks can drive (ascending ids)."""

    nodes: BoolArray
    edges: IntArray

    @property
    def node_count(self) -> int:
        return int(self.nodes.sum())

    @property
    def edge_count(self) -> int:
        return len(self.edges)


class AreaTooLarge(ValueError):  # noqa: N818 (the API error it becomes)
    """A request's area holds more than a certificate allows: its counts, the leash it was
    found at, and the caps; `field` is `"leash"`, the setting to change. Its message is the app's
    ("This area is too big to prove. It holds 1,132 junctions and 3,865 roads; a proof allows 300
    and 1,000. Shorten the corridor or bring the stops closer."), which `plan --certified` prints
    too: the leash a corridor, the nodes junctions and the edges roads."""

    field = "leash"

    def __init__(self, nodes: int, edges: int, leash_m: float, *, narrowest: bool = False) -> None:
        self.nodes, self.edges, self.leash_m = nodes, edges, float(leash_m)
        self.max_nodes, self.max_edges = CERTIFIED_MAX_NODES, CERTIFIED_MAX_EDGES
        leash = f"{leash_m:.0f} m" if leash_m < 1000 else f"{leash_m / 1000:g} km"
        where = (
            f"Even the narrowest corridor that joins the stops, {leash}, holds"
            if narrowest
            else "It holds"
        )
        remedy = (
            "Bring the stops closer."
            if narrowest
            else "Shorten the corridor or bring the stops closer."
        )
        super().__init__(
            f"This area is too big to prove. {where} {nodes:,} junctions and {edges:,} roads; a "
            f"proof allows {self.max_nodes:,} and {self.max_edges:,}. {remedy}"
        )


@dataclass(frozen=True)
class Certificate:
    """What a certified route's run says (`run.certified`): whether the solver proved it, the
    bound on the longest legal route in the area and the gap (None before the solver has
    one), the area's counts, the leash, the solve's seconds and its threads."""

    status: CertifiedStatus
    bound_m: float | None
    gap: float | None
    nodes: int
    edges: int
    leash_m: float
    solve_s: float
    workers: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, eq=False)
class Incumbent(ProgressEvent):
    """A better route the solver found, whole (its splice replaces the route before it), its
    stops' positions, and the solver's bound and gap when it found it (None when that bound lies
    below the route's own value)."""

    stop_idx: tuple[int, ...]
    bound_m: float | None
    gap: float | None


def certified_area(data: GraphData, masks: RequestMasks, stop_nodes: Sequence[int]) -> Area | None:
    """The request's area for these stops; None when a leg's walks cannot reach its end."""
    u, v = data.edge_u, data.edge_v
    candidate = np.flatnonzero(masks.active[u] & masks.active[v] & ~masks.banned & (u != v))
    if len(candidate) == 0:
        return None
    ids, local = np.unique(np.concatenate((u[candidate], v[candidate])), return_inverse=True)
    tail, head = local[: len(candidate)], local[len(candidate) :]
    stops = np.searchsorted(ids, np.asarray(stop_nodes))
    if np.any(stops >= len(ids)) or np.any(ids[np.minimum(stops, len(ids) - 1)] != stop_nodes):
        return None  # a stop with no edge in the area
    is_stop = np.zeros(len(ids), dtype=np.bool_)
    is_stop[stops] = True
    nodes = np.zeros(len(ids), dtype=np.bool_)
    keep = np.zeros(len(candidate), dtype=np.bool_)
    for s, t in pairwise(stops.tolist()):
        blocked = is_stop.copy()
        blocked[[s, t]] = False
        usable = ~blocked[tail] & ~blocked[head] & (tail != t) & (head != s)
        forward = _reached(len(ids), s, tail[usable], head[usable])
        if not forward[t]:
            return None
        backward = _reached(len(ids), t, head[usable], tail[usable])
        nodes |= forward & backward
        keep |= usable & forward[tail] & backward[head]
    area = np.zeros(data.n, dtype=np.bool_)
    area[ids[nodes]] = True
    return Area(area, candidate[keep].astype(np.int32))


def _reached(n: int, source: int, tail: NDArray[np.intp], head: NDArray[np.intp]) -> BoolArray:
    """The nodes of [0, n) reached from `source` along the edges tail → head."""
    graph = csr_array((np.ones(len(tail)), (tail, head)), shape=(n, n))
    reached = np.zeros(n, dtype=np.bool_)
    reached[breadth_first_order(graph, source, directed=True, return_predecessors=False)] = True
    return reached


def too_large(area: Area, leash_m: float) -> AreaTooLarge | None:
    """The refusal of an area over the caps, or None."""
    if area.node_count <= CERTIFIED_MAX_NODES and area.edge_count <= CERTIFIED_MAX_EDGES:
        return None
    return AreaTooLarge(area.node_count, area.edge_count, leash_m)


def certified_request(
    data: GraphData, spatial: SpatialIndex, request: RouteRequest
) -> RouteRequest:
    """`request` with the leash its certificate uses; see the module docstring."""
    if request.leash_km != "auto":
        return request
    step = CERTIFIED_LEASH_STEP_M
    low = CERTIFIED_LEASH_MIN_M // step
    widest = min(request_masks(spatial, data, request).leash_m, LEASH_AUTO_MAX_KM * 1000)
    high = max(low, int(widest // step))
    known: dict[int, tuple[int, int] | None] = {}

    def counts(k: int) -> tuple[int, int] | None:  # the area's at k steps, None: not joined
        if k not in known:
            candidate = _leashed(request, k)
            masks = request_masks(spatial, data, candidate)
            try:
                snaps = snap_stops(spatial, data, candidate.stops, masks.active)
            except SnapError:
                known[k] = None
            else:
                area = certified_area(data, masks, [s.node for s in snaps])
                known[k] = None if area is None else (area.node_count, area.edge_count)
        return known[k]

    def small(k: int) -> bool:
        c = counts(k)
        return c is None or (c[0] <= CERTIFIED_MAX_NODES and c[1] <= CERTIFIED_AUTO_MAX_EDGES)

    def joined(k: int) -> bool:
        return counts(k) is not None

    found = _last(small, low, high)
    if found is not None and joined(found):
        return _leashed(request, found)
    if not joined(high):  # no leash joins them: resolving the stops says why
        return _leashed(request, high)
    narrowest = _first(joined, low if found is None else found, high)
    nodes, edges = counts(narrowest) or (0, 0)
    if nodes <= CERTIFIED_MAX_NODES and edges <= CERTIFIED_MAX_EDGES:
        return _leashed(request, narrowest)
    raise AreaTooLarge(nodes, edges, narrowest * step, narrowest=True)


def _leashed(request: RouteRequest, steps: int) -> RouteRequest:
    return request.model_copy(update={"leash_km": steps * CERTIFIED_LEASH_STEP_M / 1000})


def _last(holds: Callable[[int], bool], low: int, high: int) -> int | None:
    """The largest k in [low, high] for which `holds` (true up to some k, false after), or None."""
    if holds(high):
        return high
    if not holds(low):
        return None
    while high - low > 1:
        mid = (low + high) // 2
        if holds(mid):
            low = mid
        else:
            high = mid
    return low


def _first(holds: Callable[[int], bool], low: int, high: int) -> int:
    """The smallest k in [low, high] for which `holds` (false up to some k, true after; true at
    `high`)."""
    if holds(low):
        return low
    while high - low > 1:
        mid = (low + high) // 2
        if holds(mid):
            high = mid
        else:
            low = mid
    return high


@dataclass(frozen=True, eq=False)
class _Model:
    model: "cp_model.CpModel"
    lits: list["cp_model.IntVar"]
    lengths: NDArray[np.int64]  # each area edge's length in decimetres


def _model(data: GraphData, area: Area, route: Route) -> _Model:
    """The CP-SAT model of `area` for the route's stops, hinted with `route`."""
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    edges = area.edges
    tails, heads = data.edge_u[edges].tolist(), data.edge_v[edges].tolist()
    nodes = np.flatnonzero(area.nodes).tolist()
    local = {g: k for k, g in enumerate(nodes)}
    stops = [int(route.nodes[k]) for k in route.stop_idx]
    start, end = stops[0], stops[-1]
    lits = [model.new_bool_var(f"e{e}") for e in edges.tolist()]
    closing = model.new_bool_var("closing")
    model.add(closing == 1)
    arcs = [(local[a], local[b], lit) for a, b, lit in zip(tails, heads, lits, strict=True)]
    arcs.append((local[end], local[start], closing))
    skips = {g: model.new_bool_var(f"skip{g}") for g in nodes if g not in stops}
    arcs += [(local[g], local[g], lit) for g, lit in skips.items()]
    model.add_circuit(arcs)
    index = {e: k for k, e in enumerate(edges.tolist())}
    owner, twins = twins_of(data, edges)
    for k, twin in zip(owner.tolist(), twins.tolist(), strict=True):
        other = index.get(twin)
        if other is not None and k < other:
            model.add_at_most_one(lits[k], lits[other])
    into: dict[int, list[int]] = {}
    out_of: dict[int, list[int]] = {}
    for k, (a, b) in enumerate(zip(tails, heads, strict=True)):
        out_of.setdefault(a, []).append(k)
        into.setdefault(b, []).append(k)
    lo = np.searchsorted(data.trans_node, nodes, side="left")
    hi = np.searchsorted(data.trans_node, nodes, side="right")
    for g, first, last in zip(nodes, lo.tolist(), hi.tolist(), strict=True):
        if first == last:
            continue
        ins, outs = data.trans_in[first:last].tolist(), data.trans_out[first:last].tolist()
        allowed = set(zip(ins, outs, strict=True))
        for a in into.get(g, []):
            for b in out_of.get(g, []):
                if (int(edges[a]), int(edges[b])) not in allowed:
                    model.add_at_most_one(lits[a], lits[b])
    visited = {int(g): k for k, g in enumerate(route.nodes.tolist())}
    if len(stops) > 2:  # the stops between the ends, in order
        position = {g: model.new_int_var(0, len(nodes) - 1, f"p{g}") for g in nodes}
        model.add(position[start] == 0)
        for a, b, lit in zip(tails, heads, lits, strict=True):
            model.add(position[b] == position[a] + 1).only_enforce_if(lit)
        for a, b in pairwise(stops):
            model.add(position[a] + 1 <= position[b])
        for g, p in position.items():
            model.add_hint(p, visited.get(g, 0))
    lengths = np.rint(data.edge_length_m[edges].astype(np.float64) * 10).astype(np.int64)
    model.maximize(cp_model.LinearExpr.weighted_sum(lits, lengths.tolist()))
    taken = set(route.edges.tolist())
    for e, lit in zip(edges.tolist(), lits, strict=True):
        model.add_hint(lit, e in taken)
    for g, lit in skips.items():
        model.add_hint(lit, g not in visited)
    model.add_hint(closing, True)
    return _Model(model, lits, lengths)


def certified_route(
    data: GraphData,
    route: Route,
    area: Area,
    objective: Objective,
    budget: Budget,
    *,
    leash_m: float,
    banned_edges: BoolArray | None = None,
    on_event: Callable[[ProgressEvent], None] | None = None,
    on_bound: OnBound | None = None,
) -> tuple[WorsenResult, Certificate]:
    """The longest legal route in `area` from `route` (the seed, scored by `objective`) within
    `budget.seconds`, its run and its certificate; see the module docstring."""
    from ortools.sat.python import cp_model

    started = time.monotonic()
    built = _model(data, area, route)
    edges, lits = area.edges, built.lits
    start = int(route.nodes[0])
    stops = [int(route.nodes[k]) for k in route.stop_idx]
    seed_value = int(built.lengths[np.isin(edges, route.edges)].sum())
    lock = threading.Lock()
    state: dict[str, Any] = {
        "best": route,
        "value": seed_value,
        "error": None,
        "bound_at": -math.inf,
    }
    events: list[ProgressEvent] = []
    counted = {"solutions": 0}

    def bound_of(bound: float) -> tuple[float, float]:
        """The bound in metres on the longest route, from the best route's length, and the gap."""
        best: Route = state["best"]
        value: int = state["value"]
        gap = (bound - value) / value if value else 0.0
        return best.metrics.length_m + (bound - value) / 10, gap

    def failed(error: BaseException, stop: Callable[[], None]) -> None:
        if state["error"] is None:
            state["error"] = error
        stop()

    solver = cp_model.CpSolver()

    class Incumbents(cp_model.CpSolverSolutionCallback):
        def on_solution_callback(self) -> None:
            with lock:
                if state["error"] is not None:
                    return
                try:
                    counted["solutions"] += 1
                    value = round(self.objective_value)
                    if value <= state["value"]:
                        return
                    chosen = np.fromiter(
                        (self.boolean_value(lit) for lit in lits), np.bool_, len(lits)
                    )
                    nodes, path = _chain(data, edges[chosen], start)
                    stop_idx = tuple(int(np.flatnonzero(nodes == s)[0]) for s in stops)
                    score, metrics = objective.evaluate(nodes, path)
                    best = Route.new(
                        data,
                        nodes,
                        path,
                        stop_idx,
                        score=score,
                        metrics=metrics,
                        seed=route.seed,
                        extra_banned=banned_edges,
                    )
                    before: Route = state["best"]
                    state["best"], state["value"] = best, value
                    bound = self.best_objective_bound
                    # a bound below the solution's own value bounds nothing: none on this event
                    bound_m, gap = bound_of(bound) if bound >= value else (None, None)
                    elapsed = time.monotonic() - started
                    splice = Splice(0, len(before.edges), best.nodes, best.edges)
                    solutions = counted["solutions"]
                    event = Incumbent(
                        solutions, elapsed, score, metrics, splice, stop_idx, bound_m, gap
                    )
                    events.append(event)
                    state["bound_at"] = elapsed
                    if on_event is not None:
                        on_event(event)
                except BaseException as error:  # noqa: BLE001 (raised again once the solve returns)
                    failed(error, self.stop_search)

    def bounded(bound: float) -> None:
        with lock:
            elapsed = time.monotonic() - started
            if state["error"] is not None or elapsed - state["bound_at"] < CERTIFIED_BOUND_EVERY_S:
                return
            if bound < state["value"]:  # not a bound on the route the solver holds
                return
            state["bound_at"] = elapsed
            try:
                if on_bound is not None:
                    on_bound(elapsed, *bound_of(bound))
            except BaseException as error:  # noqa: BLE001 (raised again once the solve returns)
                failed(error, solver.stop_search)

    params = solver.parameters
    params.num_workers = CERTIFIED_WORKERS
    params.extra_subsolvers.extend(CERTIFIED_SUBSOLVERS)
    params.random_seed = route.seed % 2**31
    params.max_time_in_seconds = budget.seconds
    params.log_search_progress = False
    solver.best_bound_callback = bounded
    status = solver.solve(built.model, Incumbents())
    if state["error"] is not None:
        raise state["error"]
    best: Route = state["best"]
    value: int = state["value"]
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE, cp_model.UNKNOWN):
        # the hint is a legal route, so the model has a solution: a bug in it
        raise RuntimeError(f"CP-SAT ended {solver.status_name(status)} on a model with a solution")
    proven = status == cp_model.OPTIMAL
    if proven and round(solver.objective_value) != value:
        message = f"CP-SAT proved {round(solver.objective_value)} dm, its last route is {value} dm"
        raise RuntimeError(message)
    bound_m: float | None = None
    gap: float | None = None
    if proven:
        bound_m, gap = best.metrics.length_m, 0.0
    elif status == cp_model.FEASIBLE:  # UNKNOWN: no solution yet, so no bound to trust
        bound = float(solver.best_objective_bound)
        if math.isfinite(bound) and bound >= value > 0:
            bound_m, gap = bound_of(bound)
    elapsed = time.monotonic() - started
    stopped: StopReason = "optimal" if proven else "budget"
    run = WorsenResult(best, tuple(events), counted["solutions"], len(events), stopped, elapsed, 0)
    certificate = Certificate(
        "optimal" if proven else "feasible",
        bound_m,
        gap,
        area.node_count,
        area.edge_count,
        leash_m,
        float(solver.wall_time),
        CERTIFIED_WORKERS,
    )
    return run, certificate


def _chain(data: GraphData, edges: IntArray, start: int) -> tuple[IntArray, IntArray]:
    """The route's nodes and edges in order from `start` along `edges`, each taken once."""
    out = {int(data.edge_u[e]): int(e) for e in edges}
    nodes, path = [start], []
    while nodes[-1] in out:
        e = out.pop(nodes[-1])
        path.append(e)
        nodes.append(int(data.edge_v[e]))
    if out:
        raise ValueError(f"the solution's edges {sorted(out.values())} are off its path")
    return np.array(nodes, dtype=np.int32), np.array(path, dtype=np.int32)
