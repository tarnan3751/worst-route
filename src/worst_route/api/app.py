"""The FastAPI application (`docs/reference/api.md`).

`create_app(settings)` loads the region's build (memory-mapped, `graph.loader`) when it is
called, so `worst-route serve` fails before it listens when the build is missing; it opens the
route store, builds the geocoding service (Nominatim, with its cache `geocode.sqlite` beside
the route store; none when the geocoder is `none`; a test passes its own), loads the region's
places index (`presentation.places`; none when `worst-route aux places` has not written it) and
mounts the endpoints (`routes_api`, `geocode_api`, `share_api`, whose images are cached in
`share/` beside the route store, `directions_api` and `ready_api`). Its lifespan starts and
stops the job manager, and closes the geocoder's client. `ApiSettings` says which build, which
backend, where the store is, the job limits, and the geocoder: which, at what URL, with what
contact in its User-Agent (`geocoder_user_agent`); and whether a reverse proxy in front of it is
trusted (`trust_proxy`: its `X-Forwarded-For` names the client, its `X-Request-ID` the request).
Every request gets an id and an access line (`logs.RequestLog`).

Every error answers an `ErrorBody` (`{code, message, ...}`): an endpoint refuses with
`routes_api.ApiError(status, code, message, **fields)`, a malformed request is 422
`INVALID_REQUEST` with pydantic's `errors` (without their `input`, which may be a body's
`Infinity` or `NaN`) and, when every error lies in one field of the body, that `field`, and
an unknown path or method is `NOT_FOUND` / `METHOD_NOT_ALLOWED`. An unhandled exception is
logged with its traceback and answered 500 `INTERNAL_ERROR` ("internal error"), with the
request's `X-Request-ID` (Starlette answers it outside the middleware that adds the header).
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from worst_route import __version__, config
from worst_route.api import directions_api, geocode_api, ready_api, routes_api, share_api
from worst_route.api.jobs import JobManager, JobSettings
from worst_route.api.logs import RequestLog, request_id_of
from worst_route.api.routes_api import ApiError
from worst_route.api.schemas import ErrorBody
from worst_route.api.store import RouteStore
from worst_route.config import DEFAULT_BACKEND, NOMINATIM_URL, BackendName, GeocoderName
from worst_route.geocode.cache import GeocodeCache
from worst_route.geocode.limiter import RateLimiter
from worst_route.geocode.nominatim import NominatimGeocoder, user_agent
from worst_route.geocode.service import GeocodeService
from worst_route.graph.loader import Loaded, load_build
from worst_route.presentation.places import PlaceIndex

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApiSettings:
    """What one API process serves."""

    data_dir: Path
    region: str
    build_id: str = "latest"
    backend: BackendName = DEFAULT_BACKEND
    store_path: Path | None = None  # default: <data_dir>/routes.sqlite
    jobs: JobSettings = field(default_factory=JobSettings)
    geocoder: GeocoderName = "nominatim"
    nominatim_url: str = NOMINATIM_URL
    contact: str | None = None  # in the geocoder's User-Agent (WORST_ROUTE_CONTACT)
    trust_proxy: bool = False  # a reverse proxy's X-Forwarded-For and X-Request-ID believed


@dataclass(frozen=True, eq=False)
class Api:
    """The application's state (`app.state.api`)."""

    settings: ApiSettings
    builds: dict[str, Loaded]  # by region
    store: RouteStore
    jobs: JobManager
    geocode: GeocodeService | None  # None when the geocoder is "none"
    places: PlaceIndex | None  # None when the region has no places index
    share_dir: Path  # the share images' cache
    share_locks: dict[str, asyncio.Lock] = field(default_factory=dict)  # by route, while rendering
    # each build's edge letters (`alphabet_search.edge_letters`), by graph_version, worked out
    # once for the alphabet routes' documents
    letters: dict[str, np.ndarray] = field(default_factory=dict)


def geocoder_user_agent(settings: ApiSettings) -> str:
    """The User-Agent the geocoder sends: the machine's (`config.Settings.user_agent`), with
    the contact when there is one."""
    return user_agent(config.settings().user_agent, settings.contact)


def create_app(settings: ApiSettings, geocode: GeocodeService | None = None) -> FastAPI:
    """The application for `settings`, its build loaded; `geocode` stands in for the geocoding
    service `settings` would build (tests), unless the geocoder is "none"."""
    loaded = load_build(
        settings.data_dir, settings.region, settings.build_id, backend=settings.backend
    )
    store_path = settings.store_path or settings.data_dir / "routes.sqlite"
    store = RouteStore(store_path)
    jobs = JobManager(settings.jobs, store, data_dir=settings.data_dir, backend=settings.backend)
    service = None
    if settings.geocoder == "nominatim":
        service = geocode or GeocodeService(
            NominatimGeocoder(settings.nominatim_url, geocoder_user_agent(settings)),
            GeocodeCache(store_path.parent / "geocode.sqlite"),
            RateLimiter(),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await jobs.start()
        try:
            yield
        finally:
            await jobs.stop()
            if service is not None:
                await service.aclose()

    app = FastAPI(title="worst-route", version=__version__, lifespan=lifespan)
    places = PlaceIndex.load(settings.region, settings.data_dir)
    share_dir = store_path.parent / "share"
    builds = {settings.region: loaded}
    app.state.api = Api(settings, builds, store, jobs, service, places, share_dir)
    app.include_router(routes_api.router)
    app.include_router(geocode_api.router)
    app.include_router(share_api.router)
    app.include_router(directions_api.router)
    app.include_router(ready_api.router)
    app.add_middleware(RequestLog, trust_proxy=settings.trust_proxy)

    @app.exception_handler(ApiError)
    async def refused(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(error.body.model_dump(), status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def malformed(request: Request, error: RequestValidationError) -> JSONResponse:
        problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]
        # without each error's `input`: a body's Infinity or NaN is not JSON to send back
        errors = [{k: v for k, v in e.items() if k != "input"} for e in error.errors()]
        body = ErrorBody(
            code="INVALID_REQUEST",
            message="; ".join(problems),
            errors=jsonable_encoder(errors),
            **_one_field(error.errors()),
        )
        return JSONResponse(body.model_dump(), status_code=422)

    @app.exception_handler(Exception)
    async def failed(request: Request, error: Exception) -> JSONResponse:
        rid = request_id_of(request.scope)
        method, path = request.method, request.url.path
        message = "%s %s failed: %s: %s"
        args = (method, path, type(error).__name__, error)
        log.error(message, *args, exc_info=error, extra={"request_id": rid})
        body = ErrorBody(code="INTERNAL_ERROR", message="internal error")
        headers = {"X-Request-ID": rid} if rid is not None else None
        return JSONResponse(body.model_dump(), status_code=500, headers=headers)

    @app.exception_handler(HTTPException)
    async def http(request: Request, error: HTTPException) -> JSONResponse:
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        code = codes.get(error.status_code, f"HTTP_{error.status_code}")
        body = ErrorBody(code=code, message=str(error.detail))
        return JSONResponse(body.model_dump(), status_code=error.status_code)

    return app


def _one_field(errors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`{"field": name}` when every error lies in the same field of the body, else nothing (a
    body that does not parse has a character's position where a field's name would be)."""
    names = {
        loc[1] if len(loc) > 1 and loc[0] == "body" and isinstance(loc[1], str) else None
        for loc in (e["loc"] for e in errors)
    }
    return {"field": names.pop()} if len(names) == 1 and None not in names else {}
