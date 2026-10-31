"""Request ids, the access lines and the JSON logs (`api/logs.py`), an unhandled error's answer
(`app.py`), and who a client is (`api/limits.py`: `client_of`, `client_hash`)."""

import json
import logging
import re
import sys
from pathlib import Path

import httpx
import pytest
from starlette.types import Scope

from worst_route.api.app import ApiSettings, create_app
from worst_route.api.limits import client_hash, client_of
from worst_route.api.logs import ContextFilter, JsonFormatter, request_id

from .hosting import hosted, job_body
from .support import slow_splices

pytestmark = pytest.mark.anyio


def access_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "worst_route.access"]


def scope(peer: str, *forwarded: str) -> Scope:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    return {"type": "http", "client": (peer, 1), "headers": headers}


def test_the_client_is_the_peer_unless_a_trusted_proxy_names_it() -> None:
    assert client_of(scope("198.51.100.4", "10.0.0.1"), trust_proxy=False) == "198.51.100.4"
    # The proxy appends the address it saw: the last one; what came before is the client's say.
    forwarded = scope("127.0.0.1", "6.6.6.6, 203.0.113.9")
    assert client_of(forwarded, trust_proxy=True) == "203.0.113.9"
    assert client_of(scope("127.0.0.1", "6.6.6.6", "203.0.113.9"), trust_proxy=True) == (
        "203.0.113.9"
    )
    assert client_of(scope("127.0.0.1"), trust_proxy=True) == "127.0.0.1"
    # IPv6 by its /64; an IPv4-mapped address as the IPv4 one.
    assert client_of(scope("2001:db8:1:2:aaaa::1"), trust_proxy=False) == "2001:db8:1:2::/64"
    assert client_of(scope("2001:db8:1:2:bbbb::9"), trust_proxy=False) == "2001:db8:1:2::/64"
    assert client_of(scope("::ffff:192.0.2.1"), trust_proxy=False) == "192.0.2.1"
    assert client_of({"type": "http", "headers": []}, trust_proxy=False) == "unknown"
    # A proxy that appends the client's port: the address alone.
    assert client_of(scope("127.0.0.1", "203.0.113.9:4711"), trust_proxy=True) == "203.0.113.9"
    v6 = scope("127.0.0.1", "6.6.6.6, [2001:db8:1:2:aaaa::1]:4711")
    assert client_of(v6, trust_proxy=True) == "2001:db8:1:2::/64"
    assert client_of(scope("127.0.0.1", "[2001:db8:1:2::9]"), trust_proxy=True) == (
        "2001:db8:1:2::/64"
    )
    assert re.fullmatch(r"[0-9a-f]{12}", client_hash("192.0.2.1"))
    assert client_hash("192.0.2.1") == client_hash("192.0.2.1") != client_hash("192.0.2.2")


async def test_every_request_gets_an_id_and_one_access_line(
    data: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="worst_route")
    async with hosted(data, client=("192.0.2.33", 4000)) as (http, _):
        first = await http.get("/health")
        second = await http.get("/geocode", params={"q": "my secret place", "region": "portland"})
    ids = [r.headers["x-request-id"] for r in (first, second)]
    assert all(re.fullmatch(r"[0-9a-f]{16}", i) for i in ids) and ids[0] != ids[1]
    lines = access_lines(caplog)
    assert [(r.method, r.path, r.status) for r in lines] == [  # type: ignore[attr-defined]
        ("GET", "/health", 200),
        ("GET", "/geocode", 503),  # the geocoder is off
    ]
    assert [r.request_id for r in lines] == ids  # type: ignore[attr-defined]
    for record in lines:
        assert record.client == client_hash("192.0.2.33")  # type: ignore[attr-defined]
        assert record.duration_ms >= 0  # type: ignore[attr-defined]
        line = JsonFormatter().format(record)
        assert "192.0.2.33" not in line and "secret" not in line  # no address, no query


async def test_a_proxy_s_request_id_is_kept_only_when_it_is_trusted(data: Path) -> None:
    given = {"X-Request-ID": "edge-7f3a.9_b"}
    async with hosted(data) as (http, _):
        assert (await http.get("/health", headers=given)).headers["x-request-id"] != (
            "edge-7f3a.9_b"
        )
    async with hosted(data, trust_proxy=True) as (http, _):
        assert (await http.get("/health", headers=given)).headers["x-request-id"] == (
            "edge-7f3a.9_b"
        )
        for bad in ("two words", "x" * 65, "a;b"):
            rid = (await http.get("/health", headers={"X-Request-ID": bad})).headers["x-request-id"]
            assert re.fullmatch(r"[0-9a-f]{16}", rid)


async def test_a_job_s_lines_carry_its_id_and_the_request_that_started_it(
    data: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="worst_route")
    caplog.handler.addFilter(ContextFilter())
    async with hosted(data) as (http, _):
        created = await http.post("/jobs", json=job_body())
        job_id = created.json()["job_id"]
        await http.get(f"/jobs/{job_id}/events")  # to its end
        status = (await http.get(f"/jobs/{job_id}")).json()
    [end] = [r for r in caplog.records if r.name == "worst_route.api.jobs"]
    assert end.levelno == logging.INFO and end.getMessage().startswith(f"job {job_id}: done (")
    fields = (end.job_id, end.status, end.outcome, end.route_id)  # type: ignore[attr-defined]
    assert fields == (job_id, "done", status["stopped_because"], status["route_id"])
    assert end.request_id == created.headers["x-request-id"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("settings", [{"jobs": {"max_jobs": 1}}], ids=["one worker"])
async def test_a_queued_job_s_lines_carry_the_request_that_submitted_it(
    data: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, object],
) -> None:
    # The second job waits for the first and is started by the first's follower, in its
    # context: the second's lines must still name the request that submitted it.
    slow_splices(monkeypatch)
    caplog.set_level(logging.INFO, logger="worst_route")
    caplog.handler.addFilter(ContextFilter())
    async with hosted(data, **settings) as (http, app):  # type: ignore[arg-type]
        first, second = [await http.post("/jobs", json=job_body(seed=s)) for s in (3, 4)]
        ids = [r.json()["job_id"] for r in (first, second)]
        assert app.state.api.jobs.jobs[ids[1]].status == "queued"
        for job_id in ids:
            await http.get(f"/jobs/{job_id}/events")  # to its end
    ends = {r.job_id: r for r in caplog.records if r.name == "worst_route.api.jobs"}  # type: ignore[attr-defined]
    for job_id, posted in zip(ids, (first, second), strict=True):
        assert ends[job_id].request_id == posted.headers["x-request-id"]  # type: ignore[attr-defined]


async def test_an_unhandled_error_is_a_500_with_the_request_s_id(
    data: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="worst_route")
    settings = ApiSettings(data_dir=data, region="portland", backend="python", geocoder="none")
    app = create_app(settings)

    def boom() -> None:
        raise RuntimeError("the disk is on fire")

    app.add_api_route("/boom", boom)
    # Starlette raises the error again once answered (for the server to log): not into the test.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        answer = await http.get("/boom")
    assert answer.status_code == 500
    assert answer.json() == {"code": "INTERNAL_ERROR", "message": "internal error"}
    rid = answer.headers["x-request-id"]
    [error] = [r for r in caplog.records if r.name == "worst_route.api.app"]
    assert error.getMessage() == "GET /boom failed: RuntimeError: the disk is on fire"
    assert error.request_id == rid and error.exc_info is not None  # type: ignore[attr-defined]
    [access] = access_lines(caplog)
    assert (access.status, access.request_id) == (500, rid)  # type: ignore[attr-defined]


def test_a_json_line_has_the_record_s_fields_and_its_traceback() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.makeLogRecord(
        {
            "name": "worst_route.api.jobs",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "msg": "job %s failed",
            "args": ("abc",),
            "exc_info": exc_info,
            "job_id": "abc",
            "color_message": "uvicorn's",
        }
    )
    token = request_id.set("0123456789abcdef")
    try:
        ContextFilter().filter(record)
    finally:
        request_id.reset(token)
    line = json.loads(JsonFormatter().format(record))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line.pop("ts"))
    assert "ValueError: boom" in line.pop("exc")
    assert line == {
        "level": "ERROR",
        "logger": "worst_route.api.jobs",
        "message": "job abc failed",
        "job_id": "abc",
        "request_id": "0123456789abcdef",
    }
    # Outside a request there is no id to add.
    bare = logging.makeLogRecord({"msg": "hello"})
    ContextFilter().filter(bare)
    assert "request_id" not in json.loads(JsonFormatter().format(bare))
