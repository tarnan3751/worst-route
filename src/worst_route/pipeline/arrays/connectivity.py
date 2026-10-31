"""Cleanup, step 5 on arrays: keep the largest strongly connected component.

The arrays engine's connectivity (`docs/reference/pipeline.md`, **The arrays engine**).
`largest_component` finds the components `connectivity.largest_component` finds (scipy's
strongly connected components: the partition is unique), keeps the largest (ties to the one
holding the smallest node id) and reports what the others would drop, its sums taken in the
order networkx's are (each component's nodes ascending, each node's out-edges by their keys
`(u, v, key)`, Python's `sum` and NumPy's `mean`), so the report is the same to the bit.
`keep_only` then numbers the kept nodes 0..n-1 as `consolidate.relabel` does: stage B's numbering
is already the order relabel sorts by, so the kept nodes keep theirs, compacted, and the edges,
twins, super-junctions, restricted nodes and transitions follow. `without_no_turn` drops the
nodes with a turn table (super-junctions, and the nodes a turn restriction gave one) left with
edges in and out and no allowed pair, and what connectivity drops after them, round by round, as
`cleanup._without_no_turn` does, adding each round into the report in the same order.
"""

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from worst_route.pipeline.arrays.stage_b import StageBGraph
from worst_route.pipeline.connectivity import DROPPED_LISTED, ConnectivityReport, DroppedComponent


def largest_component(graph: StageBGraph) -> tuple[NDArray[np.bool_], ConnectivityReport]:
    """Which nodes the largest strongly connected component holds, and what the others hold."""
    n, m = len(graph.node_x), len(graph.edge_u)
    report = ConnectivityReport(n, m)
    if not n:
        return np.zeros(0, dtype=np.bool_), report
    links = coo_matrix((np.ones(m), (graph.edge_u, graph.edge_v)), shape=(n, n))
    count, label = connected_components(links, directed=True, connection="strong")
    size = np.bincount(label, minlength=count)
    by_label = np.argsort(label, kind="stable")  # each component's nodes ascending
    starts = np.cumsum(size) - size
    components = np.lexsort((by_label[starts], -size))  # the largest first, ties to the smallest
    keep = label == components[0]
    # The edges leaving each dropped component, by component, in edge order: its nodes ascending
    # and each node's out-edges by key, the order networkx sums them in.
    leaving = np.flatnonzero(~keep[graph.edge_u])
    leaving = leaving[np.argsort(label[graph.edge_u[leaving]], kind="stable")]
    first = np.searchsorted(label[graph.edge_u[leaving]], np.arange(count + 1))
    dropped = []
    for c in components[1:].tolist():
        nodes = by_label[starts[c] : starts[c] + size[c]]
        total = sum(_road_lengths(graph, leaving[first[c] : first[c + 1]]))
        lon, lat = np.mean(graph.node_lon[nodes]), np.mean(graph.node_lat[nodes])
        dropped.append(DroppedComponent(len(nodes), float(total), float(lon), float(lat)))
    dropped.sort(key=lambda c: (-c.nodes, -c.length_m))
    lost = np.flatnonzero(~keep[graph.edge_u] | ~keep[graph.edge_v])
    report.nodes_after = int(keep.sum())
    report.edges_after = m - len(lost)
    report.dropped_nodes = n - report.nodes_after
    report.dropped_edges = len(lost)
    report.dropped_length_m = float(sum(_road_lengths(graph, lost)))
    report.dropped_components = len(dropped)
    report.retained = report.nodes_after / n
    report.largest_dropped = dropped[:DROPPED_LISTED]
    return keep, report


def without_no_turn(graph: StageBGraph, scc: ConnectivityReport) -> StageBGraph:
    """The graph less every node with a turn table (a super-junction, or a node a turn restriction
    gave one) with edges in and out that allows no pair between them, and less what connectivity
    drops after each round of those; each round added to `scc`."""
    dropped = list(scc.largest_dropped)
    while True:
        n = len(graph.node_x)
        tabled = np.concatenate([graph.super_node, graph.restricted_node])
        turns = np.bincount(graph.trans_node, minlength=n)[tabled]
        ins = np.bincount(graph.edge_v, minlength=n)[tabled]
        outs = np.bincount(graph.edge_u, minlength=n)[tabled]
        no_turn = tabled[(turns == 0) & (ins > 0) & (outs > 0)]
        if not len(no_turn):
            return graph  # untouched when nothing was dropped
        gone = np.zeros(n, dtype=np.bool_)
        gone[no_turn] = True
        lost = np.flatnonzero(gone[graph.edge_u] | gone[graph.edge_v])
        lost_length = sum(_road_lengths(graph, lost))
        trimmed = keep_only(graph, ~gone)
        keep, again = largest_component(trimmed)
        scc.dropped_no_turn += len(no_turn)
        scc.dropped_nodes += len(no_turn) + again.dropped_nodes
        scc.dropped_edges += len(lost) + again.dropped_edges
        scc.dropped_length_m += lost_length
        scc.dropped_length_m += again.dropped_length_m
        scc.dropped_components += again.dropped_components
        scc.nodes_after, scc.edges_after = again.nodes_after, again.edges_after
        scc.retained = scc.nodes_after / scc.nodes_before
        dropped.extend(again.largest_dropped)
        dropped.sort(key=lambda c: (-c.nodes, -c.length_m))
        scc.largest_dropped = dropped[:DROPPED_LISTED]
        graph = keep_only(trimmed, keep)


def keep_only(graph: StageBGraph, keep: NDArray[np.bool_]) -> StageBGraph:
    """The graph of the `keep` nodes alone, numbered 0..n-1 in their order, as
    `consolidate.relabel(graph, keep)` numbers it."""
    if keep.all():
        return graph
    nodes = np.flatnonzero(keep)
    number = np.full(len(keep), -1, dtype=np.int64)
    number[nodes] = np.arange(len(nodes))
    alive = keep[graph.edge_u] & keep[graph.edge_v]
    edges = np.flatnonzero(alive)
    renumber = np.full(len(alive), -1, dtype=np.int64)
    renumber[edges] = np.arange(len(edges))
    member_indptr, members = _rows(graph.member_indptr, nodes)
    coord_indptr, coords = _rows(graph.coord_indptr, edges)
    # An edge's twins that stay; one left without any has none.
    of = np.repeat(np.arange(len(alive)), np.diff(graph.twin_indptr))
    stay = alive[of] & alive[graph.twin_edges]
    twin_indptr = np.zeros(len(edges) + 1, dtype=np.int64)
    np.cumsum(np.bincount(renumber[of[stay]], minlength=len(edges)), out=twin_indptr[1:])
    # The super-junctions that stay, with the transitions whose two edges stay.
    supers = np.flatnonzero(keep[graph.super_node])
    part_indptr, parts = _rows(graph.part_indptr, supers)
    part_member_indptr, part_members = _rows(graph.part_member_indptr, parts)
    internal_indptr, internal = _rows(graph.internal_indptr, supers)
    trans = keep[graph.trans_node] & alive[graph.trans_in] & alive[graph.trans_out]
    restricted = graph.restricted_node[keep[graph.restricted_node]]
    return replace(
        graph,
        node_kind=graph.node_kind[nodes],
        node_x=graph.node_x[nodes],
        node_y=graph.node_y[nodes],
        node_lon=graph.node_lon[nodes],
        node_lat=graph.node_lat[nodes],
        member_indptr=member_indptr,
        member_id=graph.member_id[members],
        edge_u=number[graph.edge_u[edges]].astype(np.int32),
        edge_v=number[graph.edge_v[edges]].astype(np.int32),
        edge_key=graph.edge_key[edges],
        edge_from=graph.edge_from[edges],
        edge_length=graph.edge_length[edges],
        coord_indptr=coord_indptr,
        coord_x=graph.coord_x[coords],
        coord_y=graph.coord_y[coords],
        twin_indptr=twin_indptr,
        twin_edges=renumber[graph.twin_edges[stay]].astype(np.int32),
        super_node=number[graph.super_node[supers]].astype(np.int32),
        super_roundabouts=graph.super_roundabouts[supers],
        part_indptr=part_indptr,
        part_member_indptr=part_member_indptr,
        part_member_id=graph.part_member_id[part_members],
        internal_indptr=internal_indptr,
        internal_edge=graph.internal_edge[internal],
        internal_u=graph.internal_u[internal],
        internal_v=graph.internal_v[internal],
        trans_node=number[graph.trans_node[trans]].astype(np.int32),
        trans_in=renumber[graph.trans_in[trans]].astype(np.int32),
        trans_out=renumber[graph.trans_out[trans]].astype(np.int32),
        restricted_node=number[restricted].astype(np.int32),
    )


def _road_lengths(graph: StageBGraph, edges: NDArray[np.int64]) -> list[float]:
    """The lengths of `edges`, in their order, but for the `reversed` ones (a two-way road
    counted once), as Python floats: the report's sums are Python's, over networkx's floats."""
    reversed_ = graph.stage_a.edge_reversed[graph.edge_from[edges]]
    lengths: list[float] = graph.edge_length[edges[~reversed_]].tolist()
    return lengths


def _rows(
    indptr: NDArray[np.int64], rows: NDArray[np.int64]
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """The `rows` of a CSR table: their indptr, and the place of each of their entries."""
    counts = indptr[rows + 1] - indptr[rows]
    result = np.zeros(len(rows) + 1, dtype=np.int64)
    np.cumsum(counts, out=result[1:])
    return result, np.repeat(indptr[rows] - result[:-1], counts) + np.arange(result[-1])
