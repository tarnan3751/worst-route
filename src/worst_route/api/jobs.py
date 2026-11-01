"""The job manager: workers, their events, cancellation (`docs/reference/api.md`, **Jobs**).

A job plans one request. `prepare(loaded, request)` is the pre-flight the endpoint runs before a
job exists (the request's masks, a pilgrimage's brand within them, and `resolve_stops`, so a
brand out of reach or a stop that cannot be snapped or reached is a 422, not a job); the worker
repeats it, deterministically. `JobManager.submit` makes the job,
emits its `snapped` event from the pre-flight (each stop's position among those given, `order`,
the identity), and starts its worker (`api/worker.py`) in a
spawned process, or queues it when `max_jobs` are running; with `max_queued` waiting too it
raises `TooManyJobs` (the endpoint's 429). On a public server each job has a client
(`api/limits.py`), who runs at most `running_per_client` jobs at once: its next waits in the
queue even while workers are free, and starts when its running one ends, before later jobs of
other clients only if theirs cannot start; with `queued_per_client` of its own waiting too, its
next is `TooManyJobs` with its counts.

A job with `reorder` has no `snapped` event until its worker's search has chosen the order: the
pre-flight checked the stops in their given order. On `Started`, which carries the order and the
stops resolved in it, the manager plans as the worker does: the job's request takes its stops in
that order, its masks are worked out again for it (in a thread), its stops become the worker's,
the input's stops are put in that order (so the stored route's labels and directions follow
the route), and `snapped` is emitted with the order, before `baseline`. A reorder job cancelled
during the search ends in `error` `CANCELLED`, as any job before its route exists.

While a job runs, its follower task reads the worker's queue (in a thread, so the event loop
never blocks) and keeps the job's own copy of the route: the route the loop started from
(`Started`, which emits `baseline` and a `snapshot` of iteration 0), then every accepted splice
applied in order, as a client would. It also keeps the best route so far apart, by the request's
goal (`engine.target.Goal`): in a max mode the highest-scoring route, which is the copy until the
run converges and anneals (`engine/worsen.py`: shorter splices taken too), and may be an earlier
one after; a target run's best is the route closest to its target, which after an overshoot
may be an earlier one. Splices are sent as `progress` events at most once per `coalesce_s`, each
with the best route's score and metrics, every `snapshot_every`-th followed by a `snapshot`. The
alphabet route's worker sends each better route whole (`Improved`) instead: it becomes the job's
route and its best, sent at once as a `snapshot` and then a `progress` with its numbers and no
splice. `Finished` stores the worker's validated route (the engine's best) and emits `done`;
`Failed` emits `error`. Whenever the route a job stores is not the copy its clients have drawn,
a `snapshot` of it comes just before `done`.

Event ids count up per job. A subscriber (`subscribe`, the SSE endpoint's) first gets the
stored `snapped` and `baseline` events it has not seen (those after `Last-Event-ID`), then, once
the route exists, a `snapshot` of it carrying the id of the latest event that changed the route
(pending splices are flushed first, so no later `progress` repeats one), then the end event if
the job has ended, then live events, so its ids rise strictly. A stream ends after `done` or
`error`, and every pending splice is sent before either.

`cancel` ends a job early: a queued job, or one whose route does not exist yet, ends in `error`
`CANCELLED`; a running one has its worker terminated (its queue is never read again: a process
killed while writing may leave it corrupt), and the manager's best route so far is validated
against the pre-flight's stops and masks, stored with `stopped_because: "cancelled"` and
reported in `done` (its `run.attempts` is the attempt of the last splice applied, so planning
again with that many attempts gives it back), or `error` `VALIDATION_FAILED` if it fails. A
running job is stopped the same way `kill_grace_s` after its budget, as `"timeout"`
(`WORKER_TIMEOUT` before its route exists); a reorder job's deadline is `reorder_search_s`
later until its `Started` (its search for the order runs before the loop) and from then is the
budget and the grace again, counted from `Started`. A worker that dies without a result is
`WORKER_FAILED`: a process worker's messages come down a one-way pipe whose only write end is
the worker's, so its death ends a read at once (EOF, even partway through a message), and every
read waits at most `read_grace_s` (`poll`), so no thread is ever left blocked. A worker that
does not start ends its job as `WORKER_FAILED` too, and the queue moves on. Every `sweep_s` the
manager cancels a job whose subscribers have all gone for `orphan_s` (once one had connected; a
queued one is dropped) and forgets an ended job after `ttl_s`; an error on one job ends that
job as `WORKER_FAILED` and the sweep goes on.

With `inline_workers` (tests), the worker runs in a thread of this process on a `queue.Queue`,
and stopping it sets an Event it checks at its next accepted splice; everything else is the
same code.

Every log line about a job carries its id (`job_id`, a field of the JSON logs, `api/logs.py`)
and the id of the request that submitted it (`request_id`, kept on the job: a queued job is
started from whatever freed its slot, another job's follower, a cancel or the sweep, whose
context is not its own), and each job's end is one INFO line: its status, its stop reason or
error code, its route and how long it ran.
"""

import asyncio
import logging
import multiprocessing
import queue
import secrets
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from pydantic import BaseModel

from worst_route.api import logs, sse
from worst_route.api.limits import client_hash
from worst_route.api.schemas import (
    Done,
    DoneMetrics,
    ErrorBody,
    JobState,
    JobStatus,
    LatLon,
    Progress,
    RouteMetrics,
    Snapped,
    Snapshot,
    SpliceOut,
    StopReason,
    StopSnapped,
)
from worst_route.api.sse import Event
from worst_route.api.store import RouteStore
from worst_route.api.worker import (
    Failed,
    Finished,
    Improved,
    JobSpec,
    Message,
    RouteArrays,
    Spliced,
    Started,
    run_in_process,
    run_job,
)
from worst_route.config import (
    API_COALESCE_S,
    API_JOB_TTL_S,
    API_KILL_GRACE_S,
    API_MAX_JOBS,
    API_MAX_QUEUED,
    API_ORPHAN_S,
    API_READ_GRACE_S,
    API_SIMPLIFY_M,
    API_SNAPSHOT_EVERY,
    API_SWEEP_S,
    REORDER_SEARCH_MAX_S,
    BackendName,
)
from worst_route.engine import ENGINE_VERSION
from worst_route.engine.objectives import OBJECTIVES
from worst_route.engine.objectives.brand import BrandRefused, brand_problem
from worst_route.engine.objectives.collect import count_items
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.request import RequestMasks, RouteRequest, request_masks
from worst_route.engine.route import IntArray, Metrics
from worst_route.engine.seed_route import resolve_stops
from worst_route.engine.target import Goal
from worst_route.engine.turns import count_turns
from worst_route.engine.validator import validate_route
from worst_route.graph.loader import Loaded
from worst_route.graph.spatial import Snap

log = logging.getLogger(__name__)
ENDS = ("done", "error")
HEADER = ("snapped", "baseline")
ROUTE_EVENTS = ("baseline", "snapshot", "progress")


class TooManyJobs(Exception):  # noqa: N818 (the condition, as the 429 names it)
    """Every worker is busy and the queue is full; or the client's own jobs fill its share, its
    counts given."""

    def __init__(self, running: int | None = None, queued: int | None = None) -> None:
        super().__init__()
        self.running = running
        self.queued = queued


@dataclass(frozen=True)
class JobSettings:
    """The manager's limits and cadence (defaults: `config.API_*`)."""

    max_jobs: int = API_MAX_JOBS
    max_queued: int = API_MAX_QUEUED
    inline_workers: bool = False
    coalesce_s: float = API_COALESCE_S
    snapshot_every: int = API_SNAPSHOT_EVERY
    simplify_m: float = API_SIMPLIFY_M
    kill_grace_s: float = API_KILL_GRACE_S
    reorder_search_s: float = REORDER_SEARCH_MAX_S  # a reorder job's search, before `Started`
    orphan_s: float = API_ORPHAN_S
    ttl_s: float = API_JOB_TTL_S
    sweep_s: float = API_SWEEP_S
    read_grace_s: float = API_READ_GRACE_S
    running_per_client: int | None = None  # None: no limit (`limits.Limits`)
    queued_per_client: int | None = None


@dataclass(frozen=True, eq=False)
class Prepared:
    """A request's masks and resolved stops, worked out before its job exists."""

    request: RouteRequest
    masks: RequestMasks
    snaps: tuple[Snap, ...]


def prepare(loaded: Loaded, request: RouteRequest) -> Prepared:
    """The pre-flight: raises `BrandRefused`, `SnapError` or `StopUnreachable` as the worker
    would."""
    masks = request_masks(loaded.spatial, loaded.data, request)
    if request.brand is not None:
        problem = brand_problem(loaded.data, request.brand, masks.active)
        if problem is not None:
            raise BrandRefused(problem)
    snaps = resolve_stops(loaded.data, loaded.spatial, loaded.backend, request, masks)
    return Prepared(request, masks, tuple(snaps))


class _Worker(Protocol):
    def get(self, timeout: float) -> Message: ...
    def alive(self) -> bool: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


class _ProcessWorker:
    """A worker in a spawned process, its messages on a one-way pipe."""

    def __init__(self, spec: JobSpec, name: str) -> None:
        ctx = multiprocessing.get_context("spawn")
        self._reader, writer = ctx.Pipe(duplex=False)
        self._process = ctx.Process(
            target=run_in_process, args=(spec, writer), name=name, daemon=True
        )
        self._process.start()
        writer.close()  # the child's copy is the only one: its end is the pipe's end

    def get(self, timeout: float) -> Message:
        """The next message; `queue.Empty` after `timeout`; `EOFError` or `OSError` once the
        worker has gone, at once even when it died partway through a message."""
        if not self._reader.poll(timeout):
            raise queue.Empty
        message: Message = self._reader.recv()
        return message

    def alive(self) -> bool:
        return self._process.is_alive()

    def stop(self) -> None:
        self._process.terminate()

    def close(self) -> None:
        self._process.join(5)
        self._reader.close()


class _ThreadWorker:
    """A worker in a thread of this process (inline workers, for tests)."""

    def __init__(self, spec: JobSpec, name: str) -> None:
        self._queue: queue.Queue[Message] = queue.Queue()
        self._cancel = threading.Event()
        args = (spec, self._queue, self._cancel)
        self._thread = threading.Thread(target=run_job, args=args, name=name, daemon=True)
        self._thread.start()

    def get(self, timeout: float) -> Message:
        return self._queue.get(timeout=timeout)

    def alive(self) -> bool:
        return self._thread.is_alive()

    def stop(self) -> None:
        self._cancel.set()

    def close(self) -> None:
        pass


@dataclass(eq=False)
class Search:
    """One of a job's searches: its seed, its worker, and its own copy of the route, with the
    best route so far by the job's goal."""

    seed: int
    worker: _Worker | None = None
    route: RouteArrays | None = None
    score: float = 0.0
    best: RouteArrays | None = None  # the best route so far, by the goal: often `route` itself
    best_value: float = 0.0
    best_score: float = 0.0
    iteration: int = 0
    accepted: int = 0
    elapsed_s: float = 0.0
    finished: Finished | None = None  # its validated route, once its worker has sent it
    failure: ErrorBody | None = None  # why it ended without one
    ended: bool = False  # its worker is no longer read, and holds no slot


@dataclass(eq=False)
class Job:
    """One job and everything the manager knows of it. Its route, best route and numbers are
    those of its leading search, the one its clients are shown."""

    id: str
    loaded: Loaded
    request: RouteRequest
    snaps: tuple[Snap, ...]
    masks: RequestMasks | None  # dropped when the job ends
    input: dict[str, Any]  # with `reorder`, its stops put in the route's order once known
    base: RouteArrays | None
    created: datetime
    order: tuple[int, ...]  # each stop's position among those of the request as submitted
    request_id: str | None = None  # the request that submitted it (`logs.request_id`)
    client: str | None = None  # who asked for it (`limits.client_of`), on a public server
    status: JobState = "queued"
    started: datetime | None = None
    finished: datetime | None = None
    started_at: float = 0.0
    finished_at: float = 0.0
    last_id: int = 0
    state_id: int = 0  # the latest event that changed the route
    header: list[Event] = field(default_factory=list)
    end: Event | None = None
    subscribers: set["Subscription"] = field(default_factory=set)
    orphaned_since: float | None = None
    searches: list[Search] = field(default_factory=list)
    leader: int = 0  # the search its clients are shown
    goal: Goal | None = None
    baseline_length_m: float = 0.0
    baseline_time_s: float = 0.0
    baseline_delay_s: float = 0.0
    baseline_edges: IntArray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    pending: list[SpliceOut] = field(default_factory=list)
    progress_count: int = 0
    last_flush: float = 0.0
    route_id: str | None = None
    stopped_because: StopReason | None = None
    error: ErrorBody | None = None
    follower: "asyncio.Task[None] | None" = None
    closing: bool = False  # an end is under way; the workers' queues are no longer read

    @property
    def lead(self) -> Search:
        return self.searches[self.leader]

    @property
    def route(self) -> RouteArrays | None:
        return self.lead.route

    @property
    def best(self) -> RouteArrays | None:
        return self.lead.best

    @property
    def score(self) -> float:
        return self.lead.score

    @property
    def best_score(self) -> float:
        return self.lead.best_score

    @property
    def iteration(self) -> int:
        return self.lead.iteration

    @property
    def accepted(self) -> int:
        return self.lead.accepted

    @property
    def elapsed_s(self) -> float:
        return self.lead.elapsed_s


class Subscription:
    """One SSE client of a job: the events it gets first, then the job's live events."""

    def __init__(self, first: list[Event], close: Callable[["Subscription"], None]) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._close = close
        for event in first:
            self._queue.put_nowait(event)

    def push(self, event: Event) -> None:
        self._queue.put_nowait(event)

    async def events(self) -> AsyncIterator[Event]:
        """The events, until the job's `done` or `error`."""
        while True:
            event = await self._queue.get()
            yield event
            if event.name in ENDS:
                return

    def close(self) -> None:
        """The client has gone."""
        self._close(self)


class JobManager:
    """The jobs of one API process."""

    def __init__(
        self, settings: JobSettings, store: RouteStore, *, data_dir: Path, backend: BackendName
    ) -> None:
        self.settings = settings
        self.store = store
        self.data_dir = data_dir
        self.backend = backend
        self.jobs: dict[str, Job] = {}
        self._running: set[str] = set()
        self._queued: deque[Job] = deque()
        self._sweeper: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the sweep (orphans, expired records)."""
        self._sweeper = asyncio.create_task(self._sweep())

    async def stop(self) -> None:
        """Stop every worker; nothing is stored."""
        if self._sweeper is not None:
            self._sweeper.cancel()
        jobs = [self.jobs[j] for j in self._running]
        workers = [s.worker for job in jobs for s in job.searches if s.worker is not None]
        for job in jobs:
            job.closing = True
            if job.follower is not None:
                job.follower.cancel()
        for worker in workers:
            worker.stop()
        await asyncio.gather(
            *(asyncio.to_thread(worker.close) for worker in workers), return_exceptions=True
        )

    def refusal(self, client: str | None = None) -> TooManyJobs | None:
        """Why a job submitted now by `client` would be refused, if it would."""
        if self._may_start(client) or self._may_queue(client):
            return None
        limit = self.settings.queued_per_client
        if client is not None and limit is not None:
            running, queued = self.client_jobs(client)
            if queued >= limit:
                return TooManyJobs(running, queued)
        return TooManyJobs()

    def client_jobs(self, client: str) -> tuple[int, int]:
        """How many of `client`'s jobs are running, and how many waiting."""
        running = sum(self.jobs[j].client == client for j in self._running)
        return running, sum(j.client == client for j in self._queued)

    def submit(
        self,
        loaded: Loaded,
        prepared: Prepared,
        *,
        input: dict[str, Any],
        base: RouteArrays | None = None,
        client: str | None = None,
    ) -> Job:
        """Make a job for a prepared request (by `client`), emit `snapped`, and start or queue
        it."""
        refused = self.refusal(client)
        if refused is not None:
            raise refused
        job = Job(
            id=secrets.token_hex(6),
            loaded=loaded,
            request=prepared.request,
            snaps=prepared.snaps,
            masks=prepared.masks,
            input=input,
            base=base,
            created=datetime.now(UTC),
            order=tuple(range(len(prepared.request.stops))),
            request_id=logs.request_id.get(),
            client=client,
            searches=[Search(prepared.request.seed)],
        )
        self.jobs[job.id] = job
        if not prepared.request.reorder:  # else once the worker has chosen the order
            self._emit(job, "snapped", self._snapped(job, prepared.masks))
        if self._may_start(client, len(job.searches)):
            self._start(job)
        else:
            self._queued.append(job)
        return job

    def _free(self) -> int:
        """The workers free: `max_jobs` less one for each search of a running job that has not
        ended."""
        busy = sum(not s.ended for j in self._running for s in self.jobs[j].searches)
        return self.settings.max_jobs - busy

    def _client_may_run(self, client: str | None) -> bool:
        """Whether `client` runs fewer jobs than it may."""
        limit = self.settings.running_per_client
        return client is None or limit is None or self.client_jobs(client)[0] < limit

    def _may_start(self, client: str | None, workers: int = 1) -> bool:
        """Whether `workers` are free with no queued job waiting for workers before them, and
        `client` runs fewer jobs than it may."""
        if not self._client_may_run(client):
            return False
        waiting = any(self._client_may_run(j.client) for j in self._queued)
        return not waiting and self._free() >= workers

    def _may_queue(self, client: str | None) -> bool:
        """Whether the queue has room, for `client` too."""
        s = self.settings
        if len(self._queued) >= s.max_queued:
            return False
        limit = s.queued_per_client
        return client is None or limit is None or self.client_jobs(client)[1] < limit

    def subscribe(self, job: Job, last_event_id: int | None = None) -> Subscription:
        """A new SSE client of `job`, which had seen events up to `last_event_id`."""
        self._flush(job)
        first = [e for e in job.header if last_event_id is None or e.id > last_event_id]
        if job.route is not None:
            first.append(Event(job.state_id, "snapshot", self._snapshot(job).model_dump_json()))
        if job.end is not None:
            first.append(job.end)
        subscription = Subscription(first, lambda s: self._unsubscribe(job, s))
        if job.end is None:
            job.subscribers.add(subscription)
            job.orphaned_since = None
        return subscription

    async def cancel(self, job: Job) -> None:
        """End `job` now, storing its route so far when it has one."""
        try:
            await self._end_early(job, "cancelled")
        except Exception as error:
            log.exception("job %s: cancelling it failed", job.id, extra=_fields(job))
            await self._fail_unended(job, error)

    def status(self, job: Job, *, with_snapshot: bool) -> JobStatus:
        """What `GET /jobs/{id}` reports."""
        if job.started_at == 0.0:
            elapsed = 0.0
        else:
            elapsed = (job.finished_at or time.monotonic()) - job.started_at
        return JobStatus(
            job_id=job.id,
            status=job.status,
            region=str(job.loaded.data.meta["region"]),
            build_id=str(job.loaded.data.meta["build_id"]),
            seed=job.request.seed,
            created=job.created,
            started=job.started,
            finished=job.finished,
            last_event_id=job.last_id,
            iteration=job.iteration,
            elapsed_s=elapsed,
            metrics=self._metrics(job) if job.route is not None else None,
            route_id=job.route_id,
            stopped_because=job.stopped_because,
            error=job.error,
            snapshot=self._snapshot(job) if with_snapshot and job.route is not None else None,
        )

    # Running a job ------------------------------------------------------------------------

    def _start(self, job: Job) -> None:
        kind = _ThreadWorker if self.settings.inline_workers else _ProcessWorker
        for search in job.searches:
            spec = JobSpec(
                data_dir=self.data_dir,
                region=str(job.loaded.data.meta["region"]),
                build_id=str(job.loaded.data.meta["build_id"]),
                backend=self.backend,
                request=job.request.model_copy(update={"seed": search.seed}),
                base=job.base,
            )
            try:
                search.worker = kind(spec, f"worst-route job {job.id}")
            except Exception as error:
                log.exception("job %s: its worker did not start", job.id, extra=_fields(job))
                message = f"the worker did not start: {type(error).__name__}: {error}"
                search.failure = ErrorBody(code="WORKER_FAILED", message=message)
                search.ended = True
        failure = job.searches[0].failure
        if failure is not None and all(s.ended for s in job.searches):
            job.error = failure
            self._emit(job, "error", failure)
            self._record_end(job, "error")
            return
        job.status = "running"
        job.started = datetime.now(UTC)
        job.started_at = time.monotonic()
        self._running.add(job.id)
        job.follower = asyncio.create_task(self._follow(job))

    async def _follow(self, job: Job) -> None:
        """Read the workers' messages until the job ends."""
        s = self.settings
        deadline = job.started_at + job.request.budget_s + s.kill_grace_s
        if job.request.reorder:  # its search for the order runs before its loop
            deadline += s.reorder_search_s
        try:
            while not job.closing:
                live = [(x, x.worker) for x in job.searches if not x.ended and x.worker is not None]
                alive = [worker.alive() for _, worker in live]
                wait = s.read_grace_s
                if job.pending:
                    due = job.last_flush + s.coalesce_s - time.monotonic()
                    wait = min(wait, max(0.0, due))
                drained = await asyncio.gather(
                    *(asyncio.to_thread(_drain, worker, wait) for _, worker in live)
                )
                if job.closing:
                    return
                for (search, _), (messages, _) in zip(live, drained, strict=True):
                    for message in messages:
                        if isinstance(message, Started):
                            if job.request.reorder:
                                await self._reordered(job, message)
                                if job.closing:
                                    return
                                # the search has ended: the budget and the grace run from here
                                deadline = time.monotonic() + job.request.budget_s + s.kill_grace_s
                            self._started(job, search, message)
                        elif isinstance(message, Spliced):
                            self._spliced(job, search, message)
                        elif isinstance(message, Improved):
                            self._improved(job, search, message)
                        else:
                            self._flush(job)
                            if isinstance(message, Finished):
                                await self._finished(job, search, message)
                            else:
                                await self._failed(job, search, message)
                            return
                if job.pending and time.monotonic() - job.last_flush >= s.coalesce_s:
                    self._flush(job)
                if time.monotonic() >= deadline:
                    await self._end_early(job, "timeout")
                    continue
                for (search, _), was_alive, (messages, closed) in zip(
                    live, alive, drained, strict=True
                ):
                    if closed or (not messages and not was_alive):
                        lost = Failed("WORKER_FAILED", "the worker stopped without a result")
                        await self._failed(job, search, lost)
        except Exception as error:
            log.exception("job %s: following its worker failed", job.id, extra=_fields(job))
            await self._fail_unended(job, error)

    async def _reordered(self, job: Job, m: Started) -> None:
        """A reorder job's search has chosen the order: plan as the worker does (the request,
        its masks and its stops in that order, and the input's stops), and emit `snapped`."""
        stops = job.request.stops
        request = job.request.model_copy(update={"stops": [stops[k] for k in m.order]})
        loaded = job.loaded
        masks = await asyncio.to_thread(request_masks, loaded.spatial, loaded.data, request)
        if job.closing:  # cancelled meanwhile
            return
        given = job.input.get("stops") or []
        job.input = {**job.input, "stops": [given[k] for k in m.order]}
        job.request, job.masks, job.snaps, job.order = request, masks, m.snaps, m.order
        self._emit(job, "snapped", self._snapped(job, masks))

    def _started(self, job: Job, search: Search, m: Started) -> None:
        search.route, search.score = m.route, m.score
        job.baseline_length_m, job.baseline_time_s = m.baseline_length_m, m.baseline_time_s
        job.baseline_edges, job.baseline_delay_s = m.baseline_edges, m.baseline_delay_s
        data = job.loaded.data
        baseline = Metrics(m.baseline_length_m, m.baseline_time_s, delay_s=m.baseline_delay_s)
        # no objective to make for it; the alphabet route's ratio is its length's, as distance's
        primary = OBJECTIVES.get(job.request.objective, Distance).primary
        job.goal = Goal(job.request.target, data, primary=primary, baseline=baseline)
        search.best, search.best_value = m.route, job.goal.value(m.score, m.route.metrics)
        search.best_score = m.score
        self._emit(
            job,
            "baseline",
            sse.baseline_out(
                data, job.loaded.geometry, m.baseline_length_m, m.baseline_time_s, m.baseline_edges
            ),
        )
        self._emit(job, "snapshot", self._snapshot(job))
        job.last_flush = time.monotonic()

    def _spliced(self, job: Job, search: Search, m: Spliced) -> None:
        route, goal = search.route, job.goal
        if route is None or goal is None:
            raise RuntimeError("a splice before the start")
        nodes = np.concatenate((route.nodes[: m.i], m.nodes, route.nodes[m.j + 1 :]))
        edges = np.concatenate((route.edges[: m.i], m.edges, route.edges[m.j :]))
        shift = len(m.nodes) - (m.j - m.i + 1)
        stop_idx = tuple(k if k <= m.i else k + shift for k in route.stop_idx)
        route = RouteArrays(nodes.astype(np.int32), edges.astype(np.int32), stop_idx, m.metrics)
        search.route, search.score = route, m.score
        search.iteration, search.elapsed_s = m.iteration, m.elapsed_s
        value = goal.value(m.score, m.metrics)
        if goal.better(value, search.best_value):
            search.best, search.best_value, search.best_score = route, value, m.score
        search.accepted += 1
        job.pending.append(
            sse.splice_out(job.loaded.data, job.loaded.geometry, m.i, m.j, m.nodes, m.edges)
        )

    def _improved(self, job: Job, search: Search, m: Improved) -> None:
        """A better alphabet route: the search's route and its best, sent as a `snapshot` and
        then a `progress` with its numbers and no splice."""
        goal = job.goal
        if search.route is None or goal is None:
            raise RuntimeError("a better route before the start")
        search.route = search.best = m.route
        search.score, search.iteration, search.elapsed_s = m.score, m.iteration, m.elapsed_s
        search.best_value, search.best_score = goal.value(m.score, m.route.metrics), m.score
        search.accepted += 1
        self._emit(job, "snapshot", self._snapshot(job))
        self._emit(job, "progress", self._progress(job, []))
        job.last_flush = time.monotonic()

    def _flush(self, job: Job) -> None:
        """Send the splices not sent yet, and a snapshot every `snapshot_every` sends."""
        if not job.pending:
            return
        progress = self._progress(job, job.pending)
        job.pending = []
        job.last_flush = time.monotonic()
        self._emit(job, "progress", progress)
        job.progress_count += 1
        if job.progress_count % self.settings.snapshot_every == 0:
            self._emit(job, "snapshot", self._snapshot(job))

    async def _finished(self, job: Job, search: Search, m: Finished) -> None:
        job.closing = True
        rid = await asyncio.to_thread(
            self.store.put, m.document, input=job.input, baseline_edges=m.baseline_edges
        )
        doc = m.document
        metrics = Metrics(float(doc["metrics"]["length_m"]), float(doc["metrics"]["time_s"]))
        nodes, edges = (np.array(doc[k], dtype=np.int32) for k in ("nodes", "edges"))
        stored = RouteArrays(nodes, edges, tuple(doc["stop_idx"]), metrics)
        if not _same_route(job.route, stored):
            self._show(job, search, stored)
        await self._done(job, rid, doc["run"]["stopped_because"], _done_metrics(doc))

    async def _failed(self, job: Job, search: Search, m: Failed) -> None:
        job.closing = True
        if m.trace:
            log.error("job %s failed: %s\n%s", job.id, m.message, m.trace, extra=_fields(job))
        await self._error(job, ErrorBody(code=m.code, message=m.message, **m.fields))

    async def _end_early(self, job: Job, reason: StopReason) -> None:
        """Cancel or time out `job`: store its route so far if it has one."""
        if job.end is not None or job.closing:
            return
        job.closing = True
        if job.status == "queued":
            self._queued.remove(job)
            message = "cancelled before it started"
            await self._error(job, ErrorBody(code="CANCELLED", message=message))
            return
        self._stop_workers(job)
        self._flush(job)
        search = job.lead
        route, masks = search.best, job.masks
        if route is None or masks is None:
            if reason == "timeout":
                body = ErrorBody(code="WORKER_TIMEOUT", message="no route before the time limit")
            else:
                body = ErrorBody(code="CANCELLED", message="cancelled before its route existed")
            await self._error(job, body)
            return
        report = await asyncio.to_thread(
            validate_route,
            job.loaded.data,
            route,
            stop_nodes=[s.node for s in job.snaps],
            node_mask=masks.active,
            banned_edges=masks.banned,
            banned_turn_classes=job.request.constraints.banned_turn_classes,
            hard=job.request.constraints.hard,
            alphabet=job.request.objective == "alphabet",
        )
        if not report.ok:
            failed = "; ".join(line for line in report.lines() if "FAILED" in line)
            log.error(
                "job %s: its route so far failed validation: %s", job.id, failed, extra=_fields(job)
            )
            message = f"the route so far failed validation: {failed}"
            await self._error(job, ErrorBody(code="VALIDATION_FAILED", message=message))
            return
        document = self._document(job, search, route, reason)
        rid = await asyncio.to_thread(
            self.store.put, document, input=job.input, baseline_edges=job.baseline_edges
        )
        if not _same_route(job.route, route):
            self._show(job, search, route)
        await self._done(job, rid, reason, _done_metrics(document))

    def _show(self, job: Job, search: Search, route: RouteArrays) -> None:
        """Make `route`, the one the job stores, the route of `search`, which leads, and send it
        as a snapshot."""
        self._flush(job)
        job.leader = job.searches.index(search)
        search.route = route
        self._emit(job, "snapshot", self._snapshot(job))

    def _stop_workers(self, job: Job) -> None:
        for search in job.searches:
            if search.worker is not None:
                search.worker.stop()

    async def _fail_unended(self, job: Job, error: Exception) -> None:
        """End a job an unexpected error left without its `done` or `error`, so no stream hangs."""
        if job.end is None:
            self._stop_workers(job)
            body = ErrorBody(code="WORKER_FAILED", message=f"{type(error).__name__}: {error}")
            await self._error(job, body)

    async def _done(self, job: Job, rid: str, reason: StopReason, metrics: DoneMetrics) -> None:
        self._flush(job)  # no splice is ever sent after the end
        job.route_id, job.stopped_because = rid, reason
        self._emit(job, "done", Done(route_id=rid, stopped_because=reason, metrics=metrics))
        await self._end(job, "done")

    async def _error(self, job: Job, body: ErrorBody) -> None:
        self._flush(job)  # no splice is ever sent after the end
        job.error = body
        self._emit(job, "error", body)
        await self._end(job, "error")

    async def _end(self, job: Job, status: JobState) -> None:
        self._record_end(job, status)
        self._advance()
        workers = [s.worker for s in job.searches if s.worker is not None]
        await asyncio.gather(*(asyncio.to_thread(worker.close) for worker in workers))

    def _advance(self) -> None:
        """Start the queued jobs that may, in order: one whose client runs all the jobs it may is
        passed over, and one waiting for workers holds up those after it."""
        for waiting in list(self._queued):
            if not self._client_may_run(waiting.client):
                continue
            if self._free() < len(waiting.searches):
                break
            self._queued.remove(waiting)
            self._start(waiting)  # a start that fails ends that job alone

    def _record_end(self, job: Job, status: JobState) -> None:
        job.status = status
        job.finished = datetime.now(UTC)
        job.finished_at = time.monotonic()
        job.masks = None
        self._running.discard(job.id)
        elapsed_s = round(job.finished_at - job.started_at, 1) if job.started_at else 0.0
        outcome = job.error.code if job.error is not None else job.stopped_because
        fields = {"status": status, "outcome": outcome, "route_id": job.route_id}
        extra = {**_fields(job), **fields, "elapsed_s": elapsed_s}
        log.info("job %s: %s (%s) after %.1f s", job.id, status, outcome, elapsed_s, extra=extra)

    # Events -------------------------------------------------------------------------------

    def _emit(self, job: Job, name: str, payload: BaseModel) -> None:
        job.last_id += 1
        event = Event(job.last_id, name, payload.model_dump_json())
        if name in HEADER:
            job.header.append(event)
        if name in ROUTE_EVENTS:
            job.state_id = event.id
        for subscription in job.subscribers:
            subscription.push(event)
        if name in ENDS:
            job.end = event
            job.subscribers.clear()

    def _unsubscribe(self, job: Job, subscription: Subscription) -> None:
        job.subscribers.discard(subscription)
        if not job.subscribers and job.end is None:
            job.orphaned_since = time.monotonic()

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(self.settings.sweep_s)
            for job in list(self.jobs.values()):
                try:
                    await self._sweep_job(job)
                except Exception:  # one job's trouble never stops the sweep
                    log.exception("job %s: the sweep failed on it", job.id, extra=_fields(job))

    async def _sweep_job(self, job: Job) -> None:
        s, now = self.settings, time.monotonic()
        if job.end is not None:
            if now - job.finished_at >= s.ttl_s:
                self.jobs.pop(job.id, None)
        elif job.orphaned_since is not None and now - job.orphaned_since >= s.orphan_s:
            log.info(
                "job %s: no subscriber for %.0f s, cancelled",
                job.id,
                s.orphan_s,
                extra=_fields(job),
            )
            await self.cancel(job)  # an error while cancelling ends it as WORKER_FAILED

    # Payloads -----------------------------------------------------------------------------

    def _snapped(self, job: Job, masks: RequestMasks) -> Snapped:
        labels = [s.get("label") for s in job.input.get("stops") or []]
        labels += [None] * (len(job.snaps) - len(labels))
        stops = [
            StopSnapped(
                input=LatLon(lat=lat, lon=lon),
                label=label,
                node=s.node,
                lat=s.lat,
                lon=s.lon,
                moved_m=s.moved_m,
                note=s.note,
            )
            for (lat, lon), label, s in zip(job.request.stops, labels, job.snaps, strict=True)
        ]
        return Snapped(
            stops=stops,
            order=list(job.order),
            tier=masks.tier,
            leash_km=masks.leash_m / 1000,
            active_nodes=int(masks.active.sum()),
        )

    def _metrics(self, job: Job) -> RouteMetrics:
        metrics = job.route.metrics if job.route is not None else Metrics(0.0, 0.0)
        return sse.route_metrics(metrics, job.baseline_length_m)

    def _progress(self, job: Job, splices: list[SpliceOut]) -> Progress:
        """A `progress` event with these splices, the route's numbers and the best route's."""
        best = job.best.metrics if job.best is not None else Metrics(0.0, 0.0)
        return Progress(
            iteration=job.iteration,
            elapsed_s=job.elapsed_s,
            score=job.score,
            metrics=self._metrics(job),
            splices=splices,
            best_score=job.best_score,
            best_metrics=sse.route_metrics(best, job.baseline_length_m),
        )

    def _snapshot(self, job: Job) -> Snapshot:
        route = job.route
        if route is None:
            raise RuntimeError("no route yet")
        return sse.snapshot(
            job.loaded.data,
            job.loaded.geometry,
            nodes=route.nodes,
            edges=route.edges,
            stop_idx=route.stop_idx,
            iteration=job.iteration,
            elapsed_s=job.elapsed_s,
            metrics=self._metrics(job),
            simplify_m=self.settings.simplify_m,
        )

    def _document(
        self, job: Job, search: Search, route: RouteArrays, reason: StopReason
    ) -> dict[str, Any]:
        """The `route.json` of the manager's best route of `search` (`engine/io.py`'s
        layout)."""
        meta = job.loaded.data.meta
        ratio = sse.route_metrics(route.metrics, job.baseline_length_m).ratio
        target = job.goal.describe(search.best_value) if job.goal is not None else None
        request = job.request.model_copy(update={"seed": search.seed})
        return {
            "graph_version": meta["graph_version"],
            "region": meta["region"],
            "build_id": meta["build_id"],
            "seed": search.seed,
            "engine_version": ENGINE_VERSION,
            "request": request.model_dump(mode="json"),
            "stops": [
                {"lat": s.lat, "lon": s.lon, "node": s.node, "moved_m": s.moved_m, "note": s.note}
                for s in job.snaps
            ],
            "order": list(job.order),
            "nodes": route.nodes.tolist(),
            "edges": route.edges.tolist(),
            "stop_idx": list(route.stop_idx),
            "metrics": {
                "length_m": route.metrics.length_m,
                "time_s": route.metrics.time_s,
                "delay_s": route.metrics.delay_s,
                "baseline_length_m": job.baseline_length_m,
                "baseline_time_s": job.baseline_time_s,
                "baseline_delay_s": job.baseline_delay_s,
                "ratio": ratio,
                "turns": count_turns(job.loaded.data, route.edges),
                "items": count_items(job.loaded.data, route.nodes, route.edges),
                **route.metrics.counters,
                **({"target": target} if target is not None else {}),
            },
            "run": {
                "attempts": search.iteration,
                "accepted": search.accepted,
                "stopped_because": reason,
                "elapsed_s": search.elapsed_s,
                "backend": job.loaded.backend.name,
            },
        }


def _fields(job: Job) -> dict[str, Any]:
    """What a job's log lines carry besides their message (`api/logs.py`): its id, the id of
    the request that submitted it, whichever task logs them, and its client's hash when it has
    one."""
    fields: dict[str, Any] = {"job_id": job.id}
    if job.request_id is not None:
        fields["request_id"] = job.request_id
    if job.client is not None:
        fields["client"] = client_hash(job.client)
    return fields


def _done_metrics(document: dict[str, Any]) -> DoneMetrics:
    """`done`'s metrics: a stored route's, but the baseline's (with its target, if any), and its
    experienced time's ratio to the baseline's."""
    m = document["metrics"]
    ratio = sse.time_ratio(m["time_s"], m["delay_s"], m["baseline_time_s"], m["baseline_delay_s"])
    kept = {k: v for k, v in m.items() if not k.startswith("baseline_")}
    return DoneMetrics(**kept, time_ratio=ratio)


def _same_route(drawn: RouteArrays | None, stored: RouteArrays) -> bool:
    """Whether the route clients have drawn is the stored one: the same edges (two routes
    through the same nodes may differ by a parallel edge)."""
    return drawn is not None and drawn.edges.tobytes() == stored.edges.tobytes()


def _drain(worker: _Worker, wait: float) -> tuple[list[Message], bool]:
    """The worker's messages, waiting up to `wait` for the first, then whatever is there; and
    whether its pipe has closed (the worker has gone)."""
    messages: list[Message] = []
    try:
        messages.append(worker.get(wait))
        while len(messages) < 1000:
            messages.append(worker.get(0.0))
    except queue.Empty:
        return messages, False
    except (EOFError, OSError):
        return messages, True
    return messages, False
