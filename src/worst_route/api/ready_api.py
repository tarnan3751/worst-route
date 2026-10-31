"""The readiness endpoint (`docs/reference/api.md`, **Endpoints**; `docs/reference/hosting.md`).

    GET /ready    {status, checks: [{name, ok, detail}]}: 200 when ready, 503 when not

Where `GET /health` says the process lives, `/ready` says the deployment is whole: the served
build still on disk at the `graph_version` loaded (`build`), the route store answering (`store`),
the region's places index loaded (`places`), and with the `pmtiles` basemap its file there
(`basemap`; the labels' fonts and icons are optional). A failed check says what is missing and
its remedy, never a path on the server or an error of its system, which go to the log instead.
"""

import json
import logging
import sqlite3
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response

from worst_route.api.basemap_api import basemap_of, tiles_path
from worst_route.api.routes_api import api_of
from worst_route.api.schemas import ReadyCheck, ReadyOut

if TYPE_CHECKING:
    from worst_route.api.app import Api

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/ready", responses={503: {"model": ReadyOut}})
def ready(request: Request, response: Response) -> ReadyOut:
    """Whether the deployment has everything it serves from."""
    api = api_of(request)
    checks = [_build(api), _store(api), _places(api), _basemap(api)]
    ok = all(c.ok for c in checks)
    response.status_code = 200 if ok else 503
    return ReadyOut(status="ready" if ok else "not_ready", checks=checks)


def _build(api: "Api") -> ReadyCheck:
    for region, loaded in api.builds.items():
        meta = loaded.data.meta
        name = f"{region}/{meta['build_id']}"
        path = api.settings.data_dir / "graphs" / region / meta["build_id"] / "meta.json"
        try:
            on_disk = json.loads(path.read_text())["graph_version"]
        except (OSError, ValueError, KeyError) as error:
            log.warning("ready: the build %s does not read: %s", name, error)
            detail = f"the build {name} is gone from the data directory: restore it, or restart"
            return ReadyCheck(name="build", ok=False, detail=detail)
        if on_disk != meta["graph_version"]:
            detail = f"the build {name} was replaced on disk: restart to serve it"
            return ReadyCheck(name="build", ok=False, detail=detail)
    names = ", ".join(f"{r}/{b.data.meta['build_id']}" for r, b in api.builds.items())
    return ReadyCheck(name="build", ok=True, detail=names)


def _store(api: "Api") -> ReadyCheck:
    store = api.store
    try:
        store.get("")  # a query: no route has an empty id
    except sqlite3.Error as error:
        log.warning("ready: the route store %s does not answer: %s", store.path, error)
        detail = "the route store does not answer: check that its directory is writable"
        return ReadyCheck(name="store", ok=False, detail=detail)
    return ReadyCheck(name="store", ok=True, detail="the route store answers")


def _places(api: "Api") -> ReadyCheck:
    region = api.settings.region
    if api.places is None:
        detail = f"no places index of {region}: run `worst-route aux places --region {region}`"
        return ReadyCheck(name="places", ok=False, detail=detail)
    return ReadyCheck(name="places", ok=True, detail=f"{region}: {len(api.places.towns)} towns")


def _basemap(api: "Api") -> ReadyCheck:
    basemap = basemap_of(api)
    if basemap.kind == "raster":
        return ReadyCheck(name="basemap", ok=True, detail="raster: OpenStreetMap's tiles")
    path = tiles_path(api)
    if not path.is_file():
        region = api.settings.region
        detail = f"no basemap file {path.name}: run `tools/fetch_basemap.sh {region}`"
        return ReadyCheck(name="basemap", ok=False, detail=detail)
    labels = "with labels" if basemap.glyphs and basemap.sprite else "without labels (--assets)"
    detail = f"{path.name}, {path.stat().st_size / 1e6:.1f} MB, {labels}"
    return ReadyCheck(name="basemap", ok=True, detail=detail)
