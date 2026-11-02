"""The FastAPI application (`docs/reference/api.md`).

`create_app(settings)` loads each served region's build (memory-mapped, `graph.loader`) and its
geometry, which drawing a route needs, when it is called, so `worst-route serve` fails before it
listens when a build or its geometry is missing, or a places index is one it cannot read
(`places.PlacesFormatError`); it opens the route store, builds the geocoding service (Nominatim,
with its cache `geocode.sqlite` beside the route store; none when the geocoder is `none`; a test
passes its own), loads each region's places index (`presentation.places`; none for a region
`worst-route aux places` has not written one for, whose routes then use a served region's
that covers its states, `routes_api.places_of`) and mounts the endpoints (`routes_api`, `pick_api`,
which picks the build a trip runs on, `geocode_api`, `share_api`, whose images are cached in
`share/` beside the route store, `directions_api`, `ready_api`, `daily_api`, the daily route,
`challenges_api`, the daily challenges, and `draw_api`, a route drawn by hand). Its lifespan
starts and stops the job manager, stops following the daily route's jobs and making the daily
challenges, and closes the geocoder's client. `ApiSettings` says which builds (each
region with its build id, the first region the default), which backend, where the store is, the job
limits, and the geocoder: which, at what URL, with what contact in its User-Agent
(`geocoder_user_agent`: `user_agent`'s, else the machine's, `config.Settings.user_agent`); whether a
reverse proxy in front of it is trusted (`trust_proxy`: its `X-Forwarded-For` names the client, its
`X-Request-ID` the request); and whether it is public (`public`: the limits `limits.PUBLIC`, a
budget up to 60 s and each client's share of the jobs, the place searches, the picks of a build and
the drawing's steps and finishes, held as `Api.limits`, the job manager's settings, `Api.quota`,
`Api.pick_quota`, `Api.draw_quota` and `Api.finish_quota`; else `limits.LOCAL`); and the basemap
`GET /config` offers the frontend (`basemap`, its file named by `basemap_name`, by default the first
region), whose files it serves under `/basemap/` from `basemap_dir` (by default
`<data_dir>/basemap`; `basemap_api`). Every request gets an id and an access line
(`logs.RequestLog`), and an answer of a kilobyte or more travels gzipped to a client that accepts
it (`GZipMiddleware`, `config.GZIP_MINIMUM`), but never the event stream or what is compressed
already (`GZIP_EXCLUDED`). Once the job manager has started, the lifespan makes each build's spatial
index and then its backend in a thread (`warm_builds`; `graph.loader`): on a first start the Numba
kernels compile then, into their cache, while the server already listens, and a request that needs
one before it is made waits for the kernels' import or makes its own.

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
import threading
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.middleware.gzip import GZipMiddleware

from worst_route import __version__, config
from worst_route.api import (
    basemap_api,
    challenges_api,
    daily_api,
    directions_api,
    draw_api,
    geocode_api,
    pick_api,
    ready_api,
    routes_api,
    share_api,
)
from worst_route.api.jobs import JobManager, JobSettings
from worst_route.api.limits import LOCAL, PUBLIC, GeocodeQuota, Limits
from worst_route.api.logs import RequestLog, request_id_of
from worst_route.api.routes_api import ApiError
from worst_route.api.schemas import ChallengeOut, ErrorBody
from worst_route.api.store import RouteStore
from worst_route.config import (
    DEFAULT_BACKEND,
    DRAW_CONTEXTS,
    GZIP_EXCLUDED_TYPES,
    GZIP_MINIMUM,
    NOMINATIM_URL,
    BackendName,
    BasemapName,
    GeocoderName,
)
from worst_route.geocode.cache import GeocodeCache
from worst_route.geocode.limiter import RateLimiter
from worst_route.geocode.nominatim import NominatimGeocoder, user_agent
from worst_route.geocode.service import GeocodeService
from worst_route.graph.loader import BuildNotFound, Loaded, load_build
from worst_route.graph.store import MissingArray
from worst_route.presentation.places import PlaceIndex

log = logging.getLogger(__name__)

# Never gzipped: config's media types (the event stream, and what is compressed already, the share
# image's PNG among them) and the basemap's tiles, compressed in their file. An answer of at least
# GZIP_MINIMUM bytes travels gzipped, with `Vary: Accept-Encoding`, to a client that accepts it; a
# shorter one or a byte range (206) travels as it is, and a file's HEAD answers its plain headers.
GZIP_EXCLUDED = (*GZIP_EXCLUDED_TYPES, basemap_api.MEDIA_TYPES[".pmtiles"])


@dataclass(frozen=True)
class ApiSettings:
    """What one API process serves."""

    data_dir: Path
    builds: tuple[tuple[str, str], ...]  # (region, build id or "latest"); the first the default
    backend: BackendName = DEFAULT_BACKEND
    store_path: Path | None = None  # default: <data_dir>/routes.sqlite
    jobs: JobSettings = field(default_factory=JobSettings)
    geocoder: GeocoderName = "nominatim"
    nominatim_url: str = NOMINATIM_URL
    user_agent: str | None = None  # the geocoder's, before the contact; None: config's
    contact: str | None = None  # in the geocoder's User-Agent (WORST_ROUTE_CONTACT)
    trust_proxy: bool = False  # a reverse proxy's X-Forwarded-For and X-Request-ID believed
    public: bool = False  # the public limits (`limits.PUBLIC`)
    basemap: BasemapName = "raster"  # what `/config` offers (WORST_ROUTE_BASEMAP)
    basemap_name: str | None = None  # its file, basemap/<name>.pmtiles; None: the first region
    basemap_dir: Path | None = None  # the basemap's files; None: <data_dir>/basemap


@dataclass(frozen=True, eq=False)
class Api:
    """The application's state (`app.state.api`)."""

    settings: ApiSettings
    builds: dict[str, Loaded]  # by region, in the order served
    store: RouteStore
    jobs: JobManager
    geocode: GeocodeService | None  # None when the geocoder is "none"
    places: dict[str, PlaceIndex | None]  # by region; None for one without its own index
    share_dir: Path  # the share images' cache
    # by route and theme, while its image renders
    share_locks: dict[tuple[str, str], asyncio.Lock] = field(default_factory=dict)
    # each build's edge letters (`alphabet_search.edge_letters`), by graph_version, worked out
    # once for the alphabet routes' documents
    letters: dict[str, np.ndarray] = field(default_factory=dict)
    limits: Limits = LOCAL
    quota: GeocodeQuota | None = None  # each client's place searches, on a public server
    pick_quota: GeocodeQuota | None = None  # each client's picks of a build, on a public server
    draw_quota: GeocodeQuota | None = None  # each client's drawing steps, on a public server
    finish_quota: GeocodeQuota | None = None  # each client's finished drawings, on a public server
    # what the last requests drawn on need on every step (`draw_api`), by build and request
    draw_contexts: draw_api.DrawContexts = field(
        default_factory=lambda: draw_api.DrawContexts(DRAW_CONTEXTS)
    )
    # the daily route's (`daily_api`): a lock per (date, region) while its plan is started, the
    # tasks that follow its jobs to their end, the jobs running, which no client may cancel, and
    # the day's request and its pre-flight, by (date, region, graph_version), drawn once
    daily_locks: dict[tuple[str, str], asyncio.Lock] = field(default_factory=dict)
    daily_tasks: "set[asyncio.Task[None]]" = field(default_factory=set)
    daily_jobs: set[str] = field(default_factory=set)
    daily_plans: dict[tuple[str, str, str], daily_api.DailyPlan] = field(default_factory=dict)
    # the daily challenges (`challenges_api`), by (date, region, graph_version): each date's seven
    # as kept, and the task making a date's while it does
    challenge_days: dict[tuple[str, str, str], list[ChallengeOut]] = field(default_factory=dict)
    challenge_tasks: "dict[tuple[str, str, str], asyncio.Task[list[ChallengeOut]]]" = field(
        default_factory=dict
    )


def geocoder_user_agent(settings: ApiSettings) -> str:
    """The User-Agent the geocoder sends: `settings.user_agent`, else the machine's
    (`config.Settings.user_agent`), with the contact when there is one."""
    return user_agent(settings.user_agent or config.settings().user_agent, settings.contact)


def create_app(settings: ApiSettings, geocode: GeocodeService | None = None) -> FastAPI:
    """The application for `settings`, its builds loaded; `geocode` stands in for the geocoding
    service `settings` would build (tests), unless the geocoder is "none"."""
    builds: dict[str, Loaded] = {}
    for region, build_id in settings.builds:
        loaded = load_build(settings.data_dir, region, build_id, backend=settings.backend)
        try:
            _ = loaded.geometry  # read now: a build without its geometry cannot draw a route
        except MissingArray as error:
            raise BuildNotFound(str(error)) from None
        builds[region] = loaded
    places = {region: PlaceIndex.load(region, settings.data_dir) for region in builds}
    store_path = settings.store_path or settings.data_dir / "routes.sqlite"
    store = RouteStore(store_path)
    limits = PUBLIC if settings.public else LOCAL
    job_settings = replace(
        settings.jobs,
        running_per_client=limits.running_per_client,
        queued_per_client=limits.queued_per_client,
    )
    jobs = JobManager(job_settings, store, data_dir=settings.data_dir, backend=settings.backend)
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
        threading.Thread(
            target=warm_builds, args=(builds,), name="warm-builds", daemon=True
        ).start()
        try:
            yield
        finally:
            api: Api = app.state.api
            following = [*api.daily_tasks, *api.challenge_tasks.values()]
            for task in following:
                task.cancel()
            await asyncio.gather(*following, return_exceptions=True)
            await jobs.stop()
            if service is not None:
                await service.aclose()

    app = FastAPI(title="worst-route", version=__version__, lifespan=lifespan)
    share_dir = store_path.parent / "share"
    quota = GeocodeQuota(limits.geocode_per_minute) if limits.geocode_per_minute else None
    picks = GeocodeQuota(limits.pick_per_minute) if limits.pick_per_minute else None
    draws = GeocodeQuota(limits.draw_per_minute) if limits.draw_per_minute else None
    finishes = GeocodeQuota(limits.finish_per_minute) if limits.finish_per_minute else None
    app.state.api = Api(
        settings,
        builds,
        store,
        jobs,
        service,
        places,
        share_dir,
        limits=limits,
        quota=quota,
        pick_quota=picks,
        draw_quota=draws,
        finish_quota=finishes,
    )
    app.include_router(routes_api.router)
    app.include_router(pick_api.router)
    app.include_router(geocode_api.router)
    app.include_router(share_api.router)
    app.include_router(directions_api.router)
    app.include_router(ready_api.router)
    app.include_router(daily_api.router)
    app.include_router(challenges_api.router)
    app.include_router(draw_api.router)
    app.include_router(basemap_api.router)
    basemap_api.mount_basemap(app, basemap_api.basemap_dir(settings))
    app.add_middleware(
        GZipMiddleware, minimum_size=GZIP_MINIMUM, exclude_content_types=GZIP_EXCLUDED
    )
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


def warm_builds(builds: Mapping[str, Loaded]) -> None:
    """Make each build's spatial index, then each one's backend (on a first start the Numba
    kernels compile), and log how long it took; an error is logged, and met again by the first
    request that needs what failed."""
    started = time.monotonic()
    try:
        for loaded in builds.values():
            _ = loaded.spatial
        for loaded in builds.values():
            _ = loaded.backend
    except Exception:
        log.exception("the builds could not be made ready to plan on")
        return
    log.info("the builds are ready to plan on (%.1f s)", time.monotonic() - started)


def _one_field(errors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`{"field": name}` when every error lies in the same field of the body, else nothing (a
    body that does not parse has a character's position where a field's name would be)."""
    names = {
        loc[1] if len(loc) > 1 and loc[0] == "body" and isinstance(loc[1], str) else None
        for loc in (e["loc"] for e in errors)
    }
    return {"field": names.pop()} if len(names) == 1 and None not in names else {}
