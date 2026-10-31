"""The endpoints, over httpx in process (inline workers). httpx's ASGI transport hands a response
over once the app has finished it, so an SSE stream is read whole, after its `done` or `error`."""

import asyncio
import json
import shutil
import sqlite3
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI, Request

from worst_route.api import routes_api
from worst_route.api.app import Api, ApiSettings, create_app
from worst_route.api.jobs import JobSettings
from worst_route.engine.constraints import Constraints, returning
from worst_route.engine.snap import StopUnreachable
from worst_route.graph.data import EdgeFlag
from worst_route.graph.geometry import encode
from worst_route.graph.loader import load_build
from worst_route.graph.testing import Edge, make_graph
from worst_route.presentation.geojson import feature_collection, line_coordinates

from ..engine.graphs import (
    TREES,
    X0,
    Y0,
    at,
    at_node,
    avenue,
    edges_of,
    exactly_at_node,
    lattice,
    named_roads,
    two_rings,
)
from .support import GRID, GRID_BBOX, SLOW, save_build, slow_splices, until

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
    config = ApiSettings(
        data_dir=data, region="portland", backend="python", jobs=settings, geocoder="none"
    )
    app = create_app(config)
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
        assert m["baseline_delay_s"] == m["delay_s"] == 0.0  # the lattice has no signals
        assert m["time_ratio"] == pytest.approx(m["time_s"] / m["baseline_time_s"])
        assert m["departure"] is None and m["arrival"] is None
        assert doc["run"]["attempts"] == 300 and doc["run"]["backend"] == "python"
        assert doc["run"]["stopped_because"] == "budget" and doc["validation"]["ok"] is True
        assert doc["geometry"]["type"] == "LineString"
        assert doc["directions"][0]["kind"] == "depart" and doc["toasts"]  # test_directions_api
        assert "comparisons" not in doc

        geojson = (await client.get(f"/routes/{route_id}/geojson")).json()
        api: Api = app.state.api
        stored = api.store.get(route_id)
        assert stored is not None
        assert geojson == feature_collection(
            api.builds["portland"].data,
            api.builds["portland"].geometry,
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


async def test_a_target_job_stops_within_it_and_reports_it(data: Path) -> None:
    async with serving(data) as (client, _):
        body = job_body(
            stops=[stop(0), stop(99)], target={"kind": "ratio", "value": 3}, max_iterations=10**6
        )
        _, events = await finished(client, body)
        done = events[-1]["data"]
        assert done["stopped_because"] == "target"
        assert done["metrics"]["target"] == {
            "kind": "ratio",
            "value": 3.0,
            "achieved": done["metrics"]["ratio"],
            "within_tolerance": True,
        }
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert doc["request"]["target"] == {"kind": "ratio", "value": 3.0, "seconds": None}
        assert doc["metrics"]["target"] == done["metrics"]["target"]
        assert doc["run"]["stopped_because"] == "target"

        body = job_body(target={"kind": "duration", "seconds": 600}, max_iterations=10**6)
        _, events = await finished(client, body)
        done = events[-1]["data"]
        assert done["stopped_because"] == "target"
        assert done["metrics"]["target"]["kind"] == "duration"
        assert abs(done["metrics"]["target"]["achieved"] / 600 - 1) <= 0.02

        _, events = await finished(client, job_body())  # max: a null target
        assert events[-1]["data"]["metrics"]["target"] is None
        doc = (await client.get(f"/routes/{events[-1]['data']['route_id']}")).json()
        assert doc["metrics"]["target"] is None


async def test_a_stored_route_through_a_parallel_edge_comes_as_a_snapshot(tmp_path: Path) -> None:
    # 0 -> 1 is two edges, 100 m and 300 m, then 1 -> 2 (100 m): the baseline is 200 m and a
    # 1.45 × target (284-296 m) lies between the only two routes, 200 m and 400 m, which pass
    # through the same nodes. The run overshoots to 400 m, cannot shrink back into the band and
    # stores the closer 200 m route, which the client has not drawn last
    data = tmp_path / "line"
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0)]
    line = make_graph(xy, [Edge(0, 1, 100.0), Edge(0, 1, 300.0), Edge(1, 2, 100.0)])
    save_build(data, line, "line")
    async with serving(data) as (client, _):
        ends = (exactly_at_node(line, 0), exactly_at_node(line, 2))
        stops = [{"lat": lat, "lon": lon} for lat, lon in ends]
        body = {
            "region": "portland",
            "stops": stops,
            "seed": 1,
            "target": {"kind": "ratio", "value": 1.45},
            "max_iterations": 10**6,
        }
        job_id, events = await finished(client, body)
        assert [e["event"] for e in events][-3:] == ["progress", "snapshot", "done"]
        assert events[-3]["data"]["metrics"]["length_m"] == 400.0
        assert events[-2]["data"]["metrics"]["length_m"] == 200.0
        done = events[-1]["data"]
        assert done["stopped_because"] == "converged" and done["metrics"]["length_m"] == 200.0
        status = (await client.get(f"/jobs/{job_id}")).json()
        assert status["metrics"] == {k: done["metrics"][k] for k in status["metrics"]}
        assert status["snapshot"]["metrics"]["length_m"] == 200.0
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert doc["metrics"]["length_m"] == 200.0 and doc["metrics"]["target"]["achieved"] == 1.0


async def test_a_stored_route_is_drawn_along_its_build_s_geometry(tmp_path: Path) -> None:
    # 0 -> 1 -> 2 on a line, the road 0 - 1 bending 40 m north half-way: the route's document,
    # its GeoJSON and its share image draw the bend, and without the geometry it is unavailable
    data = tmp_path / "bent"
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0)]
    line = make_graph(xy, [Edge(a, b, 100.0) for a, b in ((0, 1), (1, 0), (1, 2), (2, 1))])
    shapes = [[0, (50, 40), 1], [1, (50, 40), 0], [1, 2], [2, 1]]  # in edge id order
    points = [xy[p] if isinstance(p, int) else (X0 + p[0], Y0 + p[1]) for s in shapes for p in s]
    indptr = np.concatenate(([0], np.cumsum([len(s) for s in shapes]))).astype(np.int64)
    bent = encode(line, np.array(points, dtype=np.float64), indptr)
    save_build(data, line, "bent", geometry=bent)
    async with serving(data) as (client, _):
        ends = (exactly_at_node(line, 0), exactly_at_node(line, 2))
        body = {"region": "portland", "stops": [{"lat": a, "lon": b} for a, b in ends], "seed": 1}
        _, events = await finished(client, body)
        route_id = events[-1]["data"]["route_id"]
        doc = (await client.get(f"/routes/{route_id}")).json()
        drawn = doc["geometry"]["coordinates"]
        assert len(drawn) == 4 and drawn == line_coordinates(line, bent, edges_of(line, [0, 1, 2]))
        assert drawn[1][1] > drawn[0][1] + 0.0003  # 40 m north: 0.00036 degrees
        geojson = (await client.get(f"/routes/{route_id}/geojson")).json()
        assert geojson["features"][0]["geometry"]["coordinates"] == drawn
        assert (await client.get(f"/routes/{route_id}/share.png")).status_code == 200
    save_build(data, line, "bare", latest=False)
    shutil.rmtree(data / "graphs" / "portland" / "bare" / "geometry")
    with pytest.raises(routes_api.ApiError) as missing:
        routes_api.geometry_of(load_build(data, "portland", "bare", backend="python"))
    assert missing.value.status == 409 and missing.value.body.code == "BUILD_UNAVAILABLE"


async def test_a_ratio_target_for_a_round_trip_is_refused_first(data: Path) -> None:
    async with serving(data) as (client, _):
        loop = [stop(45), stop(45)]
        for body in (
            job_body(stops=loop, target={"kind": "ratio", "value": 3}),
            job_body(stops=loop, target={"kind": "ratio", "value": 3}, region="atlantis"),
        ):
            r = await client.post("/jobs", json=body)
            assert r.status_code == 422 and r.json() == {
                "code": "INVALID_REQUEST",
                "message": "a round trip has no ratio target: its baseline is 0 m",
                "field": "target",
            }
        _, events = await finished(client, job_body(stops=loop))
        again = {
            "region": "portland",
            "base_route_id": events[-1]["data"]["route_id"],
            "target": {"kind": "ratio", "value": 3},
        }
        r = await client.post("/jobs", json=again)
        assert r.status_code == 422 and r.json()["field"] == "target"


async def test_a_body_with_infinity_or_nan_is_refused(data: Path) -> None:
    async with serving(data) as (client, _):
        for number in ("Infinity", "-Infinity", "NaN"):
            text = json.dumps(job_body()).replace('"budget_s": 60', f'"budget_s": {number}')
            headers = {"content-type": "application/json"}
            r = await client.post("/jobs", content=text, headers=headers)
            assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST", r.text
            assert r.json()["message"].startswith("body.budget_s: ")
            assert "input" not in r.json()["errors"][0]
            assert r.json()["field"] == "budget_s"
        # a body cut short: its error lies at a character, not in a field
        text = json.dumps(job_body())[:33]
        r = await client.post("/jobs", content=text, headers={"content-type": "application/json"})
        assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST", r.text
        assert r.json()["errors"][0]["loc"] == ["body", 33] and "field" not in r.json()


async def test_make_it_worse_with_a_smaller_target_shrinks_the_route(data: Path) -> None:
    async with serving(data) as (client, _):
        body = job_body(stops=[stop(0), stop(99)], max_iterations=10**6)
        _, events = await finished(client, body)
        worst = events[-1]["data"]
        assert worst["stopped_because"] == "converged" and worst["metrics"]["ratio"] > 5
        again = {
            "region": "portland",
            "base_route_id": worst["route_id"],
            "seed": 2,
            "target": {"kind": "ratio", "value": 4},
            "max_iterations": 10**6,
        }
        _, events = await finished(client, again)
        done = events[-1]["data"]
        assert done["stopped_because"] == "target" and done["metrics"]["target"]["within_tolerance"]
        lengths = [e["data"]["metrics"]["length_m"] for e in events if e["event"] == "progress"]
        assert (
            lengths == sorted(lengths, reverse=True) and lengths[0] < worst["metrics"]["length_m"]
        )


async def test_the_time_ratio_and_the_arrival_count_the_delays(tmp_path: Path) -> None:
    # a lattice with signals and stop signs on its primary rows and columns, which the route
    # and its baseline both pass
    root = tmp_path / "data"
    save_build(root, SLOW, "slow")
    async with serving(root) as (client, app):
        body = job_body(stops=[stop(22), stop(77)], departure="2026-10-31T09:00:00-07:00")
        _, events = await finished(client, body)
        done = events[-1]["data"]
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        m = doc["metrics"]
        assert m["delay_s"] > 0 and m["baseline_delay_s"] > 0
        experienced = (m["time_s"] + m["delay_s"]) / (m["baseline_time_s"] + m["baseline_delay_s"])
        assert m["time_ratio"] == pytest.approx(experienced)
        assert done["metrics"]["time_ratio"] == m["time_ratio"]
        trip = datetime.fromisoformat(m["arrival"]) - datetime.fromisoformat(m["departure"])
        assert trip.total_seconds() == pytest.approx(m["time_s"] + m["delay_s"])
        api: Api = app.state.api
        stored = api.store.get(done["route_id"])
        assert stored is not None
        assert stored.metrics_json["baseline_delay_s"] == m["baseline_delay_s"]
        # a route stored before the documents carried the baseline's delays has them too
        older = {k: v for k, v in stored.metrics_json.items() if k != "baseline_delay_s"}
        with sqlite3.connect(api.store.path) as con:
            con.execute(
                "UPDATE routes SET metrics_json = ? WHERE id = ?",
                (json.dumps(older), done["route_id"]),
            )
        assert (await client.get(f"/routes/{done['route_id']}")).json()["metrics"] == m


async def test_the_route_s_delays_ride_with_it_as_it_grows(tmp_path: Path) -> None:
    # on the lattice with signals and stop signs: every snapshot and progress event carries the
    # route's delays so far, the last progress the stored route's, and so does the job status
    root = tmp_path / "data"
    save_build(root, SLOW, "slow")
    async with serving(root) as (client, _):
        job_id, events = await finished(client, job_body(stops=[stop(22), stop(77)]))
        growing = [e["data"]["metrics"] for e in events if e["event"] in ("snapshot", "progress")]
        assert growing and all(m["delay_s"] >= 0 for m in growing)
        assert len({m["delay_s"] for m in growing}) > 1  # it changes as the route grows
        done = events[-1]["data"]["metrics"]
        last = [e["data"]["metrics"] for e in events if e["event"] == "progress"][-1]
        assert last["delay_s"] == done["delay_s"] > 0 and last["length_m"] == done["length_m"]
        status = (await client.get(f"/jobs/{job_id}")).json()
        assert status["metrics"]["delay_s"] == done["delay_s"]


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
        ({"constraints": {"no_left": True, "no_right": True}}, "INVALID_REQUEST"),
        ({"constraints": {"no_u_turns": True}}, "INVALID_REQUEST"),
        ({"constraints": {"strict": True}}, "INVALID_REQUEST"),
        ({"objective": "nonsense"}, "INVALID_REQUEST"),
        ({"region": "atlantis"}, "UNKNOWN_REGION"),
        ({"objective": "alphabet"}, "INVALID_REQUEST"),  # a via (job_body's stop 45)
        ({"objective": "brand"}, "INVALID_REQUEST"),
        ({"brand": 0}, "INVALID_REQUEST"),
        ({"objective": "brand", "brand": -1}, "INVALID_REQUEST"),
        ({"objective": "brand", "brand": 0}, "INVALID_REQUEST"),  # the lattice has no brands
        (
            {"objective": "brand", "brand": 0, "target": {"kind": "ratio", "value": 3}},
            "INVALID_REQUEST",
        ),
        ({"objective": "collect"}, "INVALID_REQUEST"),
        ({"objective": "collect", "item": "ufo"}, "INVALID_REQUEST"),
        ({"item": "bridge"}, "INVALID_REQUEST"),
        (
            {"objective": "collect", "item": "bridge", "target": {"kind": "ratio", "value": 3}},
            "INVALID_REQUEST",
        ),
        ({"target": {"kind": "ratio", "value": 1}}, "INVALID_REQUEST"),
        ({"target": {"kind": "ratio", "seconds": 600}}, "INVALID_REQUEST"),
        ({"target": {"kind": "duration", "seconds": 8 * 24 * 3600}}, "INVALID_REQUEST"),
        ({"target": {"kind": "ratio", "value": 3}, "stops": [stop(0), stop(0)]}, "INVALID_REQUEST"),
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


async def test_an_item_or_target_that_does_not_fit_the_objective_names_its_field(
    data: Path,
) -> None:
    async with serving(data) as (client, _):
        for fields, field in (
            ({"objective": "collect"}, "item"),
            ({"item": "roundabout"}, "item"),
            ({"objective": "brand"}, "brand"),
            ({"brand": 0}, "brand"),
            ({"objective": "brand", "brand": 0, "target": {"kind": "ratio", "value": 3}}, "target"),
            (
                {"objective": "collect", "item": "bridge", "target": {"kind": "ratio", "value": 3}},
                "target",
            ),
            ({"objective": "alphabet"}, "stops"),
            (
                {
                    "objective": "alphabet",
                    "stops": [stop(0), stop(99)],
                    "target": {"kind": "duration", "seconds": 600},
                },
                "target",
            ),  # fmt: skip
            ({"objective": "alphabet", "base_route_id": "000000000000"}, "base_route_id"),
        ):
            r = await client.post("/jobs", json=job_body(**fields))
            assert r.status_code == 422 and r.json()["field"] == field, r.text


async def test_a_collecting_job_counts_its_item_as_it_grows(tmp_path: Path) -> None:
    roads = {(a, a + 1): {"flags": EdgeFlag.BRIDGE} for a in range(10, 90, 7) if a % 10 < 9}
    root = tmp_path / "data"  # the same lattice as GRID, with bridges off its bottom row
    save_build(root, lattice(10, 10, roads=roads), "bridged", bbox=GRID_BBOX)
    async with serving(root) as (client, _):
        body = job_body(objective="collect", item="bridge", stops=[stop(0), stop(9)])
        _, events = await finished(client, body)
        progress = [e["data"]["metrics"] for e in events if e["event"] == "progress"]
        assert progress and all(m["item"] == "bridge" for m in progress)
        counts = [m["items"]["bridge"] for m in progress]
        assert counts == sorted(counts) and counts[-1] > 0
        done = events[-1]["data"]
        assert done["metrics"]["item"] == "bridge" and done["metrics"]["items"]["bridge"] > 0
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert doc["request"]["item"] == "bridge"
        assert doc["metrics"]["items"] == done["metrics"]["items"]

        _, events = await finished(client, job_body(stops=[stop(0), stop(9)]))  # distance
        done = events[-1]["data"]
        assert "item" not in done["metrics"] and done["metrics"]["items"]["toll_booth"] == 0
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert doc["metrics"]["items"] == done["metrics"]["items"]


# Brands of the GRID lattice, off its bottom row: ids in name order, 0 Acme, 1 Pilgrim Coffee,
# 2 Zed Mart.
BRANDS = {"Pilgrim Coffee": [12, 27, 45, 63, 78, 86], "Acme": [55, 95], "Zed Mart": [33, 66]}


def branded(tmp_path: Path) -> Path:
    """A data directory whose latest build is GRID's lattice with BRANDS."""
    root = tmp_path / "data"
    g = lattice(10, 10, brands=BRANDS)
    save_build(root, g, "branded", bbox=GRID_BBOX)
    return root


async def test_brands_lists_the_region_s_brands_the_most_located_first(tmp_path: Path) -> None:
    async with serving(branded(tmp_path)) as (client, _):
        r = await client.get("/brands", params={"region": "portland"})
        assert r.status_code == 200 and r.json() == {
            "region": "portland",
            "brands": [
                {"id": 1, "name": "Pilgrim Coffee", "wikidata": None, "locations": 6},
                {"id": 0, "name": "Acme", "wikidata": None, "locations": 2},
                {"id": 2, "name": "Zed Mart", "wikidata": None, "locations": 2},
            ],
        }
        unknown = await client.get("/brands", params={"region": "atlantis"})
        assert unknown.status_code == 422 and unknown.json()["code"] == "UNKNOWN_REGION"
        missing = await client.get("/brands")
        assert missing.status_code == 422 and missing.json()["code"] == "INVALID_REQUEST"


async def test_a_pilgrimage_counts_its_visits_as_it_grows(tmp_path: Path) -> None:
    async with serving(branded(tmp_path)) as (client, _):
        body = job_body(objective="brand", brand=1, stops=[stop(0), stop(9)])
        _, events = await finished(client, body)
        progress = [e["data"]["metrics"]["brand"] for e in events if e["event"] == "progress"]
        assert progress and all(
            (b["id"], b["name"], b["of"]) == (1, "Pilgrim Coffee", 6) for b in progress
        )
        visits = [b["visits"] for b in progress]
        assert visits == sorted(visits) and visits[-1] > 0
        done = events[-1]["data"]
        assert done["metrics"]["brand"] == progress[-1]
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert (doc["request"]["objective"], doc["request"]["brand"]) == ("brand", 1)
        assert doc["metrics"]["brand"] == done["metrics"]["brand"]
        report = (await client.post(f"/routes/{done['route_id']}/validate")).json()
        assert report["ok"] is True

        _, events = await finished(client, job_body(stops=[stop(0), stop(9)]))  # distance
        done = events[-1]["data"]
        assert "brand" not in done["metrics"]
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert doc["metrics"]["brand"] is None and doc["request"]["brand"] is None


async def test_a_brand_the_build_lacks_or_the_area_does_not_hold_names_its_field(
    tmp_path: Path,
) -> None:
    async with serving(branded(tmp_path)) as (client, _):
        for fields, message in (
            ({"brand": 3}, "unknown brand 3; GET /brands?region=portland lists the brands"),
            (  # a 50 m leash along the bottom row holds none of its nodes
                {"brand": 2, "leash_km": 0.05, "stops": [stop(0), stop(2)]},
                "no Zed Mart within reach",
            ),
        ):
            r = await client.post("/jobs", json=job_body(objective="brand", **fields))
            assert r.status_code == 422, r.text
            assert r.json() == {"code": "INVALID_REQUEST", "message": message, "field": "brand"}


def lettered(tmp_path: Path) -> Path:
    """A data directory whose latest build is GRID's lattice with its rows named after trees, A
    (south) to J, and its columns numbered avenues."""
    root = tmp_path / "data"
    names = named_roads(
        10, 10, [f"{t} Street" for t in TREES[:10]], [avenue(k) for k in range(1, 11)]
    )
    save_build(root, lattice(10, 10, roads=names), "lettered", bbox=GRID_BBOX)
    return root


async def test_an_alphabet_job_sends_each_better_route_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with serving(lettered(tmp_path)) as (client, app):
        body = job_body(objective="alphabet", stops=[stop(0), stop(99)], max_iterations=20)
        _, events = await finished(client, body)
        names = [e["event"] for e in events]
        assert names[:3] == ["snapped", "baseline", "snapshot"] and names[-1] == "done"
        # after the seed's snapshot, each better route: a snapshot, then a progress without splices
        assert names[3:-1] == ["snapshot", "progress"] * ((len(names) - 4) // 2)
        progress = [e["data"] for e in events if e["event"] == "progress"]
        assert progress and all(p["splices"] == [] for p in progress)
        letters = [p["metrics"]["alphabet"]["letters"] for p in progress]
        assert letters == sorted(letters) and letters[-1] == 10
        scores = [p["score"] for p in progress]
        assert scores == sorted(scores) and len(set(scores)) == len(scores)
        snapshots = [e["data"] for e in events if e["event"] == "snapshot"][1:]
        assert [s["metrics"] for s in snapshots] == [p["metrics"] for p in progress]
        done = events[-1]["data"]
        assert done["metrics"]["alphabet"] == progress[-1]["metrics"]["alphabet"]
        assert done["metrics"]["alphabet"]["sequence"] == "ABCDEFGHIJ"
        made = []
        real = routes_api.edge_letters
        monkeypatch.setattr(routes_api, "edge_letters", lambda g: made.append(1) or real(g))
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert doc["request"]["objective"] == "alphabet"
        assert doc["metrics"]["alphabet"] == done["metrics"]["alphabet"]
        again = (await client.get(f"/routes/{done['route_id']}")).json()
        assert again["metrics"]["alphabet"] == doc["metrics"]["alphabet"]
        assert made == [1]  # the build's letters, worked out once (`Api.letters`)
        assert list(app.state.api.letters) == [doc["graph_version"]]
        assert doc["run"]["stopped_because"] == "budget" and doc["run"]["attempts"] == 20
        report = (await client.post(f"/routes/{done['route_id']}/validate")).json()
        assert report["ok"] is True and report["checks"][-1]["name"] == "alphabet"

        _, events = await finished(client, job_body(stops=[stop(0), stop(99)]))  # distance
        doc = (await client.get(f"/routes/{events[-1]['data']['route_id']}")).json()
        assert (
            doc["metrics"]["alphabet"] is None and "alphabet" not in events[-1]["data"]["metrics"]
        )


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


async def test_a_leg_a_strict_constraint_cannot_drive_names_the_constraint(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def infeasible(*args: object) -> None:
        strict = Constraints(no_left=True, strict=True)
        raise strict.unreachable((0, 1), "after moving stop 1 up to 10 times")

    monkeypatch.setattr(routes_api, "prepare", infeasible)
    async with serving(data) as (client, _):
        r = await client.post("/jobs", json=job_body(constraints={"no_left": True, "strict": True}))
        assert r.status_code == 422 and r.json() == {
            "code": "INFEASIBLE_TURN_CONSTRAINT",
            "message": "no route from stop 0 to stop 1 without repeating a road, after moving "
            "stop 1 up to 10 times, under the strict no-left constraint; try without strict",
            "leg": [0, 1],
            "stop_index": 1,
            "constraint": "no_left",
        }


async def test_a_leg_no_route_covers_without_the_constraint_either_is_not_its_fault(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    save_build(root, two_rings(), "rings")
    async with serving(root) as (client, _):
        stops = [{"lat": lat, "lon": lon} for lat, lon in (at(300.0, 0.0), at(2300.0, 0.0))]
        for constraints in ({}, {"no_left": True, "strict": True}):
            r = await client.post("/jobs", json=job_body(stops=stops, constraints=constraints))
            assert r.status_code == 422, r.text
            assert {k: r.json()[k] for k in ("code", "leg", "stop_index")} == {
                "code": "STOP_UNREACHABLE_WITHOUT_REPEAT",
                "leg": [0, 1],
                "stop_index": 1,
            }
            assert "constraint" not in r.json() and "strict" not in r.json()["message"]


async def test_strict_without_a_constraint_is_refused_on_its_field(data: Path) -> None:
    async with serving(data) as (client, _):
        for constraints, why in (
            ({"strict": True}, "strict needs no_left or no_right"),
            ({"no_left": True, "no_right": True}, "straight only is not a route"),
        ):
            r = await client.post("/jobs", json=job_body(constraints=constraints))
            assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"
            assert r.json()["field"] == "constraints" and why in r.json()["message"]


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


async def test_cancel_stores_the_best_alphabet_route_so_far(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch, delay_s=0.2)
    async with serving(lettered(tmp_path)) as (client, app):
        body = job_body(objective="alphabet", stops=[stop(0), stop(99)], max_iterations=1000)
        created = (await client.post("/jobs", json=body)).json()
        job = app.state.api.jobs.jobs[created["job_id"]]
        await until(lambda: job.accepted >= 3)
        status = (await client.delete(f"/jobs/{created['job_id']}")).json()
        assert status["stopped_because"] == "cancelled"
        doc = (await client.get(f"/routes/{status['route_id']}")).json()
        assert 3 <= doc["metrics"]["alphabet"]["letters"] < 10
        report = (await client.post(f"/routes/{status['route_id']}/validate")).json()
        assert report["ok"] is True and report["checks"][-1]["name"] == "alphabet"


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


async def test_a_time_job_under_a_turn_constraint_reports_its_turns(data: Path) -> None:
    async with serving(data) as (client, _):
        body = job_body(stops=[stop(22), stop(77)], objective="time", constraints={"no_left": True})
        _, events = await finished(client, body)
        done = events[-1]["data"]
        assert events[-1]["event"] == "done"
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert doc["request"]["objective"] == "time"
        assert doc["request"]["constraints"] == {
            "no_left": True,
            "no_right": False,
            "strict": False,
        }
        turns, metrics = doc["metrics"]["turns"], doc["metrics"]
        assert set(turns) == {"left", "right", "straight", "u_turn"}
        assert sum(turns.values()) == len(doc["geometry"]["coordinates"]) - 2
        assert metrics["delay_s"] == 0.0  # the lattice has no signals
        assert done["metrics"]["turns"] == turns and done["metrics"]["delay_s"] == 0.0
        assert (await client.post(f"/routes/{done['route_id']}/validate")).json()["ok"] is True


async def test_a_strict_constraint_refuses_a_base_that_breaks_it(data: Path) -> None:
    async with serving(data) as (client, _):
        # a trip a strict no-left seed can plan, so the pre-flight passes and the base is judged
        body = job_body(stops=[stop(22), stop(77)], max_iterations=60)
        _, events = await finished(client, body)
        base_id = events[-1]["data"]["route_id"]
        doc = (await client.get(f"/routes/{base_id}")).json()
        assert doc["metrics"]["turns"]["left"] > 0
        worse = {"region": "portland", "base_route_id": base_id, "seed": 4, "max_iterations": 50}
        strict = {**worse, "constraints": {"no_left": True, "strict": True}}
        r = await client.post("/jobs", json=strict)
        assert r.status_code == 422 and r.json()["code"] == "BASE_ROUTE_MISMATCH"
        assert r.json()["field"] == "route" and "strict no-left" in r.json()["message"]
        soft = {**worse, "constraints": {"no_left": True}}
        assert (await client.post("/jobs", json=soft)).status_code == 202


async def test_a_hard_mode_job_keeps_each_road_one_stretch(tmp_path: Path) -> None:
    root = tmp_path / "data"  # GRID's lattice with its rows and columns named
    named = lattice(10, 10, named=True)
    save_build(root, named, "named", bbox=GRID_BBOX)
    async with serving(root) as (client, app):
        trip = {"stops": [stop(22), stop(77)], "max_iterations": 200}
        _, events = await finished(client, job_body(**trip))
        base_id = events[-1]["data"]["route_id"]
        base = app.state.api.store.get(base_id)
        assert returning(named, base.edges) is not None  # without hard mode it comes back

        _, events = await finished(client, job_body(**trip, constraints={"hard": True}))
        done = events[-1]["data"]
        assert events[-1]["event"] == "done"
        stored = app.state.api.store.get(done["route_id"])
        assert returning(named, stored.edges) is None
        doc = (await client.get(f"/routes/{done['route_id']}")).json()
        assert doc["request"]["constraints"] == {
            "no_left": False,
            "no_right": False,
            "strict": False,
            "hard": True,
        }
        assert (await client.post(f"/routes/{done['route_id']}/validate")).json()["ok"] is True

        worse = {"region": "portland", "base_route_id": base_id, "seed": 4, "max_iterations": 50}
        r = await client.post("/jobs", json={**worse, "constraints": {"hard": True}})
        assert r.status_code == 422 and r.json()["code"] == "BASE_ROUTE_MISMATCH"
        assert r.json()["field"] == "route" and "hard mode refuses" in r.json()["message"]
        assert (await client.post("/jobs", json=worse)).status_code == 202


async def test_a_leg_hard_mode_cannot_drive_says_so(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def infeasible(*args: object) -> None:
        raise Constraints(hard=True).unreachable((0, 1), "after moving stop 1 up to 10 times")

    monkeypatch.setattr(routes_api, "prepare", infeasible)
    async with serving(data) as (client, _):
        r = await client.post("/jobs", json=job_body(constraints={"hard": True}))
        assert r.status_code == 422 and r.json() == {
            "code": "STOP_UNREACHABLE_WITHOUT_REPEAT",
            "message": "the seed found no way from stop 0 to stop 1 under hard mode, after "
            "moving stop 1 up to 10 times; try without hard",
            "leg": [0, 1],
            "stop_index": 1,
        }
