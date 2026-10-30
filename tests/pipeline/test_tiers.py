import json
from pathlib import Path

import networkx as nx
import pytest

from tests.pipeline.chain import FIXTURES, cleaned
from worst_route.pipeline.tiers import (
    FragmentedTierError,
    parameters,
    run_tiers,
    save_stage,
    tier_classes,
)

# (kept nodes, tier nodes, kept edges, tier edges) for T0..T4. Small fixtures cut through the
# middle of a network, so their upper tiers are often fragments: with its ramps inside the
# interchange, a motorway is one-way stubs around the super-node at T0 (diamond, cloverleaf);
# the overpass's two tertiary roads meet only through a residential connector; the grid's
# one-way tertiary street joins T2's roads in one direction only.
EXPECTED = {
    "refchange": [(0, 0, 0, 0), (0, 0, 0, 0), (3, 3, 4, 4), (3, 3, 4, 4), (5, 5, 11, 11)],
    "overpass": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (4, 7, 6, 10), (7, 7, 12, 12)],
    "roundabout": [(0, 0, 0, 0)] * 4 + [(5, 5, 8, 8)],
    "divided": [(0, 0, 0, 0)] + [(8, 8, 11, 11)] * 3 + [(15, 15, 25, 25)],
    "diamond": [(1, 5, 0, 4), (1, 5, 0, 4), (3, 7, 4, 8), (3, 7, 4, 8), (7, 7, 12, 12)],
    "cloverleaf": [(1, 9, 0, 8)] * 4 + [(9, 9, 16, 16)],
    "grid": [(0, 0, 0, 0), (6, 6, 10, 10), (11, 11, 20, 20), (11, 16, 20, 25), (36, 36, 110, 110)],
    "culdesac": [(0, 0, 0, 0)] * 4 + [(7, 7, 12, 12)],
    "exclusions": [(0, 0, 0, 0)] * 4 + [(9, 9, 16, 16)],
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_tiers_of_each_fixture(name: str) -> None:
    graph = cleaned(FIXTURES / f"{name}.osm")
    report = run_tiers(graph, allow_fragmented=True)
    found = [(t.kept_nodes, t.nodes, t.kept_edges, t.edges) for t in report.tiers]
    assert found == EXPECTED[name]
    assert [t.name for t in report.tiers] == ["T0", "T1", "T2", "T3", "T4"]
    for t, tier in enumerate(report.tiers):
        assert tier.classes == tier_classes(t)
        assert tier.node_ids == sorted(set(tier.node_ids)) and len(tier.node_ids) == tier.kept_nodes
        roads = nx.DiGraph((u, v) for u, v, c in graph.edges(data="highway") if c in tier.classes)
        kept = roads.subgraph(tier.node_ids)
        if tier.kept_nodes:
            assert nx.is_strongly_connected(kept)
        assert tier.retained == (tier.kept_nodes / tier.nodes if tier.nodes else 1.0)
    assert report.tiers[-1].node_ids == list(range(graph.number_of_nodes()))  # T4: everything


def test_tiers_nest() -> None:
    graph = cleaned(FIXTURES / "grid.osm")
    report = run_tiers(graph, allow_fragmented=True)
    for lower, upper in zip(report.tiers, report.tiers[1:], strict=False):
        assert set(lower.classes) < set(upper.classes)


def test_a_tie_keeps_the_component_with_the_smallest_node() -> None:
    graph = nx.MultiDiGraph()
    for u, v in [(2, 3), (3, 2), (0, 1), (1, 0)]:
        graph.add_edge(u, v, highway="primary")
    graph.add_edge(1, 2, highway="residential")
    graph.add_edge(2, 1, highway="residential")
    report = run_tiers(graph, allow_fragmented=True)
    assert report.tiers[1].node_ids == [0, 1]
    assert (report.tiers[1].components, report.tiers[1].retained) == (2, 0.5)
    assert report.tiers[4].node_ids == [0, 1, 2, 3]


def test_a_fragmented_tier_fails() -> None:
    graph = cleaned(FIXTURES / "diamond.osm")
    with pytest.raises(FragmentedTierError) as failure:
        run_tiers(graph)
    assert str(failure.value) == (
        "T0 keeps 1 of 5 nodes (20.00%); T1 keeps 1 of 5 nodes (20.00%); "
        "T2 keeps 3 of 7 nodes (42.86%); T3 keeps 3 of 7 nodes (42.86%), "
        "below TIER_MIN_RETAINED (95%); pass --allow-fragmented to keep it anyway"
    )
    assert failure.value.report.tiers[4].retained == 1.0
    run_tiers(cleaned(FIXTURES / "divided.osm"))  # every tier whole: no error


def test_save_stage(tmp_path: Path) -> None:
    report = run_tiers(cleaned(FIXTURES / "divided.osm"))
    (tmp_path / "stages").mkdir()
    save_stage(report, tmp_path)
    stage = json.loads((tmp_path / "stages" / "tiers.json").read_text())
    assert [t["name"] for t in stage["tiers"]] == ["T0", "T1", "T2", "T3", "T4"]
    assert stage["tiers"][4]["node_ids"] == list(range(15))
    assert stage["tiers"][1]["classes"] == [
        "motorway",
        "motorway_link",
        "primary",
        "primary_link",
        "trunk",
        "trunk_link",
    ]


def test_parameters_are_json() -> None:
    values = parameters()
    assert json.loads(json.dumps(values)) == values
    assert values["TIER_MIN_RETAINED"] == 0.95
    assert len(values["TIERS"]) == 5 and len(values["TIERS"][4]) == 14
