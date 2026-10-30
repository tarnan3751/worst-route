import math
from pathlib import Path

import networkx as nx
import pyproj
import pytest

from tests.pipeline.invariants import check_edges, check_partition
from worst_route.config import (
    STAGE_A_TOLERANCE_ARTERIAL_M,
    STAGE_A_TOLERANCE_M,
    STAGE_A_TOLERANCE_ROUNDABOUT_M,
)
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.consolidate import StageAStats, stage_a, stage_a_tolerances
from worst_route.pipeline.simplify import simplify

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


def raw(source: str | Path) -> nx.MultiDiGraph:
    path = source if isinstance(source, Path) else FIXTURES / f"{source}.osm"
    return build_raw_graph(path, UTM_10N)[0]


def through_a(source: str | Path) -> tuple[nx.MultiDiGraph, nx.MultiDiGraph, StageAStats]:
    before = raw(source)
    merged, stats = stage_a(simplify(before)[0])
    return before, merged, stats


def node_of(graph: nx.MultiDiGraph, osm_id: int) -> int:
    return next(n for n, members in graph.nodes(data="members") if osm_id in members)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_invariants_after_stage_a(name: str) -> None:
    before, merged, _ = through_a(name)
    check_partition(before, merged, complete=True)
    check_edges(merged)


# Stage A ----------------------------------------------------------------------------------


def test_stage_a_tolerances() -> None:
    tolerances = stage_a_tolerances(simplify(raw("divided"))[0])
    assert tolerances[31] == tolerances[50] == STAGE_A_TOLERANCE_ARTERIAL_M  # on the primary
    assert tolerances[60] == tolerances[90] == STAGE_A_TOLERANCE_M  # residential only
    tolerances = stage_a_tolerances(simplify(raw("roundabout"))[0])
    assert {tolerances[n] for n in (10, 12, 14, 16)} == {STAGE_A_TOLERANCE_ROUNDABOUT_M}
    assert tolerances[20] == STAGE_A_TOLERANCE_M


def test_a_roundabout_beats_an_arterial(tmp_path: Path) -> None:
    text = (FIXTURES / "roundabout.osm").read_text()
    primary = text.replace('<tag k="highway" v="tertiary"/>', '<tag k="highway" v="primary"/>')
    assert primary != text
    (tmp_path / "ring.osm").write_text(primary)
    tolerances = stage_a_tolerances(simplify(raw(tmp_path / "ring.osm"))[0])
    assert {tolerances[n] for n in (10, 12, 14, 16)} == {STAGE_A_TOLERANCE_ROUNDABOUT_M}


def test_divided_crossing_merges_into_one_cluster() -> None:
    before, merged, stats = through_a("divided")
    assert stats == StageAStats(
        clusters=1,
        nodes_merged=4,
        edges_absorbed=4,
        cluster_sizes={4: 1},
        nodes=15,
        edges=25,
    )
    crossing = node_of(merged, 50)
    data = merged.nodes[crossing]
    assert set(data) == {"x", "y", "lon", "lat", "kind", "members"}
    assert data["kind"] == "cluster"
    assert data["members"] == (50, 51, 52, 53, 54)  # 54 lay inside an absorbed edge
    to_lonlat = pyproj.Transformer.from_crs(UTM_10N, 4326, always_xy=True)
    assert (data["lon"], data["lat"]) == pytest.approx(to_lonlat.transform(data["x"], data["y"]))
    for n in (50, 51, 52, 53):  # the cluster sits in the middle of the square
        assert math.dist((data["x"], data["y"]), (before.nodes[n]["x"], before.nodes[n]["y"])) < 20
    plain = merged.nodes[node_of(merged, 31)]
    assert plain == {**before.nodes[31], "kind": "plain", "members": (31,)}
    assert merged.graph == before.graph


def test_edges_reach_the_cluster() -> None:
    before, merged, _ = through_a("divided")
    crossing = node_of(merged, 50)
    position = (merged.nodes[crossing]["x"], merged.nodes[crossing]["y"])
    for _, v, data in [
        *merged.in_edges(crossing, data=True),
        *merged.out_edges(crossing, data=True),
    ]:
        assert "u_original" not in data and "v_original" not in data
        coords = list(data["geometry"].coords)
        assert coords[-1 if v == crossing else 0] == pytest.approx(position)
        assert data["length"] == pytest.approx(data["geometry"].length)
    eastbound = next(
        d for _, _, d in merged.in_edges(crossing, data=True) if d["osm_nodes"][0] == 31
    )
    assert eastbound["osm_nodes"] == (31, 73, 50)  # unchanged; the geometry gains one point
    assert len(eastbound["geometry"].coords) == 4


def test_an_overpass_does_not_merge() -> None:
    before, merged, stats = through_a("overpass")
    assert stats.clusters == 0
    assert node_of(merged, 101) != node_of(merged, 201)  # 14 m apart, no edge between them
    assert sorted(m for _, m in merged.nodes(data="members")) == [(n,) for n in sorted(before)]


def test_stage_a_without_edges() -> None:
    empty = nx.MultiDiGraph(crs="EPSG:32610")
    merged, stats = stage_a(empty)
    assert merged.number_of_nodes() == 0 and stats == StageAStats()
