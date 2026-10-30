"""The invariants the cleaned graph keeps, checked from its stored data.

They are what the validator's proof from original OSM ids (`docs/PLAN.md` §8.1, checks 3-5)
rests on. Each check works on the graph after any cleanup step; a node without `members` (the
simplified graph's nodes) stands for its own OSM id.
"""

from collections import Counter, defaultdict, deque
from typing import Any

import networkx as nx

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
    check_superjunctions(graph)
