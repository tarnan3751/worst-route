"""The invariants the cleaned graph keeps, checked from its stored data.

They are what the validator's proof from original OSM ids (`docs/PLAN.md` §8.1, checks 3-5)
rests on. Each check works on the graph after any cleanup step; a node without `members` (the
simplified graph's nodes) stands for its own OSM id. `check_build` checks the same invariants on
a saved build from its arrays alone, and `check_saved` that a build and its geometry hold the
enriched graph they were saved from.
"""

import math
from collections import Counter, defaultdict, deque
from collections.abc import Iterable
from typing import Any

import networkx as nx
import numpy as np
import shapely

from worst_route.config import GEOMETRY_SIMPLIFY_M, RESTRICTION_ONLY, TIERS, HighwayClass
from worst_route.graph.backend import TURN_CLASS, TURN_LEFT, TURN_RIGHT, TURN_STRAIGHT, TURN_U
from worst_route.graph.data import GraphData, NodeKind
from worst_route.graph.geometry import Geometry, edge_xy
from worst_route.pipeline.build import ROUNDABOUT_JUNCTIONS
from worst_route.pipeline.simplify import simplify

SUPER_KINDS = {"interchange", "roundabout"}
TURN_WORDS = {"left": TURN_LEFT, "right": TURN_RIGHT, "straight": TURN_STRAIGHT, "u": TURN_U}

type Run = tuple[tuple[int, ...], tuple[int, ...]]  # an edge by its OSM nodes and its ways
type Pairs = set[tuple[Run, Run]]


def run_of(data: dict[str, Any]) -> Run:
    return tuple(data["osm_nodes"]), tuple(data["osmids"])


def expected_turns(
    raw: nx.MultiDiGraph, graph: nx.MultiDiGraph, rows: Iterable[dict[str, Any]]
) -> dict[int, Pairs]:
    """For each node of the cleaned graph holding the via node of a restriction in `rows` (the
    filter's table), the (in, out) pairs it should allow, edges named by `run_of`: a brute force
    over the simplified graph made from `raw`, apart from the pipeline's own code. A rule bans,
    at its via node, the moves from an edge arriving on a `from` way to one leaving on a `to` way
    (any other, for an `only_*` value); where a way passes through the via node (two of its edges
    arrive there, or leave) it concerns only the pairs whose turn is the one the value names, by
    the bearings of the segments at the via node, an `only_*` value leaving the edges from without
    such a pair free, and it is void when none turns so or the value names none. A
    stage A node allows the pairs a walk through the edges it absorbed joins with no banned move,
    and those no walk joins; a super-junction, the pairs a walk through its parts joins, each
    part's moves those it allows; a node outside the super-junctions allows too the U-turns back
    over the OSM nodes an edge came by."""
    simplified = simplify(raw)[0]
    ends: dict[Run, tuple[int, int]] = {}
    bearings: dict[Run, tuple[int, int]] = {}  # leaving its first node, arriving at its last
    leaving: dict[int, list[Run]] = defaultdict(list)
    arriving: dict[int, list[Run]] = defaultdict(list)
    for _, _, d in simplified.edges(data=True):
        key = run_of(d)
        assert key not in ends, key
        ends[key] = (d["osm_nodes"][0], d["osm_nodes"][-1])
        (x0, y0), (x1, y1) = d["geometry"].coords[:2]
        (x2, y2), (x3, y3) = d["geometry"].coords[-2:]
        bearings[key] = (_degrees(x1 - x0, y1 - y0), _degrees(x3 - x2, y3 - y2))
        leaving[ends[key][0]].append(key)
        arriving[ends[key][1]].append(key)
    junctions = set(simplified.nodes)
    live: dict[int, list[tuple[bool, dict[Run, set[Run]]]]] = defaultdict(list)
    for row in rows:
        via, value = row["via_node"], row["restriction"]
        if via not in junctions:
            continue
        froms = [e for e in arriving[via] if e[1][-1] in row["from_ways"]]
        tos = [e for e in leaving[via] if e[1][0] in row["to_ways"]]
        if not froms or not tos:
            continue
        if len({e[1][-1] for e in froms}) == len(froms) and len({e[1][0] for e in tos}) == len(tos):
            moves = {a: set(tos) for a in froms}
        else:
            turn = TURN_WORDS.get(value.split("_")[1])
            moves = {}
            for a in froms:
                turning = {
                    b for b in tos if TURN_CLASS[(bearings[b][0] - bearings[a][1]) % 360] == turn
                }
                if turn is not None and turning:
                    moves[a] = turning
            if not moves:
                continue
        live[via].append((value in RESTRICTION_ONLY, moves))

    def move_ok(at: int, a: Run, b: Run) -> bool:
        return not any(a in moves and (b in moves[a]) != only for only, moves in live.get(at, ()))

    def stage_a_node(members: Iterable[int]) -> tuple[list[Run], list[Run], Pairs]:
        held = set(members) & junctions
        ins = [e for x in held for e in arriving[x] if ends[e][0] not in held]
        outs = [e for x in held for e in leaving[x] if ends[e][1] not in held]
        absorbed = {e for x in held for e in leaving[x] if ends[e][1] in held}

        def walk(a: Run, check: bool) -> set[Run]:
            got, seen, queue = set(), {a}, deque([a])
            while queue:
                e = queue.popleft()
                x = ends[e][1]
                for f in leaving[x]:
                    if check and not move_ok(x, e, f):
                        continue
                    if f in absorbed:
                        if f not in seen:
                            seen.add(f)
                            queue.append(f)
                    elif ends[f][1] not in held:
                        got.add(f)
            return got

        table = set()
        for a in ins:
            free, kept = walk(a, False), walk(a, True)
            table |= {(a, b) for b in outs if b in kept or b not in free}
        return ins, outs, table

    vias = set(live)
    expected = {}
    for n, members in graph.nodes(data="members"):
        if not set(members) & vias:
            continue
        every = {
            (run_of(d), run_of(e))
            for _, _, d in graph.in_edges(n, data=True)
            for _, _, e in graph.out_edges(n, data=True)
        }
        record = graph.graph["superjunctions"].get(n)
        if record is None:
            ins, outs, table = stage_a_node(members)
            back = {(a, b) for a in ins for b in outs if b[0] == a[0][::-1]}
            expected[n] = (table | back) & every
            continue
        parts = [set(p) & junctions for p in record["parts"]]
        part_of = {x: i for i, p in enumerate(parts) for x in p}
        tables = [stage_a_node(p)[2] for p in parts]
        inner = {
            e
            for x in part_of
            for e in leaving[x]
            if ends[e][1] in part_of and part_of[ends[e][1]] != part_of[x]
        }
        allowed = set()
        for a in {e for x in part_of for e in arriving[x] if ends[e][0] not in part_of}:
            seen, queue = {a}, deque([a])
            while queue:
                e = queue.popleft()
                p = part_of[ends[e][1]]
                for f in (f for y in parts[p] for f in leaving[y] if (e, f) in tables[p]):
                    if f in inner:
                        if f not in seen:
                            seen.add(f)
                            queue.append(f)
                    elif ends[f][1] not in part_of:
                        allowed.add((a, f))
        expected[n] = allowed & every
    return expected


def _degrees(dx: float, dy: float) -> int:
    """Whole degrees clockwise from north."""
    return round(math.degrees(math.atan2(dx, dy))) % 360


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


def check_superjunctions(graph: nx.MultiDiGraph, turns: dict[int, Pairs] | None = None) -> None:
    """(5) Transitions name boundary edges of their node; (6) they are its internal reachability,
    or, at a super-junction holding the via node of a turn restriction, the pairs `turns` (from
    `expected_turns`) gives it.

    The expected table is rebuilt from the record alone: the internal edges between parts, and
    each boundary edge's OSM end, which lies in one part.
    """
    turns = turns or {}
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
        if node in turns:
            allowed = {(run_of(graph.edges[a]), run_of(graph.edges[b])) for a, b in expected}
            transitions = record["transitions"]
            assert {(run_of(graph.edges[a]), run_of(graph.edges[b])) for a, b in transitions} == (
                turns[node]
            ), f"super-junction {node}'s transitions"
            assert turns[node] <= allowed and list(transitions) == sorted(transitions)
        else:
            assert list(record["transitions"]) == sorted(expected)


def check_restricted(graph: nx.MultiDiGraph, turns: dict[int, Pairs] | None = None) -> None:
    """(7) A restricted node's table (`G.graph["restricted"]`, present only when one has a table)
    is at a plain or cluster node, sorted, and names its own in- and out-edges; and a plain or
    cluster node holding the via node of a turn restriction allows the pairs `turns` (from
    `expected_turns`) gives it, all of them when it has no table."""
    turns = turns or {}
    restricted: dict[int, tuple[Any, ...]] = graph.graph.get("restricted", {})
    assert "restricted" not in graph.graph or restricted
    assert list(restricted) == sorted(restricted)
    for node, transitions in restricted.items():
        assert graph.nodes[node]["kind"] not in SUPER_KINDS
        assert list(transitions) == sorted(set(transitions))
        for (_, head, _), (tail, _, _) in transitions:
            assert head == tail == node
    for node, pairs in turns.items():
        if graph.nodes[node]["kind"] in SUPER_KINDS:
            continue
        ins, outs = graph.in_edges(node, keys=True), graph.out_edges(node, keys=True)
        table = restricted.get(node, [(a, b) for a in ins for b in outs])
        got = {(run_of(graph.edges[a]), run_of(graph.edges[b])) for a, b in table}
        assert got == pairs, f"node {node}'s turn table"


def check_all(
    raw: nx.MultiDiGraph,
    graph: nx.MultiDiGraph,
    *,
    complete: bool = True,
    restrictions: list[dict[str, Any]] | None = None,
) -> None:
    """Every invariant; `restrictions` the rows of the restrictions table cleanup applied."""
    check_partition(raw, graph, complete=complete)
    check_edges(graph)
    check_twins(graph)
    turns = expected_turns(raw, graph, restrictions) if restrictions else None
    check_superjunctions(graph, turns)
    check_restricted(graph, turns)


def lost_through(graph: nx.MultiDiGraph) -> list[tuple[int, int, int]]:
    """Each (super-node, in-edge key) whose road cannot be driven straight through it: the road
    (edges of its class and ref, each continuing the one before it OSM node to node, never back
    the way it came; every branch followed) leaves the super-node and comes back to it within four
    edges, and a pass from the in-edge to an edge the road leaves by after coming back is not in
    its table. The stage B defects `consolidate._carriageway_rejoins` and the pockets remove
    where they can."""
    tables = {v: set(r["transitions"]) for v, r in graph.graph["superjunctions"].items()}

    def onwards(edge: tuple[int, int, int]) -> list[tuple[int, int, int]]:
        data = graph.edges[edge]
        return [
            (edge[1], w, k)
            for _, w, k, d in graph.out_edges(edge[1], keys=True, data=True)
            if d["osm_nodes"][0] == data["osm_nodes"][-1]
            and d["osm_nodes"][1] != data["osm_nodes"][-2]
            and d["highway"] == data["highway"]
            and d.get("ref") == data.get("ref")
        ]

    lost = set()
    for v, table in tables.items():
        for u, _, k in graph.in_edges(v, keys=True):
            edge = (u, v, k)
            frontier = [(step, 1) for step in onwards(edge)]
            while frontier:
                step, depth = frontier.pop()
                if step[1] == v:
                    if any((edge, after) not in table for after in onwards(step)):
                        lost.add((v, u, k))
                elif depth < 4:
                    frontier.extend((after, depth + 1) for after in onwards(step))
    return sorted(lost)


def _run(indptr: np.ndarray, values: np.ndarray, i: int) -> list[int]:
    return values[indptr[i] : indptr[i + 1]].tolist()  # type: ignore[no-any-return]


def check_build(data: GraphData) -> None:
    """The invariants on a saved build's arrays, with the format's own checks (`validate`).

    (1) Every original OSM node id is a member of one node only (`validate`); (2) twins are
    symmetric (`validate`); (3) a transition names edges that meet at its node (`validate`):
    rows sit at super-junctions and at the nodes a turn restriction gave a table. A unit is one
    edge, or the edges of one road in either direction (its two directions, and every copy of it
    when it is mapped more than once), named by the smallest id. The tier masks are the tier's
    classes between kept nodes. What lies inside the edges (no member, one road unit each) is
    `check_saved`'s, from the stage: the build does not keep it.
    """
    data.validate()
    runs: dict[int, list[int]] = defaultdict(list)
    for e in range(data.m):
        runs[int(data.edge_unit[e])].append(e)
    for unit, edges in runs.items():
        assert unit == min(edges)
        ends = {(int(data.edge_u[e]), int(data.edge_v[e])) for e in edges}
        u, v = int(data.edge_u[unit]), int(data.edge_v[unit])
        assert ends <= {(u, v), (v, u)}
    for t, classes in enumerate(TIERS):
        in_tier = np.isin(data.edge_highway, sorted(classes))
        kept = data.tier_node_mask[t]
        assert np.array_equal(
            data.tier_edge_mask[t], in_tier & kept[data.edge_u] & kept[data.edge_v]
        )
        assert ((data.edge_tier <= t) == in_tier).all()


def check_saved(graph: nx.MultiDiGraph, data: GraphData, geometry: Geometry) -> None:
    """The build holds the enriched graph: its nodes (with each super-junction's roundabout rings),
    edges, twins, transitions and brands (each at nodes a route can pass through), keys as ids;
    its geometry draws each edge from its tail to its head within the simplification's tolerance
    and the metre's rounding (half a metre on each axis) of the edge's shape. And what the
    validator's proof from members assumes of the OSM nodes along the edges, which the build
    does not keep: an edge starts and ends in its nodes' members and repeats no id, an id inside
    an edge is no node's member and lies inside one road unit only; a unit's edges run over the
    same ids or the same ids reversed, and the edges over one run of ids, either way, are one
    unit."""
    keys = sorted(graph.edges(keys=True))
    edge_id = {key: e for e, key in enumerate(keys)}
    assert (data.n, data.m) == (graph.number_of_nodes(), len(keys))
    for v, d in graph.nodes(data=True):
        assert (data.node_x[v], data.node_y[v]) == (d["x"], d["y"])
        # lon/lat are derived from x/y: the stored ones' up to PROJ's round trip
        assert abs(data.node_lon[v] - d["lon"]) < 1e-9 and abs(data.node_lat[v] - d["lat"]) < 1e-9
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
    member_of = {m: v for v, held in graph.nodes(data="members") for m in held}
    unit_inside: dict[int, int] = {}
    runs: dict[int, list[list[int]]] = defaultdict(list)
    unit_of_run: dict[tuple[int, ...], int] = {}
    for e, key in enumerate(keys):
        ids = list(graph.edges[key]["osm_nodes"])
        assert len(set(ids)) == len(ids), f"edge {e} repeats an OSM id"
        assert member_of[ids[0]] == data.edge_u[e] and member_of[ids[-1]] == data.edge_v[e]
        unit = int(data.edge_unit[e])
        for osm_id in ids[1:-1]:
            assert osm_id not in member_of, f"{osm_id} is a member and inside edge {e}"
            assert unit_inside.setdefault(osm_id, unit) == unit, f"{osm_id} inside two roads"
        runs[unit].append(ids)
        run = tuple(min(ids, ids[::-1]))
        assert unit_of_run.setdefault(run, unit) == unit, f"edge {e}: its road in two units"
    assert all(each in (r[0], r[0][::-1]) for r in runs.values() for each in r)
    xy, counts = edge_xy(data, geometry, np.arange(data.m))
    starts = np.cumsum(counts) - counts
    assert (xy[starts] == np.column_stack([data.node_x, data.node_y])[data.edge_u]).all()
    assert (
        xy[starts + counts - 1] == np.column_stack([data.node_x, data.node_y])[data.edge_v]
    ).all()
    drawn = shapely.linestrings(xy, indices=np.repeat(np.arange(data.m), counts))
    shapes = np.asarray([graph.edges[key]["geometry"] for key in keys], dtype=object)
    assert (shapely.hausdorff_distance(drawn, shapes) <= GEOMETRY_SIMPLIFY_M + 0.5 * 2**0.5).all()
    twins = graph.graph["twins"]
    for e, key in enumerate(keys):
        row = _run(data.twin_indptr, data.twin_edges, e)
        assert row == sorted(edge_id[t] for t in twins.get(key, ()))
    tables = {v: r["transitions"] for v, r in graph.graph["superjunctions"].items()}
    tables |= graph.graph.get("restricted", {})
    rows = {(node, edge_id[a], edge_id[b]) for node, pairs in tables.items() for a, b in pairs}
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
