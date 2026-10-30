"""The `GraphBackend` protocol: the graph searches the engine programs against.

`docs/reference/graph.md`, **Backends**; `docs/PLAN.md` §3.10. M1's surface is three calls.
`PythonBackend` is the reference implementation; the CSR + Numba backend (M2) is the
production one and must return the same results on the same inputs.

Masks, exactly. A node may be entered iff `node_mask[v]` (default: every node) and not
`forbid_nodes[v]`; `src` and `dst` obey the same rule, so a caller that forbids the route's
used nodes clears the endpoints of the search from `forbid_nodes`. An edge may be used iff not
`banned_edges[e]` and both its endpoints may be entered. Masks are bool arrays of length n
(nodes) or m (edges); uint8 masks are the M2 kernels' internal business. `weights` are
float64, one per edge, non-negative and not NaN. `PythonBackend` raises `ValueError` for a node
id outside [0, n), a mask or weights of the wrong dtype or length, and NaN or negative weights;
the M2 kernels may leave the weight scan to their callers.

`dijkstra` returns a cheapest path from `src` to `dst`, or None when `dst` cannot be reached
at a cost of at most `max_cost`; the search never visits a node whose tentative cost exceeds
`max_cost`, `src` at cost 0 included, so a negative `max_cost` finds nothing. Ties are
deterministic: the heap orders entries by (cost, node), a node's out-edges are relaxed in
`out_edges` order (ascending edge id), and only a strictly smaller cost replaces a node's
predecessor.

`simple_paths_dfs` lists the simple paths from `src` to `dst` of at most `max_depth` edges, in
ascending edge-id order (lexicographic in their edge ids), and stops after `max_paths`.

`largest_scc_mask` is the node mask of the largest strongly connected component of the graph
restricted to `edge_mask` (default: every edge); ties go to the component scipy labels first.

A `Path` is `(nodes, edges)`, `edges[k]` going from `nodes[k]` to `nodes[k + 1]`; from a node
to itself it is that one node and no edge.
"""

import math
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from worst_route.graph.data import GraphData

Path = tuple[NDArray[np.int32], NDArray[np.int32]]  # (nodes, edges); len(edges) == len(nodes) - 1


class GraphBackend(Protocol):
    """Masked searches over one build's graph."""

    data: GraphData

    def dijkstra(
        self,
        src: int,
        dst: int,
        weights: NDArray[np.float64],
        *,
        node_mask: NDArray[np.bool_] | None = None,
        forbid_nodes: NDArray[np.bool_] | None = None,
        banned_edges: NDArray[np.bool_] | None = None,
        max_cost: float = math.inf,
    ) -> Path | None:
        """A cheapest path src → dst within the masks and `max_cost`, or None."""
        ...

    def simple_paths_dfs(
        self,
        src: int,
        dst: int,
        *,
        node_mask: NDArray[np.bool_] | None = None,
        forbid_nodes: NDArray[np.bool_] | None = None,
        banned_edges: NDArray[np.bool_] | None = None,
        max_depth: int,
        max_paths: int,
    ) -> list[Path]:
        """The first `max_paths` simple paths src → dst of at most `max_depth` edges."""
        ...

    def largest_scc_mask(self, edge_mask: NDArray[np.bool_] | None = None) -> NDArray[np.bool_]:
        """The nodes of the largest strongly connected component over `edge_mask`."""
        ...
