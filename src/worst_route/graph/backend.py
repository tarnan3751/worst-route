"""The `GraphBackend` protocol: the graph searches the engine programs against.

`docs/reference/graph.md`, **Backends**; `docs/PLAN.md` §3.10. M1's surface is four calls; the
alphabet route (M3) added a fifth, `dijkstra_edges_tree`.
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

`dijkstra_edges` is the transition-aware search: its states are edges, so it sees the build's
transition table (`trans_node`, `trans_in`, `trans_out`: a node without rows allows every turn;
a node with rows allows (e, f) iff the row (node, e, f) exists). The turn from e into f is
allowed iff it is allowed at head(e) and its class (`TURN_CLASS`, below) is not among
`banned_turn_classes`, a set of class bits (default 0: every class; straight is never banned)
that the turn constraints pass (`engine/constraints.py`). `entry_edge` (−1: none) is the edge
the path continues, which must end at `src`; `exit_edge` (−1: none) the edge it must hand over
to, which must start at `dst`; neither is driven, banned or costed. The start states are the
out-edges f of `src` whose turn from `entry_edge` is allowed (every out-edge without one). From
a state e, the search relaxes each f of `out_edges(head(e))`, in that order, iff the turn (e, f)
is allowed, f is not banned, head(f) may be entered, and head(f) is neither `src` nor head(e)
(no state returns to the start, no self-loop). A state e with head(e) == `dst` is terminal iff
the turn (e, `exit_edge`) is allowed (always without an exit edge); a state into `dst` that is
not terminal is a dead end, never expanded (any way on would pass `dst` twice). The cost of a
state is the sum of the weights along it; the search never keeps a state costing more than
`max_cost` (inclusive), and a negative `max_cost` finds nothing. Ties: the heap orders entries
by (cost, edge id), out-edges are relaxed in ascending edge id, and only a strictly smaller cost
replaces a state's predecessor. The answer is the path of the first terminal state popped, or
None. **That path is a walk: it may enter a node twice** (node-simplicity is not expressible in
an edge-state search, and a turn table can make the cheapest legal way a loop round a block), so
a caller that needs a simple path checks, as for the tree search (`engine/search.py` does). From
`src` to itself the answer is the one-node path unless both turn edges are given and the turn
(`entry_edge`, `exit_edge`) is not allowed at `src` (then None). On a build with no transition
rows, and with either turn edge, it finds a path whenever `dijkstra` does, at the same cost
(Dijkstra's tree of edge states never repeats a node when every turn is allowed); the path itself
is `dijkstra`'s whenever the two tie rules pick the same one. `ValueError` for a turn edge
outside [−1, m) or not meeting its end, and for `banned_turn_classes` outside [0, 7].

`dijkstra_edges_tree` is the same transition-aware search without a destination: the same start
states, moves, turn checks, `max_cost` (inclusive; negative finds nothing) and tie rule, run
until no state is left. It returns every state it settles, in the order it settles them (by
(cost, edge id)), with its cost and its parent: the position, in these same arrays, of the state
before it on its path (−1 for a start state); a state is settled after its parent, so its path
is read back by following the parents. A state in `final_edges` (default none) is settled but
never expanded: the search may end on it and never goes on from it. A path in the tree may
enter a node twice (a state is an edge, not a node); a caller that needs a simple path checks.
An empty tree when `src` may not be entered. `ValueError` as for `dijkstra_edges` (`entry_edge`
must end at `src`), and for a `final_edges` mask of the wrong dtype or length.

`simple_paths_dfs` lists the simple paths from `src` to `dst` of at most `max_depth` edges, in
ascending edge-id order (lexicographic in their edge ids), and stops after `max_paths`. Every
turn along a path is allowed (by the transition table and `banned_turn_classes`, as for
`dijkstra_edges`), the turn from `entry_edge` into its first edge and from its last edge into
`exit_edge` included (both optional).

`largest_scc_mask` is the node mask of the largest strongly connected component of the graph
restricted to `edge_mask` (default: every edge); ties go to the component scipy labels first.

A `Path` is `(nodes, edges)`, `edges[k]` going from `nodes[k]` to `nodes[k + 1]`; from a node
to itself it is that one node and no edge.

What follows the protocol is what every backend shares, so their errors, their scalars and
the SCC mask are the same whichever backend runs: `check_ends`; `check_turn_edges`;
`as_turn_classes`; `WeightCheck`, one per backend, which remembers the last weights array it
passed; `as_cost`, which makes `max_cost` a Python float, so it compares in float64 on every
backend; `as_limits`, which makes `max_depth` and `max_paths` ints in the int64 range;
`enterable` (the combined "may enter" mask, checking `node_mask` and `forbid_nodes`);
`banned_mask`; `final_mask`; `largest_scc_mask`.

`TURN_CLASS` is the class of a turn by its change of bearing, the rule `engine/turns.py`
describes: from edge e into edge f, `TURN_CLASS[(edge_bearing_out[f] − edge_bearing_in[e]) mod
360]` (the bearings are whole degrees). A class's code is also its bit in a search's set of
banned turn classes: `TURN_LEFT` 1, `TURN_RIGHT` 2, `TURN_U` 4, and `TURN_STRAIGHT` 0, which no
set can ban.
"""

import math
import operator
import weakref
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csr_array
from scipy.sparse.csgraph import connected_components

from worst_route.config import TURN_STRAIGHT_DEG, TURN_UTURN_DEG
from worst_route.graph.data import GraphData

Path = tuple[NDArray[np.int32], NDArray[np.int32]]  # (nodes, edges); len(edges) == len(nodes) - 1
# (edges, costs, parents): the settled states in settle order, their costs, each one's parent's
# position in these arrays (−1 for a start state)
EdgeTree = tuple[NDArray[np.int32], NDArray[np.float64], NDArray[np.int32]]

TURN_STRAIGHT, TURN_LEFT, TURN_RIGHT, TURN_U = 0, 1, 2, 4


def _turn_class_table() -> NDArray[np.uint8]:
    delta = np.arange(360)
    delta = np.where(delta > 180, delta - 360, delta)  # wrapped to (−180, 180]
    table = np.full(360, TURN_U, dtype=np.uint8)
    table[np.abs(delta) <= TURN_STRAIGHT_DEG] = TURN_STRAIGHT
    table[(delta > TURN_STRAIGHT_DEG) & (delta <= TURN_UTURN_DEG)] = TURN_RIGHT
    table[(delta < -TURN_STRAIGHT_DEG) & (delta >= -TURN_UTURN_DEG)] = TURN_LEFT
    table.flags.writeable = False
    return table


TURN_CLASS = _turn_class_table()


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
        ...

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
        ...

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
        ...

    def largest_scc_mask(self, edge_mask: NDArray[np.bool_] | None = None) -> NDArray[np.bool_]:
        """The nodes of the largest strongly connected component over `edge_mask`."""
        ...


def check_ends(data: GraphData, src: int, dst: int) -> None:
    """Raise `ValueError` unless both node ids lie in [0, n)."""
    if not (0 <= src < data.n and 0 <= dst < data.n):
        raise ValueError(f"src, dst: node ids must lie in [0, {data.n})")


def check_turn_edges(data: GraphData, src: int, dst: int, entry_edge: int, exit_edge: int) -> None:
    """Raise `ValueError` unless each turn edge is −1 or an edge meeting its end of the search."""
    m = data.m
    if not -1 <= entry_edge < m or (entry_edge >= 0 and data.edge_v[entry_edge] != src):
        raise ValueError(f"entry_edge: need -1 or an edge in [0, {m}) ending at src")
    if not -1 <= exit_edge < m or (exit_edge >= 0 and data.edge_u[exit_edge] != dst):
        raise ValueError(f"exit_edge: need -1 or an edge in [0, {m}) starting at dst")


def as_turn_classes(banned_turn_classes: int) -> int:
    """`banned_turn_classes` as an int (`operator.index`); `ValueError` unless it is a set of
    the bits `TURN_LEFT`, `TURN_RIGHT` and `TURN_U`."""
    banned = operator.index(banned_turn_classes)
    if not 0 <= banned <= TURN_LEFT | TURN_RIGHT | TURN_U:
        raise ValueError("banned_turn_classes: need a set of turn class bits, in [0, 7]")
    return banned


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


def final_mask(data: GraphData, final_edges: NDArray[np.bool_] | None) -> NDArray[np.bool_]:
    """`final_edges` checked, or no edge final."""
    if final_edges is None:
        return np.zeros(data.m, dtype=np.bool_)
    return _checked("final_edges", final_edges, data.m)


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
