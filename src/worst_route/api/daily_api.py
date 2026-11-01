"""The daily worst route (`docs/reference/api.md`, **The daily route**).

    GET /daily    the day's trip, the same for everyone on the server, planned once

The day is the UTC date (`today`). The trip runs on the served region with the most states
(`config.Region.states`: the national build when it is served), else on the first served
(`daily_region`), between two towns of its places index (`routes_api.places_of`,
`PlaceIndex.town_points`) that `draw_trips` draws with one `numpy.random.Generator(PCG64(seed))`,
the seed the date as YYYYMMDD (20261031): two incorporated places (`DAILY_LSAD`: a city, a town or a
village), each inside the build's bbox with a node of the build within `DAILY_NEAR_KM` that can play
its stop's role (`SpatialIndex.snap` with no mask), drawn as a pair, and again, while their
straight-line distance in the build's CRS lies outside `DAILY_BAND` of the bbox's diagonal (at most
`DAILY_DRAWS` pairs). Each is labelled with its name and its state's code ("Bend, OR"). The request
is the longest route as bad as possible, at the automatic tier and leash, with that seed,
`DAILY_MAX_ITERATIONS` attempts, and the server's largest budget (`Limits.budget_max_s`) as a safety
net, with the frontend's default vehicle (`DAILY_MPG`, `DAILY_GAS_PRICE_USD`) and, as the frontend's
empty departure does, the moment its job is submitted as its departure (UTC, to the second), so its
document has its fuel and its arrival; the trip is the first pair the request's pre-flight accepts
(`jobs.prepare`: at the automatic tier a long trip runs on the primary roads and those within 3 km
of its stops, which some towns' roads do not reach), at most `DAILY_TRIES` pairs tried. A run the
iterations (or convergence) end is the same on every server with the same build, places index and
settings, one the clock ends is not, and the answer says which ended it (`stopped_because` "budget"
with fewer `attempts` than `max_iterations` is the clock).

The first `GET /daily` of a day plans it through the job manager with no client (its job takes one
of the server's slots, never a client's share; a server whose slots and queue are full answers 429
`TOO_MANY_JOBS` before anything is drawn), under a lock per date and region (`Api.daily_locks`), and
records it in the store's `daily` table (`RouteStore.daily`), one row a date, region and
`graph_version` (a build replaced under its id has another): planning, with its job, then done, with
its route, or failed. The day's request and its pre-flight are drawn once a date, region and
`graph_version` (`Api.daily_plans`, another date's forgotten), so planning the day again only
submits its job again. The server follows the job itself (`Api.daily_tasks`: its own subscription,
so the job is never an orphan however its viewers come and go; `Api.daily_jobs` while it runs, whose
cancel `DELETE /jobs/{id}` refuses, 409 `DAILY_JOB`) and records how it ended, unless the row has
moved on; a call that finds the job ended before its follower has recorded it records it itself. A
job that ends without the day's route (an error, the time limit, a cancel from within the server) is
failed, and the next call submits it again; a day whose first `DAILY_TRIES` trips the pre-flight
refuses is failed too, and stays so, answered from its drawn plan. A planning row whose job this
process does not know (the server has restarted since) is planned again. Without a places index, or
without two towns that fit, there is no daily route: 404 `NOT_FOUND`.
"""

import asyncio
import logging
import math
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from functools import cache
from itertools import islice
from typing import TYPE_CHECKING, Any

import numpy as np
from fastapi import APIRouter, Request

from worst_route.api.jobs import Job, Prepared, Subscription, TooManyJobs, prepare
from worst_route.api.routes_api import ApiError, api_of, holds, places_of, stored_route
from worst_route.api.schemas import DailyOut, DailyStop, RouteRequestIn, StopIn, Vehicle
from worst_route.api.store import DailyRow, DailyStatus
from worst_route.config import (
    DAILY_BAND,
    DAILY_DRAWS,
    DAILY_GAS_PRICE_USD,
    DAILY_LSAD,
    DAILY_MAX_ITERATIONS,
    DAILY_MPG,
    DAILY_NEAR_KM,
    DAILY_TRIES,
    REGIONS,
)
from worst_route.engine.snap import StopUnreachable
from worst_route.graph.loader import Loaded
from worst_route.graph.spatial import Role, SnapError
from worst_route.presentation.places import TownPoint

if TYPE_CHECKING:
    from worst_route.api.app import Api

log = logging.getLogger(__name__)
router = APIRouter()

# The day's request and its pre-flight (`plannable`): None for the request when no trip fits, None
# for the pre-flight when it accepts none of the trips tried.
type DailyPlan = tuple[RouteRequestIn | None, Prepared | None]


def today() -> date:
    """The day the daily route is for: the UTC date."""
    return datetime.now(UTC).date()


def daily_seed(day: date) -> int:
    """The day's seed: its date as YYYYMMDD."""
    return int(day.strftime("%Y%m%d"))


def daily_region(api: "Api") -> str:
    """The region the daily route runs on: the served one with the most states, the first
    served on a tie."""

    def states(region: str) -> int:
        known = REGIONS.get(region)
        return 0 if known is None else len(known.states)

    return max(api.builds, key=states)


def draw_trips(
    loaded: Loaded, towns: Sequence[TownPoint], seed: int
) -> Iterator[tuple[DailyStop, DailyStop]]:
    """The day's trips on `loaded`'s build, two of `towns` each, drawn with `seed` (the module's
    rule): every pair that fits, in the order drawn, among the first `DAILY_DRAWS` pairs."""
    spatial = loaded.spatial
    stops = [
        DailyStop(lat=round(t.lat, 6), lon=round(t.lon, 6), label=f"{t.name}, {t.usps}")
        for t in towns
        if t.lsad in DAILY_LSAD
    ]
    stops = [s for s in stops if holds(loaded, s.lat, s.lon)]
    if len(stops) < 2:
        return
    west, south, east, north = loaded.bbox
    diagonal = math.dist(spatial.project(south, west), spatial.project(north, east))
    low, high = (share * diagonal for share in DAILY_BAND)

    @cache
    def xy(k: int) -> tuple[float, float]:
        return spatial.project(stops[k].lat, stops[k].lon)

    @cache
    def near(k: int, role: Role) -> bool:
        try:
            spatial.snap(stops[k].lat, stops[k].lon, role, max_radius_m=DAILY_NEAR_KM * 1000)
        except SnapError:
            return False
        return True

    rng = np.random.Generator(np.random.PCG64(seed))
    for _ in range(DAILY_DRAWS):
        a, b = (int(k) for k in rng.integers(len(stops), size=2))
        fits = a != b and low <= math.dist(xy(a), xy(b)) <= high
        if fits and near(a, "start") and near(b, "end"):
            yield stops[a], stops[b]


def plannable(loaded: Loaded, towns: Sequence[TownPoint], seed: int, budget_s: float) -> DailyPlan:
    """The day's request on `loaded`'s build and its pre-flight: the first of its trips
    (`draw_trips`, at most `DAILY_TRIES`) the pre-flight accepts; the first trip's request and
    None when it accepts none of them; None twice when no trip fits."""
    first = None
    for start, end in islice(draw_trips(loaded, towns, seed), DAILY_TRIES):
        body = RouteRequestIn(
            region=str(loaded.data.meta["region"]),
            stops=[StopIn(lat=s.lat, lon=s.lon, label=s.label) for s in (start, end)],
            budget_s=budget_s,
            max_iterations=DAILY_MAX_ITERATIONS,
            seed=seed,
            vehicle=Vehicle(mpg=DAILY_MPG, gas_price_usd=DAILY_GAS_PRICE_USD),
        )
        first = first or body
        try:
            return body, prepare(loaded, body.engine_request(seed))
        except (SnapError, StopUnreachable) as error:
            log.info("the daily trip %s to %s cannot be planned: %s", start.label, end.label, error)
    return first, None


@router.get("/daily")
async def daily(request: Request) -> DailyOut:
    """The day's trip and its route: planned on the first call of the day, then as it stands."""
    api = api_of(request)
    day = today()
    region = daily_region(api)
    loaded = api.builds[region]
    build_id, version = (str(loaded.data.meta[k]) for k in ("build_id", "graph_version"))
    async with _lock(api, day.isoformat(), region):
        row = await asyncio.to_thread(api.store.daily, day.isoformat(), region, version)
        if row is not None and row.status == "planning":
            job = api.jobs.jobs.get(row.job_id or "")
            if job is not None and job.end is None:
                return _answer(row, job.input, "planning", events_url=_events_url(job))
            if job is not None:  # ended, its follower yet to record it
                row = await _record_end(api, row, job)
        if row is not None and row.status == "done" and row.route_id is not None:
            stored = await asyncio.to_thread(stored_route, api, row.route_id)
            return _answer(
                row,
                stored.input,
                "done",
                route_id=stored.route_id,
                ratio=stored.metrics_json["ratio"],
                stopped_because=stored.run["stopped_because"],
                attempts=stored.run["attempts"],
            )
        row = DailyRow(day.isoformat(), region, build_id, version, None, "failed", None)
        return await _plan(api, row)


async def _plan(api: "Api", row: DailyRow) -> DailyOut:
    """Start planning the day's trip, drawn once a day and build (`Api.daily_plans`), recorded as
    `row` (failed until it starts)."""
    loaded = api.builds[row.region]
    places = places_of(api, row.region)
    if places is None:
        message = f"no daily route: {row.region} has no places index to draw its towns from"
        raise ApiError(404, "NOT_FOUND", message)
    if api.jobs.refusal() is not None:  # before any drawing: a full server answers at once
        raise _busy()
    key = (row.date, row.region, row.graph_version)
    planned = api.daily_plans.get(key)
    if planned is None:
        seed, budget_s = daily_seed(date.fromisoformat(row.date)), api.limits.budget_max_s
        planned = await asyncio.to_thread(
            lambda: plannable(loaded, places.town_points(), seed, budget_s)
        )
        for other in [k for k in api.daily_plans if k[0] != row.date]:
            del api.daily_plans[other]  # another day's
        api.daily_plans[key] = planned
    body, prepared = planned
    if body is None:
        raise ApiError(404, "NOT_FOUND", f"no daily route: no two towns of {row.region} fit")
    departure = datetime.now(UTC).replace(microsecond=0)  # as the frontend's "empty: now"
    asked = body.model_copy(update={"departure": departure}).model_dump(mode="json")
    if prepared is None:
        why = f"the pre-flight refused its first {DAILY_TRIES} trips"
        log.warning("the daily route of %s on %s was not planned: %s", row.date, row.region, why)
        await asyncio.to_thread(api.store.put_daily, row)
        return _answer(row, asked, "failed")
    try:
        job = api.jobs.submit(loaded, prepared, input=asked, client=None)
    except TooManyJobs:  # filled while it was drawn
        raise _busy() from None
    subscription = api.jobs.subscribe(job)  # the server's own: never an orphan
    api.daily_jobs.add(job.id)  # nobody else's to cancel
    row = replace(row, status="planning", job_id=job.id)
    await asyncio.to_thread(api.store.put_daily, row)
    task = asyncio.create_task(_follow(api, row, job, subscription))
    api.daily_tasks.add(task)
    task.add_done_callback(api.daily_tasks.discard)
    return _answer(row, asked, "planning", events_url=_events_url(job))


async def _follow(api: "Api", row: DailyRow, job: Job, subscription: Subscription) -> None:
    """Wait for the day's job to end, and record how."""
    try:
        async for _ in subscription.events():
            pass
    finally:
        subscription.close()
        api.daily_jobs.discard(job.id)
    async with _lock(api, row.date, row.region):
        await _record_end(api, row, job)


async def _record_end(api: "Api", row: DailyRow, job: Job) -> DailyRow | None:
    """Record how `row`'s job ended (done with its route, else failed) unless the day's row has
    moved on (recorded already, or planned again); the row as it now stands. Under the day's
    lock."""
    now = await asyncio.to_thread(api.store.daily, row.date, row.region, row.graph_version)
    if now != row:
        return now
    if job.route_id is not None and job.stopped_because not in ("cancelled", "timeout"):
        now = replace(row, status="done", route_id=job.route_id)
    else:
        how = job.stopped_because if job.error is None else job.error.code
        log.warning("the daily route of %s on %s failed: %s", row.date, row.region, how)
        now = replace(row, status="failed")
    await asyncio.to_thread(api.store.put_daily, now)
    return now


def _lock(api: "Api", day: str, region: str) -> asyncio.Lock:
    """The day's lock on `region`: one call at a time reads and changes its row."""
    return api.daily_locks.setdefault((day, region), asyncio.Lock())


def _busy() -> ApiError:
    """TOO_MANY_JOBS for the day's job, the server's: no client's counts."""
    return ApiError(429, "TOO_MANY_JOBS", "The server is busy. Try again in a minute.")


def _events_url(job: Job) -> str:
    return f"/jobs/{job.id}/events"


def _answer(row: DailyRow, asked: dict[str, Any], status: DailyStatus, **fields: Any) -> DailyOut:
    """The answer for `row`'s trip, as its API request `asked` gives it."""
    return DailyOut(
        date=row.date,
        region=row.region,
        build_id=row.build_id,
        stops=[DailyStop(**stop) for stop in asked["stops"]],
        seed=asked["seed"],
        max_iterations=asked["max_iterations"],
        status=status,
        **fields,
    )
