"""Cleanup, between stages A and B on arrays: OSM's turn restrictions as turn tables.

The arrays engine's `restrictions.turn_tables` (`docs/reference/pipeline.md`, **Turn
restrictions**): each rule's via node found among the stage A graph's members (a node of the
simplified graph), inside a simplified edge, or nowhere; at each stage A node holding a via node,
its edges into and out of it with the OSM node each reaches or leaves, its last or first way and
the bearing of its segment there (a cluster's stub left out), and at a cluster the simplified
edges it absorbed (both ends among its members) with the bearings of their end segments, handed to
`restrictions.junction_table`, which both engines call, so the tables and the counts are the
networkx engine's. Tables are by stage A node index, pairs of stage A edge ids.
"""

from collections import defaultdict
from typing import Any

import numpy as np
from numpy.typing import NDArray

from worst_route.pipeline.arrays.simplify import SimplifiedGraph
from worst_route.pipeline.arrays.stage_a import CLUSTER, StageAGraph
from worst_route.pipeline.restrictions import RestrictionStats, Rules, bearing, junction_table


def turn_tables(
    simplified: SimplifiedGraph, merged: StageAGraph, rules: Rules
) -> tuple[dict[int, set[tuple[int, int]]], RestrictionStats]:
    """The turn tables `rules` give the stage A graph `merged`'s nodes, from the simplified graph
    it was made from, and what became of the rules."""
    stats = RestrictionStats(read=sum(map(len, rules.values())))
    if not rules:
        return {}, stats
    n = len(merged.node_x)
    by_member = np.argsort(merged.member_id, kind="stable")
    members = merged.member_id[by_member]
    holder = np.repeat(np.arange(n), np.diff(merged.member_indptr))[by_member]
    vias = np.array(sorted(rules), dtype=np.int64)
    junction = np.isin(vias, simplified.node_id)
    inner_ref = np.ones(len(simplified.osmnode_id), dtype=np.bool_)
    inner_ref[simplified.osmnode_indptr[:-1]] = False
    inner_ref[simplified.osmnode_indptr[1:] - 1] = False
    inside = np.isin(vias, simplified.osmnode_id[inner_ref])
    at_node: dict[int, Rules] = defaultdict(dict)
    nodes = np.full(len(vias), -1, dtype=np.int64)
    nodes[junction] = holder[np.searchsorted(members, vias[junction])]
    for via, node, is_junction, is_inside in zip(
        vias.tolist(), nodes.tolist(), junction.tolist(), inside.tolist(), strict=True
    ):
        if is_junction:
            at_node[node][via] = rules[via]
        elif is_inside:
            stats.via_inside_edge += len(rules[via])
        else:
            stats.via_not_found += len(rules[via])
    if not at_node:
        return {}, stats

    first_osm, last_osm = _ends(merged.osmnode_indptr, merged.osmnode_id)
    first_way, last_way = _ends(merged.way_indptr, merged.way_id)
    into, into_ptr = _by(merged.edge_v, n)
    out_of, out_ptr = _by(merged.edge_u, n)
    # The simplified edges each restricted cluster absorbed: both ends among its members.
    owner = holder[np.searchsorted(members, simplified.node_id)]
    su, sv = owner[simplified.edge_u], owner[simplified.edge_v]
    clusters = np.array([v for v in at_node if merged.node_kind[v] == CLUSTER], dtype=np.int64)
    absorbed = np.flatnonzero((su == sv) & np.isin(su, clusters))
    s_first_osm, s_last_osm = _ends(simplified.osmnode_indptr, simplified.osmnode_id)
    s_first_way, s_last_way = _ends(simplified.way_indptr, simplified.way_id)
    inner: dict[int, list[tuple[Any, int, int, int, int, int, int]]] = defaultdict(list)
    for e in absorbed.tolist():
        start, end = int(s_first_osm[e]), int(s_last_osm[e])
        lo, hi = int(simplified.osmnode_indptr[e]), int(simplified.osmnode_indptr[e + 1])
        x, y = simplified.coord_x, simplified.coord_y
        out_b = bearing(x[lo], y[lo], x[lo + 1], y[lo + 1])
        in_b = bearing(x[hi - 2], y[hi - 2], x[hi - 1], y[hi - 1])
        inner[int(su[e])].append(
            (e, start, end, int(s_first_way[e]), int(s_last_way[e]), out_b, in_b)
        )

    tables = {}
    for node, node_rules in at_node.items():
        arriving = into[into_ptr[node] : into_ptr[node + 1]].tolist()
        departing = out_of[out_ptr[node] : out_ptr[node + 1]].tolist()
        # A cluster's edges end at its position by a stub stage A added: the bearings leave it out.
        stub = int(merged.node_kind[node] == CLUSTER)
        x, y, at = merged.coord_x, merged.coord_y, merged.coord_indptr
        ins = []
        for e in arriving:
            k = int(at[e + 1]) - 1 - stub
            ins.append(
                (e, int(last_osm[e]), int(last_way[e]), bearing(x[k - 1], y[k - 1], x[k], y[k]))
            )
        outs = []
        for e in departing:
            k = int(at[e]) + stub
            outs.append(
                (e, int(first_osm[e]), int(first_way[e]), bearing(x[k], y[k], x[k + 1], y[k + 1]))
            )
        table = junction_table(ins, outs, inner.get(node, []), node_rules, stats)
        if table is not None:
            tables[node] = table
    return tables, stats


def _ends(
    indptr: NDArray[np.int64], values: NDArray[np.int64]
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """The first and the last value of each run of a CSR table."""
    return values[indptr[:-1]], values[indptr[1:] - 1]


def _by(ends: NDArray[np.int32], n: int) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """The edges grouped by one of their ends: their ids, and each node's run in them."""
    order = np.argsort(ends, kind="stable")
    ptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(ends, minlength=n), out=ptr[1:])
    return order, ptr
