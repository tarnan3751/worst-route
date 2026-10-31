"""The site: the built frontend and the API from one process (`docs/reference/hosting.md`).

`create_site(api, frontend_dir)` serves the frontend's build (`frontend/dist/`) at `/` and the
API, `create_app`'s application with its paths unchanged, under `/api`: the prefix the frontend
calls and its dev server strips, so the same build works behind either. Starlette runs the
lifespan of the outermost application only, so the site's runs the API's (its job manager and
geocoder). The files' caching: Vite names what is under `assets/` by its content, so those are
kept a year (`immutable`); anything else (`index.html`) is `no-cache`, so a new build is seen at
the next load. A path the build does not have is 404; `/api/...` errors are the API's own.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

IMMUTABLE = "public, max-age=31536000, immutable"


class FrontendFiles(StaticFiles):
    """The frontend's build, `index.html` at `/`, its hashed assets cached for good."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory=directory, html=True)
        self.assets = (directory / "assets").resolve()

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        hashed = Path(full_path).resolve().is_relative_to(self.assets)
        response.headers["Cache-Control"] = IMMUTABLE if hashed else "no-cache"
        return response


def create_site(api: FastAPI, frontend_dir: Path) -> Starlette:
    """The frontend at `/` and `api` under `/api`."""

    @asynccontextmanager
    async def lifespan(site: Starlette) -> AsyncIterator[None]:
        async with api.router.lifespan_context(api):
            yield

    routes = [Mount("/api", app=api), Mount("/", app=FrontendFiles(frontend_dir))]
    return Starlette(routes=routes, lifespan=lifespan)
