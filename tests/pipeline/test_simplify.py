import itertools
from pathlib import Path

import networkx as nx
import pytest

from tests.pipeline.invariants import check_edges, check_partition
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.simplify import SimplifyStats, simplify

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


def raw(name: str) -> nx.MultiDiGraph:
    return build_raw_graph(FIXTURES / f"{name}.osm", UTM_10N)[0]


def by_nodes(graph: nx.MultiDiGraph) -> dict[tuple[int, ...], dict]:
    return {data["osm_nodes"]: data for _, _, data in graph.edges(data=True)}


def test_refchange_counts() -> None:
    graph, stats = simplify(raw("refchange"))
    assert stats == SimplifyStats(self_loops_dropped=0, nodes_removed=3, nodes=5, edges=11)
    assert sorted(graph.nodes) == [1, 3, 7, 20, 24]  # 5, 41 and 22 merged


def test_identical_ways_merge() -> None:
    before = raw("refchange")
    graph, _ = simplify(before)
    edges = by_nodes(graph)
    east = edges[3, 4, 5, 6, 7]
    assert east["osmids"] == (101, 102)
    assert (east["ref"], east["name"], east["oneway"], east["reversed"]) == (
        "OR 210",
        "SE Foster Rd",
        False,
        False,
    )
    halves = [before.edges[3, 5, 0], before.edges[5, 7, 0]]
    assert east["length"] == pytest.approx(sum(h["length"] for h in halves))
    assert east["length"] == pytest.approx(east["geometry"].length)
    coords = list(halves[0]["geometry"].coords) + list(halves[1]["geometry"].coords)[1:]
    assert list(east["geometry"].coords) == coords
    west = edges[7, 6, 5, 4, 3]  # the backward edge, merged the same way
    assert (west["osmids"], west["reversed"]) == ((102, 101), True)
    assert list(west["geometry"].coords) == coords[::-1]
    assert "osmid" not in east
    assert set(east) == {
        "highway",
        "name",
        "ref",
        "oneway",
        "reversed",
        "osmids",
        "length",
        "geometry",
        "osm_nodes",
    }


def test_one_way_chain_merges() -> None:
    graph, _ = simplify(raw("refchange"))
    alder = by_nodes(graph)[7, 40, 41, 42, 1]
    assert (alder["osmids"], alder["oneway"]) == ((103, 104), True)
    assert not graph.has_edge(1, 7)


def test_a_ref_change_keeps_the_node() -> None:
    graph, _ = simplify(raw("refchange"))
    assert by_nodes(graph)[1, 2, 3]["ref"] == "OR 10"
    assert graph.has_node(3)


def test_a_chain_closing_on_itself_stays_split() -> None:
    graph, _ = simplify(raw("refchange"))
    edges = by_nodes(graph)
    # 22 merged into 20-24; 24 would make one edge 20 -> ... -> 20, so it stays.
    for ring in [(20, 21, 22, 23, 24), (24, 25, 20)]:
        assert ring in edges and ring[::-1] in edges
    assert graph.has_node(24) and not graph.has_node(22)


def test_ways_drawn_in_opposite_directions_do_not_merge(tmp_path: Path) -> None:
    text = (FIXTURES / "refchange.osm").read_text()
    drawn_back = text.replace(
        '<nd ref="5"/>\n    <nd ref="6"/>\n    <nd ref="7"/>',
        '<nd ref="7"/>\n    <nd ref="6"/>\n    <nd ref="5"/>',
    )
    assert drawn_back != text
    source = tmp_path / "refchange.osm"
    source.write_text(drawn_back)
    graph, stats = simplify(build_raw_graph(source, UTM_10N)[0])
    assert graph.has_node(5)  # its two edges' `reversed` flags differ
    assert stats.nodes_removed == 2


def test_a_tag_change_keeps_the_node() -> None:
    graph, stats = simplify(raw("overpass"))
    assert stats.nodes_removed == 0
    assert graph.has_node(101)  # maxspeed changes
    assert graph.has_node(201)  # the bridge starts


def test_self_loops_are_dropped(tmp_path: Path) -> None:
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
    before = build_raw_graph(lollipop, UTM_10N)[0]
    graph, stats = simplify(before)
    assert stats == SimplifyStats(self_loops_dropped=2, nodes_removed=0, nodes=2, edges=2)
    assert nx.number_of_selfloops(graph) == 0
    assert nx.number_of_selfloops(before) == 2  # the input is left as it was
    assert all("osmid" in data for _, _, data in before.edges(data=True))


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_invariants_after_simplify(name: str) -> None:
    before = raw(name)
    graph, stats = simplify(before)
    check_partition(before, graph, complete=True)
    check_edges(graph)
    assert stats.nodes == graph.number_of_nodes() == before.number_of_nodes() - stats.nodes_removed
    for _, _, data in graph.edges(data=True):
        assert "osmid" not in data
        assert all(a != b for a, b in itertools.pairwise(data["osmids"]))
