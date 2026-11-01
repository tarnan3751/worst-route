"""What runs in a job's worker process (`docs/reference/api.md`, **Jobs**).

`run_job(spec, out, cancel=None)` loads the build (`graph.loader`, memory-mapped), plans the
request with `plan_route` (from `spec.base` for "make it worse") and puts on `out`, in order:

- `Started`, once, before the loop: the route the loop starts from (the seed, or the base
  route), its score and metrics, the baseline, and the order the stops are visited in with the
  stops resolved in it (with `reorder`, the order the engine's search chose);
- `Spliced`, for every accepted splice: the window, the detour's nodes and edges, and the
  route's attempt count, elapsed time, score and metrics after it; for the alphabet route,
  `Improved` instead, for every better route its search finds: the whole route, its restart
  count, elapsed time, score and metrics;
- then `Finished` (the validated route as its `route.json` document, the baseline's edges, and
  the route's score and metrics) or `Failed` (an error code, a one-line message, the error's
  fields and a traceback for the log).

The job manager runs it in a spawned process through `run_in_process(spec, conn)`, which sends
the messages down `conn`, the write end of a one-way pipe (the API process holds the read end
only), and stops it with `terminate()`. In a process, `run_job` also checks on start and at
every accepted splice that the API process is still alive (`multiprocessing.parent_process()`);
when it is gone, or the pipe has no reader any more, the worker closes its end and exits at
once (`os._exit`), so a crashed API never leaves workers searching. With inline workers (tests)
it runs in a thread on a `queue.Queue`, and `cancel`, an Event, ends it quietly at the next
accepted splice. A `BaseRouteMismatch` from `plan_route` (a base route the request cannot
continue) is reported under the API's code for it, and a `BrandRefused` (a brand the build does
not have or the request's area does not hold) as `INVALID_REQUEST` on the field `brand`. The
worker imports the engine and the graph package, never FastAPI, and every message is a small
picklable dataclass.
"""

import multiprocessing
import os
import traceback
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from threading import Event
from typing import Any, Protocol

from worst_route.config import BackendName
from worst_route.engine.io import route_document
from worst_route.engine.objectives.brand import BrandRefused
from worst_route.engine.plan import BaseRouteMismatch, plan_route
from worst_route.engine.request import RequestMasks, RouteRequest
from worst_route.engine.route import IntArray, Metrics, Route
from worst_route.engine.seed_route import Baseline
from worst_route.engine.snap import InfeasibleHardMode, InfeasibleTurnConstraint, StopUnreachable
from worst_route.engine.validator import ValidationFailed
from worst_route.engine.worsen import ProgressEvent
from worst_route.graph.loader import load_build
from worst_route.graph.spatial import Snap, SnapError


@dataclass(frozen=True, eq=False)
class RouteArrays:
    """A route's arrays and reported metrics: a base to continue, or the manager's own copy."""

    nodes: IntArray
    edges: IntArray
    stop_idx: tuple[int, ...]
    metrics: Metrics


@dataclass(frozen=True, eq=False)
class JobSpec:
    """Everything a worker needs: where the build is, the request, and the route to continue."""

    data_dir: Path
    region: str
    build_id: str
    backend: BackendName
    request: RouteRequest
    base: RouteArrays | None = None


@dataclass(frozen=True, eq=False)
class Started:
    route: RouteArrays
    score: float
    baseline_length_m: float
    baseline_time_s: float
    baseline_edges: IntArray
    baseline_delay_s: float
    order: tuple[int, ...]  # each stop's position among those of the request
    snaps: tuple[Snap, ...]


@dataclass(frozen=True, eq=False)
class Spliced:
    iteration: int
    elapsed_s: float
    score: float
    metrics: Metrics
    i: int
    j: int
    nodes: IntArray
    edges: IntArray


@dataclass(frozen=True, eq=False)
class Improved:
    iteration: int
    elapsed_s: float
    score: float
    route: RouteArrays


@dataclass(frozen=True, eq=False)
class Finished:
    document: dict[str, Any]
    baseline_edges: IntArray
    score: float  # the route's, which its document does not carry
    metrics: Metrics  # the route's, its objective's counters and delays among them


@dataclass(frozen=True, eq=False)
class Failed:
    code: str
    message: str
    fields: dict[str, Any] = field(default_factory=dict)
    trace: str = ""


Message = Started | Spliced | Improved | Finished | Failed


class Outbox(Protocol):
    """A `queue.Queue`, or a worker process's pipe: only `put` is used here."""

    def put(self, item: Message, /) -> None: ...


class _Pipe:
    """The write end of a worker's pipe, as an `Outbox`."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def put(self, item: Message, /) -> None:
        self.conn.send(item)


class _Cancelled(Exception):  # noqa: N818 (not an error: the job was stopped)
    pass


class _Orphaned(Exception):  # noqa: N818 (not an error: the API process is gone)
    pass


def unreachable(error: StopUnreachable) -> tuple[str, str, dict[str, Any]]:
    """The API's (code, message, fields) for a leg no route covers: `INFEASIBLE_TURN_CONSTRAINT`
    when a strict turn constraint is why (its message suggesting to drop `strict`), else
    `STOP_UNREACHABLE_WITHOUT_REPEAT` (its message suggesting to drop `hard` when hard mode is
    why)."""
    fields: dict[str, Any] = {"leg": list(error.leg), "stop_index": error.stop_index}
    if isinstance(error, InfeasibleTurnConstraint):
        message = f"{error}; try without strict"
        return "INFEASIBLE_TURN_CONSTRAINT", message, {**fields, "constraint": error.constraint}
    if isinstance(error, InfeasibleHardMode):
        return "STOP_UNREACHABLE_WITHOUT_REPEAT", f"{error}; try without hard", fields
    return "STOP_UNREACHABLE_WITHOUT_REPEAT", str(error), fields


def run_in_process(spec: JobSpec, conn: Connection) -> None:
    """A worker process's main: `run_job` down `conn`; exits at once if the API process goes."""
    try:
        run_job(spec, _Pipe(conn), parent=multiprocessing.parent_process())
    except (_Orphaned, BrokenPipeError):
        conn.close()
        os._exit(1)
    conn.close()


def run_job(
    spec: JobSpec, out: Outbox, cancel: Event | None = None, parent: BaseProcess | None = None
) -> None:
    """Plan `spec`'s request, putting every message on `out`."""

    def check() -> None:
        if cancel is not None and cancel.is_set():
            raise _Cancelled
        if parent is not None and not parent.is_alive():
            raise _Orphaned

    def start(
        snaps: tuple[Snap, ...],
        masks: RequestMasks,
        baseline: Baseline,
        route: Route,
        order: tuple[int, ...],
    ) -> None:
        check()
        arrays = RouteArrays(route.nodes, route.edges, route.stop_idx, route.metrics)
        out.put(
            Started(
                arrays,
                route.score,
                baseline.length_m,
                baseline.time_s,
                baseline.edges,
                baseline.delay_s,
                order,
                snaps,
            )
        )

    def progress(event: ProgressEvent, baseline: Baseline) -> None:
        check()
        s = event.splice
        if spec.request.objective == "alphabet":  # each event a whole better route
            route = RouteArrays(s.nodes, s.edges, (0, len(s.nodes) - 1), event.metrics)
            out.put(Improved(event.iteration, event.elapsed_s, event.score, route))
            return
        out.put(
            Spliced(
                event.iteration,
                event.elapsed_s,
                event.score,
                event.metrics,
                s.i,
                s.j,
                s.nodes,
                s.edges,
            )
        )

    try:
        loaded = load_build(spec.data_dir, spec.region, spec.build_id, backend=spec.backend)
        result = plan_route(
            loaded.data,
            spec.request,
            backend=loaded.backend,
            spatial=loaded.spatial,
            base=spec.base,
            on_start=start,
            on_progress=progress,
        )
    except _Cancelled:
        return
    except (_Orphaned, BrokenPipeError):
        raise
    except BaseRouteMismatch as error:
        out.put(Failed("BASE_ROUTE_MISMATCH", str(error), {"field": error.field}))
    except BrandRefused as error:
        out.put(Failed("INVALID_REQUEST", str(error), {"field": error.field}))
    except StopUnreachable as error:
        out.put(Failed(*unreachable(error)))
    except SnapError as error:
        out.put(Failed("NO_NODE_NEAR", str(error), {"stop_index": None}))
    except ValidationFailed as error:
        out.put(Failed("VALIDATION_FAILED", str(error), {}, traceback.format_exc()))
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        out.put(Failed("WORKER_FAILED", message, {}, traceback.format_exc()))
    else:
        document = route_document(loaded.data, result)
        route = result.route
        out.put(Finished(document, result.baseline.edges, route.score, route.metrics))
