"""The public limits (`api/limits.py`): each client's share of the jobs (counted by the job
manager) and of the place searches (`GeocodeQuota`, on a fake clock), the public budget, and
none of it on a server that is not public."""

from pathlib import Path
from typing import Any

import pytest

from worst_route.api.jobs import Job, JobManager, TooManyJobs, prepare
from worst_route.api.limits import GeocodeQuota
from worst_route.geocode.base import Candidate
from worst_route.geocode.cache import GeocodeCache
from worst_route.geocode.fake import FakeGeocoder
from worst_route.geocode.limiter import RateLimiter
from worst_route.geocode.service import GeocodeService
from worst_route.graph.loader import Loaded

from ..engine.graphs import at_node
from .hosting import hosted, job_body
from .support import BIG, GRID, api_input, loaded, manager, request, slow_splices

pytestmark = pytest.mark.anyio

PER_CLIENT = {"running_per_client": 1, "queued_per_client": 3}


def long_job(m: JobManager, build: Loaded, client: str | None, seed: int) -> Job:
    """A job that runs until it is cancelled (with `slow_splices`), by `client`."""
    req = request(BIG, [0, 399], seed=seed, max_iterations=1_000_000)
    return m.submit(build, prepare(build, req), input=api_input(req), client=client)


def test_a_client_searches_ten_times_in_any_minute() -> None:
    now = [0.0]
    quota = GeocodeQuota(10, clock=lambda: now[0])
    for k in range(10):
        now[0] = float(k)
        assert quota.take("a") is None
    now[0] = 9.5
    assert quota.take("a") == 51  # the first search leaves the minute at 60 s
    assert quota.take("b") is None  # each client has its own
    now[0] = 59.9
    assert quota.take("a") == 1  # at least a second
    now[0] = 60.0
    assert quota.take("a") is None  # the first has left the minute; a refusal counted nothing
    assert quota.take("a") == 1  # the second leaves at 61 s
    now[0] = 200.0
    assert quota.take("c") is None
    assert list(quota._times) == ["c"]  # idle clients forgotten


async def test_a_client_runs_one_job_at_a_time_with_three_waiting(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data, max_jobs=4, max_queued=8, **PER_CLIENT) as m:
        build = loaded(data, "big")
        a = [long_job(m, build, "a", seed) for seed in range(4)]
        # Workers are free, but a client runs one job at once: the others wait.
        assert [j.status for j in a] == ["running", "queued", "queued", "queued"]
        assert m.client_jobs("a") == (1, 3)
        assert m.refusal("a") is not None and m.refusal("b") is None
        with pytest.raises(TooManyJobs) as refused:
            long_job(m, build, "a", 9)
        assert (refused.value.running, refused.value.queued) == (1, 3)
        b = long_job(m, build, "b", 5)
        assert b.status == "running"  # not held behind a's
        anyone = [long_job(m, build, None, seed) for seed in (6, 7)]  # no client, no share
        assert [j.status for j in anyone] == ["running", "running"]
        await m.cancel(a[0])
        assert [j.status for j in a[1:]] == ["running", "queued", "queued"]  # a's next, in order
        assert m.client_jobs("a") == (1, 2) and m.refusal("a") is None


async def test_a_waiting_job_whose_client_is_busy_lets_the_next_one_start(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data, max_jobs=2, max_queued=8, **PER_CLIENT) as m:
        build = loaded(data, "big")
        a1, a2 = long_job(m, build, "a", 1), long_job(m, build, "a", 2)
        b1 = long_job(m, build, "b", 3)
        c1 = long_job(m, build, "c", 4)  # every worker busy
        assert [j.status for j in (a1, a2, b1, c1)] == ["running", "queued", "running", "queued"]
        await m.cancel(b1)
        assert (a2.status, c1.status) == ("queued", "running")  # a2 still waits for a1
        await m.cancel(a1)
        assert a2.status == "running"


async def test_a_full_server_refuses_without_the_client_s_counts(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with manager(data, max_jobs=1, max_queued=1, **PER_CLIENT) as m:
        build = loaded(data, "big")
        long_job(m, build, "a", 1)
        assert long_job(m, build, "b", 2).status == "queued"
        with pytest.raises(TooManyJobs) as refused:
            long_job(m, build, "c", 3)
        assert (refused.value.running, refused.value.queued) == (None, None)


async def test_a_public_budget_is_at_most_a_minute(data: Path) -> None:
    async with hosted(data, public=True) as (http, app):
        assert app.state.api.limits.budget_max_s == 60
        r = await http.post("/jobs", json=job_body(budget_s=61))
        assert r.status_code == 422
        assert r.json() == {
            "code": "BUDGET_TOO_LARGE",
            "message": "a budget of 61 s is over the 60 s allowed",
            "max_s": 60.0,
        }
        assert (await http.post("/jobs", json=job_body(budget_s=60))).status_code == 202
    async with hosted(data) as (http, app):  # not public: two minutes, as before
        assert app.state.api.jobs.settings.running_per_client is None
        assert (await http.post("/jobs", json=job_body(budget_s=61))).status_code == 202
        r = await http.post("/jobs", json=job_body(budget_s=121))
        assert r.status_code == 422 and r.json()["max_s"] == 120.0


def running_body(seed: int = 3) -> dict[str, Any]:
    """A job that runs until the server stops (with `slow_splices`)."""
    return job_body(seed=seed, max_iterations=1_000_000)


async def post_jobs(http: Any, n: int, **headers: str) -> list[int]:
    return [
        (await http.post("/jobs", json=running_body(k), headers=headers)).status_code
        for k in range(n)
    ]


async def test_a_public_server_shares_its_jobs_between_clients(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch, 0.05)
    async with hosted(data, public=True) as (http, _):
        assert await post_jobs(http, 4) == [202] * 4
        refused = await http.post("/jobs", json=running_body(9))
        assert refused.status_code == 429
        assert refused.json() == {
            "code": "TOO_MANY_JOBS",
            "message": "your jobs fill your share: 1 running and 3 waiting; wait for one to end",
            "running": 1,
            "queued": 3,
        }
    # Behind a trusted proxy each forwarded client has its share; the last address counts.
    async with hosted(data, client=("127.0.0.1", 1), public=True, trust_proxy=True) as (http, _):
        one = {"X-Forwarded-For": "198.51.100.1"}
        assert await post_jobs(http, 5, **one) == [202] * 4 + [429]
        assert await post_jobs(http, 1, **{"X-Forwarded-For": "198.51.100.2"}) == [202]
        spoofed = {"X-Forwarded-For": "198.51.100.3, 198.51.100.1"}
        assert await post_jobs(http, 1, **spoofed) == [429]
    # Without trust the header is the client's say: one peer, one share.
    async with hosted(data, client=("127.0.0.1", 1), public=True) as (http, _):
        statuses = [
            (
                await http.post(
                    "/jobs", json=running_body(), headers={"X-Forwarded-For": f"10.0.{k}.1"}
                )
            ).status_code
            for k in range(5)
        ]
        assert statuses == [202] * 4 + [429]


async def test_a_public_portfolio_runs_two_seeds_each_a_worker_of_the_server_s(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch, 0.05)
    settings: dict[str, Any] = {"public": True, "trust_proxy": True, "jobs": {"max_jobs": 3}}
    async with hosted(data, client=("127.0.0.1", 1), **settings) as (http, app):
        one, two = {"X-Forwarded-For": "198.51.100.1"}, {"X-Forwarded-For": "198.51.100.2"}
        r = await http.post("/jobs", json={**running_body(), "portfolio": 3}, headers=one)
        assert r.status_code == 422 and r.json() == {
            "code": "PORTFOLIO_TOO_LARGE",
            "message": "a portfolio of 3 seeds is over the 2 this server runs",
            "max": 2,
        }
        ids = []
        for body, headers in [
            ({**running_body(3), "portfolio": 2}, one),  # its two workers of the three
            ({**running_body(5), "portfolio": 2}, two),  # waits: one worker is free, not two
            (running_body(7), one),  # waits: its client's job runs
        ]:
            r = await http.post("/jobs", json=body, headers=headers)
            assert r.status_code == 202
            ids.append(r.json()["job_id"])
        jobs = app.state.api.jobs
        assert [jobs.jobs[k].status for k in ids] == ["running", "queued", "queued"]
        assert jobs.client_jobs("198.51.100.1") == (1, 1) and jobs._free() == 1


async def test_a_public_server_counts_each_client_s_searches(data: Path, tmp_path: Path) -> None:
    home = Candidate("Home", *at_node(GRID, 0), "building/house")

    async def no_sleep(seconds: float) -> None:
        pass

    def service() -> GeocodeService:
        limiter = RateLimiter(clock=lambda: 0.0, sleep=no_sleep)
        cache = GeocodeCache(tmp_path / f"geocode-{len(list(tmp_path.iterdir()))}.sqlite")
        return GeocodeService(FakeGeocoder({"home": [home]}), cache, limiter)

    query = {"q": "home", "region": "portland"}
    async with hosted(data, public=True, geocoder="nominatim", geocode=service()) as (http, app):
        answers = [await http.get("/geocode", params=query) for _ in range(11)]
        assert [a.status_code for a in answers] == [200] * 10 + [429]
        assert answers[1].json()["cached"] is True  # the cache's answers count too
        body = answers[-1].json()
        assert body["code"] == "RATE_LIMITED" and 1 <= body["retry_after_s"] <= 60
        assert body["message"].startswith("at most 10 searches a minute; try again in ")
        assert app.state.api.geocode.geocoder.calls == ["home"]
    async with hosted(data, geocoder="nominatim", geocode=service()) as (http, app):
        assert app.state.api.quota is None
        answers = [await http.get("/geocode", params=query) for _ in range(11)]
        assert [a.status_code for a in answers] == [200] * 11
