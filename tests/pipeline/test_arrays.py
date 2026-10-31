"""The arrays engine against the networkx engine, on every fixture: the same build, byte for byte.

Each stage the arrays engine runs is compared with the networkx engine's: the raw graph, the
simplified graph (in networkx's iteration order, keys aside), then the whole of cleanup, and the
saved build (every array, the strings, the graph_version). Cases beyond the fixtures exercise
what simplification can meet: rings and loops that close on themselves, parallel chains, a way
drawn against its neighbours, a chain whose nodes are visited in no particular order.
"""

import dataclasses
import math
import pickle
from pathlib import Path

import networkx as nx
import numpy as np
import osmium
import pyproj
import pytest

from tests.pipeline.chain import FIXTURES, UTM_10N, pois_of
from tests.pipeline.invariants import check_all
from tests.pipeline.osm import write_osm
from tests.pipeline.parity import assert_same_build, assert_same_graph, raw_graph, read_back
from tests.pipeline.test_consolidate import _near_pair
from worst_route.graph.data import GraphData
from worst_route.pipeline.arrays.build import build_raw_arrays, load_stage, save_stage
from worst_route.pipeline.arrays.simplify import SimplifiedGraph
from worst_route.pipeline.arrays.simplify import simplify as arrays_simplify
from worst_route.pipeline.arrays.stage_a import StageAGraph
from worst_route.pipeline.arrays.stage_a import stage_a as arrays_stage_a
from worst_route.pipeline.arrays.to_nx import simplified_graph, stage_a_graph
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.cleanup import run_cleanup
from worst_route.pipeline.consolidate import stage_a, stage_a_tolerances
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.save import graph_data
from worst_route.pipeline.simplify import simplify
from worst_route.pipeline.tiers import run_tiers

ALL_FIXTURES = sorted(path.stem for path in FIXTURES.glob("*.osm"))
META = {"region": "test", "build_id": "test", "crs_epsg": UTM_10N, "created": "", "params": {}}
META |= {"pipeline_version": "", "sources": []}


def assert_same_cleanup(source: Path) -> nx.MultiDiGraph:
    """Both engines' simplified, stage A and cleaned graphs and reports are the same; the
    simplified graph."""
    raw = read_back(build_raw_graph(source, UTM_10N)[0])
    arrays, _ = build_raw_arrays(source, UTM_10N)
    simplified, _ = simplify(raw)
    arrays_simplified = arrays_simplify(arrays)[0]
    assert_same_simplified(simplified, arrays_simplified)
    want_a, want_a_stats = stage_a(simplified)
    got_a, got_a_stats = arrays_stage_a(arrays_simplified)
    assert got_a_stats == want_a_stats
    assert_same_stage_a(want_a, got_a)
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
