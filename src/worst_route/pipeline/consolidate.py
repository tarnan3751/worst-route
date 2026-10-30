"""Cleanup, step 2: consolidate junctions, stage A (`docs/PLAN.md` §2.4c).

After stage A every node carries `x, y, lon, lat`, `kind` (`plain` or `cluster`) and `members`,
the sorted original OSM node ids it stands for. The members of all nodes and the interior
`osm_nodes` of the road segments partition the OSM ids of the simplified graph: an id is in
exactly one node's members or inside exactly one segment (an edge and its reverse count once).
An edge keeps its `osm_nodes`, `osmids`, tags, `oneway` and `reversed`; its `geometry` is
extended to a merged node's position, and its `length` is the extended geometry's.

**Stage A** (`stage_a`) is OSMnx 2.1's `consolidate_intersections` with a per-node tolerance
(`stage_a_tolerances`; `config.STAGE_A_*`), `max_length`, dead ends kept and the graph rebuilt:
nodes whose buffers overlap and that an edge of at most `STAGE_A_MAX_LENGTH_M` joins become one
node at the centroid of their buffers. OSMnx drops an edge whose two ends merged without
record, so the post-pass adds its interior `osm_nodes` to the cluster's members, then replaces
OSMnx's node attributes (`osmid_original`, `street_count`, list-valued `lon`/`lat`) and edge
attributes (`u_original`, `v_original`) with ours.
"""

from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import osmnx
import pyproj

from worst_route.config import (
    STAGE_A_ARTERIAL_CLASSES,
    STAGE_A_MAX_LENGTH_M,
    STAGE_A_TOLERANCE_ARTERIAL_M,
    STAGE_A_TOLERANCE_M,
    STAGE_A_TOLERANCE_ROUNDABOUT_M,
)
from worst_route.pipeline.build import ROUNDABOUT_JUNCTIONS

type EdgeData = dict[str, Any]


@dataclass
class StageAStats:
    """What stage A merged."""

    clusters: int = 0  # nodes of kind "cluster"
    nodes_merged: int = 0  # input nodes that went into a cluster
    edges_absorbed: int = 0  # directed edges dropped because both ends merged
    cluster_sizes: dict[int, int] = field(default_factory=dict)  # input nodes -> clusters
    nodes: int = 0
    edges: int = 0


def _incident(graph: nx.MultiDiGraph, node: int) -> Iterator[tuple[int, EdgeData]]:
    """(neighbour, edge data) for every edge into or out of `node`."""
    for u, _, data in graph.in_edges(node, data=True):
        yield u, data
    for _, v, data in graph.out_edges(node, data=True):
        yield v, data


def _is_roundabout(data: EdgeData) -> bool:
    return data.get("junction") in ROUNDABOUT_JUNCTIONS


def stage_a_tolerances(graph: nx.MultiDiGraph) -> dict[int, float]:
    """Each node's stage A buffer radius, by the first rule its incident edges match."""
    tolerances = {}
    for node in graph.nodes:
        edges = [data for _, data in _incident(graph, node)]
        if any(_is_roundabout(data) for data in edges):
            tolerances[node] = STAGE_A_TOLERANCE_ROUNDABOUT_M
        elif any(data["highway"] in STAGE_A_ARTERIAL_CLASSES for data in edges):
            tolerances[node] = STAGE_A_TOLERANCE_ARTERIAL_M
        else:
            tolerances[node] = STAGE_A_TOLERANCE_M
    return tolerances


def stage_a(graph: nx.MultiDiGraph) -> tuple[nx.MultiDiGraph, StageAStats]:
    """The simplified graph with its signal clusters and divided crossings merged."""
    stats = StageAStats()
    if graph.number_of_edges() == 0:
        merged = graph.copy()
        for node, data in merged.nodes(data=True):
            data.update(kind="plain", members=(node,))
        stats.nodes = merged.number_of_nodes()
        return merged, stats
    merged = osmnx.simplification.consolidate_intersections(
        graph,
        tolerance=stage_a_tolerances(graph),
        rebuild_graph=True,
        max_length=STAGE_A_MAX_LENGTH_M,
        dead_ends=True,
        reconnect_edges=True,
    )
    originals: dict[int, list[int]] = {}
    cluster_of: dict[int, int] = {}
    for node, original in merged.nodes(data="osmid_original"):
        originals[node] = original if isinstance(original, list) else [original]
        for osm_id in originals[node]:
            cluster_of[osm_id] = node
    absorbed: dict[int, set[int]] = defaultdict(set)
    for u, v, data in graph.edges(data=True):
        if cluster_of[u] == cluster_of[v]:  # no self-loops are left, so u != v
            absorbed[cluster_of[u]].update(data["osm_nodes"][1:-1])
            stats.edges_absorbed += 1
    to_lonlat = pyproj.Transformer.from_crs(graph.graph["crs"], 4326, always_xy=True)
    sizes: Counter[int] = Counter()
    for node, data in merged.nodes(data=True):
        ids = originals[node]
        if len(ids) == 1:
            attributes = {**graph.nodes[ids[0]], "kind": "plain", "members": (ids[0],)}
        else:
            lon, lat = to_lonlat.transform(data["x"], data["y"])
            attributes = {
                "x": float(data["x"]),
                "y": float(data["y"]),
                "lon": float(lon),
                "lat": float(lat),
                "kind": "cluster",
                "members": tuple(sorted({*ids, *absorbed[node]})),
            }
            sizes[len(ids)] += 1
        data.clear()
        data.update(attributes)
    for _, _, data in merged.edges(data=True):
        del data["u_original"], data["v_original"]
    merged.graph = dict(graph.graph)
    stats.clusters = sum(sizes.values())
    stats.nodes_merged = sum(size * count for size, count in sizes.items())
    stats.cluster_sizes = dict(sorted(sizes.items()))
    stats.nodes = merged.number_of_nodes()
    stats.edges = merged.number_of_edges()
    return merged, stats
