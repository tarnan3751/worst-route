# A route drawn by hand on Portland's build through the API (WORST_ROUTE_DATA), step by step: from
# Powell's round a few blocks to the north and east, then to Pioneer Courthouse Square, each move
# taken from the moves the server offers, heading for the next waypoint and backing up from a dead
# end; then finished, stored, validated, its share image rendered. Run on demand with
# `uv run pytest -m data`; skipped without the build.
import math
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from worst_route.api.app import create_app
from worst_route.api.jobs import JobSettings
from worst_route.config import settings
from worst_route.pipeline import layout

from .support import api_settings

pytestmark = [pytest.mark.data, pytest.mark.anyio]

POWELLS, SQUARE = (45.5231, -122.6765), (45.5189, -122.6793)
WAYPOINTS = [(45.5265, -122.6790), (45.5262, -122.6720), (45.5215, -122.6725)]


@pytest.fixture
async def http(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    if not layout.latest_file("portland").exists():
        pytest.skip(f"no portland build under {settings().data_dir}")
    config = api_settings(
        settings().data_dir,
        builds=(("portland", "latest"),),
        store_path=tmp_path / "routes.sqlite",
        jobs=JobSettings(inline_workers=True),
        geocoder="none",
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    dy = (a[0] - b[0]) * 111_320.0
    dx = (a[1] - b[1]) * 111_320.0 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


async def test_a_route_drawn_by_hand_round_powell_s_is_stored_and_valid(
    http: httpx.AsyncClient,
) -> None:
    request = {
        "region": "portland",
        "stops": [{"lat": lat, "lon": lon} for lat, lon in (POWELLS, SQUARE)],
        "seed": 1,
    }

    async def draw(path: list[int]) -> dict[str, Any]:
        answer = await http.post("/draw", json={"request": request, "path": path})
        assert answer.status_code == 200, answer.text
        body: dict[str, Any] = answer.json()
        return body

    path: list[int] = []
    tried: set[tuple[tuple[int, ...], int]] = set()  # (path, edge) taken and backed out of
    goal = 0
    answer = await draw(path)
    for _ in range(2000):
        at = (answer["at"]["lat"], answer["at"]["lon"])
        while goal < len(WAYPOINTS) and metres(at, WAYPOINTS[goal]) < 90:
            goal += 1
        target = WAYPOINTS[goal] if goal < len(WAYPOINTS) else SQUARE
        moves = [m for m in answer["next"] if (tuple(path), m["edge"]) not in tried]
        final = [m for m in moves if m["stop"] == 1]
        if goal == len(WAYPOINTS) and final:
            path.append(max(final, key=lambda m: m["length_m"])["edge"])
            break
        moves = [m for m in moves if m["stop"] is None]
        if not moves:  # a dead end: back up an edge
            tried.add((tuple(path[:-1]), path[-1]))
            path.pop()
        else:
            path.append(min(moves, key=lambda m: metres((m["lat"], m["lon"]), target))["edge"])
        answer = await draw(path)
    done = await draw(path)
    assert done["done"] and done["ratio"] > 2, done["ratio"]
    assert done["reached"] == [0, len(path)]

    finished = await http.post("/draw/finish", json={"request": request, "path": path})
    assert finished.status_code == 200, finished.text
    rid, ratio = finished.json()["route_id"], finished.json()["ratio"]
    assert ratio == pytest.approx(done["ratio"])
    document = (await http.get(f"/routes/{rid}")).json()
    assert (document["run"]["backend"], document["run"]["stopped_because"]) == ("human", "drawn")
    assert document["metrics"]["ratio"] == pytest.approx(ratio)
    validated = (await http.post(f"/routes/{rid}/validate")).json()
    assert validated["ok"], validated
    image = await http.get(f"/routes/{rid}/share.png")
    assert (image.status_code, image.headers["content-type"]) == (200, "image/png")
