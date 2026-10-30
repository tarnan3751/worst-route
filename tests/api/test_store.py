import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from worst_route.api.store import RouteStore, route_id
from worst_route.engine.io import route_document
from worst_route.engine.plan import PlanResult, plan_route
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.validator import validate_route
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex

from ..engine.graphs import at_node, lattice

GRID = lattice(8, 8, rounded_lonlat=True)


def planned(seed: int = 3) -> PlanResult:
    request = RouteRequest(
        stops=[at_node(GRID, v) for v in (0, 27, 63)], seed=seed, max_iterations=150, budget_s=120
    )
    return plan_route(GRID, request, backend=PythonBackend(GRID))


def put(store: RouteStore, result: PlanResult, **changes: Any) -> str:
    doc = {**route_document(GRID, result), **changes}
    return store.put(
        doc, input={"region": "test", "seed": doc["seed"]}, baseline_edges=result.baseline.edges
    )


def test_a_route_round_trips_and_validates(tmp_path: Path) -> None:
    store = RouteStore(tmp_path / "deep" / "routes.sqlite")
    result = planned()
    rid = put(store, result)
    saved = store.get(rid)
    assert saved is not None and saved.route_id == rid and len(rid) == 12
    doc = route_document(GRID, result)
    assert (saved.region, saved.build_id, saved.graph_version) == (
        "test",
        "test",
        doc["graph_version"],
    )
    assert saved.seed == 3 and saved.engine_version == doc["engine_version"]
    assert saved.request == result.request and saved.stops == result.snaps
    assert saved.input == {"region": "test", "seed": 3}
    assert saved.nodes.dtype == np.int32 and saved.nodes.tobytes() == result.route.nodes.tobytes()
    assert saved.edges.tobytes() == result.route.edges.tobytes()
    assert saved.baseline_edges.tobytes() == result.baseline.edges.tobytes()
    assert saved.stop_idx == result.route.stop_idx
    assert saved.metrics == result.route.metrics and saved.metrics_json == doc["metrics"]
    assert saved.run == doc["run"]
    masks = request_masks(SpatialIndex(GRID), GRID, saved.request)
    report = validate_route(
        GRID,
        saved,
        stop_nodes=[s.node for s in saved.stops],
        node_mask=masks.active,
        banned_edges=masks.banned,
    )
    assert report.ok
    with sqlite3.connect(tmp_path / "deep" / "routes.sqlite") as con:
        blob, stop_idx = con.execute("SELECT nodes, stop_idx FROM routes").fetchone()
    assert blob == result.route.nodes.astype("<i4").tobytes()
    assert json.loads(stop_idx) == list(result.route.stop_idx)


def test_an_unknown_id_is_none(tmp_path: Path) -> None:
    assert RouteStore(tmp_path / "routes.sqlite").get("000000000000") is None


def test_the_same_route_keeps_its_first_record(tmp_path: Path) -> None:
    store = RouteStore(tmp_path / "routes.sqlite")
    result = planned()
    rid = put(store, result)
    assert put(store, result, seed=99) == rid
    saved = store.get(rid)
    assert saved is not None and saved.seed == 3
    assert RouteStore(tmp_path / "routes.sqlite").get(rid) is not None  # reopened


def test_the_id_covers_the_build_the_edges_and_the_stops() -> None:
    base = route_id("v1", [0, 1, 2], [5, 6], [0, 2])
    assert base == route_id("v1", np.array([0, 1, 2]), np.array([5, 6]), (0, 2))
    assert base != route_id("v2", [0, 1, 2], [5, 6], [0, 2])
    assert base != route_id("v1", [0, 1, 2], [5, 7], [0, 2])  # a parallel edge
    assert base != route_id("v1", [0, 1, 2], [5, 6], [0, 1, 2])
