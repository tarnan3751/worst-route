"""The arrays engine's graphs handed to the networkx steps after them, as networkx holds them.

`simplified_graph(graph)` is the `MultiDiGraph` that `pipeline.simplify.simplify` makes from the
same roads, to the byte once pickled: the same nodes in the same order with their `x, y, lon,
lat`; the same edges with the same keys and attributes (`highway`, the `EDGE_TAGS` present,
`oneway`, `reversed`, `length`, `geometry`, `osm_nodes`, `osmids`), the attributes in the order
networkx gave them (an edge a merge made lists `osmids` after `reversed`, a raw one last); the
edges added in the order networkx added them (`simplify.simplify` copies the raw graph, then
merges), so that each node's successors and predecessors, and the keys of parallel edges (0, 1,
... per pair of nodes: an edge between two nodes that both stay is never removed), come out as
networkx's.

`stage_a_graph(graph, twins)` is the `MultiDiGraph` that `consolidate.stage_a` makes, the same
way: its nodes keyed and ordered as OSMnx numbers them, with `kind` and `members` too; its edges
added in the order OSMnx adds them (the simplified graph's), with the same keys and attributes,
up to the integer type of the labels (OSMnx keys some by NumPy integers, of the same value);
with `twins` (`twins.find_twins`'s table), the `G.graph["twins"]` that `carriageways.find_twins`
sets.

`stage_b_graph(graph)` is the `MultiDiGraph` that `consolidate.stage_b` makes: its nodes and
edges numbered and ordered as `consolidate.relabel` numbers them, with the `superjunctions` and
`twins` tables, each edge named in them by a tuple of its own, as relabel writes them.
`cleanup_graph(graph)` is the graph cleanup hands on once connectivity has kept its largest
component, with the view of its in-degrees that cleanup's last check caches.

A pickle writes a string several dicts share once, so to make the stage files the networkx
engine writes, byte for byte, the strings are shared as there: each way's tag values objects of
its own, shared by every edge along it, as osmium hands them out (`_WayTags`); and the keys the
networkx engine's graphs hold from its raw graph, which `cleanup` reads back from `build.pkl`
(an edge's class, tags, `oneway`, `length`, `reversed`, `geometry` and `osm_nodes`, a node's
position, the graph's `crs` and `pipeline_version`), one object each, apart from the same names
written in the code that the later steps add (`_raw_keys`).
"""

from typing import Any

import networkx as nx
import numpy as np
import shapely
from numpy.typing import NDArray

from worst_route.config import ALLEY_SERVICE
from worst_route.pipeline import PIPELINE_VERSION
from worst_route.pipeline.arrays.build import Tagset
from worst_route.pipeline.arrays.simplify import SimplifiedGraph
from worst_route.pipeline.arrays.stage_a import CLUSTER, PLAIN, StageAGraph
from worst_route.pipeline.arrays.stage_b import INTERCHANGE, ROUNDABOUT, StageBGraph
from worst_route.pipeline.arrays.twins import edge_keys
from worst_route.pipeline.build import EDGE_TAGS


def simplified_graph(graph: SimplifiedGraph) -> nx.MultiDiGraph:
    """The simplified graph as networkx holds it."""
    keys = _raw_keys()
    result = _empty(graph.crs_epsg, keys)
    ids = graph.node_id.tolist()
    x_, y_, lon_, lat_ = keys["x"], keys["y"], keys["lon"], keys["lat"]
    result.add_nodes_from(
        (node, {x_: x, y_: y, lon_: lon, lat_: lat})
        for node, x, y, lon, lat in zip(
            ids,
            graph.node_x.tolist(),
            graph.node_y.tolist(),
            graph.node_lon.tolist(),
            graph.node_lat.tolist(),
            strict=True,
        )
    )
    every = np.arange(len(graph.edge_u))
    data = _attributes(graph, every, graph.osmnode_indptr, graph.coord_x, graph.coord_y, keys)
    result.add_edges_from(
        (ids[graph.edge_u[e]], ids[graph.edge_v[e]], data[e])
        for e in np.argsort(graph.edge_insertion).tolist()
    )
    return result


def stage_a_graph(
    graph: StageAGraph, twins: tuple[NDArray[np.int64], NDArray[np.int32]] | None = None
) -> nx.MultiDiGraph:
    """The stage A graph as networkx holds it, with its twins table when given one."""
    keys = _raw_keys()
    result = _empty(graph.crs_epsg, keys)
    labels = graph.node_label.tolist()
    members = np.split(graph.member_id, graph.member_indptr[1:-1])
    result.add_nodes_from(
        (
            node,
            _node(keys, kind == CLUSTER, x, y, lon, lat)
            | {
                "kind": "cluster" if kind == CLUSTER else "plain",
                "members": tuple(members[k].tolist()),
            },
        )
        for k, (node, x, y, lon, lat, kind) in enumerate(
            zip(
                labels,
                graph.node_x.tolist(),
                graph.node_y.tolist(),
                graph.node_lon.tolist(),
                graph.node_lat.tolist(),
                graph.node_kind.tolist(),
                strict=True,
            )
        )
    )
    every = np.arange(len(graph.edge_u))
    data = _attributes(graph, every, graph.coord_indptr, graph.coord_x, graph.coord_y, keys)
    result.add_edges_from(
        (labels[graph.edge_u[e]], labels[graph.edge_v[e]], data[e])
        for e in np.argsort(graph.edge_from).tolist()
    )
    if twins is not None:
        indptr, twin_edges = twins
        ranks = edge_keys(graph).tolist()
        key = [
            (labels[u], labels[v], k)
            for u, v, k in zip(graph.edge_u.tolist(), graph.edge_v.tolist(), ranks, strict=True)
        ]
        table = {
            key[e]: tuple(sorted(key[f] for f in twin_edges[indptr[e] : indptr[e + 1]].tolist()))
            for e in np.flatnonzero(np.diff(indptr)).tolist()
        }
        result.graph["twins"] = dict(sorted(table.items()))
    return result


def stage_b_graph(graph: StageBGraph) -> nx.MultiDiGraph:
    """The stage B graph as networkx holds it (`consolidate.stage_b`'s, numbered by `relabel`),
    with its `superjunctions` and `twins` tables."""
    a = graph.stage_a
    keys = _raw_keys()
    tags_of = _WayTags(a.tagsets, keys)
    result = _empty(a.crs_epsg, keys)
    names = {
        PLAIN: "plain",
        CLUSTER: "cluster",
        INTERCHANGE: "interchange",
        ROUNDABOUT: "roundabout",
    }
    members = [tuple(m.tolist()) for m in np.split(graph.member_id, graph.member_indptr[1:-1])]
    for k, (x, y, lon, lat, kind) in enumerate(
        zip(
            graph.node_x.tolist(),
            graph.node_y.tolist(),
            graph.node_lon.tolist(),
            graph.node_lat.tolist(),
            graph.node_kind.tolist(),
            strict=True,
        )
    ):
        # A node stage A left as it was keeps its dict; a super-node's is `stage_b`'s own.
        node = _node(keys, kind != PLAIN, x, y, lon, lat)
        result.add_node(k, **node, kind=names[kind], members=members[k])
    data = _attributes(
        a,
        graph.edge_from,
        graph.coord_indptr,
        graph.coord_x,
        graph.coord_y,
        keys,
        tags_of,
        graph.edge_length,
    )
    # Lists, so that each place in the tables below holds a tuple of its own, as relabel's do.
    heads, keys_of = graph.edge_v.tolist(), graph.edge_key.tolist()
    ends = [[u, v, k] for u, v, k in zip(graph.edge_u.tolist(), heads, keys_of, strict=True)]
    result.add_edges_from((u, v, k, d) for (u, v, k), d in zip(ends, data, strict=True))

    indptr, x_inner, y_inner = _runs_of(a, graph.internal_edge)
    inner = _attributes(a, graph.internal_edge, indptr, x_inner, y_inner, keys, tags_of)
    parts = np.split(graph.part_member_id, graph.part_member_indptr[1:-1])
    transitions: dict[int, list[tuple[Any, Any]]] = {int(n): [] for n in graph.super_node}
    for node, into, out in zip(
        graph.trans_node.tolist(), graph.trans_in.tolist(), graph.trans_out.tolist(), strict=True
    ):
        transitions[node].append((tuple(ends[into]), tuple(ends[out])))
    records = {}
    for i, node in enumerate(graph.super_node.tolist()):
        lo, hi = int(graph.internal_indptr[i]), int(graph.internal_indptr[i + 1])
        records[node] = {
            "kind": names[int(graph.node_kind[node])],
            "members": members[node],
            "parts": tuple(
                tuple(p.tolist()) for p in parts[graph.part_indptr[i] : graph.part_indptr[i + 1]]
            ),
            "internal": tuple(
                {**inner[r], "u": int(graph.internal_u[r]), "v": int(graph.internal_v[r])}
                for r in range(lo, hi)
            ),
            "roundabouts": int(graph.super_roundabouts[i]),
            "transitions": tuple(transitions[node]),
        }
    result.graph["superjunctions"] = records
    twins = graph.twin_edges.tolist()
    result.graph["twins"] = {
        tuple(ends[e]): tuple(
            tuple(ends[f]) for f in twins[graph.twin_indptr[e] : graph.twin_indptr[e + 1]]
        )
        for e in np.flatnonzero(np.diff(graph.twin_indptr)).tolist()
    }
    return result


def cleanup_graph(graph: StageBGraph) -> nx.MultiDiGraph:
    """The cleaned graph as `cleanup.run_cleanup` returns it: `stage_b_graph`'s, with the view
    `cleanup._without_no_turn`'s last look at it caches, since a pickle holds it: the in-degrees,
    asked of a super-junction that allows no turn (one without edges, as the graph is strongly
    connected, so the out-degrees are never asked for)."""
    result = stage_b_graph(graph)
    if not np.bincount(graph.trans_node, minlength=len(graph.node_x))[graph.super_node].all():
        _ = result.in_degree
    return result


def _runs_of(
    graph: StageAGraph, edges: NDArray[np.int64]
) -> tuple[NDArray[np.int64], NDArray[np.float64], NDArray[np.float64]]:
    """The geometry of the given edges, one run each."""
    counts = graph.coord_indptr[edges + 1] - graph.coord_indptr[edges]
    indptr = np.zeros(len(edges) + 1, dtype=np.int64)
    np.cumsum(counts, out=indptr[1:])
    at = np.repeat(graph.coord_indptr[edges] - indptr[:-1], counts) + np.arange(indptr[-1])
    return indptr, graph.coord_x[at], graph.coord_y[at]


def _raw_keys() -> dict[str, str]:
    """The keys of the networkx engine's raw graph as `cleanup` reads it back from `build.pkl`:
    one object per name, apart from the code's own strings of the same names."""
    names = ("highway", *EDGE_TAGS, "oneway", "length", "reversed", "geometry", "osm_nodes")
    return {
        name: _own(name) for name in (*names, "x", "y", "lon", "lat", "crs", "pipeline_version")
    }


def _empty(crs_epsg: int, keys: dict[str, str]) -> nx.MultiDiGraph:
    """A graph with the raw graph's `crs` and `pipeline_version`, keyed as read back."""
    result = nx.MultiDiGraph()
    result.graph[keys["crs"]] = f"EPSG:{crs_epsg}"
    result.graph[keys["pipeline_version"]] = _own(PIPELINE_VERSION)
    return result


def _node(
    keys: dict[str, str], merged: bool, x: float, y: float, lon: float, lat: float
) -> dict[str, Any]:
    """A stage A node's position: a merged cluster's as `consolidate.stage_a` writes it, a plain
    node's as the raw graph holds it."""
    if merged:
        return {"x": x, "y": y, "lon": lon, "lat": lat}
    return {keys["x"]: x, keys["y"]: y, keys["lon"]: lon, keys["lat"]: lat}


def _attributes(
    graph: SimplifiedGraph | StageAGraph,
    edges: NDArray[np.int64],
    coord_indptr: NDArray[np.int64],
    coord_x: NDArray[np.float64],
    coord_y: NDArray[np.float64],
    keys: dict[str, str],
    tags_of: "_WayTags | None" = None,
    lengths: NDArray[np.float64] | None = None,
) -> list[dict[str, Any]]:
    """networkx's attribute dicts of the graph's `edges`, in networkx's key order and with its
    keys (`keys`, the raw graph's), with the geometry of the coordinate runs `coord_indptr` (one
    per edge of `edges`) and, when given, those `lengths` (one per edge of `edges`)."""
    tags_of = tags_of or _WayTags(graph.tagsets, keys)
    oneway_, length_, reversed_k = keys["oneway"], keys["length"], keys["reversed"]
    geometry_, osm_nodes_ = keys["geometry"], keys["osm_nodes"]
    runs = np.repeat(np.arange(len(edges)), np.diff(coord_indptr))
    geometries = np.asarray(shapely.linestrings(coord_x, coord_y, indices=runs))
    osm_nodes = np.split(graph.osmnode_id, graph.osmnode_indptr[1:-1])
    ways = np.split(graph.way_id, graph.way_indptr[1:-1])
    data = []
    for k, e in enumerate(edges.tolist()):
        tags = tags_of(int(graph.way_id[graph.way_indptr[e]]), int(graph.edge_tags[e]))
        oneway, reversed_ = bool(graph.edge_oneway[e]), bool(graph.edge_reversed[e])
        length = float(graph.edge_length[e] if lengths is None else lengths[k])
        geometry = geometries[k]
        nodes, osmids = tuple(osm_nodes[e].tolist()), tuple(ways[e].tolist())
        # The order networkx's dicts hold them in: a raw edge's build order with `osmid` popped
        # and `osmids` added last; a merged edge's as `simplify._merged` writes it.
        if graph.edge_merged[e]:
            edge = {**tags, oneway_: oneway, reversed_k: reversed_, "osmids": osmids}
            edge |= {"length": length, "geometry": geometry, "osm_nodes": nodes}
        else:
            edge = {**tags, oneway_: oneway, length_: length, reversed_k: reversed_}
            edge |= {geometry_: geometry, osm_nodes_: nodes, "osmids": osmids}
        data.append(edge)
    return data


class _WayTags:
    """Each way's class and tags as `build` holds them: one dict per way, whose strings are the
    way's own (every edge along a way shares them, an edge a merge made takes its first edge's),
    the tag keys the raw graph's and the alley class the config's constant."""

    def __init__(self, tagsets: list[Tagset], keys: dict[str, str]) -> None:
        self.tagsets = tagsets
        self.keys = keys
        self.ways: dict[int, dict[str, str]] = {}

    def __call__(self, way: int, tagset: int) -> dict[str, str]:
        if way not in self.ways:
            road_class, tags = self.tagsets[tagset]
            highway = ALLEY_SERVICE if road_class == ALLEY_SERVICE else _own(road_class)
            row = {self.keys["highway"]: highway}
            row |= {self.keys[k]: _own(v) for k, v in tags}
            self.ways[way] = row
        return self.ways[way]


def _own(value: str) -> str:
    """A string equal to `value` but its own object, as osmium hands each way its tags (a
    string of one character or none is CPython's shared one either way)."""
    return (value + " ")[:-1]
