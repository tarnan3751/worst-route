"""Cleanup, steps 2 and 4: consolidate junctions in two stages (`docs/PLAN.md` §2.4c-d).

After both stages every node carries `x, y, lon, lat`, `kind` (`plain`, `cluster`,
`interchange` or `roundabout`) and `members`, the sorted original OSM node ids it stands for.
The members of all nodes and the interior `osm_nodes` of the road segments partition the OSM
ids of the simplified graph: an id is in exactly one node's members or inside exactly one
segment (an edge and its reverse count once). An edge keeps its `osm_nodes`, `osmids`, tags,
`oneway` and `reversed`; its `geometry` is extended to a merged node's position, and its
`length` is the extended geometry's.

**Stage A** (`stage_a`) is OSMnx 2.1's `consolidate_intersections` with a per-node tolerance
(`stage_a_tolerances`; `config.STAGE_A_*`), `max_length`, dead ends kept and the graph rebuilt:
nodes whose buffers overlap and that an edge of at most `STAGE_A_MAX_LENGTH_M` joins become one
node. The post-pass places each such cluster itself (`cluster_centre`): at the centroid of the
union of its members' buffers when it is a whole group of overlapping buffers
(`buffer_groups`), at the centroid of its members' points when the edge test split the group
(as OSMnx does). OSMnx takes the first from one union of every buffer in the region, whose last
bits depend on all the others, so ours is the cluster's own and does not depend on the rest of
the graph. Edges at a cluster end at that position, their `length` the geometry's. OSMnx drops
an edge whose two ends merged without record, so the post-pass adds its interior `osm_nodes` to
the cluster's members, then replaces OSMnx's node attributes (`osmid_original`,
`street_count`, list-valued `lon`/`lat`) and edge attributes (`u_original`, `v_original`) with
ours.

**Stage B** (`stage_b`) is ours. The ramp (`*_link`) and roundabout edges, undirected, form
components; a component's attachment nodes are those with another road too. Two components
whose attachment roads share two identities (`names.road_signature`) and have attachment
nodes within `STAGE_B_JOIN_M` are one cluster: the ramps of one interchange rarely share a node.
A cluster whose links are all minor (`STAGE_B_MINOR_LINKS`, slip lanes) also takes every node
that two of its attachment nodes reach through roads of two different identities of at most
`STAGE_B_SLIP_CENTER_M`, the at-grade crossing the slip lanes bypass; clusters that claim the
same node, or a node of another cluster, become one. Then each cluster takes in its pockets
(`_absorb_pockets`): the interior nodes of a path of at most `STAGE_B_POCKET_M` along one road
from one of its attachment nodes to another, outside it, such as a bridge between two ramp
terminals; without that, the road would leave the super-node and come back to it, and no route
could go straight through. A cluster whose bbox diagonal exceeds `STAGE_B_MAX_DIAMETER_M` (never
a roundabout) is split by single linkage of its attachment nodes at `STAGE_B_SPLIT_EPS_M`, every
other node joining the group of its nearest attachment node along the cluster's edges; a group
of one node stays as it is. Each cluster becomes one super-node at the mean position of the
nodes it replaces, `kind` `roundabout` when all its ramp and roundabout edges are roundabout
edges, else `interchange`. Its internal edges (both ends inside) leave the graph; boundary edges
are re-pointed to it.

`G.graph["superjunctions"][node]` describes each super-node: `kind`, `members`, `parts` (the
members of each node it replaced), `internal` (each internal edge's attributes plus `u` and `v`,
the smallest OSM id of the part at each end), `roundabouts` (the roundabout rings among its
internal edges) and `transitions`, the sorted `(in_edge, out_edge)` pairs, edges keyed `(u, v,
key)`, for which a directed path through the internal edges leads from the in-edge's end to the
out-edge's start (the same node counts). Route splicing enforces them; every super-node has the
entry, even when no pair is allowed.

Twins (`carriageways.find_twins`) are found on the stage A graph, so a carriageway stretch that
stage B makes internal still has its twins; `stage_b` carries the table across (`_carry_twins`).
An edge that stays maps to itself. An edge internal to a super-node maps to the super-node's
boundary edges on the same road (equal `road_signature`) along its own carriageway: the in-edges
from which that road leads to it through the super-node and the out-edges it leads to, those
heading its way (within 90 degrees); failing those, every boundary edge on that road heading its
way, then every boundary edge heading its way (`_along`). So going through on one carriageway
bans the other carriageway's stretch beside it, and never the same carriageway. A pair whose two
edges are internal to one super-node is not carried (a route passes the super-node once), but a
passage through it that must drive both (out on one carriageway, a U-turn at the median's end
inside the junction, back on the other) is banned: its in-edge and out-edge become twins.
Besides, a transition every internal path of which drives an internal edge with a twin outside
the super-node (a cross street that jogs along a carriageway inside it) bans that twin on its
in-edge and its out-edge, whatever road they are on: an over-ban when a route uses either edge
for another transition, the exact form being a per-transition ban table. When that twin is
itself a boundary edge of the super-node, which a route can only use in a passage through it,
only the transition that drives the stretch and then the twin is banned. The table stays
symmetric.

`relabel` numbers the nodes `0..n-1`: plain and cluster nodes by their smallest member, then the
super-nodes by theirs, keeping edge keys and remapping the `superjunctions` and `twins` tables.
`stage_b` ends with it, and cleanup calls it again after dropping the small components.
"""

import heapq
import math
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
import osmnx
import pyproj
import shapely
from numpy.typing import NDArray
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from worst_route.config import (
    STAGE_A_ARTERIAL_CLASSES,
    STAGE_A_MAX_LENGTH_M,
    STAGE_A_TOLERANCE_ARTERIAL_M,
    STAGE_A_TOLERANCE_M,
    STAGE_A_TOLERANCE_ROUNDABOUT_M,
    STAGE_B_JOIN_M,
    STAGE_B_MAX_DIAMETER_M,
    STAGE_B_MINOR_LINKS,
    STAGE_B_POCKET_M,
    STAGE_B_SLIP_CENTER_M,
    STAGE_B_SPLIT_EPS_M,
)
from worst_route.pipeline.build import ROUNDABOUT_JUNCTIONS
from worst_route.pipeline.names import road_signature

SUPER_KINDS = frozenset({"interchange", "roundabout"})
# Segments per quarter circle of a stage A buffer: geopandas' default, which OSMnx's buffers use,
# so a pair whose distance is near the sum of their radii overlaps as it does there.
BUFFER_QUAD_SEGS = 16

type EdgeKey = tuple[int, int, int]
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


@dataclass
class StageBStats:
    """What stage B made."""

    superjunctions: dict[str, int] = field(default_factory=dict)  # by kind
    sizes: dict[int, int] = field(default_factory=dict)  # nodes replaced -> super-nodes
    joined: int = 0  # clusters made of two ramp components or more
    slip_centres: int = 0  # slip-lane clusters that took in the node they bypass
    pocket_nodes: int = 0  # nodes taken in as pockets
    split: int = 0  # clusters split by the diameter guard
    internal_edges: int = 0
    twin_pairs: int = 0  # unordered twin pairs carried across
    twin_transitions: int = 0  # transitions that must drive an internal edge with a twin outside
    twin_passages: int = 0  # passages banned for driving both edges of a twin pair inside
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


def _is_junction_edge(data: EdgeData) -> bool:
    """A ramp or roundabout edge: what stage B turns into super-junctions."""
    return bool(data["highway"].endswith("_link")) or _is_roundabout(data)


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


def buffer_groups(xy: NDArray[np.float64], radii: NDArray[np.float64]) -> NDArray[np.int32]:
    """Each point's group: the points whose stage A buffers overlap, directly or through others.

    A buffer is the `BUFFER_QUAD_SEGS` polygon OSMnx merges. Two whose centres lie further apart
    than the sum of their radii cannot meet; the others are tested with GEOS on the polygons
    themselves, whose corners decide the pairs near that sum.
    """
    if len(xy) == 0:
        return np.zeros(0, dtype=np.int32)
    pairs = cKDTree(xy).query_pairs(2 * float(radii.max()), output_type="ndarray")
    a, b = pairs[:, 0], pairs[:, 1]
    near = np.hypot(*(xy[a] - xy[b]).T) <= radii[a] + radii[b]
    a, b = a[near], b[near]
    ends = np.unique(np.concatenate([a, b]))
    polygon = np.empty(len(xy), dtype=object)
    polygon[ends] = shapely.buffer(
        shapely.points(xy[ends]), radii[ends], quad_segs=BUFFER_QUAD_SEGS
    )
    meet = shapely.intersects(polygon[a], polygon[b])
    links = coo_matrix((np.ones(int(meet.sum())), (a[meet], b[meet])), shape=(len(xy), len(xy)))
    labels: NDArray[np.int32] = connected_components(links, directed=False)[1].astype(np.int32)
    return labels


def cluster_centre(
    xy: NDArray[np.float64], radii: NDArray[np.float64], whole: bool
) -> tuple[float, float]:
    """A stage A cluster's position, from its members' positions and radii: the centroid of the
    union of their buffers when the cluster is a whole group of overlapping buffers, else (the
    edge test split the group) the centroid of their points. Neither depends on the order."""
    points = shapely.points(xy)
    shapes = shapely.buffer(points, radii, quad_segs=BUFFER_QUAD_SEGS) if whole else points
    centre = shapely.union_all(shapes).centroid
    return float(centre.x), float(centre.y)


def stage_a(graph: nx.MultiDiGraph) -> tuple[nx.MultiDiGraph, StageAStats]:
    """The simplified graph with its signal clusters and divided crossings merged."""
    stats = StageAStats()
    if graph.number_of_edges() == 0:
        merged = graph.copy()
        for node, data in merged.nodes(data=True):
            data.update(kind="plain", members=(node,))
        stats.nodes = merged.number_of_nodes()
        return merged, stats
    tolerances = stage_a_tolerances(graph)
    merged = osmnx.simplification.consolidate_intersections(
        graph,
        tolerance=tolerances,
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
    centres = _cluster_centres(graph, tolerances, originals)
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
            x, y = centres[node]
            lon, lat = to_lonlat.transform(x, y)
            attributes = {
                "x": x,
                "y": y,
                "lon": float(lon),
                "lat": float(lat),
                "kind": "cluster",
                "members": tuple(sorted({*ids, *absorbed[node]})),
            }
            sizes[len(ids)] += 1
        data.clear()
        data.update(attributes)
    for u, v, data in merged.edges(data=True):
        del data["u_original"], data["v_original"]
        if u in centres or v in centres:  # OSMnx extended it to its own position of the cluster
            coords = list(data["geometry"].coords)
            coords[0] = centres.get(u, coords[0])
            coords[-1] = centres.get(v, coords[-1])
            data["geometry"] = shapely.LineString(coords)
            data["length"] = float(data["geometry"].length)
    merged.graph = dict(graph.graph)
    stats.clusters = sum(sizes.values())
    stats.nodes_merged = sum(size * count for size, count in sizes.items())
    stats.cluster_sizes = dict(sorted(sizes.items()))
    stats.nodes = merged.number_of_nodes()
    stats.edges = merged.number_of_edges()
    return merged, stats


def _cluster_centres(
    graph: nx.MultiDiGraph, tolerances: dict[int, float], originals: dict[int, list[int]]
) -> dict[int, tuple[float, float]]:
    """The position of each cluster of `originals` (node -> the nodes of `graph` it merged) that
    merged more than one node. A cluster always lies within one group of overlapping buffers
    (OSMnx only splits a group); one that does not raises `RuntimeError`."""
    nodes = list(graph.nodes)
    index = {node: i for i, node in enumerate(nodes)}
    xy = np.array([(graph.nodes[n]["x"], graph.nodes[n]["y"]) for n in nodes]).reshape(-1, 2)
    radii = np.array([tolerances[n] for n in nodes], dtype=np.float64)
    group = buffer_groups(xy, radii)
    size = np.bincount(group)
    centres = {}
    for node, ids in originals.items():
        if len(ids) > 1:
            at = np.array(sorted(index[n] for n in ids))
            if np.any(group[at] != group[at[0]]):
                raise RuntimeError(
                    f"stage A merged nodes {sorted(ids)} whose buffers do not meet: GEOS's union "
                    "of the buffers and its intersection test disagree"
                )
            centres[node] = cluster_centre(xy[at], radii[at], bool(size[group[at[0]]] == len(at)))
    return centres


def stage_b(graph: nx.MultiDiGraph) -> tuple[nx.MultiDiGraph, StageBStats]:
    """The stage A graph with its interchanges and roundabouts as super-junctions."""
    stats = StageBStats()
    signature = _road_signatures(graph)
    clusters = _absorb_pockets(graph, _ramp_clusters(graph, stats), signature, stats)
    supers: list[tuple[set[int], str]] = []
    for nodes in clusters:
        inner = [d for _, _, d in _edges_within(graph, nodes) if _is_junction_edge(d)]
        kind = "roundabout" if inner and all(map(_is_roundabout, inner)) else "interchange"
        groups = [nodes]
        if kind == "interchange" and _diagonal(graph, nodes) > STAGE_B_MAX_DIAMETER_M:
            groups = _split(graph, nodes)
            stats.split += len(groups) > 1
        supers.extend((group, kind) for group in groups if len(group) > 1)
    supers.sort(key=lambda item: min(graph.nodes[n]["members"][0] for n in item[0]))

    super_of = {node: i for i, (nodes, _) in enumerate(supers) for node in nodes}
    base = max(graph.nodes, default=-1) + 1
    result = nx.MultiDiGraph()
    result.graph.update(graph.graph)
    for node, data in graph.nodes(data=True):
        if node not in super_of:
            result.add_node(node, **data)
    to_lonlat = pyproj.Transformer.from_crs(graph.graph["crs"], 4326, always_xy=True)
    position = []
    for i, (nodes, kind) in enumerate(supers):
        ordered = sorted(nodes)  # a float sum depends on the order; a set's order may vary
        x = float(np.mean([graph.nodes[n]["x"] for n in ordered]))
        y = float(np.mean([graph.nodes[n]["y"] for n in ordered]))
        lon, lat = to_lonlat.transform(x, y)
        position.append((x, y))
        result.add_node(base + i, x=x, y=y, lon=float(lon), lat=float(lat), kind=kind)

    internal: dict[int, list[tuple[EdgeKey, EdgeData]]] = defaultdict(list)
    ins: dict[int, list[tuple[EdgeKey, int]]] = defaultdict(list)  # (edge, node it reached)
    outs: dict[int, list[tuple[EdgeKey, int]]] = defaultdict(list)  # (edge, node it left)
    new_key: dict[EdgeKey, EdgeKey] = {}
    for u, v, k, data in graph.edges(keys=True, data=True):
        su, sv = super_of.get(u), super_of.get(v)
        if su is not None and su == sv:
            internal[su].append(((u, v, k), data))
            continue
        attributes = dict(data)
        if su is not None or sv is not None:
            coords = list(data["geometry"].coords)
            if su is not None:
                coords.insert(0, position[su])
                outs[su].append(((u, v, k), u))
            if sv is not None:
                coords.append(position[sv])
                ins[sv].append(((u, v, k), v))
            attributes["geometry"] = shapely.LineString(coords)
            attributes["length"] = float(attributes["geometry"].length)
        nu = u if su is None else base + su
        nv = v if sv is None else base + sv
        new_key[u, v, k] = (nu, nv, result.add_edge(nu, nv, **attributes))

    superjunctions = {}
    sizes: Counter[int] = Counter()
    kinds: Counter[str] = Counter()
    for i, (nodes, kind) in enumerate(supers):
        canon = {n: graph.nodes[n]["members"][0] for n in nodes}
        edges = sorted(
            internal[i], key=lambda e: (canon[e[0][0]], canon[e[0][1]], e[1]["osm_nodes"])
        )
        members = {m for n in nodes for m in graph.nodes[n]["members"]}
        members.update(m for _, data in edges for m in data["osm_nodes"][1:-1])
        reach = _reachability(nodes, [(u, v) for (u, v, _), _ in edges])
        rings = nx.Graph((u, v) for (u, v, _), data in edges if _is_roundabout(data))
        transitions = sorted(
            (new_key[into], new_key[out])
            for into, head in ins[i]
            for out, tail in outs[i]
            if tail in reach[head]
        )
        result.nodes[base + i]["members"] = tuple(sorted(members))
        superjunctions[base + i] = {
            "kind": kind,
            "members": result.nodes[base + i]["members"],
            "parts": tuple(sorted(graph.nodes[n]["members"] for n in nodes)),
            "internal": tuple({**data, "u": canon[u], "v": canon[v]} for (u, v, _), data in edges),
            "roundabouts": nx.number_connected_components(rings),
            "transitions": tuple(transitions),
        }
        sizes[len(nodes)] += 1
        kinds[kind] += 1
        stats.internal_edges += len(edges)
    result.graph["superjunctions"] = superjunctions
    if "twins" in graph.graph:
        result.graph["twins"], stats.twin_transitions, stats.twin_passages = _carry_twins(
            graph, internal, ins, outs, new_key, signature
        )
        stats.twin_pairs = sum(map(len, result.graph["twins"].values())) // 2
    stats.superjunctions = {kind: kinds[kind] for kind in sorted(SUPER_KINDS)}
    stats.sizes = dict(sorted(sizes.items()))
    relabelled = relabel(result)
    stats.nodes = relabelled.number_of_nodes()
    stats.edges = relabelled.number_of_edges()
    return relabelled, stats


def _road_signatures(graph: nx.MultiDiGraph) -> dict[EdgeKey, Any]:
    """`names.road_signature` of every edge that is not a ramp or roundabout edge."""
    return {
        (u, v, k): road_signature(data)
        for u, v, k, data in graph.edges(keys=True, data=True)
        if not _is_junction_edge(data)
    }


def _edges_within(graph: nx.MultiDiGraph, nodes: set[int]) -> Iterator[tuple[int, int, EdgeData]]:
    """(u, v, data) of every edge with both ends in `nodes`, in ascending order of u."""
    for u in sorted(nodes):
        for _, v, data in graph.out_edges(u, data=True):
            if v in nodes:
                yield u, v, data


def _ramp_clusters(graph: nx.MultiDiGraph, stats: StageBStats) -> list[set[int]]:
    """The candidate clusters: ramp components joined per interchange, slip centres added."""
    ramps = nx.Graph()
    ramps.add_edges_from((u, v) for u, v, data in graph.edges(data=True) if _is_junction_edge(data))
    first = {n: graph.nodes[n]["members"][0] for n in ramps}
    components = sorted(
        (set(c) for c in nx.connected_components(ramps)), key=lambda c: min(first[n] for n in c)
    )
    signatures: list[set[Any]] = []
    points: list[tuple[float, float]] = []
    owner: list[int] = []
    for i, component in enumerate(components):
        found: set[Any] = set()
        for node in sorted(component, key=first.__getitem__):
            roads = [data for _, data in _incident(graph, node) if not _is_junction_edge(data)]
            if roads:
                points.append((graph.nodes[node]["x"], graph.nodes[node]["y"]))
                owner.append(i)
                found.update(s for data in roads if (s := road_signature(data)) is not None)
        signatures.append(found)
    joins = nx.Graph()
    joins.add_nodes_from(range(len(components)))
    if points:
        for p, q in cKDTree(np.asarray(points)).query_pairs(STAGE_B_JOIN_M, output_type="ndarray"):
            i, j = owner[p], owner[q]
            if i != j and len(signatures[i] & signatures[j]) >= 2:
                joins.add_edge(i, j)
    clusters = []
    for group in nx.connected_components(joins):
        clusters.append(set().union(*(components[i] for i in group)))
        stats.joined += len(group) > 1

    # Slip lanes: a cluster of minor links takes in the crossing its slip lanes bypass, a node
    # two of its attachment nodes reach along two different roads (one road is no crossing).
    cluster_of = {node: i for i, nodes in enumerate(clusters) for node in nodes}
    claims = nx.Graph()
    claims.add_nodes_from(("cluster", i) for i in range(len(clusters)))
    for i, nodes in enumerate(clusters):
        links = [d for _, _, d in _edges_within(graph, nodes) if _is_junction_edge(d)]
        if not all(d["highway"] in STAGE_B_MINOR_LINKS for d in links):
            continue
        reached: dict[int, list[tuple[int, Any]]] = defaultdict(list)
        for node in sorted(nodes):
            for w, data in _incident(graph, node):
                if (
                    w not in nodes
                    and not _is_junction_edge(data)
                    and data["length"] <= STAGE_B_SLIP_CENTER_M
                ):
                    reached[w].append((node, road_signature(data)))
        centres = sorted(
            w
            for w, via in reached.items()
            if len({node for node, _ in via}) >= 2 and len({road for _, road in via}) >= 2
        )
        stats.slip_centres += bool(centres)
        for w in centres:
            claims.add_edge(("cluster", i), ("node", w))
            if w in cluster_of:
                claims.add_edge(("node", w), ("cluster", cluster_of[w]))
    merged = []
    for group in nx.connected_components(claims):
        nodes = set()
        for tag, value in group:
            nodes.update(clusters[value] if tag == "cluster" else (value,))
        merged.append(nodes)
    return merged


def _absorb_pockets(
    graph: nx.MultiDiGraph,
    clusters: list[set[int]],
    signature: dict[EdgeKey, Any],
    stats: StageBStats,
) -> list[set[int]]:
    """Each cluster with its pockets: the interior nodes of a path along one road, of at most
    `STAGE_B_POCKET_M`, from one of its attachment nodes to another and outside it.

    Clusters are taken in order of their smallest member; a node a cluster took is closed to the
    later ones, and nodes of other clusters are never crossed. A cluster repeats until it takes
    nothing more, since a node it took is an attachment node too. Unnamed roads (no signature)
    make no pockets.
    """
    first = {n: graph.nodes[n]["members"][0] for nodes in clusters for n in nodes}
    clusters = sorted(clusters, key=lambda nodes: min(first[n] for n in nodes))
    owner = {node: i for i, nodes in enumerate(clusters) for node in nodes}
    for i, nodes in enumerate(clusters):
        while True:
            taken: set[int] = set()
            for start in sorted(nodes):
                roads = {
                    road
                    for _, v, k in graph.out_edges(start, keys=True)
                    if (road := signature.get((start, v, k))) is not None
                }
                for road in roads:
                    taken |= _pocket(graph, start, road, nodes, owner, signature)
            if not taken:
                break
            nodes |= taken
            owner.update((node, i) for node in taken)
            stats.pocket_nodes += len(taken)
    return clusters


def _pocket(
    graph: nx.MultiDiGraph,
    start: int,
    road: Any,
    nodes: set[int],
    owner: dict[int, int],
    signature: dict[EdgeKey, Any],
) -> set[int]:
    """The interior nodes of the paths along `road` from `start` back into `nodes`.

    A Dijkstra search forward along `road`'s edges, at most `STAGE_B_POCKET_M` long, that never
    enters a node of another cluster and stops at the cluster's own nodes; each path to one of
    them other than `start` gives its interior nodes.
    """
    cluster = owner[start]
    best = {start: 0.0}
    before: dict[int, int] = {}
    heap = [(0.0, 0, start)]
    pushed = 0  # ties go to the order of discovery
    taken: set[int] = set()
    while heap:
        dist, _, u = heapq.heappop(heap)
        if dist > best[u]:
            continue
        for _, v, k, data in graph.out_edges(u, keys=True, data=True):
            length = dist + data["length"]
            if signature.get((u, v, k)) != road or length > STAGE_B_POCKET_M:
                continue
            if v in nodes:
                node = u if v != start else start  # back to `start` is no pocket
                while node != start:
                    taken.add(node)
                    node = before[node]
                continue
            if owner.get(v, cluster) == cluster and length < best.get(v, math.inf):
                best[v] = length
                before[v] = u
                pushed += 1
                heapq.heappush(heap, (length, pushed, v))
    return taken


def _carry_twins(
    graph: nx.MultiDiGraph,
    internal: dict[int, list[tuple[EdgeKey, EdgeData]]],
    ins: dict[int, list[tuple[EdgeKey, int]]],
    outs: dict[int, list[tuple[EdgeKey, int]]],
    new_key: dict[EdgeKey, EdgeKey],
    signature: dict[EdgeKey, Any],
) -> tuple[dict[EdgeKey, tuple[EdgeKey, ...]], int, int]:
    """The stage A graph's twins table in stage B's edge keys (the module header has the rule),
    the number of transitions that ban a twin of an internal edge they must drive, and the number
    of passages banned for driving both edges of a twin pair inside their super-node."""
    twins: dict[EdgeKey, tuple[EdgeKey, ...]] = graph.graph["twins"]
    owner = {edge: i for i, edges in internal.items() for edge, _ in edges}
    targets: dict[EdgeKey, set[EdgeKey]] = {}

    def carried(edge: EdgeKey) -> set[EdgeKey]:
        i = owner.get(edge)
        if i is None:
            return {new_key[edge]}
        if edge not in targets:
            targets[edge] = _along(graph, edge, internal[i], ins[i], outs[i], new_key, signature)
        return targets[edge]

    pairs: dict[EdgeKey, set[EdgeKey]] = defaultdict(set)
    for e, others in twins.items():
        for f in others:
            if e > f or (owner.get(e) is not None and owner.get(e) == owner.get(f)):
                continue  # each pair once; two edges inside one super-node are passed once
            for a in carried(e):
                for b in carried(f):
                    if a != b:
                        pairs[a].add(b)
                        pairs[b].add(a)

    # A transition every internal path of which drives an internal edge with a twin outside the
    # super-node bans that twin on its in-edge and its out-edge too, whatever road they are on;
    # one forced along both edges of a twin pair inside the super-node (out on one carriageway, a
    # U-turn at the median's end, back on the other) is banned outright.
    matched: set[tuple[int, EdgeKey, EdgeKey]] = set()
    passages: set[tuple[int, EdgeKey, EdgeKey]] = set()
    for i, edges in internal.items():
        keys = [edge for edge, _ in edges]
        outside = {
            edge: {
                target for f in twins.get(edge, ()) if owner.get(f) != i for target in carried(f)
            }
            for edge in keys
        }
        inside = [(e, f) for e in keys for f in twins.get(e, ()) if owner.get(f) == i and e < f]
        if not inside and not any(outside.values()):
            continue
        ends = {n for edge in keys for n in edge[:2]}
        ends |= {n for _, n in ins[i]} | {n for _, n in outs[i]}
        reach = _reachability(ends, [edge[:2] for edge in keys])
        without = {
            x: _reachability(ends, [edge[:2] for edge in keys if edge != x])
            for x in {x for x in keys if outside[x]} | {x for pair in inside for x in pair}
        }

        for e, f in inside:
            for into, head in ins[i]:
                for out, tail in outs[i]:
                    if _forced(reach, without, head, tail, (e, f)):
                        passages.add((i, into, out))
                        pairs[new_key[into]].add(new_key[out])
                        pairs[new_key[out]].add(new_key[into])
        for x, banned in outside.items():
            if not banned:
                continue
            for into, head in ins[i]:
                for out, tail in outs[i]:
                    if not _forced(reach, without, head, tail, (x,)):
                        continue
                    matched.add((i, into, out))
                    a, b = new_key[into], new_key[out]
                    for f in banned:
                        if a[1] in f[:2]:
                            # A boundary edge of this super-node is only ever used in a passage
                            # through it: ban the one transition that drives x and then f.
                            bans = [(a, f)] if f == b else [(f, b)] if f == a else []
                        else:
                            bans = [(a, f), (b, f)]
                        for one, other in bans:
                            pairs[one].add(other)
                            pairs[other].add(one)
    table = {edge: tuple(sorted(others)) for edge, others in sorted(pairs.items())}
    return table, len(matched), len(passages)


def _forced(
    reach: dict[int, set[int]],
    without: dict[EdgeKey, dict[int, set[int]]],
    head: int,
    tail: int,
    drives: tuple[EdgeKey, ...],
) -> bool:
    """Whether a passage from `head` to `tail` is allowed and every internal path of it drives
    each edge of `drives` (`without[x]` is the reachability with `x` taken out)."""
    return tail in reach[head] and all(tail not in without[x][head] for x in drives)


_NO_ROAD = object()  # the signature of a ramp or roundabout edge, equal to no road's


def _along(
    graph: nx.MultiDiGraph,
    edge: EdgeKey,
    internal: list[tuple[EdgeKey, EdgeData]],
    ins: list[tuple[EdgeKey, int]],
    outs: list[tuple[EdgeKey, int]],
    new_key: dict[EdgeKey, EdgeKey],
    signature: dict[EdgeKey, Any],
) -> set[EdgeKey]:
    """The boundary edges an internal edge's twins carry to.

    The first of these that is not empty: the in-edges its road reaches it from and the
    out-edges it reaches, through the super-node; every boundary edge on its road; every
    boundary edge; each kept only where it heads the internal edge's way (within 90 degrees),
    so a same-named turnaround inside the super-node cannot carry a twin to the other
    carriageway. When the heading leaves none at any level, the first level unfiltered.
    """
    road = signature[edge]
    same = [key for key, _ in internal if signature.get(key, _NO_ROAD) == road]
    ends = {n for key in same for n in key[:2]} | {n for _, n in ins} | {n for _, n in outs}
    reach = _reachability(ends, [key[:2] for key in same])
    u, v, _ = edge
    boundary = [key for key, _ in [*ins, *outs]]
    on_road = [key for key in boundary if signature.get(key, _NO_ROAD) == road]
    reached = [a for a, head in ins if a in on_road and u in reach[head]]
    reached += [b for b, tail in outs if b in on_road and tail in reach[v]]
    levels = [reached, on_road, boundary]
    heading = _heading(graph, edge)
    for level in levels:
        ahead = [key for key in level if np.dot(_heading(graph, key), heading) > 0]
        if ahead:
            return {new_key[key] for key in ahead}
    return {new_key[key] for key in next((level for level in levels if level), [])}


def _heading(graph: nx.MultiDiGraph, edge: EdgeKey) -> np.ndarray:
    """The vector from an edge's start to its end, leaving out stage A's stubs."""
    u, v, _ = edge
    coords = graph.edges[edge]["geometry"].coords
    first = coords[0 if graph.nodes[u]["kind"] == "plain" else 1]
    last = coords[-1 if graph.nodes[v]["kind"] == "plain" else -2]
    return np.asarray(last, dtype=float) - np.asarray(first, dtype=float)


def _diagonal(graph: nx.MultiDiGraph, nodes: Iterable[int]) -> float:
    xy = np.asarray([(graph.nodes[n]["x"], graph.nodes[n]["y"]) for n in nodes])
    return float(np.hypot(*np.ptp(xy, axis=0)))


def _split(graph: nx.MultiDiGraph, nodes: set[int]) -> list[set[int]]:
    """The cluster split by single linkage of its attachment nodes; the others join the nearest."""
    attachments = sorted(
        (n for n in nodes if any(not _is_junction_edge(d) for _, d in _incident(graph, n))),
        key=lambda n: graph.nodes[n]["members"][0],
    )
    if not attachments:
        return [nodes]
    xy = np.asarray([(graph.nodes[n]["x"], graph.nodes[n]["y"]) for n in attachments])
    linked = nx.Graph()
    linked.add_nodes_from(range(len(attachments)))
    linked.add_edges_from(cKDTree(xy).query_pairs(STAGE_B_SPLIT_EPS_M, output_type="ndarray"))
    group_of: dict[int, int] = {}
    for g, members in enumerate(nx.connected_components(linked)):
        group_of.update((attachments[i], g) for i in members)
    within = nx.Graph()
    for u, v, data in _edges_within(graph, nodes):
        if not within.has_edge(u, v) or within[u][v]["weight"] > data["length"]:
            within.add_edge(u, v, weight=data["length"])
    # Every node reaches an attachment node: a cluster is ramp components, each connected by its
    # own edges and holding an attachment node (components join, and slip lanes claim nodes,
    # only through attachment nodes), plus the crossings slip lanes took in, which are
    # attachment nodes themselves.
    _, paths = nx.multi_source_dijkstra(within, attachments)
    groups: dict[int, set[int]] = defaultdict(set)
    for node in nodes:
        groups[group_of[paths[node][0]]].add(node)
    return [groups[g] for g in sorted(groups)]


def _reachability(nodes: set[int], edges: list[tuple[int, int]]) -> dict[int, set[int]]:
    """For each node, the nodes a directed path along `edges` reaches from it (itself too)."""
    after: dict[int, list[int]] = defaultdict(list)
    for u, v in edges:
        after[u].append(v)
    reach = {}
    for start in nodes:
        seen = {start}
        queue = deque([start])
        while queue:
            for v in after[queue.popleft()]:
                if v not in seen:
                    seen.add(v)
                    queue.append(v)
        reach[start] = seen
    return reach


def relabel(graph: nx.MultiDiGraph, keep: Iterable[int] | None = None) -> nx.MultiDiGraph:
    """The graph (only `keep`'s nodes, if given) numbered 0..n-1, its tables remapped."""
    nodes = graph.nodes if keep is None else keep
    order = sorted(
        nodes,
        key=lambda n: (graph.nodes[n]["kind"] in SUPER_KINDS, graph.nodes[n]["members"][0]),
    )
    number = {old: new for new, old in enumerate(order)}
    result = nx.MultiDiGraph()
    result.graph.update(
        (key, value) for key, value in graph.graph.items() if key not in ("superjunctions", "twins")
    )
    result.add_nodes_from((number[old], graph.nodes[old]) for old in order)
    result.add_edges_from(
        sorted(
            (
                (number[u], number[v], k, data)
                for u, v, k, data in graph.edges(keys=True, data=True)
                if u in number and v in number
            ),
            key=lambda edge: edge[:3],
        )
    )

    def renumber(edge: EdgeKey) -> EdgeKey | None:
        u, v, k = edge
        return (number[u], number[v], k) if u in number and v in number else None

    if "superjunctions" in graph.graph:
        tables = {}
        for node, record in graph.graph["superjunctions"].items():
            if node in number:
                pairs = ((renumber(a), renumber(b)) for a, b in record["transitions"])
                allowed = tuple(sorted((a, b) for a, b in pairs if a is not None and b is not None))
                tables[number[node]] = {**record, "transitions": allowed}
        result.graph["superjunctions"] = dict(sorted(tables.items()))
    if "twins" in graph.graph:
        twins = {}
        for edge, others in graph.graph["twins"].items():
            new = renumber(edge)
            kept = tuple(sorted(e for e in map(renumber, others) if e is not None))
            if new is not None and kept:
                twins[new] = kept
        result.graph["twins"] = dict(sorted(twins.items()))
    return result
