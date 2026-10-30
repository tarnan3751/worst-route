"""`PythonBackend`: the reference `GraphBackend`, pure Python and NumPy over GraphData's arrays.

Readable and exact rather than fast: the M1 engine runs on it, the M2 Numba backend is checked
against it, and the tests check it against networkx. The semantics are `backend.py`'s, and so
are the argument checks and the SCC mask, which every backend shares.

The adjacency (out_indptr, out_edges, edge_u, edge_v) becomes Python lists once, at
construction. Each search combines the node masks into one "may enter" array with a NumPy
operation, then reads it, the banned edges and the weights only for the edges it touches, so a
small bounded search does not pay for the whole graph.
"""

import math
from heapq import heappop, heappush

import numpy as np
from numpy.typing import NDArray

from worst_route.graph.backend import (
    Path,
    WeightCheck,
    as_cost,
    as_limits,
    banned_mask,
    check_ends,
    enterable,
    largest_scc_mask,
)
from worst_route.graph.data import GraphData


class PythonBackend:
    """The reference implementation of `GraphBackend`."""

    def __init__(self, data: GraphData) -> None:
        self.data = data
        self._check_weights = WeightCheck(data)
        self._out_indptr: list[int] = data.out_indptr.tolist()
        self._out_edges: list[int] = data.out_edges.tolist()
        self._tail: list[int] = data.edge_u.tolist()
        self._head: list[int] = data.edge_v.tolist()

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
        may_enter, is_banned, weight = enter.item, banned.item, weights.item
        out_indptr, out_edges, head = self._out_indptr, self._out_edges, self._head

        cost_of = {src: 0.0}
        pred: dict[int, int] = {}
        settled: set[int] = set()
        heap = [(0.0, src)]
        while heap:
            cost, v = heappop(heap)
            if v in settled:
                continue
            if v == dst:
                return self._path(src, dst, pred)
            settled.add(v)
            for k in range(out_indptr[v], out_indptr[v + 1]):
                e = out_edges[k]
                w = head[e]
                if w in settled or is_banned(e) or not may_enter(w):
                    continue
                c = cost + weight(e)
                if c <= max_cost and c < cost_of.get(w, math.inf):
                    cost_of[w] = c
                    pred[w] = e
                    heappush(heap, (c, w))
        return None

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
        check_ends(self.data, src, dst)
        enter = enterable(self.data, node_mask, forbid_nodes)
        banned = banned_mask(self.data, banned_edges)
        max_depth, max_paths = as_limits(max_depth, max_paths)
        if max_paths <= 0 or not (enter[src] and enter[dst]):
            return []
        if src == dst:
            return [_as_path([src], [])]
        may_enter, is_banned = enter.item, banned.item
        out_indptr, out_edges, head = self._out_indptr, self._out_edges, self._head

        paths: list[Path] = []
        nodes: list[int] = [src]
        edges: list[int] = []
        on_path = {src}
        stack = [iter(range(out_indptr[src], out_indptr[src + 1]))]
        while stack:
            k = next(stack[-1], None)
            if k is None:  # every out-edge of nodes[-1] tried: step back
                stack.pop()
                if stack:
                    on_path.discard(nodes.pop())
                    edges.pop()
                continue
            e = out_edges[k]
            w = head[e]
            if w in on_path or is_banned(e) or not may_enter(w) or len(edges) >= max_depth:
                continue
            if w == dst:
                paths.append(_as_path([*nodes, w], [*edges, e]))
                if len(paths) == max_paths:
                    break
            elif len(edges) + 1 < max_depth:
                nodes.append(w)
                edges.append(e)
                on_path.add(w)
                stack.append(iter(range(out_indptr[w], out_indptr[w + 1])))
        return paths

    def largest_scc_mask(self, edge_mask: NDArray[np.bool_] | None = None) -> NDArray[np.bool_]:
        """The nodes of the largest strongly connected component over `edge_mask`."""
        return largest_scc_mask(self.data, edge_mask)

    def _path(self, src: int, dst: int, pred: dict[int, int]) -> Path:
        nodes: list[int] = [dst]
        edges: list[int] = []
        while nodes[-1] != src:
            e = pred[nodes[-1]]
            edges.append(e)
            nodes.append(self._tail[e])
        return _as_path(nodes[::-1], edges[::-1])


def _as_path(nodes: list[int], edges: list[int]) -> Path:
    return np.array(nodes, dtype=np.int32), np.array(edges, dtype=np.int32)
