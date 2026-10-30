"""Detours: replacements for a window of a route (`docs/PLAN.md` §3.5; `docs/reference/engine.md`).

`find_detour(backend, spatial, data, route, i, j, ...)` looks for a path a → b, a = nodes[i] and
b = nodes[j], inside the masks the worsen loop passes (`forbid`: the nodes the route still
uses; `banned`: the bans left once the window is released, with the request's and the
objective's), and returns it only if it differs from the removed run and `route.check_splice`
accepts it; a refused candidate is tallied in `refused` by its kind when the caller passes a
counter. The strategy is drawn from `rng` with `STRATEGY_WEIGHTS`, but local exhaustive only
when a and b lie less than `LOCAL_EXHAUSTIVE_MAX_M` apart (otherwise waypoint 2/3, random
weight 1/3); `strategy` forces one (tests, the round-trip seed).

- `waypoint_path`: a waypoint w drawn from the nodes the search may enter within
  `WAYPOINT_BALL_FACTOR × radius` of the midpoint of a and b (the objective's pool when it has
  one; never a or b; at least `min_dist_m` from the midpoint), with probability ∝
  exp(−distance / radius); then a → w (not through b), continuing `entry_edge`, and w → b
  (not through a → w, nor any twin of its edges, nor, when a = b, back along one of its roads),
  continuing a → w's last edge and handing over to `exit_edge`, each bounded by `max_cost` and
  searched again without a twin it drives (`search.shortest_path`, `avoid_twins`).
- `random_weight_path`: a → b on a copy of the search weights in which every edge leaving a
  node within `WAYPOINT_BALL_FACTOR × radius` of the midpoint is multiplied by a factor drawn
  from `RANDOM_WEIGHT_RANGE` (in ascending edge id; the edges farther out keep their weight,
  so the draws do not grow with the graph), bounded by the same budget in scaled cost, from
  `entry_edge` to `exit_edge`, and searched again without a twin it drives.
- `local_exhaustive_paths`: the simple paths a → b of at most `LOCAL_EXHAUSTIVE_MAX_DEPTH`
  edges, up to `LOCAL_EXHAUSTIVE_MAX_PATHS`, inside a ball around the midpoint of radius
  `LOCAL_EXHAUSTIVE_BALL_FACTOR × dist(a, b)` capped at `LOCAL_EXHAUSTIVE_BALL_MAX_M`, from
  `entry_edge` to `exit_edge`, after a Dijkstra probe that skips the enumeration when b cannot
  be reached at all (turns ignored: a legal path exists only if a turn-blind one does).
  `find_detour` ranks them by `objective.delta` (stable, so ties keep the search's order) and
  returns the first the splice accepts.

Every search goes through `search.py`, so on a build with transition rows each turn of a
detour, its two boundary junctions included (`entry_edge` = edges[i − 1] into its first edge,
its last edge into `exit_edge` = edges[j]), is one the tables allow. What the searches cannot
avoid makes a candidate fail the splice check: two twins inside one local-exhaustive path (the
next one ranked is tried). A refused candidate is a failed attempt, nothing worse. The
randomness is `rng`'s alone.
"""

from collections import Counter
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from worst_route.config import (
    LOCAL_EXHAUSTIVE_BALL_FACTOR,
    LOCAL_EXHAUSTIVE_BALL_MAX_M,
    LOCAL_EXHAUSTIVE_MAX_DEPTH,
    LOCAL_EXHAUSTIVE_MAX_M,
    LOCAL_EXHAUSTIVE_MAX_PATHS,
    RANDOM_WEIGHT_RANGE,
    STRATEGY_WEIGHTS,
    WAYPOINT_BALL_FACTOR,
)
from worst_route.engine.objectives.base import Objective
from worst_route.engine.route import IllegalSplice, IntArray, Route, twins_of
from worst_route.engine.search import shortest_path, simple_paths
from worst_route.graph.backend import GraphBackend, Path
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex

BoolArray = NDArray[np.bool_]
FloatArray = NDArray[np.float64]
Strategy = Literal["waypoint", "random_weight", "local_exhaustive"]
STRATEGIES: tuple[Strategy, ...] = ("waypoint", "random_weight", "local_exhaustive")


def pick_strategy(distance_m: float, rng: np.random.Generator) -> Strategy:
    """A strategy by `STRATEGY_WEIGHTS`, local exhaustive only for close ends."""
    weights = list(STRATEGY_WEIGHTS)
    if distance_m >= LOCAL_EXHAUSTIVE_MAX_M:
        weights[2] = 0.0
    draw = rng.random() * sum(weights)
    for strategy, upto in zip(STRATEGIES, np.cumsum(weights).tolist(), strict=True):
        if draw < upto:
            return strategy
    return "waypoint"  # draw == the total, which rounding alone can produce


def find_detour(
    backend: GraphBackend,
    spatial: SpatialIndex,
    data: GraphData,
    route: Route,
    i: int,
    j: int,
    *,
    forbid: BoolArray,
    banned: BoolArray,
    node_mask: BoolArray,
    radius: float,
    objective: Objective,
    rng: np.random.Generator,
    entry_edge: int,
    exit_edge: int,
    strategy: Strategy | None = None,
    refused: Counter[type[IllegalSplice]] | None = None,
) -> Path | None:
    """A new detour for nodes[i..j] that the splice accepts, or None."""
    a, b = int(route.nodes[i]), int(route.nodes[j])
    chosen = strategy or pick_strategy(_distance(data, a, b), rng)
    weights = objective.search_weights(data)
    candidates: list[Path] = []
    if chosen == "waypoint":
        found = waypoint_path(
            backend,
            spatial,
            data,
            a,
            b,
            forbid=forbid,
            banned=banned,
            node_mask=node_mask,
            radius=radius,
            weights=weights,
            max_cost=objective.search_budget(radius),
            rng=rng,
            pool=objective.waypoint_pool(data, route),
            entry_edge=entry_edge,
            exit_edge=exit_edge,
        )
        candidates = [] if found is None else [found]
    elif chosen == "random_weight":
        found = random_weight_path(
            backend,
            spatial,
            data,
            a,
            b,
            forbid=forbid,
            banned=banned,
            node_mask=node_mask,
            weights=weights,
            radius=radius,
            max_cost=objective.search_budget(radius),
            rng=rng,
            entry_edge=entry_edge,
            exit_edge=exit_edge,
        )
        candidates = [] if found is None else [found]
    else:
        paths = local_exhaustive_paths(
            backend,
            spatial,
            data,
            a,
            b,
            forbid=forbid,
            banned=banned,
            node_mask=node_mask,
            weights=weights,
            entry_edge=entry_edge,
            exit_edge=exit_edge,
        )
        gains = np.array([objective.delta(route, i, j, *p) for p in paths], dtype=np.float64)
        candidates = [paths[k] for k in np.argsort(-gains, kind="stable").tolist()]
    removed = route.edges[i:j]
    for nodes, edges in candidates:
        if np.array_equal(edges, removed):
            continue
        try:
            route.check_splice(data, i, j, nodes, edges, extra_banned=banned)
        except IllegalSplice as error:
            if refused is not None:
                refused[type(error)] += 1
            continue
        return nodes, edges
    return None


def waypoint_path(
    backend: GraphBackend,
    spatial: SpatialIndex,
    data: GraphData,
    a: int,
    b: int,
    *,
    forbid: BoolArray,
    banned: BoolArray,
    node_mask: BoolArray,
    radius: float,
    weights: FloatArray,
    max_cost: float,
    rng: np.random.Generator,
    pool: IntArray | None = None,
    min_dist_m: float = 0.0,
    entry_edge: int = -1,
    exit_edge: int = -1,
) -> Path | None:
    """a → w → b through a waypoint w drawn near the midpoint of a and b, or None."""
    middle = (_xy(data, a) + _xy(data, b)) / 2
    take = spatial.bubble_mask(middle[None, :], WAYPOINT_BALL_FACTOR * radius)
    take &= node_mask & ~forbid
    if pool is not None:
        listed = np.zeros(data.n, dtype=np.bool_)
        listed[pool] = True
        take &= listed
    take[[a, b]] = False
    candidates = np.flatnonzero(take)
    distance = np.hypot(data.node_x[candidates] - middle[0], data.node_y[candidates] - middle[1])
    far = distance >= min_dist_m
    candidates, distance = candidates[far], distance[far]
    if len(candidates) == 0:
        return None
    odds = np.exp(-distance / radius)
    w = int(rng.choice(candidates, p=odds / odds.sum()))

    first_forbid = forbid.copy()
    if a != b:
        first_forbid[b] = True
    first = shortest_path(
        backend,
        a,
        w,
        weights,
        entry_edge=entry_edge,
        node_mask=node_mask,
        forbid_nodes=first_forbid,
        banned_edges=banned,
        max_cost=max_cost,
        avoid_twins=True,
    )
    if first is None:
        return None
    second_forbid = forbid.copy()
    second_forbid[first[0]] = True
    second_forbid[[w, b]] = False
    second_banned = banned.copy()
    second_banned[twins_of(data, first[1])[1]] = True
    if a == b:  # a loop: not back along a road of the way out, a two-edge round trip
        second_banned[np.isin(data.edge_unit, data.edge_unit[first[1]])] = True
    second = shortest_path(
        backend,
        w,
        b,
        weights,
        entry_edge=int(first[1][-1]),
        exit_edge=exit_edge,
        node_mask=node_mask,
        forbid_nodes=second_forbid,
        banned_edges=second_banned,
        max_cost=max_cost,
        avoid_twins=True,
    )
    if second is None:
        return None
    return np.concatenate((first[0], second[0][1:])), np.concatenate((first[1], second[1]))


def random_weight_path(
    backend: GraphBackend,
    spatial: SpatialIndex,
    data: GraphData,
    a: int,
    b: int,
    *,
    forbid: BoolArray,
    banned: BoolArray,
    node_mask: BoolArray,
    weights: FloatArray,
    radius: float,
    max_cost: float,
    rng: np.random.Generator,
    entry_edge: int = -1,
    exit_edge: int = -1,
) -> Path | None:
    """a → b with the weights near the window scaled at random, or None."""
    middle = (_xy(data, a) + _xy(data, b)) / 2
    near = spatial.bubble_mask(middle[None, :], WAYPOINT_BALL_FACTOR * radius)
    drawn = np.flatnonzero(near[data.edge_u])
    low, high = RANDOM_WEIGHT_RANGE
    scaled = weights.copy()
    scaled[drawn] *= rng.uniform(low, high, size=len(drawn))
    return shortest_path(
        backend,
        a,
        b,
        scaled,
        entry_edge=entry_edge,
        exit_edge=exit_edge,
        node_mask=node_mask,
        forbid_nodes=forbid,
        banned_edges=banned,
        max_cost=max_cost,
        avoid_twins=True,
    )


def local_exhaustive_paths(
    backend: GraphBackend,
    spatial: SpatialIndex,
    data: GraphData,
    a: int,
    b: int,
    *,
    forbid: BoolArray,
    banned: BoolArray,
    node_mask: BoolArray,
    weights: FloatArray,
    entry_edge: int = -1,
    exit_edge: int = -1,
) -> list[Path]:
    """The simple paths a → b in a small ball around their midpoint."""
    middle = (_xy(data, a) + _xy(data, b)) / 2
    reach = min(LOCAL_EXHAUSTIVE_BALL_FACTOR * _distance(data, a, b), LOCAL_EXHAUSTIVE_BALL_MAX_M)
    ball = spatial.bubble_mask(middle[None, :], reach) & node_mask
    if (
        backend.dijkstra(a, b, weights, node_mask=ball, forbid_nodes=forbid, banned_edges=banned)
        is None
    ):
        return []
    return simple_paths(
        backend,
        a,
        b,
        entry_edge=entry_edge,
        exit_edge=exit_edge,
        node_mask=ball,
        forbid_nodes=forbid,
        banned_edges=banned,
        max_depth=LOCAL_EXHAUSTIVE_MAX_DEPTH,
        max_paths=LOCAL_EXHAUSTIVE_MAX_PATHS,
    )


def _xy(data: GraphData, v: int) -> FloatArray:
    return np.array([data.node_x[v], data.node_y[v]], dtype=np.float64)


def _distance(data: GraphData, a: int, b: int) -> float:
    return float(np.hypot(*(_xy(data, a) - _xy(data, b))))
