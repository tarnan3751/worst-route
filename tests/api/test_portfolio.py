"""Portfolios in the job manager: one job, several seeds. Fed workers replay real runs' messages
when a test hands them over, so it decides which search is ahead and when; inline workers run
real searches."""

import asyncio
import json
import queue
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from worst_route.api import jobs as jobs_module
from worst_route.api.jobs import JobManager
from worst_route.api.sse import Event, path_points
from worst_route.api.store import RouteStore
from worst_route.api.worker import Failed, Finished, JobSpec, Message, Started, run_job
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.engine.target import Target
from worst_route.graph.data import EdgeFlag

from ..engine.graphs import lattice
from .support import BIG, GRID, collect, loaded, manager, request, save_build, submit, until
from .test_jobs import ORDER, REORDERED, Client, names, validated

pytestmark = pytest.mark.anyio

# GRID's lattice with signals at every third row and column and bridges off its bottom row.
BRIDGED = lattice(
    10,
    10,
    primary_every=3,
    roads={(a, a + 1): {"flags": EdgeFlag.BRIDGE} for a in range(10, 90, 7) if a % 10 < 9},
)

# On BIG with 200 attempts, seed 3 grows to 23.4 km, seed 4 to 27.8 km and seed 5 to 27.0 km,
# all from the same seed route.
THREE = [0, 210, 399]


class Fed:
    """A worker that sends a recorded run's messages when the test hands them over."""

    def __init__(self, messages: list[Message]) -> None:
        self.left = deque(messages)
        self.out: queue.Queue[Message] = queue.Queue()
        self.stopped = False

    def send(self, count: int | None = None) -> None:
        for _ in range(len(self.left) if count is None else count):
            self.out.put(self.left.popleft())

    def get(self, timeout: float) -> Message:
        return self.out.get(timeout=timeout)

    def alive(self) -> bool:
        return not self.stopped

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        pass


def recorded(data: Path, req: RouteRequest, seed: int, build: str = "big") -> list[Message]:
    """The messages a worker sends planning `req` with `seed` on a build (the big lattice)."""
    out: queue.Queue[Message] = queue.Queue()
    run_job(JobSpec(data, "portland", build, "python", req.model_copy(update={"seed": seed})), out)
    return list(out.queue)


def feeding(monkeypatch: pytest.MonkeyPatch, runs: dict[int, list[Message]]) -> dict[int, Fed]:
    """Inline workers become fed ones, by seed: each sends its seed's run, or nothing."""
    fed: dict[int, Fed] = {}

    def make(spec: JobSpec, name: str) -> Fed:
        fed[spec.request.seed] = Fed(runs.get(spec.request.seed, []))
        return fed[spec.request.seed]

    monkeypatch.setattr(jobs_module, "_ThreadWorker", make)
    return fed


def payloads(events: list[Event], name: str) -> list[dict[str, Any]]:
    return [json.loads(e.data) for e in events if e.name == name]


def replayed(events: list[Event]) -> Client:
    client = Client()
    for event in events:
        client.apply(event)
    return client


async def test_the_lead_passes_to_the_search_ahead_and_only_the_leader_s_splices_are_sent(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request(BIG, THREE, max_iterations=200)
    runs = {seed: recorded(data, req, seed) for seed in (3, 4)}
    fed = feeding(monkeypatch, runs)
    async with manager(data, leader_hold_s=0.0) as m:
        build = loaded(data, "big")
        job = submit(m, build, req, portfolio=2)
        first, second = job.searches
        live = m.subscribe(job)
        fed[3].send(6)  # its start and five splices
        await until(lambda: first.accepted == 5)
        fed[4].send(1)  # its start, the same seed route: a tie keeps the leader
        await until(lambda: second.route is not None)
        assert job.leader == 0
        fed[4].send(len(runs[4]) - 2)  # every splice, well ahead
        await until(lambda: second.accepted == len(runs[4]) - 2)
        assert job.leader == 1
        fed[3].send(len(runs[3]) - 7)  # the rest of its splices, behind it: never sent
        await until(lambda: first.accepted == len(runs[3]) - 2)
        assert job.leader == 1
        fed[3].send()
        fed[4].send()
        events = await collect(live)

    assert names(events)[:3] == ["snapped", "baseline", "snapshot"]
    assert names(events).count("snapped") == names(events).count("baseline") == 1
    progress = payloads(events, "progress")
    assert all(p["portfolio"]["k"] == 2 for p in progress)
    leaders = [p["portfolio"]["leader"] for p in progress]
    switch = leaders.index(1)
    assert set(leaders[:switch]) == {0} and set(leaders[switch:]) == {1}
    assert progress[switch]["splices"] == []  # the new leader's numbers, after its snapshot
    at = events.index([e for e in events if e.name == "progress"][switch])
    assert events[at - 1].name == "snapshot"
    assert sum(len(p["splices"]) for p in progress[:switch]) == 5  # the first search's
    ratios = progress[-1]["portfolio"]["best_ratios"]
    assert ratios[1] > ratios[0] and ratios[1] == progress[-1]["best_metrics"]["ratio"]

    done = json.loads(events[-1].data)
    saved = validated(m, data, done["route_id"], "big")
    finished = runs[4][-1]
    assert isinstance(finished, Finished) and saved.nodes.tolist() == finished.document["nodes"]
    assert saved.seed == 4 and saved.request.seed == 4 and saved.input["seed"] == 3
    assert saved.run["portfolio"] == {"k": 2, "seeds": [3, 4], "winner": 1}
    assert saved.run["attempts"] == 200 and saved.input["portfolio"] == 2
    client = replayed(events)
    assert (client.coords, client.index) == path_points(build.data, build.geometry, saved.edges)
    assert names(events)[-2:] == ["progress", "done"]  # the leader's route is the one stored


async def test_the_hold_keeps_the_leader_but_never_the_stored_route_from_the_clients(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request(BIG, THREE, max_iterations=200)
    runs = {seed: recorded(data, req, seed) for seed in (3, 4)}
    fed = feeding(monkeypatch, runs)
    async with manager(data, leader_hold_s=60.0) as m:
        build = loaded(data, "big")
        job = submit(m, build, req, portfolio=2)
        live = m.subscribe(job)
        fed[3].send()  # its whole run: it has ended, and the job goes on
        await until(lambda: job.searches[0].ended)
        assert job.end is None
        fed[4].send(len(runs[4]) - 1)  # all but its end: ahead, but within the hold
        await until(lambda: job.searches[1].accepted == len(runs[4]) - 2)
        assert job.leader == 0
        fed[4].send()
        events = await collect(live)

    assert {p["portfolio"]["leader"] for p in payloads(events, "progress")} == {0}
    assert names(events)[-2:] == ["snapshot", "done"]  # the winner, drawn before the end
    done = json.loads(events[-1].data)
    saved = validated(m, data, done["route_id"], "big")
    assert saved.seed == 4 and saved.run["portfolio"]["winner"] == 1
    client = replayed(events)
    assert (client.coords, client.index) == path_points(build.data, build.geometry, saved.edges)
    assert job.leader == 1


async def test_a_search_that_fails_drops_out_and_the_lead_passes_at_once(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request(BIG, THREE, max_iterations=200)
    lost = Failed("WORKER_FAILED", "the worker stopped without a result")
    runs = {3: recorded(data, req, 3)[:4] + [lost], 4: recorded(data, req, 4)}
    fed = feeding(monkeypatch, runs)
    async with manager(data, leader_hold_s=60.0) as m:
        build = loaded(data, "big")
        job = submit(m, build, req, portfolio=2)
        live = m.subscribe(job)
        fed[3].send(4)
        fed[4].send(1)
        await until(lambda: job.searches[1].route is not None and job.searches[0].accepted == 3)
        fed[3].send()  # it fails while it leads
        await until(lambda: job.leader == 1)
        assert job.end is None and job.searches[0].failure is not None
        fed[4].send()
        events = await collect(live)

    progress = payloads(events, "progress")
    handed = next(p for p in progress if p["portfolio"]["leader"] == 1)
    assert handed["splices"] == [] and handed["portfolio"]["best_ratios"][0] is None
    done = json.loads(events[-1].data)
    saved = validated(m, data, done["route_id"], "big")
    assert saved.seed == 4 and saved.run["portfolio"]["winner"] == 1
    client = replayed(events)
    assert (client.coords, client.index) == path_points(build.data, build.geometry, saved.edges)


async def test_the_winner_drawn_before_done_keeps_its_delays_and_its_items(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_build(data, BRIDGED, "bridged", latest=False)
    req = request(BRIDGED, [0, 9], objective="collect", item="bridge", max_iterations=300)
    runs = {seed: recorded(data, req, seed, "bridged") for seed in (3, 4)}
    scores: dict[int, float] = {}
    for seed, run in runs.items():
        end = run[-1]
        assert isinstance(end, Finished)
        scores[seed] = end.score
    loser, winner = sorted(runs, key=scores.__getitem__)
    assert scores[loser] < scores[winner]
    fed = feeding(monkeypatch, runs)
    async with manager(data, leader_hold_s=60.0) as m:
        job = submit(m, loaded(data, "bridged"), req, portfolio=2)
        live = m.subscribe(job)
        fed[loser].send()  # it starts first, so it leads to its end, the hold keeping it
        await until(lambda: job.searches[loser - 3].ended)
        fed[winner].send()
        events = await collect(live)
        status = m.status(job, with_snapshot=True)
    assert names(events)[-2:] == ["snapshot", "done"]  # the winner, not the route drawn
    stored = m.store.get(json.loads(events[-1].data)["route_id"])
    assert stored is not None and stored.seed == winner
    doc = stored.metrics_json
    assert doc["delay_s"] > 0 and doc["items"]["bridge"] > 0 and doc["item"] == "bridge"
    assert status.snapshot is not None and status.metrics is not None
    for shown in (json.loads(events[-2].data)["metrics"], status.metrics.model_dump()):
        for key in ("length_m", "time_s", "delay_s", "item", "items"):
            assert shown[key] == doc[key], key
    assert status.snapshot.metrics == status.metrics


async def test_a_failed_leader_hands_the_lead_on_as_soon_as_another_search_starts(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request(BIG, THREE, max_iterations=200)
    lost = Failed("WORKER_FAILED", "the worker stopped without a result")
    runs = {3: recorded(data, req, 3)[:4] + [lost], 4: recorded(data, req, 4)}
    fed = feeding(monkeypatch, runs)
    async with manager(data, leader_hold_s=60.0) as m:
        job = submit(m, loaded(data, "big"), req, portfolio=2)
        live = m.subscribe(job)
        fed[3].send()  # it leads, alone, and fails: no other search has a route to lead with
        await until(lambda: job.searches[0].failure is not None)
        assert job.leader == 0 and job.end is None
        fed[4].send(1)  # its start: the lead passes at once, the hold keeping no failed leader
        await until(lambda: job.leader == 1)
        fed[4].send()
        events = await collect(live)
    handed = next(p for p in payloads(events, "progress") if p["portfolio"]["leader"] == 1)
    assert handed["splices"] == [] and handed["iteration"] == 0
    # its numbers are its start route's, the best's among them
    assert handed["best_score"] == handed["score"] > 0
    assert handed["best_metrics"] == handed["metrics"]
    done = json.loads(events[-1].data)
    assert validated(m, data, done["route_id"], "big").seed == 4


async def test_a_finished_leader_s_numbers_keep_coming_while_another_searches_behind(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request(BIG, THREE, max_iterations=200)
    # seeds 4 and 5, the second fed seed 3's run, which stays behind seed 4's
    runs = {4: recorded(data, req, 4), 5: recorded(data, req, 3)}
    fed = feeding(monkeypatch, runs)
    async with manager(data, leader_hold_s=60.0, tick_s=0.05) as m:
        job = submit(m, loaded(data, "big"), req.model_copy(update={"seed": 4}), portfolio=2)
        leader, behind = job.searches
        live = m.subscribe(job)
        fed[4].send()  # the leader's whole run: it has finished, the other not started
        await until(lambda: leader.ended)
        assert job.end is None
        fed[5].send(1)
        for _ in range(4):  # its splices a few at a time, a tick or more apart
            fed[5].send(3)
            await asyncio.sleep(0.15)
        await until(lambda: behind.accepted == 12)
        fed[5].send()
        events = await collect(live)
    ticks = [
        p
        for p in payloads(events, "progress")
        if p["splices"] == [] and p["portfolio"]["best_ratios"][1] is not None
    ]
    assert len(ticks) >= 3 and all(p["portfolio"]["leader"] == 0 for p in ticks)
    assert all(p["iteration"] == leader.iteration for p in ticks)  # the leader's last numbers
    ratios = [p["portfolio"]["best_ratios"][1] for p in ticks]
    assert ratios == sorted(ratios) and ratios[-1] > ratios[0]
    elapsed = [p["elapsed_s"] for p in ticks]  # the job's, rising
    assert all(b > a for a, b in zip(elapsed, elapsed[1:], strict=False))
    assert json.loads(events[-1].data)["stopped_because"] == "budget"


async def test_a_portfolio_whose_searches_all_fail_ends_in_the_first_s_error(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request(BIG, THREE, max_iterations=200)
    runs = {
        3: recorded(data, req, 3)[:3] + [Failed("WORKER_FAILED", "the first")],
        4: recorded(data, req, 4)[:5] + [Failed("VALIDATION_FAILED", "the second")],
    }
    fed = feeding(monkeypatch, runs)
    async with manager(data) as m:
        job = submit(m, loaded(data, "big"), req, portfolio=2)
        live = m.subscribe(job)
        fed[4].send()  # the second fails first
        await until(lambda: job.searches[1].ended)
        fed[3].send()
        events = await collect(live)
    error = json.loads(events[-1].data)
    assert events[-1].name == "error" and (error["code"], error["message"]) == (
        "WORKER_FAILED",
        "the first",
    )
    assert job.route_id is None


async def test_cancel_stores_the_best_route_so_far_of_the_search_ahead(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request(BIG, THREE, max_iterations=200)
    runs = {seed: recorded(data, req, seed) for seed in (3, 4)}
    fed = feeding(monkeypatch, runs)
    async with manager(data, leader_hold_s=60.0) as m:
        build = loaded(data, "big")
        job = submit(m, build, req, portfolio=2)
        live = m.subscribe(job)
        fed[3].send(4)
        fed[4].send(len(runs[4]) - 1)  # all but its end: ahead, but the hold keeps the leader
        second = job.searches[1]
        await until(lambda: second.accepted == len(runs[4]) - 2 and job.searches[0].accepted == 3)
        assert job.leader == 0
        await m.cancel(job)
        events = await collect(live)
        assert fed[3].stopped and fed[4].stopped

    assert names(events)[-2:] == ["snapshot", "done"]
    done = json.loads(events[-1].data)
    assert done["stopped_because"] == "cancelled"
    saved = validated(m, data, done["route_id"], "big")
    assert saved.seed == 4 and saved.run["portfolio"] == {"k": 2, "seeds": [3, 4], "winner": 1}
    assert saved.run["attempts"] == second.iteration and saved.run["accepted"] == second.accepted
    again = req.model_copy(update={"seed": 4, "max_iterations": saved.run["attempts"]})
    replay = plan_route(build.data, again, backend=build.backend, spatial=build.spatial)
    assert replay.route.nodes.tobytes() == saved.nodes.tobytes()
    client = replayed(events)
    assert (client.coords, client.index) == path_points(build.data, build.geometry, saved.edges)


async def test_the_first_search_to_reach_its_target_ends_the_job(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a 3× target: seed 4 reaches it in 7 attempts, seed 3 in 38
    req = request(BIG, [0, 399], max_iterations=100_000, target=Target(kind="ratio", value=3.0))
    runs = {seed: recorded(data, req, seed) for seed in (3, 4)}
    fed = feeding(monkeypatch, runs)
    async with manager(data) as m:
        job = submit(m, loaded(data, "big"), req, portfolio=2)
        live = m.subscribe(job)
        fed[3].send(3)
        await until(lambda: job.searches[0].accepted == 2)
        fed[4].send()
        events = await collect(live)
        assert fed[3].stopped and not job.searches[0].ended
    done = json.loads(events[-1].data)
    assert done["stopped_because"] == "target" and done["metrics"]["target"]["within_tolerance"]
    saved = validated(m, data, done["route_id"], "big")
    assert saved.seed == 4 and saved.run["portfolio"]["winner"] == 1


async def test_a_portfolio_stores_the_best_of_its_seeds_runs_which_its_seed_reproduces(
    data: Path,
) -> None:
    build = loaded(data, "big")
    req = request(BIG, THREE, max_iterations=200)
    plans = [
        plan_route(
            build.data,
            req.model_copy(update={"seed": seed}),
            backend=build.backend,
            spatial=build.spatial,
        )
        for seed in (3, 4, 5)
    ]
    best = max(plans, key=lambda p: p.route.score)
    async with manager(data, leader_hold_s=0.0) as m:
        job = submit(m, build, req, portfolio=3)
        events = await collect(m.subscribe(job))
        alone = submit(m, build, req)  # one search: no portfolio in its events or its run
        plain = await collect(m.subscribe(alone))

    assert all(p["portfolio"]["k"] == 3 for p in payloads(events, "progress"))
    done = json.loads(events[-1].data)
    saved = validated(m, data, done["route_id"], "big")
    assert saved.nodes.tobytes() == best.route.nodes.tobytes()
    assert saved.seed == best.request.seed == 4 and saved.run["portfolio"]["seeds"] == [3, 4, 5]
    assert saved.run["attempts"] == best.run.attempts and saved.run["accepted"] == best.run.accepted
    client = replayed(events)
    assert (client.coords, client.index) == path_points(build.data, build.geometry, saved.edges)
    assert all("portfolio" not in p for p in payloads(plain, "progress"))
    alone_saved = m.store.get(json.loads(plain[-1].data)["route_id"])
    assert alone_saved is not None and "portfolio" not in alone_saved.run


async def test_a_portfolio_in_the_worst_order_chooses_it_once(data: Path) -> None:
    async with manager(data) as m:
        build = loaded(data)
        job = submit(m, build, request(GRID, REORDERED, reorder=True), portfolio=2)
        events = await collect(m.subscribe(job))
    assert names(events).count("snapped") == 1
    assert json.loads(events[0].data)["order"] == ORDER
    saved = validated(m, data, json.loads(events[-1].data)["route_id"])
    assert list(saved.order) == ORDER
    assert [s["label"] for s in saved.input["stops"]] == [f"stop {k}" for k in ORDER]


async def test_a_search_that_chose_another_order_fails_the_job(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request(GRID, REORDERED, reorder=True)
    runs: dict[int, list[Message]] = {}
    for seed in (3, 4):
        out: queue.Queue[Message] = queue.Queue()
        spec = JobSpec(data, "portland", "grid", "python", req.model_copy(update={"seed": seed}))
        run_job(spec, out)
        runs[seed] = list(out.queue)
    started = runs[4][0]
    assert isinstance(started, Started)
    runs[4][0] = replace(started, order=tuple(range(len(REORDERED))))
    fed = feeding(monkeypatch, runs)
    async with manager(data) as m:
        job = submit(m, loaded(data), req, portfolio=2)
        live = m.subscribe(job)
        fed[3].send(1)
        await until(lambda: job.route is not None)
        fed[4].send(1)
        events = await collect(live)
    error = json.loads(events[-1].data)
    assert error["code"] == "WORKER_FAILED"
    assert "seed 4 visits the stops [0, 1, 2, 3, 4]" in error["message"]


async def test_a_portfolio_holds_a_worker_a_search_and_the_queue_keeps_its_order(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fed = feeding(monkeypatch, {})  # workers that send nothing until told
    async with manager(data, max_jobs=4) as m:
        build = loaded(data, "big")
        req = request(BIG, [0, 399])
        three = submit(m, build, req, portfolio=3)  # seeds 3, 4, 5
        two = submit(m, build, req.model_copy(update={"seed": 10}), portfolio=2)
        one = submit(m, build, req.model_copy(update={"seed": 20}))
        # one worker free, but the job waiting for two holds up the one behind it
        assert (three.status, two.status, one.status) == ("running", "queued", "queued")
        assert m._free() == 1
        fed[3].out.put(Failed("WORKER_FAILED", "gone"))  # a search ends: its worker is free
        await until(lambda: two.status == "running")
        assert one.status == "queued" and three.status == "running" and m._free() == 0
        fed[4].out.put(Failed("WORKER_FAILED", "gone"))
        await until(lambda: one.status == "running")
        assert m._free() == 0
        for job in (three, two, one):
            await m.cancel(job)
        assert m._free() == 4


async def test_a_client_runs_one_job_whatever_its_searches(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feeding(monkeypatch, {})
    async with manager(data, max_jobs=6, running_per_client=1, queued_per_client=3) as m:
        build = loaded(data, "big")
        req = request(BIG, [0, 399])
        first = submit(m, build, req, portfolio=2, client="a")
        later = submit(m, build, req.model_copy(update={"seed": 9}), portfolio=2, client="a")
        other = submit(m, build, req.model_copy(update={"seed": 19}), portfolio=2, client="b")
        assert (first.status, later.status, other.status) == ("running", "queued", "running")
        assert m.client_jobs("a") == (1, 1) and m._free() == 2
        await m.cancel(first)
        assert later.status == "running" and m._free() == 2
        for job in (later, other):
            await m.cancel(job)


def test_a_full_server_refuses_a_portfolio_it_has_no_room_to_queue(data: Path) -> None:
    m = JobManager(
        jobs_module.JobSettings(max_jobs=2, max_queued=0, inline_workers=True),
        RouteStore(data / "routes.sqlite"),
        data_dir=data,
        backend="python",
    )
    assert m.refusal(None, 2) is None
    refused = m.refusal(None, 3)
    assert refused is not None and refused.running is None
