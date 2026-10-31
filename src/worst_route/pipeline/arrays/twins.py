"""Cleanup, step 3 on arrays: link the two carriageways of a divided road as twins.

The arrays engine's twins (`docs/reference/pipeline.md`, **The arrays engine**): the pairs
`carriageways.find_twins` finds on the same stage A graph, by the same rules and arithmetic, on
a `stage_a.StageAGraph`. Candidates are the one-way edges of `TWIN_CLASSES` (no roundabout
rings) with a signature (`names.road_signature`), or in `TWIN_GEOMETRY_ONLY_CLASSES` without one;
their segments are their geometry's, less a merged cluster's position at either end, a repeated
point left out; a `shapely.STRtree` finds the segments within each segment's lateral distance
(queried `QUERY_CHUNK` segments at a time, which finds the same pairs), and two segments pair as
`find_twins` pairs them (`carriageways._perpendicular` included). Edges with a pair are twins
unless they share an OSM way, or unless their OSM nodes are the same run reversed (one road mapped
twice, counted in `same_road_pairs`).

The candidates are taken in the order of their networkx keys `(u, v, key)`, as `find_twins`
takes them, so the tree, and the float sums of the report, are the same. `find_twins` returns
the table as arrays (each edge's twins, ascending) and the same `TwinStats`.
"""

import numpy as np
import shapely
from numpy.typing import NDArray

from worst_route.config import (
    TWIN_BEARING_TOL,
    TWIN_CLASSES,
    TWIN_GEOMETRY_ONLY_CLASSES,
    TWIN_LATERAL_M,
)
from worst_route.pipeline.arrays.stage_a import CLUSTER, StageAGraph
from worst_route.pipeline.build import ROUNDABOUT_JUNCTIONS
from worst_route.pipeline.carriageways import TwinStats, _perpendicular
from worst_route.pipeline.names import name_without_directions, road_signature

QUERY_CHUNK = 1_000_000  # segments per STRtree query


def edge_keys(graph: StageAGraph) -> NDArray[np.int64]:
    """Each edge's networkx key: its rank among the edges with its tail and head, in order."""
    m = len(graph.edge_u)
    by_pair = np.lexsort((np.arange(m), graph.edge_v, graph.edge_u))
    pair = np.column_stack([graph.edge_u[by_pair], graph.edge_v[by_pair]])
    new = np.ones(m, dtype=np.bool_)
    new[1:] = np.any(pair[1:] != pair[:-1], axis=1)
    start = np.flatnonzero(new)[np.cumsum(new) - 1]
    keys = np.empty(m, dtype=np.int64)
    keys[by_pair] = np.arange(m) - start
    return keys


def find_twins(
    graph: StageAGraph,
) -> tuple[NDArray[np.int64], NDArray[np.int32], TwinStats]:
    """Each edge's twins (`indptr`, the twins ascending) and what was found."""
    stats = TwinStats()
    m = len(graph.edge_u)
    tags = [dict(t) for _, t in graph.tagsets]
    classes = [road_class for road_class, _ in graph.tagsets]
    signature_of = [road_signature(t) for t in tags]
    # Freeway carriageways carry alias names, and couplets are never motorways (`find_twins`).
    name_of = [
        name_without_directions(t["name"]) or None if "name" in t and c != "motorway" else None
        for t, c in zip(tags, classes, strict=True)
    ]
    kept_tags = np.array(
        [
            c in TWIN_CLASSES
            and t.get("junction") not in ROUNDABOUT_JUNCTIONS
            and (signature_of[k] is not None or c in TWIN_GEOMETRY_ONLY_CLASSES)
            for k, (t, c) in enumerate(zip(tags, classes, strict=True))
        ],
        dtype=np.bool_,
    )
    keys = edge_keys(graph)
    edges = np.flatnonzero(graph.edge_oneway & kept_tags[graph.edge_tags]) if m else np.arange(0)
    edges = edges[np.lexsort((keys[edges], graph.edge_v[edges], graph.edge_u[edges]))]
    stats.candidates = len(edges)
    edge_tags = graph.edge_tags[edges].tolist()

    found = np.zeros((0, 2), dtype=np.int64)
    if len(edges):
        # Each candidate's segments, less a merged cluster's position at either end.
        first = graph.coord_indptr[edges] + (graph.node_kind[graph.edge_u[edges]] == CLUSTER)
        last = graph.coord_indptr[edges + 1] - (graph.node_kind[graph.edge_v[edges]] == CLUSTER)
        counts = np.maximum(last - first - 1, 0)
        owner = np.repeat(np.arange(len(edges)), counts)
        at = np.repeat(first - (np.cumsum(counts) - counts), counts) + np.arange(counts.sum())
        a = np.column_stack([graph.coord_x[at], graph.coord_y[at]])
        b = np.column_stack([graph.coord_x[at + 1], graph.coord_y[at + 1]])
        moves = np.any(b != a, axis=1)  # a repeated point has no bearing
        a, b, owner = a[moves], b[moves], owner[moves]
        numbers: dict[object, int] = {None: -1}
        names: dict[object, int] = {None: -1}
        signature = np.array(
            [numbers.setdefault(signature_of[t], len(numbers) - 1) for t in edge_tags]
        )
        named = np.array([names.setdefault(name_of[t], len(names) - 1) for t in edge_tags])
        reach = np.array([TWIN_LATERAL_M[classes[t]] for t in edge_tags])[owner]
        found = _pairs(a, b, owner, reach, signature, named)
    twins, stats.same_road_pairs = _not_one_road(graph, edges, found)

    paired = set(twins[:, 0].tolist())
    stats.paired = len(paired)
    stats.pairs = len(twins) // 2
    lengths = graph.edge_length[edges].tolist()
    major = [i for i, t in enumerate(edge_tags) if classes[t] in TWIN_GEOMETRY_ONLY_CLASSES]
    stats.motorway_trunk_m = float(sum(lengths[i] for i in major))
    if stats.motorway_trunk_m > 0:
        covered = sum(lengths[i] for i in major if int(edges[i]) in paired)
        stats.coverage = covered / stats.motorway_trunk_m
    indptr = np.zeros(m + 1, dtype=np.int64)
    np.cumsum(np.bincount(twins[:, 0], minlength=m), out=indptr[1:])
    return indptr, twins[:, 1].astype(np.int32), stats


def _pairs(
    a: NDArray[np.float64],
    b: NDArray[np.float64],
    owner: NDArray[np.int64],
    reach: NDArray[np.float64],
    signature: NDArray[np.int64],
    named: NDArray[np.int64],
) -> NDArray[np.int64]:
    """The (candidate, candidate) pairs of which some segments pair, as `find_twins` pairs them:
    within the lateral distance, the same signature and name, opposite bearings, side by side."""
    segments = np.asarray(shapely.linestrings(np.stack([a, b], axis=1)))
    tree = shapely.STRtree(segments)
    delta = b - a
    bearing = np.degrees(np.arctan2(delta[:, 0], delta[:, 1]))
    middle = (a + b) / 2
    found: set[tuple[int, int]] = set()
    for lo in range(0, len(segments), QUERY_CHUNK):
        hi = min(lo + QUERY_CHUNK, len(segments))
        q, t = tree.query(segments[lo:hi], predicate="dwithin", distance=reach[lo:hi])
        q = q + lo
        nq, nt = named[owner[q]], named[owner[t]]
        keep = (owner[q] != owner[t]) & (signature[owner[q]] == signature[owner[t]])
        keep &= (nq < 0) | (nt < 0) | (nq == nt)
        q, t = q[keep], t[keep]
        turn = (bearing[q] - bearing[t] + 180.0) % 360.0 - 180.0
        keep = np.abs(turn) >= 180.0 - TWIN_BEARING_TOL
        q, t = q[keep], t[keep]
        on_t, near_t = _perpendicular(middle[q], a[t], b[t], reach[q])
        on_q, near_q = _perpendicular(middle[t], a[q], b[q], reach[q])
        keep = near_t & near_q & (on_t | on_q)
        found.update(zip(owner[q[keep]].tolist(), owner[t[keep]].tolist(), strict=True))
    return np.array(sorted(found), dtype=np.int64).reshape(-1, 2)


def _not_one_road(
    graph: StageAGraph, edges: NDArray[np.int64], pairs: NDArray[np.int64]
) -> tuple[NDArray[np.int64], int]:
    """The pairs as edge ids, both ways, sorted, but for two edges that share an OSM way or run
    over the same OSM nodes in opposite directions; and the number of the latter (unordered)."""
    twins, same_road = set(), set()
    for i, j in pairs.tolist():
        e, f = int(edges[i]), int(edges[j])
        ways_e = graph.way_id[graph.way_indptr[e] : graph.way_indptr[e + 1]]
        ways_f = graph.way_id[graph.way_indptr[f] : graph.way_indptr[f + 1]]
        if np.intersect1d(ways_e, ways_f).size:
            continue  # one way curling back beside itself is one road, not two carriageways
        nodes_e = graph.osmnode_id[graph.osmnode_indptr[e] : graph.osmnode_indptr[e + 1]]
        nodes_f = graph.osmnode_id[graph.osmnode_indptr[f] : graph.osmnode_indptr[f + 1]]
        if np.array_equal(nodes_e, nodes_f[::-1]):
            same_road.add((min(e, f), max(e, f)))
            continue  # one road mapped twice, as two ways in opposite directions
        twins.update({(e, f), (f, e)})
    return np.array(sorted(twins), dtype=np.int64).reshape(-1, 2), len(same_road)
