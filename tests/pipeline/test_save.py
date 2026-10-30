import json
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pytest

from tests.pipeline.chain import FIXTURES, enriched, tier_nodes
from tests.pipeline.invariants import check_build, check_saved
from worst_route.config import TIERS
from worst_route.graph.store import graph_version, read_build
from worst_route.pipeline.save import (
    EmptyTransitionsError,
    graph_data,
    remove_build,
    save_build,
)

META: dict[str, Any] = {
    "region": "test",
    "build_id": "b1",
    "crs_epsg": 32610,
    "created": "2026-10-30T00:00:00+00:00",
    "pipeline_version": "1",
    "params": {"bbox": None},
    "sources": [],
}
FIXTURE_NAMES = [
    "refchange",
    "overpass",
    "roundabout",
    "divided",
    "diamond",
    "cloverleaf",
    "grid",
    "culdesac",
    "exclusions",
    "oneway",  # fragmented: cleanup keeps its largest component
]


def build_dir(tmp_path: Path) -> Path:
    path = tmp_path / "graphs" / "test" / "b1"
    (path / "stages").mkdir(parents=True)
    return path


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_save_each_fixture(name: str, tmp_path: Path) -> None:
    graph = enriched(FIXTURES / f"{name}.osm", tmp_path, allow_fragmented=name == "oneway")
    data = graph_data(graph, tier_nodes(graph), META)
    path = build_dir(tmp_path)
    report = save_build(data, path)
    loaded = read_build(path)
    loaded.validate()  # deep
    check_build(loaded)
    check_saved(graph, loaded)
    assert loaded.meta["graph_version"] == report.graph_version == graph_version(data)
    assert report.counts == loaded.meta["counts"]
    assert report.counts["nodes"] == graph.number_of_nodes()
    assert report.counts["twin_pairs"] * 2 == sum(len(t) for t in graph.graph["twins"].values())
    saved = json.loads((path / "stages" / "save.json").read_text())
    assert saved["graph_version"] == report.graph_version
    for t, classes in enumerate(TIERS):  # a tier's edges are its classes, nothing else
        in_tier = np.isin(loaded.edge_highway, sorted(classes))
        assert not (loaded.tier_edge_mask[t] & ~in_tier).any()
    assert loaded.tier_node_mask[4].all() and loaded.tier_edge_mask[4].all()
    for v in range(loaded.n):  # each node's out-edges and in-edges in ascending id
        out = loaded.out_edges[loaded.out_indptr[v] : loaded.out_indptr[v + 1]]
        into = loaded.in_edges[loaded.in_indptr[v] : loaded.in_indptr[v + 1]]
        assert (np.diff(out) > 0).all() and (np.diff(into) > 0).all()


def test_the_build_holds_the_enriched_values(tmp_path: Path) -> None:
    graph = enriched(FIXTURES / "grid.osm", tmp_path)
    data = graph_data(graph, tier_nodes(graph), META)
    names = {data.strings.names[i] for i in data.edge_name.tolist()}
    assert "Baker Rd" in names and data.strings.names == sorted(data.strings.names)
    assert data.strings.refs == ["OR 10", "US 26"]
    assert "OR 10" in data.strings.identities
    assert int(data.edge_toll_booths.sum()) == 2 and int(data.edge_level_crossings.sum()) == 2
    assert sorted(data.node_delay_s[data.node_delay_s > 0].tolist()) == [10.0, 30.0]
    assert (data.node_state == -1).all()
    two_way = data.edge_unit != np.arange(data.m)
    assert int(two_way.sum()) == 50  # the later edge of each of the 50 two-way pairs


def test_super_junctions_and_their_transitions(tmp_path: Path) -> None:
    graph = enriched(FIXTURES / "diamond.osm", tmp_path)
    data = graph_data(graph, tier_nodes(graph), META)
    (node,) = graph.graph["superjunctions"]
    assert data.node_kind[node] == 2  # interchange
    assert set(data.trans_node.tolist()) == {node} and len(data.trans_node) == 16  # 4 in x 4 out
    assert data.tier_node_mask[2, node]  # T2 holds the secondary road through it


def test_a_super_junction_with_no_allowed_turn_is_refused(tmp_path: Path) -> None:
    graph = enriched(FIXTURES / "roundabout.osm", tmp_path)
    (node,) = graph.graph["superjunctions"]
    graph.graph["superjunctions"][node]["transitions"] = ()
    with pytest.raises(EmptyTransitionsError) as refusal:
        graph_data(graph, tier_nodes(graph), META)
    assert str(refusal.value).startswith(f"super-junction {node} (roundabout, at 45.")
    assert "allows no turn through it" in str(refusal.value)


def test_the_same_graph_gives_the_same_version(tmp_path: Path) -> None:
    first = enriched(FIXTURES / "cloverleaf.osm", tmp_path)
    second = enriched(FIXTURES / "cloverleaf.osm", tmp_path)
    one = graph_data(first, tier_nodes(first), META)
    other = graph_data(second, tier_nodes(second), {**META, "created": "later"})
    assert graph_version(one) == graph_version(other)
    changed = graph_data(first, tier_nodes(first), {**META, "params": {"bbox": [0, 0, 1, 1]}})
    assert graph_version(changed) != graph_version(one)


def test_a_written_build_is_not_overwritten_and_can_be_removed(tmp_path: Path) -> None:
    graph = enriched(FIXTURES / "divided.osm", tmp_path)
    data = graph_data(graph, tier_nodes(graph), META)
    path = build_dir(tmp_path)
    save_build(data, path)
    with pytest.raises(FileExistsError):
        save_build(data, path)
    remove_build(path)
    assert sorted(p.name for p in path.iterdir()) == ["stages"]
    save_build(data, path)
    assert (path / "meta.json").exists()


def test_an_isolated_tier_edge_is_not_in_the_tier(tmp_path: Path) -> None:
    # The overpass's two tertiary roads: T3 keeps SE 21st Ave, the one with more nodes; SE
    # Holgate Blvd's edges stay out of T3's edge mask though their class is in it.
    graph = enriched(FIXTURES / "overpass.osm", tmp_path)
    data = graph_data(graph, tier_nodes(graph), META)
    tertiary = data.edge_tier == 3
    assert int(tertiary.sum()) == 10 and int(data.tier_edge_mask[3].sum()) == 6
    dropped = tertiary & ~data.tier_edge_mask[3]
    assert not data.tier_node_mask[3][data.edge_u[dropped]].any()


def test_graph_data_does_not_change_the_graph(tmp_path: Path) -> None:
    graph = enriched(FIXTURES / "divided.osm", tmp_path)
    before = nx.to_dict_of_dicts(graph)
    graph_data(graph, tier_nodes(graph), META)
    assert nx.to_dict_of_dicts(graph) == before
