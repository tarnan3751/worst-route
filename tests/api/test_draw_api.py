"""`POST /draw` and `POST /draw/finish` over httpx in process, on the drawing board (a 5 × 5
lattice with one-ways, twins and a straight-only junction at node 12): the start's legal ways
out, the steps, each refusal with its code and edge, a finished drawing stored under its content
hash with its document, directions, share image and validation, continued by make it worse, the
stops a job resolves, the request's refusals, the length cap, the shared quota and the memo."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from worst_route.api import draw_api
from worst_route.api.app import create_app
from worst_route.api.draw_api import DrawContexts
from worst_route.api.jobs import JobSettings
from worst_route.api.store import route_id
from worst_route.config import MAX_DRAW_EDGES
from worst_route.graph.testing import edge_id

from ..engine.graphs import at_node, board
from .support import PORTLAND, api_settings, save_build

pytestmark = pytest.mark.anyio

BOARD = board()
BBOX = [
    round(float(BOARD.node_lon.min()) - 0.1, 7),
    round(float(BOARD.node_lat.min()) - 0.1, 7),
    round(float(BOARD.node_lon.max()) + 0.1, 7),
    round(float(BOARD.node_lat.max()) + 0.1, 7),
]


@pytest.fixture
def drawn(tmp_path: Path) -> Path:
    """A data directory holding the board as portland's latest build."""
    root = tmp_path / "data"
    save_build(root, BOARD, "board", bbox=BBOX)
    return root


@asynccontextmanager
async def serving(data: Path, **settings: Any) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    jobs = JobSettings(inline_workers=True, coalesce_s=0.0)
    config = api_settings(
        data, builds=PORTLAND, backend="python", jobs=jobs, geocoder="none", **settings
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


def stop(v: int, label: str | None = None) -> dict[str, Any]:
    lat, lon = at_node(BOARD, v)
    return {"lat": lat, "lon": lon, "label": label}


def request(*stops: int, **fields: Any) -> dict[str, Any]:
    return {"region": "portland", "stops": [stop(v) for v in stops], **fields}


def path(nodes: list[int]) -> list[int]:
    return [edge_id(BOARD, u, v) for u, v in zip(nodes[:-1], nodes[1:], strict=True)]


async def drew(client: httpx.AsyncClient, req: dict[str, Any], edges: list[int]) -> dict[str, Any]:
    answer = await client.post("/draw", json={"request": req, "path": edges})
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


async def refused(
    client: httpx.AsyncClient, req: dict[str, Any], edges: list[int], url: str = "/draw"
) -> dict[str, Any]:
    answer = await client.post(url, json={"request": req, "path": edges})
    assert answer.status_code == 422, answer.text
    body: dict[str, Any] = answer.json()
    return body


def point(v: int) -> list[float]:
    return [round(float(BOARD.node_lon[v]), 7), round(float(BOARD.node_lat[v]), 7)]


def event(stream: str, name: str) -> dict[str, Any]:
    """The data of the first event `name` of an SSE body."""
    for block in stream.replace("\r\n", "\n").split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if lines.get("event") == name:
            data: dict[str, Any] = json.loads(lines["data"])
            return data
    raise AssertionError(f"no {name} event")


def legal_ways_out(v: int) -> list[int]:
    """The out-edges of v by hand: every edge leaving it, the board having no bans, every node
    in the area, and no stop but the end beside the start here."""
    return [int(e) for e in BOARD.out_edges[BOARD.out_indptr[v] : BOARD.out_indptr[v + 1]]]


async def test_an_empty_path_is_the_start_with_its_legal_ways_out(drawn: Path) -> None:
    async with serving(drawn) as (client, _):
        body = await drew(client, request(6, 18), [])
    assert [s["node"] for s in body["stops"]] == [6, 18]
    assert body["at"] == {"node": 6, "lat": body["stops"][0]["lat"], "lon": body["stops"][0]["lon"]}
    assert (body["reached"], body["legal"], body["done"]) == ([0], True, False)
    assert (body["length_m"], body["ratio"], body["geometry"]) == (0.0, None, None)
    assert body["baseline_length_m"] == pytest.approx(400.0)
    # 6 → 5 does not exist: row 1 runs east
    assert [m["edge"] for m in body["next"]] == legal_ways_out(6)
    assert sorted(m["node"] for m in body["next"]) == [1, 7, 11]
    east = next(m for m in body["next"] if m["node"] == 7)
    assert (east["name"], east["length_m"], east["turn"], east["stop"]) == (
        "Row 1",
        100.0,
        None,
        None,
    )
    assert east["coords"] == [point(6), point(7)]
    assert (east["lat"], east["lon"]) == (east["coords"][1][1], east["coords"][1][0])


async def test_each_step_draws_on_with_its_turns_and_stops(drawn: Path) -> None:
    req = request(0, 2, 22)
    async with serving(drawn) as (client, _):
        first = await drew(client, req, path([0, 1]))
        assert first["at"]["node"] == 1 and first["length_m"] == pytest.approx(100.0)
        turns = {m["node"]: (m["turn"], m["stop"]) for m in first["next"]}
        assert turns == {2: ("straight", 1), 6: ("left", None)}  # 0 is passed
        assert first["geometry"] == {"type": "LineString", "coordinates": [point(0), point(1)]}
        on = await drew(client, req, path([0, 1, 2, 7, 12, 17]))
        assert (on["reached"], on["done"], on["ratio"]) == ([0, 2], False, None)
        assert [(m["node"], m["stop"]) for m in on["next"]] == [(16, None), (18, None), (22, 2)]
        done = await drew(client, req, path([0, 1, 2, 7, 12, 17, 22]))
    assert (done["reached"], done["done"], done["next"]) == ([0, 2, 6], True, [])
    assert done["ratio"] == pytest.approx(600.0 / done["baseline_length_m"])
    assert len(done["geometry"]["coordinates"]) == 7


async def test_each_refusal_names_its_code_and_first_illegal_edge(drawn: Path) -> None:
    async with serving(drawn) as (client, _):
        cases = [
            (request(0, 24), path([0, 1, 6, 11, 10, 5, 6]), "NODE_REUSED", 5),
            (request(13, 24), path([13, 12, 11, 16, 17]), "EDGE_BANNED", 3),  # 12 → 11's twin
            (request(0, 24), path([0, 1, 2, 7, 12, 11]), "TRANSITION_FORBIDDEN", 4),
            (request(0, 4, 2), path([0, 1, 2]), "STOP_SKIPPED", 1),  # the end before stop 1
            (request(5, 24), path([5, 6, 7]) + path([6, 7]), "BROKEN_CHAIN", 2),  # 7 → 6: one-way
            (request(0, 2), path([0, 1, 2, 7]), "PAST_END", 2),
            (request(0, 24), path([0, 1]) + [BOARD.m], "BROKEN_CHAIN", 1),
        ]
        for req, edges, code, index in cases:
            body = await refused(client, req, edges)
            assert (body["code"], body["refusal"], body["index"]) == ("ILLEGAL_PATH", code, index)
            assert f"(edge {index} of the path)" in body["message"]
            await drew(client, req, edges[:index])  # the path before it is legal
        strict = request(0, 24, constraints={"no_left": True, "strict": True})
        body = await refused(client, strict, path([0, 1, 6]))
        assert (body["refusal"], body["index"]) == ("TURN_REFUSED", 1)
        hard = request(0, 24, constraints={"hard": True})
        body = await refused(client, hard, path([0, 1, 6, 7, 2, 3]))
        assert (body["refusal"], body["index"]) == ("ROAD_AGAIN", 4)
        # soft: a left turn is a move like any other
        soft = request(0, 24, constraints={"no_left": True})
        assert (await drew(client, soft, path([0, 1, 6])))["at"]["node"] == 6


async def test_a_finished_drawing_is_stored_as_a_route_drawn_by_hand(drawn: Path) -> None:
    labelled = {**request(0, 24), "stops": [stop(0, "home"), stop(24, "work")], "seed": 11}
    drawn_path = path([0, 1, 6, 11, 10, 15, 16, 17, 18, 19, 24])
    async with serving(drawn) as (client, app):
        finished = await client.post("/draw/finish", json={"request": labelled, "path": drawn_path})
        assert finished.status_code == 200, finished.text
        body = finished.json()
        version = app.state.api.builds["portland"].data.meta["graph_version"]
        nodes = [0, 1, 6, 11, 10, 15, 16, 17, 18, 19, 24]
        assert body["route_id"] == route_id(version, nodes, drawn_path, [0, 10])
        assert body["ratio"] == pytest.approx(1000.0 / 800.0)
        rid = body["route_id"]
        doc = (await client.get(f"/routes/{rid}")).json()
        assert doc["run"] == {
            "attempts": 10,
            "accepted": 10,
            "stopped_because": "drawn",
            "elapsed_s": 0.0,
            "backend": "human",
        }
        assert doc["seed"] == 11 and doc["request"]["seed"] == 11
        assert [s["label"] for s in doc["stops"]] == ["home", "work"]
        assert doc["metrics"]["ratio"] == pytest.approx(1.25)
        assert doc["directions"][-1]["text"] == "Arrive at work"
        validated = (await client.post(f"/routes/{rid}/validate")).json()
        assert validated["ok"] is True and all(c["ok"] for c in validated["checks"])
        assert (await client.get(f"/routes/{rid}/directions")).status_code == 200
        image = await client.get(f"/routes/{rid}/share.png")
        assert (image.status_code, image.headers["content-type"]) == (200, "image/png")
        again = await client.post("/draw/finish", json={"request": labelled, "path": drawn_path})
        assert again.json()["route_id"] == rid  # the same route, the same id
        worse = await client.post(
            "/jobs",
            json={"region": "portland", "base_route_id": rid, "seed": 4, "max_iterations": 50},
        )
        assert worse.status_code == 202, worse.text
        done = event((await client.get(worse.json()["events_url"])).text, "done")
        assert done["metrics"]["length_m"] >= 1000.0


async def test_a_finished_drawing_without_a_seed_gets_one(drawn: Path) -> None:
    async with serving(drawn) as (client, _):
        body = {"request": request(0, 0), "path": path([0, 5, 6, 1, 0])}
        finished = (await client.post("/draw/finish", json=body)).json()
        assert finished["ratio"] is None  # a round trip
        doc = (await client.get(f"/routes/{finished['route_id']}")).json()
        assert 0 <= doc["seed"] < 2**31 and doc["request"]["seed"] == doc["seed"]


async def test_an_unfinished_drawing_is_not_stored(drawn: Path) -> None:
    async with serving(drawn) as (client, _):
        body = await refused(client, request(0, 2, 24), path([0, 1, 2, 3]), "/draw/finish")
        assert body == {
            "code": "DRAWING_UNFINISHED",
            "message": "the drawing has not reached the end: 2 of 3 stops reached",
            "reached": 2,
        }
        body = await refused(client, request(0, 24), path([0, 1, 0]), "/draw/finish")
        assert (body["code"], body["refusal"], body["index"]) == ("ILLEGAL_PATH", "NODE_REUSED", 1)


async def test_the_stops_are_those_a_job_resolves(drawn: Path) -> None:
    req = {**request(0, 12, 24), "seed": 3, "max_iterations": 20}
    async with serving(drawn) as (client, _):
        drawing = await drew(client, req, [])
        job = (await client.post("/jobs", json=req)).json()
        snapped = event((await client.get(job["events_url"])).text, "snapped")
    assert drawing["stops"] == snapped["stops"]


async def test_what_a_drawing_cannot_ask_is_refused(drawn: Path) -> None:
    async with serving(drawn) as (client, _):
        for fields, field in [
            ({"objective": "time"}, "objective"),
            ({"objective": "nonsense"}, "objective"),
            ({"target": {"kind": "ratio", "value": 5}}, "target"),
            ({"reorder": True}, "reorder"),
            ({"base_route_id": "abcdefabcdef"}, "base_route_id"),
            ({"portfolio": 2}, "portfolio"),
            ({"certified": True}, "certified"),
            ({"item": "bridge"}, "item"),
        ]:
            body = await refused(client, request(0, 24, **fields), [])
            assert (body["code"], body["field"]) == ("INVALID_REQUEST", field), body
        unknown = await refused(client, {**request(0, 24), "region": "mars"}, [])
        assert unknown["code"] == "UNKNOWN_REGION"
        far = {**request(0, 24), "stops": [stop(0), {"lat": 10.0, "lon": 10.0}]}
        outside = await refused(client, far, [])
        assert (outside["code"], outside["stop_index"]) == ("STOP_OUTSIDE_REGION", 1)
        long = await refused(client, request(0, 24), [0] * (MAX_DRAW_EDGES + 1))
        assert (long["code"], long["field"]) == ("INVALID_REQUEST", "path")
        negative = await refused(client, request(0, 24), [-1])
        assert (negative["code"], negative["field"]) == ("INVALID_REQUEST", "path")


async def test_a_public_server_counts_each_client_s_drawing_steps(drawn: Path) -> None:
    async with serving(drawn) as (client, app):
        assert (app.state.api.draw_quota, app.state.api.finish_quota) == (None, None)
    async with serving(drawn, public=True) as (client, app):
        api = app.state.api
        assert api.draw_quota is not None and api.draw_quota not in (api.quota, api.pick_quota)
        body = {"request": request(0, 24), "path": []}
        for _ in range(10):  # the two endpoints share the client's steps
            assert (await client.post("/draw/finish", json=body)).status_code == 422
        for _ in range(230):
            assert (await client.post("/draw", json=body)).status_code == 200
        answer = await client.post("/draw", json=body)
        assert answer.status_code == 429
        limited = answer.json()
        wait = limited["retry_after_s"]
        assert limited["code"] == "RATE_LIMITED" and wait >= 1
        assert limited["message"] == f"at most 240 drawing steps a minute; try again in {wait} s"
        assert (await client.post("/draw/finish", json=body)).status_code == 429


async def test_a_public_server_counts_each_client_s_finished_drawings(drawn: Path) -> None:
    async with serving(drawn, public=True) as (client, app):
        api = app.state.api
        assert api.finish_quota is not None and api.finish_quota is not api.draw_quota
        body = {"request": request(0, 2), "path": path([0, 1, 2])}
        ids = set()
        for _ in range(10):
            finished = await client.post("/draw/finish", json=body)
            assert finished.status_code == 200, finished.text
            ids.add(finished.json()["route_id"])
        assert len(ids) == 1  # the same drawing, the same route
        answer = await client.post("/draw/finish", json=body)
        assert answer.status_code == 429
        limited = answer.json()
        wait = limited["retry_after_s"]
        assert limited["code"] == "RATE_LIMITED" and wait >= 1
        assert limited["message"] == f"at most 10 finished drawings a minute; try again in {wait} s"
        assert (await client.post("/draw", json=body)).status_code == 200  # steps go on


async def test_a_request_s_context_is_worked_out_once(
    drawn: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[list[tuple[float, float]]] = []
    context = draw_api._context

    def counted(loaded: Any, req: Any) -> Any:
        made.append(req.stops)
        return context(loaded, req)

    monkeypatch.setattr(draw_api, "_context", counted)
    async with serving(drawn) as (client, _):
        for edges in ([], path([0, 1]), path([0, 1, 2])):
            await drew(client, {**request(0, 24), "seed": len(edges)}, edges)
        await drew(client, request(0, 23), [])
        await drew(client, request(0, 24, constraints={"hard": True}), [])
    assert len(made) == 3  # one per stops and constraints, whatever the seed and the path


def test_the_contexts_kept_are_the_last_used() -> None:
    kept = DrawContexts(2)
    made: list[str] = []

    def make(key: str) -> Any:
        made.append(key)
        return key

    for key in ("a", "b", "a", "c", "a", "b"):
        assert kept.get(key, lambda key=key: make(key)) == key
    assert made == ["a", "b", "c", "b"]  # b was the least used when c came
