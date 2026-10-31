"""The arrays engine against the networkx engine, on every fixture: the same build, byte for byte.

Each stage the arrays engine runs is compared with the networkx engine's: the raw graph, the
simplified graph (in networkx's iteration order, keys aside), then the whole of cleanup, and the
saved build (every array, the strings, the graph_version). Cases beyond the fixtures exercise
what simplification can meet: rings and loops that close on themselves, parallel chains, a way
drawn against its neighbours, a chain whose nodes are visited in no particular order, two one-way
ways overlapping in part, a road mapped several times over one run of nodes.
"""

import dataclasses
import json
import math
import pickle
from pathlib import Path

import networkx as nx
import numpy as np
import osmium
import pyproj
import pytest

from tests.pipeline import test_carriageways as carriageway_cases
from tests.pipeline import test_cleanup as cleanup_cases
from tests.pipeline import test_consolidate as consolidate_cases
from tests.pipeline.chain import FIXTURES, UTM_10N, pois_of
from tests.pipeline.copies import (
    COPIES,
    OVERLAPS,
    US_322_NODE,
    mapped_copies,
    overlapping,
    us_322,
)
from tests.pipeline.invariants import check_all
from tests.pipeline.osm import write_osm
from tests.pipeline.parity import assert_same_build, assert_same_graph, raw_graph, read_back, same
from tests.pipeline.random_roads import random_roads
from tests.pipeline.test_consolidate import _near_pair
from worst_route.graph.data import FORMAT_VERSION, GraphData
from worst_route.pipeline import cleanup
from worst_route.pipeline.arrays import cleaned
from worst_route.pipeline.arrays import connectivity as arrays_connectivity
from worst_route.pipeline.arrays.build import build_raw_arrays, load_stage, save_stage
from worst_route.pipeline.arrays.simplify import SimplifiedGraph
from worst_route.pipeline.arrays.simplify import simplify as arrays_simplify
from worst_route.pipeline.arrays.stage_a import StageAGraph
from worst_route.pipeline.arrays.stage_a import stage_a as arrays_stage_a
from worst_route.pipeline.arrays.stage_b import StageBGraph
from worst_route.pipeline.arrays.stage_b import stage_b as arrays_stage_b
from worst_route.pipeline.arrays.to_nx import (
    cleanup_graph,
    simplified_graph,
    stage_a_graph,
    stage_b_graph,
)
from worst_route.pipeline.arrays.twins import find_twins as arrays_find_twins
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.carriageways import find_twins
from worst_route.pipeline.cleanup import FragmentedGraphError, run_arrays_cleanup, run_cleanup
from worst_route.pipeline.connectivity import largest_component
from worst_route.pipeline.consolidate import relabel, stage_a, stage_a_tolerances, stage_b
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.save import graph_data
from worst_route.pipeline.simplify import simplify
from worst_route.pipeline.tiers import run_tiers

ALL_FIXTURES = sorted(path.stem for path in FIXTURES.glob("*.osm"))
META = {"region": "test", "build_id": "test", "crs_epsg": UTM_10N, "created": "", "params": {}}
META |= {"pipeline_version": "", "sources": []}


def assert_same_cleanup(source: Path) -> nx.MultiDiGraph:
    """Both engines' simplified, stage A and stage B graphs, twins, and cleaned graphs and reports
    are the same; the simplified graph."""
    raw = read_back(build_raw_graph(source, UTM_10N)[0])
    arrays, _ = build_raw_arrays(source, UTM_10N)
    simplified, _ = simplify(raw)
    arrays_simplified = arrays_simplify(arrays)[0]
    assert_same_simplified(simplified, arrays_simplified)
    want_a, want_a_stats = stage_a(simplified)
    got_a, got_a_stats = arrays_stage_a(arrays_simplified)
    assert got_a_stats == want_a_stats
    assert_same_stage_a(want_a, got_a)
    want_twins = find_twins(want_a)
    twin_indptr, twin_edges, got_twins = arrays_find_twins(got_a)
    assert got_twins == want_twins
    got_table = stage_a_graph(got_a, (twin_indptr, twin_edges)).graph["twins"]
    assert same(want_a.graph["twins"], got_table)
    want_b, want_b_stats = stage_b(want_a)
    got_b, got_b_stats = arrays_stage_b(got_a, (twin_indptr, twin_edges))
    assert got_b_stats == want_b_stats
    assert_same_graph(want_b, stage_b_graph(got_b))
    want, want_report = run_cleanup(raw, allow_fragmented=True)
    got, got_report = run_cleanup(arrays, allow_fragmented=True)
    assert_same_graph(want, got)
    assert pickle.dumps(got, pickle.HIGHEST_PROTOCOL) == pickle.dumps(want, pickle.HIGHEST_PROTOCOL)
    assert dataclasses.replace(got_report, seconds={}) == dataclasses.replace(
        want_report, seconds={}
    )
    check_all(raw, got, complete=got_report.connectivity.dropped_nodes == 0)
    return simplified


def assert_same_simplified(want: nx.MultiDiGraph, got: SimplifiedGraph) -> None:
    """The arrays hold networkx's simplified graph in its iteration order (which the adapter
    does not need: networkx groups the edges it adds by neighbour itself; the arrays' own order
    is what the next stages rank by), and the adapter makes networkx's graph."""
    ends = zip(got.node_id[got.edge_u].tolist(), got.node_id[got.edge_v].tolist(), strict=True)
    assert list(ends) == list(want.edges())
    assert_same_graph(want, simplified_graph(got))


def assert_same_stage_a(want: nx.MultiDiGraph, got: StageAGraph) -> None:
    """The arrays hold networkx's stage A graph in its iteration order (which the adapter would
    regroup by neighbour, as in `assert_same_simplified`)."""
    label = got.node_label
    ends = zip(label[got.edge_u].tolist(), label[got.edge_v].tolist(), strict=True)
    assert list(ends) == list(want.edges())
    assert_same_graph(want, stage_a_graph(got))


def build_of(source: Path, tmp: Path, *, arrays: bool) -> GraphData:
    raw = build_raw_arrays(source, UTM_10N)[0] if arrays else build_raw_graph(source, UTM_10N)[0]
    graph, _ = run_cleanup(raw, allow_fragmented=True)
    run_enrich(graph, pois_of(source, tmp))
    tiers = [t.node_ids for t in run_tiers(graph, allow_fragmented=True).tiers]
    return graph_data(graph, tiers, dict(META))


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_the_raw_graph_is_the_same(name: str) -> None:
    source = FIXTURES / f"{name}.osm"
    want, want_stats = build_raw_graph(source, UTM_10N)
    got, got_stats = build_raw_arrays(source, UTM_10N)
    assert got_stats == want_stats
    assert_same_graph(want, raw_graph(got))


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_the_simplified_graph_is_the_same(name: str) -> None:
    source = FIXTURES / f"{name}.osm"
    want, want_stats = simplify(build_raw_graph(source, UTM_10N)[0])
    simplified, got_stats = arrays_simplify(build_raw_arrays(source, UTM_10N)[0])
    assert got_stats == want_stats
    assert_same_simplified(want, simplified)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_cleanup_is_the_same(name: str) -> None:
    assert_same_cleanup(FIXTURES / f"{name}.osm")


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_the_build_is_the_same(name: str, tmp_path: Path) -> None:
    source = FIXTURES / f"{name}.osm"
    assert_same_build(
        build_of(source, tmp_path, arrays=False), build_of(source, tmp_path, arrays=True)
    )


def test_the_stage_reads_back(tmp_path: Path) -> None:
    graph, stats = build_raw_arrays(FIXTURES / "grid.osm", UTM_10N)
    save_stage(graph, stats, tmp_path)
    assert (tmp_path / "stages" / "build" / "manifest.json").exists()
    assert (tmp_path / "stages" / "build.json").exists()
    back = load_stage(tmp_path)
    assert back.crs_epsg == graph.crs_epsg and back.tagsets == graph.tagsets
    assert_same_graph(raw_graph(graph), raw_graph(back))


def test_a_node_without_a_location_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "nowhere.osm"
    source.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n'
        '  <node id="1" version="1" lat="45.5" lon="-122.6"/>\n'
        '  <node id="2" version="1"/>\n'
        '  <node id="3" version="1" lat="45.501" lon="-122.6"/>\n'
        '  <way id="1" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/>'
        '<tag k="highway" v="residential"/></way>\n</osm>\n'
    )
    for build in (build_raw_graph, build_raw_arrays):
        with pytest.raises(osmium.InvalidLocationError):
            build(source, UTM_10N)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_the_stage_a_graph_is_the_same(name: str) -> None:
    source = FIXTURES / f"{name}.osm"
    want, want_stats = stage_a(simplify(build_raw_graph(source, UTM_10N)[0])[0])
    got, got_stats = arrays_stage_a(arrays_simplify(build_raw_arrays(source, UTM_10N)[0])[0])
    assert got_stats == want_stats
    assert_same_stage_a(want, got)


# Simplification's own cases ---------------------------------------------------------------

STREET = {"highway": "residential", "name": "Pine Street"}


def test_rings_and_loops(tmp_path: Path) -> None:
    """Chains whose last merge would close them on themselves: a one-way and a two-way loop off
    a street and a loop off a junction keep their highest inner node; a ring touching nothing
    keeps its two highest nodes."""
    nodes = {i: (100.0 * (i % 10), 100.0 * (i // 10)) for i in range(1, 40)}
    oneway = {**STREET, "oneway": "yes"}
    ways = [
        (1, [1, 2, 3], STREET),
        (2, [3, 13, 12], oneway),
        (3, [12, 11], oneway),
        (4, [11, 21, 22], oneway),
        (5, [22, 3], oneway),
        (6, [1, 31, 32], STREET),
        (7, [32, 33, 34], STREET),
        (8, [34, 24, 23], STREET),
        (9, [23, 32], STREET),
        (10, [2, 7, 8], STREET),
        (11, [8, 9, 19], STREET),
        (12, [19, 18, 17], STREET),
        (13, [17, 8], STREET),
        (14, [36, 37], oneway),
        (15, [37, 38], oneway),
        (16, [38, 36], oneway),
    ]
    simplified = assert_same_cleanup(write_osm(tmp_path / "rings.osm", nodes, ways))
    assert sorted(simplified) == [1, 2, 3, 8, 19, 22, 32, 34, 37, 38]


def test_parallel_chains_and_reversed_ways(tmp_path: Path) -> None:
    """Two chains between the same two junctions (parallel edges once simplified), a way drawn
    against the chain it continues (a node kept: its edges' `reversed` differ), duplicate ways,
    and node ids in no order along the chains, so merges happen out of order."""
    nodes = {
        50: (0.0, 0.0),
        3: (100.0, 0.0),
        41: (200.0, 0.0),
        7: (300.0, 0.0),
        60: (400.0, 0.0),
        12: (100.0, 80.0),
        90: (200.0, 90.0),
        5: (300.0, 80.0),
        15: (200.0, 160.0),
        70: (0.0, -100.0),
        80: (400.0, -100.0),
        33: (200.0, -50.0),
    }
    ways = [
        (1, [50, 3], STREET),
        (2, [3, 41], STREET),
        (3, [7, 41], STREET),  # drawn the other way
        (4, [7, 60], STREET),
        (5, [50, 12, 90], STREET),
        (6, [90, 5], STREET),
        (7, [5, 60], STREET),
        (8, [70, 50], {"highway": "tertiary", "name": "Oak Street"}),
        (9, [60, 80], {"highway": "tertiary", "name": "Oak Street"}),
        (10, [3, 33, 7], {**STREET, "oneway": "yes"}),
        (11, [3, 33, 7], {**STREET, "oneway": "yes"}),  # a duplicate way, another id
        (12, [50, 15], STREET),
        (13, [15, 60], STREET),
    ]
    simplified = assert_same_cleanup(write_osm(tmp_path / "parallel.osm", nodes, ways))
    assert simplified.number_of_edges(50, 60) == simplified.number_of_edges(60, 50) == 2
    assert 41 in simplified and simplified.number_of_edges(3, 33) == 2


def test_an_adjacency_entry_keeps_its_place(tmp_path: Path) -> None:
    """networkx lists a node's edges by neighbour, each neighbour where its first edge came: two
    roads from 1 to 2 drawn before and after a road from 1 to 3 come out 2, 2, 3, not by their
    own order 2, 3, 2."""
    nodes = {1: (0.0, 0.0), 2: (100.0, 0.0), 3: (0.0, 100.0), 4: (50.0, 40.0), 5: (-100.0, 0.0)}
    ways = [
        (1, [1, 2], STREET),
        (2, [1, 3], {"highway": "residential", "name": "Oak Street"}),
        (3, [1, 4, 2], {"highway": "residential", "name": "Elm Street"}),
        (4, [5, 1], {"highway": "tertiary", "name": "Ash Street"}),
    ]
    simplified = assert_same_cleanup(write_osm(tmp_path / "entries.osm", nodes, ways))
    assert [v for _, v in simplified.out_edges(1)] == [2, 2, 3, 5]


@pytest.mark.parametrize(
    "ways",
    [
        [(1, [1, 1], {"highway": "footway"})],  # nothing kept
        [(1, [1, 2], STREET)],  # one segment
        [(1, [3, 3], STREET), (2, [1, 3, 2], STREET)],  # a way of one node makes a junction
    ],
    ids=["nothing kept", "one segment", "a one-node way"],
)
def test_small_inputs(tmp_path: Path, ways: list[tuple[int, list[int], dict[str, str]]]) -> None:
    nodes = {1: (0.0, 0.0), 2: (50.0, 0.0), 3: (0.0, 50.0)}
    assert_same_cleanup(write_osm(tmp_path / "small.osm", nodes, ways))


def test_two_carriageways_meeting_at_a_node(tmp_path: Path) -> None:
    """US 322 (`copies.us_322`), whose node 106661013 the national build's save refused: the node
    stays on both engines, whose cleanup and build are the same, and the build saves and
    validates."""
    source = us_322(tmp_path / "us322.osm")
    assert US_322_NODE in assert_same_cleanup(source)
    want, got = build_of(source, tmp_path, arrays=False), build_of(source, tmp_path, arrays=True)
    assert_same_build(want, got)
    for data in (want, got):  # deep, with the format a written build's meta names
        dataclasses.replace(data, meta={**data.meta, "format": FORMAT_VERSION}).validate()


def test_merged_edges_through_a_node(tmp_path: Path) -> None:
    """At a node with two edges in and two out, merged edges are the two directions of one road
    only when one contraction made them so. A two-way street in three ways: 20 goes, then 30,
    whose edges to 10 are the two merged at 20. A one-way ring 50 -> 1 -> 60 -> 2 -> 50 meeting
    one-way ways 60 -> 70 and 70 -> 60: 1 and 2 go, but 60 stays, its two edges to 50 merged at
    different nodes and not one run reversed."""
    one_way = {**STREET, "oneway": "yes"}
    oak = {"highway": "residential", "name": "Oak Street"}
    nodes = {10: (0.0, 0.0), 20: (100.0, 0.0), 30: (200.0, 0.0), 40: (300.0, 0.0)}
    nodes |= {11: (0.0, -100.0), 41: (300.0, -100.0)}
    nodes |= {50: (0.0, 500.0), 1: (100.0, 550.0), 60: (200.0, 500.0), 2: (100.0, 450.0)}
    nodes |= {70: (300.0, 500.0), 71: (300.0, 400.0)}
    ways = [
        (1, [10, 20], STREET),
        (2, [20, 30], STREET),
        (3, [30, 40], STREET),
        (4, [11, 10], oak),
        (5, [40, 41], oak),
        (6, [50, 1], one_way),
        (7, [1, 60], one_way),
        (8, [60, 2], one_way),
        (9, [2, 50], one_way),
        (10, [60, 70], one_way),
        (11, [70, 60], one_way),
        (12, [70, 71], oak),
    ]
    simplified = assert_same_cleanup(write_osm(tmp_path / "merged.osm", nodes, ways))
    assert sorted(simplified) == [10, 11, 40, 41, 50, 60, 70, 71]


@pytest.mark.parametrize(("mill_rd", "runs"), OVERLAPS.values(), ids=OVERLAPS.keys())
def test_two_one_way_ways_over_a_node_in_opposite_directions(
    tmp_path: Path, mill_rd: list[tuple[int, list[int]]], runs: list[tuple[int, ...]]
) -> None:
    """Two one-way ways over node 2 in opposite directions (`copies.overlapping`): over the same
    nodes, one road whose node 2 goes; overlapping in part, node 2 stays, whichever way the file
    lists first (the arrays engine then meets the pair that is no reverse at the second
    neighbour) and when both ways have a node of their own between 1 and 2 (two runs as long,
    compared ref by ref). The same cleanup and build on both engines."""
    source = overlapping(tmp_path / "overlap.osm", mill_rd)
    assert (2 in assert_same_cleanup(source)) == (len(runs) == 4)
    assert_same_build(
        build_of(source, tmp_path, arrays=False), build_of(source, tmp_path, arrays=True)
    )


@pytest.mark.parametrize("inner", [False, True], ids=["one segment", "an inner junction"])
@pytest.mark.parametrize("copies", COPIES.values(), ids=COPIES.keys())
def test_a_road_mapped_several_times(
    tmp_path: Path, copies: list[tuple[bool, bool]], inner: bool
) -> None:
    """Mill Rd mapped two or three times over one run of OSM nodes (`copies.py`): the same
    cleanup and the same build, its copies one road unit, on both engines."""
    source = mapped_copies(tmp_path / "copies.osm", copies, inner=inner)
    assert_same_cleanup(source)
    assert_same_build(
        build_of(source, tmp_path, arrays=False), build_of(source, tmp_path, arrays=True)
    )


# Stage A's own cases ------------------------------------------------------------------------


def test_pairs_near_the_sum_of_their_radii(tmp_path: Path) -> None:
    """Pairs of junctions on a primary road (15 m buffers) a few centimetres closer than 30 m, in
    several directions: their 64-sided buffers' corners decide whether they meet, so some merge
    and some do not, which their distance alone would not tell."""
    nodes: dict[int, tuple[float, float]] = {}
    ways: list[tuple[int, list[int], dict[str, str]]] = []
    primary = {"highway": "primary", "name": "Main Street"}
    pairs = []
    for k, (fraction, angle) in enumerate(
        (f, a) for f in (0.999, 0.9995, 0.99975) for a in (0.0, 2.8125, 45.0, 88.0)
    ):
        a, b = 10 * k + 1, 10 * k + 2
        x0, y0 = 500.0 * k, 0.0
        d = 30.0 * fraction
        nodes[a] = (x0, y0)
        nodes[b] = (x0 + d * math.cos(math.radians(angle)), y0 + d * math.sin(math.radians(angle)))
        nodes[a + 2], nodes[b + 2] = (x0 - 100.0, y0 - 100.0), (x0 + 100.0, y0 - 100.0)
        ways += [(a, [a, b], primary), (a + 2, [a, a + 2], STREET), (b + 2, [b, b + 2], STREET)]
        pairs.append((a, b))
    simplified = assert_same_cleanup(write_osm(tmp_path / "band.osm", nodes, ways))
    merged = {n: d["members"] for n, d in stage_a(simplified)[0].nodes(data=True)}
    tolerances = stage_a_tolerances(simplified)
    band = set()
    for a, b in pairs:
        reach = tolerances[a] + tolerances[b]
        d = math.dist(*((simplified.nodes[n]["x"], simplified.nodes[n]["y"]) for n in (a, b)))
        if reach * math.cos(math.pi / 64) < d < reach:
            band.add(any(a in m and b in m for m in merged.values()))
    assert band == {True, False}


def test_a_clusters_edges_come_by_neighbour(tmp_path: Path) -> None:
    """Junctions 1 and 2 merge; 1's roads to 3 and to 4 come before 2's road to 3 among the
    simplified edges, but OSMnx's graph lists the cluster's edges by neighbour: 3, 3, 4."""
    nodes = {1: (0.0, 0.0), 2: (10.0, 0.0), 3: (0.0, 100.0), 4: (-100.0, 0.0), 5: (0.0, 200.0)}
    ways = [
        (1, [1, 2], {"highway": "primary", "name": "Main Street"}),
        (2, [1, 3], STREET),
        (3, [1, 4], {"highway": "residential", "name": "Oak Street"}),
        (4, [2, 3], {"highway": "residential", "name": "Elm Street"}),
        (5, [3, 5], {"highway": "residential", "name": "Ash Street"}),
    ]
    simplified = assert_same_cleanup(write_osm(tmp_path / "neighbours.osm", nodes, ways))
    merged, _ = stage_a(simplified)
    label = {members[0]: node for node, members in merged.nodes(data="members")}
    targets = [merged.nodes[v]["members"][0] for _, v in merged.out_edges(label[1])]
    assert merged.nodes[label[1]]["members"] == (1, 2) and targets == [3, 3, 4]


def _touching_pair(d: float) -> SimplifiedGraph:
    """Junctions 1 and 2 (8 m buffers) `d` apart, joined by a street, each with a dead end."""
    x0, y0 = 525000.0, 5040000.0
    xs = np.array([x0, x0 + d, x0 - 100.0, x0 + d + 100.0])
    ys = np.array([y0, y0, y0 - 100.0, y0 - 100.0])
    lon, lat = pyproj.Transformer.from_crs(UTM_10N, 4326, always_xy=True).transform(xs, ys)
    u = np.array([0, 0, 1, 1, 2, 3], dtype=np.int32)  # networkx's order: by tail, then head
    v = np.array([1, 2, 0, 3, 0, 1], dtype=np.int32)
    ends = np.column_stack([u, v]).ravel()
    ids = np.array([1, 2, 3, 4], dtype=np.int64)
    return SimplifiedGraph(
        crs_epsg=UTM_10N,
        node_id=ids,
        node_x=xs,
        node_y=ys,
        node_lon=np.asarray(lon),
        node_lat=np.asarray(lat),
        edge_u=u,
        edge_v=v,
        edge_tags=np.zeros(6, dtype=np.int32),
        edge_oneway=np.zeros(6, dtype=np.bool_),
        edge_reversed=np.array([False, False, True, False, True, True]),
        edge_length=np.hypot(xs[u] - xs[v], ys[u] - ys[v]),
        edge_merged=np.zeros(6, dtype=np.bool_),
        edge_insertion=np.arange(6, dtype=np.int64),
        way_indptr=np.arange(7, dtype=np.int64),
        way_id=np.array([10, 11, 10, 12, 11, 12], dtype=np.int64),
        osmnode_indptr=np.arange(0, 13, 2, dtype=np.int64),
        osmnode_id=ids[ends],
        coord_x=xs[ends],
        coord_y=ys[ends],
        tagsets=[("residential", (("name", "Pine Street"),))],
    )


@pytest.mark.parametrize(("d", "clusters"), [(16.0, 0), (15.9, 1), (16.1, 0)])
def test_buffers_touching_at_a_corner(d: float, clusters: int) -> None:
    """At 16 m the two buffers meet at one corner only: OSMnx keeps them apart, and so must the
    arrays (a hair closer they merge)."""
    graph = _touching_pair(d)
    want, want_stats = stage_a(simplified_graph(graph))
    got, got_stats = arrays_stage_a(graph)
    assert got_stats == want_stats and got_stats.clusters == clusters
    assert_same_stage_a(want, got)


def test_a_split_group(tmp_path: Path) -> None:
    """A group of three buffers that no short edge joins to its third member: the pair sits at
    its points' centroid, as OSMnx places a split group, on both engines."""
    simplified = assert_same_cleanup(_near_pair(tmp_path, overpass=True))
    assert stage_a(simplified)[1].cluster_sizes == {2: 1}


# Twins' own cases -------------------------------------------------------------------------


def test_a_stub_is_not_a_carriageway(tmp_path: Path) -> None:
    """An eastbound carriageway ends at a junction merged into a cluster just east of it, and a
    westbound one of the same road runs 10 m north from beyond the cluster: only the stub to the
    cluster's position runs beside it, and a stub is left out, so they are no twins."""
    nodes = {1: (-100.0, 0.0), 2: (0.0, 0.0), 3: (12.0, 0.0), 4: (50.0, 10.0), 5: (-1.0, 10.0)}
    road = {"highway": "primary", "ref": "OR 99W", "oneway": "yes"}
    ways = [(1, [1, 2], road), (2, [2, 3], STREET), (3, [4, 5], road)]
    simplified = assert_same_cleanup(write_osm(tmp_path / "stub.osm", nodes, ways))
    merged, stats = stage_a(simplified)
    assert stats.cluster_sizes == {2: 1}
    assert find_twins(merged).candidates == 2 and merged.graph["twins"] == {}


def test_a_way_beside_itself_is_one_road(tmp_path: Path) -> None:
    """A one-way way out 200 m east and back west 20 m north of itself, split where a street
    leaves it: its two edges run side by side in opposite directions, but share their way."""
    nodes = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (200.0, 20.0), 4: (0.0, 20.0), 5: (300.0, 20.0)}
    road = {"highway": "primary", "ref": "OR 8", "oneway": "yes"}
    ways = [(1, [1, 2, 3, 4], road), (2, [3, 5], STREET)]
    simplified = assert_same_cleanup(write_osm(tmp_path / "hairpin.osm", nodes, ways))
    merged, _ = stage_a(simplified)
    assert find_twins(merged).candidates == 2 and merged.graph["twins"] == {}


def test_a_road_mapped_twice(tmp_path: Path) -> None:
    """Two one-way ways over the same nodes in opposite directions, beside a divided road: one
    road mapped twice is no twin pair but a count, and the divided road's pair is still found."""
    nodes = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (0.0, 30.0), 4: (200.0, 30.0)}
    nodes |= {5: (0.0, 100.0), 6: (100.0, 100.0), 7: (200.0, 100.0)}
    road = {"highway": "primary", "ref": "OR 8", "oneway": "yes"}
    ways = [(1, [1, 2], road), (2, [4, 3], road), (3, [5, 6, 7], road), (4, [7, 6, 5], road)]
    simplified = assert_same_cleanup(write_osm(tmp_path / "twice.osm", nodes, ways))
    merged, _ = stage_a(simplified)
    stats = find_twins(merged)
    assert stats.same_road_pairs == 1 and stats.pairs == 1


@pytest.mark.parametrize("seed", [13, 26, 48])
def test_the_reviewers_random_roads(tmp_path: Path, seed: int) -> None:
    """The random graphs of the review (`random_roads.py`) that map a road twice: cleanup and
    the build are the same on both engines, the road counted once in each."""
    nodes, ways = random_roads(seed)
    source = write_osm(tmp_path / f"random-{seed}.osm", nodes, ways)
    assert_same_cleanup(source)
    assert_same_build(
        build_of(source, tmp_path, arrays=False), build_of(source, tmp_path, arrays=True)
    )
    _, report = run_cleanup(build_raw_arrays(source, UTM_10N)[0], allow_fragmented=True)
    assert report.twins.same_road_pairs > 0


# Stage B's own cases: those of the networkx engine's tests ------------------------------

STAGE_B_CASES = {
    "half diamond": lambda tmp: consolidate_cases.half_diamond(tmp),
    "slip lane near": lambda tmp: consolidate_cases.slip_lane(tmp, 60),
    "slip lane far": lambda tmp: consolidate_cases.slip_lane(tmp, 200),
    "motorway slip": lambda tmp: consolidate_cases.slip_lane(tmp, 60, "motorway_link"),
    "merging branches": lambda tmp: consolidate_cases.merging_branches(tmp),
    "two interchanges": lambda tmp: consolidate_cases.interchanges(tmp, "B St"),
    "one interchange": lambda tmp: consolidate_cases.interchanges(tmp, "A St"),
    "too wide": lambda tmp: consolidate_cases.wide(tmp),
    "detached": lambda tmp: consolidate_cases.detached(tmp),
    "bridged diamond": lambda tmp: consolidate_cases.bridged_diamond(tmp),
    "crossover": lambda tmp: consolidate_cases.crossover(tmp),
    "one-sided ramps": lambda tmp: carriageway_cases.one_sided(tmp),
    "offset gores": lambda tmp: carriageway_cases.offset_gores(tmp),
    "corbett": lambda tmp: carriageway_cases.corbett(tmp, linked=False),
    "corbett linked": lambda tmp: carriageway_cases.corbett(tmp, linked=True),
    "cornell": lambda tmp: carriageway_cases.cornell(tmp),
    "woodburn": lambda tmp: consolidate_cases.woodburn(tmp),
    "woodburn far": lambda tmp: consolidate_cases.woodburn(tmp, far=True),
    "woodburn fork 0": lambda tmp: consolidate_cases.woodburn(tmp, fork=0),
    "woodburn fork 1": lambda tmp: consolidate_cases.woodburn(tmp, fork=1),
    "mlk": lambda tmp: consolidate_cases.mlk(tmp),
    "block": lambda tmp: consolidate_cases.block(tmp),
    "turn back": lambda tmp: consolidate_cases.turn_back(tmp),
    "cut interchange": lambda tmp: cleanup_cases.cut_interchange(tmp),
    "cut beyond": lambda tmp: cleanup_cases.cut_beyond(tmp),
}


@pytest.mark.parametrize("case", sorted(STAGE_B_CASES))
def test_stage_b_cases(tmp_path: Path, case: str) -> None:
    assert_same_cleanup(STAGE_B_CASES[case](tmp_path))


def test_a_twin_that_is_a_boundary_edge(tmp_path: Path) -> None:
    """Corbett's interchange with a ramp from the southbound carriageway at 16 to Bancroft St:
    16 joins the super-node, so the twin of the northbound stretch inside it (12-16) is a
    boundary edge of it, and the transitions that must drive the stretch ban only what a passage
    through it could drive."""
    source = carriageway_cases.corbett(tmp_path, linked=False)
    text = source.read_text().replace(
        "</osm>",
        '  <node id="47" version="1" lat="45.4977503" lon="-122.5992302"/>\n'
        '  <way id="12" version="1"><nd ref="16"/><nd ref="47"/><nd ref="41"/>'
        '<tag k="highway" v="motorway_link"/></way>\n</osm>',
    )
    source.write_text(text)
    simplified = assert_same_cleanup(source)
    merged, _ = stage_a(simplified)
    find_twins(merged)
    _, stats = stage_b(merged)
    assert stats.twin_transitions > 0 and sum(stats.superjunctions.values()) == 1


# Connectivity's own cases ----------------------------------------------------------------


def test_components_of_one_size(tmp_path: Path) -> None:
    """Three roads of two junctions each and a one-way spur off one: the component kept is the
    one holding the smallest node, the others are listed by size and then length, and both
    engines refuse the graph alike unless told to keep it."""
    nodes = {1: (0, 0), 2: (300, 0), 3: (0, 500), 4: (200, 500)}
    nodes |= {5: (0, 1000), 6: (400, 1000), 7: (600, 1000)}
    road = {"highway": "residential"}
    ways = [(10, [3, 4], road), (11, [1, 2], road), (12, [5, 6], road)]
    ways.append((13, [6, 7], {**road, "oneway": "yes"}))
    source = write_osm(tmp_path / "components.osm", nodes, ways)
    assert_same_cleanup(source)
    graph, report = run_cleanup(build_raw_arrays(source, UTM_10N)[0], allow_fragmented=True)
    assert [graph.nodes[n]["members"] for n in graph] == [(1,), (2,)]
    dropped = report.connectivity.largest_dropped
    assert [c.nodes for c in dropped] == [2, 2, 1]
    assert dropped[0].length_m > dropped[1].length_m > dropped[2].length_m == 0
    refusals = []
    for raw in (
        read_back(build_raw_graph(source, UTM_10N)[0]),
        build_raw_arrays(source, UTM_10N)[0],
    ):
        with pytest.raises(FragmentedGraphError) as refused:
            run_cleanup(raw)
        refusals.append(str(refused.value))
    assert refusals[0] == refusals[1]


def test_a_twin_left_without_its_pair(tmp_path: Path) -> None:
    """A one-way side of a block and, beside it, a one-way carriageway leading nowhere: the
    carriageway is dropped, and the side it was a twin of keeps no twins."""
    nodes = {1: (0, 0), 2: (0, 400), 3: (300, 400), 4: (300, 0), 5: (20, 350), 6: (20, 50)}
    road = {"highway": "residential"}
    primary = {"highway": "primary", "name": "A Ave", "oneway": "yes"}
    ways = [(10, [1, 2], primary), (11, [2, 3, 4, 1], road), (12, [5, 6], primary)]
    source = write_osm(tmp_path / "twin.osm", nodes, ways)
    simplified = assert_same_cleanup(source)
    merged, _ = stage_a(simplified)
    find_twins(merged)
    assert merged.graph["twins"]
    graph, report = run_cleanup(build_raw_arrays(source, UTM_10N)[0], allow_fragmented=True)
    assert report.connectivity.dropped_nodes == 2 and not graph.graph["twins"]


def arrays_stage_b_of(source: Path) -> StageBGraph:
    merged = arrays_stage_a(arrays_simplify(build_raw_arrays(source, UTM_10N)[0])[0])[0]
    return arrays_stage_b(merged, arrays_find_twins(merged)[:2])[0]


def without_turns(graph: StageBGraph, emptied: int, limited: int | None = None) -> StageBGraph:
    """The stage B graph with super-junction `emptied` allowing no turn, and `limited` (if given)
    only the pairs over an edge between the two, the review's construction: dropping the first
    leaves the second no turn, for a second round."""
    u, v, node = graph.edge_u, graph.edge_v, graph.trans_node
    between = ((u == limited) & (v == emptied)) | ((u == emptied) & (v == limited))
    kept = (node != emptied) & (
        (node != limited) | between[graph.trans_in] | between[graph.trans_out]
    )
    return dataclasses.replace(
        graph,
        trans_node=graph.trans_node[kept],
        trans_in=graph.trans_in[kept],
        trans_out=graph.trans_out[kept],
    )


@pytest.mark.parametrize(
    ("case", "emptied", "limited", "rounds"),
    [
        ("roundabout", 4, None, 1),  # its four two-way arms lost inside the round
        ("too wide", 3, 2, 2),
        ("turn back", 5, 4, 2),
        ("woodburn far", 9, 10, 2),
    ],
)
def test_super_junctions_left_without_a_turn_round_by_round(
    tmp_path: Path, case: str, emptied: int, limited: int | None, rounds: int
) -> None:
    """Super-junctions allowing no turn dropped as cleanup drops them, a round each, two-way
    roads lost among their edges: the same report and graph on both engines."""
    source = FIXTURES / f"{case}.osm" if case == "roundabout" else STAGE_B_CASES[case](tmp_path)
    graph = without_turns(arrays_stage_b_of(source), emptied, limited)
    want = stage_b_graph(graph)
    keep, want_report = largest_component(want)
    want = cleanup._without_no_turn(relabel(want, keep), want_report)
    component, got_report = arrays_connectivity.largest_component(graph)
    kept = arrays_connectivity.keep_only(graph, component)
    got = cleanup_graph(arrays_connectivity.without_no_turn(kept, got_report))
    assert got_report == want_report and got_report.dropped_no_turn == rounds
    assert_same_graph(want, got)
    assert pickle.dumps(got, pickle.HIGHEST_PROTOCOL) == pickle.dumps(want, pickle.HIGHEST_PROTOCOL)


def test_the_cleanup_stage_reads_back(tmp_path: Path) -> None:
    """The cleaned graph written as `stages/cleanup/` reads back the same, both its graphs."""
    raw = build_raw_arrays(FIXTURES / "diamond.osm", UTM_10N)[0]
    graph, report = run_arrays_cleanup(raw, allow_fragmented=True)
    cleaned.save_stage(graph, report, tmp_path)
    back = cleaned.load_stage(tmp_path)
    for want, got in ((graph, back), (graph.stage_a, back.stage_a)):
        for field in dataclasses.fields(want):
            if field.name != "stage_a":
                a, b = getattr(want, field.name), getattr(got, field.name)
                same = (
                    np.array_equal(a, b) and a.dtype == b.dtype
                    if isinstance(a, np.ndarray)
                    else a == b
                )
                assert same, field.name
    written = (tmp_path / "stages" / "cleanup.json").read_text()
    assert written == json.dumps(dataclasses.asdict(report), indent=2) + "\n"  # as networkx's
