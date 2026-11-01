"""The certified worst over the API: `POST /jobs` with `certified`, its refusals, its events (each
better route a snapshot and a progress, the bound between them a progress, each carrying the
solver's bound and gap), its route document's `run.certified`, and a cancelled job's."""

import queue
from pathlib import Path
from typing import Any

import pytest

from worst_route.api import jobs as jobs_module
from worst_route.api.worker import Bounded, Improved, JobSpec, Message, Started, run_job
from worst_route.config import CERTIFIED_WORKERS
from worst_route.engine import exact
from worst_route.engine.request import RouteRequest

from .support import loaded, manager, submit, until
from .test_jobs import names, validated
from .test_portfolio import Fed, payloads
from .test_routes_api import finished, job_body, serving, stop

pytestmark = pytest.mark.anyio


def certified(**fields: Any) -> dict[str, Any]:
    """Row 0's two ends on the 10 × 10 lattice, a 250 m leash: rows 0 to 2."""
    body = {"stops": [stop(0), stop(9)], "certified": True, "leash_km": 0.25, "budget_s": 30}
    return job_body(**{**body, **fields})


async def test_a_certified_job_streams_better_routes_and_stores_its_certificate(
    data: Path,
) -> None:
    async with serving(data) as (client, _):
        _, events = await finished(client, certified())
        kinds = [e["event"] for e in events]
        assert kinds[:3] == ["snapped", "baseline", "snapshot"] and kinds[-1] == "done"
        assert events[0]["data"]["leash_km"] == 0.25
        progress = [e["data"] for e in events if e["event"] == "progress"]
        assert progress and all(p["splices"] == [] for p in progress)
        assert all(p["certified"]["gap"] >= 0 for p in progress)
        # after the seed's snapshot, each better route a snapshot, then its progress
        for k, kind in enumerate(kinds[3:-1], start=3):
            if kind == "snapshot":
                assert kinds[k + 1] == "progress"
        lengths = [p["metrics"]["length_m"] for p in progress]
        assert lengths == sorted(lengths)
        done = events[-1]["data"]
        assert done["stopped_because"] == "optimal"
        assert done["metrics"]["length_m"] == lengths[-1]
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert doc["request"]["certified"] is True and doc["run"]["stopped_because"] == "optimal"
        run = doc["run"]["certified"]
        assert run["status"] == "optimal" and run["gap"] == 0.0
        assert run["bound_m"] == doc["metrics"]["length_m"]
        assert (run["nodes"], run["leash_m"], run["workers"]) == (30, 250.0, CERTIFIED_WORKERS)
        report = (await client.post(f"/routes/{done['route_id']}/validate")).json()
        assert report["ok"] is True

        _, events = await finished(client, job_body(stops=[stop(0), stop(9)]))  # not certified
        doc = (await client.get(f"/routes/{events[-1]['data']['route_id']}")).json()
        assert "certified" not in doc["run"] and "certified" not in doc["request"]
        progress = [e["data"] for e in events if e["event"] == "progress"]
        assert progress and all("certified" not in p for p in progress)


async def test_the_automatic_leash_is_the_one_snapped_and_recorded(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exact, "CERTIFIED_AUTO_MAX_EDGES", 100)
    async with serving(data) as (client, _):
        _, events = await finished(client, certified(leash_km="auto"))
        leash_km = events[0]["data"]["leash_km"]
        assert 0.05 <= leash_km < 3  # narrower than the automatic leash, whose area is the grid
        doc = (await client.get(f"/routes/{events[-1]['data']['route_id']}")).json()
        assert doc["request"]["leash_km"] == "auto"  # as asked; the run says the leash used
        assert doc["run"]["certified"]["leash_m"] == pytest.approx(leash_km * 1000)
        assert doc["run"]["certified"]["edges"] <= 100
        report = (await client.post(f"/routes/{events[-1]['data']['route_id']}/validate")).json()
        assert report["ok"] is True


async def test_a_better_route_whose_bound_lies_below_it_is_sent_with_a_null_bound(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ortools.sat.python import cp_model

    below = property(lambda _: 0.0)
    monkeypatch.setattr(cp_model.CpSolverSolutionCallback, "best_objective_bound", below)
    async with serving(data) as (client, _):
        _, events = await finished(client, certified())
        kinds = [e["event"] for e in events]
        better = [
            events[k + 1]["data"]
            for k, kind in enumerate(kinds[:-1])
            if kind == "snapshot" and k > 2 and kinds[k + 1] == "progress"
        ]
        assert better and all(p["certified"] == {"bound_m": None, "gap": None} for p in better)
        assert events[-1]["data"]["stopped_because"] == "optimal"


@pytest.mark.parametrize(
    "fields",
    [
        {"objective": "time"},
        {"target": {"kind": "duration", "seconds": 3600}},
        {"reorder": True, "stops": [stop(0), stop(5), stop(9)]},
        {"constraints": {"no_left": True}},
        {"constraints": {"hard": True}},
        {"stops": [stop(0), stop(0)]},
        {"portfolio": 2},
        {"base_route_id": "abcdefabcdef"},
    ],
)
async def test_what_a_certified_route_does_not_take_is_refused(
    data: Path, fields: dict[str, Any]
) -> None:
    async with serving(data) as (client, _):
        r = await client.post("/jobs", json=certified(**fields))
        assert r.status_code == 422
        assert (r.json()["code"], r.json()["field"]) == ("INVALID_REQUEST", "certified"), r.json()


async def test_an_area_too_large_to_certify_names_the_leash(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exact, "CERTIFIED_MAX_EDGES", 50)
    async with serving(data) as (client, app):
        r = await client.post("/jobs", json=certified(leash_km=1.0))
        assert r.status_code == 422
        body = r.json()
        assert body["code"] == "AREA_TOO_LARGE_TO_CERTIFY" and body["field"] == "leash"
        assert (body["nodes"], body["max_nodes"], body["max_edges"]) == (100, 300, 50)
        assert body["edges"] > 50 and body["leash_m"] == 1000.0
        assert "tighten the leash" in body["message"]
        assert not app.state.api.jobs.jobs  # refused before a job existed


def recorded_run(data: Path, req: RouteRequest) -> list[Message]:
    out: queue.Queue[Message] = queue.Queue()
    run_job(JobSpec(data, "portland", "grid", "python", req), out)
    return list(out.queue)


async def test_a_bound_is_a_progress_and_a_cancelled_job_stores_its_certificate(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = loaded(data)
    req = RouteRequest(
        stops=[stop_at(0), stop_at(9)], seed=3, certified=True, leash_km=0.25, budget_s=30
    )
    messages = recorded_run(data, req)
    started = next(m for m in messages if isinstance(m, Started))
    better = [m for m in messages if isinstance(m, Improved)]
    assert better and all(m.certified is not None for m in better)
    feed: list[Message] = [started, better[0], Bounded(9.5, 4321.0, 0.25)]
    fed: list[Fed] = []

    def make(spec: JobSpec, name: str) -> Fed:
        fed.append(Fed(feed))
        return fed[-1]

    monkeypatch.setattr(jobs_module, "_ThreadWorker", make)
    async with manager(data) as m:
        job = submit(m, build, req)
        assert job.area is not None and job.request.leash_km == 0.25
        live = m.subscribe(job)
        fed[0].send()
        await until(lambda: job.lead.certified is not None and job.lead.certified.gap == 0.25)
        await m.cancel(job)
        events = [e async for e in live.events()]
    assert names(events)[-1] == "done"
    progress = payloads(events, "progress")
    assert progress[-1]["splices"] == [] and progress[-1]["elapsed_s"] == 9.5
    assert progress[-1]["certified"] == {"bound_m": 4321.0, "gap": 0.25}
    first = better[0].certified
    assert first is not None
    assert progress[0]["certified"] == {"bound_m": first[0], "gap": first[1]}
    saved = validated(m, data, job.route_id or "")
    assert saved.run["stopped_because"] == "cancelled"
    assert saved.run["certified"] == {
        "status": "feasible",
        "bound_m": 4321.0,
        "gap": 0.25,
        "nodes": job.area[0],
        "edges": job.area[1],
        "leash_m": 250.0,
        "solve_s": 9.5,  # the last bound's time, not the last better route's
        "workers": CERTIFIED_WORKERS,
    }
    assert saved.run["elapsed_s"] == 9.5


def stop_at(v: int) -> tuple[float, float]:
    s = stop(v)
    return s["lat"], s["lon"]


async def test_a_drawing_is_never_certified(data: Path) -> None:
    async with serving(data) as (client, _):
        r = await client.post("/draw", json={"request": certified(), "path": []})
        assert r.status_code == 422
        assert (r.json()["code"], r.json()["field"]) == ("INVALID_REQUEST", "certified")
