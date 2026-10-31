import json
import pickle
from pathlib import Path
from typing import Any

import networkx as nx
import pytest

from tests.pipeline.chain import poi_table, tier_nodes
from tests.pipeline.invariants import check_all
from tests.pipeline.osm import write_osm
from tests.pipeline.test_carriageways import mapped_twice
from tests.pipeline.test_save import META
from worst_route import config
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.carriageways import find_twins
from worst_route.pipeline.cleanup import (
    FragmentedGraphError,
    parameters,
    run_cleanup,
    save_stage,
)
from worst_route.pipeline.consolidate import stage_a, stage_b
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.save import graph_data
from worst_route.pipeline.simplify import simplify

FIXTURES = Path(__file__).parent.parent / "fixtures" / "osm"
UTM_10N = 32610


def raw(name: str) -> nx.MultiDiGraph:
    return build_raw_graph(FIXTURES / f"{name}.osm", UTM_10N)[0]


@pytest.mark.parametrize(
    ("name", "nodes", "edges", "kinds"),
    [
        ("refchange", 5, 11, {"plain": 5}),
        ("overpass", 7, 12, {"plain": 7}),
        ("roundabout", 5, 8, {"plain": 4, "roundabout": 1}),
        ("divided", 15, 25, {"plain": 14, "cluster": 1}),
        ("diamond", 7, 12, {"plain": 6, "interchange": 1}),
        ("cloverleaf", 9, 16, {"plain": 8, "interchange": 1}),
        ("grid", 36, 110, {"plain": 36}),
        ("culdesac", 7, 12, {"plain": 7}),
        ("exclusions", 9, 16, {"plain": 9}),
    ],
)
def test_cleanup_of_each_fixture(name: str, nodes: int, edges: int, kinds: dict[str, int]) -> None:
    before = raw(name)
    graph, report = run_cleanup(before)
    check_all(before, graph)  # nothing was dropped, so every OSM id is accounted for
    assert (graph.number_of_nodes(), graph.number_of_edges()) == (nodes, edges)
    assert list(graph.nodes) == list(range(nodes))
    found: dict[str, int] = {}
    for _, kind in graph.nodes(data="kind"):
        found[kind] = found.get(kind, 0) + 1
    assert found == kinds
    assert report.input == {"nodes": before.number_of_nodes(), "edges": before.number_of_edges()}
    assert report.connectivity.retained == 1.0
    assert (report.connectivity.nodes_after, report.connectivity.edges_after) == (nodes, edges)
    assert set(report.seconds) == {"simplify", "stage_a", "stage_b", "twins", "connectivity"}
    assert set(graph.graph) == {"crs", "pipeline_version", "superjunctions", "twins"}
    assert report.connectivity.dropped_no_turn == 0


def cut_interchange(tmp_path: Path) -> Path:
    """I 90 cut by a region's edge at an interchange whose interior lies beyond the extracts: the
    eastbound carriageway ends at its on-ramp's merge (3), the westbound starts at its on-ramp's
    merge (4), and both ramps come from the cross road's terminal (8), whose road is outside. A
    street joins the carriageways' west ends (1, 6). No path inside the interchange leads from
    where eastbound arrives to where westbound leaves, so it allows no turn through it, and the
    street makes it part of the largest strongly connected component all the same."""
    return write_osm(
        tmp_path / "cut.osm",
        {
            1: (-900, 15),
            2: (-400, 15),
            3: (0, 15),
            4: (0, -15),
            5: (-400, -15),
            6: (-900, -15),
            7: (-950, 0),
            8: (300, 0),
        },  # fmt: skip
        [
            (1, [1, 2, 3], {"highway": "motorway", "ref": "I 90", "oneway": "yes"}),
            (2, [4, 5, 6], {"highway": "motorway", "ref": "I 90", "oneway": "yes"}),
            (3, [6, 7, 1], {"highway": "residential", "name": "Stateline Rd"}),
            (4, [8, 3], {"highway": "motorway_link"}),
            (5, [8, 4], {"highway": "motorway_link"}),
        ],
    )


def cut_beyond(tmp_path: Path) -> Path:
    """The shape the Washington-Idaho line gave: I 90 runs through an interchange to stubs beyond
    a region's edge, eastbound leaving to a dead end (9) and westbound arriving from a start
    (10), its off-ramps (3 to 8, 4 to 8) ending at the cross road's terminal, which is outside.
    Stage B allows the two straight-through pairs, both through a stub; once connectivity drops
    the stubs, eastbound arriving from the west and westbound leaving to it are left with no pair
    between them."""
    return write_osm(
        tmp_path / "beyond.osm",
        {
            1: (-900, 15),
            2: (-400, 15),
            3: (0, 15),
            9: (600, 15),
            10: (600, -15),
            4: (0, -15),
            5: (-400, -15),
            6: (-900, -15),
            7: (-950, 0),
            8: (300, 80),
        },  # fmt: skip
        [
            (1, [1, 2, 3, 9], {"highway": "motorway", "ref": "I 90", "oneway": "yes"}),
            (2, [10, 4, 5, 6], {"highway": "motorway", "ref": "I 90", "oneway": "yes"}),
            (3, [6, 7, 1], {"highway": "residential", "name": "Stateline Rd"}),
            (4, [3, 8], {"highway": "motorway_link"}),
            (5, [4, 8], {"highway": "motorway_link"}),
        ],
    )


def street_of(graph: nx.MultiDiGraph) -> dict[tuple[int, ...], list[tuple[float, float]]]:
    """Stateline Rd's edges, by their OSM nodes: their geometry."""
    return {
        d["osm_nodes"]: list(d["geometry"].coords)
        for _, _, d in graph.edges(data=True)
        if "name" in d
    }


def staged(path: Path) -> nx.MultiDiGraph:
    """The graph stage B makes of the OSM file, before connectivity."""
    graph, _ = stage_a(simplify(build_raw_graph(path, UTM_10N)[0])[0])
    find_twins(graph)
    return stage_b(graph)[0]


@pytest.mark.parametrize("case", [cut_interchange, cut_beyond])
def test_a_super_junction_left_with_no_allowed_turn_is_dropped(tmp_path: Path, case: Any) -> None:
    path = case(tmp_path)
    before = build_raw_graph(path, UTM_10N)[0]
    graph, report = run_cleanup(before, allow_fragmented=True)  # the motorway is most of it
    scc = report.connectivity
    assert report.stage_b.superjunctions == {"interchange": 1, "roundabout": 0}
    assert scc.dropped_no_turn == 1
    assert sorted(m for _, members in graph.nodes(data="members") for m in members) == [1, 6]
    assert graph.graph["superjunctions"] == {} and graph.graph["twins"] == {}
    assert street_of(graph) == street_of(before)  # the street as it was built
    assert (scc.nodes_after, scc.edges_after) == (2, 2) == (len(graph), graph.number_of_edges())
    assert scc.dropped_nodes == scc.nodes_before - 2 and scc.retained == 2 / scc.nodes_before
    assert scc.dropped_edges == scc.edges_before - 2
    i_90 = [d for _, _, d in staged(path).edges(data=True) if "name" not in d]
    assert scc.dropped_length_m == pytest.approx(sum(d["length"] for d in i_90))


def test_stage_b_allows_the_pairs_through_the_stubs(tmp_path: Path) -> None:
    # What leaves the second case to cleanup: stage B allows both straight-through pairs, and
    # only connectivity, dropping the stubs (a dead end and a start), takes them away.
    (record,) = staged(cut_beyond(tmp_path)).graph["superjunctions"].values()
    assert len(record["transitions"]) == 2
    _, report = run_cleanup(
        build_raw_graph(cut_beyond(tmp_path), UTM_10N)[0], allow_fragmented=True
    )
    assert report.connectivity.dropped_components == 2


def test_a_road_mapped_twice_is_cleaned_and_saved(tmp_path: Path) -> None:
    before = build_raw_graph(mapped_twice(tmp_path), UTM_10N)[0]
    graph, report = run_cleanup(before)
    check_all(before, graph)
    assert (report.twins.pairs, report.twins.same_road_pairs) == (0, 1)
    assert report.connectivity.retained == 1.0 and graph.graph["twins"] == {}
    # Save accepts it: Mill Rd's inner node lies inside the two edges of one road unit.
    run_enrich(graph, poi_table([]))
    data = graph_data(graph, tier_nodes(graph), META)
    data.validate()
    assert data.m == graph.number_of_edges() == 6 and len(set(data.edge_unit.tolist())) == 3


@pytest.mark.parametrize("name", ["divided", "cloverleaf"])
def test_cleanup_is_deterministic(name: str) -> None:
    first, _ = run_cleanup(raw(name))
    second, _ = run_cleanup(raw(name))
    assert pickle.dumps(first) == pickle.dumps(second)


def test_a_fragmented_graph_fails() -> None:
    with pytest.raises(FragmentedGraphError) as failure:
        run_cleanup(raw("oneway"))
    message = str(failure.value)
    assert message.startswith(
        "the largest strongly connected component keeps 3 of 13 nodes (23.08%), "
        "below SCC_MIN_RETAINED (98%); largest dropped: 2 nodes, "
    )
    assert message.endswith("; pass --allow-fragmented to keep it anyway")
    assert failure.value.report.dropped_components == 7


def test_a_graph_dropping_a_large_component_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SCC_MIN_RETAINED", 0.2)  # the share alone would pass
    monkeypatch.setattr(config, "SCC_MAX_DROPPED_NODES", 2)
    graph, report = run_cleanup(raw("oneway"))  # its largest dropped component has 2 nodes
    assert report.connectivity.retained < 0.25 and graph.number_of_nodes() == 3
    monkeypatch.setattr(config, "SCC_MAX_DROPPED_NODES", 1)
    with pytest.raises(FragmentedGraphError) as failure:
        run_cleanup(raw("oneway"))
    assert str(failure.value).startswith(
        "the largest strongly connected component keeps 3 of 13 nodes (23.08%), and a "
        "component it drops has more than SCC_MAX_DROPPED_NODES (1); largest dropped: 2 nodes, "
    )
    monkeypatch.setattr(config, "SCC_MIN_RETAINED", 0.98)
    with pytest.raises(FragmentedGraphError) as failure:
        run_cleanup(raw("oneway"))
    assert "(23.08%), below SCC_MIN_RETAINED (98%), and a component it drops has more " in str(
        failure.value
    )


def test_a_fragmented_graph_can_be_kept() -> None:
    before = raw("oneway")
    graph, report = run_cleanup(before, allow_fragmented=True)
    check_all(before, graph, complete=False)
    assert list(graph.nodes) == [0, 1, 2]
    assert report.connectivity.nodes_after == 3
    (ring,) = graph.graph["superjunctions"]
    assert graph.nodes[ring]["kind"] == "roundabout"


def test_save_stage(tmp_path: Path) -> None:
    graph, report = run_cleanup(raw("diamond"))
    (tmp_path / "b1" / "stages").mkdir(parents=True)
    save_stage(graph, report, tmp_path / "b1")
    with (tmp_path / "b1" / "stages" / "cleanup.pkl").open("rb") as f:
        loaded = pickle.load(f)
    assert loaded.graph == graph.graph
    assert list(loaded.edges(keys=True, data=True)) == list(graph.edges(keys=True, data=True))
    summary = json.loads((tmp_path / "b1" / "stages" / "cleanup.json").read_text())
    assert set(summary) == {
        "input",
        "simplify",
        "stage_a",
        "stage_b",
        "twins",
        "connectivity",
        "seconds",
    }
    assert summary["stage_b"]["superjunctions"] == {"interchange": 1, "roundabout": 0}
    assert summary["twins"]["pairs"] == 3  # found on the stage A graph, mainline stretches too
    assert summary["stage_b"]["twin_pairs"] == 2  # those two are inside one super-node


def test_parameters_are_json() -> None:
    values = parameters()
    assert json.loads(json.dumps(values)) == values
    assert values["STAGE_A_ARTERIAL_CLASSES"] == ["primary", "secondary", "trunk"]
    assert values["SCC_MIN_RETAINED"] == 0.98
    assert values["SCC_MAX_DROPPED_NODES"] == 200
