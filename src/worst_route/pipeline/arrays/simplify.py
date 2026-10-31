"""Cleanup, step 1 on arrays: drop self-loops and collapse degree-2 chains.

The arrays engine's `simplify` (`docs/reference/pipeline.md`, **The arrays engine**) makes the
graph `pipeline.simplify.simplify` makes, from a `build.RawGraph`, by running the same algorithm:
nodes visited one at a time in ascending OSM id (`_contract`, Numba), each contracted when its
edges form exactly one through path per allowed direction (one in and one out, or two in and two
out pairing into the two directions of one road) whose two edges carry the same class, tags,
`oneway` and `reversed`, and whose far ends differ. That last test is networkx's "the merge would
repeat an OSM node" test: segments share only junctions, and a contracted node lies inside one
chain only, so two edges through a node share an OSM node exactly when their far ends are one
node (a ring's or a loop's last merge, a dead end). Each merge adds an edge whose length is the
sum of its two in the order networkx adds them, so the float sums agree; its geometry, OSM nodes
and ways (a way repeated back to back once) are the leaves of its merge tree in order, expanded
at the end (`_expand`).

The order of the result is networkx's, which the steps after it depend on: nodes in the raw
graph's order; edges by their tail's position, then their entry in the tail's adjacency (the
earliest of the edges between the same two nodes), then their own insertion. An edge's insertion
is its raw one (segment k's forward edge, then its backward one) when no merge touched it, else
the visit of the node whose contraction made it, after every raw edge; this is the order in
which networkx's dicts hold them (`docs/reference/pipeline.md` has the argument). Each edge also
keeps whether a merge made it and its place in the order networkx added it (the raw graph's
copy first, then the merges), from which `to_nx.py` rebuilds networkx's graph as networkx built
it, predecessors included.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
from numba import njit
from numpy.typing import NDArray

from worst_route.pipeline.arrays.build import RawGraph, Tagset
from worst_route.pipeline.simplify import SimplifyStats


def _jit[Kernel: Callable[..., Any]](function: Kernel) -> Kernel:
    """`njit`, cached on disk, keeping the function's own type for mypy."""
    return cast(Kernel, njit(cache=True)(function))


@dataclass(frozen=True)
class SimplifiedGraph:
    """The simplified graph as arrays, in networkx's order: n nodes, m edges, c coordinates."""

    crs_epsg: int
    node_id: NDArray[np.int64]  # [n] OSM node id
    node_x: NDArray[np.float64]  # [n] projected
    node_y: NDArray[np.float64]
    node_lon: NDArray[np.float64]
    node_lat: NDArray[np.float64]
    edge_u: NDArray[np.int32]  # [m] index of the tail in the node arrays
    edge_v: NDArray[np.int32]  # [m] and of the head
    edge_tags: NDArray[np.int32]  # [m] index into `tagsets`
    edge_oneway: NDArray[np.bool_]  # [m]
    edge_reversed: NDArray[np.bool_]  # [m]
    edge_length: NDArray[np.float64]  # [m] metres
    edge_merged: NDArray[np.bool_]  # [m] made by a merge (networkx orders its attributes so)
    edge_insertion: NDArray[np.int64]  # [m] its place in the order networkx added the edges
    way_indptr: NDArray[np.int64]  # [m + 1] the edge's OSM ways, in order
    way_id: NDArray[np.int64]
    osmnode_indptr: NDArray[np.int64]  # [m + 1] the edge's OSM nodes, and their coordinates
    osmnode_id: NDArray[np.int64]  # [c]
    coord_x: NDArray[np.float64]  # [c] projected
    coord_y: NDArray[np.float64]
    tagsets: list[Tagset]


def simplify(raw: RawGraph) -> tuple[SimplifiedGraph, SimplifyStats]:
    """The raw graph without self-loops, its mergeable degree-2 nodes contracted."""
    stats = SimplifyStats()
    # The directed edges in networkx's insertion order: segment k forward, then backward.
    seq = np.flatnonzero(np.column_stack([raw.seg_forward, raw.seg_backward]).ravel())
    seg, backward = seq // 2, (seq % 2).astype(np.bool_)
    tail = np.where(backward, raw.seg_v[seg], raw.seg_u[seg]).astype(np.int32)
    head = np.where(backward, raw.seg_u[seg], raw.seg_v[seg]).astype(np.int32)
    loop = tail == head
    stats.self_loops_dropped = int(loop.sum())
    seq, seg, backward, tail, head = (
        seq[~loop],
        seg[~loop],
        backward[~loop],
        tail[~loop],
        head[~loop],
    )
    oneway = raw.seg_forward[seg] != raw.seg_backward[seg]
    reversed_ = backward & raw.seg_forward[seg]  # as networkx: a oneway=-1 edge is not reversed
    # Equal keys are equal attributes: the class and tags, oneway, reversed.
    key = raw.seg_tags[seg].astype(np.int64) * 4 + oneway * 2 + reversed_
    n = len(raw.node_id)
    visit = np.argsort(raw.node_id, kind="stable").astype(np.int32)
    lengths = raw.seg_length[seg]
    p_tail, p_head, p_key, p_length, left, right, made_at, alive, removed = _contract(
        n, visit, tail, head, key, lengths
    )

    live = np.flatnonzero(alive)
    # networkx's order: tail position, the entry's first insertion, then the edge's own.
    merged = made_at[live] >= 0
    when = np.empty(len(live), dtype=np.int64)
    when[merged] = raw.node_id[made_at[live[merged]]]
    when[~merged] = seq[live[~merged]]
    entry_merged, entry_when = _entry_first(p_tail[live], p_head[live], merged, when)
    iteration = np.lexsort((when, merged, entry_when, entry_merged, p_tail[live]))
    order = live[iteration]
    # networkx's graph is a copy of the raw graph, its edges added in the raw graph's iteration
    # order (which sets each node's predecessors in the order of their places), then the merges'
    # edges as they happen: the order `to_nx.py` adds them in to rebuild it.
    added = np.concatenate(
        [iteration[~merged[iteration]], np.flatnonzero(merged)[np.argsort(when[merged])]]
    )
    insertion = np.empty(len(live), dtype=np.int64)
    insertion[added] = np.arange(len(live))

    leaves, leaf_indptr = _expand(order, left, right)
    leaf_seg = seg[leaves]
    leaf_backward = backward[leaves]
    starts = raw.ref_indptr[leaf_seg]
    stops = raw.ref_indptr[leaf_seg + 1]
    refs, osmnode_indptr = _chain_refs(starts, stops, leaf_backward, leaf_indptr)
    ways = raw.seg_way[leaf_seg]
    repeat = np.zeros(len(ways), dtype=np.bool_)
    repeat[1:] = ways[1:] == ways[:-1]
    repeat[leaf_indptr[:-1]] = False  # an edge's first way is always its own
    owner = np.repeat(np.arange(len(order)), np.diff(leaf_indptr))
    way_indptr = np.zeros(len(order) + 1, dtype=np.int64)
    np.cumsum(np.bincount(owner[~repeat], minlength=len(order)), out=way_indptr[1:])

    kept = np.flatnonzero(~removed)
    index = np.full(n, -1, dtype=np.int32)
    index[kept] = np.arange(len(kept), dtype=np.int32)
    simplified = SimplifiedGraph(
        crs_epsg=raw.crs_epsg,
        node_id=raw.node_id[kept],
        node_x=raw.node_x[kept],
        node_y=raw.node_y[kept],
        node_lon=raw.node_lon[kept],
        node_lat=raw.node_lat[kept],
        edge_u=index[p_tail[order]],
        edge_v=index[p_head[order]],
        edge_tags=(p_key[order] // 4).astype(np.int32),
        edge_oneway=(p_key[order] // 2 % 2).astype(np.bool_),
        edge_reversed=(p_key[order] % 2).astype(np.bool_),
        edge_length=p_length[order],
        edge_merged=merged[iteration],
        edge_insertion=insertion[iteration],
        way_indptr=way_indptr,
        way_id=ways[~repeat],
        osmnode_indptr=osmnode_indptr,
        osmnode_id=raw.ref_id[refs],
        coord_x=raw.ref_x[refs],
        coord_y=raw.ref_y[refs],
        tagsets=raw.tagsets,
    )
    stats.nodes_removed = int(removed.sum())
    stats.nodes = len(kept)
    stats.edges = len(order)
    return simplified, stats


def _entry_first(
    tail: NDArray[np.int32],
    head: NDArray[np.int32],
    merged: NDArray[np.bool_],
    when: NDArray[np.int64],
) -> tuple[NDArray[np.bool_], NDArray[np.int64]]:
    """For each edge, the earliest insertion among the edges with its tail and head."""
    first = np.lexsort((when, merged, head, tail))
    pair = np.column_stack([tail[first], head[first]])
    new = np.ones(len(first), dtype=np.bool_)
    new[1:] = np.any(pair[1:] != pair[:-1], axis=1)
    group = np.cumsum(new) - 1
    lead = first[np.flatnonzero(new)]
    entry_merged = np.empty(len(first), dtype=np.bool_)
    entry_when = np.empty(len(first), dtype=np.int64)
    entry_merged[first] = merged[lead][group]
    entry_when[first] = when[lead][group]
    return entry_merged, entry_when


@_jit
def _contract(
    n: int,
    visit: NDArray[np.int32],
    tail0: NDArray[np.int32],
    head0: NDArray[np.int32],
    key0: NDArray[np.int64],
    length0: NDArray[np.float64],
) -> tuple[
    NDArray[np.int32],
    NDArray[np.int32],
    NDArray[np.int64],
    NDArray[np.float64],
    NDArray[np.int64],
    NDArray[np.int64],
    NDArray[np.int32],
    NDArray[np.bool_],
    NDArray[np.bool_],
]:
    """networkx's contraction, node by node in `visit` order, over a pool of edges: the raw edges
    first, then one per merge, each with its two children and the node whose contraction made it
    (-1 for a raw edge). Each node's edges sit in fixed slots, one per raw in- or out-edge: a
    merge puts the new edge in the slots its children held at its two ends."""
    m0 = len(tail0)
    cap = m0 + 2 * n
    tail = np.empty(cap, np.int32)
    head = np.empty(cap, np.int32)
    key = np.empty(cap, np.int64)
    length = np.empty(cap, np.float64)
    left = np.full(cap, -1, np.int64)
    right = np.full(cap, -1, np.int64)
    made_at = np.full(cap, -1, np.int32)
    alive = np.zeros(cap, np.bool_)
    tail[:m0] = tail0
    head[:m0] = head0
    key[:m0] = key0
    length[:m0] = length0
    alive[:m0] = True
    out_ptr = np.zeros(n + 1, np.int64)
    in_ptr = np.zeros(n + 1, np.int64)
    for e in range(m0):
        out_ptr[tail0[e] + 1] += 1
        in_ptr[head0[e] + 1] += 1
    for v in range(n):
        out_ptr[v + 1] += out_ptr[v]
        in_ptr[v + 1] += in_ptr[v]
    out_slot = np.empty(m0, np.int64)
    in_slot = np.empty(m0, np.int64)
    out_pos = np.empty(cap, np.int64)
    in_pos = np.empty(cap, np.int64)
    out_fill = out_ptr[:-1].copy()
    in_fill = in_ptr[:-1].copy()
    for e in range(m0):
        out_pos[e] = out_fill[tail0[e]]
        out_slot[out_fill[tail0[e]]] = e
        out_fill[tail0[e]] += 1
        in_pos[e] = in_fill[head0[e]]
        in_slot[in_fill[head0[e]]] = e
        in_fill[head0[e]] += 1
    removed = np.zeros(n, np.bool_)
    m = m0
    for i in range(n):
        node = visit[i]
        ins = in_ptr[node + 1] - in_ptr[node]
        outs = out_ptr[node + 1] - out_ptr[node]
        if ins == 1 and outs == 1:
            into = in_slot[in_ptr[node]]
            out = out_slot[out_ptr[node]]
            if tail[into] == head[out] or key[into] != key[out]:
                continue
            pairs = ((into, out), (-1, -1))
        elif ins == 2 and outs == 2:
            i0 = in_slot[in_ptr[node]]
            i1 = in_slot[in_ptr[node] + 1]
            o0 = out_slot[out_ptr[node]]
            o1 = out_slot[out_ptr[node] + 1]
            a, b = tail[i0], tail[i1]
            if a == b:
                continue
            if head[o0] == a and head[o1] == b:
                to_a, to_b = o0, o1
            elif head[o0] == b and head[o1] == a:
                to_a, to_b = o1, o0
            else:
                continue
            if key[i0] != key[to_b] or key[i1] != key[to_a]:
                continue
            pairs = ((i0, to_b), (i1, to_a))
        else:
            continue
        for into, out in pairs:
            if into < 0:
                break
            tail[m] = tail[into]
            head[m] = head[out]
            key[m] = key[into]
            length[m] = length[into] + length[out]
            left[m] = into
            right[m] = out
            made_at[m] = node
            alive[m] = True
            alive[into] = False
            alive[out] = False
            out_pos[m] = out_pos[into]
            out_slot[out_pos[into]] = m
            in_pos[m] = in_pos[out]
            in_slot[in_pos[out]] = m
            m += 1
        removed[node] = True
    return (
        tail[:m],
        head[:m],
        key[:m],
        length[:m],
        left[:m],
        right[:m],
        made_at[:m],
        alive[:m],
        removed,
    )


@_jit
def _expand(
    edges: NDArray[np.int64], left: NDArray[np.int64], right: NDArray[np.int64]
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """The raw edges of each of `edges`, in order: the leaves of its merge tree, left first."""
    indptr = np.zeros(len(edges) + 1, np.int64)
    leaves = np.empty(len(left), np.int64)  # a tree of k leaves has k - 1 inner edges
    stack = np.empty(len(left), np.int64)
    count = 0
    for k in range(len(edges)):
        top = 0
        stack[0] = edges[k]
        while top >= 0:
            e = stack[top]
            top -= 1
            if left[e] < 0:
                leaves[count] = e
                count += 1
            else:
                stack[top + 1] = right[e]
                stack[top + 2] = left[e]
                top += 2
        indptr[k + 1] = count
    return leaves[:count], indptr


@_jit
def _chain_refs(
    starts: NDArray[np.int64],
    stops: NDArray[np.int64],
    backward: NDArray[np.bool_],
    leaf_indptr: NDArray[np.int64],
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """The positions of each edge's OSM nodes in the raw refs: its leaves' refs (a backward leaf's
    reversed) in order, the node two leaves share once."""
    total = 0
    for k in range(len(starts)):
        total += stops[k] - starts[k]
    refs = np.empty(total, np.int64)
    indptr = np.zeros(len(leaf_indptr), np.int64)
    count = 0
    for e in range(len(leaf_indptr) - 1):
        for k in range(leaf_indptr[e], leaf_indptr[e + 1]):
            skip = 0 if k == leaf_indptr[e] else 1
            size = stops[k] - starts[k]
            for j in range(skip, size):
                refs[count] = stops[k] - 1 - j if backward[k] else starts[k] + j
                count += 1
        indptr[e + 1] = count
    return refs[:count], indptr
