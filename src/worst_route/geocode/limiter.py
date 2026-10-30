"""The geocoder's rate limit (`docs/reference/api.md`, **Geocoding**): upstream requests one at a
time, each at least `1 / rps` seconds after the previous one's answer (or failure) came back,
as Nominatim's usage policy asks of the whole application.

`turn()` is a caller's turn: callers take it one at a time, in the order they came, behind one
`asyncio.Lock`. A caller that finds `waiting` callers already queued (the turn's holder is not
one of them) is refused at once, without taking a place, when `(waiting + 1) / rps` is more than
`max_wait_s` (`RateLimited`, with `ceil((waiting + 1) / rps - max_wait_s)` seconds, at least 1,
after which a retry would be let in); the cap counts intervals, not upstream latencies. A
caller cancelled while it waits leaves the queue. Within its turn a caller may find what it
wanted without asking (the service re-reads its cache) and leave at once, taking no interval.
`upstream()` is the request itself: it sleeps until `next_free`, and once the request has
answered or failed sets `next_free` to that moment plus `1 / rps`, so two requests reach the
geocoder at least the interval plus the latency apart. A caller cancelled before its request
leaves `next_free` as it was. The limit is per process (uvicorn runs one). `clock` and `sleep`
are given so the tests run on a fake clock.
"""

import asyncio
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from worst_route.config import GEOCODE_MAX_WAIT_S, GEOCODE_RPS


class RateLimited(Exception):  # noqa: N818 (the condition, as the 429 names it)
    """The queue is too long; a retry after `retry_after_s` seconds would be let in."""

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"the queue is full; retry after {retry_after_s} s")
        self.retry_after_s = retry_after_s


class RateLimiter:
    """Upstream requests one at a time, `1 / rps` seconds from one answer to the next request,
    each caller queueing at most about `max_wait_s`."""

    def __init__(
        self,
        rps: float = GEOCODE_RPS,
        max_wait_s: float = GEOCODE_MAX_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.interval_s = 1.0 / rps
        self.max_wait_s = max_wait_s
        self.clock = clock
        self.sleep = sleep
        self.next_free = -math.inf
        self.waiting = 0  # callers queued for the turn, its holder not counted
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def turn(self) -> AsyncIterator[None]:
        """The caller's turn, after those queued before it; `RateLimited` when the queue is too
        long to join."""
        needed = (self.waiting + 1) * self.interval_s
        if needed > self.max_wait_s:
            raise RateLimited(max(1, math.ceil(needed - self.max_wait_s)))
        self.waiting += 1
        try:
            await self._lock.acquire()
        finally:
            self.waiting -= 1
        try:
            yield
        finally:
            self._lock.release()

    @asynccontextmanager
    async def upstream(self) -> AsyncIterator[None]:
        """Within a turn, the request: wait until it may go; once it has answered or failed,
        the next one waits the interval from then."""
        wait = self.next_free - self.clock()
        if wait > 0:
            await self.sleep(wait)
        try:
            yield
        finally:
            self.next_free = self.clock() + self.interval_s
