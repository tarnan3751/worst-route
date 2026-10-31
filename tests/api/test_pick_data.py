# The pick on Portland's, Oregon's and the national T1 build, served together from WORST_ROUTE_DATA
# with their places indexes: a trip within Portland on Portland's build, one within Oregon on
# Oregon's all roads, one across states on the national build (Portland → Vancouver, WA, too,
# though Portland's bbox holds both), and a stop beyond them all. Run on demand with
# `uv run pytest -m data`; skipped without the builds or the national places index.
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from worst_route.api.app import ApiSettings, create_app
from worst_route.api.jobs import JobSettings
from worst_route.config import settings
from worst_route.pipeline import layout
from worst_route.presentation.places import places_dir

pytestmark = [pytest.mark.data, pytest.mark.anyio]

PORTLAND, BEND = {"lat": 45.5231, "lon": -122.6765}, {"lat": 44.0582, "lon": -121.3153}
REED = {"lat": 45.4817, "lon": -122.6263}
CHICAGO, VANCOUVER = {"lat": 41.8781, "lon": -87.6298}, {"lat": 45.6270, "lon": -122.6727}
HONOLULU = {"lat": 21.3069, "lon": -157.8583}


@pytest.fixture
async def http(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    for region in ("portland", "oregon", "us-t1"):
        if not layout.latest_file(region).exists():
            pytest.skip(f"no {region} build under {settings().data_dir}")
    if not (places_dir() / "us-t1.parquet").exists():
        pytest.skip("no national places index")
    config = ApiSettings(
        data_dir=settings().data_dir,
        builds=(("portland", "latest"), ("oregon", "latest"), ("us-t1", "latest")),
        store_path=tmp_path / "routes.sqlite",
        jobs=JobSettings(inline_workers=True),
        geocoder="none",
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def pick(http: httpx.AsyncClient, *stops: dict[str, float]) -> httpx.Response:
    return await http.post("/regions/pick", json={"stops": list(stops)})


async def test_a_trip_within_portland_runs_on_portland_s_build(http: httpx.AsyncClient) -> None:
    answer = (await pick(http, PORTLAND, REED)).json()
    assert (answer["region"], answer["reason"]) == ("portland", "all stops within Portland")


async def test_a_trip_within_oregon_runs_on_oregon_s_build(http: httpx.AsyncClient) -> None:
    answer = (await pick(http, PORTLAND, BEND)).json()
    assert (answer["region"], answer["reason"]) == ("oregon", "all stops in Oregon")


async def test_a_trip_across_states_runs_on_the_national_build(http: httpx.AsyncClient) -> None:
    answer = (await pick(http, PORTLAND, CHICAGO)).json()
    assert (answer["region"], answer["reason"]) == ("us-t1", "stops in Oregon and Illinois")
    answer = (await pick(http, PORTLAND, BEND, VANCOUVER)).json()
    assert (answer["region"], answer["reason"]) == ("us-t1", "stops in Oregon and Washington")
    answer = (await pick(http, PORTLAND, VANCOUVER)).json()  # both in Portland's bbox
    assert (answer["region"], answer["reason"]) == ("us-t1", "stops in Oregon and Washington")


async def test_a_stop_beyond_both_is_refused(http: httpx.AsyncClient) -> None:
    answer = await pick(http, PORTLAND, HONOLULU)
    assert answer.status_code == 422
    assert (answer.json()["code"], answer.json()["stop_index"]) == ("STOP_OUTSIDE_REGION", 1)
