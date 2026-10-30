"""The FastAPI application (`docs/reference/api.md`).

`create_app(settings)` loads the region's build (memory-mapped, `graph.loader`) when it is
called, so `worst-route serve` fails before it listens when the build is missing (or when the
CSR backend is asked for on a build with transition tables, which it cannot search yet:
`NotImplementedError`); it opens the route store and mounts the endpoints (`routes_api`). Its
lifespan starts and stops the job manager. `ApiSettings` says which build, which backend, where
the store is, and the job limits.

Every error answers an `ErrorBody` (`{code, message, ...}`): an endpoint refuses with
`routes_api.ApiError(status, code, message, **fields)`, a malformed request is 422
`INVALID_REQUEST` with pydantic's `errors`, and an unknown path or method is `NOT_FOUND` /
`METHOD_NOT_ALLOWED`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from worst_route import __version__
from worst_route.api import routes_api
from worst_route.api.jobs import JobManager, JobSettings
from worst_route.api.routes_api import ApiError
from worst_route.api.schemas import ErrorBody
from worst_route.api.store import RouteStore
from worst_route.config import DEFAULT_BACKEND, BackendName
from worst_route.graph.loader import Loaded, load_build


@dataclass(frozen=True)
class ApiSettings:
    """What one API process serves."""

    data_dir: Path
    region: str
    build_id: str = "latest"
    backend: BackendName = DEFAULT_BACKEND
    store_path: Path | None = None  # default: <data_dir>/routes.sqlite
    jobs: JobSettings = field(default_factory=JobSettings)


@dataclass(frozen=True, eq=False)
class Api:
    """The application's state (`app.state.api`)."""

    settings: ApiSettings
    builds: dict[str, Loaded]  # by region
    store: RouteStore
    jobs: JobManager


def create_app(settings: ApiSettings) -> FastAPI:
    """The application for `settings`, its build loaded."""
    loaded = load_build(
        settings.data_dir, settings.region, settings.build_id, backend=settings.backend
    )
    if loaded.backend.name == "csr" and len(loaded.data.trans_node):
        from worst_route.graph.csr_backend import NO_TURN_KERNEL

        raise NotImplementedError(NO_TURN_KERNEL)
    store = RouteStore(settings.store_path or settings.data_dir / "routes.sqlite")
    jobs = JobManager(settings.jobs, store, data_dir=settings.data_dir, backend=settings.backend)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await jobs.start()
        try:
            yield
        finally:
            await jobs.stop()

    app = FastAPI(title="worst-route", version=__version__, lifespan=lifespan)
    app.state.api = Api(settings, {settings.region: loaded}, store, jobs)
    app.include_router(routes_api.router)

    @app.exception_handler(ApiError)
    async def refused(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(error.body.model_dump(), status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def malformed(request: Request, error: RequestValidationError) -> JSONResponse:
        problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]
        body = ErrorBody(
            code="INVALID_REQUEST",
            message="; ".join(problems),
            errors=jsonable_encoder(error.errors()),
        )
        return JSONResponse(body.model_dump(), status_code=422)

    @app.exception_handler(HTTPException)
    async def http(request: Request, error: HTTPException) -> JSONResponse:
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        code = codes.get(error.status_code, f"HTTP_{error.status_code}")
        body = ErrorBody(code=code, message=str(error.detail))
        return JSONResponse(body.model_dump(), status_code=error.status_code)

    return app
