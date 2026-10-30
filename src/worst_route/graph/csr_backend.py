"""`CsrBackend`: the production `GraphBackend`, Numba kernels over GraphData's CSR arrays.

The semantics are `backend.py`'s and the results are `PythonBackend`'s, byte for byte: the same
paths, the same ties, the same Nones (`docs/reference/graph.md`, **The CSR backend**). Each call
runs the checks and scalar conversions `backend.py` shares with the reference, in the
reference's order, so a bad argument raises the same error; answers the trivial cases as the
reference does; then
hands the CSR arrays (`out_indptr`, `out_edges`, `edge_u`, `edge_v`), the weights and the masks,
as zero-copy uint8 views, to a kernel in `kernels.py`. `largest_scc_mask` is the reference's
scipy call. Nothing is copied at construction; the transition table is precomputed once into
the two arrays the turn kernels read (`trans_indptr`, `trans_key`, `kernels.py`).

The turn DFS has no kernel yet: `simple_paths_dfs` raises `NotImplementedError` on a build with
transition rows; on a build without rows every turn is allowed, so the DFS's turn edges change
nothing and the plain kernel answers.
"""

import math

import numpy as np
from numpy.typing import NDArray

from worst_route.graph import kernels
from worst_route.graph.backend import (
    Path,
    WeightCheck,
    as_cost,
    as_limits,
    banned_mask,
    check_ends,
    check_turn_edges,
    enterable,
    largest_scc_mask,
)
from worst_route.graph.data import GraphData

NO_TURN_KERNEL = "the csr backend cannot search turn tables yet; use --backend python"


class CsrBackend:
    """The Numba implementation of `GraphBackend`."""

    name = "csr"

    def __init__(self, data: GraphData) -> None:
        self.data = data
        self._check_weights = WeightCheck(data)
        # node v's transition rows are trans_indptr[v]:trans_indptr[v + 1] (sorted by node), and
        # each row's key in_edge * m + out_edge ascends within them
        self._trans_indptr = np.searchsorted(data.trans_node, np.arange(data.n + 1)).astype(
            np.int64
        )
        self._trans_key = data.trans_in.astype(np.int64) * data.m + data.trans_out
        self._trans_indptr.setflags(write=False)
        self._trans_key.setflags(write=False)

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
        self._check_weights(weights)
        check_ends(self.data, src, dst)
        enter = enterable(self.data, node_mask, forbid_nodes)
        banned = banned_mask(self.data, banned_edges)
        max_cost = as_cost(max_cost)
        if max_cost < 0 or not (enter[src] and enter[dst]):
            return None
        g = self.data
        found, nodes, edges = kernels.dijkstra_masked(
            g.out_indptr,
            g.out_edges,
            g.edge_u,
            g.edge_v,
            weights,
            enter.view(np.uint8),
            banned.view(np.uint8),
            src,
            dst,
            max_cost,
        )
        return (nodes, edges) if found else None

    def dijkstra_edges(
        self,
        src: int,
        dst: int,
        weights: NDArray[np.float64],
        *,
        entry_edge: int = -1,
        exit_edge: int = -1,
        node_mask: NDArray[np.bool_] | None = None,
        forbid_nodes: NDArray[np.bool_] | None = None,
        banned_edges: NDArray[np.bool_] | None = None,
        max_cost: float = math.inf,
    ) -> Path | None:
        """A cheapest path src → dst whose every turn is allowed, within the masks, or None."""
        self._check_weights(weights)
        check_ends(self.data, src, dst)
        check_turn_edges(self.data, src, dst, entry_edge, exit_edge)
        enter = enterable(self.data, node_mask, forbid_nodes)
        banned = banned_mask(self.data, banned_edges)
        max_cost = as_cost(max_cost)
        if max_cost < 0 or not (enter[src] and enter[dst]):
            return None
        if src == dst:
            if entry_edge >= 0 and exit_edge >= 0 and not self._turn_ok(entry_edge, exit_edge):
                return None
            return np.array([src], dtype=np.int32), np.array([], dtype=np.int32)
        g = self.data
        found, nodes, edges = kernels.dijkstra_edges_masked(
            g.out_indptr,
            g.out_edges,
            g.edge_u,
            g.edge_v,
            weights,
            enter.view(np.uint8),
            banned.view(np.uint8),
            self._trans_indptr,
            self._trans_key,
            src,
            dst,
            entry_edge,
            exit_edge,
            max_cost,
        )
        return (nodes, edges) if found else None

    def simple_paths_dfs(
        self,
        src: int,
        dst: int,
        *,
        entry_edge: int = -1,
        exit_edge: int = -1,
        node_mask: NDArray[np.bool_] | None = None,
        forbid_nodes: NDArray[np.bool_] | None = None,
        banned_edges: NDArray[np.bool_] | None = None,
        max_depth: int,
        max_paths: int,
    ) -> list[Path]:
        """The first `max_paths` simple paths src → dst of at most `max_depth` edges."""
        check_ends(self.data, src, dst)
        check_turn_edges(self.data, src, dst, entry_edge, exit_edge)
        if len(self.data.trans_node):
            raise NotImplementedError(NO_TURN_KERNEL)
        enter = enterable(self.data, node_mask, forbid_nodes)
        banned = banned_mask(self.data, banned_edges)
        max_depth, max_paths = as_limits(max_depth, max_paths)
        if max_paths <= 0 or not (enter[src] and enter[dst]):
            return []
        if src == dst:
            return [(np.array([src], dtype=np.int32), np.array([], dtype=np.int32))]
        g = self.data
        offsets, nodes, edges = kernels.simple_paths_dfs(
            g.out_indptr,
            g.out_edges,
            g.edge_v,
            enter.view(np.uint8),
            banned.view(np.uint8),
            src,
            dst,
            max_depth,
            max_paths,
        )
        ends = offsets.tolist()
        return [
            (nodes[a + p : b + p + 1], edges[a:b])
            for p, (a, b) in enumerate(zip(ends[:-1], ends[1:], strict=True))
        ]

    def largest_scc_mask(self, edge_mask: NDArray[np.bool_] | None = None) -> NDArray[np.bool_]:
        """The nodes of the largest strongly connected component over `edge_mask`."""
        return largest_scc_mask(self.data, edge_mask)

    def _turn_ok(self, e_in: int, e_out: int) -> bool:
        """Whether the turn e_in → e_out is allowed at head(e_in)."""
        return kernels.turn_allowed(
            self._trans_indptr, self._trans_key, self.data.edge_v, e_in, e_out
        )
