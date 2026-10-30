"""Cleanup, step 1: drop self-loops and collapse degree-2 chains (`docs/PLAN.md` §2.4b).

Ours, not OSMnx's `simplify_graph`, which rebuilds a merged edge's geometry from node
coordinates and would lose the shape points the raw graph keeps inside each edge. `simplify`
works on a copy of the raw graph (`build.py`). It first drops every self-loop: no vertex-simple
route can use one, and an edge's `osm_nodes` never repeat an id after cleanup. Every edge's
`osmid` becomes `osmids`, the tuple of way ids it runs along, in order.

A node is contracted when its edges form exactly one through path per allowed direction (one
in and one out from two different neighbours, or two in and two out pairing into the two
directions of one road) and the two edges of each pair carry the same `MERGE_ATTRIBUTES`:
`highway`, `oneway`, `reversed` and every tag the build stores. In the raw graph such a node
always ends a way, so comparing every stored tag is what keeps a node where a way's tags change
(a new ref, name, speed limit or bridge); merged edges stay homogeneous. A pair whose merge
would repeat an OSM node id (a chain closing on itself) is left split. The merged edge
concatenates `geometry` and `osm_nodes` (the shared node once) and `osmids` (a way repeated
back to back once), and sums `length`. Nodes are visited in ascending id order, so the result
is deterministic.
"""

from dataclasses import dataclass
from typing import Any

import networkx as nx
import shapely

from worst_route.pipeline.build import EDGE_TAGS

MERGE_ATTRIBUTES = ("highway", "oneway", "reversed", *EDGE_TAGS)

type EdgeData = dict[str, Any]


@dataclass
class SimplifyStats:
    """What simplification removed and left."""

    self_loops_dropped: int = 0
    nodes_removed: int = 0
    nodes: int = 0
    edges: int = 0


def simplify(raw: nx.MultiDiGraph) -> tuple[nx.MultiDiGraph, SimplifyStats]:
    """The raw graph without self-loops, its mergeable degree-2 nodes contracted."""
    graph = raw.copy()
    stats = SimplifyStats()
    loops = [(u, v, k) for u, v, k in graph.edges(keys=True) if u == v]
    graph.remove_edges_from(loops)
    stats.self_loops_dropped = len(loops)
    for _, _, data in graph.edges(data=True):
        data["osmids"] = (data.pop("osmid"),)
    for node in sorted(graph.nodes):
        pairs = _through_pairs(graph, node)
        if pairs is None:
            continue
        for (a, _, ka, into), (_, b, kb, out) in pairs:
            graph.add_edge(a, b, **_merged(into, out))
            graph.remove_edge(a, node, ka)
            graph.remove_edge(node, b, kb)
        graph.remove_node(node)
        stats.nodes_removed += 1
    stats.nodes = graph.number_of_nodes()
    stats.edges = graph.number_of_edges()
    return graph, stats


type _Edge = tuple[int, int, int, EdgeData]


def _through_pairs(graph: nx.MultiDiGraph, node: int) -> list[tuple[_Edge, _Edge]] | None:
    """The (in, out) edge pairs through `node` if it can be contracted, else None."""
    ins: list[_Edge] = list(graph.in_edges(node, keys=True, data=True))
    outs: list[_Edge] = list(graph.out_edges(node, keys=True, data=True))
    if len(ins) == 1 and len(outs) == 1:
        pairs = [(ins[0], outs[0])]
    elif len(ins) == 2 and len(outs) == 2:
        a, b = ins[0][0], ins[1][0]
        out_to = {edge[1]: edge for edge in outs}
        if a == b or set(out_to) != {a, b}:
            return None
        pairs = [(ins[0], out_to[b]), (ins[1], out_to[a])]
    else:
        return None
    for (_, _, _, into), (_, _, _, out) in pairs:
        if any(into.get(key) != out.get(key) for key in MERGE_ATTRIBUTES):
            return None
        if set(into["osm_nodes"][:-1]) & set(out["osm_nodes"]):
            return None  # the chain closes on itself (also when both ends are one neighbour)
    return pairs


def _merged(into: EdgeData, out: EdgeData) -> EdgeData:
    """One edge running along `into` then `out`."""
    osmids = into["osmids"] + tuple(
        way for i, way in enumerate(out["osmids"]) if i or way != into["osmids"][-1]
    )
    coords = list(into["geometry"].coords) + list(out["geometry"].coords)[1:]
    return {
        **{key: value for key, value in into.items() if key in MERGE_ATTRIBUTES},
        "osmids": osmids,
        "length": into["length"] + out["length"],
        "geometry": shapely.LineString(coords),
        "osm_nodes": into["osm_nodes"] + out["osm_nodes"][1:],
    }
