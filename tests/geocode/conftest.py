"""Fixtures for the geocoding tests: anyio on asyncio for the async tests (`@pytest.mark.anyio`),
and a fake clock. No test here reaches a geocoder or builds a real HTTP client: they use
`FakeGeocoder`, or a `NominatimGeocoder` on an `httpx.MockTransport` (`support.offline_clients`
for one that makes its own), and `tests/conftest.py` refuses real transports."""

import asyncio

import pytest

from worst_route.geocode.base import Candidate

PORTLAND = (-123.05, 45.25, -122.35, 45.72)  # config.REGIONS["portland"].bbox
POWELLS = Candidate(
    "Powell's City of Books, 1005, West Burnside Street, Pearl District, Portland, Multnomah "
    "County, Oregon, 97209, United States",
    45.5231165,
    -122.6812548,
    "shop/books",
)


class FakeTime:
    """A clock the test moves (`now`), and a sleep that moves it, records how long it was asked
    to wait and lets the other tasks run (only a turn's holder sleeps: the rest wait on its
    lock)."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()
