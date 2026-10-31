"""The client's configuration and the basemap's files (`docs/reference/api.md`, **Basemap**;
`docs/reference/hosting.md`).

    GET /config            {public, basemap: {kind, tiles, glyphs, sprite, style}, limits}
    GET /basemap/<path>    the files under the basemap's directory, with byte ranges

`/config` tells the frontend which basemap to draw (`ApiSettings.basemap`, from
`WORST_ROUTE_BASEMAP`): `raster`, OpenStreetMap's tiles at hobby volume; `openfreemap`,
OpenFreeMap's vector tiles from its public instance, in the style `config.OPENFREEMAP_STYLE`
names (`style`), which the frontend fetches; or `pmtiles`, Protomaps' vector tiles from
`<name>.pmtiles` in the basemap's directory (`basemap_dir`: `ApiSettings.basemap_dir`, else
`<data>/basemap/`; the desktop app names one of its own beside its read-only data), a file
`tools/fetch_basemap.sh` cuts from a Protomaps build, the name `ApiSettings.basemap_name` (`serve
--basemap-name`), by default the first region served, so one file may serve every build. With
`pmtiles` it names the file (`tiles`) and, when that script's `--assets` put them there, the labels'
fonts (`glyphs`, a MapLibre template) and the icons (`sprite`, the grayscale flavor the frontend
draws); without them the frontend draws the basemap without labels. Paths are the API's own, which
the frontend prefixes with `/api`. It also gives whether the server is public and the limits a
request meets (`limits.Limits`).

`/basemap/` serves that directory as static files (`BasemapFiles`): byte ranges (206, a range
running past the end cut at it; 416 with `Content-Range` for one starting past it), `ETag` and
`Last-Modified`, `If-Range`, `HEAD`, a day's `Cache-Control`, and the media types
`application/vnd.pmtiles` and `application/x-protobuf`. The directory is the operator's and holds
only the basemap, so a symlink in it is followed (a file kept elsewhere and linked in). A file
that is not there, or a directory that is not, is 404 `NOT_FOUND`; no path leaves the
directory.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI, Request
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from worst_route.api.routes_api import api_of
from worst_route.api.schemas import BasemapOut, ConfigOut, LimitsOut
from worst_route.config import OPENFREEMAP_STYLE

if TYPE_CHECKING:
    from worst_route.api.app import Api, ApiSettings

router = APIRouter()

SPRITE = "grayscale"  # the flavor the frontend draws (`frontend/src/map/basemap.ts`)
CACHE_CONTROL = "public, max-age=86400"
MEDIA_TYPES = {".pmtiles": "application/vnd.pmtiles", ".pbf": "application/x-protobuf"}


def basemap_dir(settings: "ApiSettings") -> Path:
    """Where the basemap's files are (the tiles, and the fonts and icons of the labels):
    `basemap_dir`, else `<data_dir>/basemap`."""
    return settings.basemap_dir or settings.data_dir / "basemap"


def basemap_name(api: "Api") -> str:
    """The basemap file's name: `basemap_name`, else the first region served."""
    return api.settings.basemap_name or api.settings.builds[0][0]


def tiles_path(api: "Api") -> Path:
    """The basemap's tiles."""
    return basemap_dir(api.settings) / f"{basemap_name(api)}.pmtiles"


def basemap_of(api: "Api") -> BasemapOut:
    """The basemap `/config` describes."""
    if api.settings.basemap == "raster":
        return BasemapOut(kind="raster")
    if api.settings.basemap == "openfreemap":
        return BasemapOut(kind="openfreemap", style=OPENFREEMAP_STYLE)
    directory = basemap_dir(api.settings)
    fonts = (directory / "fonts").is_dir()
    sprite = (directory / "sprites" / f"{SPRITE}.json").is_file()
    return BasemapOut(
        kind="pmtiles",
        tiles=f"/basemap/{basemap_name(api)}.pmtiles",
        glyphs="/basemap/fonts/{fontstack}/{range}.pbf" if fonts else None,
        sprite=f"/basemap/sprites/{SPRITE}" if sprite else None,
    )


@router.get("/config")
def client_config(request: Request) -> ConfigOut:
    """What the frontend needs to know of this server: its basemap and its limits."""
    api = api_of(request)
    limits = api.limits
    return ConfigOut(
        public=api.settings.public,
        basemap=basemap_of(api),
        limits=LimitsOut(budget_max_s=limits.budget_max_s, leash_max_km=limits.leash_max_km),
    )


class BasemapFiles(StaticFiles):
    """Static files with a day's caching, the basemap's media types, symlinks followed, and no
    error when the directory is missing (each file is then 404)."""

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = CACHE_CONTROL
        media_type = MEDIA_TYPES.get(Path(full_path).suffix)
        if media_type is not None and isinstance(response, FileResponse):
            response.headers["Content-Type"] = media_type
        return response

    async def check_config(self) -> None:
        pass  # a basemap directory may come later, or never: its files are then 404


def mount_basemap(app: FastAPI, directory: Path) -> None:
    """Serve `directory` (`basemap_dir`) under `/basemap/`."""
    files = BasemapFiles(directory=directory, check_dir=False, follow_symlink=True)
    app.mount("/basemap", files, name="basemap")
