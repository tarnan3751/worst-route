"""The rate limit on a fake clock: one request at a time, the interval from the last answer,
the queue's cap, and callers that leave."""

import asyncio

import pytest

from worst_route.geocode.limiter import RateLimited, RateLimiter

from .conftest import FakeTime

pytestmark = pytest.mark.anyio


def limiter(time: FakeTime, rps: float = 1.0, max_wait_s: float = 5.0) -> RateLimiter:
    return RateLimiter(rps=rps, max_wait_s=max_wait_s, clock=time.clock, sleep=time.sleep)


async def burst(
    rate: RateLimiter, time: FakeTime, n: int, latency_s: float
) -> tuple[list[float], list[int]]:
    """`n` callers at once, each asking upstream once (taking `latency_s`): when each request
    went, and the refused callers' retry_after_s."""
    sent: list[float] = []
    refused: list[int] = []

    async def caller() -> None:
        try:
            async with rate.turn(), rate.upstream():
                sent.append(time.now)
                await time.sleep(latency_s)
        except RateLimited as error:
            refused.append(error.retry_after_s)

    await asyncio.gather(*(caller() for _ in range(n)))
    return sent, refused


async def test_a_burst_of_ten_serves_six_and_refuses_four(fake_time: FakeTime) -> None:
    sent, refused = await burst(limiter(fake_time), fake_time, 10, latency_s=0.0)
    assert sent == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]  # the first at once, five queued behind it
    assert refused == [1, 1, 1, 1]


async def test_requests_are_the_interval_plus_the_latency_apart(fake_time: FakeTime) -> None:
    sent, refused = await burst(limiter(fake_time), fake_time, 6, latency_s=0.3)
    assert refused == []
    gaps = [b - a for a, b in zip(sent, sent[1:], strict=False)]
    assert gaps == pytest.approx([1.3] * 5)


async def test_the_interval_runs_from_the_answer(fake_time: FakeTime) -> None:
    rate = limiter(fake_time)
    async with rate.turn(), rate.upstream():
        await fake_time.sleep(0.3)  # answered at 0.3
    fake_time.now = 0.5
    async with rate.turn(), rate.upstream():
        assert fake_time.now == pytest.approx(1.3)
    assert fake_time.slept == [0.3, pytest.approx(0.8)]


async def test_a_failure_spaces_the_next_request_too(fake_time: FakeTime) -> None:
    rate = limiter(fake_time)
    with pytest.raises(RuntimeError):
        async with rate.turn(), rate.upstream():
            raise RuntimeError("upstream failed")
    assert rate.next_free == 1.0
    async with rate.turn(), rate.upstream():
        assert fake_time.now == 1.0


async def test_a_turn_without_a_request_takes_no_interval(fake_time: FakeTime) -> None:
    rate = limiter(fake_time)
    async with rate.turn(), rate.upstream():
        pass
    async with rate.turn():  # answered without asking: the service's cache under the lock
        pass
    assert rate.next_free == 1.0
    fake_time.now = 1.0
    async with rate.turn(), rate.upstream():
        pass
    assert fake_time.slept == []


async def test_retry_after_is_the_whole_seconds_until_the_queue_would_take_the_caller(
    fake_time: FakeTime,
) -> None:
    with pytest.raises(RateLimited) as refused:  # one 2.5 s interval, no wait allowed
        async with limiter(fake_time, rps=0.4, max_wait_s=0.0).turn():
            pass
    assert refused.value.retry_after_s == 3
    with pytest.raises(RateLimited) as refused:
        async with limiter(fake_time, max_wait_s=0.5).turn():
            pass
    assert refused.value.retry_after_s == 1  # at least 1


async def test_a_refused_caller_takes_no_place_and_a_cancelled_one_leaves(
    fake_time: FakeTime,
) -> None:
    rate = limiter(fake_time, max_wait_s=2.0)  # room for two queued behind the holder
    release = asyncio.Event()
    order: list[str] = []

    async def caller(name: str) -> None:
        async with rate.turn():
            order.append(name)
            if name == "a":
                await release.wait()

    a = asyncio.create_task(caller("a"))
    await asyncio.sleep(0)
    b = asyncio.create_task(caller("b"))
    c = asyncio.create_task(caller("c"))
    await asyncio.sleep(0)
    assert (order, rate.waiting) == (["a"], 2)
    with pytest.raises(RateLimited):
        await caller("d")
    assert rate.waiting == 2
    b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await b
    assert rate.waiting == 1
    d = asyncio.create_task(caller("d"))  # b's place is free again
    await asyncio.sleep(0)
    assert rate.waiting == 2
    release.set()
    await asyncio.gather(a, c, d)
    assert order == ["a", "c", "d"]
    assert rate.waiting == 0
