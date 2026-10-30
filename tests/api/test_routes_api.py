"""The endpoints, over httpx in process (inline workers). httpx's ASGI transport hands a response
over once the app has finished it, so an SSE stream is read whole, after its `done` or `error`."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from worst_route.api import routes_api
from worst_route.api.app import Api, ApiSettings, create_app
from worst_route.api.jobs import JobSettings
from worst_route.engine.io import feature_collection
from worst_route.engine.snap import StopUnreachable

from ..engine.graphs import at_node
from .support import GRID, GRID_BBOX, slow_splices, until

pytestmark = pytest.mark.anyio


def stop(v: int, label: str | None = None) -> dict[str, Any]:
    lat, lon = at_node(GRID, v)
    return {"lat": lat, "lon": lon, "label": label}


def job_body(**fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "region": "portland",
        "stops": [stop(0, "home"), stop(45), stop(99, "work")],
        "seed": 3,
        "max_iterations": 300,
        "budget_s": 60,
    }
    return {**body, **fields}


@asynccontextmanager
async def serving(data: Path, **jobs: Any) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    settings = JobSettings(**{"inline_workers": True, "coalesce_s": 0.0, **jobs})
    app = create_app(ApiSettings(data_dir=data, region="portland", backend="python", jobs=settings))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


def events_of(text: str) -> list[dict[str, Any]]:
    """The events of an SSE body: {id, event, data}."""
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            out.append(
                {
                    "id": int(fields["id"]),
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return out


async def finished(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    """Start a job and read its whole stream."""
    created = await client.post("/jobs", json=body)
    assert created.status_code == 202, created.text
    job = created.json()
    stream = await client.get(job["events_url"])
    assert stream.headers["content-type"].startswith("text/event-stream")
    return job["job_id"], events_of(stream.text)


async def test_health_and_regions_describe_the_loaded_build(data: Path) -> None:
    async with serving(data) as (client, app):
        version = app.state.api.builds["portland"].data.meta["graph_version"]
        health = (await client.get("/health")).json()
        assert health == {
            "status": "ok",
            "builds": [{"region": "portland", "build_id": "grid", "graph_version": version}],
        }
        [region] = (await client.get("/regions")).json()
        assert region["bbox"] == GRID_BBOX and region["counts"]["nodes"] == 100
        assert [t["tier"] for t in region["tiers"]] == ["T0", "T1", "T2", "T3", "T4"]
        assert region["tiers"][4] == {"tier": "T4", "nodes": 100, "edges": 360}
        missing = await client.get("/nowhere")
        assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"


async def test_snap_puts_a_stop_on_a_node_for_its_role(data: Path) -> None:
    async with serving(data) as (client, _):
        lat, lon = at_node(GRID, 45)
        for role in ("start", "via", "end"):
            r = await client.post(
                "/snap", json={"region": "portland", "lat": lat, "lon": lon, "role": role}
            )
            assert r.status_code == 200 and r.json()["node"] == 45 and r.json()["note"] is None
        near = await client.post(
            "/snap",
            json={
                "region": "portland",
                "lat": lat + 0.0006,  # 67 m north: nearer node 55
                "lon": lon,
                "role": "via",
                "tier": "T0",
            },
        )
        assert near.json()["node"] == 55  # T0 has no road here: the bubble keeps the streets
        far = {"region": "portland", "lat": GRID_BBOX[3] - 0.01, "lon": lon, "role": "start"}
        r = await client.post("/snap", json=far)
        assert r.status_code == 422 and r.json()["code"] == "NO_NODE_NEAR"
        outside = {**far, "lat": GRID_BBOX[3] + 0.01}
        r = await client.post("/snap", json=outside)
        assert r.status_code == 422 and r.json()["code"] == "STOP_OUTSIDE_REGION"
        r = await client.post("/snap", json={**far, "role": "nowhere"})
        assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"


async def test_a_job_streams_its_route_and_stores_it(data: Path) -> None:
    async with serving(data) as (client, app):
        job_id, events = await finished(client, job_body())
        assert [e["event"] for e in events[:3]] == ["snapped", "baseline", "snapshot"]
        assert events[-1]["event"] == "done" and any(e["event"] == "progress" for e in events)
        assert [e["id"] for e in events] == sorted({e["id"] for e in events})
        snapped = events[0]["data"]
        assert [s["label"] for s in snapped["stops"]] == ["home", None, "work"]
        assert snapped["stops"][0]["input"] == {"lat": stop(0)["lat"], "lon": stop(0)["lon"]}
        route_id = events[-1]["data"]["route_id"]

        status = (await client.get(f"/jobs/{job_id}")).json()
        assert status["status"] == "done" and status["route_id"] == route_id
        assert status["snapshot"]["node_count"] == len(status["snapshot"]["node_index"])

        doc = (await client.get(f"/routes/{route_id}")).json()
        assert doc["route_id"] == route_id and doc["seed"] == 3 and doc["region"] == "portland"
        assert doc["request"]["max_iterations"] == 300 and doc["request"]["base_route_id"] is None
        assert [s["label"] for s in doc["stops"]] == ["home", None, "work"]
        assert [s["node"] for s in doc["stops"]] == [0, 45, 99]
        assert doc["stops"][0]["route_index"] == 0
        assert doc["legs"][0]["node_range"] == [0, doc["stops"][1]["route_index"]]
        m = doc["metrics"]
        assert m["ratio"] == pytest.approx(m["length_m"] / m["baseline_length_m"])
        assert m["time_ratio"] == pytest.approx(m["time_s"] / m["baseline_time_s"])
        assert m["departure"] is None and m["arrival"] is None
        assert doc["run"]["attempts"] == 300 and doc["run"]["backend"] == "python"
        assert doc["run"]["stopped_because"] == "budget" and doc["validation"]["ok"] is True
        assert doc["geometry"]["type"] == "LineString"
        for absent in ("directions", "toasts", "comparisons"):
            assert absent not in doc

        geojson = (await client.get(f"/routes/{route_id}/geojson")).json()
        api: Api = app.state.api
        stored = api.store.get(route_id)
        assert stored is not None
        assert geojson == feature_collection(
            api.builds["portland"].data,
            edges=stored.edges,
            length_m=stored.metrics.length_m,
            time_s=stored.metrics.time_s,
            baseline_edges=stored.baseline_edges,
            baseline_length_m=stored.metrics_json["baseline_length_m"],
            baseline_time_s=stored.metrics_json["baseline_time_s"],
            snaps=stored.stops,
        )
        report = (await client.post(f"/routes/{route_id}/validate")).json()
        assert report["ok"] is True and len(report["checks"]) == 8

        resumed = events_of(
            (await client.get(f"/jobs/{job_id}/events", headers={"Last-Event-ID": "2"})).text
        )
        assert [e["event"] for e in resumed] == ["snapshot", "done"]


async def test_a_departure_gives_an_arrival(data: Path) -> None:
    async with serving(data) as (client, _):
        body = job_body(
            departure="2026-10-31T09:00:00-07:00", vehicle={"mpg": 24, "gas_price_usd": 3.89}
        )
        _, events = await finished(client, body)
        doc = (await client.get(f"/routes/{events[-1]['data']['route_id']}")).json()
        assert doc["metrics"]["departure"] == "2026-10-31T09:00:00-07:00"
        assert doc["metrics"]["arrival"] > doc["metrics"]["departure"]
        assert doc["request"]["vehicle"] == {"mpg": 24.0, "gas_price_usd": 3.89}


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"stops": None}, "INVALID_REQUEST"),
        ({"constraints": {"no_left": True}}, "INVALID_REQUEST"),
        ({"objective": "nonsense"}, "INVALID_REQUEST"),
        ({"region": "atlantis"}, "UNKNOWN_REGION"),
        ({"objective": "time"}, "NOT_YET"),
        ({"target": {"kind": "ratio", "value": 50}}, "NOT_YET"),
        ({"budget_s": 121}, "BUDGET_TOO_LARGE"),
        ({"base_route_id": "000000000000"}, "UNKNOWN_ROUTE"),
        ({"seed": 2**64}, "INVALID_REQUEST"),
    ],
)
async def test_a_job_that_cannot_be_planned_is_refused(
    data: Path, fields: dict[str, Any], code: str
) -> None:
    async with serving(data) as (client, _):
        r = await client.post("/jobs", json=job_body(**fields))
        assert r.status_code == 422 and r.json()["code"] == code, r.text
        assert r.json()["message"]


async def test_stops_outside_the_region_or_far_from_roads_are_named(data: Path) -> None:
    async with serving(data) as (client, _):
        lat, lon = at_node(GRID, 0)
        outside = job_body(stops=[stop(0), {"lat": GRID_BBOX[3] + 0.01, "lon": lon}])
        r = await client.post("/jobs", json=outside)
        assert r.json()["code"] == "STOP_OUTSIDE_REGION" and r.json()["stop_index"] == 1
        far = job_body(stops=[stop(0), {"lat": GRID_BBOX[3] - 0.01, "lon": lon}])
        r = await client.post("/jobs", json=far)
        assert (
            r.status_code == 422
            and r.json()["code"] == "NO_NODE_NEAR"
            and r.json()["stop_index"] == 1
        )


async def test_an_unreachable_stop_names_its_leg(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(*args: object) -> None:
        raise StopUnreachable((1, 2), "after moving stop 1 and stop 2 up to 10 times each")

    monkeypatch.setattr(routes_api, "prepare", unreachable)
    async with serving(data) as (client, _):
        r = await client.post("/jobs", json=job_body())
        assert r.status_code == 422
        assert {k: r.json()[k] for k in ("code", "leg", "stop_index")} == {
            "code": "STOP_UNREACHABLE_WITHOUT_REPEAT",
            "leg": [1, 2],
            "stop_index": 2,
        }


async def test_a_full_server_answers_429(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slow_splices(monkeypatch)
    async with serving(data, max_jobs=1, max_queued=0) as (client, app):
        first = await client.post("/jobs", json=job_body(max_iterations=1_000_000))
        assert first.status_code == 202
        r = await client.post("/jobs", json=job_body(seed=4))
        assert r.status_code == 429 and r.json()["code"] == "TOO_MANY_JOBS"
        await client.delete(f"/jobs/{first.json()['job_id']}")


async def test_unknown_jobs_and_routes_are_not_found(data: Path) -> None:
    async with serving(data) as (client, _):
        for method, path in [
            ("GET", "/jobs/nope"),
            ("GET", "/jobs/nope/events"),
            ("DELETE", "/jobs/nope"),
            ("GET", "/routes/nope"),
            ("GET", "/routes/nope/geojson"),
            ("POST", "/routes/nope/validate"),
        ]:
            r = await client.request(method, path)
            assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", path


async def test_cancel_stores_the_route_so_far(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slow_splices(monkeypatch, delay_s=0.02)
    (data / "graphs" / "portland" / "latest").write_text("big\n")  # a longer run
    async with serving(data) as (client, app):
        created = (await client.post("/jobs", json=job_body(max_iterations=1_000_000))).json()
        job = app.state.api.jobs.jobs[created["job_id"]]
        await until(lambda: job.accepted >= 5)
        r = await client.delete(f"/jobs/{created['job_id']}")
        status = r.json()
        assert r.status_code == 200 and status["status"] == "done"
        assert status["stopped_because"] == "cancelled" and status["snapshot"] is None
        events = events_of((await client.get(created["events_url"])).text)
        assert (
            events[-1]["event"] == "done" and events[-1]["data"]["stopped_because"] == "cancelled"
        )
        doc = (await client.get(f"/routes/{status['route_id']}")).json()
        assert doc["run"]["stopped_because"] == "cancelled"
        assert (await client.post(f"/routes/{status['route_id']}/validate")).json()["ok"] is True


async def test_closing_the_stream_cancels_the_job(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch, delay_s=0.02)
    (data / "graphs" / "portland" / "latest").write_text("big\n")  # a longer run
    async with serving(data, orphan_s=0.2, sweep_s=0.05) as (client, app):
        created = (await client.post("/jobs", json=job_body(max_iterations=1_000_000))).json()
        job = app.state.api.jobs.jobs[created["job_id"]]
        reading = asyncio.create_task(client.get(created["events_url"]))
        await until(lambda: bool(job.subscribers) and job.accepted >= 3)
        reading.cancel()  # the tab is closed
        await until(lambda: job.end is not None, timeout=5)
        assert job.stopped_because == "cancelled" and job.route_id is not None


async def test_a_stream_subscribes_only_once_it_runs(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    (data / "graphs" / "portland" / "latest").write_text("big\n")  # a longer run
    async with serving(data) as (client, app):
        created = (await client.post("/jobs", json=job_body(max_iterations=1_000_000))).json()
        job = app.state.api.jobs.jobs[created["job_id"]]
        scope = {"type": "http", "method": "GET", "headers": [], "app": app, "query_string": b""}
        response = await routes_api.job_events(created["job_id"], Request(scope))
        assert not job.subscribers  # a client gone before the stream starts leaves none
        stream = response.body_iterator
        assert isinstance(stream, AsyncGenerator)
        first = await anext(stream)
        assert first.event == "snapped" and len(job.subscribers) == 1
        await stream.aclose()  # the client goes
        assert not job.subscribers
        await client.delete(created["events_url"].removesuffix("/events"))


async def test_a_base_route_outside_its_request_s_masks_is_a_mismatch(data: Path) -> None:
    async with serving(data) as (client, app):
        _, events = await finished(client, job_body(max_iterations=60))
        base_id = events[-1]["data"]["route_id"]
        api: Api = app.state.api
        with sqlite3.connect(api.store.path) as con:  # as if planned with a 150 m leash
            (text,) = con.execute(
                "SELECT request_json FROM routes WHERE id = ?", (base_id,)
            ).fetchone()
            narrowed = {**json.loads(text), "leash_km": 0.15}
            con.execute(
                "UPDATE routes SET request_json = ? WHERE id = ?", (json.dumps(narrowed), base_id)
            )
        r = await client.post(
            "/jobs", json={"region": "portland", "base_route_id": base_id, "seed": 4}
        )
        assert r.status_code == 422 and r.json()["code"] == "BASE_ROUTE_MISMATCH"
        assert r.json()["field"] == "route" and "leaves the request's area" in r.json()["message"]


async def test_a_continuation_that_changes_nothing_reports_the_stored_route(data: Path) -> None:
    async with serving(data) as (client, _):
        _, events = await finished(
            client, job_body(max_iterations=60, departure="2026-10-31T09:00:00-07:00")
        )
        base_id = events[-1]["data"]["route_id"]
        still = {"region": "portland", "base_route_id": base_id, "seed": 9, "max_iterations": 0}
        _, events = await finished(client, still)
        assert events[-1]["data"]["route_id"] == base_id  # the same route, so the same id
        doc = (await client.get(f"/routes/{base_id}")).json()
        assert doc["seed"] == 3 and doc["request"]["base_route_id"] is None  # the first record
        assert doc["metrics"]["departure"] == "2026-10-31T09:00:00-07:00"


async def test_make_it_worse_continues_a_stored_route(data: Path) -> None:
    async with serving(data) as (client, app):
        _, events = await finished(client, job_body(max_iterations=60))
        base_id = events[-1]["data"]["route_id"]
        base = (await client.get(f"/routes/{base_id}")).json()

        worse = {"region": "portland", "base_route_id": base_id, "seed": 4, "max_iterations": 400}
        _, events = await finished(client, worse)
        assert events[2]["event"] == "snapshot" and events[2]["data"]["iteration"] == 0
        assert events[2]["data"]["node_count"] == len(base["geometry"]["coordinates"])
        doc = (await client.get(f"/routes/{events[-1]['data']['route_id']}")).json()
        assert doc["request"]["base_route_id"] == base_id and doc["seed"] == 4
        assert doc["request"]["stops"] == base["request"]["stops"]
        assert [s["node"] for s in doc["stops"]] == [s["node"] for s in base["stops"]]
        assert doc["metrics"]["length_m"] >= base["metrics"]["length_m"]
        assert (await client.post(f"/routes/{doc['route_id']}/validate")).json()["ok"] is True

        same_stops = {**worse, "stops": base["request"]["stops"], "tier": "auto"}
        assert (await client.post("/jobs", json=same_stops)).status_code == 202
        for change, field in [
            ({"stops": [stop(0), stop(98)]}, "stops"),
            ({"tier": "T2"}, "tier"),
            ({"leash_km": 9}, "leash_km"),
        ]:
            r = await client.post("/jobs", json={**worse, **change})
            assert r.status_code == 422 and r.json()["code"] == "BASE_ROUTE_MISMATCH"
            assert r.json()["field"] == field

        api: Api = app.state.api
        stored = api.store.get(base_id)
        assert stored is not None
        document = {
            "graph_version": "another build",
            "region": "portland",
            "build_id": "grid",
            "seed": 1,
            "engine_version": stored.engine_version,
            "request": stored.request.model_dump(mode="json"),
            "stops": [
                {"lat": s.lat, "lon": s.lon, "node": s.node, "moved_m": s.moved_m, "note": s.note}
                for s in stored.stops
            ],
            "nodes": stored.nodes.tolist(),
            "edges": stored.edges.tolist(),
            "stop_idx": list(stored.stop_idx),
            "metrics": stored.metrics_json,
            "run": stored.run,
        }
        elsewhere = api.store.put(
            document, input=stored.input, baseline_edges=stored.baseline_edges
        )
        r = await client.post("/jobs", json={**worse, "base_route_id": elsewhere})
        assert r.status_code == 422 and r.json()["code"] == "BUILD_MISMATCH"
        r = await client.get(f"/routes/{elsewhere}")
        assert r.status_code == 409 and r.json()["code"] == "BUILD_UNAVAILABLE"

        older = doc["route_id"]  # as if an earlier engine had made it
        with sqlite3.connect(api.store.path) as con:
            con.execute("UPDATE routes SET engine_version = '1' WHERE id = ?", (older,))
        for r in (
            await client.post("/jobs", json={**worse, "base_route_id": older}),
            await client.post(f"/routes/{older}/validate"),
        ):
            assert r.status_code == 422 and r.json()["code"] == "BUILD_MISMATCH"
            assert "engine version 1" in r.json()["message"]
