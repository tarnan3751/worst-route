import json
import pickle
from pathlib import Path

import networkx as nx
import pytest

from tests.pipeline.invariants import check_all
from worst_route import config
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.cleanup import (
    FragmentedGraphError,
    parameters,
    run_cleanup,
    save_stage,
)

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
    assert report.stage_b.dropped_empty == 0


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
