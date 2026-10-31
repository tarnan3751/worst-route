"""Builds and helpers shared by the API tests: lattice builds written into a data directory, the
job manager with inline workers, submitting a job, reading its events, waiting on a condition."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from worst_route.api import worker as worker_module
from worst_route.api.jobs import Job, JobManager, JobSettings, Subscription, prepare
from worst_route.api.sse import Event
from worst_route.api.store import RouteStore
from worst_route.api.worker import RouteArrays
from worst_route.engine.request import RouteRequest
from worst_route.graph.data import GraphData
from worst_route.graph.loader import Loaded, load_build
from worst_route.graph.store import write_build

from ..engine.graphs import at_node, lattice

GRID = lattice(10, 10)
BIG = lattice(20, 20)
SLOW = lattice(10, 10, primary_every=3)  # GRID's nodes, with delays
# The grid build's region: 0.1° around its nodes, so a stop can be inside it and still more
# than the 5 km snapping reaches from every node.
GRID_BBOX = [
    round(float(GRID.node_lon.min()) - 0.1, 7),
    round(float(GRID.node_lat.min()) - 0.1, 7),
    round(float(GRID.node_lon.max()) + 0.1, 7),
    round(float(GRID.node_lat.max()) + 0.1, 7),
]


def save_build(
    data_dir: Path, g: GraphData, build_id: str, latest: bool = True, **params: Any
) -> None:
    meta = {**g.meta, "region": "portland", "build_id": build_id, "params": params}
    g = replace(g, meta=meta)
    write_build(g, data_dir / "graphs" / "portland" / build_id)
    if latest:
        (data_dir / "graphs" / "portland" / "latest").write_text(f"{build_id}\n")


def request(g: GraphData, stops: list[int], seed: int = 3, **fields: Any) -> RouteRequest:
    values: dict[str, Any] = {"budget_s": 120, "max_iterations": 300, **fields}
    return RouteRequest(stops=[at_node(g, v) for v in stops], seed=seed, **values)


def api_input(req: RouteRequest) -> dict[str, Any]:
    """The API request a test job stands for."""
    stops = [
        {"lat": lat, "lon": lon, "label": f"stop {k}"} for k, (lat, lon) in enumerate(req.stops)
    ]
    return {"region": "portland", "stops": stops, "seed": req.seed}


@asynccontextmanager
async def manager(data: Path, **settings: Any) -> AsyncIterator[JobManager]:
    """A started manager on `data` with inline workers and no coalescing, unless told."""
    values: dict[str, Any] = {"inline_workers": True, "coalesce_s": 0.0, **settings}
    m = JobManager(
        JobSettings(**values), RouteStore(data / "routes.sqlite"), data_dir=data, backend="python"
    )
    await m.start()
    try:
        yield m
    finally:
        await m.stop()


def loaded(data: Path, build_id: str = "grid") -> Loaded:
    return load_build(data, "portland", build_id, backend="python")


def submit(m: JobManager, build: Loaded, req: RouteRequest, base: RouteArrays | None = None) -> Job:
    return m.submit(build, prepare(build, req), input=api_input(req), base=base)


async def collect(subscription: Subscription, timeout: float = 60.0) -> list[Event]:
    """Every event until the stream ends."""
    events: list[Event] = []

    async def read() -> None:
        async for event in subscription.events():
            events.append(event)

    await asyncio.wait_for(read(), timeout)
    return events


async def until(condition: Callable[[], bool], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")
        await asyncio.sleep(0.01)


def slow_splices(monkeypatch: pytest.MonkeyPatch, delay_s: float = 0.01) -> None:
    """Make an inline worker take `delay_s` per accepted splice, so a job is still running when
    a test acts on it (a lattice run converges in well under a second)."""
    real = worker_module.plan_route

    def slow(*args: Any, on_progress: Any = None, **kwargs: Any) -> Any:
        def progress(event: Any, baseline: Any) -> None:
            time.sleep(delay_s)
            on_progress(event, baseline)

        return real(*args, on_progress=progress, **kwargs)

    monkeypatch.setattr(worker_module, "plan_route", slow)
