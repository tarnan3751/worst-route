"""Cleanup, step 2 on arrays: stage A, the signal clusters and divided crossings merged.

The arrays engine's stage A (`docs/reference/pipeline.md`, **The arrays engine**) makes the
graph `consolidate.stage_a` makes from the same simplified graph, without OSMnx, by the rules
OSMnx's `consolidate_intersections` applies there:

- each node's buffer radius by the first rule its edges match (`consolidate`'s tolerances: a
  roundabout edge, an arterial class, else the default);
- the groups of nodes whose buffers overlap (`consolidate.buffer_groups`: GEOS's tests on the
  same 64-sided polygons, two that meet at a point only left apart as OSMnx's union of every
  buffer leaves them; on Portland the groups are exactly that union's parts);
- each group split into the nodes that edges of at most `STAGE_A_MAX_LENGTH_M` join (OSMnx's
  weakly connected components of the group's subgraph without its long edges): the clusters;
- a cluster of more than one node at `consolidate.cluster_centre` (pipeline version 4), its
  members its nodes and the inner OSM nodes of the edges it absorbs (both ends inside it);
- every other edge re-pointed to the clusters at its ends, its geometry extended to a merged
  cluster's position at each such end, its length then the geometry's (GEOS's).

Its order is that of the graph OSMnx rebuilds, which the steps after it depend on: a cluster's
place is the first place of its nodes among the simplified graph's (pandas' `factorize` over
them, in order), and the edges are added in the simplified graph's order, so they come by their
tail's cluster, then the entry of their head's (`order.iteration_order`), then that order. Each
edge keeps the simplified edge it is (`edge_from`, the order OSMnx adds them in) and whether a
merge made it, for `to_nx.stage_a_graph`. A graph with no edges stays as it is, its nodes plain
and still keyed by OSM id, as `consolidate.stage_a` leaves it. A cluster whose nodes lie in two
groups raises `RuntimeError`, as `consolidate.stage_a` does.
"""

from dataclasses import dataclass

import numpy as np
import pyproj
import shapely
from numpy.typing import NDArray
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from worst_route.config import (
    STAGE_A_ARTERIAL_CLASSES,
    STAGE_A_MAX_LENGTH_M,
    STAGE_A_TOLERANCE_ARTERIAL_M,
    STAGE_A_TOLERANCE_M,
    STAGE_A_TOLERANCE_ROUNDABOUT_M,
)
from worst_route.pipeline.arrays.build import LENGTH_CHUNK, Tagset
from worst_route.pipeline.arrays.order import iteration_order
from worst_route.pipeline.arrays.simplify import SimplifiedGraph
from worst_route.pipeline.build import ROUNDABOUT_JUNCTIONS
from worst_route.pipeline.consolidate import StageAStats, buffer_groups, cluster_centre

PLAIN, CLUSTER = 0, 1  # node kinds


@dataclass(frozen=True)
class StageAGraph:
    """The stage A graph as arrays, in networkx's order: n nodes, m edges."""

    crs_epsg: int
    node_label: NDArray[np.int64]  # [n] the node's key in networkx (its place, or its OSM id)
    node_kind: NDArray[np.uint8]  # [n] PLAIN or CLUSTER
    node_x: NDArray[np.float64]  # [n] projected
    node_y: NDArray[np.float64]
    node_lon: NDArray[np.float64]
    node_lat: NDArray[np.float64]
    member_indptr: NDArray[np.int64]  # [n + 1] the OSM nodes each node stands for, ascending
    member_id: NDArray[np.int64]
    edge_u: NDArray[np.int32]  # [m] node index of the tail
    edge_v: NDArray[np.int32]  # [m] and of the head
    edge_tags: NDArray[np.int32]  # [m] index into `tagsets`
    edge_oneway: NDArray[np.bool_]
    edge_reversed: NDArray[np.bool_]
    edge_length: NDArray[np.float64]  # [m] metres, along the geometry
    edge_merged: NDArray[np.bool_]  # [m] made by a merge in simplification
    edge_from: NDArray[np.int64]  # [m] the simplified edge it is (OSMnx adds them in that order)
    way_indptr: NDArray[np.int64]  # [m + 1] the edge's OSM ways, in order
    way_id: NDArray[np.int64]
    osmnode_indptr: NDArray[np.int64]  # [m + 1] the edge's OSM nodes
    osmnode_id: NDArray[np.int64]
    coord_indptr: NDArray[np.int64]  # [m + 1] the geometry: a cluster's position at a merged
    coord_x: NDArray[np.float64]  # end, then the OSM nodes' positions
    coord_y: NDArray[np.float64]
    tagsets: list[Tagset]


def stage_a(graph: SimplifiedGraph) -> tuple[StageAGraph, StageAStats]:
    """The simplified graph with its signal clusters and divided crossings merged."""
    stats = StageAStats()
    n, m = len(graph.node_id), len(graph.edge_u)
    if m == 0:
        stats.nodes = n
        cluster = np.arange(n)
        return _graph(graph, graph.node_id, cluster, graph.node_x, graph.node_y, stats), stats

    radii = _tolerances(graph)
    xy = np.column_stack([graph.node_x, graph.node_y])
    group = buffer_groups(xy, radii)
    # Clusters: within a group, the nodes that edges of at most STAGE_A_MAX_LENGTH_M join.
    inside = (group[graph.edge_u] == group[graph.edge_v]) & (
        graph.edge_length <= STAGE_A_MAX_LENGTH_M
    )
    u, v = graph.edge_u[inside], graph.edge_v[inside]
    links = coo_matrix((np.ones(len(u)), (u, v)), shape=(n, n))
    label = connected_components(links, directed=False)[1]
    # Numbered by the first place of their nodes, as pandas' factorize numbers them.
    first = np.full(label.max() + 1, n, dtype=np.int64)
    np.minimum.at(first, label, np.arange(n))
    rank = np.empty(len(first), dtype=np.int64)
    rank[np.argsort(first, kind="stable")] = np.arange(len(first))
    cluster = rank[label]

    size = np.bincount(cluster)
    group_size = np.bincount(group)
    x, y = np.empty(len(size)), np.empty(len(size))
    single = size[cluster] == 1
    x[cluster[single]], y[cluster[single]] = graph.node_x[single], graph.node_y[single]
    nodes_of = np.argsort(cluster, kind="stable")  # each cluster's nodes, in their places
    starts = np.concatenate([[0], np.cumsum(size)])
    for c in np.flatnonzero(size > 1):
        at = nodes_of[starts[c] : starts[c + 1]]
        if np.any(group[at] != group[at[0]]):  # as `consolidate._cluster_centres` guards
            raise RuntimeError(
                f"stage A merged nodes {graph.node_id[at].tolist()} whose buffers do not meet"
            )
        whole = bool(group_size[group[at[0]]] == len(at))
        x[c], y[c] = cluster_centre(xy[at], radii[at], whole)
    sizes = np.bincount(size[size > 1])
    stats.clusters = int((size > 1).sum())
    stats.nodes_merged = int(size[size > 1].sum())
    stats.cluster_sizes = {int(k): int(c) for k, c in enumerate(sizes) if c}
    result = _graph(graph, np.arange(len(size)), cluster, x, y, stats)
    return result, stats


def _tolerances(graph: SimplifiedGraph) -> NDArray[np.float64]:
    """Each node's buffer radius, by the first rule its edges (in or out) match."""
    roundabout = np.array(
        [dict(tags).get("junction") in ROUNDABOUT_JUNCTIONS for _, tags in graph.tagsets]
    )
    arterial = np.array([road_class in STAGE_A_ARTERIAL_CLASSES for road_class, _ in graph.tagsets])
    n = len(graph.node_id)
    ends = np.concatenate([graph.edge_u, graph.edge_v])
    tags = np.concatenate([graph.edge_tags, graph.edge_tags])
    on_roundabout = np.bincount(ends, weights=roundabout[tags], minlength=n) > 0
    on_arterial = np.bincount(ends, weights=arterial[tags], minlength=n) > 0
    radii = np.full(n, STAGE_A_TOLERANCE_M)
    radii[on_arterial] = STAGE_A_TOLERANCE_ARTERIAL_M
    radii[on_roundabout] = STAGE_A_TOLERANCE_ROUNDABOUT_M
    return radii


def _graph(
    graph: SimplifiedGraph,
    labels: NDArray[np.int64],
    cluster: NDArray[np.int64],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    stats: StageAStats,
) -> StageAGraph:
    """The rebuilt graph: `cluster` maps each simplified node to its cluster, numbered in
    networkx's order and placed at (`x`, `y`)."""
    k = len(labels)
    size = np.bincount(cluster, minlength=k)
    merged = size > 1
    cu, cv = cluster[graph.edge_u], cluster[graph.edge_v]
    absorbed = cu == cv
    stats.edges_absorbed = int(absorbed.sum())

    # Members: the nodes, and the inner OSM nodes of the edges a cluster absorbs.
    counts = np.diff(graph.osmnode_indptr)
    owner = np.repeat(np.arange(len(counts)), counts)
    offset = np.arange(len(owner)) - graph.osmnode_indptr[owner]
    inner = absorbed[owner] & (offset > 0) & (offset < counts[owner] - 1)
    member_of = np.concatenate([cluster, cu[owner[inner]]])
    members = np.concatenate([graph.node_id, graph.osmnode_id[inner]])
    keyed = np.unique(np.column_stack([member_of, members]), axis=0)  # by cluster, ascending
    member_indptr = np.zeros(k + 1, dtype=np.int64)
    np.cumsum(np.bincount(keyed[:, 0], minlength=k), out=member_indptr[1:])

    keep = np.flatnonzero(~absorbed)
    order = keep[iteration_order(cu[keep], cv[keep], keep)]
    start, end = merged[cu[order]], merged[cv[order]]
    counts = counts[order]
    coord_indptr = np.zeros(len(order) + 1, dtype=np.int64)
    np.cumsum(counts + start + end, out=coord_indptr[1:])
    where = _runs(graph.osmnode_indptr[order], counts)
    shifted = np.repeat(coord_indptr[:-1] + start, counts) + _offsets(counts)
    coord_x, coord_y = np.empty(coord_indptr[-1]), np.empty(coord_indptr[-1])
    coord_x[shifted], coord_y[shifted] = graph.coord_x[where], graph.coord_y[where]
    head, tail = coord_indptr[:-1][start], coord_indptr[1:][end] - 1
    coord_x[head], coord_y[head] = x[cu[order][start]], y[cu[order][start]]
    coord_x[tail], coord_y[tail] = x[cv[order][end]], y[cv[order][end]]
    length = graph.edge_length[order].copy()
    stubbed = np.flatnonzero(start | end)
    length[stubbed] = _lengths(coord_x, coord_y, coord_indptr, stubbed)

    ways = np.diff(graph.way_indptr)[order]
    way_indptr = np.zeros(len(order) + 1, dtype=np.int64)
    np.cumsum(ways, out=way_indptr[1:])
    kind = np.where(merged, CLUSTER, PLAIN).astype(np.uint8)
    lon, lat = _lonlat(graph, cluster, x, y, merged)
    stats.nodes = k
    stats.edges = len(order)
    return StageAGraph(
        crs_epsg=graph.crs_epsg,
        node_label=labels.astype(np.int64),
        node_kind=kind,
        node_x=x,
        node_y=y,
        node_lon=lon,
        node_lat=lat,
        member_indptr=member_indptr,
        member_id=keyed[:, 1].astype(np.int64),
        edge_u=cu[order].astype(np.int32),
        edge_v=cv[order].astype(np.int32),
        edge_tags=graph.edge_tags[order],
        edge_oneway=graph.edge_oneway[order],
        edge_reversed=graph.edge_reversed[order],
        edge_length=length,
        edge_merged=graph.edge_merged[order],
        edge_from=order.astype(np.int64),
        way_indptr=way_indptr,
        way_id=graph.way_id[_runs(graph.way_indptr[order], ways)],
        osmnode_indptr=np.concatenate([[0], np.cumsum(counts)]).astype(np.int64),
        osmnode_id=graph.osmnode_id[where],
        coord_indptr=coord_indptr,
        coord_x=coord_x,
        coord_y=coord_y,
        tagsets=graph.tagsets,
    )


def _lonlat(
    graph: SimplifiedGraph,
    cluster: NDArray[np.int64],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    merged: NDArray[np.bool_],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Each node's lon and lat: a plain node's own, a merged cluster's from its position."""
    lon, lat = np.empty(len(x)), np.empty(len(x))
    single = ~merged[cluster]
    lon[cluster[single]], lat[cluster[single]] = graph.node_lon[single], graph.node_lat[single]
    to_lonlat = pyproj.Transformer.from_crs(graph.crs_epsg, 4326, always_xy=True)
    lon[merged], lat[merged] = to_lonlat.transform(x[merged], y[merged])
    return lon, lat


def _offsets(counts: NDArray[np.int64]) -> NDArray[np.int64]:
    """0, 1, ... within each run of the given lengths, the runs one after the other."""
    starts = np.cumsum(counts) - counts
    offsets: NDArray[np.int64] = np.arange(int(counts.sum())) - np.repeat(starts, counts)
    return offsets


def _runs(starts: NDArray[np.int64], counts: NDArray[np.int64]) -> NDArray[np.int64]:
    """The indices of the runs [start, start + count), one after the other."""
    return np.repeat(starts, counts) + _offsets(counts)


def _lengths(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    indptr: NDArray[np.int64],
    edges: NDArray[np.int64],
) -> NDArray[np.float64]:
    """GEOS's length of the given edges' coordinate runs, `LENGTH_CHUNK` at a time."""
    lengths = np.empty(len(edges))
    for lo in range(0, len(edges), LENGTH_CHUNK):
        chunk = edges[lo : lo + LENGTH_CHUNK]
        counts = indptr[chunk + 1] - indptr[chunk]
        at = _runs(indptr[chunk], counts)
        runs = np.repeat(np.arange(len(chunk)), counts)
        lines = shapely.linestrings(x[at], y[at], indices=runs)
        lengths[lo : lo + len(chunk)] = shapely.length(lines)
    return lengths
