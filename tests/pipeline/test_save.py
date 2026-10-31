import json
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pytest
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.pipeline.chain import FIXTURES, brand_pois, cleaned, east_of, enriched, tier_nodes
from tests.pipeline.invariants import check_build, check_saved
from tests.pipeline.osm import write_osm
from worst_route.config import GEOMETRY_SIMPLIFY_M, PIPELINE_VERSION, TIERS
from worst_route.graph.data import FORMAT_VERSION, NodeKind
from worst_route.graph.geometry import edge_xy, read_geometry
from worst_route.graph.store import graph_version, read_build
from worst_route.graph.testing import GRID_ORIGIN, make_graph
from worst_route.pipeline import save
from worst_route.pipeline.enrich import run_enrich
from worst_route.pipeline.save import (
    EmptyTransitionsError,
    TooManySpeedsError,
    graph_data,
    graph_geometry,
    remove_build,
    save_build,
)

META: dict[str, Any] = {
    "region": "test",
    "build_id": "b1",
    "crs_epsg": 32610,
    "created": "2026-10-30T00:00:00+00:00",
    "pipeline_version": PIPELINE_VERSION,
    "format": FORMAT_VERSION,
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
    report = save_build(data, path, graph_geometry(graph, data))
    loaded = read_build(path)
    loaded.validate()  # deep
    check_build(loaded)
    check_saved(graph, loaded, read_geometry(path, loaded.m))
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
    keys = sorted(graph.edges(keys=True))
    speeds = [np.float32(graph.edges[k]["speed_kmh"]) for k in keys]
    assert data.speed_kmh.tolist() == sorted(set(speeds))
    assert data.edge_speed_kmh.tolist() == speeds
    two_way = data.edge_unit != np.arange(data.m)
    assert int(two_way.sum()) == 50  # the later edge of each of the 50 two-way pairs


def test_super_junctions_and_their_transitions(tmp_path: Path) -> None:
    graph = enriched(FIXTURES / "diamond.osm", tmp_path)
    data = graph_data(graph, tier_nodes(graph), META)
    (node,) = graph.graph["superjunctions"]
    assert data.node_kind[node] == 2  # interchange
    assert set(data.trans_node.tolist()) == {node} and len(data.trans_node) == 16  # 4 in x 4 out
    assert data.tier_node_mask[2, node]  # T2 holds the secondary road through it


def test_roundabout_rings_are_saved_at_their_super_junctions(tmp_path: Path) -> None:
    # A ring: one roundabout super-node with one ring. A diamond: an interchange with none. A
    # slip lane past a ring: one interchange that still holds the ring. Written and read back.
    nodes = {10: (0, 15), 12: (-15, 0), 14: (0, -15), 16: (15, 0)}
    nodes |= {20: (0, 150), 21: (0, -150), 22: (150, 0), 23: (-150, 0), 24: (0, 50), 25: (50, 0)}
    bypassed = write_osm(
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
    sources = [FIXTURES / "roundabout.osm", FIXTURES / "diamond.osm", bypassed]
    expected = [(NodeKind.ROUNDABOUT, 1), (NodeKind.INTERCHANGE, 0), (NodeKind.INTERCHANGE, 1)]
    for k, (source, (kind, rings)) in enumerate(zip(sources, expected, strict=True)):
        graph = enriched(source, tmp_path)
        path = build_dir(tmp_path / str(k))
        data = graph_data(graph, tier_nodes(graph), META)
        save_build(data, path, graph_geometry(graph, data))
        loaded = read_build(path)
        (node,) = graph.graph["superjunctions"]
        assert (loaded.node_kind[node], loaded.node_roundabouts[node]) == (kind, rings)
        assert int(loaded.node_roundabouts.sum()) == rings


def test_the_brand_index_is_saved_and_read_back(tmp_path: Path) -> None:
    graph = cleaned(FIXTURES / "grid.osm")
    rows = [(v, 5.0, "Acme", "Q1", None) for v in (0, 7, 14)]
    rows += [(v, dx, "Zed", None, None) for v, dx in ((3, 5.0), (3, -5.0), (9, 5.0), (20, 5.0))]
    run_enrich(graph, brand_pois([(*east_of(graph, v, dx), *tags) for v, dx, *tags in rows]))
    data = graph_data(graph, tier_nodes(graph), META)
    path = build_dir(tmp_path)
    save_build(data, path, graph_geometry(graph, data))
    loaded = read_build(path)
    loaded.validate()
    check_saved(graph, loaded, read_geometry(path, loaded.m))
    assert (loaded.strings.brands, loaded.strings.brand_wikidata) == (["Acme", "Zed"], ["Q1", None])
    assert loaded.nodes_of_brand(0).tolist() == [0, 7, 14]
    assert loaded.nodes_of_brand(1).tolist() == [3, 9, 20]
    assert loaded.brand_locations.tolist() == [1, 1, 1, 2, 1, 1]
    assert loaded.meta["graph_version"] == graph_version(data)
    plain = enriched(FIXTURES / "grid.osm", tmp_path)  # its two brand POIs, one location each
    assert graph_version(graph_data(plain, tier_nodes(plain), META)) != graph_version(data)


def test_a_super_junction_with_no_allowed_turn_is_refused(tmp_path: Path) -> None:
    graph = enriched(FIXTURES / "roundabout.osm", tmp_path)
    (node,) = graph.graph["superjunctions"]
    graph.graph["superjunctions"][node]["transitions"] = ()
    with pytest.raises(EmptyTransitionsError) as refusal:
        graph_data(graph, tier_nodes(graph), META)
    assert str(refusal.value).startswith(f"super-junction {node} (roundabout, at 45.")
    assert "allows no turn through it" in str(refusal.value)


def test_more_speeds_than_a_build_can_index_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = enriched(FIXTURES / "grid.osm", tmp_path)
    speeds = len({np.float32(d["speed_kmh"]) for *_, d in graph.edges(data=True)})
    monkeypatch.setattr(save, "MAX_SPEEDS", speeds - 1)
    with pytest.raises(TooManySpeedsError, match=f"{speeds} distinct speeds"):
        graph_data(graph, tier_nodes(graph), META)


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
    geometry = graph_geometry(graph, data)
    save_build(data, path, geometry)
    with pytest.raises(FileExistsError):
        save_build(data, path, geometry)
    remove_build(path)
    assert sorted(p.name for p in path.iterdir()) == ["stages"]
    save_build(data, path, geometry)
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


@settings(max_examples=200, deadline=None)
@given(st.lists(st.tuples(st.floats(0.0, 1000.0), st.floats(-30.0, 30.0)), min_size=0, max_size=40))
def test_the_geometry_is_the_shape_within_the_tolerance_and_the_rounding(
    inner: list[tuple[float, float]],
) -> None:
    # One edge, a wiggly road from a node to another 1 km east: what the build draws lies within
    # GEOMETRY_SIMPLIFY_M of it, plus the metre's rounding, and keeps its ends exactly.
    x0, y0 = GRID_ORIGIN
    line = [(x0, y0), *[(x0 + x, y0 + y) for x, y in sorted(inner)], (x0 + 1000.0, y0)]
    graph = nx.MultiDiGraph()
    graph.add_edge(0, 1, geometry=shapely.LineString(line))
    data = make_graph([line[0], line[-1]], [(0, 1, 1000.0)])
    geometry = save.graph_geometry(graph, data)
    xy, counts = edge_xy(data, geometry, np.array([0]))
    assert xy[0].tolist() == list(line[0]) and xy[-1].tolist() == list(line[-1])
    assert counts[0] <= len(line)
    drawn, shape = shapely.LineString(xy), shapely.LineString(line)
    assert shapely.hausdorff_distance(drawn, shape, densify=0.05) <= GEOMETRY_SIMPLIFY_M + 0.71
