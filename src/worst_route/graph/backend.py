"""The `GraphBackend` protocol: the graph searches the engine programs against.

`docs/reference/graph.md`, **Backends**; `docs/PLAN.md` §3.10. M1's surface is three calls.
`PythonBackend` is the reference implementation; `CsrBackend`, over Numba kernels, is the
production one and returns the same results on the same inputs of the declared types, byte for
byte.

Masks, exactly. A node may be entered iff `node_mask[v]` (default: every node) and not
`forbid_nodes[v]`; `src` and `dst` obey the same rule, so a caller that forbids the route's
used nodes clears the endpoints of the search from `forbid_nodes`. An edge may be used iff not
`banned_edges[e]` and both its endpoints may be entered. Masks are bool arrays of length n
(nodes) or m (edges); the kernels' uint8 views of them are `CsrBackend`'s internal business.
`weights` are float64, one per edge, non-negative and not NaN. Every backend raises
`ValueError` for a node id outside [0, n), a mask or weights of the wrong dtype or length, and
NaN or negative weights (the shared checks below). The weights' values are scanned once per
array object and backend: a caller that changes a checked array in place is not checked again
(the engine never does; a reweighted search makes a new array).

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

What follows the protocol is what every backend shares, so their errors, their scalars and
the SCC mask are the same whichever backend runs: `check_ends`; `WeightCheck`, one per
backend, which remembers the last weights array it passed; `as_cost`, which makes `max_cost`
a Python float, so it compares in float64 on every backend; `as_limits`, which makes
`max_depth` and `max_paths` ints in the int64 range; `enterable` (the combined "may
enter" mask, checking `node_mask` and `forbid_nodes`); `banned_mask`; `largest_scc_mask`.
"""

import math
import operator
import weakref
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csr_array
from scipy.sparse.csgraph import connected_components

from worst_route.graph.data import GraphData

Path = tuple[NDArray[np.int32], NDArray[np.int32]]  # (nodes, edges); len(edges) == len(nodes) - 1


class GraphBackend(Protocol):
    """Masked searches over one build's graph."""

    name: str  # "python", "csr": what a route records it was searched with
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


def check_ends(data: GraphData, src: int, dst: int) -> None:
    """Raise `ValueError` unless both node ids lie in [0, n)."""
    if not (0 <= src < data.n and 0 <= dst < data.n):
        raise ValueError(f"src, dst: node ids must lie in [0, {data.n})")


class WeightCheck:
    """Raises `ValueError` unless `weights` are float64, one per edge, non-negative, not NaN.

    The dtype and shape are checked on every call; the O(m) scan of the values only for an array
    object other than the last one that passed (held by a weak reference, so a new array that
    reuses a freed one's address is scanned too).
    """

    def __init__(self, data: GraphData) -> None:
        self.m = data.m
        self._passed: weakref.ref[NDArray[np.float64]] | None = None

    def __call__(self, weights: NDArray[np.float64]) -> None:
        if weights.dtype != np.float64 or weights.shape != (self.m,):
            raise ValueError(f"weights: need float64 of shape ({self.m},)")
        if self._passed is not None and self._passed() is weights:
            return
        if not (weights >= 0).all():
            raise ValueError("weights: must be non-negative and not NaN")
        self._passed = weakref.ref(weights)


def as_cost(max_cost: float) -> float:
    """`max_cost` as a Python float: a NumPy float32 would otherwise compare in float32."""
    return float(max_cost)


INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1


def as_limits(max_depth: int, max_paths: int) -> tuple[int, int]:
    """`max_depth` and `max_paths` as ints (`operator.index`: a float, inf included, raises
    `TypeError`), clamped to the int64 range (no search reaches its ends)."""
    depth, paths = (
        min(max(operator.index(x), INT64_MIN), INT64_MAX) for x in (max_depth, max_paths)
    )
    return depth, paths


def enterable(
    data: GraphData, node_mask: NDArray[np.bool_] | None, forbid_nodes: NDArray[np.bool_] | None
) -> NDArray[np.bool_]:
    """The nodes a search may enter: `node_mask` (default every node) and not `forbid_nodes`."""
    n = data.n
    enter = np.ones(n, dtype=np.bool_) if node_mask is None else node_mask
    _checked("node_mask", enter, n)
    if forbid_nodes is not None:
        enter = enter & ~_checked("forbid_nodes", forbid_nodes, n)
    return enter


def banned_mask(data: GraphData, banned_edges: NDArray[np.bool_] | None) -> NDArray[np.bool_]:
    """`banned_edges` checked, or no edge banned."""
    if banned_edges is None:
        return np.zeros(data.m, dtype=np.bool_)
    return _checked("banned_edges", banned_edges, data.m)


def largest_scc_mask(
    data: GraphData, edge_mask: NDArray[np.bool_] | None = None
) -> NDArray[np.bool_]:
    """The nodes of the largest strongly connected component over `edge_mask`."""
    n, u, v = data.n, data.edge_u, data.edge_v
    if edge_mask is not None:
        keep = _checked("edge_mask", edge_mask, data.m)
        u, v = u[keep], v[keep]
    graph = csr_array((np.ones(len(u), dtype=np.int32), (u, v)), shape=(n, n))
    _, labels = connected_components(graph, directed=True, connection="strong")
    largest = np.argmax(np.bincount(labels))
    mask: NDArray[np.bool_] = labels == largest
    return mask


def _checked(name: str, mask: NDArray[np.bool_], size: int) -> NDArray[np.bool_]:
    if mask.dtype != np.bool_ or mask.shape != (size,):
        raise ValueError(f"{name}: need bool of shape ({size},)")
    return mask
