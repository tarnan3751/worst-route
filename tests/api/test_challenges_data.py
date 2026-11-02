# The daily challenges on the national T3 build (WORST_ROUTE_DATA's us-t3 and its places index),
# three days from 2026-11-02 made through `GET /challenges` as a server makes them, each plan a job
# of the app's manager in a worker process: the seven each day (today's route made worse on the
# last, today, only), every challenge's towns within its band (the worst order's every two apart),
# its request as its setup asks accepted by the pre-flight `POST /jobs` runs (its stops snap, under
# strict no-left and hard mode too), its bar at its floor or above (hard mode's 1.5×), and its
# title naming it; the first day again on a server with a store of its own, the same. About a
# minute a day. Run on demand with `uv run pytest -m data`; skipped without the build or its index.
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from worst_route.api import daily_api
from worst_route.api.app import ApiSettings, create_app
from worst_route.api.challenges_api import TYPES
from worst_route.api.jobs import prepare
from worst_route.api.schemas import ChallengesOut
from worst_route.config import CHALLENGE_BANDS_KM, CHALLENGE_ORDER_KM, settings
from worst_route.engine.constraints import Constraints
from worst_route.engine.request import RouteRequest
from worst_route.graph.loader import Loaded
from worst_route.pipeline import layout
from worst_route.presentation.places import places_dir

pytestmark = [pytest.mark.data, pytest.mark.anyio]

FIRST = date(2026, 11, 2)
DAYS = [FIRST + timedelta(days=k) for k in range(3)]


@asynccontextmanager
async def national(store: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Loaded]]:
    config = ApiSettings(
        data_dir=settings().data_dir,
        builds=(("us-t3", "latest"),),
        store_path=store,
        geocoder="none",
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app.state.api.builds["us-t3"]


def check_day(loaded: Loaded, day: ChallengesOut, today: bool) -> None:
    worse = ["daily-worse"] if today else []
    assert [c.type for c in day.challenges] == [*TYPES, *worse], day.date
    for challenge in day.challenges:
        setup = challenge.setup
        if setup is None:
            continue
        xy = [loaded.spatial.project(s.lat, s.lon) for s in setup.stops]
        km = [math.dist(p, q) / 1000 for i, p in enumerate(xy) for q in xy[i + 1 :]]
        band = CHALLENGE_ORDER_KM if setup.reorder else CHALLENGE_BANDS_KM[challenge.type]
        assert all(band[0] <= d <= band[1] for d in km), (challenge.id, km)
        request = RouteRequest(
            stops=[(s.lat, s.lon) for s in setup.stops],
            objective=setup.objective,
            item=setup.item,
            brand=None if setup.brand is None else setup.brand.id,
            constraints=Constraints(**setup.constraints.model_dump()),
            reorder=setup.reorder,
            seed=1,
        )
        prepare(loaded, request)  # raises when a stop cannot be planned as set up
        goal: dict[str, Any] = challenge.goal.model_dump()
        floors = {"ratio": ("min", 1.0), "hard": ("min_ratio", 1.5), "items": ("min", 3)}
        floors |= {"brand": ("min_visits", 3), "duration": ("min_s", 3600)}
        floors |= {"no_left": ("min_ratio", 1.0)}
        name, floor = floors[goal["kind"]]
        assert goal[name] >= floor, challenge.id
        named = challenge.type in ("bridges", "pilgrimage", "worst-order")
        assert named or setup.stops[0].label in challenge.title


@pytest.mark.timeout(900)
async def test_three_days_of_challenges_plan_on_the_national_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not layout.latest_file("us-t3").exists():
        pytest.skip(f"no us-t3 build under {settings().data_dir}")
    if not (places_dir() / "us-t3.places.json").exists():
        pytest.skip("no us-t3 places index")
    monkeypatch.setattr(daily_api, "today", lambda: DAYS[-1])
    answers = []
    async with national(tmp_path / "one.sqlite") as (http, loaded):
        for day in DAYS:
            got = await http.get("/challenges", params={"date": day.isoformat()})
            assert got.status_code == 200, got.text
            answers.append(ChallengesOut.model_validate(got.json()))
            check_day(loaded, answers[-1], today=day == DAYS[-1])
    async with national(tmp_path / "two.sqlite") as (http, _):
        again = await http.get("/challenges", params={"date": DAYS[0].isoformat()})
        assert ChallengesOut.model_validate(again.json()) == answers[0]
