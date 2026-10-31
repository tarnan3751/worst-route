"""The engine's path searches (`docs/reference/engine.md`, **Searches**): which backend call runs.

Every path the engine builds (a baseline leg, a seed leg, a detour) comes from here: a detour's
and a baseline leg's from `shortest_path`, a seed leg's from `leg_path`. On a build
with transition rows, or when the search refuses turn classes (`banned_turn_classes`, a strict
turn constraint's), `shortest_path` is the transition-aware `GraphBackend.dijkstra_edges` and
`simple_paths` the DFS with its turn checks, so a path turns only where the tables and the
classes allow, at the junctions inside it and, given `entry_edge` and `exit_edge`, at its two
ends. Otherwise no turn can be forbidden, so they are the node-state `dijkstra` and the plain
DFS, whose paths every backend reproduces byte for byte; the turn edges are then ignored.

The transition-aware search answers the cheapest legal walk, which may enter a node twice (a
table can make the cheapest legal way a loop round a block), and such a walk is no path. So the
search is made again with one more edge banned, the one by which the walk first comes back to a
node it has passed (the node stays passable, by its other ways in), the bans adding up, up to
`SIMPLE_RETRY_ROUNDS` times; then there is no path. A heuristic: a node-simple path that drives a
banned edge is missed. `simple_retry=False` (the baseline's) answers no path at once.
Deterministic: the bans depend only on the walks found.

`avoided_turn_classes` (a soft turn constraint's) are refused too on a first search; when it
finds nothing, the search is made again without them, so a path avoids those turns when it
can and takes them when it must.

`shortest_path(..., avoid_twins=True)` is for the paths a route is made of (seed legs, detours):
a path that drives an edge and one of its twins (both carriageways of one road) would be refused
by the splice, so the later edge of the pair that closes first along the path is banned and the
search made again, up to `TWIN_RESEARCH_ROUNDS` times; then there is no path. Deterministic: the
bans depend only on the paths found.

`leg_path` is a seed leg's search: the same searches, made again by a depth-first tree over bans
instead of a chain. A walk that enters a node X a second time has two children, the walk banned
the edge by which it comes back to X (the chain's one step, tried first) and the walk banned the
edge by which it first entered X; a node-simple path enters X once, so it survives in one of the
two, and the tree, given searches enough, finds a node-simple legal path whenever one exists. A
path that drives an edge and its twin has one child, the later edge banned, as above. The tree
makes at most 1 + `LEG_SEARCHES` searches, and one more per `LEG_SEARCH_KM` of the straight line
between the leg's ends: a cross-country leg passes many junctions whose cheapest legal walk
loops, one a search. Where the chain finds a path without meeting a twin, the tree's first branch
is the chain and finds the same one. Deterministic: the bans depend only on the walks found.
"""

import math

import numpy as np
from numpy.typing import NDArray

from worst_route.config import (
    LEG_SEARCH_KM,
    LEG_SEARCHES,
    SIMPLE_RETRY_ROUNDS,
    TWIN_RESEARCH_ROUNDS,
)
from worst_route.engine.route import IntArray, twins_of
from worst_route.graph.backend import GraphBackend, Path
from worst_route.graph.data import GraphData

BoolArray = NDArray[np.bool_]


def shortest_path(
    backend: GraphBackend,
    src: int,
    dst: int,
    weights: NDArray[np.float64],
    *,
    entry_edge: int = -1,
    exit_edge: int = -1,
    node_mask: BoolArray | None = None,
    forbid_nodes: BoolArray | None = None,
    banned_edges: BoolArray | None = None,
    max_cost: float = math.inf,
    avoid_twins: bool = False,
    banned_turn_classes: int = 0,
    avoided_turn_classes: int = 0,
    simple_retry: bool = True,
) -> Path | None:
    """A cheapest path src → dst within the masks, turning only where the build and the banned
    turn classes allow, and without the avoided ones when it can (and, with `avoid_twins`, never
    driving an edge and its twin); a node-simple one searched for again when the cheapest legal
    walk enters a node twice, unless not `simple_retry`."""
    banned = banned_edges
    for _ in range(TWIN_RESEARCH_ROUNDS + 1):
        path = None
        for classes in _first_without(banned_turn_classes, avoided_turn_classes):
            path = _search(
                backend,
                src,
                dst,
                weights,
                entry_edge=entry_edge,
                exit_edge=exit_edge,
                node_mask=node_mask,
                forbid_nodes=forbid_nodes,
                banned_edges=banned,
                max_cost=max_cost,
                banned_turn_classes=classes,
                simple_retry=simple_retry,
            )
            if path is not None:
                break
        if path is None or not avoid_twins:
            return path
        clash = _twin_clash(backend.data, path[1])
        if clash < 0:
            return path
        banned = np.zeros(backend.data.m, dtype=np.bool_) if banned is None else banned.copy()
        banned[clash] = True
    return None


def leg_path(
    backend: GraphBackend,
    src: int,
    dst: int,
    weights: NDArray[np.float64],
    *,
    entry_edge: int = -1,
    exit_edge: int = -1,
    node_mask: BoolArray | None = None,
    forbid_nodes: BoolArray | None = None,
    banned_edges: BoolArray | None = None,
    avoid_twins: bool = False,
    banned_turn_classes: int = 0,
) -> Path | None:
    """A node-simple path src → dst within the masks, turning only where the build and the banned
    turn classes allow (and, with `avoid_twins`, never driving an edge and its twin): the cheapest
    walk under the first bans of the tree the module docstring describes that is one, or None."""
    data = backend.data
    apart_m = math.hypot(data.node_x[src] - data.node_x[dst], data.node_y[src] - data.node_y[dst])
    searches = 1 + LEG_SEARCHES + int(apart_m / 1000 / LEG_SEARCH_KM)
    base = np.zeros(data.m, dtype=np.bool_) if banned_edges is None else banned_edges
    work = base.copy()
    stack: list[list[int]] = [[]]  # each pending search's bans beyond `base`
    while stack and searches > 0:
        searches -= 1
        bans = stack.pop()
        work[bans] = True
        walk = _walk(
            backend,
            src,
            dst,
            weights,
            entry_edge=entry_edge,
            exit_edge=exit_edge,
            node_mask=node_mask,
            forbid_nodes=forbid_nodes,
            banned_edges=work,
            banned_turn_classes=banned_turn_classes,
        )
        work[bans] = False  # an edge a walk drove was not banned before
        if walk is None:
            continue
        nodes, edges = walk
        again = _reentry(nodes)
        if again >= 0:  # the edge it first entered by, then the one it comes back by, on top
            first = int(np.flatnonzero(nodes[:again] == nodes[again])[0])
            assert first > 0, "a walk never comes back to its start (backend.py)"
            stack += [[*bans, int(edges[first - 1])], [*bans, int(edges[again - 1])]]
            continue
        clash = _twin_clash(data, edges) if avoid_twins else -1
        if clash >= 0:
            stack.append([*bans, clash])
            continue
        return walk
    return None


def _first_without(banned: int, avoided: int) -> tuple[int, ...]:
    """The turn classes to refuse, in the order to try: without the avoided ones first."""
    return (banned | avoided, banned) if avoided else (banned,)


def _twin_clash(data: GraphData, edges: IntArray) -> int:
    """The later edge of the first twin pair to close along `edges`, or −1 if none."""
    owner, twins = twins_of(data, edges)
    position = {e: k for k, e in enumerate(edges.tolist())}
    later = [
        max(k, position[t])
        for k, t in zip(owner.tolist(), twins.tolist(), strict=True)
        if t in position
    ]
    return int(edges[min(later)]) if later else -1


def _search(
    backend: GraphBackend,
    src: int,
    dst: int,
    weights: NDArray[np.float64],
    *,
    entry_edge: int,
    exit_edge: int,
    node_mask: BoolArray | None,
    forbid_nodes: BoolArray | None,
    banned_edges: BoolArray | None,
    max_cost: float,
    banned_turn_classes: int,
    simple_retry: bool,
) -> Path | None:
    if len(backend.data.trans_node) == 0 and banned_turn_classes == 0:
        return backend.dijkstra(
            src,
            dst,
            weights,
            node_mask=node_mask,
            forbid_nodes=forbid_nodes,
            banned_edges=banned_edges,
            max_cost=max_cost,
        )
    banned = banned_edges
    rounds = SIMPLE_RETRY_ROUNDS if simple_retry else 0
    for round_ in range(rounds + 1):
        walk = backend.dijkstra_edges(
            src,
            dst,
            weights,
            entry_edge=entry_edge,
            exit_edge=exit_edge,
            node_mask=node_mask,
            forbid_nodes=forbid_nodes,
            banned_edges=banned,
            max_cost=max_cost,
            banned_turn_classes=banned_turn_classes,
        )
        if walk is None:
            return None
        again = _reentry(walk[0])
        if again < 0:
            return walk
        if round_ < rounds:  # the edge entering that node the second time, banned
            banned = np.zeros(backend.data.m, dtype=np.bool_) if banned is None else banned.copy()
            banned[walk[1][again - 1]] = True
    return None


def _walk(
    backend: GraphBackend,
    src: int,
    dst: int,
    weights: NDArray[np.float64],
    *,
    entry_edge: int,
    exit_edge: int,
    node_mask: BoolArray | None,
    forbid_nodes: BoolArray | None,
    banned_edges: BoolArray | None,
    banned_turn_classes: int,
) -> Path | None:
    """One search: the node-state one on a build without rows and with no class banned, where
    no turn can be forbidden (the turn edges ignored), else the transition-aware one."""
    if len(backend.data.trans_node) == 0 and banned_turn_classes == 0:
        return backend.dijkstra(
            src,
            dst,
            weights,
            node_mask=node_mask,
            forbid_nodes=forbid_nodes,
            banned_edges=banned_edges,
        )
    return backend.dijkstra_edges(
        src,
        dst,
        weights,
        entry_edge=entry_edge,
        exit_edge=exit_edge,
        node_mask=node_mask,
        forbid_nodes=forbid_nodes,
        banned_edges=banned_edges,
        banned_turn_classes=banned_turn_classes,
    )


def _reentry(nodes: IntArray) -> int:
    """The position along a walk's nodes where it first enters a node a second time, or −1."""
    order = np.sort(nodes)
    if not (order[1:] == order[:-1]).any():
        return -1
    seen: set[int] = set()
    for k, v in enumerate(nodes.tolist()):
        if v in seen:
            return k
        seen.add(v)
    return -1  # unreachable: a repeated node is seen again


def simple_paths(
    backend: GraphBackend,
    src: int,
    dst: int,
    *,
    entry_edge: int = -1,
    exit_edge: int = -1,
    node_mask: BoolArray | None = None,
    forbid_nodes: BoolArray | None = None,
    banned_edges: BoolArray | None = None,
    max_depth: int,
    max_paths: int,
    banned_turn_classes: int = 0,
    avoided_turn_classes: int = 0,
) -> list[Path]:
    """The first `max_paths` simple paths src → dst, turning only where the build and the
    banned turn classes allow; without the avoided ones, unless no path avoids them."""
    paths: list[Path] = []
    for classes in _first_without(banned_turn_classes, avoided_turn_classes):
        paths = _dfs(
            backend,
            src,
            dst,
            entry_edge=entry_edge,
            exit_edge=exit_edge,
            node_mask=node_mask,
            forbid_nodes=forbid_nodes,
            banned_edges=banned_edges,
            max_depth=max_depth,
            max_paths=max_paths,
            banned_turn_classes=classes,
        )
        if paths:
            break
    return paths


def _dfs(
    backend: GraphBackend,
    src: int,
    dst: int,
    *,
    entry_edge: int,
    exit_edge: int,
    node_mask: BoolArray | None,
    forbid_nodes: BoolArray | None,
    banned_edges: BoolArray | None,
    max_depth: int,
    max_paths: int,
    banned_turn_classes: int,
) -> list[Path]:
    if len(backend.data.trans_node) == 0 and banned_turn_classes == 0:
        return backend.simple_paths_dfs(
            src,
            dst,
            node_mask=node_mask,
            forbid_nodes=forbid_nodes,
            banned_edges=banned_edges,
            max_depth=max_depth,
            max_paths=max_paths,
        )
    return backend.simple_paths_dfs(
        src,
        dst,
        entry_edge=entry_edge,
        exit_edge=exit_edge,
        node_mask=node_mask,
        forbid_nodes=forbid_nodes,
        banned_edges=banned_edges,
        max_depth=max_depth,
        max_paths=max_paths,
        banned_turn_classes=banned_turn_classes,
    )
