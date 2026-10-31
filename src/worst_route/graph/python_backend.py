"""`PythonBackend`: the reference `GraphBackend`, pure Python and NumPy over GraphData's arrays.

Readable and exact rather than fast: the M1 engine runs on it, the M2 Numba backend is checked
against it, and the tests check it against networkx. The semantics are `backend.py`'s, and so
are the argument checks and the SCC mask, which every backend shares.

The adjacency (out_indptr, out_edges, edge_u, edge_v) becomes Python lists once, at
construction, and so does the transition table: which nodes have rows, and the allowed turns as
a set of `in_edge * m + out_edge` keys (a row's node is its in-edge's head, so the key names it;
the set is only ever asked for membership, which is what a binary search on the sorted rows
answers); so do the bearings and `TURN_CLASS`, read when a search bans turn classes (and at a
turn edge). Each search combines the node masks into one "may enter" array with a NumPy
operation, then reads it, the banned edges and the weights only for the edges it touches, so a
small bounded search does not pay for the whole graph.
"""

import math
from heapq import heappop, heappush

import numpy as np
from numpy.typing import NDArray

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


class PythonBackend:
    """The reference implementation of `GraphBackend`."""

    name = "python"

    def __init__(self, data: GraphData) -> None:
        self.data = data
        self._check_weights = WeightCheck(data)
        self._out_indptr: list[int] = data.out_indptr.tolist()
        self._out_edges: list[int] = data.out_edges.tolist()
        self._tail: list[int] = data.edge_u.tolist()
        self._head: list[int] = data.edge_v.tolist()
        has_rows = np.zeros(data.n, dtype=np.bool_)
        has_rows[data.trans_node] = True
        self._has_rows: list[bool] = has_rows.tolist()
        keys = data.trans_in.astype(np.int64) * data.m + data.trans_out
        self._turns: frozenset[int] = frozenset(keys.tolist())
        self._bearing_in: list[int] = data.edge_bearing_in.tolist()
        self._bearing_out: list[int] = data.edge_bearing_out.tolist()
        self._turn_class: list[int] = TURN_CLASS.tolist()

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
        """A cheapest walk src → dst whose every turn is allowed, within the masks, or None."""
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
            return _as_path([src], [])
        may_enter, is_banned, weight = enter.item, banned.item, weights.item
        out_indptr, out_edges, head = self._out_indptr, self._out_edges, self._head
        has_rows, turns, m = self._has_rows, self._turns, self.data.m
        bearing_in, bearing_out, turn_class = self._bearing_in, self._bearing_out, self._turn_class

        cost_of: dict[int, float] = {}
        pred: dict[int, int] = {}
        settled: set[int] = set()
        heap: list[tuple[float, int]] = []
        entry_key = entry_edge * m if entry_edge >= 0 and has_rows[src] else -1
        entry_classes = classes if entry_edge >= 0 else 0
        for k in range(out_indptr[src], out_indptr[src + 1]):
            f = out_edges[k]
            w = head[f]
            if w == src or is_banned(f) or not may_enter(w):
                continue
            if entry_key >= 0 and entry_key + f not in turns:
                continue
            if (
                entry_classes
                and turn_class[(bearing_out[f] - bearing_in[entry_edge]) % 360] & classes
            ):
                continue
            c = weight(f)
            if c <= max_cost:
                cost_of[f] = c
                pred[f] = -1
                heappush(heap, (c, f))
        while heap:
            cost, e = heappop(heap)
            if e in settled:
                continue
            settled.add(e)
            v = head[e]
            if v == dst:
                if exit_edge < 0 or self._turn_ok(e, exit_edge, classes):
                    return self._edge_path(src, e, pred)
                continue  # a dead end: any way on would pass dst twice
            key = e * m if has_rows[v] else -1
            arriving = bearing_in[e]
            for k in range(out_indptr[v], out_indptr[v + 1]):
                f = out_edges[k]
                w = head[f]
                if w in (src, v) or f in settled or is_banned(f) or not may_enter(w):
                    continue
                if key >= 0 and key + f not in turns:
                    continue
                if classes and turn_class[(bearing_out[f] - arriving) % 360] & classes:
                    continue
                c = cost + weight(f)
                if c <= max_cost and c < cost_of.get(f, math.inf):
                    cost_of[f] = c
                    pred[f] = e
                    heappush(heap, (c, f))
        return None

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
        settled_edges: list[int] = []
        settled_costs: list[float] = []
        parents: list[int] = []
        if max_cost < 0 or not enter[src]:
            return _as_tree(settled_edges, settled_costs, parents)
        may_enter, is_banned, is_final, weight = enter.item, banned.item, final.item, weights.item
        out_indptr, out_edges, head = self._out_indptr, self._out_edges, self._head
        has_rows, turns, m = self._has_rows, self._turns, self.data.m
        bearing_in, bearing_out, turn_class = self._bearing_in, self._bearing_out, self._turn_class

        cost_of: dict[int, float] = {}
        pred: dict[int, int] = {}
        slot: dict[int, int] = {}  # a settled state's position in the answer
        heap: list[tuple[float, int]] = []
        entry_key = entry_edge * m if entry_edge >= 0 and has_rows[src] else -1
        entry_classes = classes if entry_edge >= 0 else 0
        for k in range(out_indptr[src], out_indptr[src + 1]):
            f = out_edges[k]
            w = head[f]
            if w == src or is_banned(f) or not may_enter(w):
                continue
            if entry_key >= 0 and entry_key + f not in turns:
                continue
            if (
                entry_classes
                and turn_class[(bearing_out[f] - bearing_in[entry_edge]) % 360] & classes
            ):
                continue
            c = weight(f)
            if c <= max_cost:
                cost_of[f] = c
                pred[f] = -1
                heappush(heap, (c, f))
        while heap:
            cost, e = heappop(heap)
            if e in slot:
                continue
            slot[e] = len(settled_edges)
            settled_edges.append(e)
            settled_costs.append(cost)
            parents.append(slot[pred[e]] if pred[e] >= 0 else -1)
            if is_final(e):
                continue
            v = head[e]
            key = e * m if has_rows[v] else -1
            arriving = bearing_in[e]
            for k in range(out_indptr[v], out_indptr[v + 1]):
                f = out_edges[k]
                w = head[f]
                if w in (src, v) or f in slot or is_banned(f) or not may_enter(w):
                    continue
                if key >= 0 and key + f not in turns:
                    continue
                if classes and turn_class[(bearing_out[f] - arriving) % 360] & classes:
                    continue
                c = cost + weight(f)
                if c <= max_cost and c < cost_of.get(f, math.inf):
                    cost_of[f] = c
                    pred[f] = e
                    heappush(heap, (c, f))
        return _as_tree(settled_edges, settled_costs, parents)

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
            return [_as_path([src], [])]
        may_enter, is_banned = enter.item, banned.item
        out_indptr, out_edges, head = self._out_indptr, self._out_edges, self._head
        has_rows, turns, m = self._has_rows, self._turns, self.data.m
        bearing_in, bearing_out, turn_class = self._bearing_in, self._bearing_out, self._turn_class

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
            last = edges[-1] if edges else entry_edge
            if last >= 0 and has_rows[nodes[-1]] and last * m + e not in turns:
                continue
            if (
                last >= 0
                and classes
                and turn_class[(bearing_out[e] - bearing_in[last]) % 360] & classes
            ):
                continue
            if w == dst:
                if exit_edge >= 0 and not self._turn_ok(e, exit_edge, classes):
                    continue
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

    def _turn_ok(self, e_in: int, e_out: int, classes: int) -> bool:
        """Whether the turn e_in → e_out is allowed at head(e_in), and of a class not in
        `classes`."""
        if self._has_rows[self._head[e_in]] and e_in * self.data.m + e_out not in self._turns:
            return False
        delta = (self._bearing_out[e_out] - self._bearing_in[e_in]) % 360
        return not self._turn_class[delta] & classes

    def _path(self, src: int, dst: int, pred: dict[int, int]) -> Path:
        nodes: list[int] = [dst]
        edges: list[int] = []
        while nodes[-1] != src:
            e = pred[nodes[-1]]
            edges.append(e)
            nodes.append(self._tail[e])
        return _as_path(nodes[::-1], edges[::-1])

    def _edge_path(self, src: int, last: int, pred: dict[int, int]) -> Path:
        """The walk of edge states ending in `last` (it may enter a node twice)."""
        edges: list[int] = [last]
        while pred[edges[-1]] >= 0:
            edges.append(pred[edges[-1]])
        edges.reverse()
        nodes = [src, *(self._head[e] for e in edges)]
        return _as_path(nodes, edges)


def _as_path(nodes: list[int], edges: list[int]) -> Path:
    return np.array(nodes, dtype=np.int32), np.array(edges, dtype=np.int32)


def _as_tree(edges: list[int], costs: list[float], parents: list[int]) -> EdgeTree:
    return (
        np.array(edges, dtype=np.int32),
        np.array(costs, dtype=np.float64),
        np.array(parents, dtype=np.int32),
    )
