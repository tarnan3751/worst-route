"""The Numba kernels of `CsrBackend` (`docs/reference/graph.md`, **The CSR backend**).

Plain arrays and scalars in, plain arrays out; no Python objects inside. The semantics are
`backend.py`'s, tie rule included: the heap orders entries by (cost, node), a node's out-edges
are relaxed in `out_edges` order (ascending edge id), only a strictly smaller cost replaces a
predecessor, and `max_cost` is inclusive. `CsrBackend` checks the arguments and answers the
trivial cases (a negative `max_cost`, an end that may not be entered) before it calls a
kernel, as `PythonBackend` does before it searches.

The transition-aware search (`dijkstra_edges_masked`) is the same over edge states: its heap
orders entries by (cost, edge id). It and the turn DFS (`simple_paths_dfs_turns`) read the
transition table as `CsrBackend` precomputes it once per build: `trans_indptr`, each node's run
of rows (rows sorted by node), and `trans_key`, each row's `in_edge * m + out_edge`, ascending
within a node's run; `turn_allowed` answers a turn by a binary search in the run of the turn's
node (a node without rows allows every turn).

Masks arrive as uint8: zero-copy views of the bool masks (`mask.view(np.uint8)`). Each kernel
is compiled once, eagerly, for one signature whose arrays are read-only of any layout, so
memory-mapped builds, read-only cached weights and fresh writeable masks share one compiled
specialization; `cache=True` keeps it in this package's `__pycache__`, so only the first
process after a change to this file pays the compile. `NUMBA_DISABLE_JIT=1` runs the kernels
as the plain Python below (slow, but the test suite passes that way, which helps debugging).
Every search allocates its own work arrays: O(n) per call, as the masks already are. The
edge-state search keeps one uint8 state per edge and leaves its costs and predecessors
uninitialised until a state is reached, so a small search does not pay to fill O(m) floats. Its
heap holds at most m entries: a state's cost is its predecessor's plus its own weight, and
states pop in non-decreasing cost, so with non-negative weights a state's first push is its only
one. The heap still starts at 64 entries and doubles when full: a small search allocates little,
and memory stays safe if a weights array changed in place after its cached check
(`backend.py`, `WeightCheck`) holds a negative weight, which breaks the bound.
"""

from collections.abc import Callable
from typing import Any, TypeVar, cast

import numpy as np
from numba import njit, types
from numpy.typing import NDArray

Kernel = TypeVar("Kernel", bound=Callable[..., Any])


def _kernel(signature: Any) -> Callable[[Kernel], Kernel]:
    """`njit` for one signature, cached on disk, keeping the function's own type for mypy."""
    return cast(Callable[[Kernel], Kernel], njit(signature, cache=True))


def _readonly(dtype: Any) -> Any:
    return types.Array(dtype, 1, "A", readonly=True)


_I8, _I4, _F8, _U1 = (_readonly(t) for t in (types.int64, types.int32, types.float64, types.uint8))
_PATH = types.Tuple((types.boolean, types.int32[::1], types.int32[::1]))  # type: ignore[no-untyped-call]
_PATHS = types.Tuple((types.int64[::1], types.int32[::1], types.int32[::1]))  # type: ignore[no-untyped-call]
_HEAP = types.Tuple((types.float64[::1], types.int64[::1], types.int64))  # type: ignore[no-untyped-call]

I32 = NDArray[np.int32]
I64 = NDArray[np.int64]
F64 = NDArray[np.float64]
U8 = NDArray[np.uint8]


@_kernel(_PATH(_I8, _I4, _I4, _I4, _F8, _U1, _U1, types.int64, types.int64, types.float64))
def dijkstra_masked(
    out_indptr: I64,
    out_edges: I32,
    edge_u: I32,
    edge_v: I32,
    weights: NDArray[np.float64],
    enter: U8,
    banned: U8,
    src: int,
    dst: int,
    max_cost: float,
) -> tuple[bool, I32, I32]:
    """(found, nodes, edges): a cheapest path src → dst, both ends enterable, max_cost ≥ 0."""
    n = out_indptr.shape[0] - 1
    dist = np.full(n, np.inf)
    pred = np.empty(n, dtype=np.int32)
    settled = np.zeros(n, dtype=np.uint8)
    # A binary heap of (cost, node) entries, stale ones skipped when popped. Each edge is
    # relaxed at most once (when its tail settles), so it never holds more than m + 1.
    heap_cost = np.empty(out_edges.shape[0] + 1, dtype=np.float64)
    heap_node = np.empty(out_edges.shape[0] + 1, dtype=np.int64)
    dist[src] = 0.0
    heap_cost[0] = 0.0
    heap_node[0] = src
    size = 1
    while size > 0:
        cost = heap_cost[0]
        v = heap_node[0]
        size -= 1
        if size > 0:  # the last entry to the root, then down while a child is less
            last_cost = heap_cost[size]
            last_node = heap_node[size]
            i = 0
            while True:
                child = 2 * i + 1
                if child >= size:
                    break
                right = child + 1
                if right < size and (
                    heap_cost[right] < heap_cost[child]
                    or (
                        heap_cost[right] == heap_cost[child] and heap_node[right] < heap_node[child]
                    )
                ):
                    child = right
                if heap_cost[child] < last_cost or (
                    heap_cost[child] == last_cost and heap_node[child] < last_node
                ):
                    heap_cost[i] = heap_cost[child]
                    heap_node[i] = heap_node[child]
                    i = child
                else:
                    break
            heap_cost[i] = last_cost
            heap_node[i] = last_node
        if settled[v]:
            continue
        if v == dst:
            length = 0
            w = dst
            while w != src:
                w = edge_u[pred[w]]
                length += 1
            nodes = np.empty(length + 1, dtype=np.int32)
            edges = np.empty(length, dtype=np.int32)
            nodes[length] = dst
            w = dst
            for k in range(length - 1, -1, -1):
                e = pred[w]
                edges[k] = e
                w = edge_u[e]
                nodes[k] = w
            return True, nodes, edges
        settled[v] = 1
        for k in range(out_indptr[v], out_indptr[v + 1]):
            e = out_edges[k]
            w = edge_v[e]
            if settled[w] or banned[e] or not enter[w]:
                continue
            c = cost + weights[e]
            if c <= max_cost and c < dist[w]:
                dist[w] = c
                pred[w] = e
                i = size  # a new entry at the bottom, then up while less than its parent
                size += 1
                while i > 0:
                    parent = (i - 1) // 2
                    if c < heap_cost[parent] or (c == heap_cost[parent] and w < heap_node[parent]):
                        heap_cost[i] = heap_cost[parent]
                        heap_node[i] = heap_node[parent]
                        i = parent
                    else:
                        break
                heap_cost[i] = c
                heap_node[i] = w
    return False, np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32)


@_kernel(_PATHS(_I8, _I4, _I4, _U1, _U1, types.int64, types.int64, types.int64, types.int64))
def simple_paths_dfs(
    out_indptr: I64,
    out_edges: I32,
    edge_v: I32,
    enter: U8,
    banned: U8,
    src: int,
    dst: int,
    max_depth: int,
    max_paths: int,
) -> tuple[I64, I32, I32]:
    """(offsets, nodes, edges): the first `max_paths` simple paths src → dst of at most
    `max_depth` edges, in ascending edge-id order, src ≠ dst both enterable, max_paths > 0.

    Packed: path p is `edges[offsets[p]:offsets[p + 1]]` and
    `nodes[offsets[p] + p : offsets[p + 1] + p + 1]`.
    """
    n = out_indptr.shape[0] - 1
    # The path so far: its nodes (current), the edges between them, and per depth the next
    # out-edge to try. A simple path has at most n - 1 edges, whatever max_depth says.
    levels = max(min(max_depth, n), 0) + 1
    current = np.empty(levels, dtype=np.int64)
    path_edges = np.empty(levels, dtype=np.int32)
    cursor = np.empty(levels, dtype=np.int64)
    on_path = np.zeros(n, dtype=np.uint8)
    # The output, doubled whenever it fills.
    offsets = np.zeros(64, dtype=np.int64)
    edges = np.empty(1024, dtype=np.int32)
    nodes = np.empty(1024, dtype=np.int32)
    count = 0
    n_edges = 0
    n_nodes = 0

    depth = 0
    current[0] = src
    cursor[0] = out_indptr[src]
    on_path[src] = 1
    while True:
        v = current[depth]
        k = cursor[depth]
        if k == out_indptr[v + 1]:  # every out-edge of v tried: step back
            if depth == 0:
                break
            on_path[v] = 0
            depth -= 1
            continue
        cursor[depth] = k + 1
        e = out_edges[k]
        w = edge_v[e]
        if on_path[w] or banned[e] or not enter[w] or depth >= max_depth:
            continue
        if w == dst:
            if count + 2 > offsets.shape[0]:
                bigger_offsets = np.zeros(2 * offsets.shape[0], dtype=np.int64)
                bigger_offsets[: count + 1] = offsets[: count + 1]
                offsets = bigger_offsets
            if n_nodes + depth + 2 > nodes.shape[0]:
                size = 2 * (nodes.shape[0] + depth + 2)
                bigger_edges = np.empty(size, dtype=np.int32)
                bigger_edges[:n_edges] = edges[:n_edges]
                edges = bigger_edges
                bigger_nodes = np.empty(size, dtype=np.int32)
                bigger_nodes[:n_nodes] = nodes[:n_nodes]
                nodes = bigger_nodes
            for t in range(depth):
                edges[n_edges + t] = path_edges[t]
                nodes[n_nodes + t] = current[t]
            edges[n_edges + depth] = e
            nodes[n_nodes + depth] = v
            nodes[n_nodes + depth + 1] = w
            n_edges += depth + 1
            n_nodes += depth + 2
            count += 1
            offsets[count] = n_edges
            if count == max_paths:
                break
        elif depth + 1 < max_depth:
            path_edges[depth] = e
            depth += 1
            current[depth] = w
            cursor[depth] = out_indptr[w]
            on_path[w] = 1
    return offsets[: count + 1].copy(), nodes[:n_nodes].copy(), edges[:n_edges].copy()


@_kernel(types.boolean(_I8, _I8, _I4, types.int64, types.int64))
def turn_allowed(trans_indptr: I64, trans_key: I64, edge_v: I32, e: int, f: int) -> bool:
    """Whether the turn from edge e into edge f is allowed at head(e): it has no rows, or a row
    (head(e), e, f)."""
    v = edge_v[e]
    lo = trans_indptr[v]
    end = trans_indptr[v + 1]
    if lo == end:
        return True
    key = e * edge_v.shape[0] + f
    hi = end
    while lo < hi:  # the first row of the run whose key is not less than `key`
        mid = (lo + hi) // 2
        if trans_key[mid] < key:
            lo = mid + 1
        else:
            hi = mid
    return bool(lo < end and trans_key[lo] == key)


@_kernel(_HEAP(types.float64[::1], types.int64[::1], types.int64, types.float64, types.int64))
def _heap_push(
    heap_cost: F64, heap_edge: I64, size: int, cost: float, edge: int
) -> tuple[F64, I64, int]:
    """Push (cost, edge) on the binary heap of `size` entries ordered by (cost, edge), doubling
    its arrays when full: (heap_cost, heap_edge, size) after the push."""
    if size == heap_cost.shape[0]:
        bigger_cost = np.empty(2 * size, dtype=np.float64)
        bigger_cost[:size] = heap_cost
        heap_cost = bigger_cost
        bigger_edge = np.empty(2 * size, dtype=np.int64)
        bigger_edge[:size] = heap_edge
        heap_edge = bigger_edge
    i = size  # a new entry at the bottom, then up while less than its parent
    while i > 0:
        parent = (i - 1) // 2
        if cost < heap_cost[parent] or (cost == heap_cost[parent] and edge < heap_edge[parent]):
            heap_cost[i] = heap_cost[parent]
            heap_edge[i] = heap_edge[parent]
            i = parent
        else:
            break
    heap_cost[i] = cost
    heap_edge[i] = edge
    return heap_cost, heap_edge, size + 1


@_kernel(
    _PATH(
        _I8,
        _I4,
        _I4,
        _I4,
        _F8,
        _U1,
        _U1,
        _I8,
        _I8,
        types.int64,
        types.int64,
        types.int64,
        types.int64,
        types.float64,
    )
)
def dijkstra_edges_masked(
    out_indptr: I64,
    out_edges: I32,
    edge_u: I32,
    edge_v: I32,
    weights: F64,
    enter: U8,
    banned: U8,
    trans_indptr: I64,
    trans_key: I64,
    src: int,
    dst: int,
    entry_edge: int,
    exit_edge: int,
    max_cost: float,
) -> tuple[bool, I32, I32]:
    """(found, nodes, edges): a cheapest path src → dst over edge states whose every turn is
    allowed, as `backend.py` defines it; src ≠ dst, both enterable, max_cost ≥ 0. Not found
    when the first end state popped has a path that enters a node twice."""
    m = out_edges.shape[0]
    # Per edge state: 0 not reached, 1 reached (dist and pred hold its best so far), 2 settled.
    state = np.zeros(m, dtype=np.uint8)
    dist = np.empty(m, dtype=np.float64)
    pred = np.empty(m, dtype=np.int32)
    heap_cost = np.empty(64, dtype=np.float64)
    heap_edge = np.empty(64, dtype=np.int64)
    size = 0
    # The start states: kept at their own weight when within max_cost, even an infinite one
    # (no other state can reach them: no state enters src).
    for k in range(out_indptr[src], out_indptr[src + 1]):
        f = out_edges[k]
        w = edge_v[f]
        if w == src or banned[f] or not enter[w]:
            continue
        if entry_edge >= 0 and not turn_allowed(trans_indptr, trans_key, edge_v, entry_edge, f):
            continue
        c = weights[f]
        if c <= max_cost:
            state[f] = 1
            dist[f] = c
            pred[f] = -1
            heap_cost, heap_edge, size = _heap_push(heap_cost, heap_edge, size, c, f)
    while size > 0:
        cost = heap_cost[0]
        e = heap_edge[0]
        size -= 1
        if size > 0:  # the last entry to the root, then down while a child is less
            last_cost = heap_cost[size]
            last_edge = heap_edge[size]
            i = 0
            while True:
                child = 2 * i + 1
                if child >= size:
                    break
                right = child + 1
                if right < size and (
                    heap_cost[right] < heap_cost[child]
                    or (
                        heap_cost[right] == heap_cost[child] and heap_edge[right] < heap_edge[child]
                    )
                ):
                    child = right
                if heap_cost[child] < last_cost or (
                    heap_cost[child] == last_cost and heap_edge[child] < last_edge
                ):
                    heap_cost[i] = heap_cost[child]
                    heap_edge[i] = heap_edge[child]
                    i = child
                else:
                    break
            heap_cost[i] = last_cost
            heap_edge[i] = last_edge
        if state[e] == 2:
            continue
        state[e] = 2
        v = edge_v[e]
        if v == dst:
            if exit_edge >= 0 and not turn_allowed(trans_indptr, trans_key, edge_v, e, exit_edge):
                continue  # a dead end: any way on would pass dst twice
            length = 1
            x = e
            while pred[x] >= 0:
                x = pred[x]
                length += 1
            nodes = np.empty(length + 1, dtype=np.int32)
            edges = np.empty(length, dtype=np.int32)
            nodes[length] = v
            x = e
            for k in range(length - 1, -1, -1):
                edges[k] = x
                nodes[k] = edge_u[x]
                x = pred[x]
            order = np.sort(nodes)
            for k in range(length):
                if order[k] == order[k + 1]:  # a node entered twice: an illegal path
                    return False, np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32)
            return True, nodes, edges
        for k in range(out_indptr[v], out_indptr[v + 1]):
            f = out_edges[k]
            w = edge_v[f]
            # (numba cannot type `w in (src, v)` on a tuple of an int64 and an int32)
            if w == src or w == v or state[f] == 2 or banned[f] or not enter[w]:  # noqa: SIM109
                continue
            if not turn_allowed(trans_indptr, trans_key, edge_v, e, f):
                continue
            c = cost + weights[f]
            if c <= max_cost and c < (dist[f] if state[f] == 1 else np.inf):
                state[f] = 1
                dist[f] = c
                pred[f] = e
                heap_cost, heap_edge, size = _heap_push(heap_cost, heap_edge, size, c, f)
    return False, np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32)


@_kernel(
    _PATHS(
        _I8,
        _I4,
        _I4,
        _U1,
        _U1,
        _I8,
        _I8,
        types.int64,
        types.int64,
        types.int64,
        types.int64,
        types.int64,
        types.int64,
    )
)
def simple_paths_dfs_turns(
    out_indptr: I64,
    out_edges: I32,
    edge_v: I32,
    enter: U8,
    banned: U8,
    trans_indptr: I64,
    trans_key: I64,
    src: int,
    dst: int,
    entry_edge: int,
    exit_edge: int,
    max_depth: int,
    max_paths: int,
) -> tuple[I64, I32, I32]:
    """`simple_paths_dfs` whose every turn is allowed: from `entry_edge` into the first edge,
    between edges, and from the last edge into `exit_edge` (each turn edge −1 for none).
    The same enumeration, limits and packing otherwise."""
    n = out_indptr.shape[0] - 1
    levels = max(min(max_depth, n), 0) + 1
    current = np.empty(levels, dtype=np.int64)
    path_edges = np.empty(levels, dtype=np.int32)
    cursor = np.empty(levels, dtype=np.int64)
    on_path = np.zeros(n, dtype=np.uint8)
    offsets = np.zeros(64, dtype=np.int64)
    edges = np.empty(1024, dtype=np.int32)
    nodes = np.empty(1024, dtype=np.int32)
    count = 0
    n_edges = 0
    n_nodes = 0

    depth = 0
    current[0] = src
    cursor[0] = out_indptr[src]
    on_path[src] = 1
    while True:
        v = current[depth]
        k = cursor[depth]
        if k == out_indptr[v + 1]:  # every out-edge of v tried: step back
            if depth == 0:
                break
            on_path[v] = 0
            depth -= 1
            continue
        cursor[depth] = k + 1
        e = out_edges[k]
        w = edge_v[e]
        if on_path[w] or banned[e] or not enter[w] or depth >= max_depth:
            continue
        last = path_edges[depth - 1] if depth > 0 else entry_edge
        if last >= 0 and not turn_allowed(trans_indptr, trans_key, edge_v, last, e):
            continue
        if w == dst:
            if exit_edge >= 0 and not turn_allowed(trans_indptr, trans_key, edge_v, e, exit_edge):
                continue
            if count + 2 > offsets.shape[0]:
                bigger_offsets = np.zeros(2 * offsets.shape[0], dtype=np.int64)
                bigger_offsets[: count + 1] = offsets[: count + 1]
                offsets = bigger_offsets
            if n_nodes + depth + 2 > nodes.shape[0]:
                size = 2 * (nodes.shape[0] + depth + 2)
                bigger_edges = np.empty(size, dtype=np.int32)
                bigger_edges[:n_edges] = edges[:n_edges]
                edges = bigger_edges
                bigger_nodes = np.empty(size, dtype=np.int32)
                bigger_nodes[:n_nodes] = nodes[:n_nodes]
                nodes = bigger_nodes
            for t in range(depth):
                edges[n_edges + t] = path_edges[t]
                nodes[n_nodes + t] = current[t]
            edges[n_edges + depth] = e
            nodes[n_nodes + depth] = v
            nodes[n_nodes + depth + 1] = w
            n_edges += depth + 1
            n_nodes += depth + 2
            count += 1
            offsets[count] = n_edges
            if count == max_paths:
                break
        elif depth + 1 < max_depth:
            path_edges[depth] = e
            depth += 1
            current[depth] = w
            cursor[depth] = out_indptr[w]
            on_path[w] = 1
    return offsets[: count + 1].copy(), nodes[:n_nodes].copy(), edges[:n_edges].copy()
