"""The route store: finished routes in sqlite (`docs/reference/api.md`, **The store**).

A route is stored when its job ends with one (`done`, cancelled or timed out included); every
stored route has passed the validator. A row holds what `route.json` holds
(`engine/io.py`), plus the API request as planned and the baseline's edges:

    routes(id, created, region, build_id, graph_version, engine_version, seed,
           request_json    the engine's RouteRequest, as in route.json
           input_json      the API request as planned (seed filled in; for a continuation the
                           stops, tier and leash of the route it continued)
           stops_json      [{lat, lon, node, moved_m, note}], where each stop was snapped
           nodes, edges    int32 little-endian blobs
           stop_idx        JSON list
           baseline_edges  int32 little-endian blob
           metrics_json    route.json's metrics
           run_json        route.json's run)

`route_id(graph_version, nodes, edges, stop_idx)` is the first 12 hex digits of a sha256 over
the graph_version, the node and edge ids as int32 little-endian bytes and the stop positions as
JSON, so the same route on the same build has one id whichever run found it; `put` keeps the
row stored first. `RouteStore(path)` creates the file and its table when missing, and opens a
connection per call, so it may be used from any thread. `get` returns a `StoredRoute`, which the
validator reads like a `Route` (its metrics' counters an alphabet route's `alphabet`, which the
validator's alphabet check needs).
"""

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from worst_route.engine.request import RouteRequest
from worst_route.engine.route import IntArray, Metrics
from worst_route.graph.spatial import Snap

_COLUMNS = (
    "id",
    "created",
    "region",
    "build_id",
    "graph_version",
    "engine_version",
    "seed",
    "request_json",
    "input_json",
    "stops_json",
    "nodes",
    "edges",
    "stop_idx",
    "baseline_edges",
    "metrics_json",
    "run_json",
)
_SCHEMA = f"CREATE TABLE IF NOT EXISTS routes ({', '.join(_COLUMNS)}, PRIMARY KEY (id))"


@dataclass(frozen=True, eq=False)
class StoredRoute:
    """A route read back from the store."""

    route_id: str
    created: datetime
    region: str
    build_id: str
    graph_version: str
    engine_version: str
    seed: int
    request: RouteRequest
    input: dict[str, Any]
    stops: tuple[Snap, ...]
    nodes: IntArray
    edges: IntArray
    stop_idx: tuple[int, ...]
    baseline_edges: IntArray
    metrics: Metrics
    metrics_json: dict[str, Any]
    run: dict[str, Any]


def route_id(
    graph_version: str, nodes: ArrayLike, edges: ArrayLike, stop_idx: Sequence[int]
) -> str:
    """The id of a route: 12 hex digits of a sha256 over its build and its arrays."""
    digest = hashlib.sha256(graph_version.encode())
    digest.update(_blob(nodes))
    digest.update(_blob(edges))
    digest.update(json.dumps([int(k) for k in stop_idx]).encode())
    return digest.hexdigest()[:12]


class RouteStore:
    """The routes the API has made, in one sqlite file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as con, con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(_SCHEMA)

    def put(
        self, document: dict[str, Any], *, input: dict[str, Any], baseline_edges: ArrayLike
    ) -> str:
        """Store a route given as its `route.json` document; return its id."""
        rid = route_id(
            document["graph_version"], document["nodes"], document["edges"], document["stop_idx"]
        )
        row = (
            rid,
            datetime.now(UTC).isoformat(),
            document["region"],
            document["build_id"],
            document["graph_version"],
            document["engine_version"],
            int(document["seed"]),
            json.dumps(document["request"]),
            json.dumps(input),
            json.dumps(document["stops"]),
            _blob(document["nodes"]),
            _blob(document["edges"]),
            json.dumps([int(k) for k in document["stop_idx"]]),
            _blob(baseline_edges),
            json.dumps(document["metrics"]),
            json.dumps(document["run"]),
        )
        marks = ", ".join("?" for _ in _COLUMNS)
        with closing(sqlite3.connect(self.path, timeout=10)) as con, con:
            con.execute(f"INSERT OR IGNORE INTO routes VALUES ({marks})", row)
        return rid

    def get(self, rid: str) -> StoredRoute | None:
        """The route stored as `rid`, or None."""
        with closing(sqlite3.connect(self.path, timeout=10)) as con:
            row = con.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM routes WHERE id = ?", (rid,)
            ).fetchone()
        if row is None:
            return None
        values = dict(zip(_COLUMNS, row, strict=True))
        metrics = json.loads(values["metrics_json"])
        return StoredRoute(
            route_id=values["id"],
            created=datetime.fromisoformat(values["created"]),
            region=values["region"],
            build_id=values["build_id"],
            graph_version=values["graph_version"],
            engine_version=values["engine_version"],
            seed=int(values["seed"]),
            request=RouteRequest.model_validate_json(values["request_json"]),
            input=json.loads(values["input_json"]),
            stops=tuple(Snap(**s) for s in json.loads(values["stops_json"])),
            nodes=_ids(values["nodes"]),
            edges=_ids(values["edges"]),
            stop_idx=tuple(json.loads(values["stop_idx"])),
            baseline_edges=_ids(values["baseline_edges"]),
            metrics=Metrics(
                float(metrics["length_m"]),
                float(metrics["time_s"]),
                {"alphabet": metrics["alphabet"]} if "alphabet" in metrics else {},
            ),
            metrics_json=metrics,
            run=json.loads(values["run_json"]),
        )


def _blob(ids: ArrayLike) -> bytes:
    return np.asarray(ids, dtype="<i4").tobytes()


def _ids(blob: bytes) -> IntArray:
    return np.frombuffer(blob, dtype="<i4").astype(np.int32)
