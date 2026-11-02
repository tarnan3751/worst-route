"""The site (`api/static.py`): the frontend's build at `/` with its caching, and the API under
`/api` with its paths, errors, lifespan (a job runs to its end) and access lines unchanged; both
gzipped to a client that accepts it (`config.GZIP_MINIMUM`, `app.GZIP_EXCLUDED`), with one `Vary`
on a file's 200 and its 304, but not the event stream, which still goes out event by event, the
share image or the basemap's tiles."""

import asyncio
import gzip
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message

from worst_route.api.app import ApiSettings, create_app
from worst_route.api.jobs import JobSettings
from worst_route.api.static import IMMUTABLE, create_site

from .hosting import job_body
from .support import PORTLAND

pytestmark = pytest.mark.anyio


def write_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Worst Route</title>")
    (dist / "assets" / "index-Ab12.js").write_text("console.log(1)")
    (dist / "assets" / "maplibre-gl-worker-Cd34.mjs").write_text("self.onmessage = null")
    return dist


SCRIPT = b"console.log('the worst route');\n" * 8000  # 256 kB: four of a file's 64 kB chunks


def settings_of(data: Path, **fields: Any) -> ApiSettings:
    jobs = JobSettings(inline_workers=True, coalesce_s=0.0)
    values: dict[str, Any] = {"builds": PORTLAND, "backend": "python", "geocoder": "none"}
    return ApiSettings(data_dir=data, jobs=jobs, **{**values, **fields})


def varies(response: httpx.Response) -> list[str]:
    """The names of the answer's `Vary` headers, each as often as it is given."""
    values = response.headers.get_list("vary")
    return [name.strip() for value in values for name in value.split(",")]


async def plain_get(http: httpx.AsyncClient, url: str) -> httpx.Response:
    """A GET without `Accept-Encoding` (httpx sends `gzip, deflate` unless told otherwise)."""
    request = http.build_request("GET", url)
    del request.headers["Accept-Encoding"]
    return await http.send(request)


async def asgi_get(app: ASGIApp, path: str) -> list[Message]:
    """The messages `app` sends a server for a GET of `path` accepting gzip, as it sends them (httpx
    gathers a body whole); the client stays until the answer ends."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"accept-encoding", b"gzip")],
        "client": ("203.0.113.7", 50000),
        "server": ("test", 80),
    }
    requested = False
    messages: list[Message] = []

    async def receive() -> Message:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # cancelled once the answer has ended
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return messages


async def test_the_frontend_at_the_root_and_the_api_under_api(
    data: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="worst_route")
    jobs = JobSettings(inline_workers=True, coalesce_s=0.0)
    settings = ApiSettings(
        data_dir=data, builds=PORTLAND, backend="python", jobs=jobs, geocoder="none"
    )
    site = create_site(create_app(settings), write_dist(tmp_path))
    async with site.router.lifespan_context(site):
        transport = httpx.ASGITransport(app=site)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            page = await http.get("/")
            assert page.status_code == 200 and "Worst Route" in page.text
            assert page.headers["content-type"].startswith("text/html")
            assert page.headers["cache-control"] == "no-cache"
            script = await http.get("/assets/index-Ab12.js")
            assert script.headers["cache-control"] == IMMUTABLE
            worker = await http.get("/assets/maplibre-gl-worker-Cd34.mjs")
            assert worker.headers["content-type"].startswith("text/javascript")  # a module
            assert (await http.get("/nowhere")).status_code == 404
            assert (await http.get("/health")).status_code == 404  # the API is under /api
            health = await http.get("/api/health")
            assert health.status_code == 200 and health.json()["status"] == "ok"
            missing = await http.get("/api/nowhere")
            assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"
            # The lifespan ran: a job runs to its end, its events at /api + its events_url.
            created = await http.post("/api/jobs", json=job_body())
            assert created.status_code == 202, created.text
            events_url = created.json()["events_url"]
            assert events_url.startswith("/jobs/")  # the API's own path, as before
            stream = await http.get(f"/api{events_url}")
            names = [
                line.removeprefix("event: ")
                for line in stream.text.replace("\r\n", "\n").splitlines()
                if line.startswith("event: ")
            ]
            assert names[0] == "snapped" and names[-1] == "done"
            route_id = json.loads(stream.text.rsplit("data: ", 1)[1])["route_id"]
            assert (await http.get(f"/api/routes/{route_id}")).status_code == 200
    paths = [r.path for r in caplog.records if r.name == "worst_route.access"]  # type: ignore[attr-defined]
    assert paths[:2] == ["/api/health", "/api/nowhere"]  # the API's requests only, whole paths


async def test_the_build_travels_gzipped_to_a_client_that_accepts_it_with_its_caching(
    data: Path, tmp_path: Path
) -> None:
    dist = write_dist(tmp_path)
    (dist / "assets" / "index-Ef56.js").write_bytes(SCRIPT)
    (dist / "assets" / "font-Gh78.woff2").write_bytes(bytes(4096))  # compressed already
    site = create_site(create_app(settings_of(data)), dist)
    async with site.router.lifespan_context(site):
        transport = httpx.ASGITransport(app=site)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            gzipped = await http.get("/assets/index-Ef56.js", headers={"Accept-Encoding": "gzip"})
            assert gzipped.status_code == 200 and gzipped.content == SCRIPT  # httpx unzips it
            assert gzipped.headers["content-encoding"] == "gzip"
            assert varies(gzipped) == ["Accept-Encoding"]
            assert gzipped.headers["cache-control"] == IMMUTABLE
            assert gzipped.headers["content-type"].startswith("text/javascript")
            headers = {"Accept-Encoding": "gzip"}
            async with http.stream("GET", "/assets/index-Ef56.js", headers=headers) as wire:
                raw = b"".join([chunk async for chunk in wire.aiter_raw()])
            assert gzip.decompress(raw) == SCRIPT and len(raw) < len(SCRIPT) // 10
            plain = await plain_get(http, "/assets/index-Ef56.js")
            assert plain.status_code == 200 and plain.content == SCRIPT
            assert "content-encoding" not in plain.headers
            assert plain.headers["content-length"] == str(len(SCRIPT))
            assert varies(plain) == ["Accept-Encoding"]  # a cache keeps the two apart
            assert plain.headers["cache-control"] == IMMUTABLE
            # A 304 carries the Vary its 200 would, once, whatever the client accepts.
            for accept in ("gzip", "identity"):
                headers = {"Accept-Encoding": accept, "If-None-Match": gzipped.headers["etag"]}
                cached = await http.get("/assets/index-Ef56.js", headers=headers)
                assert cached.status_code == 304 and varies(cached) == ["Accept-Encoding"]
                assert cached.headers["cache-control"] == IMMUTABLE
            # A page under a kilobyte and a font travel as they are, their 304s without a Vary.
            page = await http.get("/", headers={"Accept-Encoding": "gzip"})
            assert page.status_code == 200 and "Worst Route" in page.text
            assert "content-encoding" not in page.headers and varies(page) == []
            assert page.headers["cache-control"] == "no-cache"
            font = await http.get("/assets/font-Gh78.woff2", headers={"Accept-Encoding": "gzip"})
            assert font.headers["content-type"] == "font/woff2" and font.content == bytes(4096)
            assert "content-encoding" not in font.headers and varies(font) == []
            for url, answer in (("/", page), ("/assets/font-Gh78.woff2", font)):
                headers = {"Accept-Encoding": "gzip", "If-None-Match": answer.headers["etag"]}
                cached = await http.get(url, headers=headers)
                assert cached.status_code == 304 and varies(cached) == []


async def test_the_apis_answers_travel_gzipped_but_not_its_stream_share_image_or_tiles(
    data: Path, tmp_path: Path
) -> None:
    basemap = tmp_path / "basemap"
    basemap.mkdir()
    tiles = bytes(range(256)) * 32  # 8 kB, which a whole GET reads at once
    (basemap / "portland.pmtiles").write_bytes(tiles)
    site = create_site(create_app(settings_of(data, basemap_dir=basemap)), write_dist(tmp_path))
    async with site.router.lifespan_context(site):
        transport = httpx.ASGITransport(app=site)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            created = await http.post("/api/jobs", json=job_body())
            assert created.status_code == 202, created.text
            # The stream, accepting gzip: plain text, each event sent on as it comes.
            messages = await asgi_get(site, f"/api{created.json()['events_url']}")
            start = Headers(raw=messages[0]["headers"])
            assert messages[0]["status"] == 200 and "content-encoding" not in start
            assert start["content-type"].startswith("text/event-stream")
            bodies = [m["body"].decode() for m in messages[1:] if m.get("body")]
            names = [b.split("event: ", 1)[1].split("\r\n", 1)[0] for b in bodies]
            assert names[0] == "snapped" and names[-1] == "done" and len(names) > 2
            assert all(b.count("event: ") == 1 for b in bodies)  # one event a message
            route_id = json.loads(bodies[-1].rsplit("data: ", 1)[1])["route_id"]
            # The route's document, gzipped to a client that accepts it.
            document = await http.get(
                f"/api/routes/{route_id}", headers={"Accept-Encoding": "gzip"}
            )
            assert document.status_code == 200 and document.json()["route_id"] == route_id
            assert document.headers["content-encoding"] == "gzip"
            assert varies(document) == ["Accept-Encoding"]
            plain = await plain_get(http, f"/api/routes/{route_id}")
            assert "content-encoding" not in plain.headers and plain.json() == document.json()
            assert int(plain.headers["content-length"]) > 1024
            # Not a file: its compressed length is given.
            length = int(document.headers["content-length"])
            assert length == document.num_bytes_downloaded < int(plain.headers["content-length"])
            # The share image and the basemap's tiles are compressed already.
            image = await http.get(f"/api/routes/{route_id}/share.png")
            assert image.status_code == 200 and image.headers["content-type"] == "image/png"
            assert "content-encoding" not in image.headers
            whole = await http.get("/api/basemap/portland.pmtiles")
            assert whole.status_code == 200 and whole.content == tiles
            assert whole.headers["content-type"] == "application/vnd.pmtiles"
            assert "content-encoding" not in whole.headers
