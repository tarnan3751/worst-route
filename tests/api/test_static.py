"""The site (`api/static.py`): the frontend's build at `/` with its caching, and the API under
`/api` with its paths, errors, lifespan (a job runs to its end) and access lines unchanged."""

import json
import logging
from pathlib import Path

import httpx
import pytest

from worst_route.api.app import ApiSettings, create_app
from worst_route.api.jobs import JobSettings
from worst_route.api.static import IMMUTABLE, create_site

from .hosting import job_body

pytestmark = pytest.mark.anyio


def write_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Worst Route</title>")
    (dist / "assets" / "index-Ab12.js").write_text("console.log(1)")
    (dist / "assets" / "maplibre-gl-worker-Cd34.mjs").write_text("self.onmessage = null")
    return dist


async def test_the_frontend_at_the_root_and_the_api_under_api(
    data: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="worst_route")
    jobs = JobSettings(inline_workers=True, coalesce_s=0.0)
    settings = ApiSettings(
        data_dir=data, region="portland", backend="python", jobs=jobs, geocoder="none"
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
