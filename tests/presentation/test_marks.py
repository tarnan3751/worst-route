"""`marks`: where a pilgrimage visits its brand and a collecting route collects its item, one mark a
place, in route order, as the counters count them (`brand_counts`, `count_items`): a node's mark
at the node, an edge's at the middle of its shape, a bridge's at the middle of its run, a round
trip's start once and a run through it once, and on random walks over the lattice as many as the
counters' visits and bridges, and the places holding the other items."""

from dataclasses import replace

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from worst_route.engine.objectives.brand import brand_counts
from worst_route.engine.objectives.collect import ITEMS, CollectItem, count_items
from worst_route.graph.data import EdgeFlag, GraphData, NodeKind
from worst_route.graph.geometry import edge_points, encode, straight, xy_to_lonlat
from worst_route.graph.testing import edge_id
from worst_route.presentation.marks import Mark, marks

from ..engine.graphs import X0, Y0, edges_of, lattice

BRIDGE = {"flags": EdgeFlag.BRIDGE}


def town() -> GraphData:
    """A 6 × 6 lattice (node r * 6 + c, 100 m apart): a toll booth on 0–1, two level crossings on
    3–9, bridges on 1–2, 2–3 (one bridge split by a junction), 14–15 and round the block 20, 21,
    27, 26; a roundabout at node 9 and an interchange holding two rings at node 14; Ring Donuts at
    nodes 2, 9 and 30."""
    block = {(20, 21): BRIDGE, (21, 27): BRIDGE, (26, 27): BRIDGE, (20, 26): BRIDGE}
    roads = {
        (0, 1): {"toll_booths": 1},
        (3, 9): {"level_crossings": 2},
        (1, 2): BRIDGE,
        (2, 3): BRIDGE,
        (14, 15): BRIDGE,
        **block,
    }
    g = lattice(6, 6, roads=roads, brands={"Ring Donuts": [2, 9, 30]})
    kind, rings = g.node_kind.copy(), g.node_roundabouts.copy()
    kind[[9, 14]] = NodeKind.ROUNDABOUT, NodeKind.INTERCHANGE
    rings[[9, 14]] = 1, 2
    g = replace(g, node_kind=kind, node_roundabouts=rings)
    g.validate()
    return g


TOWN = town()
GEOMETRY = straight(TOWN.m)
# 0→1 toll, 1→2→3 one bridge, 3→9 two crossings, 9 a ring, 15→14 a bridge, 14 two rings
TOUR = [0, 1, 2, 3, 9, 15, 14, 8]


def found(
    nodes: list[int], *, item: CollectItem | None = None, brand: int | None = None
) -> list[Mark]:
    path = np.array(nodes, dtype=np.int32)
    return marks(TOWN, GEOMETRY, path, edges_of(TOWN, nodes), item=item, brand=brand)


def at(g: GraphData, x: float, y: float) -> tuple[float, float]:
    """(lat, lon) of a point in the build's CRS, to 7 decimals."""
    lon, lat = xy_to_lonlat(g, np.array([x]), np.array([y]))
    return round(float(lat[0]), 7), round(float(lon[0]), 7)


def node_at(v: int) -> tuple[float, float]:
    return at(TOWN, float(TOWN.node_x[v]), float(TOWN.node_y[v]))


def between(u: int, v: int) -> tuple[float, float]:
    x = (float(TOWN.node_x[u]) + float(TOWN.node_x[v])) / 2
    y = (float(TOWN.node_y[u]) + float(TOWN.node_y[v])) / 2
    return at(TOWN, x, y)


def places(found: list[Mark]) -> list[tuple[float, float]]:
    return [(m.lat, m.lon) for m in found]


def test_a_node_s_mark_is_where_the_node_is() -> None:
    v = np.arange(TOWN.n)
    lon, lat = xy_to_lonlat(TOWN, TOWN.node_x[v], TOWN.node_y[v])
    assert lon.tolist() == TOWN.node_lon.tolist() and lat.tolist() == TOWN.node_lat.tolist()


def test_each_item_is_marked_once_a_place_in_route_order() -> None:
    items = count_items(TOWN, np.array(TOUR), edges_of(TOWN, TOUR))
    assert items == {"toll_booth": 1, "level_crossing": 2, "roundabout": 3, "bridge": 2}
    toll = found(TOUR, item="toll_booth")
    assert toll == [Mark("toll_booth", *between(0, 1), None)]
    assert places(found(TOUR, item="level_crossing")) == [between(3, 9)]  # one edge, two crossings
    assert places(found(TOUR, item="roundabout")) == [node_at(9), node_at(14)]  # 14's two rings
    bridges = found(TOUR, item="bridge")  # the middle of 1→2→3 is node 2's place
    assert places(bridges) == [node_at(2), between(15, 14)]
    assert {(m.kind, m.label) for m in bridges} == {("bridge", None)}
    assert found(TOUR) == [] and found([0, 6, 12]) == []


def test_a_pilgrimage_marks_its_visits_labelled_with_the_brand() -> None:
    visits = found(TOUR, brand=0)
    assert visits == [
        Mark("brand", *node_at(2), "Ring Donuts"),
        Mark("brand", *node_at(9), "Ring Donuts"),
    ]
    tour = np.array(TOUR, dtype=np.int32)
    assert len(visits) == brand_counts(TOWN, 0, tour)["visits"]


def test_a_round_trip_s_start_and_a_run_through_it_count_once() -> None:
    # From node 2 east over the 2→3 bridge, up and round, back over the 1→2 bridge: one run,
    # entered at the end, its middle node 2; Ring Donuts at the start once.
    loop = [2, 3, 9, 8, 7, 1, 2]
    edges = edges_of(TOWN, loop)
    assert count_items(TOWN, np.array(loop), edges)["bridge"] == 1
    assert places(found(loop, item="bridge")) == [node_at(2)]
    assert places(found(loop, brand=0)) == [node_at(2), node_at(9)]
    # Starting on the 2→3 bridge and ending off a bridge: the run opens at the start.
    assert places(found([2, 3, 4, 10, 9, 8], item="bridge")) == [between(2, 3)]
    # A round trip round the bridged block counts no bridge, and has no mark.
    block = [20, 21, 27, 26, 20]
    assert count_items(TOWN, np.array(block), edges_of(TOWN, block))["bridge"] == 0
    assert found(block, item="bridge") == []


def test_an_edge_s_mark_is_at_the_middle_of_its_shape() -> None:
    # Edge 0→1 bent through (50, -100), each leg √(50² + 100²) long: its middle is the bend.
    lines = []
    for e in range(TOWN.m):
        u, v = int(TOWN.edge_u[e]), int(TOWN.edge_v[e])
        ends = [(TOWN.node_x[u], TOWN.node_y[u]), (TOWN.node_x[v], TOWN.node_y[v])]
        bend = [(X0 + 50.0, Y0 - 100.0)] if {u, v} == {0, 1} else []
        lines.append([ends[0], *bend, ends[1]])
    points = np.array([p for line in lines for p in line], dtype=np.float64)
    indptr = np.concatenate(([0], np.cumsum([len(line) for line in lines]))).astype(np.int64)
    bent = encode(TOWN, points, indptr)
    path = np.array([0, 1, 7], dtype=np.int32)
    [mark] = marks(TOWN, bent, path, edges_of(TOWN, [0, 1, 7]), item="toll_booth", brand=None)
    shape, _ = edge_points(TOWN, bent, np.array([edge_id(TOWN, 0, 1)]))
    assert (mark.lon, mark.lat) == tuple(np.round(shape[1], 7).tolist())


def ahead(v: int) -> list[int]:
    """The nodes one edge on from v."""
    out = TOWN.out_edges[TOWN.out_indptr[v] : TOWN.out_indptr[v + 1]]
    return [int(w) for w in TOWN.edge_v[out]]


@st.composite
def walks(draw: st.DrawFn) -> list[int]:
    """A walk over TOWN that never repeats a node, closed into a round trip when it can be."""
    walk = [draw(st.integers(0, TOWN.n - 1))]
    for _ in range(draw(st.integers(1, 30))):
        fresh = [v for v in ahead(walk[-1]) if v not in walk]
        if not fresh:
            break
        walk.append(fresh[draw(st.integers(0, len(fresh) - 1))])
    if len(walk) > 2 and walk[0] in ahead(walk[-1]) and draw(st.booleans()):
        walk.append(walk[0])
    return walk


@settings(max_examples=300, deadline=None)
@given(walks())
def test_marks_follow_the_counters_on_any_walk(walk: list[int]) -> None:
    nodes, edges = np.array(walk, dtype=np.int32), edges_of(TOWN, walk)
    counted = nodes[:-1] if walk[0] == walk[-1] else nodes
    items = count_items(TOWN, nodes, edges)
    assert len(found(walk, brand=0)) == brand_counts(TOWN, 0, nodes)["visits"]
    assert len(found(walk, item="bridge")) == items["bridge"]
    assert len(found(walk, item="roundabout")) == int((TOWN.node_roundabouts[counted] > 0).sum())
    assert len(found(walk, item="toll_booth")) == int((TOWN.edge_toll_booths[edges] > 0).sum())
    level = int((TOWN.edge_level_crossings[edges] > 0).sum())
    assert len(found(walk, item="level_crossing")) == level
    for item in ITEMS:
        assert len(found(walk, item=item)) <= items[item]
