"""The geocoding proxy's order (`docs/reference/api.md`, **Geocoding**): the cache, then the
pause, then a turn of the rate limit, then the cache again, then the geocoder.

`GeocodeService.search(region, bbox, query)` normalises the query and answers from the cache
when it holds it (no turn taken, nothing sent upstream). Else, while the geocoder is paused it
refuses at once (`GeocodeError`, "the geocoder refused; paused for N s"); otherwise it queues
for a turn (`RateLimited` when the queue is too long). In its turn it reads the cache again, so
a query queued behind the same one in flight is answered from it and leaves at once, and checks
the pause again; then it asks the geocoder for at most `limit` candidates in the bbox, keeps at
most `limit` of what comes back, and caches the answer, an empty one too, before its turn ends.
A failure is never cached. A refusal (`GeocodeRefusedError`: Nominatim's 429 or 403) pauses
the geocoder `pause_s` (60 s) from when it came back. The cache's sqlite calls run in a thread,
off the event loop; `clock` is given so the tests run on a fake clock.
"""

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from worst_route.config import GEOCODE_LIMIT, GEOCODE_PAUSE_S
from worst_route.geocode.base import (
    Bbox,
    Candidate,
    GeocodeError,
    Geocoder,
    GeocodeRefusedError,
    normalise,
)
from worst_route.geocode.cache import GeocodeCache
from worst_route.geocode.limiter import RateLimiter


@dataclass(frozen=True)
class Answer:
    """A search's answer: the normalised query, the candidates, and whether the cache had them."""

    query: str
    candidates: list[Candidate]
    cached: bool


class GeocodeService:
    """A geocoder behind a cache, a rate limit and a pause after a refusal."""

    def __init__(
        self,
        geocoder: Geocoder,
        cache: GeocodeCache,
        limiter: RateLimiter,
        limit: int = GEOCODE_LIMIT,
        pause_s: float = GEOCODE_PAUSE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.geocoder = geocoder
        self.cache = cache
        self.limiter = limiter
        self.limit = limit
        self.pause_s = pause_s
        self.clock = clock
        self.paused_until = -math.inf

    async def search(self, region: str, bbox: Bbox, query: str) -> Answer:
        """The places `query` may mean in `region` (whose area is `bbox`)."""
        query = normalise(query)
        hit = await asyncio.to_thread(self.cache.get, region, bbox, query)
        if hit is not None:
            return Answer(query, hit, cached=True)
        self._check_paused()
        async with self.limiter.turn():
            hit = await asyncio.to_thread(self.cache.get, region, bbox, query)
            if hit is not None:  # answered while this one queued
                return Answer(query, hit, cached=True)
            self._check_paused()
            async with self.limiter.upstream():
                try:
                    candidates = await self.geocoder.search(query, bbox, self.limit)
                except GeocodeRefusedError:
                    self.paused_until = self.clock() + self.pause_s
                    message = f"the geocoder refused; paused for {math.ceil(self.pause_s)} s"
                    raise GeocodeError(message) from None
            candidates = candidates[: self.limit]
            await asyncio.to_thread(self.cache.put, region, bbox, query, candidates)
        return Answer(query, candidates, cached=False)

    def _check_paused(self) -> None:
        """Refuse while the geocoder is paused after a refusal."""
        left = self.paused_until - self.clock()
        if left > 0:
            raise GeocodeError(f"the geocoder refused; paused for {math.ceil(left)} s")

    async def aclose(self) -> None:
        """Release the geocoder's client."""
        await self.geocoder.aclose()
