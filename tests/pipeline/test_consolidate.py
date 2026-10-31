import math
import random
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pyproj
import pytest
import shapely

from tests.pipeline.invariants import check_all, lost_through
from tests.pipeline.osm import write_osm
from worst_route.config import (
    STAGE_A_TOLERANCE_ARTERIAL_M,
    STAGE_A_TOLERANCE_M,
    STAGE_A_TOLERANCE_ROUNDABOUT_M,
)
from worst_route.pipeline import consolidate
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.carriageways import find_twins
from worst_route.pipeline.consolidate import (
    BUFFER_QUAD_SEGS,
    StageAStats,
    StageBStats,
    _absorb_pockets,
    _edges_within,
    _incident,
    _is_junction_edge,
    _ramp_clusters,
    _road_keys,
    _road_signatures,
    buffer_groups,
    relabel,
    stage_a,
    stage_a_tolerances,
    stage_b,
)
from worst_route.pipeline.simplify import simplify

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
UTM_10N = 32610
ALL_FIXTURES = [
    "grid",
    "exclusions",
    "oneway",
    "culdesac",
    "bbox",
    "refchange",
    "overpass",
    "roundabout",
    "divided",
    "diamond",
    "cloverleaf",
]


def raw(source: str | Path) -> nx.MultiDiGraph:
    path = source if isinstance(source, Path) else FIXTURES / f"{source}.osm"
    return build_raw_graph(path, UTM_10N)[0]


def through_a(source: str | Path) -> tuple[nx.MultiDiGraph, nx.MultiDiGraph, StageAStats]:
    before = raw(source)
    merged, stats = stage_a(simplify(before)[0])
    return before, merged, stats


def through_b(source: str | Path) -> tuple[nx.MultiDiGraph, nx.MultiDiGraph]:
    before, merged, _ = through_a(source)
    return before, stage_b(merged)[0]


def node_of(graph: nx.MultiDiGraph, osm_id: int) -> int:
    return next(n for n, members in graph.nodes(data="members") if osm_id in members)


def supers(graph: nx.MultiDiGraph) -> dict[int, dict]:
    return graph.graph["superjunctions"]


def pairs_by_osm(graph: nx.MultiDiGraph, node: int) -> set[tuple[tuple[int, int], tuple[int, int]]]:
    """A super-node's allowed pairs, each edge named by the (first, last) OSM ids it runs."""

    def ends(edge: tuple[int, int, int]) -> tuple[int, int]:
        nodes = graph.edges[edge]["osm_nodes"]
        return nodes[0], nodes[-1]

    return {(ends(a), ends(b)) for a, b in supers(graph)[node]["transitions"]}


def boundary(graph: nx.MultiDiGraph, node: int) -> set[tuple[tuple[int, int], tuple[int, int]]]:
    """Every (in, out) pair of a node, edges named as in `pairs_by_osm`."""
    ins = [d["osm_nodes"] for _, _, d in graph.in_edges(node, data=True)]
    outs = [d["osm_nodes"] for _, _, d in graph.out_edges(node, data=True)]
    return {((a[0], a[-1]), (b[0], b[-1])) for a in ins for b in outs}


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_invariants_after_each_stage(name: str) -> None:
    before, merged, _ = through_a(name)
    check_all(before, merged)
    graph, stats = stage_b(merged)
    check_all(before, graph)
    assert list(graph.nodes) == list(range(graph.number_of_nodes())) and stats.nodes == len(graph)


# Stage A ----------------------------------------------------------------------------------


def test_stage_a_tolerances() -> None:
    tolerances = stage_a_tolerances(simplify(raw("divided"))[0])
    assert tolerances[31] == tolerances[50] == STAGE_A_TOLERANCE_ARTERIAL_M  # on the primary
    assert tolerances[60] == tolerances[90] == STAGE_A_TOLERANCE_M  # residential only
    tolerances = stage_a_tolerances(simplify(raw("roundabout"))[0])
    assert {tolerances[n] for n in (10, 12, 14, 16)} == {STAGE_A_TOLERANCE_ROUNDABOUT_M}
    assert tolerances[20] == STAGE_A_TOLERANCE_M


def test_a_roundabout_beats_an_arterial(tmp_path: Path) -> None:
    text = (FIXTURES / "roundabout.osm").read_text()
    primary = text.replace('<tag k="highway" v="tertiary"/>', '<tag k="highway" v="primary"/>')
    assert primary != text
    (tmp_path / "ring.osm").write_text(primary)
    tolerances = stage_a_tolerances(simplify(raw(tmp_path / "ring.osm"))[0])
    assert {tolerances[n] for n in (10, 12, 14, 16)} == {STAGE_A_TOLERANCE_ROUNDABOUT_M}


def test_divided_crossing_merges_into_one_cluster() -> None:
    before, merged, stats = through_a("divided")
    assert stats == StageAStats(
        clusters=1,
        nodes_merged=4,
        edges_absorbed=4,
        cluster_sizes={4: 1},
        nodes=15,
        edges=25,
    )
    crossing = node_of(merged, 50)
    data = merged.nodes[crossing]
    assert set(data) == {"x", "y", "lon", "lat", "kind", "members"}
    assert data["kind"] == "cluster"
    assert data["members"] == (50, 51, 52, 53, 54)  # 54 lay inside an absorbed edge
    to_lonlat = pyproj.Transformer.from_crs(UTM_10N, 4326, always_xy=True)
    assert (data["lon"], data["lat"]) == pytest.approx(to_lonlat.transform(data["x"], data["y"]))
    for n in (50, 51, 52, 53):  # the cluster sits in the middle of the square
        assert math.dist((data["x"], data["y"]), (before.nodes[n]["x"], before.nodes[n]["y"])) < 20
    plain = merged.nodes[node_of(merged, 31)]
    assert plain == {**before.nodes[31], "kind": "plain", "members": (31,)}
    assert merged.graph == before.graph


def test_edges_reach_the_cluster() -> None:
    before, merged, _ = through_a("divided")
    crossing = node_of(merged, 50)
    position = (merged.nodes[crossing]["x"], merged.nodes[crossing]["y"])
    for _, v, data in [
        *merged.in_edges(crossing, data=True),
        *merged.out_edges(crossing, data=True),
    ]:
        assert "u_original" not in data and "v_original" not in data
        coords = list(data["geometry"].coords)
        assert coords[-1 if v == crossing else 0] == pytest.approx(position)
        assert data["length"] == pytest.approx(data["geometry"].length)
    eastbound = next(
        d for _, _, d in merged.in_edges(crossing, data=True) if d["osm_nodes"][0] == 31
    )
    assert eastbound["osm_nodes"] == (31, 73, 50)  # unchanged; the geometry gains one point
    assert len(eastbound["geometry"].coords) == 4


def test_a_cluster_sits_at_the_centroid_of_its_own_buffers() -> None:
    before, merged, _ = through_a("divided")
    simplified = simplify(before)[0]
    tolerances = stage_a_tolerances(simplified)
    members = (50, 51, 52, 53)
    xy = [(simplified.nodes[n]["x"], simplified.nodes[n]["y"]) for n in members]
    radii = [tolerances[n] for n in members]
    buffers = shapely.buffer(shapely.points(xy), radii, quad_segs=BUFFER_QUAD_SEGS)
    centre = shapely.union_all(buffers).centroid
    crossing = node_of(merged, 50)
    assert (merged.nodes[crossing]["x"], merged.nodes[crossing]["y"]) == (centre.x, centre.y)
    for u, _, data in [
        *merged.in_edges(crossing, data=True),
        *merged.out_edges(crossing, data=True),
    ]:
        assert data["geometry"].coords[0 if u == crossing else -1] == (centre.x, centre.y)
        assert data["length"] == data["geometry"].length


def _near_pair(tmp_path: Path, overpass: bool) -> Path:
    """Junctions 2 (on a primary, 15 m buffer) and 3 (residential, 8 m) joined by a 10 m street,
    and, with `overpass`, junction 7 of an unconnected road 15 m north of 3, whose buffer meets
    both of theirs."""
    nodes = {1: (-100.0, 0.0), 2: (0.0, 0.0), 3: (10.0, 0.0), 4: (110.0, 0.0)}
    nodes |= {5: (0.0, 100.0), 6: (10.0, -100.0)}
    residential = {"highway": "residential", "name": "Pine Street"}
    ways = [
        (1, [1, 2], {"highway": "primary", "name": "Main Street"}),
        (2, [2, 3, 4], residential),
        (3, [2, 5], {"highway": "residential", "name": "Oak Street"}),
        (4, [3, 6], {"highway": "residential", "name": "Elm Street"}),
    ]
    if overpass:
        nodes |= {7: (10.0, 15.0), 8: (-100.0, 15.0), 9: (100.0, 15.0), 10: (10.0, 115.0)}
        ways += [
            (5, [8, 7, 9], {"highway": "residential", "name": "High Street", "bridge": "yes"}),
            (6, [7, 10], {"highway": "residential", "name": "Ash Street"}),
        ]
    return write_osm(tmp_path / "pair.osm", nodes, ways)


@pytest.mark.parametrize("overpass", [False, True], ids=["whole group", "split group"])
def test_a_cluster_sits_at_its_buffers_or_at_its_points(tmp_path: Path, overpass: bool) -> None:
    before, merged, stats = through_a(_near_pair(tmp_path, overpass))
    simplified = simplify(before)[0]
    tolerances = stage_a_tolerances(simplified)
    assert (tolerances[2], tolerances[3]) == (STAGE_A_TOLERANCE_ARTERIAL_M, STAGE_A_TOLERANCE_M)
    junctions = [n for n in (2, 3, 7) if n in simplified]  # 7 with the overpass only
    xy = np.array([(simplified.nodes[n]["x"], simplified.nodes[n]["y"]) for n in junctions])
    radii = np.array([tolerances[n] for n in junctions])
    # One group: 2's and 3's buffers, and with the overpass 7's, which meets both.
    assert len(set(buffer_groups(xy, radii).tolist())) == 1
    assert stats.cluster_sizes == {2: 1}  # no short edge reaches 7: only 2 and 3 merge
    pair = merged.nodes[node_of(merged, 2)]
    assert node_of(merged, 3) == node_of(merged, 2) and pair["kind"] == "cluster"
    # A split group sits at its members' points, as OSMnx places it; the whole group at its
    # buffers, nearer the wider one than the midpoint.
    points = shapely.points(xy[:2])
    buffers = shapely.buffer(points, radii[:2], quad_segs=BUFFER_QUAD_SEGS)
    centre = shapely.union_all(points if overpass else buffers).centroid
    assert (pair["x"], pair["y"]) == (centre.x, centre.y)
    off_midpoint = math.dist(xy[:2].mean(axis=0), (pair["x"], pair["y"]))
    assert off_midpoint < 1e-9 if overpass else off_midpoint > 1.0


def _reordered(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """The graph with its nodes and edges added in reverse order."""
    result = nx.MultiDiGraph()
    result.graph.update(graph.graph)
    result.add_nodes_from(reversed(list(graph.nodes(data=True))))
    result.add_edges_from(reversed(list(graph.edges(keys=True, data=True))))
    return result


def _placed(graph: nx.MultiDiGraph) -> tuple[dict[Any, Any], dict[Any, Any]]:
    """Each node's position and each edge's geometry and length, keyed by members."""
    members = dict(graph.nodes(data="members"))
    nodes = {d["members"]: (d["x"], d["y"], d["lon"], d["lat"]) for _, d in graph.nodes(data=True)}
    edges = {
        (members[u], members[v], d["osm_nodes"]): (tuple(d["geometry"].coords), d["length"])
        for u, v, d in graph.edges(data=True)
    }
    return nodes, edges


@pytest.mark.parametrize("case", ["divided", "split group"])
def test_cluster_positions_do_not_depend_on_the_order(tmp_path: Path, case: str) -> None:
    source = FIXTURES / "divided.osm" if case == "divided" else _near_pair(tmp_path, True)
    simplified = simplify(raw(source))[0]
    forward, _ = stage_a(simplified)
    backward, _ = stage_a(_reordered(simplified))
    assert any(kind == "cluster" for _, kind in forward.nodes(data="kind"))
    assert _placed(backward) == _placed(forward)


def test_an_overpass_does_not_merge() -> None:
    before, merged, stats = through_a("overpass")
    assert stats.clusters == 0
    assert node_of(merged, 101) != node_of(merged, 201)  # 14 m apart, no edge between them
    assert sorted(m for _, m in merged.nodes(data="members")) == [(n,) for n in sorted(before)]


def test_stage_a_without_edges() -> None:
    empty = nx.MultiDiGraph(crs="EPSG:32610")
    merged, stats = stage_a(empty)
    assert merged.number_of_nodes() == 0 and stats == StageAStats()


# Stage B ----------------------------------------------------------------------------------


def test_roundabout_becomes_one_super_node() -> None:
    before, graph = through_b("roundabout")
    (ring,) = supers(graph)
    record = supers(graph)[ring]
    assert graph.nodes[ring]["kind"] == record["kind"] == "roundabout"
    assert record["members"] == tuple(range(10, 18))
    assert record["parts"] == ((10,), (12,), (14,), (16,))
    assert [(e["u"], e["v"]) for e in record["internal"]] == [
        (10, 12),
        (12, 14),
        (14, 16),
        (16, 10),
    ]
    assert pairs_by_osm(graph, ring) == boundary(graph, ring)  # every pair, U-turns too
    assert len(record["transitions"]) == 16
    assert (graph.number_of_nodes(), graph.number_of_edges()) == (5, 8)
    x, y = graph.nodes[ring]["x"], graph.nodes[ring]["y"]
    for _, _, data in graph.out_edges(ring, data=True):
        assert data["geometry"].coords[0] == (x, y)
        assert data["length"] == pytest.approx(data["geometry"].length)


def test_diamond_becomes_one_interchange() -> None:
    _, graph = through_b("diamond")
    (node,) = supers(graph)
    record = supers(graph)[node]
    assert record["kind"] == "interchange"
    assert record["parts"] == ((102,), (103,), (112,), (113,), (121,), (122,))
    assert record["members"] == (102, 103, 112, 113, 121, 122, 130, 131, 132, 133)
    assert len(record["internal"]) == 8
    allowed = pairs_by_osm(graph, node)
    assert allowed == boundary(graph, node) and len(allowed) == 16
    assert ((100, 102), (103, 105)) in allowed  # northbound through
    assert ((100, 102), (122, 123)) in allowed  # off the motorway, east on Main St
    assert ((120, 121), (113, 115)) in allowed  # from Main St onto I-5 south
    assert ((100, 102), (113, 115)) in allowed  # off, across and back on: a U-turn


def test_cloverleaf_becomes_one_interchange() -> None:
    _, graph = through_b("cloverleaf")
    (node,) = supers(graph)
    record = supers(graph)[node]
    assert len(record["parts"]) == 16 and len(record["members"]) == 44
    assert len(record["internal"]) == 20
    allowed = pairs_by_osm(graph, node)
    assert allowed == boundary(graph, node) and len(allowed) == 16
    assert ((290, 200), (203, 293)) in allowed  # I 84 eastbound through
    assert ((280, 220), (223, 283)) in allowed  # I 205 northbound through
    assert ((290, 200), (223, 283)) in allowed  # I 84 east to I 205 north by the SE loop
    assert ((290, 200), (213, 297)) in allowed  # a U-turn through two loops


def half_diamond(tmp_path: Path) -> Path:
    """A motorway with only a northbound off-ramp and a southbound on-ramp at Main St."""
    return write_osm(
        tmp_path / "half.osm",
        {
            1: (15, -900),
            2: (15, -350),
            3: (15, 900),
            4: (-15, 900),
            5: (-15, -350),
            6: (-15, -900),
            7: (-300, 0),
            8: (-80, 0),
            9: (80, 0),
            10: (300, 0),
            11: (50, -200),
            12: (-50, -200),
        },  # fmt: skip
        [
            (1, [1, 2, 3], {"highway": "motorway", "ref": "I 5", "oneway": "yes"}),
            (2, [4, 5, 6], {"highway": "motorway", "ref": "I 5", "oneway": "yes"}),
            (3, [7, 8, 9, 10], {"highway": "secondary", "name": "Main St"}),
            (4, [2, 11, 9], {"highway": "motorway_link"}),
            (5, [8, 12, 5], {"highway": "motorway_link"}),
        ],
    )


def test_half_diamond_allows_only_what_its_ramps_reach(tmp_path: Path) -> None:
    before, graph = through_b(half_diamond(tmp_path))
    check_all(before, graph)
    (node,) = supers(graph)
    allowed = pairs_by_osm(graph, node)
    absent = boundary(graph, node) - allowed
    assert absent == {
        ((4, 5), (2, 3)),  # southbound in, northbound out: no southbound off-ramp
        ((4, 5), (8, 7)),
        ((4, 5), (9, 10)),
        ((7, 8), (2, 3)),  # Main St to I-5 north: no northbound on-ramp
        ((10, 9), (2, 3)),
    }
    assert len(allowed) == 11


def slip_lane(tmp_path: Path, gap: float, link: str = "primary_link") -> Path:
    """A crossroads at 22 with a right-turn slip lane 21 -> 25, `gap` metres from it."""
    return write_osm(
        tmp_path / "slip.osm",
        {
            20: (-300, 0),
            21: (-gap, 0),
            22: (0, 0),
            23: (300, 0),
            24: (0, 300),
            25: (0, -gap),
            26: (0, -300),
            27: (-gap / 2.5, -gap / 2.5),
        },  # fmt: skip
        [
            (1, [20, 21, 22, 23], {"highway": "primary", "name": "A St"}),
            (2, [24, 22, 25, 26], {"highway": "secondary", "name": "B Ave"}),
            (3, [21, 27, 25], {"highway": link, "oneway": "yes"}),
        ],
    )


def test_a_slip_lane_takes_in_the_crossing_it_bypasses(tmp_path: Path) -> None:
    before, merged, _ = through_a(slip_lane(tmp_path, 60))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    (node,) = supers(graph)
    assert supers(graph)[node]["parts"] == ((21,), (22,), (25,))
    assert stats.slip_centres == 1
    assert pairs_by_osm(graph, node) == boundary(graph, node)


@pytest.mark.parametrize(("gap", "link"), [(200, "primary_link"), (60, "motorway_link")])
def test_a_slip_lane_keeps_its_cluster_otherwise(tmp_path: Path, gap: float, link: str) -> None:
    before, merged, _ = through_a(slip_lane(tmp_path, gap, link))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    (node,) = supers(graph)
    assert supers(graph)[node]["parts"] == ((21,), (25,))  # the crossing stays its own node
    assert stats.slip_centres == 0


def merging_branches(tmp_path: Path) -> Path:
    """Two one-way branches of Main St merging at 52, with a crossover link between them."""
    one_way = {"highway": "primary", "name": "Main St", "oneway": "yes"}
    return write_osm(
        tmp_path / "branches.osm",
        {50: (-300, 20), 51: (-60, 20), 52: (0, 0), 53: (300, 0), 54: (-300, -20), 55: (-60, -20)},
        [
            (1, [50, 51, 52], one_way),
            (2, [54, 55, 52], one_way),
            (3, [52, 53], {"highway": "primary", "name": "Main St"}),
            (4, [51, 55], {"highway": "primary_link", "oneway": "yes"}),
        ],
    )


def test_a_node_reached_along_one_road_is_not_a_crossing(tmp_path: Path) -> None:
    # 51 and 55 both reach 52 within 150 m, but only along Main St: no crossing is bypassed.
    before, merged, _ = through_a(merging_branches(tmp_path))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    (node,) = supers(graph)
    assert supers(graph)[node]["parts"] == ((51,), (55,))
    assert (stats.slip_centres, stats.pocket_nodes) == (0, 0)


def interchanges(tmp_path: Path, second: str) -> Path:
    """A motorway with an off-ramp to A St and, 500 m on, one to the street named `second`."""
    return write_osm(
        tmp_path / "two.osm",
        {
            1: (15, -900),
            2: (15, -200),
            3: (15, 300),
            4: (15, 900),
            5: (-200, 0),
            6: (80, 0),
            7: (300, 0),
            8: (-200, 500),
            9: (80, 500),
            10: (300, 500),
            11: (50, -100),
            12: (50, 400),
        },  # fmt: skip
        [
            (1, [1, 2, 3, 4], {"highway": "motorway", "ref": "I 5", "oneway": "yes"}),
            (2, [5, 6, 7], {"highway": "secondary", "name": "A St"}),
            (3, [8, 9, 10], {"highway": "secondary", "name": second}),
            (4, [2, 11, 6], {"highway": "motorway_link"}),
            (5, [3, 12, 9], {"highway": "motorway_link"}),
        ],
    )


@pytest.mark.parametrize(("second", "count"), [("B St", 2), ("A St", 1)])
def test_ramps_join_when_they_connect_the_same_two_roads(
    tmp_path: Path, second: str, count: int
) -> None:
    before, merged, _ = through_a(interchanges(tmp_path, second))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    assert len(supers(graph)) == count
    assert stats.joined == (count == 1)
    assert stats.rejoined == 0  # the motorway passes both, but never comes back to the first


def woodburn(tmp_path: Path, far: bool = False, fork: int | None = None) -> Path:
    """I-5 over the Newberg Highway as at Woodburn: the highway is OR 219 west of the bridge and
    OR 214 east of it, so the interchange's halves share one identity, I-5. Northbound, I-5
    leaves the east half's off-ramp (2), passes the west half's loop on-ramp (3), then the east
    half's on-ramp (4); east of I-5 the highway is a one-way couplet as far as Evergreen Rd, too
    long to be a pocket, so the east half's off-ramp (to 13) cannot reach its on-ramp (from 23).
    With `far`, the east half also has a ramp 1.5 km east to Far Rd. With `fork` (0 or 1, the
    order its ways are written in), northbound I-5 forks at 26 into two I 5 branches, one past the
    west half's on-ramp and back to the east half's (3, 4), the other bypassing both (27)."""
    nodes = {
        1: (15, -1000), 2: (15, -400), 3: (15, 150), 4: (15, 500), 5: (15, 1000),
        6: (-15, 1000), 7: (-15, 400), 8: (-15, -300), 9: (-15, -1000),
        10: (-400, 0), 11: (-150, 0), 12: (0, 0), 13: (150, 0), 14: (800, 0),
        15: (100, -200), 16: (150, 300), 17: (-60, 100), 18: (-100, 200), 19: (-100, -200),
        23: (250, 0), 24: (250, -40), 25: (800, 300), 26: (15, -150), 27: (60, 300),
        20: (900, 0), 21: (1650, 0), 22: (1650, 300),
    }  # fmt: skip
    i5 = {"highway": "motorway", "ref": "I 5", "oneway": "yes"}
    or214 = {"highway": "primary", "name": "Newberg Highway", "ref": "OR 214"}
    ramp = {"highway": "motorway_link", "oneway": "yes"}
    north = [(1, [1, 2, 3, 4, 5], i5)]
    if fork is not None:
        branches = [(31, [26, 3, 4, 5], i5), (32, [26, 27, 5], i5)]
        north = [(1, [1, 2, 26], i5), *(branches if fork == 0 else branches[::-1])]
    ways = [
        *north,
        (2, [6, 7, 8, 9], i5),
        (3, [10, 11, 12], {**or214, "ref": "OR 219"}),
        (4, [12, 13], or214),
        (5, [14, 23, 13], {**or214, "oneway": "yes"}),  # westbound
        (6, [13, 24, 14], {**or214, "oneway": "yes"}),  # eastbound
        (14, [14, 25], {"highway": "tertiary", "name": "Evergreen Rd"}),
        (7, [2, 15, 13], ramp),  # east half: off northbound
        (8, [23, 16, 4], ramp),  # east half: on northbound
        (9, [11, 17, 3], ramp),  # west half: loop on northbound
        (10, [7, 18, 11], ramp),  # west half: off southbound
        (11, [11, 19, 8], ramp),  # west half: on southbound
    ]
    if far:
        ways += [
            (12, [23, 20, 21], ramp),
            (13, [21, 22], {"highway": "tertiary", "name": "Far Rd"}),
        ]
    else:
        nodes = {n: xy for n, xy in nodes.items() if n not in (20, 21, 22)}
    if fork is None:
        nodes = {n: xy for n, xy in nodes.items() if n not in (26, 27)}
    return write_osm(tmp_path / "woodburn.osm", nodes, ways)


def passes(graph: nx.MultiDiGraph, osm_ids: list[int]) -> list[int]:
    """The nodes a drive through these OSM nodes passes, in order (one node once per visit)."""
    holder = {m: n for n, members in graph.nodes(data="members") for m in members}
    nodes = [holder[i] for i in osm_ids]
    return [v for i, v in enumerate(nodes) if i == 0 or v != nodes[i - 1]]


DRIVES = {
    "I-5 north": [1, 2, 3, 4, 5],
    "east": [10, 11, 12, 13, 14],
    "west": [14, 23, 13, 12, 11, 10],
}


def test_a_carriageway_that_comes_back_joins_its_clusters(tmp_path: Path) -> None:
    before, merged, _ = through_a(woodburn(tmp_path))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    (node,) = supers(graph)
    assert supers(graph)[node]["kind"] == "interchange" and stats.rejoined == 1
    for osm_ids in DRIVES.values():
        drive = passes(graph, osm_ids)
        assert len(drive) == len(set(drive))  # each node once
    pairs = pairs_by_osm(graph, node)
    assert ((1, 2), (4, 5)) in pairs  # I-5 straight through, northbound
    assert ((6, 7), (8, 9)) in pairs  # and southbound
    assert lost_through(graph) == []


def test_apart_the_halves_lose_a_through_movement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, merged, _ = through_a(woodburn(tmp_path))
    monkeypatch.setattr(consolidate, "_carriageway_rejoins", lambda *args: [])
    graph, stats = stage_b(merged)
    check_all(before, graph)
    assert len(supers(graph)) == 2 and stats.rejoined == 0
    north = passes(graph, DRIVES["I-5 north"])
    assert len(north) == 5 and north[1] == north[3]  # the east half twice
    ((node, _, _),) = lost_through(graph)
    assert node == north[1]


def test_a_pocket_follows_a_road_whose_ref_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, merged, _ = through_a(woodburn(tmp_path))

    def refs_only(graph: nx.MultiDiGraph, signature: dict[Any, Any]) -> dict[Any, frozenset[Any]]:
        return {e: frozenset({r}) for e, r in signature.items() if r is not None}

    monkeypatch.setattr(consolidate, "_road_keys", refs_only)
    graph, _ = stage_b(merged)
    check_all(before, graph)
    for name in ("east", "west"):  # out to the bridge, where OR 219 becomes OR 214, and back
        drive = passes(graph, DRIVES[name])
        assert len(drive) > len(set(drive))
    monkeypatch.undo()
    graph, _ = stage_b(merged)
    for name in ("east", "west"):
        drive = passes(graph, DRIVES[name])
        assert len(drive) == len(set(drive))


def mlk(tmp_path: Path) -> Path:
    """OR 99E (one-way north) passes a cluster of two ramp components joined by their roads (both
    to OR 99E and Clay St, one-way south between them), then another cluster (to Main St), then
    the other part of the first, as Martin Luther King Boulevard does in Portland."""
    trunk = {"highway": "trunk", "ref": "OR 99E", "name": "MLK Blvd", "oneway": "yes"}
    link = {"highway": "trunk_link", "oneway": "yes"}
    return write_osm(
        tmp_path / "mlk.osm",
        {
            21: (0, -600),
            22: (0, -150),
            23: (0, 0),
            24: (0, 150),
            25: (0, 600),
            31: (80, -120),
            32: (150, -100),
            33: (150, 100),
            34: (80, 120),
            35: (150, 0),
            41: (-80, 20),
            42: (-150, 0),
            43: (-400, 0),
        },  # fmt: skip
        [
            (20, [21, 22, 23, 24, 25], trunk),
            (21, [22, 31, 32], link),
            (22, [33, 34, 24], link),
            (23, [33, 35, 32], {"highway": "tertiary", "name": "Clay St", "oneway": "yes"}),
            (24, [23, 41, 42], link),
            (25, [43, 42], {"highway": "tertiary", "name": "Main St"}),
        ],
    )


def test_a_carriageway_coming_back_to_another_part_of_its_cluster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before, merged, _ = through_a(mlk(tmp_path))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    (node,) = supers(graph)
    assert stats.joined == 1 and stats.rejoined == 1
    assert ((21, 22), (24, 25)) in pairs_by_osm(graph, node)
    assert lost_through(graph) == []
    monkeypatch.setattr(consolidate, "_carriageway_rejoins", lambda *args: [])
    apart, _ = stage_b(merged)
    assert len(supers(apart)) == 2 and len(lost_through(apart)) == 1


def test_a_joined_cluster_too_wide_is_split(tmp_path: Path) -> None:
    before, merged, _ = through_a(woodburn(tmp_path, far=True))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    assert stats.rejoined == 1 and stats.split == 1
    # single linkage at 300 m: the south gores (2 and 8) and the rest; Far Rd's end alone stays
    # a plain node
    assert len(supers(graph)) == 2
    for osm_ids in DRIVES.values():
        drive = passes(graph, osm_ids)
        assert len(drive) == len(set(drive))
    assert lost_through(graph) == []


@pytest.mark.parametrize("order", [0, 1])
def test_a_carriageway_that_forks_is_followed_down_every_branch(tmp_path: Path, order: int) -> None:
    before, merged, _ = through_a(woodburn(tmp_path, fork=order))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    assert stats.rejoined == 1 and len(supers(graph)) == 1
    drive = passes(graph, [1, 2, 26, 3, 4, 5])
    assert len(drive) == len(set(drive))
    assert lost_through(graph) == []


def block(tmp_path: Path) -> Path:
    """Four primary streets of four names, with no ref, round a 300 m block: a slip lane at the
    south-west corner (on West St and South St) and one on North St (to Up St). No one road
    leaves the corner's cluster and comes back to it; a chain that turned the corners would."""
    street = {"highway": "primary"}
    link = {"highway": "primary_link", "oneway": "yes"}
    return write_osm(
        tmp_path / "block.osm",
        {
            1: (0, 0),
            2: (0, 300),
            3: (300, 300),
            4: (300, 0),
            11: (0, 40),
            12: (40, 0),
            13: (15, 15),
            21: (120, 300),
            22: (180, 300),
            23: (150, 340),
            24: (150, 500),
            31: (-200, 0),
            32: (0, -200),
        },  # fmt: skip
        [
            (1, [1, 11, 2], {**street, "name": "West St"}),
            (2, [2, 21, 22, 3], {**street, "name": "North St"}),
            (3, [3, 4], {**street, "name": "East St"}),
            (4, [4, 12, 1], {**street, "name": "South St"}),
            (5, [11, 13, 12], link),
            (6, [21, 23, 22], link),
            (7, [23, 24], {"highway": "tertiary", "name": "Up St"}),
            (8, [31, 1], {**street, "name": "West Spur"}),
            (9, [32, 1], {**street, "name": "South Spur"}),
        ],
    )


def test_a_carriageway_never_turns_onto_another_road(tmp_path: Path) -> None:
    before, merged, _ = through_a(block(tmp_path))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    assert stats.rejoined == 0 and len(supers(graph)) == 2


def shuffled(path: Path, seed: int) -> Path:
    """The same OSM file with its nodes, and its ways, written in another order."""
    lines = path.read_text().splitlines()
    nodes = [line for line in lines if line.lstrip().startswith("<node")]
    ways = [line for line in lines if line.lstrip().startswith("<way")]
    rng = random.Random(seed)
    rng.shuffle(nodes)
    rng.shuffle(ways)
    out = path.with_name(f"{path.stem}.{seed}.osm")
    out.write_text("\n".join([*lines[:2], *nodes, *ways, lines[-1]]) + "\n")
    return out


def in_osm_terms(graph: nx.MultiDiGraph) -> tuple[dict[Any, Any], frozenset[Any]]:
    """The super-junctions (by members: kind, parts and transitions) and the twin pairs, each
    edge named by its OSM nodes, so two runs that number nodes and edges differently compare."""

    def osm(edge: tuple[int, int, int]) -> tuple[int, ...]:
        return tuple(graph.edges[edge]["osm_nodes"])

    junctions = {
        record["members"]: (
            record["kind"],
            record["parts"],
            frozenset((osm(a), osm(b)) for a, b in record["transitions"]),
        )
        for record in supers(graph).values()
    }
    twins = frozenset(
        frozenset((osm(edge), osm(other)))
        for edge, others in graph.graph["twins"].items()
        for other in others
    )
    return junctions, twins


@pytest.mark.parametrize("fixture", ["woodburn", "mlk", "fork"])
def test_the_joins_do_not_depend_on_the_input_order(tmp_path: Path, fixture: str) -> None:
    source = {
        "woodburn": lambda: woodburn(tmp_path, far=True),
        "mlk": lambda: mlk(tmp_path),
        "fork": lambda: woodburn(tmp_path, fork=0),
    }[fixture]()
    _, merged, _ = through_a(source)
    find_twins(merged)
    expected, stats = stage_b(merged)
    assert bool(expected.graph["twins"]) == (fixture != "mlk")  # I-5's carriageways compared too
    for seed in range(6):
        before, merged, _ = through_a(shuffled(source, seed))
        find_twins(merged)
        graph, other = stage_b(merged)
        check_all(before, graph)
        assert other.rejoined == stats.rejoined
        assert in_osm_terms(graph) == in_osm_terms(expected)


def wide(tmp_path: Path) -> Path:
    """A ramp chain 2 km long between two points of Front St."""
    return write_osm(
        tmp_path / "wide.osm",
        {
            30: (-100, 0),
            31: (0, 0),
            32: (2000, 0),
            33: (2100, 0),
            40: (500, 300),
            41: (900, 300),
            42: (1500, 300),
        },  # fmt: skip
        [
            (1, [30, 31, 32, 33], {"highway": "tertiary", "name": "Front St"}),
            (2, [31, 40], {"highway": "motorway_link", "name": "Ramp A"}),
            (3, [40, 41], {"highway": "motorway_link", "name": "Ramp B"}),
            (4, [41, 42], {"highway": "motorway_link", "name": "Ramp C"}),
            (5, [42, 32], {"highway": "motorway_link", "name": "Ramp D"}),
        ],
    )


def detached(tmp_path: Path) -> Path:
    """A ramp chain 2 km long that touches no other road."""
    return write_osm(
        tmp_path / "detached.osm",
        {50: (0, 0), 51: (700, 0), 52: (1400, 0), 53: (2000, 0)},
        [
            (6, [50, 51], {"highway": "motorway_link", "name": "Ramp E"}),
            (7, [51, 52], {"highway": "motorway_link", "name": "Ramp F"}),
            (8, [52, 53], {"highway": "motorway_link", "name": "Ramp G"}),
        ],
    )


def test_a_cluster_too_wide_is_split(tmp_path: Path) -> None:
    source = wide(tmp_path)
    before, merged, _ = through_a(source)
    graph, stats = stage_b(merged)
    check_all(before, graph)
    assert stats.split == 1
    parts = sorted(record["parts"] for record in supers(graph).values())
    assert parts == [((31,), (40,), (41,)), ((32,), (42,))]  # each ramp node to the nearer end


def test_a_cluster_without_attachment_nodes_is_not_split(tmp_path: Path) -> None:
    before, merged, _ = through_a(detached(tmp_path))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    assert stats.split == 0  # too wide, but there is nothing to split it by
    (node,) = supers(graph)
    assert supers(graph)[node]["parts"] == ((50,), (51,), (52,), (53,))


def test_every_cluster_node_reaches_an_attachment_node(tmp_path: Path) -> None:
    # What the diameter guard relies on to give every node of a cluster a group.
    sources = [
        *ALL_FIXTURES,
        half_diamond(tmp_path),
        slip_lane(tmp_path, 60),
        interchanges(tmp_path, "A St"),
        bridged_diamond(tmp_path),
        crossover(tmp_path),
        merging_branches(tmp_path),
        wide(tmp_path),
        detached(tmp_path),
    ]
    for source in sources:
        _, merged, _ = through_a(source)
        stats = StageBStats()
        clusters = _ramp_clusters(merged, stats)
        for nodes in _absorb_pockets(
            merged, clusters, _road_keys(merged, _road_signatures(merged)), stats
        ):
            within = nx.Graph()
            within.add_nodes_from(nodes)
            within.add_edges_from((u, v) for u, v, _ in _edges_within(merged, nodes))
            attachments = [
                n for n in nodes if any(not _is_junction_edge(d) for _, d in _incident(merged, n))
            ]
            if attachments:  # a cluster with none is never split
                reached = set().union(
                    *(nx.node_connected_component(within, n) for n in attachments)
                )
                assert reached == nodes, source


def bridged_diamond(tmp_path: Path) -> Path:
    """The diamond fixture's layout, Main St crossing between the terminals on a bridge."""
    motorway = {"highway": "motorway", "ref": "I 5", "oneway": "yes"}
    main = {"highway": "secondary", "name": "Main St"}
    return write_osm(
        tmp_path / "bridged.osm",
        {
            100: (15, -900),
            102: (15, -350),
            103: (15, 350),
            105: (15, 900),
            110: (-15, 900),
            112: (-15, 350),
            113: (-15, -350),
            115: (-15, -900),
            120: (-300, 0),
            121: (-80, 0),
            124: (-30, 0),
            125: (30, 0),
            122: (80, 0),
            123: (300, 0),
            130: (50, -200),
            131: (50, 200),
            132: (-50, 200),
            133: (-50, -200),
        },  # fmt: skip
        [
            (1, [100, 102, 103, 105], motorway),
            (2, [110, 112, 113, 115], motorway),
            (3, [120, 121, 124], main),
            (4, [124, 125], {**main, "bridge": "yes", "layer": "1"}),
            (5, [125, 122, 123], main),
            (6, [102, 130, 122], {"highway": "motorway_link"}),
            (7, [122, 131, 103], {"highway": "motorway_link"}),
            (8, [112, 132, 121], {"highway": "motorway_link"}),
            (9, [121, 133, 113], {"highway": "motorway_link"}),
        ],
    )


def test_a_pocket_between_ramp_terminals_is_taken_in(tmp_path: Path) -> None:
    before, merged, _ = through_a(bridged_diamond(tmp_path))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    (node,) = supers(graph)
    # The bridge ends sit on Main St between the two terminals: without them, Main St would
    # leave the interchange and come back, and nobody could drive straight along it.
    assert {(124,), (125,)} <= set(supers(graph)[node]["parts"])
    assert stats.pocket_nodes == 2
    allowed = pairs_by_osm(graph, node)
    assert ((120, 121), (122, 123)) in allowed and ((123, 122), (121, 120)) in allowed


def crossover(tmp_path: Path) -> Path:
    """A divided Main St (carriageways 60 m apart) with a crossover link 41 -> 42, crossed by
    Oak St at x = 0; the carriageways meet 500 m west and east."""
    one_way = {"highway": "primary", "name": "Main St", "oneway": "yes"}
    return write_osm(
        tmp_path / "crossover.osm",
        {
            30: (-500, 0),
            31: (0, 30),
            32: (0, -30),
            33: (500, 0),
            41: (100, 30),
            42: (100, -30),
            34: (0, 300),
            35: (0, -300),
        },  # fmt: skip
        [
            (1, [33, 41, 31, 30], one_way),  # westbound, the north carriageway
            (2, [30, 32, 42, 33], one_way),  # eastbound, the south carriageway
            (3, [34, 31, 32, 35], {"highway": "residential", "name": "Oak St"}),
            (4, [41, 42], {"highway": "primary_link", "oneway": "yes"}),
        ],
    )


def test_a_path_that_changes_road_is_no_pocket(tmp_path: Path) -> None:
    # From 41 west on Main St, across on Oak St and east on Main St to 42 is three roads'
    # worth of path; Main St alone joins 41 and 42 only around the far ends, over 600 m.
    before, merged, _ = through_a(crossover(tmp_path))
    graph, stats = stage_b(merged)
    check_all(before, graph)
    (node,) = supers(graph)
    assert supers(graph)[node]["parts"] == ((41,), (42,))
    assert (stats.pocket_nodes, stats.slip_centres) == (0, 0)


def test_roundabouts_are_counted(tmp_path: Path) -> None:
    _, graph = through_b("roundabout")
    assert [r["roundabouts"] for r in supers(graph).values()] == [1]
    _, graph = through_b("diamond")
    assert [r["roundabouts"] for r in supers(graph).values()] == [0]
    # A slip lane past the ring joins it into one interchange that still holds one ring.
    nodes = {10: (0, 15), 12: (-15, 0), 14: (0, -15), 16: (15, 0)}
    nodes |= {20: (0, 150), 21: (0, -150), 22: (150, 0), 23: (-150, 0), 24: (0, 50), 25: (50, 0)}
    source = write_osm(
        tmp_path / "bypassed.osm",
        nodes,
        [
            (1, [10, 12, 14, 16, 10], {"highway": "tertiary", "junction": "roundabout"}),
            (2, [10, 24, 20], {"highway": "residential", "name": "NE 42nd Ave"}),
            (3, [14, 21], {"highway": "residential", "name": "NE 42nd Ave"}),
            (4, [16, 25, 22], {"highway": "residential", "name": "NE Going St"}),
            (5, [12, 23], {"highway": "residential", "name": "NE Going St"}),
            (6, [24, 25], {"highway": "tertiary_link", "oneway": "yes"}),
        ],
    )
    before, graph = through_b(source)
    check_all(before, graph)
    ((node, record),) = supers(graph).items()
    assert (record["kind"], record["roundabouts"]) == ("interchange", 1)


def test_stage_b_numbers_the_nodes() -> None:
    _, graph = through_b("diamond")
    assert list(graph.nodes) == list(range(7))
    assert [graph.nodes[n]["members"][0] for n in range(6)] == [100, 105, 110, 115, 120, 123]
    assert graph.nodes[6]["kind"] == "interchange" and list(supers(graph)) == [6]


def test_relabel_keeps_only_what_it_is_told() -> None:
    _, graph = through_b("diamond")
    find_twins(graph)
    north = node_of(graph, 105)
    kept = relabel(graph, set(graph) - {north})
    assert kept.number_of_nodes() == 6 and list(kept.nodes) == list(range(6))
    (node,) = supers(kept)
    assert len(supers(kept)[node]["transitions"]) == 12  # the 4 pairs leaving north are gone
    assert len(kept.graph["twins"]) == 2  # northbound departure and its twin dropped out
    for edge, others in kept.graph["twins"].items():
        assert kept.has_edge(*edge) and all(kept.has_edge(*e) for e in others)
