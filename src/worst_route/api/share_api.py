"""The share image endpoint (`docs/reference/api.md`, **Share image**;
`docs/reference/presentation.md`, **Share image**).

    GET  /routes/{id}/share.png?theme=<id>    the route's share image, a 1200 × 630 PNG
    HEAD /routes/{id}/share.png?theme=<id>    the same headers, no body (link unfurlers ask first)

An id that is not a route id (12 lowercase hex digits) is 404 `NOT_FOUND`, before any file is
looked at; a `theme` that is not one of the frontend's (`share_palettes.PALETTES`; classic when
there is none) is 422 `INVALID_REQUEST` on `field: "theme"`. An image already rendered is served
from `<share_dir>/<id>-<theme>-v<SHARE_IMAGE_VERSION>.png` (the share directory is `share/`
beside the route store, as `geocode.sqlite` is). Otherwise the route is read from the store (404
`NOT_FOUND`) and drawn in the theme's palette on its build (409 `BUILD_UNAVAILABLE`, as the other
route endpoints answer) with the outlines of its region's places index (`routes_api.places_of`),
its comparison line, and its objective and `time_ratio` as its document has them (a slowest-time
route shows its time ratio), in a worker thread, under a lock per route and theme so that
concurrent first requests for an image render it once; the file is written whole (a temporary
name of its own, then a rename) before the lock is released, and the lock is forgotten whatever
happened. Every image is answered with `Cache-Control: public, max-age=86400`; a HEAD renders it
too, to give its length.

matplotlib is imported by the first render, never with this module, so the API starts without it
(its first import may build its font cache, which takes seconds on a cold machine).
`SHARE_IMAGE_VERSION` names the drawing (`presentation/share_image.py`): raise it whenever the
drawing changes, so images drawn before are left behind rather than served.
"""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import Response

from worst_route.api.routes_api import (
    ApiError,
    api_of,
    build_of,
    geometry_of,
    places_of,
    stored_delays,
    stored_route,
)
from worst_route.api.sse import time_ratio
from worst_route.api.store import StoredRoute
from worst_route.presentation.comparisons import comparison_line
from worst_route.presentation.share_palettes import PALETTES

if TYPE_CHECKING:
    from worst_route.api.app import Api

router = APIRouter()

SHARE_IMAGE_VERSION = 2
ROUTE_ID = re.compile(r"[0-9a-f]{12}")
CACHE_CONTROL = "public, max-age=86400"


def share_path(share_dir: Path, route_id: str, theme: str) -> Path:
    """Where the share image of `route_id` in `theme` is kept, for this version of the drawing."""
    return share_dir / f"{route_id}-{theme}-v{SHARE_IMAGE_VERSION}.png"


@router.api_route("/routes/{route_id}/share.png", methods=["GET", "HEAD"], response_class=Response)
async def share_image(route_id: str, request: Request, theme: str = "classic") -> Response:
    """The route's share image in `theme`, rendered once and then served from the cache."""
    api = api_of(request)
    if not ROUTE_ID.fullmatch(route_id):
        raise ApiError(404, "NOT_FOUND", f"no route {route_id}")
    if theme not in PALETTES:
        message = f"unknown theme {theme!r}; known: {', '.join(PALETTES)}"
        raise ApiError(422, "INVALID_REQUEST", message, field="theme")
    path = share_path(api.share_dir, route_id, theme)
    if not path.exists():
        stored = await asyncio.to_thread(stored_route, api, route_id)
        key = (route_id, theme)
        lock = api.share_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                if not path.exists():
                    png = await asyncio.to_thread(_render, api, stored, theme)
                    await asyncio.to_thread(_write, path, png)
        finally:
            # Forgotten by whoever is done with it first; a request still waiting holds its own
            # reference, and finds the file (or renders again) once it gets the lock.
            if api.share_locks.get(key) is lock:
                del api.share_locks[key]
    headers = {"Cache-Control": CACHE_CONTROL}
    if request.method == "HEAD":
        headers["Content-Length"] = str(path.stat().st_size)
        return Response(media_type="image/png", headers=headers)
    body = await asyncio.to_thread(path.read_bytes)
    return Response(body, media_type="image/png", headers=headers)


def _render(api: "Api", stored: StoredRoute, theme: str) -> bytes:
    from worst_route.presentation.share_image import render_share_image  # imports matplotlib

    loaded = build_of(api, stored)
    data = loaded.data
    m = stored.metrics_json
    delay_s, baseline_delay_s = stored_delays(data, stored)
    ratio = time_ratio(float(m["time_s"]), delay_s, float(m["baseline_time_s"]), baseline_delay_s)
    return render_share_image(
        data,
        geometry_of(loaded),
        stored.nodes,
        stored.edges,
        {**m, "time_ratio": ratio},  # as the route's document has it
        places_of(api, stored.region),
        comparison_line(stored.metrics.length_m),
        baseline_edges=stored.baseline_edges,
        objective=stored.request.objective,
        palette=PALETTES[theme],
    )


def _write(path: Path, png: bytes) -> None:
    """Write `png` as `path` whole: to a temporary name of its own, then renamed, so writers
    of the same image (two servers on one store, or a request after a failed render) never
    share a part file, and the last rename wins."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, part = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(png)
        os.replace(part, path)
    finally:
        Path(part).unlink(missing_ok=True)  # gone once renamed; a failed write leaves nothing
