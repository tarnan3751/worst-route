"""The service's order: the cache, the pause, a turn of the rate limit, the cache again, the
geocoder; at most `limit` candidates; a pause after a refusal."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from worst_route.geocode.base import Bbox, Candidate, GeocodeError, Geocoder
from worst_route.geocode.cache import GeocodeCache
from worst_route.geocode.fake import FakeGeocoder
from worst_route.geocode.limiter import RateLimited, RateLimiter
from worst_route.geocode.nominatim import NominatimGeocoder
from worst_route.geocode.service import Answer, GeocodeService

from .conftest import PORTLAND, POWELLS, FakeTime

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parents[1] / "fixtures" / "geocode"


def service(
    path: Path, geocoder: Geocoder, time: FakeTime, max_wait_s: float = 5.0
) -> GeocodeService:
    limiter = RateLimiter(max_wait_s=max_wait_s, clock=time.clock, sleep=time.sleep)
    cache = GeocodeCache(path / "geocode.sqlite")
    return GeocodeService(geocoder, cache, limiter, clock=time.clock)


class Failing:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str, bbox: Bbox, limit: int) -> list[Candidate]:
        self.calls += 1
        raise GeocodeError("Nominatim answered 503")

    async def aclose(self) -> None:
        pass


class Many:
    """A geocoder that answers seven places whatever the limit."""

    async def search(self, query: str, bbox: Bbox, limit: int) -> list[Candidate]:
        return [Candidate(f"place {k}", 45.5, -122.6, "shop/books") for k in range(7)]

    async def aclose(self) -> None:
        pass


async def test_a_cached_answer_takes_no_turn_and_asks_nothing(
    tmp_path: Path, fake_time: FakeTime
) -> None:
    fake = FakeGeocoder({"powell's": [POWELLS]})
    places = service(tmp_path, fake, fake_time)
    first = await places.search("portland", PORTLAND, "  Powell's ")
    assert first == Answer("powell's", [POWELLS], cached=False)
    again = await places.search("portland", PORTLAND, "POWELL'S")
    assert again == Answer("powell's", [POWELLS], cached=True)
    assert fake.calls == ["powell's"]
    closed = service(tmp_path, fake, fake_time, max_wait_s=0.0)  # refuses every turn
    assert (await closed.search("portland", PORTLAND, "powell's")).cached
    with pytest.raises(RateLimited):
        await closed.search("portland", PORTLAND, "reed college")


async def test_identical_queries_queued_behind_one_in_flight_ask_once(
    tmp_path: Path, fake_time: FakeTime
) -> None:
    fake = FakeGeocoder({"powell's": [POWELLS]})
    places = service(tmp_path, fake, fake_time)
    answers = await asyncio.gather(
        *(places.search("portland", PORTLAND, q) for q in ["Powell's", "POWELL'S", " powell's"])
    )
    assert fake.calls == ["powell's"]
    assert sorted(a.cached for a in answers) == [False, True, True]
    assert all(a.candidates == [POWELLS] for a in answers)
    assert places.limiter.next_free == 1.0  # the two answered from the cache took no interval


async def test_an_empty_answer_is_cached_and_a_failure_is_not(
    tmp_path: Path, fake_time: FakeTime
) -> None:
    places = service(tmp_path, FakeGeocoder({}), fake_time)
    assert (await places.search("portland", PORTLAND, "atlantis")).candidates == []
    assert (await places.search("portland", PORTLAND, "atlantis")).cached
    failing = Failing()
    broken = service(tmp_path / "other", failing, fake_time)
    for _ in range(2):
        with pytest.raises(GeocodeError, match="Nominatim answered 503"):
            await broken.search("portland", PORTLAND, "powell's")
    assert failing.calls == 2


async def test_at_most_the_limit_whatever_the_geocoder_answers(
    tmp_path: Path, fake_time: FakeTime
) -> None:
    answer = await service(tmp_path, Many(), fake_time).search("portland", PORTLAND, "books")
    assert [c.label for c in answer.candidates] == [f"place {k}" for k in range(5)]


@pytest.mark.parametrize("status", [429, 403])
async def test_a_refusal_pauses_the_geocoder(
    tmp_path: Path, fake_time: FakeTime, status: int
) -> None:
    body = json.loads((FIXTURES / "nominatim-portland.json").read_text())
    answers = [httpx.Response(status, json=[]), httpx.Response(200, json=body)]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return answers[min(len(requests), 2) - 1]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    nominatim = NominatimGeocoder("https://nominatim.test", "worst-route/test", client=client)
    places = service(tmp_path, nominatim, fake_time)
    places.cache.put("portland", PORTLAND, "reed college", [])
    fake_time.now = 100.0
    with pytest.raises(GeocodeError, match=r"^the geocoder refused; paused for 60 s$"):
        await places.search("portland", PORTLAND, "powell's")
    fake_time.now = 130.0
    with pytest.raises(GeocodeError, match=r"^the geocoder refused; paused for 30 s$"):
        await places.search("portland", PORTLAND, "powell's")
    assert (await places.search("portland", PORTLAND, "reed college")).cached  # the cache answers
    assert len(requests) == 1
    fake_time.now = 160.0
    answer = await places.search("portland", PORTLAND, "powell's")
    assert answer.candidates[0] == POWELLS
    assert len(requests) == 2
