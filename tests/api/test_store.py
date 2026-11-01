import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from worst_route.api import store as store_module
from worst_route.api.store import DailyRow, RouteStore, route_id
from worst_route.engine.io import route_document
from worst_route.engine.plan import PlanResult, plan_route
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.validator import validate_route
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex

from ..engine.graphs import at_node, lattice

GRID = lattice(8, 8)


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


def test_a_day_s_row_is_its_graph_version_s_and_replaced_in_place(tmp_path: Path) -> None:
    store = RouteStore(tmp_path / "routes.sqlite")
    assert store.daily("2026-10-31", "us-t3", "v1") is None
    planning = DailyRow("2026-10-31", "us-t3", "b1", "v1", None, "planning", "a1b2c3")
    store.put_daily(planning)
    assert store.daily("2026-10-31", "us-t3", "v1") == planning
    done = DailyRow("2026-10-31", "us-t3", "b1", "v1", "5ea844bffd40", "done", "a1b2c3")
    store.put_daily(done)
    assert RouteStore(tmp_path / "routes.sqlite").daily("2026-10-31", "us-t3", "v1") == done
    # the same build id at another graph_version (`save --force`): another row
    assert store.daily("2026-10-31", "us-t3", "v2") is None
    assert store.daily("2026-11-01", "us-t3", "v1") is None
    with sqlite3.connect(tmp_path / "routes.sqlite") as con:
        assert con.execute("SELECT COUNT(*) FROM daily").fetchone() == (1,)


def test_a_route_keeps_its_order_and_a_store_from_before_gains_one(tmp_path: Path) -> None:
    path = tmp_path / "routes.sqlite"
    store = RouteStore(path)
    rid = put(store, planned(), order=[0, 1, 2])
    reordered = put(store, planned(seed=4), order=[0, 2, 1])  # as if visited in that order
    orders = [saved.order for saved in map(store.get, (rid, reordered)) if saved is not None]
    assert orders == [(0, 1, 2), (0, 2, 1)]
    as_before_the_worst_order(path)
    reopened = RouteStore(path)
    with sqlite3.connect(path) as con:
        assert "order_json" in [row[1] for row in con.execute("PRAGMA table_info(routes)")]
    old = reopened.get(reordered)
    assert old is not None and old.order == (0, 1, 2)  # its order not kept: the identity
    newer = reopened.get(put(reopened, planned(seed=5), order=[0, 2, 1]))
    assert newer is not None and newer.order == (0, 2, 1)


def as_before_the_worst_order(path: Path) -> None:
    """Make the store at `path` one from before the worst order: its routes table without
    order_json, its rows without one."""
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE old AS SELECT * FROM routes")
        con.execute("ALTER TABLE old DROP COLUMN order_json")
        con.execute("DROP TABLE routes")
        con.execute("ALTER TABLE old RENAME TO routes")


def columns(path: Path) -> list[str]:
    with sqlite3.connect(path) as con:
        return [row[1] for row in con.execute("PRAGMA table_info(routes)")]


def test_opening_a_store_from_before_again_and_again_adds_its_order_once(tmp_path: Path) -> None:
    path = tmp_path / "routes.sqlite"
    rid = put(RouteStore(path), planned())
    as_before_the_worst_order(path)
    for _ in range(3):
        store = RouteStore(path)
    assert columns(path).count("order_json") == 1
    saved = store.get(rid)
    assert saved is not None and saved.order == (0, 1, 2)


def test_an_order_column_another_server_added_meanwhile_counts_as_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the column read as missing though it is there, as for a server that looked before another
    # added it: the change refused as a duplicate, the store opens all the same
    path = tmp_path / "routes.sqlite"
    rid = put(RouteStore(path), planned())
    monkeypatch.setattr(store_module, "_columns", lambda con, table: set())
    store = RouteStore(path)
    assert columns(path).count("order_json") == 1
    saved = store.get(rid)
    assert saved is not None and saved.order == (0, 1, 2)
    monkeypatch.setattr(store_module, "_columns", broken)  # any other error is raised
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        RouteStore(path)


def broken(con: sqlite3.Connection, table: str) -> set[str]:
    raise sqlite3.OperationalError("disk I/O error")
