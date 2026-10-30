import itertools
import json
import math
import pickle
import re
from pathlib import Path

import networkx as nx
import pytest

from worst_route.pipeline.build import BuildStats, build_raw_graph, direction, save_stage
from worst_route.pipeline.filter import filter_roads

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
UTM_10N = 32610
GRID = [1000 + 10 * r + c for r in range(6) for c in range(6)]
ATTRIBUTE_TAGS = [
    "name",
    "ref",
    "maxspeed",
    "lanes",
    "bridge",
    "tunnel",
    "toll",
    "junction",
    "service",
    "access",
    "layer",
]


def build(name: str) -> tuple[nx.MultiDiGraph, BuildStats]:
    return build_raw_graph(FIXTURES / f"{name}.osm", UTM_10N)


def fixture_locations(name: str) -> dict[int, tuple[float, float]]:
    """Node id -> (lon, lat), read from the fixture's XML."""
    pattern = r'<node id="(\d+)" version="1" lat="([-\d.]+)" lon="([-\d.]+)"'
    text = (FIXTURES / f"{name}.osm").read_text()
    return {int(i): (float(lon), float(lat)) for i, lat, lon in re.findall(pattern, text)}


def local_metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance by a local equirectangular approximation, independent of the projection."""
    lat = math.radians((a[1] + b[1]) / 2)
    dx = (b[0] - a[0]) * 111_320 * math.cos(lat)
    dy = (b[1] - a[1]) * 111_132
    return math.hypot(dx, dy)


def edges_of(graph: nx.MultiDiGraph, way_id: int) -> dict[tuple[int, int], dict]:
    return {(u, v): d for u, v, d in graph.edges(data=True) if d["osmid"] == way_id}


def test_grid_counts() -> None:
    graph, stats = build("grid")
    assert graph.graph == {"crs": "EPSG:32610", "pipeline_version": "1"}
    assert sorted(graph.nodes) == GRID  # shape points, railway and building nodes are not nodes
    assert stats == BuildStats(
        ways_read=14,  # 12 streets, the railway and the building
        ways_kept=12,
        segments=60,
        nodes=36,
        edges=110,
        edges_by_rule={"oneway": 5, "oneway_reverse": 5, "two_way": 100},
    )
    assert graph.number_of_edges() == 110


def test_grid_node_attributes() -> None:
    graph, _ = build("grid")
    where = fixture_locations("grid")
    for node, data in graph.nodes(data=True):
        assert set(data) == {"x", "y", "lon", "lat"}
        assert (data["lon"], data["lat"]) == pytest.approx(where[node], abs=1e-7)
        assert 500_000 < data["x"] < 600_000 and 5_030_000 < data["y"] < 5_050_000  # UTM 10N
    # Neighbouring grid nodes are ~100 m apart in projected coordinates.
    a, b = graph.nodes[1000], graph.nodes[1001]
    planar = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
    assert planar == pytest.approx(local_metres(where[1000], where[1001]), rel=0.01)


def test_grid_edge_geometry_and_length() -> None:
    graph, _ = build("grid")
    where = fixture_locations("grid")
    for u, v, data in graph.edges(data=True):
        nodes = data["osm_nodes"]
        assert (nodes[0], nodes[-1]) == (u, v)
        coords = list(data["geometry"].coords)
        assert len(coords) == len(nodes)
        assert coords[0] == (graph.nodes[u]["x"], graph.nodes[u]["y"])
        assert coords[-1] == (graph.nodes[v]["x"], graph.nodes[v]["y"])
        assert data["length"] == pytest.approx(data["geometry"].length)
        along = sum(local_metres(where[a], where[b]) for a, b in itertools.pairwise(nodes))
        assert data["length"] == pytest.approx(along, rel=0.01)


def test_grid_directions() -> None:
    graph, _ = build("grid")
    # Dogwood Ave (oneway=yes): west to east only, in node order.
    dogwood = edges_of(graph, 2003)
    assert sorted(dogwood) == [(1030 + c, 1031 + c) for c in range(5)]
    assert all(d["oneway"] and not d["reversed"] for d in dogwood.values())
    # Kerby Ave (oneway=-1, drawn south to north): north to south only, osm_nodes reversed.
    kerby = edges_of(graph, 2104)
    assert sorted(kerby) == sorted((1014 + 10 * r, 1004 + 10 * r) for r in range(5))
    assert all(d["oneway"] and not d["reversed"] for d in kerby.values())
    assert kerby[1014, 1004]["osm_nodes"] == (1014, 1004)
    first = kerby[1014, 1004]["geometry"].coords[0]
    assert first == (graph.nodes[1014]["x"], graph.nodes[1014]["y"])
    # Ash St (two-way): both directions, the backward edge marked reversed.
    ash = edges_of(graph, 2000)
    assert len(ash) == 10
    forward, backward = ash[1000, 1001], ash[1001, 1000]
    assert (forward["oneway"], forward["reversed"]) == (False, False)
    assert (backward["oneway"], backward["reversed"]) == (False, True)
    assert forward["osm_nodes"] == (1000, 1100, 1001)  # through the shape point
    assert backward["osm_nodes"] == (1001, 1100, 1000)
    assert list(backward["geometry"].coords) == list(forward["geometry"].coords)[::-1]
    assert forward["length"] == backward["length"]


def test_grid_shape_points_and_attributes() -> None:
    graph, _ = build("grid")
    baker = edges_of(graph, 2001)[1012, 1013]
    assert baker["osm_nodes"] == (1012, 1102, 1013)  # the toll booth is a shape point
    assert {k: baker[k] for k in ("highway", "name", "ref", "maxspeed", "lanes")} == {
        "highway": "primary",
        "name": "Baker Rd",
        "ref": "OR 10",
        "maxspeed": "35 mph",
        "lanes": "2",
    }
    assert set(baker) == {
        "osmid",
        "highway",
        "name",
        "ref",
        "maxspeed",
        "lanes",
        "oneway",
        "reversed",
        "length",
        "geometry",
        "osm_nodes",
    }  # tags the way lacks are absent, not None
    fir = edges_of(graph, 2005)[1051, 1052]
    assert fir["osm_nodes"] == (1051, 1103, 1052)  # the level crossing too
    cedar = edges_of(graph, 2002)[1020, 1021]
    assert cedar["highway"] == "residential" and cedar["name"] == "Cedar St"
    assert not set(ATTRIBUTE_TAGS[1:]) & set(cedar)
    assert edges_of(graph, 2102)[1002, 1012]["ref"] == "US 26"


def test_oneway_rules() -> None:
    graph, stats = build("oneway")
    assert stats.edges_by_rule == {
        "motorway_implied": 2,
        "oneway_no": 4,
        "oneway_reverse": 1,
        "reversible": 2,
        "roundabout": 2,
        "two_way": 4,
    }
    assert (stats.nodes, stats.segments, stats.edges) == (15, 10, 15)
    expected = {
        300: {(1, 3): (True, False, (1, 2, 3))},  # motorway, no oneway tag
        301: {(3, 4): (True, False, (3, 4))},  # motorway_link
        302: {  # the roundabout ring, split where the approaches join
            (10, 12): (True, False, (10, 11, 12)),
            (12, 10): (True, False, (12, 13, 10)),
        },
        303: {(14, 10): (False, False, (14, 10)), (10, 14): (False, True, (10, 14))},
        304: {(12, 15): (False, False, (12, 15)), (15, 12): (False, True, (15, 12))},
        305: {(22, 20): (True, False, (22, 21, 20))},  # oneway=-1
        306: {(30, 31): (False, False, (30, 31)), (31, 30): (False, True, (31, 30))},
        307: {(40, 41): (False, False, (40, 41)), (41, 40): (False, True, (41, 40))},
        308: {(50, 51): (False, False, (50, 51)), (51, 50): (False, True, (51, 50))},
    }
    for way_id, edges in expected.items():
        got = edges_of(graph, way_id)
        assert {uv: (d["oneway"], d["reversed"], d["osm_nodes"]) for uv, d in got.items()} == edges
    ring = edges_of(graph, 302)[10, 12]
    assert (ring["highway"], ring["junction"]) == ("tertiary", "roundabout")
    reverse = edges_of(graph, 305)[22, 20]
    assert reverse["geometry"].coords[0] == (graph.nodes[22]["x"], graph.nodes[22]["y"])
    assert reverse["geometry"].coords[-1] == (graph.nodes[20]["x"], graph.nodes[20]["y"])


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"highway": "residential"}, ("two_way", True, True)),
        ({"highway": "residential", "oneway": "yes"}, ("oneway", True, False)),
        ({"highway": "residential", "oneway": "true"}, ("oneway", True, False)),
        ({"highway": "residential", "oneway": "1"}, ("oneway", True, False)),
        ({"highway": "residential", "oneway": "-1"}, ("oneway_reverse", False, True)),
        ({"highway": "residential", "oneway": "reverse"}, ("oneway_reverse", False, True)),
        ({"highway": "tertiary", "junction": "roundabout"}, ("roundabout", True, False)),
        ({"highway": "tertiary", "junction": "circular"}, ("roundabout", True, False)),
        # The first matching rule wins: an explicit reverse beats the roundabout, which beats
        # oneway=no (as OSMnx reads roundabouts).
        ({"junction": "roundabout", "oneway": "-1"}, ("oneway_reverse", False, True)),
        ({"junction": "roundabout", "oneway": "no"}, ("roundabout", True, False)),
        ({"highway": "motorway"}, ("motorway_implied", True, False)),
        ({"highway": "motorway_link"}, ("motorway_implied", True, False)),
        ({"highway": "motorway", "oneway": "no"}, ("oneway_no", True, True)),
        ({"highway": "motorway", "oneway": "reversible"}, ("reversible", True, True)),
        ({"highway": "trunk"}, ("two_way", True, True)),
        ({"highway": "trunk_link"}, ("two_way", True, True)),
        ({"highway": "residential", "oneway": "no"}, ("oneway_no", True, True)),
        ({"highway": "primary", "oneway": "reversible"}, ("reversible", True, True)),
        ({"highway": "primary", "oneway": "alternating"}, ("reversible", True, True)),
        ({"highway": "residential", "oneway": "yes;no"}, ("two_way", True, True)),
        ({"highway": "residential", "junction": "jughandle"}, ("two_way", True, True)),
    ],
)
def test_direction(tags: dict[str, str], expected: tuple[str, bool, bool]) -> None:
    assert direction(tags) == expected


def test_culdesac_junctions() -> None:
    graph, stats = build("culdesac")
    assert sorted(graph.nodes) == [1, 3, 5, 7, 21, 22, 23]  # 21 only because Short St ends there
    assert (stats.segments, stats.edges) == (6, 12)
    forward = {d["osm_nodes"] for _, _, d in graph.edges(data=True) if not d["reversed"]}
    assert forward == {(1, 2, 3), (3, 4, 5), (3, 6, 7), (5, 21), (21, 22), (23, 21)}
    assert graph.degree(7) == 2  # the cul-de-sac end: one segment, both ways


def test_build_applies_the_exclusions() -> None:
    graph, stats = build("exclusions")
    assert (stats.ways_read, stats.ways_kept) == (16, 5)
    # Dropped branches make no junctions on Main St.
    assert sorted(graph.nodes) == [1, 13, 14, 15, 16, 113, 114, 115, 116]
    assert edges_of(graph, 200)[1, 13]["osm_nodes"] == tuple(range(1, 14))
    alley = edges_of(graph, 221)[13, 113]
    assert (alley["highway"], alley["service"], alley["name"]) == ("alley", "alley", "Alder Alley")
    assert edges_of(graph, 222)[14, 114]["access"] == "private"


def test_duplicate_ways_are_read_once(tmp_path: Path) -> None:
    text = (FIXTURES / "grid.osm").read_text()
    ways = "".join(re.findall(r"  <way .*?</way>\n", text, flags=re.DOTALL))
    doubled = tmp_path / "doubled.osm"
    doubled.write_text(text.replace("</osm>", ways + "</osm>"))
    once, once_stats = build("grid")
    twice, twice_stats = build_raw_graph(doubled, UTM_10N)
    assert (twice_stats.ways_read, twice_stats.ways_kept) == (28, 12)
    assert twice_stats.edges == once_stats.edges
    assert list(twice.edges(keys=True, data=True)) == list(once.edges(keys=True, data=True))
    assert list(twice.nodes(data=True)) == list(once.nodes(data=True))


def test_filtered_file_builds_the_same_graph(tmp_path: Path) -> None:
    roads = tmp_path / "roads.osm.pbf"
    grid = FIXTURES / "grid.osm"
    filter_roads([grid, grid], roads, tmp_path / "p.parquet", None)
    filtered, stats = build_raw_graph(roads, UTM_10N)
    direct, _ = build("grid")
    assert stats.ways_read == 12
    assert list(filtered.edges(keys=True, data=True)) == list(direct.edges(keys=True, data=True))


def test_empty_roads_file(tmp_path: Path) -> None:
    roads = tmp_path / "roads.osm.pbf"
    bbox = (-100.0, 40.0, -99.0, 41.0)  # far from every fixture
    filter_roads([FIXTURES / "culdesac.osm"], roads, tmp_path / "p.parquet", bbox)
    graph, stats = build_raw_graph(roads, UTM_10N)
    assert graph.number_of_nodes() == graph.number_of_edges() == 0
    assert stats == BuildStats()


def test_missing_node_is_an_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.osm"
    broken.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n'
        '  <node id="1" version="1" lat="45.5" lon="-122.6"/>\n'
        '  <way id="9" version="1"><nd ref="1"/><nd ref="2"/>'
        '<tag k="highway" v="residential"/></way>\n</osm>\n'
    )
    with pytest.raises(ValueError, match="node 2"):
        build_raw_graph(broken, UTM_10N)


def test_save_stage(tmp_path: Path) -> None:
    graph, stats = build("oneway")
    save_stage(graph, stats, tmp_path / "b1")
    with (tmp_path / "b1" / "stages" / "build.pkl").open("rb") as f:
        loaded = pickle.load(f)
    assert loaded.graph == graph.graph
    assert list(loaded.edges(keys=True, data=True)) == list(graph.edges(keys=True, data=True))
    summary = json.loads((tmp_path / "b1" / "stages" / "build.json").read_text())
    assert summary["edges"] == 15
    assert summary["edges_by_rule"]["roundabout"] == 2


def test_a_way_through_its_own_node_is_split_there(tmp_path: Path) -> None:
    # A lollipop: 1 -> 2 -> 3 -> 4 -> back to 2. Node 2 is a junction though one way uses it.
    lollipop = tmp_path / "lollipop.osm"
    lollipop.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n'
        '  <node id="1" version="1" lat="45.5000" lon="-122.6000"/>\n'
        '  <node id="2" version="1" lat="45.5010" lon="-122.6000"/>\n'
        '  <node id="3" version="1" lat="45.5015" lon="-122.6005"/>\n'
        '  <node id="4" version="1" lat="45.5015" lon="-122.5995"/>\n'
        '  <way id="9" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/>'
        '<nd ref="2"/><tag k="highway" v="residential"/></way>\n</osm>\n'
    )
    graph, stats = build_raw_graph(lollipop, UTM_10N)
    assert sorted(graph.nodes) == [1, 2]
    assert (stats.segments, stats.edges) == (2, 4)
    loop = {d["osm_nodes"] for u, v, d in graph.edges(data=True) if u == v}
    assert loop == {(2, 3, 4, 2), (2, 4, 3, 2)}


@pytest.mark.parametrize("name", ["grid", "exclusions", "oneway", "culdesac", "bbox"])
def test_segment_invariants(name: str) -> None:
    graph, _ = build(name)
    interior: list[int] = []
    for u, v, data in graph.edges(data=True):
        nodes = data["osm_nodes"]
        assert len(set(nodes)) == len(nodes) - (u == v)  # no repeat but a self-loop's ends
        if not data["reversed"] or data["oneway"]:  # each segment once
            interior.extend(nodes[1:-1])
    assert len(interior) == len(set(interior))  # a shape point lies in one segment
    assert not set(interior) & set(graph.nodes)


def test_a_node_repeated_back_to_back_is_read_once(tmp_path: Path) -> None:
    stutter = tmp_path / "stutter.osm"
    stutter.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n'
        '  <node id="1" version="1" lat="45.5000" lon="-122.6000"/>\n'
        '  <node id="2" version="1" lat="45.5005" lon="-122.6000"/>\n'
        '  <node id="3" version="1" lat="45.5010" lon="-122.6000"/>\n'
        '  <way id="9" version="1"><nd ref="1"/><nd ref="2"/><nd ref="2"/><nd ref="3"/>'
        '<tag k="highway" v="residential"/></way>\n</osm>\n'
    )
    graph, stats = build_raw_graph(stutter, UTM_10N)
    assert sorted(graph.nodes) == [1, 3]  # 2 stays a shape point: no junction, no self-loop
    assert (stats.segments, stats.edges) == (1, 2)
    assert {d["osm_nodes"] for _, _, d in graph.edges(data=True)} == {(1, 2, 3), (3, 2, 1)}


def test_ways_without_nodes_are_an_error(tmp_path: Path) -> None:
    bare = tmp_path / "bare.osm"
    bare.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n'
        '  <way id="9" version="1"><nd ref="1"/><nd ref="2"/>'
        '<tag k="highway" v="residential"/></way>\n</osm>\n'
    )
    with pytest.raises(ValueError, match="node 1"):
        build_raw_graph(bare, UTM_10N)
