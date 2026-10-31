"""The baseline and the seed route (`docs/PLAN.md` §3.3; `docs/reference/engine.md`, **Seed**).

`baseline_route` is what a sane GPS would drive, the denominator of the inefficiency ratio: the
shortest path between each pair of consecutive stops (on length in the request's masks, nothing
else forbidden), concatenated. Its legs may share nodes; it is not a `Route`. Each leg turns
only where the transition tables allow (`search.shortest_path`), independently of the leg
before it, so the baseline is never longer than the seed; a leg whose cheapest legal path would
pass a node twice is the turn-blind shortest path instead (a lower bound, and the baseline
then exists whenever the seed does), with no search for a node-simple one (`simple_retry`).

`seed_route` is the shortest route through the stops that never repeats, the route the worsen loop
starts from. Its legs are built in order on length, each forbidding the nodes of the other legs
built so far and every other stop's node, and banning their edges' twins; each continues the leg
before it (`entry_edge`) and hands over to the leg after it when that one is built (`exit_edge`), so
every turn, at the vias too, is one the tables allow, and, under a strict turn constraint
(`constraints.Constraints`), none of a class it refuses (a soft one leaves the seed alone). Each
leg is searched by `search.leg_path`'s tree over bans: a walk entering a node twice is searched
again without the edge coming back to it, else without the edge it first entered by, and a leg
found driving an edge and its twin without the later of the two (`avoid_twins`), up to its
searches. A round trip's leg whose other legs drive a single edge avoids that edge's road
(`edge_unit`), so the trip is not one road out and back. When a leg cannot
be built, the penalty re-solve runs up to `SEED_PENALTY_ROUNDS` rounds: the leg's unconstrained path
names the nodes other legs hold that it needs; those legs are rebuilt with `SEED_PENALTY_FACTOR` ×
the weight on every edge touching those nodes (keeping their old path when no other exists), and the
leg is tried again. Under hard mode (`constraints.HardMode`), once the legs are built, while a road
the seed has left comes back (`constraints.returning`), up to `HARD_SEED_ROUNDS` rounds: the leg
where it comes back is searched again with that road banned but for the stretch before, else the leg
of that stretch with the road banned but for the one it comes back on, a kept stretch free to grow
by an edge at either end; the bans stay with their leg for the rest of the build. When that fails,
it is tried once more from the legs as they are, with every ban dropped but the one the last return
asks for. It is a heuristic: it may fail where a legal route exists. A leg that still fails, a leg
that would not move (two stops on one node), a road neither leg can drop, or an assembled route
`check_route` refuses names the leg: `StopUnreachable`, unless a constraint that shapes the seed is
to blame, found with at most two more builds, on failure only (`_unreachable`): under a strict turn
constraint, `InfeasibleTurnConstraint` ("under the strict no-left constraint") when the seed can be
built without it; under hard mode, `InfeasibleHardMode` ("the seed found no way from stop 0 to stop
1 under hard mode") when it can be built without that; under both, the first when hard mode alone
builds it, the second when the strict constraint alone does; else the plain `StopUnreachable`.

`resolve_stops(data, spatial, backend, request, masks)` settles where the stops go before the
seed is built (under the request's strict turn constraint and hard mode, if any),
deterministically for a request and a build, so `worst-route validate` can re-derive them. It
snaps them (`snap.snap_stops`) and, when the seed cannot be built, falls back (`docs/PLAN.md`
§3.2): the failing leg's earlier stop, if it is a via, is re-snapped to its next-nearest node up to
`SNAP_FALLBACK_CANDIDATES` times, trying the whole seed each time; then, with it back on its
node, the later stop the same way (a via or the end); the start never moves. Then
`StopUnreachable` names the leg. So a via trapped where the route can enter but not leave walks
out, and the stops after it stay. It costs a few seed builds.

A round trip with no stop between its ends (start and end on one node) has no legs: its seed is
a loop through a waypoint, the waypoint strategy from the start back to itself on length, with
a radius of `RADIUS_INITIAL_MIN_M` (at most r_max) and the waypoint at least `ROUND_TRIP_MIN_M`
from the start, drawn up to `ROUND_TRIP_SEED_TRIES` times from `rng`; when none closes into a
legal loop (under hard mode, one that never comes back to a road it left), `StopUnreachable` for
the leg (0, 1), or its constraint's error as above. A stop cannot be moved to make a loop, so
there is no fallback.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from numpy.typing import NDArray

from worst_route.config import (
    HARD_SEED_ROUNDS,
    RADIUS_INITIAL_MIN_M,
    ROUND_TRIP_MIN_M,
    ROUND_TRIP_SEED_TRIES,
    SEARCH_BUDGET_FACTOR,
    SEED_PENALTY_FACTOR,
    SEED_PENALTY_ROUNDS,
    SNAP_FALLBACK_CANDIDATES,
)
from worst_route.engine.constraints import NO_CONSTRAINTS, Constraints, returning
from worst_route.engine.detour import waypoint_path
from worst_route.engine.objectives.base import route_delay_s
from worst_route.engine.request import RequestMasks, RouteRequest
from worst_route.engine.route import IllegalSplice, IntArray, Metrics, check_route, twins_of
from worst_route.engine.search import leg_path, shortest_path
from worst_route.engine.snap import (
    StopUnreachable,
    apart_from,
    is_round_trip,
    resnap,
    role_of,
    snap_stops,
)
from worst_route.graph.backend import GraphBackend, Path
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import Snap, SnapError, SpatialIndex

BoolArray = NDArray[np.bool_]


@dataclass(frozen=True, eq=False)
class Baseline:
    """The unconstrained shortest route through the stops, with its length, time and delays."""

    length_m: float
    time_s: float
    nodes: IntArray
    edges: IntArray
    delay_s: float

    @property
    def metrics(self) -> Metrics:
        """Its length, time and delays as `Metrics`."""
        return Metrics(self.length_m, self.time_s, delay_s=self.delay_s)


@dataclass(frozen=True, eq=False)
class SeedRoute:
    """The seed's arrays and the stops it was built for."""

    nodes: IntArray
    edges: IntArray
    stop_idx: tuple[int, ...]
    snaps: tuple[Snap, ...]


# A road the seed comes back to: the seed's edges, the stretch before and the stretch again
# ((start, end) positions), and the legs holding the second's start and the first's end.
_Return = tuple[IntArray, tuple[int, int], tuple[int, int], int, int]


class _LegError(Exception):
    def __init__(self, leg: int) -> None:
        super().__init__(f"leg {leg} cannot be built")
        self.leg = leg


def baseline_route(
    backend: GraphBackend,
    data: GraphData,
    stop_nodes: Sequence[int],
    node_mask: BoolArray,
    banned_edges: BoolArray,
) -> Baseline:
    """The shortest path between each pair of consecutive stops, concatenated."""
    weights = data.edge_length_m.astype(np.float64)
    nodes: list[IntArray] = [np.array(stop_nodes[:1], dtype=np.int32)]
    edges: list[IntArray] = [np.zeros(0, dtype=np.int32)]
    for k, (s, t) in enumerate(pairwise(stop_nodes)):
        path = shortest_path(
            backend,
            s,
            t,
            weights,
            node_mask=node_mask,
            banned_edges=banned_edges,
            simple_retry=False,
        )
        if path is None and len(data.trans_node):  # the legal way passes a node twice
            path = backend.dijkstra(s, t, weights, node_mask=node_mask, banned_edges=banned_edges)
        if path is None:
            raise StopUnreachable((k, k + 1), "nor any route in the request's area")
        nodes.append(path[0][1:])
        edges.append(path[1])
    all_edges, all_nodes = np.concatenate(edges), np.concatenate(nodes)
    return Baseline(
        length_m=math.fsum(data.edge_length_m[all_edges].astype(np.float64).tolist()),
        time_s=math.fsum(data.edge_time_s[all_edges].astype(np.float64).tolist()),
        nodes=all_nodes,
        edges=all_edges,
        delay_s=route_delay_s(data, all_nodes),
    )


def resolve_stops(
    data: GraphData,
    spatial: SpatialIndex,
    backend: GraphBackend,
    request: RouteRequest,
    masks: RequestMasks,
) -> list[Snap]:
    """Where the request's stops go: snapped, then moved until a seed can be built."""
    stops = request.stops
    snaps = snap_stops(spatial, data, stops, masks.active)
    if len(snaps) == 2 and snaps[0].node == snaps[1].node:
        return snaps  # a round trip without legs: nothing to fall back from
    builder = _Builder(backend, data, masks.active, masks.banned, request.constraints)
    try:
        builder.seed(snaps)
        return snaps
    except _LegError as failed:
        leg = failed.leg
    note = request.constraints.note
    why = (
        f"the seed found no way to nearer nodes {note}"  # hard mode's seed is a heuristic
        if request.constraints.hard
        else f"nearer nodes cannot be reached {note or 'without repeating one'}"
    )
    round_trip = is_round_trip(stops)
    moved = [k for k in (leg, leg + 1) if k > 0]  # the earlier via, then the later stop
    for stop in moved:
        original = snaps[stop]
        tried = [original.node]
        for _ in range(SNAP_FALLBACK_CANDIDATES):
            exclude = np.zeros(data.n, dtype=np.bool_)
            exclude[tried + apart_from(snaps, stop, len(snaps), round_trip)] = True
            role = role_of(stop, len(snaps))
            try:
                snaps[stop] = resnap(spatial, stops[stop], role, masks.active, exclude, why)
            except SnapError:
                break
            tried.append(snaps[stop].node)
            try:
                builder.seed(snaps)
                return snaps
            except _LegError:
                continue
        snaps[stop] = original
    names = " and ".join(f"stop {k}" for k in moved)
    each = " each" if len(moved) > 1 else ""
    after = f"after moving {names} up to {SNAP_FALLBACK_CANDIDATES} times{each}"

    def build(constraints: Constraints) -> SeedRoute:
        return _Builder(backend, data, masks.active, masks.banned, constraints).seed(snaps)

    raise _unreachable(request.constraints, (leg, leg + 1), after, build)


def seed_route(
    backend: GraphBackend,
    spatial: SpatialIndex,
    data: GraphData,
    snaps: Sequence[Snap],
    node_mask: BoolArray,
    banned_edges: BoolArray,
    rng: np.random.Generator,
    *,
    r_max: float,
    constraints: Constraints = NO_CONSTRAINTS,
) -> SeedRoute:
    """The shortest route through the resolved stops with node-disjoint legs, or a loop."""
    snaps = list(snaps)
    if len(snaps) == 2 and snaps[0].node == snaps[1].node:
        return _loop(
            backend, spatial, data, snaps, node_mask, banned_edges, rng, r_max, constraints
        )
    try:
        return _Builder(backend, data, node_mask, banned_edges, constraints).seed(snaps)
    except _LegError as failed:
        leg = (failed.leg, failed.leg + 1)

        def build(constraints: Constraints) -> SeedRoute:
            return _Builder(backend, data, node_mask, banned_edges, constraints).seed(snaps)

        raise _unreachable(constraints, leg, "", build) from failed


def _loop(
    backend: GraphBackend,
    spatial: SpatialIndex,
    data: GraphData,
    snaps: list[Snap],
    node_mask: BoolArray,
    banned_edges: BoolArray,
    rng: np.random.Generator,
    r_max: float,
    constraints: Constraints,
) -> SeedRoute:
    """A round trip's seed: a loop from the start through a waypoint and back."""
    found = _loop_through_a_waypoint(
        backend, spatial, data, snaps, node_mask, banned_edges, rng, r_max, constraints
    )
    if found is not None:
        return found
    tries = f"after {ROUND_TRIP_SEED_TRIES} tries at a loop through a waypoint"

    def build(constraints: Constraints) -> SeedRoute:
        args = (backend, spatial, data, snaps, node_mask, banned_edges, rng, r_max)
        loop = _loop_through_a_waypoint(*args, constraints)
        if loop is None:
            raise _LegError(0)
        return loop

    raise _unreachable(constraints, (0, 1), tries, build)


def _loop_through_a_waypoint(
    backend: GraphBackend,
    spatial: SpatialIndex,
    data: GraphData,
    snaps: list[Snap],
    node_mask: BoolArray,
    banned_edges: BoolArray,
    rng: np.random.Generator,
    r_max: float,
    constraints: Constraints,
) -> SeedRoute | None:
    """A loop from the start through a waypoint and back, or None after the tries."""
    start = snaps[0].node
    radius = min(RADIUS_INITIAL_MIN_M, r_max)
    for _ in range(ROUND_TRIP_SEED_TRIES):
        found = waypoint_path(
            backend,
            spatial,
            data,
            start,
            start,
            forbid=np.zeros(data.n, dtype=np.bool_),
            banned=banned_edges,
            node_mask=node_mask,
            radius=radius,
            weights=data.edge_length_m.astype(np.float64),
            max_cost=SEARCH_BUDGET_FACTOR * radius,
            rng=rng,
            min_dist_m=ROUND_TRIP_MIN_M,
            banned_turn_classes=constraints.banned_turn_classes,
        )
        if found is None:
            continue
        nodes, edges = found
        stop_idx = (0, len(nodes) - 1)
        try:
            check_route(data, nodes, edges, stop_idx, banned_edges)
        except IllegalSplice:
            continue
        if constraints.hard and returning(data, edges) is not None:
            continue
        return SeedRoute(nodes, edges, stop_idx, tuple(snaps))
    return None


def _unreachable(
    constraints: Constraints,
    leg: tuple[int, int],
    detail: str,
    build: Callable[[Constraints], SeedRoute],
) -> StopUnreachable:
    """The error for a leg the seed cannot build under `constraints`, blaming the constraints
    that shape the seed (a strict one, hard mode) when `build` succeeds without them: at most
    two more builds, on failure only. Under one of them, its error when the build without it
    succeeds; under both, the strict one's when hard mode alone builds, else hard mode's when
    the strict one alone does; else, and without them, `StopUnreachable`."""

    def builds(without: Constraints) -> bool:
        try:
            build(without)
        except (_LegError, StopUnreachable):
            return False
        return True

    hard, strict = Constraints(hard=True), constraints.model_copy(update={"hard": False})
    if constraints.strict and constraints.hard:
        if builds(hard):
            return strict.unreachable(leg, detail)
        if builds(strict):
            return hard.unreachable(leg, detail)
    elif (constraints.strict or constraints.hard) and builds(NO_CONSTRAINTS):
        return constraints.unreachable(leg, detail)
    return StopUnreachable(leg, detail)


class _Builder:
    """Builds the seed's legs for one set of stop nodes."""

    def __init__(
        self,
        backend: GraphBackend,
        data: GraphData,
        node_mask: BoolArray,
        banned: BoolArray,
        constraints: Constraints,
    ) -> None:
        self.backend = backend
        self.data = data
        self.node_mask = node_mask
        self.banned = banned
        self.classes = constraints.banned_turn_classes
        self.hard = constraints.hard
        self.weights = data.edge_length_m.astype(np.float64)
        self.road_bans: dict[int, BoolArray] = {}  # hard mode's, per leg

    def seed(self, snaps: Sequence[Snap]) -> SeedRoute:
        stops = [s.node for s in snaps]
        legs: list[Path | None] = [None] * (len(stops) - 1)
        self.road_bans = {}
        for leg in range(len(legs)):
            path = self._leg(stops, legs, leg, self.weights)
            if path is None:
                path = self._penalty_resolve(stops, legs, leg)
            if path is None or len(path[1]) == 0:  # no way, or two stops on one node
                raise _LegError(leg)
            legs[leg] = path
        if self.hard:
            self._one_stretch_each(stops, legs)
        built = [p for p in legs if p is not None]
        nodes = np.concatenate([built[0][0], *(p[0][1:] for p in built[1:])])
        edges = np.concatenate([p[1] for p in built])
        stop_idx = tuple(np.concatenate(([0], np.cumsum([len(p[1]) for p in built]))).tolist())
        try:
            check_route(self.data, nodes, edges, stop_idx, self.banned)
        except IllegalSplice as error:
            leg = int(np.searchsorted(stop_idx, error.index, side="right")) - 1
            raise _LegError(min(max(leg, 0), len(legs) - 1)) from error
        return SeedRoute(nodes, edges, stop_idx, tuple(snaps))

    def _leg(
        self,
        stops: list[int],
        legs: list[Path | None],
        leg: int,
        weights: NDArray[np.float64],
        road_bans: BoolArray | None = None,
    ) -> Path | None:
        """Leg `leg` avoiding the other built legs' nodes, every other stop and their twins, and
        the roads hard mode bans it (`road_bans`, else its own), turning legally into it from the
        leg before and out of it into the leg after."""
        others = [p for k, p in enumerate(legs) if p is not None and k != leg]
        forbid = np.zeros(self.data.n, dtype=np.bool_)
        forbid[stops] = True
        banned = self.banned.copy()
        bans = road_bans if road_bans is not None else self.road_bans.get(leg)
        if bans is not None:
            banned |= bans
        for nodes, edges in others:
            forbid[nodes] = True
            banned[edges] = True
            banned[twins_of(self.data, edges)[1]] = True
        driven = [e for _, edges in others for e in edges.tolist()]
        if stops[0] == stops[-1] and len(driven) == 1:  # not one road out and back
            banned[self.data.edge_unit == self.data.edge_unit[driven[0]]] = True
        s, t = stops[leg], stops[leg + 1]
        forbid[[s, t]] = False
        entry, exit_ = _turn_edges(legs, leg)
        return leg_path(
            self.backend,
            s,
            t,
            weights,
            entry_edge=entry,
            exit_edge=exit_,
            node_mask=self.node_mask,
            forbid_nodes=forbid,
            banned_edges=banned,
            avoid_twins=True,
            banned_turn_classes=self.classes,
        )

    def _one_stretch_each(self, stops: list[int], legs: list[Path | None]) -> None:
        """Hard mode: search legs again until no road the seed leaves comes back; when that
        fails, once more from the legs as they are, every road ban dropped but the one the last
        return asks for; else raise `_LegError` for the leg where a road first came back."""
        failed = self._drop_returns(stops, legs)
        if failed is None:
            return
        self.road_bans = {}
        if self._drop_return(stops, legs, failed) and self._drop_returns(stops, legs) is None:
            return
        raise _LegError(failed[3])

    def _drop_returns(self, stops: list[int], legs: list[Path | None]) -> _Return | None:
        """Up to `HARD_SEED_ROUNDS` rounds of `_drop_return`, until no road comes back (None) or
        it cannot drop one (that return)."""
        for round_ in range(HARD_SEED_ROUNDS + 1):
            built = [p for p in legs if p is not None]
            edges = np.concatenate([p[1] for p in built])
            found = returning(self.data, edges)
            if found is None:
                return None
            before, again = found
            ends = np.cumsum([len(p[1]) for p in built])
            later = int(np.searchsorted(ends, again[0], side="right"))
            earlier = int(np.searchsorted(ends, before[1] - 1, side="right"))
            back = (edges, before, again, later, earlier)
            if round_ == HARD_SEED_ROUNDS or not self._drop_return(stops, legs, back):
                return back
        return None  # unreachable: the last round returns

    def _drop_return(self, stops: list[int], legs: list[Path | None], back: _Return) -> bool:
        """Search the leg where a road comes back again with that road banned but for the
        stretch before, else the leg of that stretch with it banned but for the one it comes
        back on, each stretch free to grow by an edge at either end; whether either found a
        way (then kept, with its bans)."""
        edges, before, again, later, earlier = back
        ids = self.data.edge_identity
        road = ids[edges[again[0]]]
        for leg, keep in ((later, before), (earlier, again)):
            kept = edges[keep[0] : keep[1]]
            ends = [self.data.edge_u[kept[0]], self.data.edge_v[kept[-1]]]
            spared = (ids == road) & (
                np.isin(self.data.edge_u, ends) | np.isin(self.data.edge_v, ends)
            )
            spared[kept] = True
            old = self.road_bans.get(leg, np.zeros(self.data.m, dtype=np.bool_))
            bans = old | (ids == road)
            bans[spared] = old[spared]
            path = self._leg(stops, legs, leg, self.weights, bans)
            if path is not None:
                legs[leg], self.road_bans[leg] = path, bans
                return True
        return False

    def _penalty_resolve(self, stops: list[int], legs: list[Path | None], leg: int) -> Path | None:
        """Push the other legs off the nodes this leg needs, then try it again."""
        s, t = stops[leg], stops[leg + 1]
        entry, exit_ = _turn_edges(legs, leg)
        wanted = leg_path(
            self.backend,
            s,
            t,
            self.weights,
            entry_edge=entry,
            exit_edge=exit_,
            node_mask=self.node_mask,
            banned_edges=self.banned,
            banned_turn_classes=self.classes,
        )
        if wanted is None:
            return None
        is_stop = np.zeros(self.data.n, dtype=np.bool_)
        is_stop[stops] = True
        penalty = np.zeros(self.data.n, dtype=np.bool_)
        for _ in range(SEED_PENALTY_ROUNDS):
            held = np.zeros(self.data.n, dtype=np.bool_)
            for k, p in enumerate(legs):
                if p is not None and k != leg:
                    held[p[0]] = True
            needed = wanted[0][1:-1]
            conflict = needed[held[needed] & ~is_stop[needed]]
            if len(conflict) == 0:
                return None
            penalty[conflict] = True
            touched = penalty[self.data.edge_u] | penalty[self.data.edge_v]
            weights = np.where(touched, self.weights * SEED_PENALTY_FACTOR, self.weights)
            for k, p in enumerate(legs):
                if p is not None and np.isin(p[0], conflict).any():
                    legs[k] = self._leg(stops, legs, k, weights) or p
            path = self._leg(stops, legs, leg, self.weights)
            if path is not None:
                return path
        return None


def _turn_edges(legs: list[Path | None], leg: int) -> tuple[int, int]:
    """The edge leg `leg` continues (the last of the leg before) and the one it hands over to
    (the first of the leg after), −1 where that leg is not built."""
    before = legs[leg - 1] if leg > 0 else None
    after = legs[leg + 1] if leg + 1 < len(legs) else None
    entry = int(before[1][-1]) if before is not None and len(before[1]) else -1
    exit_ = int(after[1][0]) if after is not None and len(after[1]) else -1
    return entry, exit_
