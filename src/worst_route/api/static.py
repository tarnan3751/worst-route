"""The site: the built frontend and the API from one process (`docs/reference/hosting.md`).

`create_site(api, frontend_dir)` serves the frontend's build (`frontend/dist/`) at `/` and the
API, `create_app`'s application with its paths unchanged, under `/api`: the prefix the frontend
calls and its dev server strips, so the same build works behind either. Starlette runs the
lifespan of the outermost application only, so the site's runs the API's (its job manager and
geocoder). The files' caching: Vite names what is under `assets/` by its content, so those are
kept a year (`immutable`); anything else (`index.html`) is `no-cache`, so a new build is seen at
the next load. The build's files are gzipped by a `GZipMiddleware` of their own, with the
API's settings (`config.GZIP_MINIMUM`, `app.GZIP_EXCLUDED`); the API compresses its answers itself.
The middleware gives a file's 200 its `Vary: Accept-Encoding` but a 304 none (its body is empty),
so a file's 304 is given the one its 200 would carry (`gzipped`; RFC 9110 §15.4.5). A path the
build does not have is 404; `/api/...` errors are the API's own.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from mimetypes import guess_type
from pathlib import Path

from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from worst_route.api.app import GZIP_EXCLUDED
from worst_route.config import GZIP_MINIMUM

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
        if response.status_code == 304 and gzipped(full_path, stat_result):
            response.headers["Vary"] = "Accept-Encoding"
        return response


def gzipped(full_path: str | os.PathLike[str], stat_result: os.stat_result) -> bool:
    """Whether the file's 200 goes through `GZipMiddleware`'s compression, with its `Vary`: at
    least `GZIP_MINIMUM` bytes long, of a media type (`FileResponse`'s guess) not excluded."""
    media_type = guess_type(full_path)[0] or "application/octet-stream"
    kinds = {media_type, media_type.partition("/")[0] + "/*"}
    return stat_result.st_size >= GZIP_MINIMUM and kinds.isdisjoint(GZIP_EXCLUDED)


def create_site(api: FastAPI, frontend_dir: Path) -> Starlette:
    """The frontend at `/` and `api` under `/api`."""

    @asynccontextmanager
    async def lifespan(site: Starlette) -> AsyncIterator[None]:
        async with api.router.lifespan_context(api):
            yield

    files = GZipMiddleware(
        FrontendFiles(frontend_dir), minimum_size=GZIP_MINIMUM, exclude_content_types=GZIP_EXCLUDED
    )
    routes = [Mount("/api", app=api), Mount("/", app=files)]
    return Starlette(routes=routes, lifespan=lifespan)
