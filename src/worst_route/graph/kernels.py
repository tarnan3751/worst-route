"""The Numba kernels of `CsrBackend` (`docs/reference/graph.md`, **The CSR backend**).

Plain arrays and scalars in, plain arrays out; no Python objects inside. The semantics are
`backend.py`'s, tie rule included: the heap orders entries by (cost, node), a node's out-edges
are relaxed in `out_edges` order (ascending edge id), only a strictly smaller cost replaces a
predecessor, and `max_cost` is inclusive. `CsrBackend` checks the arguments and answers the
trivial cases (a negative `max_cost`, an end that may not be entered) before it calls a
kernel, as `PythonBackend` does before it searches.

Masks arrive as uint8: zero-copy views of the bool masks (`mask.view(np.uint8)`). Each kernel
is compiled once, eagerly, for one signature whose arrays are read-only of any layout, so
memory-mapped builds, read-only cached weights and fresh writeable masks share one compiled
specialization; `cache=True` keeps it in this package's `__pycache__`, so only the first
process after a change to this file pays the compile. `NUMBA_DISABLE_JIT=1` runs the kernels
as the plain Python below (slow, but the test suite passes that way, which helps debugging).
Every search allocates its own work arrays: O(n) per call, as the masks already are.
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

I32 = NDArray[np.int32]
I64 = NDArray[np.int64]
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
