"""GET /geocode over httpx in process: the answer, the cache across casing and spacing, and every
refusal. The geocoder is a `FakeGeocoder`, one that fails, or a `NominatimGeocoder` on an
`httpx.MockTransport`; the rate limit's clock stands still: nothing here reaches Nominatim or
builds a real HTTP client."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from worst_route.api.app import ApiSettings, create_app
from worst_route.api.geocode_api import BUSY
from worst_route.config import settings as machine_settings
from worst_route.geocode.base import Bbox, Candidate, GeocodeError, Geocoder
from worst_route.geocode.cache import GeocodeCache
from worst_route.geocode.fake import FakeGeocoder
from worst_route.geocode.limiter import RateLimiter
from worst_route.geocode.nominatim import NominatimGeocoder
from worst_route.geocode.service import GeocodeService

from ..engine.graphs import at_node
from ..geocode.support import offline_clients
from .support import GRID, GRID_BBOX, PORTLAND

pytestmark = pytest.mark.anyio

HOME = Candidate("Home, 1 Lattice Street, Portland", *at_node(GRID, 0), "building/house")
WORK = Candidate("Work, 99 Lattice Street, Portland", *at_node(GRID, 99), "office/company")


class Failing:
    async def search(self, query: str, bbox: Bbox, limit: int) -> list[Candidate]:
        raise GeocodeError("Nominatim answered 503")

    async def aclose(self) -> None:
        pass


def service(path: Path, geocoder: Geocoder, max_wait_s: float = 5.0) -> GeocodeService:
    """The geocoder behind a cache in `path` and a rate limit whose clock stands still and whose
    sleeps return at once; with `max_wait_s` under a second it lets no caller in."""
    limiter = RateLimiter(max_wait_s=max_wait_s, clock=lambda: 0.0, sleep=_no_sleep)
    return GeocodeService(geocoder, GeocodeCache(path / "geocode.sqlite"), limiter)


async def _no_sleep(seconds: float) -> None:
    pass


@asynccontextmanager
async def serving(
    data: Path, geocode: GeocodeService | None = None, **settings: Any
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    config = ApiSettings(data_dir=data, builds=PORTLAND, backend="python", **settings)
    app = create_app(config, geocode=geocode)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


async def test_a_search_answers_and_the_same_query_comes_from_the_cache(
    data: Path, tmp_path: Path
) -> None:
    fake = FakeGeocoder({"home sweet home": [HOME, WORK]})
    async with serving(data, service(tmp_path, fake)) as (client, _):
        first = await client.get("/geocode", params={"q": "Home sweet home", "region": "portland"})
        assert first.status_code == 200, first.text
        candidate = {"label": HOME.label, "lat": HOME.lat, "lon": HOME.lon}
        assert first.json() == {
            "query": "home sweet home",
            "region": "portland",
            "candidates": [
                {**candidate, "category": "building/house"},
                {"label": WORK.label, "lat": WORK.lat, "lon": WORK.lon, "category": WORK.category},
            ],
            "cached": False,
        }
        again = await client.get(
            "/geocode", params={"q": "  HOME \t sweet  Home ", "region": "portland"}
        )
        assert again.json() == {**first.json(), "cached": True}
    assert fake.calls == ["home sweet home"]


async def test_at_most_five_candidates(data: Path, tmp_path: Path) -> None:
    many = [Candidate(f"place {k}", HOME.lat, HOME.lon, "shop/books") for k in range(7)]
    async with serving(data, service(tmp_path, FakeGeocoder({"books": many}))) as (client, _):
        answer = (await client.get("/geocode", params={"q": "books", "region": "portland"})).json()
    assert [c["label"] for c in answer["candidates"]] == [f"place {k}" for k in range(5)]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"q": ""}, "the query is empty"),
        ({"q": " \t\n "}, "the query is empty"),
        ({}, "the query is empty"),
        ({"q": "x" * 201}, "the query is over 200 characters"),
        ({"q": "  ".join(["xx"] * 68)}, "the query is over 200 characters"),  # 203 once normalised
    ],
)
async def test_an_empty_or_long_query_is_refused(
    data: Path, tmp_path: Path, params: dict[str, str], message: str
) -> None:
    fake = FakeGeocoder({})
    async with serving(data, service(tmp_path, fake)) as (client, _):
        refused = await client.get("/geocode", params={**params, "region": "portland"})
        assert refused.status_code == 422
        assert refused.json() == {"code": "INVALID_REQUEST", "message": message, "field": "q"}
        long = {"q": "  " + "x" * 200 + "  ", "region": "portland"}  # 200 once normalised
        assert (await client.get("/geocode", params=long)).status_code == 200
    assert fake.calls == ["x" * 200]


async def test_a_region_missing_or_not_served_is_refused(data: Path, tmp_path: Path) -> None:
    async with serving(data, service(tmp_path, FakeGeocoder({}))) as (client, _):
        missing = await client.get("/geocode", params={"q": "home"})
        assert missing.status_code == 422
        assert missing.json()["code"] == "INVALID_REQUEST"
        assert missing.json()["errors"][0]["loc"] == ["query", "region"]
        unknown = await client.get("/geocode", params={"q": "home", "region": "atlantis"})
        assert unknown.status_code == 422
        assert unknown.json() == {
            "code": "UNKNOWN_REGION",
            "message": "region 'atlantis' is not served here (portland)",
        }


async def test_a_full_queue_is_rate_limited_and_the_cache_still_answers(
    data: Path, tmp_path: Path
) -> None:
    fake = FakeGeocoder({"home": [HOME]})
    geocode = service(tmp_path, fake, max_wait_s=0.5)
    geocode.cache.put("portland", tuple(GRID_BBOX), "home", [HOME])
    async with serving(data, geocode) as (client, _):
        busy = await client.get("/geocode", params={"q": "work", "region": "portland"})
        assert busy.status_code == 429
        assert busy.json() == {"code": "RATE_LIMITED", "message": BUSY, "retry_after_s": 1}
        cached = await client.get("/geocode", params={"q": "HOME", "region": "portland"})
        assert cached.json()["cached"] is True  # the cache needs no turn
    assert fake.calls == []
    assert BUSY == "Place search is busy. Try again in a few seconds."


async def test_a_refusal_upstream_pauses_the_geocoder(data: Path, tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    nominatim = NominatimGeocoder("https://nominatim.test", "worst-route/test", client=client)
    async with serving(data, service(tmp_path, nominatim)) as (api, _):
        first = await api.get("/geocode", params={"q": "home", "region": "portland"})
        assert first.status_code == 502
        assert first.json() == {
            "code": "GEOCODER_ERROR",
            "message": "the geocoder refused; paused for 60 s",
        }
        later = await api.get("/geocode", params={"q": "work", "region": "portland"})
        assert later.status_code == 502
        assert later.json()["message"].startswith("the geocoder refused; paused for ")
    assert len(requests) == 1


async def test_a_failing_geocoder_is_a_geocoder_error(data: Path, tmp_path: Path) -> None:
    async with serving(data, service(tmp_path, Failing())) as (client, _):
        failed = await client.get("/geocode", params={"q": "home", "region": "portland"})
    assert failed.status_code == 502
    assert failed.json() == {"code": "GEOCODER_ERROR", "message": "Nominatim answered 503"}


async def test_without_a_geocoder_the_search_is_disabled(data: Path) -> None:
    async with serving(data, geocoder="none") as (client, app):
        assert app.state.api.geocode is None
        disabled = await client.get("/geocode", params={"q": "home", "region": "portland"})
        assert disabled.status_code == 503
        assert disabled.json() == {
            "code": "GEOCODER_DISABLED",
            "message": "this server has no geocoder",
        }
        unknown = await client.get("/geocode", params={"q": "home", "region": "atlantis"})
        assert unknown.json()["code"] == "UNKNOWN_REGION"
    assert not (data / "geocode.sqlite").exists()


async def test_by_default_it_asks_nominatim_as_the_project_with_its_cache_by_the_store(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WORST_ROUTE_USER_AGENT", raising=False)
    machine_settings.cache_clear()
    made = offline_clients(monkeypatch)
    store = tmp_path / "lane" / "routes.sqlite"
    settings = {
        "store_path": store,
        "nominatim_url": "https://nominatim.test",
        "contact": "ops@example.org",
    }
    async with serving(data, **settings) as (_, app):
        geocode = app.state.api.geocode
        nominatim = geocode.geocoder
        assert isinstance(nominatim, NominatimGeocoder)
        assert made == [nominatim.client]  # its own client, on a MockTransport here
        assert nominatim.base_url == "https://nominatim.test"
        assert nominatim.user_agent == (
            "worst-route/0.1.0 (https://github.com/tarnan3751/worst-route; ops@example.org)"
        )
        assert geocode.cache.path == tmp_path / "lane" / "geocode.sqlite"
        assert geocode.cache.path.exists()
        assert (geocode.limiter.interval_s, geocode.limiter.max_wait_s) == (1.0, 5.0)
        assert (geocode.limit, geocode.pause_s) == (5, 60.0)
    assert nominatim.client.is_closed  # the lifespan closed its own client
    assert not (data / "geocode.sqlite").exists()
    # An embedding (the desktop app) names a User-Agent of its own; the contact still goes in it.
    agent = "worst-route-desktop/0.1.0 (https://github.com/tarnan3751/worst-route)"
    async with serving(data, **settings, user_agent=agent) as (_, app):
        assert app.state.api.geocode.geocoder.user_agent == (
            "worst-route-desktop/0.1.0 (https://github.com/tarnan3751/worst-route; ops@example.org)"
        )
    machine_settings.cache_clear()
