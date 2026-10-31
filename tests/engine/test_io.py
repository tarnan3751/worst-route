import json
from pathlib import Path

import pytest

from worst_route.config import TARGET_TOLERANCE
from worst_route.engine import ENGINE_VERSION
from worst_route.engine.io import read_route, route_document
from worst_route.engine.plan import PlanResult, plan_route
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.target import Target
from worst_route.engine.turns import count_turns
from worst_route.engine.validator import validate_route
from worst_route.graph.spatial import SpatialIndex

from .graphs import at_node, lattice

GRID = lattice(8, 8)


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
    for stop in doc["stops"]:  # the node's position, at most 7 decimals as OSM writes them
        assert stop["lat"] == round(stop["lat"], 7) and stop["lon"] == round(stop["lon"], 7)
        assert len(repr(stop["lon"]).split(".")[1]) <= 7
    assert saved.nodes.tobytes() == result.route.nodes.tobytes()
    assert saved.edges.tobytes() == result.route.edges.tobytes()
    assert saved.stop_idx == result.route.stop_idx
    metrics = doc["metrics"]
    reported = (saved.metrics.length_m, saved.metrics.time_s)
    assert reported == (result.route.metrics.length_m, result.route.metrics.time_s)
    assert saved.metrics.counters == {"items": metrics["items"]}  # for the validator's recount
    assert metrics["ratio"] == pytest.approx(metrics["length_m"] / metrics["baseline_length_m"])
    assert metrics["delay_s"] == 0.0  # the lattice has no signals
    assert metrics["turns"] == count_turns(GRID, result.route.edges)
    assert sum(metrics["turns"].values()) == len(result.route.edges) - 1
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


def test_a_round_trip_has_no_ratio() -> None:
    assert route_document(GRID, planned([27, 27]))["metrics"]["ratio"] is None


def test_a_file_that_is_not_a_route_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "route.json"
    path.write_text(json.dumps({"graph_version": "x"}))
    with pytest.raises(KeyError):
        read_route(path)
