"""Cleanup, step 4 on arrays: stage B, interchanges and roundabouts as super-junctions.

The arrays engine's stage B (`docs/reference/pipeline.md`, **The arrays engine**) makes the graph
`consolidate.stage_b` makes from the same stage A graph and twins, by its rules, read from arrays:
the ramp and roundabout components and their joins, those along a carriageway that leaves a cluster
and comes back to it (`_carriageway_rejoins`, `_comes_back`), the slip-lane centres, the pockets
(along a road by its ref set or its name), the diameter guard's split, the super-nodes with their
records and transitions, the twins carried across, then the nodes numbered as `consolidate.relabel`
numbers them; with the turn tables of stage A nodes (`restrictions.turn_tables`), each honoured
inside a super-junction by `consolidate`'s own walk (`_passages`, `_forcing`) and kept at a node
outside them. The rules are `consolidate`'s (its module header has them); what this module adds is
reading them off arrays in the order networkx would visit them, wherever the order can decide: the
stage A graph's out-edges in its iteration order (the arrays' own, `order.py`), its nodes by their
stage A labels (their index), the pocket search's ties to the order of discovery, the split's
multi-source search over a small networkx graph built as `consolidate._split` builds it, and a
super-node's position as the mean of its nodes' in label order. Everything else is sets.

Its local work (a cluster's nodes, its pockets, its records) runs in Python over the arrays,
cluster by cluster: clusters hold a percent or two of the nodes. `StageBGraph` holds the result
in the final numbering, with the stage A graph it came from for the internal edges' attributes;
its transitions are the super-junctions' and those of the nodes outside them with a table
(`restricted_node`).
"""

import heapq
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import networkx as nx
import numpy as np
import pyproj
from numpy.typing import NDArray
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from worst_route.config import (
    STAGE_B_CARRIAGEWAY_CLASSES,
    STAGE_B_JOIN_M,
    STAGE_B_MAX_DIAMETER_M,
    STAGE_B_MINOR_LINKS,
    STAGE_B_POCKET_M,
    STAGE_B_SLIP_CENTER_M,
    STAGE_B_SPLIT_EPS_M,
)
from worst_route.pipeline.arrays.stage_a import PLAIN, StageAGraph, _lengths
from worst_route.pipeline.build import ROUNDABOUT_JUNCTIONS
from worst_route.pipeline.consolidate import (
    StageBStats,
    Turns,
    _forcing,
    _passages,
    _reachability,
)
from worst_route.pipeline.names import normalize_name, road_signature

INTERCHANGE, ROUNDABOUT = 2, 3  # node kinds beside stage A's PLAIN and CLUSTER
_NO_ROAD = object()  # the signature of a ramp or roundabout edge, equal to no road's


@dataclass(frozen=True)
class StageBGraph:
    """The stage B graph as arrays, numbered as `consolidate.relabel` numbers it: n nodes, m
    edges in the order of their networkx keys (u, v, key), s super-junctions by node."""

    stage_a: StageAGraph  # the internal edges' attributes are its edges'
    node_kind: NDArray[np.uint8]  # [n] PLAIN, CLUSTER, INTERCHANGE or ROUNDABOUT
    node_x: NDArray[np.float64]  # [n]
    node_y: NDArray[np.float64]
    node_lon: NDArray[np.float64]
    node_lat: NDArray[np.float64]
    member_indptr: NDArray[np.int64]  # [n + 1] the OSM nodes each node stands for, ascending
    member_id: NDArray[np.int64]
    edge_u: NDArray[np.int32]  # [m]
    edge_v: NDArray[np.int32]
    edge_key: NDArray[np.int64]  # [m] networkx's key among the edges with the same ends
    edge_from: NDArray[np.int64]  # [m] the stage A edge it is
    edge_length: NDArray[np.float64]  # [m] along the geometry, extended to a super-node
    coord_indptr: NDArray[np.int64]  # [m + 1] the geometry
    coord_x: NDArray[np.float64]
    coord_y: NDArray[np.float64]
    twin_indptr: NDArray[np.int64]  # [m + 1] each edge's twins, ascending
    twin_edges: NDArray[np.int32]
    super_node: NDArray[np.int32]  # [s] ascending
    super_roundabouts: NDArray[np.int64]  # [s] the roundabout rings among its internal edges
    part_indptr: NDArray[np.int64]  # [s + 1] its parts, rows of `part_member_indptr`
    part_member_indptr: NDArray[np.int64]  # [p + 1] each part's members, ascending
    part_member_id: NDArray[np.int64]
    internal_indptr: NDArray[np.int64]  # [s + 1] its internal edges, in its record's order
    internal_edge: NDArray[np.int64]  # stage A edge ids
    internal_u: NDArray[np.int64]  # the smallest member of the part each end is in
    internal_v: NDArray[np.int64]
    trans_node: NDArray[np.int32]  # the allowed (in, out) pairs, by (node, in, out)
    trans_in: NDArray[np.int32]
    trans_out: NDArray[np.int32]
    restricted_node: NDArray[np.int32]  # [r] ascending: the nodes outside the super-junctions
    # whose rows are a turn table (`consolidate.stage_b`'s `G.graph["restricted"]`)


class _View:
    """The stage A graph's adjacency and edge classes, as `stage_b` reads them."""

    def __init__(self, graph: StageAGraph) -> None:
        self.graph = graph
        n, m = len(graph.node_x), len(graph.edge_u)
        self.n, self.m = n, m
        tags = [dict(t) for _, t in graph.tagsets]
        classes = [c for c, _ in graph.tagsets]
        roundabout = np.array([t.get("junction") in ROUNDABOUT_JUNCTIONS for t in tags], bool)
        link = np.array([c.endswith("_link") for c in classes], dtype=np.bool_)
        minor = np.array([c in STAGE_B_MINOR_LINKS for c in classes], dtype=np.bool_)
        self.roundabout = roundabout.reshape(-1)[graph.edge_tags] if m else np.zeros(0, bool)
        self.junction = (
            (link.reshape(-1)[graph.edge_tags] | self.roundabout) if m else self.roundabout
        )
        self.minor = minor.reshape(-1)[graph.edge_tags] if m else np.zeros(0, bool)
        tag_signature = [road_signature(t) for t in tags]
        self.signature: list[Any] = [
            _NO_ROAD if self.junction[e] else tag_signature[t]
            for e, t in enumerate(graph.edge_tags.tolist())
        ]
        # The keys a pocket may follow an edge by (`consolidate._road_keys`): its signature and
        # its normalised name, a ramp or roundabout edge none.
        tag_keys = [
            frozenset(k for k in (sig, normalize_name(t["name"]) if t.get("name") else None) if k)
            for sig, t in zip(tag_signature, tags, strict=True)
        ]
        self.road_keys: list[frozenset[Any]] = [
            frozenset() if self.junction[e] else tag_keys[t]
            for e, t in enumerate(graph.edge_tags.tolist())
        ]
        # Each edge's carriageway key (`consolidate._carriageway_key`), numbered; -1 none.
        numbers: dict[Any, int] = {}
        tag_carriageway = [
            numbers.setdefault((c, sig), len(numbers))
            if c in STAGE_B_CARRIAGEWAY_CLASSES and sig is not None
            else -1
            for c, sig in zip(classes, tag_signature, strict=True)
        ]
        self.carriageway = [tag_carriageway[t] for t in graph.edge_tags.tolist()]
        # Each edge's first two and last two OSM nodes, for a chain continuing node to node.
        lo, hi = graph.osmnode_indptr[:-1], graph.osmnode_indptr[1:]
        self.osm_first = graph.osmnode_id[lo].tolist()
        self.osm_second = graph.osmnode_id[lo + 1].tolist()
        self.osm_last = graph.osmnode_id[hi - 1].tolist()
        self.osm_penult = graph.osmnode_id[hi - 2].tolist()
        self.first = graph.member_id[graph.member_indptr[:-1]].tolist()  # smallest member
        self.out_ptr = np.zeros(n + 1, dtype=np.int64)  # edges are sorted by tail already
        np.cumsum(np.bincount(graph.edge_u, minlength=n), out=self.out_ptr[1:])
        self.in_order = np.argsort(graph.edge_v, kind="stable")
        self.in_ptr = np.zeros(n + 1, dtype=np.int64)
        np.cumsum(np.bincount(graph.edge_v, minlength=n), out=self.in_ptr[1:])
        self.u = graph.edge_u.tolist()
        self.v = graph.edge_v.tolist()
        self.length = graph.edge_length.tolist()

    def outs(self, node: int) -> range:
        return range(int(self.out_ptr[node]), int(self.out_ptr[node + 1]))

    def incident(self, node: int) -> list[tuple[int, int]]:
        """(neighbour, edge) for every edge into or out of `node`."""
        ins = self.in_order[self.in_ptr[node] : self.in_ptr[node + 1]].tolist()
        return [(self.u[e], e) for e in ins] + [(self.v[e], e) for e in self.outs(node)]

    def within(self, nodes: set[int]) -> list[int]:
        """The edges with both ends in `nodes`, by ascending tail, as `_edges_within` gives them."""
        return [e for u in sorted(nodes) for e in self.outs(u) if self.v[e] in nodes]


def stage_b(
    graph: StageAGraph,
    twins: tuple[NDArray[np.int64], NDArray[np.int32]],
    turns: Turns | None = None,
) -> tuple[StageBGraph, StageBStats]:
    """The stage A graph with its interchanges and roundabouts as super-junctions, the turn
    tables `turns` of its nodes (stage A node -> allowed pairs of stage A edge ids) honoured
    inside them and kept at the others."""
    turns = turns or {}
    stats = StageBStats()
    view = _View(graph)
    clusters = _absorb_pockets(view, _ramp_clusters(view, stats), stats)
    supers: list[tuple[set[int], int]] = []
    for nodes in clusters:
        inner = [e for e in view.within(nodes) if view.junction[e]]
        kind = ROUNDABOUT if inner and all(view.roundabout[e] for e in inner) else INTERCHANGE
        groups = [nodes]
        if kind == INTERCHANGE and _diagonal(graph, nodes) > STAGE_B_MAX_DIAMETER_M:
            groups = _split(view, nodes)
            stats.split += len(groups) > 1
        supers.extend((group, kind) for group in groups if len(group) > 1)
    supers.sort(key=lambda item: min(view.first[n] for n in item[0]))
    return _rebuild(view, supers, twins, turns, stats), stats


def _ramp_clusters(view: _View, stats: StageBStats) -> list[set[int]]:
    """`consolidate._ramp_clusters`: the ramp components joined per interchange, slip centres
    added."""
    graph = view.graph
    junction = np.flatnonzero(view.junction)
    ends = np.unique(np.concatenate([graph.edge_u[junction], graph.edge_v[junction]]))
    links = coo_matrix(
        (np.ones(len(junction)), (graph.edge_u[junction], graph.edge_v[junction])),
        shape=(view.n, view.n),
    )
    label = connected_components(links, directed=False)[1][ends]
    by_label: dict[int, set[int]] = defaultdict(set)
    for node, c in zip(ends.tolist(), label.tolist(), strict=True):
        by_label[c].add(node)
    components = sorted(by_label.values(), key=lambda c: min(view.first[n] for n in c))
    signatures: list[set[Any]] = []
    points: list[tuple[float, float]] = []
    owner: list[int] = []
    attached: list[int] = []
    for i, component in enumerate(components):
        found: set[Any] = set()
        for node in sorted(component, key=view.first.__getitem__):
            roads = [e for _, e in view.incident(node) if not view.junction[e]]
            if roads:
                points.append((float(graph.node_x[node]), float(graph.node_y[node])))
                owner.append(i)
                attached.append(node)
                found.update(s for e in roads if (s := view.signature[e]) is not None)
        signatures.append(found)
    joins = nx.Graph()
    joins.add_nodes_from(range(len(components)))
    pairs: list[tuple[int, int]] = []
    if points:
        tree = cKDTree(np.asarray(points))
        found_pairs = tree.query_pairs(STAGE_B_JOIN_M, output_type="ndarray")
        pairs = [(int(p), int(q)) for p, q in found_pairs]
        for p, q in pairs:
            i, j = owner[p], owner[q]
            if i != j and len(signatures[i] & signatures[j]) >= 2:
                joins.add_edge(i, j)
    rejoins = _carriageway_rejoins(
        view, components, joins, [(attached[p], attached[q]) for p, q in pairs]
    )
    joins.add_edges_from(rejoins)
    stats.rejoined = len(rejoins)
    clusters = []
    for group in nx.connected_components(joins):
        clusters.append(set().union(*(components[i] for i in group)))
        stats.joined += len(group) > 1

    cluster_of = {node: i for i, nodes in enumerate(clusters) for node in nodes}
    claims = nx.Graph()
    claims.add_nodes_from(("cluster", i) for i in range(len(clusters)))
    for i, nodes in enumerate(clusters):
        if not all(view.minor[e] for e in view.within(nodes) if view.junction[e]):
            continue
        reached: dict[int, list[tuple[int, Any]]] = defaultdict(list)
        for node in sorted(nodes):
            for w, e in view.incident(node):
                if (
                    w not in nodes
                    and not view.junction[e]
                    and view.length[e] <= STAGE_B_SLIP_CENTER_M
                ):
                    reached[w].append((node, view.signature[e]))
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
        claimed: set[int] = set()
        for tag, value in group:
            claimed.update(clusters[value] if tag == "cluster" else (value,))
        merged.append(claimed)
    return merged


def _carriageway_rejoins(
    view: _View,
    components: list[set[int]],
    joins: nx.Graph,
    pairs: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """`consolidate._carriageway_rejoins`: the joins of clusters one carriageway leaves, passes
    the other of and comes back to, the candidates `pairs` of attachment nodes of two clusters
    that share a carriageway key, repeated until nothing more joins."""
    cluster = {i: min(group) for group in nx.connected_components(joins) for i in group}
    nodes: dict[int, set[int]] = defaultdict(set)
    for i, c in cluster.items():
        nodes[c] |= components[i]
    component = {node: i for i, members in enumerate(components) for node in members}
    keys = {node: _carriageways(view, node) for pair in pairs for node in pair}
    rejoins: list[tuple[int, int]] = []
    changed = True
    while changed:
        changed = False
        for p, q in pairs:
            a, b = cluster[component[p]], cluster[component[q]]
            shared = keys[p] & keys[q]
            if a == b or not shared:
                continue
            if _comes_back(view, nodes[a], nodes[b], shared) or _comes_back(
                view, nodes[b], nodes[a], shared
            ):
                rejoins.append((component[p], component[q]))
                keep, gone = min(a, b), max(a, b)
                nodes[keep] |= nodes.pop(gone)
                cluster = {i: keep if c == gone else c for i, c in cluster.items()}
                changed = True
    return rejoins


def _carriageways(view: _View, node: int) -> set[int]:
    """The carriageway keys of the edges in and out of `node`."""
    return {k for _, e in view.incident(node) if (k := view.carriageway[e]) >= 0}


def _comes_back(view: _View, nodes: set[int], other: set[int], keys: set[int]) -> bool:
    """`consolidate._comes_back`: whether a chain of one of `keys` leaves `nodes`, passes a node
    of `other` and comes back into `nodes` within `STAGE_B_MAX_DIAMETER_M`, every continuation
    explored, shortest first, each (edge, passed) state once per first edge."""
    for start in sorted(nodes):
        for first in view.outs(start):
            key = view.carriageway[first]
            if view.v[first] in nodes or key not in keys:
                continue
            heap = [(view.length[first], 0, first, view.v[first] in other)]
            pushed = 0
            seen: set[tuple[int, bool]] = set()
            while heap:
                length, _, edge, passed = heapq.heappop(heap)
                if (edge, passed) in seen:
                    continue
                seen.add((edge, passed))
                v = view.v[edge]
                if v in nodes:
                    if passed:
                        return True
                    continue
                for e in view.outs(v):
                    total = length + view.length[e]
                    if (
                        view.osm_first[e] == view.osm_last[edge]
                        and view.osm_second[e] != view.osm_penult[edge]
                        and view.carriageway[e] == key
                        and total <= STAGE_B_MAX_DIAMETER_M
                    ):
                        pushed += 1
                        heapq.heappush(heap, (total, pushed, e, passed or view.v[e] in other))
    return False


def _absorb_pockets(view: _View, clusters: list[set[int]], stats: StageBStats) -> list[set[int]]:
    """`consolidate._absorb_pockets`: each cluster, in order of its smallest member, with its
    pockets, repeated until it takes nothing more."""
    clusters = sorted(clusters, key=lambda nodes: min(view.first[n] for n in nodes))
    owner = {node: i for i, nodes in enumerate(clusters) for node in nodes}
    for i, nodes in enumerate(clusters):
        while True:
            taken: set[int] = set()
            for start in sorted(nodes):
                roads = {road for e in view.outs(start) for road in view.road_keys[e]}
                for road in roads:
                    taken |= _pocket(view, start, road, nodes, owner)
            if not taken:
                break
            nodes |= taken
            owner.update((node, i) for node in taken)
            stats.pocket_nodes += len(taken)
    return clusters


def _pocket(view: _View, start: int, road: Any, nodes: set[int], owner: dict[int, int]) -> set[int]:
    """`consolidate._pocket`: the interior nodes of the paths along `road` from `start` back into
    `nodes`, by a Dijkstra search whose ties go to the order of discovery."""
    cluster = owner[start]
    best = {start: 0.0}
    before: dict[int, int] = {}
    heap = [(0.0, 0, start)]
    pushed = 0
    taken: set[int] = set()
    while heap:
        dist, _, u = heapq.heappop(heap)
        if dist > best[u]:
            continue
        for e in view.outs(u):
            v = view.v[e]
            length = dist + view.length[e]
            if road not in view.road_keys[e] or length > STAGE_B_POCKET_M:
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


def _diagonal(graph: StageAGraph, nodes: Iterable[int]) -> float:
    at = np.fromiter(nodes, dtype=np.int64)
    xy = np.column_stack([graph.node_x[at], graph.node_y[at]])
    return float(np.hypot(*np.ptp(xy, axis=0)))


def _split(view: _View, nodes: set[int]) -> list[set[int]]:
    """`consolidate._split`: the cluster split by single linkage of its attachment nodes, the
    others joining the nearest along the cluster's edges (networkx's multi-source search over the
    same small graph, so its ties fall the same way)."""
    graph = view.graph
    attachments = sorted(
        (n for n in nodes if any(not view.junction[e] for _, e in view.incident(n))),
        key=view.first.__getitem__,
    )
    if not attachments:
        return [nodes]
    xy = np.asarray([(graph.node_x[n], graph.node_y[n]) for n in attachments])
    linked = nx.Graph()
    linked.add_nodes_from(range(len(attachments)))
    linked.add_edges_from(cKDTree(xy).query_pairs(STAGE_B_SPLIT_EPS_M, output_type="ndarray"))
    group_of: dict[int, int] = {}
    for g, members in enumerate(nx.connected_components(linked)):
        group_of.update((attachments[i], g) for i in members)
    within = nx.Graph()
    for e in view.within(nodes):
        u, v, length = view.u[e], view.v[e], view.length[e]
        if not within.has_edge(u, v) or within[u][v]["weight"] > length:
            within.add_edge(u, v, weight=length)
    _, paths = nx.multi_source_dijkstra(within, attachments)
    groups: dict[int, set[int]] = defaultdict(set)
    for node in nodes:
        groups[group_of[paths[node][0]]].add(node)
    return [groups[g] for g in sorted(groups)]


def _rebuild(
    view: _View,
    supers: list[tuple[set[int], int]],
    twins: tuple[NDArray[np.int64], NDArray[np.int32]],
    turns: Turns,
    stats: StageBStats,
) -> StageBGraph:
    """The graph with each super-junction one node, its records, the twins carried across, then
    numbered as `consolidate.relabel` numbers it."""
    graph = view.graph
    n, m, s = view.n, view.m, len(supers)
    super_of = np.full(n, -1, dtype=np.int64)
    for i, (nodes, _) in enumerate(supers):
        super_of[list(nodes)] = i
    # Positions: the mean of a super-node's nodes, in their label order (np.mean's own sum).
    sx, sy = np.empty(s), np.empty(s)
    for i, (nodes, _) in enumerate(supers):
        ordered = sorted(nodes)
        sx[i] = float(np.mean([graph.node_x[k] for k in ordered]))
        sy[i] = float(np.mean([graph.node_y[k] for k in ordered]))

    su, sv = super_of[graph.edge_u], super_of[graph.edge_v]
    inside = (su >= 0) & (su == sv)
    kept = np.flatnonzero(~inside)  # stage A order, which networkx adds them in
    # Pre-relabel nodes: stage A labels, super i is n + i.
    pu = np.where(su >= 0, n + su, graph.edge_u)[kept]
    pv = np.where(sv >= 0, n + sv, graph.edge_v)[kept]
    # networkx's key: the rank among the kept edges with the same (pu, pv), in that order.
    by_pair = np.lexsort((np.arange(len(kept)), pv, pu))
    new = np.ones(len(kept), dtype=np.bool_)
    new[1:] = (pu[by_pair][1:] != pu[by_pair][:-1]) | (pv[by_pair][1:] != pv[by_pair][:-1])
    start = np.flatnonzero(new)[np.cumsum(new) - 1]
    key = np.empty(len(kept), dtype=np.int64)
    key[by_pair] = np.arange(len(kept)) - start
    pre_id = np.full(m, -1, dtype=np.int64)  # stage A edge -> its index among the kept
    pre_id[kept] = np.arange(len(kept))

    internal: dict[int, list[int]] = defaultdict(list)
    ins: dict[int, list[tuple[int, int]]] = defaultdict(list)  # (stage A edge, node it reached)
    outs: dict[int, list[tuple[int, int]]] = defaultdict(list)  # (stage A edge, node it left)
    for e in np.flatnonzero(inside).tolist():
        internal[int(su[e])].append(e)
    for e in np.flatnonzero(~inside & (su >= 0)).tolist():
        outs[int(su[e])].append((e, view.u[e]))
    for e in np.flatnonzero(~inside & (sv >= 0)).tolist():
        ins[int(sv[e])].append((e, view.v[e]))

    # Members: a super-node's parts and its internal edges' inner OSM nodes.
    node_members = np.split(graph.member_id, graph.member_indptr[1:-1])
    records: list[Any] = []  # one per super-junction
    sizes: Counter[int] = Counter()
    kinds: Counter[int] = Counter()
    for i, (nodes, kind) in enumerate(supers):
        canon = {k: view.first[k] for k in nodes}
        edges = sorted(
            internal[i],
            key=lambda e: (canon[view.u[e]], canon[view.v[e]], tuple(_osm_nodes(graph, e))),
        )
        members = {int(x) for k in nodes for x in node_members[k]}
        members.update(int(x) for e in edges for x in _osm_nodes(graph, e)[1:-1])
        within = [(e, view.u[e], view.v[e]) for e in edges]
        rings = [(view.u[e], view.v[e]) for e in edges if view.roundabout[e]]
        transitions = _passages(ins[i], outs[i], within, turns)
        stats.restricted += any(node in turns for node in nodes)
        parts = sorted(tuple(int(x) for x in node_members[k]) for k in nodes)
        records.append((sorted(members), parts, edges, canon, _components(rings), transitions))
        sizes[len(nodes)] += 1
        kinds[kind] += 1
        stats.internal_edges += len(edges)

    pairs, stats.twin_transitions, stats.twin_passages = _carry_twins(
        view, supers, internal, ins, outs, pre_id, n, pu, pv, twins, turns
    )
    stats.superjunctions = {"interchange": kinds[INTERCHANGE], "roundabout": kinds[ROUNDABOUT]}
    stats.sizes = dict(sorted(sizes.items()))
    restricted = {node: table for node, table in turns.items() if super_of[node] < 0}
    return _relabel(view, supers, records, sx, sy, kept, pu, pv, key, pairs, restricted, stats)


def _osm_nodes(graph: StageAGraph, e: int) -> list[int]:
    nodes: list[int] = graph.osmnode_id[
        graph.osmnode_indptr[e] : graph.osmnode_indptr[e + 1]
    ].tolist()
    return nodes


def _components(edges: list[tuple[int, int]]) -> int:
    """The connected components of the undirected graph of `edges` (its nodes their ends)."""
    graph = nx.Graph(edges)
    return int(nx.number_connected_components(graph))


def _carry_twins(
    view: _View,
    supers: list[tuple[set[int], int]],
    internal: dict[int, list[int]],
    ins: dict[int, list[tuple[int, int]]],
    outs: dict[int, list[tuple[int, int]]],
    pre_id: NDArray[np.int64],
    n: int,
    pu: NDArray[np.int64],
    pv: NDArray[np.int64],
    twins: tuple[NDArray[np.int64], NDArray[np.int32]],
    turns: Turns,
) -> tuple[set[tuple[int, int]], int, int]:
    """`consolidate._carry_twins` over edge ids: the twin pairs (both ways) among the kept edges'
    indices, the transitions that ban a twin of an internal edge they must drive, and the
    passages banned for driving both edges of a twin pair inside their super-node, a passage's
    internal paths those the turn tables `turns` allow."""
    indptr, twin_edges = twins
    twin_of = {
        e: twin_edges[indptr[e] : indptr[e + 1]].tolist()
        for e in np.flatnonzero(np.diff(indptr)).tolist()
    }
    owner = {e: i for i, edges in internal.items() for e in edges}
    targets: dict[int, set[int]] = {}

    def carried(e: int) -> set[int]:
        i = owner.get(e)
        if i is None:
            return {int(pre_id[e])}
        if e not in targets:
            targets[e] = _along(view, e, internal[i], ins[i], outs[i], pre_id)
        return targets[e]

    pairs: set[tuple[int, int]] = set()
    for e, others in twin_of.items():
        for f in others:
            if e > f or (owner.get(e) is not None and owner.get(e) == owner.get(f)):
                continue  # each pair once; two edges inside one super-node are passed once
            for a in carried(e):
                for b in carried(f):
                    if a != b:
                        pairs.update({(a, b), (b, a)})

    matched: set[tuple[int, int, int]] = set()
    passages: set[tuple[int, int, int]] = set()
    for i, keys in internal.items():
        outside = {
            e: {t for f in twin_of.get(e, ()) if owner.get(f) != i for t in carried(f)}
            for e in keys
        }
        inside = [(e, f) for e in keys for f in twin_of.get(e, ()) if owner.get(f) == i and e < f]
        if not inside and not any(outside.values()):
            continue
        dropped = {x for x in keys if outside[x]} | {x for pair in inside for x in pair}
        within = [(e, view.u[e], view.v[e]) for e in keys]
        forced = _forcing(ins[i], outs[i], within, turns, dropped)
        for e, f in inside:
            for into, head in ins[i]:
                for out, tail in outs[i]:
                    if forced(into, head, out, tail, (e, f)):
                        passages.add((i, into, out))
                        a, b = int(pre_id[into]), int(pre_id[out])
                        pairs.update({(a, b), (b, a)})
        node = n + i
        for x, banned in outside.items():
            if not banned:
                continue
            for into, head in ins[i]:
                for out, tail in outs[i]:
                    if not forced(into, head, out, tail, (x,)):
                        continue
                    matched.add((i, into, out))
                    a, b = int(pre_id[into]), int(pre_id[out])
                    for f in banned:
                        if node in (pu[f], pv[f]):
                            # A boundary edge of this super-node is only ever used in a passage
                            # through it: ban the one transition that drives x and then f.
                            bans = [(a, f)] if f == b else [(f, b)] if f == a else []
                        else:
                            bans = [(a, f), (b, f)]
                        for one, other in bans:
                            pairs.update({(one, other), (other, one)})
    return pairs, len(matched), len(passages)


def _along(
    view: _View,
    edge: int,
    internal: list[int],
    ins: list[tuple[int, int]],
    outs: list[tuple[int, int]],
    pre_id: NDArray[np.int64],
) -> set[int]:
    """`consolidate._along`: the boundary edges an internal edge's twins carry to."""
    road = view.signature[edge]
    same = [e for e in internal if view.signature[e] == road]
    ends = {x for e in same for x in (view.u[e], view.v[e])}
    ends |= {x for _, x in ins} | {x for _, x in outs}
    reach = _reachability(ends, [(view.u[e], view.v[e]) for e in same])
    u, v = view.u[edge], view.v[edge]
    boundary = [e for e, _ in [*ins, *outs]]
    on_road = [e for e in boundary if view.signature[e] == road]
    reached = [a for a, head in ins if a in on_road and u in reach[head]]
    reached += [b for b, tail in outs if b in on_road and tail in reach[v]]
    levels = [reached, on_road, boundary]
    heading = _heading(view, edge)
    for level in levels:
        ahead = [e for e in level if np.dot(_heading(view, e), heading) > 0]
        if ahead:
            return {int(pre_id[e]) for e in ahead}
    return {int(pre_id[e]) for e in next((level for level in levels if level), [])}


def _heading(view: _View, e: int) -> NDArray[np.float64]:
    """`consolidate._heading`: an edge's start to its end, without stage A's stubs."""
    graph = view.graph
    lo, hi = int(graph.coord_indptr[e]), int(graph.coord_indptr[e + 1])
    first = lo + (graph.node_kind[view.u[e]] != PLAIN)
    last = hi - 1 - (graph.node_kind[view.v[e]] != PLAIN)
    start = np.asarray((graph.coord_x[first], graph.coord_y[first]), dtype=float)
    end = np.asarray((graph.coord_x[last], graph.coord_y[last]), dtype=float)
    heading: NDArray[np.float64] = end - start
    return heading


def _relabel(
    view: _View,
    supers: list[tuple[set[int], int]],
    records: list[Any],
    sx: NDArray[np.float64],
    sy: NDArray[np.float64],
    kept: NDArray[np.int64],
    pu: NDArray[np.int64],
    pv: NDArray[np.int64],
    key: NDArray[np.int64],
    pairs: set[tuple[int, int]],
    restricted: Turns,
    stats: StageBStats,
) -> StageBGraph:
    """The graph numbered as `consolidate.relabel` numbers it: the stage A nodes left (plain and
    clusters) by their smallest member, then the super-nodes by theirs; edges by (u, v, key).
    The turn tables of the nodes left, `restricted` (stage A node and edge ids), join the
    super-junctions' transitions."""
    graph = view.graph
    n, s = view.n, len(supers)
    stats.twin_pairs = len(pairs) // 2
    in_super = np.zeros(n, dtype=np.bool_)
    for nodes, _ in supers:
        in_super[list(nodes)] = True
    left = np.flatnonzero(~in_super)
    first_left = graph.member_id[graph.member_indptr[left]]
    super_first = np.array([record[0][0] for record in records], dtype=np.int64)
    order = np.concatenate([left[np.argsort(first_left, kind="stable")],
                            n + np.argsort(super_first, kind="stable")])  # fmt: skip
    number = np.full(n + s, -1, dtype=np.int64)
    number[order] = np.arange(len(order))

    nu, nv = number[pu], number[pv]
    edge_order = np.lexsort((key, nv, nu))
    final = np.empty(len(kept), dtype=np.int64)  # kept index -> final edge id
    final[edge_order] = np.arange(len(kept))
    e_from = kept[edge_order]

    # Nodes in the final order.
    to_lonlat = pyproj.Transformer.from_crs(graph.crs_epsg, 4326, always_xy=True)
    slon, slat = to_lonlat.transform(sx, sy) if s else (np.empty(0), np.empty(0))
    kind = np.concatenate([graph.node_kind[left], [k for _, k in supers]]).astype(np.uint8)
    x = np.concatenate([graph.node_x[left], sx])
    y = np.concatenate([graph.node_y[left], sy])
    lon = np.concatenate([graph.node_lon[left], np.asarray(slon, dtype=np.float64)])
    lat = np.concatenate([graph.node_lat[left], np.asarray(slat, dtype=np.float64)])
    where = np.full(n + s, -1, dtype=np.int64)  # pre-relabel node -> row of the arrays above
    where[left] = np.arange(len(left))
    where[n + np.arange(s)] = len(left) + np.arange(s)
    rows = where[order]  # final node -> its row
    members = [
        graph.member_id[graph.member_indptr[k] : graph.member_indptr[k + 1]].tolist()
        for k in left.tolist()
    ] + [record[0] for record in records]
    member_lists = [members[r] for r in rows.tolist()]
    member_indptr = np.zeros(len(order) + 1, dtype=np.int64)
    np.cumsum([len(x) for x in member_lists], out=member_indptr[1:])

    # Edges: geometry extended to a super-node's position at each end on one.
    su_kept, sv_kept = pu >= n, pv >= n
    counts = graph.coord_indptr[kept + 1] - graph.coord_indptr[kept]
    starts_new = su_kept.astype(np.int64)
    total = counts + starts_new + sv_kept
    coord_indptr_pre = np.zeros(len(kept) + 1, dtype=np.int64)
    np.cumsum(total, out=coord_indptr_pre[1:])
    cx, cy = np.empty(coord_indptr_pre[-1]), np.empty(coord_indptr_pre[-1])
    src = np.repeat(graph.coord_indptr[kept] - (np.cumsum(counts) - counts), counts) + np.arange(
        counts.sum()
    )
    dst = np.repeat(coord_indptr_pre[:-1] + starts_new - (np.cumsum(counts) - counts), counts)
    dst = dst + np.arange(counts.sum())
    cx[dst], cy[dst] = graph.coord_x[src], graph.coord_y[src]
    head = coord_indptr_pre[:-1][su_kept]
    tail = coord_indptr_pre[1:][sv_kept] - 1
    cx[head], cy[head] = sx[pu[su_kept] - n], sy[pu[su_kept] - n]
    cx[tail], cy[tail] = sx[pv[sv_kept] - n], sy[pv[sv_kept] - n]
    length = graph.edge_length[kept].copy()
    extended = np.flatnonzero(su_kept | sv_kept)
    length[extended] = _lengths(cx, cy, coord_indptr_pre, extended)
    # In the final edge order.
    total_f = total[edge_order]
    coord_indptr = np.zeros(len(kept) + 1, dtype=np.int64)
    np.cumsum(total_f, out=coord_indptr[1:])
    take = np.repeat(coord_indptr_pre[:-1][edge_order] - (np.cumsum(total_f) - total_f), total_f)
    take = take + np.arange(total_f.sum())

    # Twins in final edge ids.
    twin_pairs = np.array(sorted((int(final[a]), int(final[b])) for a, b in pairs), dtype=np.int64)
    twin_pairs = twin_pairs.reshape(-1, 2)
    if len(twin_pairs):
        twin_pairs = twin_pairs[np.lexsort((twin_pairs[:, 1], twin_pairs[:, 0]))]
    twin_indptr = np.zeros(len(kept) + 1, dtype=np.int64)
    np.cumsum(np.bincount(twin_pairs[:, 0], minlength=len(kept)), out=twin_indptr[1:])

    # Super-junctions, by their final node ids.
    pre_id = np.full(view.m, -1, dtype=np.int64)
    pre_id[kept] = np.arange(len(kept))
    super_number = number[n + np.arange(s)]
    by_node = np.argsort(super_number, kind="stable")
    part_lists: list[tuple[int, ...]] = []
    internal_rows: list[tuple[int, int, int]] = []
    trans: list[tuple[int, int, int]] = []
    roundabouts = []
    part_indptr, internal_indptr = [0], [0]
    for i in by_node.tolist():
        _, parts, edges, canon, rings, transitions = records[i]
        part_lists.extend(parts)
        part_indptr.append(len(part_lists))
        internal_rows.extend((e, canon[view.u[e]], canon[view.v[e]]) for e in edges)
        internal_indptr.append(len(internal_rows))
        roundabouts.append(rings)
        node = int(super_number[i])
        trans.extend((node, int(final[pre_id[a]]), int(final[pre_id[b]])) for a, b in transitions)
    for stage_a_node, table in restricted.items():
        node = int(number[stage_a_node])
        trans.extend((node, int(final[pre_id[a]]), int(final[pre_id[b]])) for a, b in table)
    trans_array = np.array(sorted(trans), dtype=np.int32).reshape(-1, 3)
    part_member_indptr = np.zeros(len(part_lists) + 1, dtype=np.int64)
    np.cumsum([len(p) for p in part_lists], out=part_member_indptr[1:])
    internal = np.array(internal_rows, dtype=np.int64).reshape(-1, 3)
    stats.nodes = len(order)
    stats.edges = len(kept)
    return StageBGraph(
        stage_a=graph,
        node_kind=kind[rows],
        node_x=x[rows],
        node_y=y[rows],
        node_lon=lon[rows],
        node_lat=lat[rows],
        member_indptr=member_indptr,
        member_id=np.array([x for row in member_lists for x in row], dtype=np.int64),
        edge_u=nu[edge_order].astype(np.int32),
        edge_v=nv[edge_order].astype(np.int32),
        edge_key=key[edge_order],
        edge_from=e_from,
        edge_length=length[edge_order],
        coord_indptr=coord_indptr,
        coord_x=cx[take],
        coord_y=cy[take],
        twin_indptr=twin_indptr,
        twin_edges=twin_pairs[:, 1].astype(np.int32),
        super_node=super_number[by_node].astype(np.int32),
        super_roundabouts=np.array(roundabouts, dtype=np.int64),
        part_indptr=np.array(part_indptr, dtype=np.int64),
        part_member_indptr=part_member_indptr,
        part_member_id=np.array([x for p in part_lists for x in p], dtype=np.int64),
        internal_indptr=np.array(internal_indptr, dtype=np.int64),
        internal_edge=internal[:, 0],
        internal_u=internal[:, 1],
        internal_v=internal[:, 2],
        trans_node=trans_array[:, 0].copy(),
        trans_in=trans_array[:, 1].copy(),
        trans_out=trans_array[:, 2].copy(),
        restricted_node=np.array(sorted(number[list(restricted)].tolist()), dtype=np.int32),
    )
