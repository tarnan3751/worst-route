"""The job manager with inline workers: events, resync, cancellation, limits, continuations."""

import json
import queue
import time
from pathlib import Path
from typing import Any

import pytest

from worst_route.api import jobs as jobs_module
from worst_route.api import worker as worker_module
from worst_route.api.jobs import JobManager, TooManyJobs, prepare
from worst_route.api.sse import Event, path_points
from worst_route.api.worker import JobSpec, RouteArrays
from worst_route.engine import plan as plan_module
from worst_route.engine.plan import plan_route
from worst_route.engine.request import request_masks
from worst_route.engine.target import Target
from worst_route.engine.validator import validate_route

from ..engine.graphs import lattice, uneven_lattice
from .support import (
    BIG,
    GRID,
    collect,
    loaded,
    manager,
    request,
    save_build,
    slow_splices,
    submit,
    until,
)

pytestmark = pytest.mark.anyio


class Client:
    """What a frontend keeps: the route's points and each node's index among them."""

    def __init__(self) -> None:
        self.coords: list[list[float]] = []
        self.index: list[int] = []

    def apply(self, event: Event) -> None:
        payload = json.loads(event.data)
        if event.name == "snapshot":
            self.coords = payload["geometry"]["coordinates"]
            self.index = payload["node_index"]
        elif event.name == "progress":
            for s in payload["splices"]:
                a, b = self.index[s["i"]], self.index[s["j"]]
                shift = len(s["coords"]) - (b - a + 1)
                self.coords = self.coords[:a] + s["coords"] + self.coords[b + 1 :]
                self.index = (
                    self.index[: s["i"]]
                    + [a + k for k in s["node_index"]]
                    + [k + shift for k in self.index[s["j"] + 1 :]]
                )


def names(events: list[Event]) -> list[str]:
    return [e.name for e in events]


def validated(m: JobManager, data: Path, route_id: str, build: str = "grid") -> Any:
    saved = m.store.get(route_id)
    assert saved is not None
    g = loaded(data, build)
    masks = request_masks(g.spatial, g.data, saved.request)
    report = validate_route(
        g.data,
        saved,
        stop_nodes=[s.node for s in saved.stops],
        node_mask=masks.active,
        banned_edges=masks.banned,
    )
    assert report.ok, report.lines()
    return saved


async def test_events_come_in_order_and_replay_to_the_stored_route(data: Path) -> None:
    async with manager(data, snapshot_every=3) as m:
        build = loaded(data)
        job = submit(m, build, request(GRID, [0, 45, 99], max_iterations=400))
        events = await collect(m.subscribe(job))
        assert names(events)[:3] == ["snapped", "baseline", "snapshot"]
        assert names(events)[-1] == "done" and "progress" in names(events)
        ids = [e.id for e in events]
        assert ids == sorted(ids) and len(set(ids)) == len(ids) and ids[0] == 1
        later = names(events)[3:-1]
        for k, name in enumerate(later):
            if name == "snapshot":
                assert later[k - 1] == "progress" and later[:k].count("progress") % 3 == 0
        snapped = json.loads(events[0].data)
        assert [s["node"] for s in snapped["stops"]] == [0, 45, 99]
        assert [s["label"] for s in snapped["stops"]] == ["stop 0", "stop 1", "stop 2"]
        assert snapped["tier"] == "T4" and snapped["active_nodes"] == 100
        assert json.loads(events[2].data)["iteration"] == 0

        client = Client()
        for event in events:
            client.apply(event)
        done = json.loads(events[-1].data)
        assert done["stopped_because"] == "budget"
        saved = validated(m, data, done["route_id"])
        coords, node_index = path_points(build.data, build.geometry, saved.edges)
        assert client.coords == coords and client.index == node_index
        assert job.route is not None and job.route.nodes.tobytes() == saved.nodes.tobytes()
        assert done["metrics"]["length_m"] == saved.metrics.length_m
        assert saved.run["attempts"] == 400 and saved.run["accepted"] == job.accepted
        assert saved.input["stops"][1]["label"] == "stop 1"
        mirrored = m._document(job, job.route, "budget")
        stored = m.store.get(done["route_id"])
        assert stored is not None and mirrored["metrics"] == stored.metrics_json
        assert m.status(job, with_snapshot=False).status == "done"


async def test_a_subscriber_after_the_end_gets_the_header_a_snapshot_and_the_end(
    data: Path,
) -> None:
    async with manager(data) as m:
        job = submit(m, loaded(data), request(GRID, [0, 99]))
        await until(lambda: job.end is not None)
        events = await collect(m.subscribe(job))
        assert names(events) == ["snapped", "baseline", "snapshot", "done"]
        assert events[1].id < events[2].id < events[3].id == job.last_id  # the route's last change
        again = await collect(m.subscribe(job, last_event_id=job.last_id))
        assert names(again) == ["snapshot", "done"]


async def test_a_reconnect_resyncs_with_a_snapshot_then_continues(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data, coalesce_s=0.05) as m:
        build = loaded(data, "big")
        job = submit(m, build, request(BIG, [0, 399], max_iterations=4000))
        first = m.subscribe(job)
        await until(lambda: job.accepted >= 5)
        first.close()
        seen = job.last_id
        await until(lambda: job.last_id > seen + 2 or job.end is not None)
        events = await collect(m.subscribe(job, last_event_id=seen))
        assert events[0].name == "snapshot" and events[0].id >= seen
        assert all(e.name not in ("snapped", "baseline") for e in events)
        ids = [e.id for e in events]
        assert ids == sorted(ids) and events[-1].name == "done"
        assert all(e.id > events[0].id for e in events[1:-1])
        client = Client()
        for event in events:
            client.apply(event)
        saved = validated(m, data, json.loads(events[-1].data)["route_id"], "big")
        assert (client.coords, client.index) == path_points(build.data, build.geometry, saved.edges)


async def test_cancel_stores_the_route_so_far_which_its_attempts_reproduce(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data) as m:
        build = loaded(data, "big")
        req = request(BIG, [0, 210, 399], seed=4, max_iterations=1_000_000)
        job = submit(m, build, req)
        subscription = m.subscribe(job)
        await until(lambda: job.accepted >= 20)
        await m.cancel(job)
        assert job.status == "done" and job.stopped_because == "cancelled"
        events = await collect(subscription)
        done = json.loads(events[-1].data)
        assert events[-1].name == "done" and done["stopped_because"] == "cancelled"
        saved = validated(m, data, done["route_id"], "big")
        assert job.route is not None and saved.nodes.tobytes() == job.route.nodes.tobytes()
        assert saved.run["stopped_because"] == "cancelled"
        assert saved.run["attempts"] == job.iteration and saved.run["accepted"] == job.accepted
        again = req.model_copy(update={"max_iterations": saved.run["attempts"]})
        replayed = plan_route(build.data, again, backend=build.backend, spatial=build.spatial)
        assert replayed.route.nodes.tobytes() == saved.nodes.tobytes()
        await m.cancel(job)  # a second cancel changes nothing
        assert job.stopped_because == "cancelled"


# Every corner-to-corner path of a 5 × 5 town is 800 m + a multiple of 200 m, and a 2.1 ×
# target (1,646-1,714 m) holds none: a run grows to 1,600 m, overshoots to 1,800 m after a cycle
# with nothing smaller, cannot shrink back into the band, and keeps 1,600 m as its best.
TOWN = lattice(5, 5)
OVERSHOOT = Target(kind="ratio", value=2.1)


async def test_a_best_route_the_client_did_not_draw_last_comes_as_a_snapshot(data: Path) -> None:
    save_build(data, TOWN, "town", latest=False)
    async with manager(data) as m:
        build = loaded(data, "town")
        job = submit(m, build, request(TOWN, [0, 24], max_iterations=10**6, target=OVERSHOOT))
        events = await collect(m.subscribe(job))
        done = json.loads(events[-1].data)
        assert done["stopped_because"] == "converged"
        assert names(events)[-3:] == ["progress", "snapshot", "done"]
        assert json.loads(events[-3].data)["metrics"]["length_m"] == 1800.0
        assert json.loads(events[-2].data)["metrics"]["length_m"] == 1600.0
        assert done["metrics"]["length_m"] == 1600.0
        assert done["metrics"]["target"]["achieved"] == 2.0
        client = Client()
        for event in events:
            client.apply(event)
        saved = validated(m, data, done["route_id"], "town")
        assert (client.coords, client.index) == path_points(build.data, build.geometry, saved.edges)
        assert job.route is not None and job.route.nodes.tobytes() == saved.nodes.tobytes()
        late = await collect(m.subscribe(job))  # a late subscriber draws the stored route too
        assert names(late)[-2:] == ["snapshot", "done"]
        assert json.loads(late[-2].data)["metrics"]["length_m"] == 1600.0


async def test_cancel_stores_the_closest_route_so_far(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_build(data, TOWN, "town", latest=False)
    real = worker_module.plan_route

    def held(*args: Any, on_progress: Any = None, **kwargs: Any) -> Any:
        def progress(event: Any, baseline: Any) -> None:
            on_progress(event, baseline)
            if event.metrics.length_m == 1800.0:
                time.sleep(3)  # hold the job just after its overshoot, for the cancel

        return real(*args, on_progress=progress, **kwargs)

    monkeypatch.setattr(worker_module, "plan_route", held)
    async with manager(data) as m:
        build = loaded(data, "town")
        req = request(TOWN, [0, 24], max_iterations=10**6, target=OVERSHOOT)
        job = submit(m, build, req)
        subscription = m.subscribe(job)
        await until(lambda: job.route is not None and job.route.metrics.length_m == 1800.0)
        await m.cancel(job)
        events = await collect(subscription)
        assert names(events)[-2:] == ["snapshot", "done"]
        done = json.loads(events[-1].data)
        assert done["stopped_because"] == "cancelled" and done["metrics"]["length_m"] == 1600.0
        assert done["metrics"]["target"]["within_tolerance"] is False
        saved = validated(m, data, done["route_id"], "town")
        assert saved.metrics.length_m == 1600.0 and saved.metrics_json["target"]["achieved"] == 2.0
        # its attempts end at the overshoot, and planning that many returns the same best
        again = req.model_copy(update={"max_iterations": saved.run["attempts"]})
        replayed = real(build.data, again, backend=build.backend, spatial=build.spatial)
        assert replayed.run.events[-1].metrics.length_m == 1800.0
        assert replayed.route.nodes.tobytes() == saved.nodes.tobytes()


async def test_jobs_wait_in_the_queue_and_too_many_are_refused(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data, max_jobs=1, max_queued=1) as m:
        build = loaded(data, "big")
        long = request(BIG, [0, 399], max_iterations=1_000_000)
        running = submit(m, build, long)
        waiting = submit(m, build, request(BIG, [0, 399], seed=5))
        assert (running.status, waiting.status) == ("running", "queued")
        assert m.refusal() is not None
        with pytest.raises(TooManyJobs):
            submit(m, build, request(BIG, [0, 399], seed=6))
        await m.cancel(waiting)
        assert waiting.status == "error" and waiting.error is not None
        assert waiting.error.code == "CANCELLED"
        third = submit(m, build, request(BIG, [0, 399], seed=7))
        assert third.status == "queued"
        await m.cancel(running)
        await until(lambda: third.status != "queued")
        await until(lambda: third.end is not None)
        assert third.status == "done"


async def test_a_job_cancelled_before_its_route_exists_is_an_error(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = worker_module.load_build

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(worker_module, "load_build", slow)
    async with manager(data) as m:
        job = submit(m, loaded(data), request(GRID, [0, 99]))
        subscription = m.subscribe(job)
        await m.cancel(job)
        assert job.status == "error" and job.route_id is None
        events = await collect(subscription)
        assert names(events) == ["snapped", "error"]
        assert json.loads(events[-1].data)["code"] == "CANCELLED"


async def test_a_continuation_starts_from_its_base_route(data: Path) -> None:
    async with manager(data) as m:
        build = loaded(data)
        first = submit(m, build, request(GRID, [0, 45, 99], max_iterations=50))
        await until(lambda: first.end is not None)
        assert first.route_id is not None
        base = m.store.get(first.route_id)
        assert base is not None
        arrays = RouteArrays(base.nodes, base.edges, base.stop_idx, base.metrics)
        again = request(GRID, [0, 45, 99], seed=11, max_iterations=400)
        job = submit(m, build, again, base=arrays)
        events = await collect(m.subscribe(job))
        seed = json.loads(events[2].data)
        assert events[2].name == "snapshot" and seed["iteration"] == 0
        assert seed["node_count"] == len(base.nodes)
        done = json.loads(events[-1].data)
        saved = validated(m, data, done["route_id"])
        assert saved.metrics.length_m >= base.metrics.length_m and saved.seed == 11


async def test_the_time_limit_stores_the_route_as_timed_out(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data, kill_grace_s=-119.6) as m:  # killed 0.4 s into its 120 s
        build = loaded(data, "big")
        job = submit(m, build, request(BIG, [0, 399], max_iterations=1_000_000))
        await until(lambda: job.end is not None)
        assert job.status == "done" and job.stopped_because == "timeout"
        assert job.route_id is not None
        saved = validated(m, data, job.route_id, "big")
        assert saved.run["stopped_because"] == "timeout"


async def test_a_job_whose_subscribers_left_is_cancelled_and_a_queued_one_dropped(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data, max_jobs=1, orphan_s=0.2, sweep_s=0.05) as m:
        build = loaded(data, "big")
        long = request(BIG, [0, 399], max_iterations=1_000_000)
        watched = submit(m, build, long)
        queued = submit(m, build, request(BIG, [0, 399], seed=5))
        m.subscribe(queued).close()
        await until(lambda: queued.end is not None, timeout=5)
        assert queued.error is not None and queued.error.code == "CANCELLED"
        assert watched.status == "running"  # never subscribed: it is not an orphan
        subscription = m.subscribe(watched)
        await until(lambda: watched.accepted >= 3)
        subscription.close()
        await until(lambda: watched.end is not None, timeout=5)
        assert watched.stopped_because == "cancelled" and watched.route_id is not None


async def test_a_worker_that_raises_ends_its_job_in_an_error(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_module, "plan_route", broken)
    async with manager(data) as m:
        job = submit(m, loaded(data), request(GRID, [0, 99]))
        events = await collect(m.subscribe(job))
        assert names(events) == ["snapped", "error"]
        error = json.loads(events[-1].data)
        assert error == {"code": "WORKER_FAILED", "message": "RuntimeError: boom"}
        assert job.status == "error"


async def test_a_worker_refuses_a_brand_its_request_s_area_does_not_hold(tmp_path: Path) -> None:
    # Planning repeats the pre-flight's check: a job that skipped it ends INVALID_REQUEST.
    root = tmp_path / "data"
    save_build(root, lattice(10, 10, brands={"Acme": [55]}), "branded")
    out: queue.Queue[worker_module.Message] = queue.Queue()
    req = request(GRID, [0, 2], objective="brand", brand=0, leash_km=0.05)
    worker_module.run_job(JobSpec(root, "portland", "branded", "python", req), out)
    failed = out.get_nowait()
    assert isinstance(failed, worker_module.Failed) and out.empty()
    assert (failed.code, failed.message) == ("INVALID_REQUEST", "no Acme within reach")
    assert failed.fields == {"field": "brand"}


async def test_a_store_that_fails_while_cancelling_still_ends_the_stream(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data) as m:
        job = submit(m, loaded(data, "big"), request(BIG, [0, 399], max_iterations=1_000_000))
        subscription = m.subscribe(job)
        await until(lambda: job.accepted >= 3)

        def full(*args: object, **kwargs: object) -> str:
            raise OSError("disk full")

        monkeypatch.setattr(m.store, "put", full)
        await m.cancel(job)
        events = await collect(subscription)
        assert events[-1].name == "error"
        assert json.loads(events[-1].data)["message"] == "OSError: disk full"
        assert job.status == "error" and job.route_id is None


async def test_a_sweep_that_fails_on_a_job_ends_it_and_goes_on(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data, max_jobs=1, orphan_s=0.1, sweep_s=0.05) as m:
        build = loaded(data, "big")
        first = submit(m, build, request(BIG, [0, 399], max_iterations=1_000_000))
        waiting = submit(m, build, request(BIG, [0, 399], seed=5, max_iterations=1_000_000))
        await until(lambda: first.accepted >= 3)
        real = m.store.put

        def full(*args: object, **kwargs: object) -> str:
            raise OSError("disk full")

        monkeypatch.setattr(m.store, "put", full)
        m.subscribe(first).close()  # its subscribers have gone: the sweep cancels it
        await until(lambda: first.end is not None, timeout=5)
        assert first.error is not None and first.error.code == "WORKER_FAILED"
        assert "disk full" in first.error.message
        await until(lambda: waiting.status == "running")  # its slot was freed
        monkeypatch.setattr(m.store, "put", real)
        subscription = m.subscribe(waiting)
        await until(lambda: waiting.accepted >= 1)
        subscription.close()
        await until(lambda: waiting.end is not None, timeout=5)  # the sweep went on
        assert waiting.stopped_because == "cancelled" and waiting.route_id is not None


async def test_a_worker_that_does_not_start_ends_its_job_and_the_queue_moves_on(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    real = jobs_module._ThreadWorker

    def picky(spec: JobSpec, name: str) -> Any:
        if spec.request.seed == 5:
            raise OSError("no more processes")
        return real(spec, name)

    monkeypatch.setattr(jobs_module, "_ThreadWorker", picky)
    async with manager(data, max_jobs=1) as m:
        build = loaded(data, "big")
        running = submit(m, build, request(BIG, [0, 399], max_iterations=1_000_000))
        doomed = submit(m, build, request(BIG, [0, 399], seed=5))
        after = submit(m, build, request(BIG, [0, 399], seed=6))
        assert (doomed.status, after.status) == ("queued", "queued")
        await m.cancel(running)
        assert doomed.status == "error" and doomed.error is not None
        assert doomed.error.code == "WORKER_FAILED" and "no more processes" in doomed.error.message
        assert after.status == "running"
        await until(lambda: after.end is not None)
        assert after.status == "done"
        now = submit(m, build, request(BIG, [0, 399], seed=5))  # a start that fails at once
        assert now.status == "error"
        assert names(await collect(m.subscribe(now))) == ["snapped", "error"]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
async def test_a_worker_dead_with_splices_waiting_sends_them_before_its_error(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = worker_module.plan_route

    def dies(*args: Any, on_progress: Any = None, **kwargs: Any) -> Any:
        count = 0

        def progress(event: Any, baseline: Any) -> None:
            nonlocal count
            on_progress(event, baseline)
            count += 1
            if count == 9:
                raise SystemExit  # the worker is gone, no result sent

        return real(*args, on_progress=progress, **kwargs)

    monkeypatch.setattr(worker_module, "plan_route", dies)
    async with manager(data, coalesce_s=60.0) as m:  # the 9 splices are still waiting
        job = submit(m, loaded(data, "big"), request(BIG, [0, 399], max_iterations=1_000_000))
        live = m.subscribe(job)
        await until(lambda: job.end is not None)
        assert job.error is not None and job.error.code == "WORKER_FAILED"
        watched = await collect(live)
        assert names(watched) == ["snapped", "baseline", "snapshot", "progress", "error"]
        assert len(json.loads(watched[3].data)["splices"]) == 9
        late = await collect(m.subscribe(job))
        assert names(late) == ["snapped", "baseline", "snapshot", "error"]
        ids = [e.id for e in late]
        assert ids == sorted(set(ids)) and late[-1].id == job.last_id  # the error is last
        assert late[2].id == watched[3].id  # the snapshot reflects the last progress


async def test_an_ended_job_is_forgotten_after_its_ttl(data: Path) -> None:
    async with manager(data, ttl_s=0.1, sweep_s=0.05) as m:
        job = submit(m, loaded(data), request(GRID, [0, 99]))
        await until(lambda: job.end is not None)
        await until(lambda: job.id not in m.jobs, timeout=5)


def test_prepare_resolves_the_stops_as_the_worker_will(data: Path) -> None:
    build = loaded(data)
    req = request(GRID, [0, 45, 99])
    prepared = prepare(build, req)
    result = plan_route(build.data, req, backend=build.backend, spatial=build.spatial)
    assert prepared.snaps == result.snaps and prepared.masks.tier == result.masks.tier


# The worst order: on GRID, (0, 9, 45, 90, 99) is visited 0, 9, 90, 45, 99.
REORDERED = [0, 9, 45, 90, 99]
ORDER = [0, 1, 3, 2, 4]


async def test_a_reorder_job_says_where_its_stops_went_once_it_has_chosen_their_order(
    data: Path,
) -> None:
    async with manager(data) as m:
        build = loaded(data)
        plain = submit(m, build, request(GRID, [0, 45, 99]))
        assert json.loads(plain.header[0].data)["order"] == [0, 1, 2]  # at once, as given
        req = request(GRID, REORDERED, reorder=True)
        job = submit(m, build, req)
        assert job.header == []  # the pre-flight checked the given order only
        events = await collect(m.subscribe(job))
        assert names(events)[:3] == ["snapped", "baseline", "snapshot"]
        assert names(events).count("snapped") == 1 and names(events)[-1] == "done"
        snapped = json.loads(events[0].data)
        assert snapped["order"] == ORDER
        assert [s["node"] for s in snapped["stops"]] == [REORDERED[k] for k in ORDER]
        assert [s["label"] for s in snapped["stops"]] == [f"stop {k}" for k in ORDER]
        given = [{"lat": lat, "lon": lon} for lat, lon in req.stops]
        assert [s["input"] for s in snapped["stops"]] == [given[k] for k in ORDER]
        saved = validated(m, data, json.loads(events[-1].data)["route_id"])
        assert saved.order == tuple(ORDER) and saved.request.reorder
        assert saved.request.stops == [req.stops[k] for k in ORDER]
        assert [s["label"] for s in saved.input["stops"]] == [f"stop {k}" for k in ORDER]
        await until(lambda: plain.end is not None)


async def test_a_reorder_job_cancelled_during_its_search_is_an_error(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = plan_module.choose_order

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(plan_module, "choose_order", slow)
    async with manager(data) as m:
        job = submit(m, loaded(data), request(GRID, REORDERED, reorder=True))
        subscription = m.subscribe(job)
        await m.cancel(job)
        assert job.status == "error" and job.route_id is None
        events = await collect(subscription)
        assert names(events) == ["error"] and json.loads(events[0].data)["code"] == "CANCELLED"


async def test_a_reorder_job_cancelled_while_it_runs_stores_its_route_in_its_order(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the route so far is validated against the masks and stops of the order chosen
    slow_splices(monkeypatch)
    async with manager(data) as m:
        stops, order = [0, 19, 210, 380, 399], [0, 2, 1, 3, 4]  # on BIG
        req = request(BIG, stops, seed=4, reorder=True, max_iterations=1_000_000)
        job = submit(m, loaded(data, "big"), req)
        subscription = m.subscribe(job)
        await until(lambda: job.accepted >= 10)
        await m.cancel(job)
        events = await collect(subscription)
        done = json.loads(events[-1].data)
        assert events[-1].name == "done" and done["stopped_because"] == "cancelled"
        saved = validated(m, data, done["route_id"], "big")
        assert saved.order == tuple(order) and saved.request.stops == [req.stops[k] for k in order]
        assert [int(saved.nodes[k]) for k in saved.stop_idx] == [stops[k] for k in order]


def slow_search(monkeypatch: pytest.MonkeyPatch, delay_s: float) -> None:
    """Make the order search take `delay_s` more."""
    real = plan_module.choose_order

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(delay_s)
        return real(*args, **kwargs)

    monkeypatch.setattr(plan_module, "choose_order", slow)


async def test_a_reorder_job_s_deadline_waits_for_its_search_then_runs_from_it(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a 6 s search, past the 3 s budget and the 1.5 s grace: within the 3 s allowance before
    # `Started`, and the job, its budget run from `Started`, ends past budget + grace + allowance
    slow_search(monkeypatch, 6.0)
    slow_splices(monkeypatch, 0.2)  # the loop runs its whole budget
    async with manager(data, kill_grace_s=1.5, reorder_search_s=3.0) as m:
        req = request(BIG, [0, 19, 210, 380, 399], seed=4, reorder=True, budget_s=3)
        job = submit(m, loaded(data, "big"), req.model_copy(update={"max_iterations": 10**6}))
        events = await collect(m.subscribe(job))
        done = json.loads(events[-1].data)
        assert names(events)[:2] == ["snapped", "baseline"] and events[-1].name == "done"
        assert done["stopped_because"] == "budget"
        assert job.finished_at - job.started_at > 3.0 + 1.5 + 3.0
        validated(m, data, done["route_id"], "big")


async def test_a_reorder_job_whose_search_outlasts_its_allowance_times_out(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_search(monkeypatch, 3.0)
    async with manager(data, kill_grace_s=0.5, reorder_search_s=0.5) as m:
        job = submit(m, loaded(data), request(GRID, REORDERED, reorder=True, budget_s=1))
        events = await collect(m.subscribe(job))
        assert names(events) == ["error"] and job.route_id is None
        assert json.loads(events[0].data)["code"] == "WORKER_TIMEOUT"


# Seed 3 on this town of uneven streets: the run converges, reheats, takes shorter splices and ends
# a little below its best (4,295 m against 4,309 m).
UNEVEN_TOWN = uneven_lattice(6, 6, 0)


async def test_an_annealing_job_sends_its_best_in_every_progress_and_ends_on_it(data: Path) -> None:
    save_build(data, UNEVEN_TOWN, "uneven", latest=False)
    async with manager(data) as m:
        build = loaded(data, "uneven")
        job = submit(m, build, request(UNEVEN_TOWN, [0, 35], seed=3, max_iterations=10**6))
        events = await collect(m.subscribe(job))
        done = json.loads(events[-1].data)
        assert done["stopped_because"] == "converged"
        progress = [json.loads(e.data) for e in events if e.name == "progress"]
        best = [p["best_score"] for p in progress]
        assert best == sorted(best) and all(p["score"] <= p["best_score"] for p in progress)
        assert progress[-1]["score"] < best[-1]  # the current route ended below the best
        assert names(events)[-3:] == ["progress", "snapshot", "done"]
        shown = json.loads(events[-2].data)["metrics"]
        last = progress[-1]["best_metrics"]
        assert shown["length_m"] == last["length_m"] == done["metrics"]["length_m"] == best[-1]
        assert last["ratio"] == done["metrics"]["ratio"]
        validated(m, data, done["route_id"], "uneven")
