import json
from pathlib import Path
from typing import Any

import pytest

from tests.pipeline.chain import FIXTURES, UTM_10N, pois_of
from worst_route.pipeline import PIPELINE_VERSION, cleanup, enrich, tiers
from worst_route.pipeline.build import build_raw_graph
from worst_route.pipeline.qa import LAYERS, parameters, run_qa
from worst_route.pipeline.save import graph_data, save_build

META: dict[str, Any] = {
    "region": "test",
    "build_id": "b1",
    "crs_epsg": UTM_10N,
    "created": "2026-10-30T00:00:00+00:00",
    "pipeline_version": PIPELINE_VERSION,
    "params": {},
    "sources": [],
}


def built(name: str, tmp_path: Path) -> Path:
    """Every stage of a fixture and its saved build, in `tmp_path / "b1"`."""
    source = FIXTURES / f"{name}.osm"
    path = tmp_path / "b1"
    (path / "stages").mkdir(parents=True)
    graph, cleaned = cleanup.run_cleanup(build_raw_graph(source, UTM_10N)[0])
    cleanup.save_stage(graph, cleaned, path)
    enrich.save_stage(graph, enrich.run_enrich(graph, pois_of(source, tmp_path)), path)
    report = tiers.run_tiers(graph, allow_fragmented=True)
    tiers.save_stage(report, path)
    save_build(graph_data(graph, [t.node_ids for t in report.tiers], META), path)
    return path


def layer(path: Path, name: str) -> list[dict[str, Any]]:
    collection = json.loads((path / "qa" / f"{name}.geojson").read_text())
    assert collection["type"] == "FeatureCollection"
    return collection["features"]  # type: ignore[no-any-return]


def test_qa_of_a_build_that_passes(tmp_path: Path) -> None:
    path = built("divided", tmp_path)
    report = run_qa(path)
    assert report.passed
    assert [(t.name, t.value, t.passed) for t in report.thresholds] == [
        ("twin coverage", None, True),  # no one-way motorway or trunk to measure
        ("SCC retained", 1.0, True),
        ("SCC largest dropped", 0, True),
        ("T0 retained", 1.0, True),
        ("T1 retained", 1.0, True),
        ("T2 retained", 1.0, True),
        ("T3 retained", 1.0, True),
        ("T4 retained", 1.0, True),
    ]
    written = json.loads((path / "qa" / "qa.json").read_text())
    assert written["passed"] is True and written["build_id"] == "b1"
    assert written["counts"]["nodes"] == 15 and written["counts"]["edges"] == 25
    assert written["counts"]["node_kinds"] == {
        "plain": 14,
        "cluster": 1,
        "interchange": 0,
        "roundabout": 0,
    }
    assert set(written["counts"]["by_class"]) == {"primary", "residential"}
    assert written["counts"]["by_tier"]["T1"] == {"nodes": 8, "edges": 11}
    assert written["twins"]["pairs"] == written["twins"]["pairs_saved"] == 5
    assert "node_ids" not in written["tiers"]["tiers"][0]
    assert set(written) >= {"cleanup", "enrich", "tiers", "save", "thresholds"}
    twins = layer(path, "twins")
    assert len(twins) == 5
    for feature in twins:
        assert feature["geometry"]["type"] == "MultiLineString"
        assert len(feature["geometry"]["coordinates"]) == 2
        assert feature["properties"]["identity"] == ["OR 99W", "OR 99W"]
    assert [layer(path, name) for name in ("superjunctions", "dropped_components", "tiers")] == [
        [],
        [],
        [],
    ]


def test_qa_of_a_build_that_fails(tmp_path: Path) -> None:
    path = built("diamond", tmp_path)
    report = run_qa(path)
    assert not report.passed
    failed = [t.name for t in report.thresholds if not t.passed]
    assert failed == ["T0 retained", "T1 retained", "T2 retained", "T3 retained"]
    assert report.thresholds[0].value == 1.0  # the motorway's carriageways are twins
    (junction,) = layer(path, "superjunctions")
    assert junction["geometry"]["type"] == "Point"
    assert junction["properties"] == {
        "node": 6,  # super-nodes are numbered after the others
        "kind": "interchange",
        "parts": 6,
        "members": 10,
        "transitions": 16,
        "in": 4,
        "out": 4,
        "every_turn": True,
    }
    dropped = layer(path, "tiers")  # four of each fragmented tier's nodes
    assert [f["properties"]["tier"] for f in dropped] == [f"T{t}" for t in range(4) for _ in "abcd"]
    assert len(layer(path, "twins")) == 2
    twins = json.loads((path / "qa" / "qa.json").read_text())["twins"]
    assert (twins["pairs"], twins["pairs_saved"]) == (3, 2)  # found at stage A, then carried
    assert report.superjunctions == {"interchange": {6: 1}, "roundabout": {}}
    assert json.loads((path / "qa" / "qa.json").read_text())["passed"] is False


def test_qa_twin_coverage_threshold(tmp_path: Path) -> None:
    path = built("cloverleaf", tmp_path)
    stage = path / "stages" / "cleanup.json"
    report = json.loads(stage.read_text())
    report["twins"]["coverage"] = 0.9
    stage.write_text(json.dumps(report))
    twin = run_qa(path).thresholds[0]
    assert (twin.name, twin.value, twin.minimum, twin.passed) == ("twin coverage", 0.9, 0.95, False)


def test_qa_largest_dropped_component_threshold(tmp_path: Path) -> None:
    path = built("divided", tmp_path)
    stage = path / "stages" / "cleanup.json"
    report = json.loads(stage.read_text())
    dropped = [{"nodes": n, "length_m": 10.0, "lon": -122.6, "lat": 45.5} for n in (200, 3)]
    report["connectivity"]["largest_dropped"] = dropped
    stage.write_text(json.dumps(report))
    largest = run_qa(path).thresholds[2]
    assert (largest.name, largest.value, largest.minimum, largest.maximum, largest.passed) == (
        "SCC largest dropped",
        200,
        None,
        200,
        True,
    )
    dropped[0]["nodes"] = 201
    stage.write_text(json.dumps(report))
    qa = run_qa(path)
    assert not qa.passed and [t.name for t in qa.thresholds if not t.passed] == [
        "SCC largest dropped"
    ]


def test_qa_lists_dropped_components(tmp_path: Path) -> None:
    path = built("divided", tmp_path)
    stage = path / "stages" / "cleanup.json"
    report = json.loads(stage.read_text())
    report["connectivity"]["largest_dropped"] = [
        {"nodes": 3, "length_m": 120.04, "lon": -122.6, "lat": 45.5}
    ]
    stage.write_text(json.dumps(report))
    run_qa(path)
    (point,) = layer(path, "dropped_components")
    assert point["geometry"]["coordinates"] == [-122.6, 45.5]
    assert point["properties"] == {"nodes": 3, "length_m": 120.0}


@pytest.mark.parametrize("name", LAYERS)
def test_every_layer_is_written(name: str, tmp_path: Path) -> None:
    path = built("roundabout", tmp_path)
    run_qa(path)
    assert isinstance(layer(path, name), list)


def test_parameters_are_json() -> None:
    values = parameters()
    assert json.loads(json.dumps(values)) == values
    assert values == {
        "QA_TWIN_COVERAGE_MIN": 0.95,
        "SCC_MIN_RETAINED": 0.98,
        "SCC_MAX_DROPPED_NODES": 200,
        "TIER_MIN_RETAINED": 0.95,
    }
