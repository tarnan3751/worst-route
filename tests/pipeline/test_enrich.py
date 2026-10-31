import json
import math
import pickle
from pathlib import Path

import networkx as nx
import pytest
import shapely

from tests.pipeline.chain import (
    FIXTURES,
    brand_pois,
    cleaned,
    east_of,
    node_of,
    poi_table,
    pois_of,
)
from tests.pipeline.osm import write_osm
from worst_route.graph.backend import TURN_CLASS, TURN_LEFT, TURN_STRAIGHT
from worst_route.graph.data import EdgeFlag
from worst_route.pipeline import enrich
from worst_route.pipeline.enrich import (
    KMH_PER_MPH,
    bearings,
    edge_flags,
    parameters,
    parse_maxspeed,
    road_geometry,
    run_enrich,
    save_stage,
)

NO_POIS = poi_table([])


def enriched(name: str, tmp_path: Path) -> nx.MultiDiGraph:
    source = FIXTURES / f"{name}.osm"
    graph = cleaned(source)
    run_enrich(graph, pois_of(source, tmp_path))
    return graph


@pytest.mark.parametrize(
    ("value", "kmh"),
    [
        ("45 mph", 45 * KMH_PER_MPH),
        ("45mph", 45 * KMH_PER_MPH),
        ("25 MPH", 25 * KMH_PER_MPH),
        ("35", 35 * KMH_PER_MPH),  # a bare number is mph in the US
        ("27.5 mph", 27.5 * KMH_PER_MPH),
        ("50 km/h", 50.0),
        ("50 kmh", 50.0),
        ("60 kph", 60.0),
        ("35 mph;25 mph", 35 * KMH_PER_MPH),
        (" 30 mph ", 30 * KMH_PER_MPH),
        ("signals", None),
        ("none", None),
        ("walk", None),
        ("US:urban", None),
        ("0", None),
        ("40 knots", None),
        ("", None),
    ],
)
def test_parse_maxspeed(value: str, kmh: float | None) -> None:
    assert parse_maxspeed(value) == (None if kmh is None else pytest.approx(kmh))


def test_bearings() -> None:
    lines = [
        shapely.LineString([(0, 0), (0, 100)]),  # north
        shapely.LineString([(0, 0), (100, 0)]),  # east
        shapely.LineString([(0, 0), (-100, 0)]),  # west
        shapely.LineString([(0, 0), (70, 70)]),  # north-east
        shapely.LineString([(0, 0), (0, 100), (100, 100)]),  # north, then east
        shapely.LineString([(0, 0), (0, 30), (30, 30)]),  # 30 m each: 20 m from each end
        shapely.LineString([(0, 10), (0, 0)]),  # south, shorter than 20 m
        shapely.LineString([(0, 0), (0, 10), (10, 10)]),  # shorter than 20 m in all: one chord
    ]
    inward, outward = bearings(lines)
    assert outward.tolist() == [0, 90, 270, 45, 0, 0, 180, 45]  # leaving u
    assert inward.tolist() == [0, 90, 270, 45, 90, 90, 180, 45]  # arriving at v


def test_bearings_at_a_cluster_describe_the_road_not_the_stub(tmp_path: Path) -> None:
    # the divided crossing (OSM 50-53, a 25 m square) is one stage A cluster, and cleanup extends
    # each edge at it diagonally to the square's centre. By the roads' own headings, eastbound
    # Barbur goes straight on through it and turns left onto northbound Main; by the stubs it
    # would be a right turn and straight on
    graph = enriched("divided", tmp_path)
    crossing = node_of(graph, 50)
    assert graph.nodes[crossing]["kind"] == "cluster"

    def edge(first: int, last: int) -> tuple[int, int, int]:
        (key,) = (
            k
            for k in graph.edges(keys=True)
            if graph.edges[k]["osm_nodes"][0] == first and graph.edges[k]["osm_nodes"][-1] == last
        )
        return key

    east_in, east_on, north = edge(31, 50), edge(51, 32), edge(53, 62)
    for key in (east_in, east_on, north):
        road, whole = road_geometry(graph, key), graph.edges[key]["geometry"]
        assert len(road.coords) == len(whole.coords) - 1  # one stub, at the crossing
    assert graph.edges[east_in]["bearing_in"] == 90 and graph.edges[east_on]["bearing_out"] == 90
    assert graph.edges[north]["bearing_out"] == 345  # Main bends a little west of north
    arriving = graph.edges[east_in]["bearing_in"]
    assert TURN_CLASS[(graph.edges[east_on]["bearing_out"] - arriving) % 360] == TURN_STRAIGHT
    assert TURN_CLASS[(graph.edges[north]["bearing_out"] - arriving) % 360] == TURN_LEFT
    # away from merged nodes the road is the whole geometry
    plain = edge(95, 70)
    assert road_geometry(graph, plain).equals(graph.edges[plain]["geometry"])


def test_speeds_and_times(tmp_path: Path) -> None:
    graph = enriched("grid", tmp_path)
    for _, _, data in graph.edges(data=True):
        if data["name"] == "Baker Rd":  # maxspeed=35 mph
            assert data["speed_parsed"] and data["speed_kmh"] == pytest.approx(35 * KMH_PER_MPH)
        elif data["highway"] == "residential":
            assert not data["speed_parsed"]
            assert data["speed_kmh"] == pytest.approx(25 * KMH_PER_MPH)
        assert data["time_s"] == pytest.approx(data["length"] / (data["speed_kmh"] / 3.6))


def test_identities(tmp_path: Path) -> None:
    graph = enriched("grid", tmp_path)
    identity = {data["name"]: data["identity"] for _, _, data in graph.edges(data=True)}
    assert identity["Baker Rd"] == "OR 10"  # the ref first
    assert identity["Ivy St"] == "US 26"
    assert identity["Ash St"] == "ash street"  # else the normalised name


def test_units_pair_the_two_directions_of_a_road(tmp_path: Path) -> None:
    graph = enriched("grid", tmp_path)
    keys = sorted(graph.edges(keys=True))
    run = {tuple(graph.edges[k]["osm_nodes"]): k for k in keys}
    for key in keys:
        reverse = run.get(tuple(reversed(graph.edges[key]["osm_nodes"])))
        if graph.edges[key]["oneway"]:
            assert reverse is None and graph.edges[key]["unit"] == key
        else:
            assert reverse is not None and graph.edges[key]["unit"] == min(key, reverse)


def test_parallel_roads_are_separate_units(tmp_path: Path) -> None:
    # Two two-way ways join junctions 1 and 2 through different shape points, a one-way way
    # joins them too, and a spur makes 1 and 2 junctions.
    nodes = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (100.0, 50.0), 4: (100.0, -50.0), 5: (300.0, 0.0)}
    nodes |= {6: (100.0, 0.0), 7: (-100.0, 0.0)}
    ways = [
        (10, [1, 3, 2], {"highway": "residential", "name": "North Loop"}),
        (11, [1, 4, 2], {"highway": "residential", "name": "South Loop"}),
        (12, [1, 6, 2], {"highway": "residential", "name": "Middle Way", "oneway": "yes"}),
        (13, [2, 5], {"highway": "residential", "name": "East St"}),
        (14, [7, 1], {"highway": "residential", "name": "West St"}),
    ]
    graph = cleaned(write_osm(tmp_path / "loops.osm", nodes, ways))
    report = run_enrich(graph, NO_POIS)
    units: dict[tuple[int, int, int], set[str]] = {}
    for _, _, data in graph.edges(data=True):
        units.setdefault(data["unit"], set()).add(data["name"])
    assert sorted(len(names) for names in units.values()) == [1] * 5  # one road per unit
    assert (report.units, report.two_way_pairs) == (5, 4)
    assert graph.number_of_edges() == 9


def test_flags(tmp_path: Path) -> None:
    nodes = {i: (100.0 * i, 0.0) for i in range(1, 8)}
    tags = [
        {"bridge": "yes"},
        {"bridge": "no"},
        {"bridge": "viaduct", "tunnel": "no"},
        {"tunnel": "building_passage"},
        {"toll": "yes"},
        {"toll": "no"},
    ]
    ways = [
        (10 + i, [i + 1, i + 2], {"highway": "residential", "name": f"Way {i}", **t})
        for i, t in enumerate(tags)
    ]
    graph = cleaned(write_osm(tmp_path / "flags.osm", nodes, ways))
    report = run_enrich(graph, NO_POIS)
    flags = {data["name"]: data["flags"] for _, _, data in graph.edges(data=True)}
    assert flags == {
        "Way 0": EdgeFlag.BRIDGE,
        "Way 1": 0,
        "Way 2": EdgeFlag.BRIDGE,
        "Way 3": EdgeFlag.TUNNEL,
        "Way 4": EdgeFlag.TOLL,
        "Way 5": 0,
    }
    assert report.flags == {"bridge": 4, "tunnel": 2, "toll": 2, "roundabout": 0, "link": 0}


@pytest.mark.parametrize(
    ("data", "flags"),
    [
        ({"highway": "motorway_link"}, EdgeFlag.LINK),
        ({"highway": "tertiary_link", "bridge": "yes"}, EdgeFlag.LINK | EdgeFlag.BRIDGE),
        ({"highway": "residential", "junction": "roundabout"}, EdgeFlag.ROUNDABOUT),
        ({"highway": "primary", "junction": "circular"}, EdgeFlag.ROUNDABOUT),
        ({"highway": "primary", "junction": "jughandle"}, 0),
        ({"highway": "motorway", "toll": "yes", "tunnel": "yes"}, EdgeFlag.TOLL | EdgeFlag.TUNNEL),
    ],
)
def test_edge_flags(data: dict[str, str], flags: int) -> None:
    # Ramps and roundabout rings are almost all inside super-junctions after cleanup, so the
    # rule is checked on tags directly.
    assert edge_flags(data) == flags


def test_collectibles_inside_edges(tmp_path: Path) -> None:
    graph = enriched("grid", tmp_path)
    found = {
        (data["name"], data["toll_booths"], data["level_crossings"])
        for _, _, data in graph.edges(data=True)
        if data["toll_booths"] or data["level_crossings"]
    }
    # Each lies inside both directions of a two-way street; a route drives one of them.
    assert found == {("Baker Rd", 1, 0), ("Fir St", 0, 1)}
    counted = [d for _, _, d in graph.edges(data=True) if d["toll_booths"] or d["level_crossings"]]
    assert len(counted) == 4
    assert len({d["unit"] for d in counted if d["toll_booths"]}) == 1


def test_collectibles_at_junctions_and_off_the_graph() -> None:
    graph = cleaned(FIXTURES / "divided.osm")
    cluster = node_of(graph, 50)  # 50-53 merged, 54 a shape point of an absorbed edge
    pois = poi_table(
        [
            ("n", 52, "level_crossing"),  # a member: the cluster's lowest-key out-edge
            ("n", 54, "level_crossing"),  # an absorbed shape point is a member too
            ("n", 999, "level_crossing"),  # on no kept road
            ("w", 500, "toll_booth"),  # a way, not a node: not a collectible
            ("n", 71, "toll_booth"),  # inside the one-way eastbound carriageway
        ]
    )
    report = run_enrich(graph, pois)
    first = min(graph.out_edges(cluster, keys=True))
    crossings = {
        (u, v, k): d["level_crossings"] for u, v, k, d in graph.edges(keys=True, data=True)
    }
    assert {key: n for key, n in crossings.items() if n} == {first: 2}
    booths = [d for _, _, d in graph.edges(data=True) if d["toll_booths"]]
    assert len(booths) == 1 and booths[0]["oneway"] and 71 in booths[0]["osm_nodes"]
    assert report.collectibles == {
        "toll_booth": {"found": 1, "on_edges": 1, "at_junctions": 0, "off_graph": 0, "edges": 1},
        "level_crossing": {
            "found": 3,
            "on_edges": 0,
            "at_junctions": 2,
            "off_graph": 1,
            "edges": 1,
        },
    }


def test_delays() -> None:
    graph = cleaned(FIXTURES / "divided.osm")
    cluster = node_of(graph, 50)
    pois = poi_table(
        [
            ("n", 50, "traffic_signals"),
            ("n", 53, "traffic_signals"),
            ("n", 51, "stop"),
            ("n", 74, "stop"),  # a shape point: no delay
            ("n", 90, "stop"),  # the dead end of SW Oak St
        ]
    )
    report = run_enrich(graph, pois)
    delays = {n: d for n, d in graph.nodes(data="delay_s") if d}
    assert delays == {cluster: 30.0, node_of(graph, 90): 10.0}  # the largest, not the sum
    assert report.delays == {
        "stop": {"found": 3, "at_junctions": 2, "shape_points": 1, "off_graph": 0},
        "traffic_signals": {"found": 2, "at_junctions": 2, "shape_points": 0, "off_graph": 0},
    }
    assert report.delay_nodes == 2


def test_pass_through() -> None:
    graph = cleaned(FIXTURES / "culdesac.osm")
    report = run_enrich(graph, NO_POIS)
    dead_ends = {node_of(graph, osm_id) for osm_id in (1, 7, 22, 23)}
    for node, through in graph.nodes(data="pass_through"):
        assert through == (node not in dead_ends)
    assert report.pass_through == 3


def test_pass_through_on_one_way_streets() -> None:
    graph = cleaned(FIXTURES / "grid.osm")
    run_enrich(graph, NO_POIS)
    assert all(graph.nodes[n]["pass_through"] for n in graph.nodes)


def test_every_edge_and_node_is_enriched(tmp_path: Path) -> None:
    graph = enriched("cloverleaf", tmp_path)
    for _, _, data in graph.edges(data=True):
        assert {
            "speed_kmh",
            "speed_parsed",
            "time_s",
            "identity",
            "unit",
            "flags",
            "bearing_in",
            "bearing_out",
            "toll_booths",
            "level_crossings",
        } <= set(data)
        assert 0 <= data["bearing_in"] < 360 and 0 <= data["bearing_out"] < 360
        assert math.isfinite(data["time_s"]) and data["time_s"] > 0
    for _, data in graph.nodes(data=True):
        assert {"delay_s", "pass_through"} <= set(data)


def test_enrich_is_deterministic(tmp_path: Path) -> None:
    first, second = enriched("divided", tmp_path), enriched("divided", tmp_path)
    assert pickle.dumps(first) == pickle.dumps(second)


def test_save_stage(tmp_path: Path) -> None:
    source = FIXTURES / "grid.osm"
    graph = cleaned(source)
    report = run_enrich(graph, pois_of(source, tmp_path))
    (tmp_path / "b1" / "stages").mkdir(parents=True)
    save_stage(graph, report, tmp_path / "b1")
    with (tmp_path / "b1" / "stages" / "enrich.pkl").open("rb") as f:
        loaded = pickle.load(f)
    assert list(loaded.edges(keys=True, data=True)) == list(graph.edges(keys=True, data=True))
    summary = json.loads((tmp_path / "b1" / "stages" / "enrich.json").read_text())
    assert summary["speed_parsed"] == {"primary": 10}
    assert summary["units"] == 60 and summary["two_way_pairs"] == 50
    assert summary["delays"]["traffic_signals"]["at_junctions"] == 1


def test_parameters_are_json() -> None:
    values = parameters()
    assert json.loads(json.dumps(values)) == values
    assert values["SPEED_DEFAULT_MPH"]["alley"] == 10.0
    assert values["NODE_DELAY_S"] == {"stop": 10.0, "traffic_signals": 30.0}
    assert (values["BRAND_SNAP_M"], values["BRAND_MIN_LOCATIONS"]) == (100.0, 3)


def test_brands_are_grouped_by_wikidata_else_by_their_name() -> None:
    graph = cleaned(FIXTURES / "grid.osm")
    rows = [
        (0, 5.0, "Acme", "Q1", None),
        (1, 5.0, "Acme", "Q1", None),
        (2, 5.0, "ACME", "Q1", None),
        (2, -5.0, None, "Q1", "Acme Store"),  # no spelling; a second POI at node 2
        (3, 5.0, "Acme", None, None),  # no wikidata: another brand, though spelled the same
        (4, 5.0, "acme", None, None),
        (5, 5.0, " Acme ", None, None),
        (0, -5.0, "Plaid Pantry", None, None),  # beside Acme: two brands at one node
        (7, 5.0, "plaid  PANTRY", None, None),
        (8, 5.0, "Plaid Pantry", None, None),
        (9, 5.0, None, "Q9", "Zed's"),  # no spelling: named by its most common name
        (10, 5.0, None, "Q9", "Zed's"),
        (11, 5.0, None, "Q9", None),
        (12, 5.0, None, "Q8", None),  # neither: named by its wikidata id
        (13, 5.0, None, "Q8", None),
        (14, 5.0, None, "Q8", None),
        (15, 5.0, "Two Spot", None, None),  # three POIs on two nodes: left out
        (15, -5.0, "Two Spot", None, None),
        (16, 5.0, "Two Spot", None, None),
        (17, 5.0, "", None, None),  # an empty brand tag names no brand
        (18, 5.0, "  ", None, None),
        (19, 5.0, "", None, "Corner Store"),
    ]
    pois = brand_pois([(*east_of(graph, node, dx), *tags) for node, dx, *tags in rows])
    report = run_enrich(graph, pois)
    assert graph.graph["brands"] == [
        ("Acme", None, [(3, 1), (4, 1), (5, 1)]),
        ("Acme", "Q1", [(0, 1), (1, 1), (2, 2)]),
        ("Plaid Pantry", None, [(0, 1), (7, 1), (8, 1)]),
        ("Q8", "Q8", [(12, 1), (13, 1), (14, 1)]),
        ("Zed's", "Q9", [(9, 1), (10, 1), (11, 1)]),
    ]
    assert report.brands == {
        "found": 22,
        "snapped": 22,
        "at_dead_ends": 0,
        "too_far": 0,
        "brands": 6,
        "kept": 5,
        "rows": 15,
        "nodes": 14,
    }


def test_a_brand_poi_snaps_within_100_m_to_a_node_a_route_can_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(enrich, "BRAND_MIN_LOCATIONS", 1)
    grid = cleaned(FIXTURES / "grid.osm")
    corner = min(grid.nodes, key=lambda v: (grid.nodes[v]["x"], grid.nodes[v]["y"]))
    near, far = east_of(grid, corner, -99.0), east_of(grid, corner, -101.0)
    report = run_enrich(grid, brand_pois([(*near, "Near", None, None), (*far, "Far", None, None)]))
    assert grid.graph["brands"] == [("Near", None, [(corner, 1)])]
    assert (report.brands["snapped"], report.brands["too_far"]) == (1, 1)

    # Clinton Ct runs 111 m north from Clinton St (OSM node 3) to its cul-de-sac end (7).
    culdesac = cleaned(FIXTURES / "culdesac.osm")
    rows = [
        (-122.6430, 45.5612, "Kiosk", None, None),  # 22 m from the end, 133 m from node 3
        (-122.6430, 45.5606, "Shop", None, None),  # 44 m from the end, 67 m from node 3
        (-122.6430, 45.5640, "Shop", None, None),  # 333 m from the end
    ]
    report = run_enrich(culdesac, brand_pois(rows))
    assert culdesac.graph["brands"] == [("Shop", None, [(node_of(culdesac, 3), 1)])]
    assert report.brands == {
        "found": 3,
        "snapped": 1,
        "at_dead_ends": 1,
        "too_far": 1,
        "brands": 2,
        "kept": 1,
        "rows": 1,
        "nodes": 1,
    }


def test_no_brand_pois_index_no_brands() -> None:
    graph = cleaned(FIXTURES / "grid.osm")
    report = run_enrich(graph, NO_POIS)
    assert graph.graph["brands"] == []
    assert report.brands == dict.fromkeys(
        ("found", "snapped", "at_dead_ends", "too_far", "brands", "kept", "rows", "nodes"), 0
    )
