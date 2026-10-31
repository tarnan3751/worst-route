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

`dijkstra_edges` is always the edge-state kernel, and `dijkstra_edges_tree` its kernel without a
destination. `simple_paths_dfs` is the turn kernel on a
build with transition rows, when a turn edge is given or when turn classes are banned;
otherwise every turn is allowed and the plain kernel answers, byte for byte the same. The turn
kernels read the build's bearings and `TURN_CLASS` as they are, zero-copy.
"""

import math

import numpy as np
from numpy.typing import NDArray

from worst_route.graph import kernels
from worst_route.graph.backend import (
    TURN_CLASS,
    EdgeTree,
    Path,
    WeightCheck,
    as_cost,
    as_limits,
    as_turn_classes,
    banned_mask,
    check_ends,
    check_turn_edges,
    enterable,
    final_mask,
    largest_scc_mask,
)
from worst_route.graph.data import GraphData


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
        banned_turn_classes: int = 0,
    ) -> Path | None:
        """A cheapest path src → dst whose every turn is allowed, within the masks, or None."""
        self._check_weights(weights)
        check_ends(self.data, src, dst)
        check_turn_edges(self.data, src, dst, entry_edge, exit_edge)
        classes = as_turn_classes(banned_turn_classes)
        enter = enterable(self.data, node_mask, forbid_nodes)
        banned = banned_mask(self.data, banned_edges)
        max_cost = as_cost(max_cost)
        if max_cost < 0 or not (enter[src] and enter[dst]):
            return None
        if src == dst:
            if (
                entry_edge >= 0
                and exit_edge >= 0
                and not self._turn_ok(entry_edge, exit_edge, classes)
            ):
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
            g.edge_bearing_in,
            g.edge_bearing_out,
            TURN_CLASS,
            classes,
            src,
            dst,
            entry_edge,
            exit_edge,
            max_cost,
        )
        return (nodes, edges) if found else None

    def dijkstra_edges_tree(
        self,
        src: int,
        weights: NDArray[np.float64],
        *,
        entry_edge: int = -1,
        node_mask: NDArray[np.bool_] | None = None,
        forbid_nodes: NDArray[np.bool_] | None = None,
        banned_edges: NDArray[np.bool_] | None = None,
        final_edges: NDArray[np.bool_] | None = None,
        max_cost: float = math.inf,
        banned_turn_classes: int = 0,
    ) -> EdgeTree:
        """Every edge state the transition-aware search from src settles within `max_cost`, in
        settle order, with its cost and its parent's position; `final_edges` are not expanded."""
        self._check_weights(weights)
        check_ends(self.data, src, src)
        check_turn_edges(self.data, src, src, entry_edge, -1)
        classes = as_turn_classes(banned_turn_classes)
        enter = enterable(self.data, node_mask, forbid_nodes)
        banned = banned_mask(self.data, banned_edges)
        final = final_mask(self.data, final_edges)
        max_cost = as_cost(max_cost)
        if max_cost < 0 or not enter[src]:
            return (
                np.zeros(0, dtype=np.int32),
                np.zeros(0, dtype=np.float64),
                np.zeros(0, dtype=np.int32),
            )
        g = self.data
        return kernels.dijkstra_edges_tree(
            g.out_indptr,
            g.out_edges,
            g.edge_v,
            weights,
            enter.view(np.uint8),
            banned.view(np.uint8),
            final.view(np.uint8),
            self._trans_indptr,
            self._trans_key,
            g.edge_bearing_in,
            g.edge_bearing_out,
            TURN_CLASS,
            classes,
            src,
            entry_edge,
            max_cost,
        )

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
        banned_turn_classes: int = 0,
    ) -> list[Path]:
        """The first `max_paths` simple paths src → dst of at most `max_depth` edges."""
        check_ends(self.data, src, dst)
        check_turn_edges(self.data, src, dst, entry_edge, exit_edge)
        classes = as_turn_classes(banned_turn_classes)
        enter = enterable(self.data, node_mask, forbid_nodes)
        banned = banned_mask(self.data, banned_edges)
        max_depth, max_paths = as_limits(max_depth, max_paths)
        if max_paths <= 0 or not (enter[src] and enter[dst]):
            return []
        if src == dst:
            if (
                entry_edge >= 0
                and exit_edge >= 0
                and not self._turn_ok(entry_edge, exit_edge, classes)
            ):
                return []
            return [(np.array([src], dtype=np.int32), np.array([], dtype=np.int32))]
        g = self.data
        if len(g.trans_node) or entry_edge >= 0 or exit_edge >= 0 or classes:
            offsets, nodes, edges = kernels.simple_paths_dfs_turns(
                g.out_indptr,
                g.out_edges,
                g.edge_v,
                enter.view(np.uint8),
                banned.view(np.uint8),
                self._trans_indptr,
                self._trans_key,
                g.edge_bearing_in,
                g.edge_bearing_out,
                TURN_CLASS,
                classes,
                src,
                dst,
                entry_edge,
                exit_edge,
                max_depth,
                max_paths,
            )
        else:
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

    def _turn_ok(self, e_in: int, e_out: int, classes: int) -> bool:
        """Whether the turn e_in → e_out is allowed at head(e_in), and of a class not in
        `classes`."""
        g = self.data
        return kernels.turn_allowed(
            self._trans_indptr, self._trans_key, g.edge_v, e_in, e_out
        ) and kernels.turn_class_allowed(
            g.edge_bearing_in, g.edge_bearing_out, TURN_CLASS, classes, e_in, e_out
        )
