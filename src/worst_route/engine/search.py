"""The engine's path searches (`docs/reference/engine.md`, **Searches**): which backend call runs.

Every path the engine builds (a baseline leg, a seed leg, a detour) comes from here. On a build
with transition rows, `shortest_path` is the transition-aware `GraphBackend.dijkstra_edges` and
`simple_paths` the DFS with its turn checks, so a path turns only where the tables allow, at
the junctions inside it and, given `entry_edge` and `exit_edge`, at its two ends. On a build
without rows no turn can be forbidden, so they are the node-state `dijkstra` and the plain DFS,
whose paths every backend reproduces byte for byte; the turn edges are then ignored.

`shortest_path(..., avoid_twins=True)` is for the paths a route is made of (seed legs, detours):
a path that drives an edge and one of its twins (both carriageways of one road) would be refused
by the splice, so the later edge of the pair that closes first along the path is banned and the
search made again, up to `TWIN_RESEARCH_ROUNDS` times; then there is no path. Deterministic: the
bans depend only on the paths found.
"""

import math

import numpy as np
from numpy.typing import NDArray

from worst_route.config import TWIN_RESEARCH_ROUNDS
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
) -> Path | None:
    """A cheapest path src → dst within the masks, turning only where the build allows (and,
    with `avoid_twins`, never driving an edge and its twin)."""
    banned = banned_edges
    for _ in range(TWIN_RESEARCH_ROUNDS + 1):
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
        )
        if path is None or not avoid_twins:
            return path
        clash = _twin_clash(backend.data, path[1])
        if clash < 0:
            return path
        banned = np.zeros(backend.data.m, dtype=np.bool_) if banned is None else banned.copy()
        banned[clash] = True
    return None


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
) -> Path | None:
    if len(backend.data.trans_node) == 0:
        return backend.dijkstra(
            src,
            dst,
            weights,
            node_mask=node_mask,
            forbid_nodes=forbid_nodes,
            banned_edges=banned_edges,
            max_cost=max_cost,
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
        max_cost=max_cost,
    )


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
) -> list[Path]:
    """The first `max_paths` simple paths src → dst, turning only where the build allows."""
    if len(backend.data.trans_node) == 0:
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
    )
