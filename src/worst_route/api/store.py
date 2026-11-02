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
           run_json        route.json's run
           order_json      route.json's order: each stop's position among those asked for)

The daily route's rows (`api/daily_api.py`) are a second table:

    daily(date, region, build_id, graph_version, route_id, status, job_id)

one row per UTC date (YYYY-MM-DD), region and `graph_version` (a build replaced under its id by
`pipeline save --force` has another, and so its own row), `status` planning (`job_id` names its
job), done (`route_id` its route) or failed; `put_daily` replaces the day's row. The daily
challenges (`api/challenges_api.py`) are a third:

    challenges(date, region, graph_version, build_id, challenges_json)

one row per date, region and `graph_version` once that day's challenges are made, `challenges_json`
the seven as `GET /challenges` answers them (the list of `ChallengeOut`s, today's route made worse
left out); `put_challenges` replaces it. A store from before them gains the table when it is
opened, and code from before them never reads it.

A store made before the worst order gains `order_json` when it is opened (`ALTER TABLE`, the
check and the change under one write lock, `BEGIN IMMEDIATE`, so servers opening it at once add
it once; a column found added all the same counts as there); its rows, and a document without
`order`, read as the identity order. The change is one way: code from before the series stores
a row by its 16 columns' positions, which a store with 17 refuses ("table routes has 17 columns
but 16 values were supplied"); the way back is to drop the column (`ALTER TABLE routes DROP
COLUMN order_json`, SQLite 3.35 and later) or to start a fresh store file. A reordered route's
`input_json` holds its stops in the order the route visits them, as its `request_json` and
`stops_json` do.

`route_id(graph_version, nodes, edges, stop_idx)` is the first 12 hex digits of a sha256 over
the graph_version, the node and edge ids as int32 little-endian bytes and the stop positions as
JSON, so the same route on the same build has one id whichever run found it; `put` keeps the
row stored first. `RouteStore(path)` creates the file and its tables when missing, and opens a
connection per call, so it may be used from any thread. `get` returns a `StoredRoute`, which the
validator reads like a `Route` (its metrics' counters an alphabet route's `alphabet`, which the
validator's alphabet check needs).
"""

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import astuple, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

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
    "order_json",
)
_SCHEMA = f"CREATE TABLE IF NOT EXISTS routes ({', '.join(_COLUMNS)}, PRIMARY KEY (id))"
_DAILY_COLUMNS = ("date", "region", "build_id", "graph_version", "route_id", "status", "job_id")
_DAILY_SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS daily ({', '.join(_DAILY_COLUMNS)}, "
    "PRIMARY KEY (date, region, graph_version))"
)

_CHALLENGES_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS challenges (date, region, graph_version, build_id, "
    "challenges_json, PRIMARY KEY (date, region, graph_version))"
)

DailyStatus = Literal["planning", "done", "failed"]


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
    order: tuple[int, ...]


@dataclass(frozen=True)
class DailyRow:
    """A day's route on one build, at its graph_version: planning (by its job), done (its route)
    or failed."""

    date: str  # the UTC date, YYYY-MM-DD
    region: str
    build_id: str
    graph_version: str
    route_id: str | None
    status: DailyStatus
    job_id: str | None


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
            con.execute(_DAILY_SCHEMA)
            con.execute(_CHALLENGES_SCHEMA)
        self._add_order_column()

    def _add_order_column(self) -> None:
        """Give a store from before the worst order its `order_json`: the check and the change
        under one write lock, so that servers opening it at once add it once; a column another
        connection added all the same counts as there."""
        with closing(sqlite3.connect(self.path, isolation_level=None)) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                if "order_json" not in _columns(con, "routes"):
                    con.execute("ALTER TABLE routes ADD COLUMN order_json")
            except sqlite3.OperationalError as error:
                con.execute("ROLLBACK")
                if "duplicate column name" not in str(error):
                    raise
            else:
                con.execute("COMMIT")

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
            json.dumps([int(k) for k in document.get("order", range(len(document["stops"])))]),
        )
        names, marks = ", ".join(_COLUMNS), ", ".join("?" for _ in _COLUMNS)
        with closing(sqlite3.connect(self.path, timeout=10)) as con, con:
            con.execute(f"INSERT OR IGNORE INTO routes ({names}) VALUES ({marks})", row)
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
        stops = tuple(Snap(**s) for s in json.loads(values["stops_json"]))
        order = values["order_json"]
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
            stops=stops,
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
            order=tuple(json.loads(order)) if order is not None else tuple(range(len(stops))),
        )

    def daily(self, date: str, region: str, graph_version: str) -> DailyRow | None:
        """The daily route's row for `date` on `region`'s build at `graph_version`, or None."""
        with closing(sqlite3.connect(self.path, timeout=10)) as con:
            row = con.execute(
                f"SELECT {', '.join(_DAILY_COLUMNS)} FROM daily "
                "WHERE date = ? AND region = ? AND graph_version = ?",
                (date, region, graph_version),
            ).fetchone()
        return None if row is None else DailyRow(*row)

    def put_daily(self, row: DailyRow) -> None:
        """Record `row`, in place of the day's row on its build."""
        values = astuple(row)
        marks = ", ".join("?" for _ in _DAILY_COLUMNS)
        with closing(sqlite3.connect(self.path, timeout=10)) as con, con:
            con.execute(f"INSERT OR REPLACE INTO daily VALUES ({marks})", values)

    def challenges(self, date: str, region: str, graph_version: str) -> str | None:
        """The challenges of `date` on `region`'s build at `graph_version`, as JSON, or None."""
        with closing(sqlite3.connect(self.path, timeout=10)) as con:
            row = con.execute(
                "SELECT challenges_json FROM challenges "
                "WHERE date = ? AND region = ? AND graph_version = ?",
                (date, region, graph_version),
            ).fetchone()
        return None if row is None else str(row[0])

    def put_challenges(
        self, date: str, region: str, graph_version: str, build_id: str, challenges: str
    ) -> None:
        """Record the challenges of `date` on the build (JSON), in place of any kept."""
        values = (date, region, graph_version, build_id, challenges)
        with closing(sqlite3.connect(self.path, timeout=10)) as con, con:
            con.execute("INSERT OR REPLACE INTO challenges VALUES (?, ?, ?, ?, ?)", values)


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    """The names of `table`'s columns."""
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def _blob(ids: ArrayLike) -> bytes:
    return np.asarray(ids, dtype="<i4").tobytes()


def _ids(blob: bytes) -> IntArray:
    return np.frombuffer(blob, dtype="<i4").astype(np.int32)
