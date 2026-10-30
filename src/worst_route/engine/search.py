"""The engine's path searches (`docs/reference/engine.md`, **Searches**): which backend call runs.

Every path the engine builds (a baseline leg, a seed leg, a detour) comes from here. On a build
with transition rows, `shortest_path` is the transition-aware `GraphBackend.dijkstra_edges` and
`simple_paths` the DFS with its turn checks, so a path turns only where the tables allow, at
the junctions inside it and, given `entry_edge` and `exit_edge`, at its two ends. On a build
without rows no turn can be forbidden, so they are the node-state `dijkstra` and the plain DFS,
whose paths every backend reproduces byte for byte; the turn edges are then ignored. A backend
that cannot search a table yet raises `NotImplementedError` on a build with rows.
"""

import math

import numpy as np
from numpy.typing import NDArray

from worst_route.graph.backend import GraphBackend, Path

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
) -> Path | None:
    """A cheapest path src → dst within the masks, turning only where the build allows."""
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
