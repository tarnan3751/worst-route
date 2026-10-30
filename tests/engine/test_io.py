import json
from pathlib import Path

import numpy as np
import pytest

from worst_route.config import TARGET_TOLERANCE
from worst_route.engine import ENGINE_VERSION
from worst_route.engine.io import (
    feature_collection,
    line_coordinates,
    read_route,
    route_document,
    route_geojson,
)
from worst_route.engine.plan import PlanResult, plan_route
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.target import Target
from worst_route.engine.validator import validate_route
from worst_route.graph.spatial import SpatialIndex

from .graphs import at_node, lattice

GRID = lattice(8, 8, rounded_lonlat=True)


def planned(stops: list[int], seed: int = 2) -> PlanResult:
    request = RouteRequest(
        stops=[at_node(GRID, v) for v in stops], seed=seed, max_iterations=150, budget_s=120
    )
    return plan_route(GRID, request)


def test_route_json_round_trips(tmp_path: Path) -> None:
    result = planned([0, 27, 63])
    doc = route_document(GRID, result)
    path = tmp_path / "route.json"
    path.write_text(json.dumps(doc, indent=2))
    saved = read_route(path)
    assert saved.graph_version == GRID.meta["graph_version"]
    assert (saved.region, saved.build_id, saved.seed) == ("test", "test", 2)
    assert saved.engine_version == ENGINE_VERSION
    assert saved.request == result.request
    assert saved.stops == result.snaps
    assert saved.nodes.tobytes() == result.route.nodes.tobytes()
    assert saved.edges.tobytes() == result.route.edges.tobytes()
    assert saved.stop_idx == result.route.stop_idx
    assert saved.metrics == result.route.metrics
    metrics = doc["metrics"]
    assert metrics["ratio"] == pytest.approx(metrics["length_m"] / metrics["baseline_length_m"])
    assert doc["run"] == {
        "attempts": 150,
        "accepted": result.run.accepted,
        "stopped_because": "budget",
        "elapsed_s": result.run.elapsed_s,
        "backend": "csr",  # plan_route's default when numba imports
    }
    masks = request_masks(SpatialIndex(GRID), GRID, saved.request)
    report = validate_route(
        GRID,
        saved,
        stop_nodes=[s.node for s in saved.stops],
        node_mask=masks.active,
        banned_edges=masks.banned,
    )
    assert report.ok


def test_the_target_is_in_the_request_and_for_a_target_run_in_the_metrics() -> None:
    plain = route_document(GRID, planned([0, 63]))
    assert plain["request"]["target"] == {"kind": "max", "value": None}
    assert "target" not in plain["metrics"]

    request = RouteRequest(
        stops=[at_node(GRID, v) for v in (0, 63)],
        seed=2,
        max_iterations=150,
        budget_s=120,
        target=Target(kind="ratio", value=3),
    )
    doc = route_document(GRID, plan_route(GRID, request))
    assert doc["request"]["target"] == {"kind": "ratio", "value": 3.0}
    target = doc["metrics"]["target"]
    assert target["kind"] == "ratio" and target["value"] == 3.0
    assert target["achieved"] == doc["metrics"]["ratio"]
    assert target["within_tolerance"] == (abs(target["achieved"] / 3 - 1) <= TARGET_TOLERANCE)


def test_a_round_trip_has_no_ratio_and_no_baseline_line() -> None:
    result = planned([27, 27])
    assert route_document(GRID, result)["metrics"]["ratio"] is None
    features = route_geojson(GRID, result)["features"]
    assert features[1]["properties"]["kind"] == "baseline" and features[1]["geometry"] is None


def test_geojson_draws_the_route_the_baseline_and_the_stops() -> None:
    result = planned([0, 27, 63])
    collection = route_geojson(GRID, result)
    assert collection["type"] == "FeatureCollection"
    route, baseline, *stops = collection["features"]
    assert route["properties"]["kind"] == "route" and baseline["properties"]["kind"] == "baseline"
    line = np.array(route["geometry"]["coordinates"])
    assert len(line) == len(result.route.nodes)  # one point per node: shared junctions kept once
    start = result.route.nodes[0]
    assert line[0] == pytest.approx([GRID.node_lon[start], GRID.node_lat[start]], abs=1e-5)
    assert route["properties"]["length_m"] == result.route.metrics.length_m
    assert len(baseline["geometry"]["coordinates"]) == len(result.baseline.nodes)
    assert [s["properties"]["node"] for s in stops] == [0, 27, 63]
    assert stops[1]["geometry"]["coordinates"] == [
        round(result.snaps[1].lon, 7),
        round(result.snaps[1].lat, 7),
    ]
    json.dumps(collection)  # JSON-native throughout


def test_the_collection_from_a_route_s_parts_is_route_geojson_s() -> None:
    result = planned([0, 27, 63])
    route, baseline = result.route, result.baseline
    parts = feature_collection(
        GRID,
        edges=route.edges,
        length_m=route.metrics.length_m,
        time_s=route.metrics.time_s,
        baseline_edges=baseline.edges,
        baseline_length_m=baseline.length_m,
        baseline_time_s=baseline.time_s,
        snaps=result.snaps,
    )
    assert json.dumps(parts) == json.dumps(route_geojson(GRID, result))
    line = parts["features"][0]["geometry"]["coordinates"]
    assert line_coordinates(GRID, route.edges) == line


def test_a_file_that_is_not_a_route_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "route.json"
    path.write_text(json.dumps({"graph_version": "x"}))
    with pytest.raises(KeyError):
        read_route(path)
