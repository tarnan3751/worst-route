"""The invariants the cleaned graph keeps, checked from its stored data.

They are what the validator's proof from original OSM ids (`docs/PLAN.md` §8.1, checks 3-5)
rests on. Each check works on the graph after any cleanup step; a node without `members` (the
simplified graph's nodes) stands for its own OSM id. `check_build` checks the same invariants on
a saved build from its arrays alone, and `check_saved` that a build holds the enriched graph it
was saved from.
"""

from collections import Counter, defaultdict, deque
from typing import Any

import networkx as nx
import numpy as np

from worst_route.config import TIERS, HighwayClass
from worst_route.graph.data import GraphData, NodeKind
from worst_route.pipeline.build import ROUNDABOUT_JUNCTIONS

SUPER_KINDS = {"interchange", "roundabout"}


def members(graph: nx.MultiDiGraph, node: int) -> tuple[int, ...]:
    return tuple(graph.nodes[node].get("members", (node,)))


def osm_ids(raw: nx.MultiDiGraph) -> set[int]:
    """Every OSM node id of the raw graph that cleanup keeps track of (self-loops aside)."""
    ids = set(raw.nodes)
    for u, v, data in raw.edges(data=True):
        if u != v:
            ids.update(data["osm_nodes"])
    return ids


def check_partition(raw: nx.MultiDiGraph, graph: nx.MultiDiGraph, *, complete: bool) -> None:
    """(1) Every tracked OSM id is in one node's members or inside one road segment.

    An id may sit inside two edges only when they are the two directions of one road. With
    `complete`, every OSM id of the raw graph is accounted for (nothing was dropped yet);
    otherwise what remains is a subset.
    """
    seen: Counter[int] = Counter()
    for node in graph.nodes:
        seen.update(members(graph, node))
    assert max(seen.values(), default=1) == 1, "an OSM id is a member of two nodes"
    inside: dict[int, list[tuple[int, ...]]] = defaultdict(list)
    for _, _, data in graph.edges(data=True):
        for osm_id in data["osm_nodes"][1:-1]:
            inside[osm_id].append(data["osm_nodes"])
    for osm_id, runs in inside.items():
        assert osm_id not in seen, f"{osm_id} is both a member and inside an edge"
        assert len(runs) == 1 or (len(runs) == 2 and runs[0] == runs[1][::-1]), (
            f"{osm_id} is inside two road segments"
        )
    found = set(seen) | set(inside)
    expected = osm_ids(raw)
    if complete:
        assert found == expected
    else:
        assert found <= expected


def check_edges(graph: nx.MultiDiGraph) -> None:
    """(2) An edge starts and ends in its nodes' members; (3) its OSM ids never repeat."""
    for u, v, data in graph.edges(data=True):
        nodes = data["osm_nodes"]
        assert nodes[0] in members(graph, u) and nodes[-1] in members(graph, v)
        assert len(set(nodes)) == len(nodes), f"edge {u}->{v} repeats an OSM id"


def check_twins(graph: nx.MultiDiGraph) -> None:
    """(4) Twins are symmetric, sorted, between existing edges, never an edge and itself."""
    twins = graph.graph.get("twins", {})
    for edge, others in twins.items():
        assert graph.has_edge(*edge)
        assert list(others) == sorted(set(others)) and edge not in others
        for other in others:
            assert graph.has_edge(*other) and edge in twins[other]


def check_superjunctions(graph: nx.MultiDiGraph) -> None:
    """(5) Transitions name boundary edges of their node; (6) they are its internal reachability.

    The expected table is rebuilt from the record alone: the internal edges between parts, and
    each boundary edge's OSM end, which lies in one part.
    """
    tables: dict[int, dict[str, Any]] = graph.graph.get("superjunctions", {})
    supers = {n for n, kind in graph.nodes(data="kind") if kind in SUPER_KINDS}
    assert set(tables) == supers
    for node, record in tables.items():
        assert record["kind"] == graph.nodes[node]["kind"]
        assert record["members"] == graph.nodes[node]["members"]
        part_of = {osm_id: part[0] for part in record["parts"] for osm_id in part}
        inside = {m for edge in record["internal"] for m in edge["osm_nodes"][1:-1]}
        assert set(record["members"]) == set(part_of) | inside
        ring_edges = [e for e in record["internal"] if e.get("junction") in ROUNDABOUT_JUNCTIONS]
        rings = nx.Graph((e["u"], e["v"]) for e in ring_edges)
        assert record["roundabouts"] == nx.number_connected_components(rings)
        after: dict[int, list[int]] = defaultdict(list)
        for edge in record["internal"]:
            assert part_of[edge["osm_nodes"][0]] == edge["u"]
            assert part_of[edge["osm_nodes"][-1]] == edge["v"]
            after[edge["u"]].append(edge["v"])
        ins = [(u, v, k) for u, v, k in graph.in_edges(node, keys=True)]
        outs = [(u, v, k) for u, v, k in graph.out_edges(node, keys=True)]
        expected = []
        for into in ins:
            head = part_of[graph.edges[into]["osm_nodes"][-1]]
            reach, queue = {head}, deque([head])
            while queue:
                for nxt in after[queue.popleft()]:
                    if nxt not in reach:
                        reach.add(nxt)
                        queue.append(nxt)
            for out in outs:
                if part_of[graph.edges[out]["osm_nodes"][0]] in reach:
                    expected.append((into, out))
        assert list(record["transitions"]) == sorted(expected)


def check_all(raw: nx.MultiDiGraph, graph: nx.MultiDiGraph, *, complete: bool = True) -> None:
    check_partition(raw, graph, complete=complete)
    check_edges(graph)
    check_twins(graph)
    check_superjunctions(graph)


def _run(indptr: np.ndarray, values: np.ndarray, i: int) -> list[int]:
    return values[indptr[i] : indptr[i + 1]].tolist()  # type: ignore[no-any-return]


def check_build(data: GraphData) -> None:
    """The invariants on a saved build's arrays, with the format's own checks (`validate`).

    (1) An id inside an edge is no node's member and lies inside one road unit only (`validate`
    holds every member to one node); (2) an edge starts and ends in its nodes' members; (3) it
    repeats no id; (4) twins are symmetric (`validate`); (5) transitions sit at super-junctions
    and name their edges (`validate`). A unit is one edge, or the two directions of one road,
    named by the smaller id. The tier masks are the tier's classes between kept nodes.
    """
    data.validate()
    owner = np.repeat(np.arange(data.n), np.diff(data.member_indptr))
    member_of = dict(zip(data.member_osm_ids.tolist(), owner.tolist(), strict=True))
    unit_inside: dict[int, int] = {}
    runs: dict[int, list[tuple[int, ...]]] = defaultdict(list)
    for e in range(data.m):
        ids = _run(data.osmnode_indptr, data.osmnode_ids, e)
        assert len(set(ids)) == len(ids), f"edge {e} repeats an OSM id"
        assert member_of[ids[0]] == data.edge_u[e] and member_of[ids[-1]] == data.edge_v[e]
        unit = int(data.edge_unit[e])
        for osm_id in ids[1:-1]:
            assert osm_id not in member_of, f"{osm_id} is a member and inside edge {e}"
            assert unit_inside.setdefault(osm_id, unit) == unit, f"{osm_id} inside two roads"
        runs[unit].append(tuple(ids))
    for unit, edges in runs.items():
        assert len(edges) in (1, 2) and (len(edges) == 1 or edges[0] == edges[1][::-1])
        assert unit == min(np.flatnonzero(data.edge_unit == unit))
    supers = {NodeKind.INTERCHANGE, NodeKind.ROUNDABOUT}
    assert all(NodeKind(k) in supers for k in data.node_kind[data.trans_node].tolist())
    for t, classes in enumerate(TIERS):
        in_tier = np.isin(data.edge_highway, sorted(classes))
        kept = data.tier_node_mask[t]
        assert np.array_equal(
            data.tier_edge_mask[t], in_tier & kept[data.edge_u] & kept[data.edge_v]
        )
        assert ((data.edge_tier <= t) == in_tier).all()


def check_saved(graph: nx.MultiDiGraph, data: GraphData) -> None:
    """The build holds the enriched graph: its nodes (with each super-junction's roundabout rings),
    edges, twins, transitions and brands (each at nodes a route can pass through), keys as ids."""
    keys = sorted(graph.edges(keys=True))
    edge_id = {key: e for e, key in enumerate(keys)}
    assert (data.n, data.m) == (graph.number_of_nodes(), len(keys))
    for v, d in graph.nodes(data=True):
        position = (data.node_x[v], data.node_y[v], data.node_lon[v], data.node_lat[v])
        assert position == (d["x"], d["y"], d["lon"], d["lat"])
        assert _run(data.member_indptr, data.member_osm_ids, v) == list(d["members"])
        assert NodeKind(data.node_kind[v]).name.lower() == d["kind"]
        assert data.node_delay_s[v] == np.float32(d["delay_s"])
        assert bool(data.node_pass_through[v]) == d["pass_through"]
        record = graph.graph["superjunctions"].get(v)
        assert data.node_roundabouts[v] == (0 if record is None else record["roundabouts"])

    def text(table: list[str], i: int) -> str | None:
        return None if i < 0 else table[i]

    for e, (u, v, k) in enumerate(keys):
        d = graph.edges[u, v, k]
        assert (data.edge_u[e], data.edge_v[e]) == (u, v)
        assert _run(data.osmnode_indptr, data.osmnode_ids, e) == list(d["osm_nodes"])
        assert _run(data.way_indptr, data.way_osm_ids, e) == list(d["osmids"])
        for name, attribute in (
            ("edge_length_m", "length"),
            ("edge_speed_kmh", "speed_kmh"),
            ("edge_time_s", "time_s"),
        ):
            assert getattr(data, name)[e] == np.float32(d[attribute])
        assert HighwayClass(data.edge_highway[e]).name.lower() == d["highway"]
        assert text(data.strings.names, data.edge_name[e]) == d.get("name")
        assert text(data.strings.refs, data.edge_ref[e]) == d.get("ref")
        assert text(data.strings.identities, data.edge_identity[e]) == d["identity"]
        assert data.edge_unit[e] == edge_id[d["unit"]]
        saved = (
            data.edge_flags[e],
            data.edge_toll_booths[e],
            data.edge_level_crossings[e],
            data.edge_bearing_in[e],
            data.edge_bearing_out[e],
        )
        attributes = ("flags", "toll_booths", "level_crossings", "bearing_in", "bearing_out")
        assert saved == tuple(d[a] for a in attributes)
        coords = data.coords[data.coord_indptr[e] : data.coord_indptr[e + 1]]
        assert len(coords) == len(d["geometry"].coords)
        for end, node in ((coords[0], u), (coords[-1], v)):  # geometry runs node to node
            assert abs(end[0] - data.node_lon[node]) < 1e-5
            assert abs(end[1] - data.node_lat[node]) < 1e-5
    twins = graph.graph["twins"]
    for e, key in enumerate(keys):
        row = _run(data.twin_indptr, data.twin_edges, e)
        assert row == sorted(edge_id[t] for t in twins.get(key, ()))
    rows = {
        (node, edge_id[a], edge_id[b])
        for node, record in graph.graph["superjunctions"].items()
        for a, b in record["transitions"]
    }
    table = np.column_stack([data.trans_node, data.trans_in, data.trans_out]).tolist()
    assert {tuple(row) for row in table} == rows and len(table) == len(rows)
    brands = graph.graph["brands"]
    assert data.strings.brands == [name for name, _, _ in brands]
    assert data.strings.brand_wikidata == [wikidata for _, wikidata, _ in brands]
    for b, (_, _, at) in enumerate(brands):
        nodes = data.nodes_of_brand(b)
        located = _run(data.brand_indptr, data.brand_locations, b)
        assert list(zip(nodes.tolist(), located, strict=True)) == at
        assert data.node_pass_through[nodes].all()
