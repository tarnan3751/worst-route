"""The app as public hosting runs it, for the hosting tests (logs, limits, readiness, basemap,
the site): `hosted` serves `create_app` in process over httpx, its requests from `client` (an
address and a port, as uvicorn gives the peer), with inline workers, no challenges asked for at its
start unless told (`challenges_at_start`) and, given one, a geocoding service of the test's own
(with `geocoder="nominatim"`)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from worst_route.api.app import ApiSettings, create_app
from worst_route.api.jobs import JobSettings
from worst_route.geocode.service import GeocodeService

from ..engine.graphs import at_node
from .support import GRID, PORTLAND


def stop(v: int) -> dict[str, float]:
    lat, lon = at_node(GRID, v)
    return {"lat": lat, "lon": lon}


def job_body(**fields: Any) -> dict[str, Any]:
    body = {"region": "portland", "stops": [stop(0), stop(99)], "seed": 3, "max_iterations": 300}
    return {**body, **fields}


@asynccontextmanager
async def hosted(
    data: Path,
    *,
    client: tuple[str, int] = ("203.0.113.7", 50000),
    jobs: dict[str, Any] | None = None,
    geocode: GeocodeService | None = None,
    **settings: Any,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    job_settings = JobSettings(**{"inline_workers": True, "coalesce_s": 0.0, **(jobs or {})})
    values: dict[str, Any] = {
        "builds": PORTLAND,
        "backend": "python",
        "geocoder": "none",
        "challenges_at_start": False,
        **settings,
    }
    config = ApiSettings(data_dir=data, jobs=job_settings, **values)
    app = create_app(config, geocode=geocode)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http, app
