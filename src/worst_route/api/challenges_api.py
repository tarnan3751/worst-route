"""The daily challenges (`docs/reference/api.md`, **The daily challenges**).

    GET /challenges    the day's challenges, the same on every server with the main build

Eight a day: seven drawn from the date on the daily route's build (`daily_api.daily_region`: the
national build where it is served), between towns of its places index taken as the daily route
takes them (`daily_api.TownPool`), then, for today only, today's route made worse. In the order of
`TYPES`:

- `long-way`: A to B at a ratio or worse;
- `slow-way`: A to B under the slowest mode, taking at least so many hours, delays included;
- `no-left`: A to B under strict no-left, so no left turn at all, at a ratio or worse;
- `bridges`: at least so many bridges between A and B (`collect`);
- `pilgrimage`: at least so many locations between A and B of one of the chains of
  `CHALLENGE_BRANDS` (`brand`), one with at least `CHALLENGE_MIN_LOCATIONS` in the trip's area;
- `hard-mode`: A to B under hard mode at a ratio or worse;
- `worst-order`: four towns in the worst order (`reorder`) at a ratio or worse;
- `daily-worse`: today's route made worse once, which the browser grades against `GET /daily`.

Each of the seven draws with its own `numpy.random.Generator(PCG64(seed))`, the seed the date as
YYYYMMDD times ten plus its place in `TYPES` (1 to 7; `challenge_seed`), so none is the daily
route's. A pair's start and end are drawn uniformly from the pool again and again (at most
`CHALLENGE_DRAWS` times) and kept when they differ, lie within the type's band of straight-line
distances in the build's CRS (`CHALLENGE_BANDS_KM`) and have a node near that can play their
roles; the worst order's first town is drawn uniformly, then three among the towns within
`CHALLENGE_ORDER_KM` of it, kept when every two of the four are within it (start, via, via, end).
Each draw kept, in order and at most `CHALLENGE_TRIES`, becomes the challenge when its setup's
request (as the frontend sends it: the automatic tier and leash, as bad as possible) passes the
pre-flight `POST /jobs` runs (`jobs.prepare`) and its plan finds at least `CHALLENGE_MIN_FOUND`
bridges or visits where it counts them, for hard mode a bar of at least
`CHALLENGE_HARD_MIN_RATIO`, and for no-left a ratio of at least `CHALLENGE_NO_LEFT_MIN_RATIO`. A
plan is a job of the job manager with no client, as the daily route's is: a worker process plans
it with the type's seed, `CHALLENGE_ATTEMPTS` attempts (hard mode's `CHALLENGE_HARD_ATTEMPTS`) and
the largest budget as a safety net, its route stored like any other (a warning logged when the
clock, not the attempts, ended it). The bar is `CHALLENGE_BAR_SHARE` of what the plan reached,
rounded down: a ratio to the half below 10× and to the whole above (at least 1×), hours to the
whole hour, bridges and visits to the whole (at least `CHALLENGE_MIN_ITEMS`), and from
`CHALLENGE_TENS_FROM` to the ten; no-left's is `CHALLENGE_NO_LEFT_SHARE` of its ratio, down to the
tenth. A challenge's title names its trip and its bar ("Cross 600 bridges between A and B"), its
blurb how to play it, in one sentence. A pilgrimage's chain is drawn with the type's generator
among those (each the build's most located brand of its wikidata id, worked out once a day) with
at least `CHALLENGE_MIN_LOCATIONS` nodes in the request's area (`request_masks`, `brand_counts`),
another of them when its plan visits too few, then the next draw. A type whose draws all fail is
left out of the day.

The same date, build (`graph_version`), places index and settings give the same challenges on any
server when the attempts end each plan (or it converges) within the budget, as for the daily route
(`docs/reference/engine.md`, **Determinism**). The answer is the date's seven as kept, in memory
(`Api.challenge_days`) or in the route store (`RouteStore.challenges`, by date, region and
`graph_version`), read before anything waits, with today's route made worse when the date is today.
A date kept nowhere is made by a task of its own (`Api.challenge_tasks`), which the date's calls
wait for while the others are answered, then stored and kept; the draws and pre-flights run in
threads, the plans in the job manager's workers, and the server's stop cancels the task. The dates
answered are today and the `CHALLENGE_DAYS` days before (another is 422 `INVALID_REQUEST` on
`date`); a public server makes only today's, and answers an earlier date only when it is kept
("that day's challenges are not kept here", 422 on `date`). A full server is 429 `TOO_MANY_JOBS`
before anything is made, and without a places index there are none: 404 `NOT_FOUND`.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import TYPE_CHECKING, Annotated, Any

import numpy as np
from fastapi import APIRouter, Query, Request
from pydantic import TypeAdapter

from worst_route.api import daily_api
from worst_route.api.daily_api import TownPool, daily_region, daily_seed
from worst_route.api.jobs import Prepared, TooManyJobs, prepare
from worst_route.api.routes_api import ApiError, api_of, places_of
from worst_route.api.schemas import (
    BrandGoal,
    ChallengeBrand,
    ChallengeGoal,
    ChallengeOut,
    ChallengeSetup,
    ChallengesOut,
    DailyStop,
    DailyWorseGoal,
    DurationGoal,
    HardGoal,
    ItemsGoal,
    NoLeftGoal,
    RatioGoal,
    RouteRequestIn,
    StopIn,
)
from worst_route.config import (
    BUDGET_MAX_S,
    CHALLENGE_ATTEMPTS,
    CHALLENGE_BANDS_KM,
    CHALLENGE_BAR_SHARE,
    CHALLENGE_BRANDS,
    CHALLENGE_DAYS,
    CHALLENGE_DRAWS,
    CHALLENGE_HARD_ATTEMPTS,
    CHALLENGE_HARD_MIN_RATIO,
    CHALLENGE_MIN_FOUND,
    CHALLENGE_MIN_ITEMS,
    CHALLENGE_MIN_LOCATIONS,
    CHALLENGE_NO_LEFT_MIN_RATIO,
    CHALLENGE_NO_LEFT_SHARE,
    CHALLENGE_ORDER_KM,
    CHALLENGE_TENS_FROM,
    CHALLENGE_TRIES,
)
from worst_route.engine.constraints import Constraints
from worst_route.engine.objectives.brand import BrandRefused, brand_counts
from worst_route.engine.request import request_masks
from worst_route.engine.snap import StopUnreachable
from worst_route.graph.data import GraphData
from worst_route.graph.loader import Loaded
from worst_route.graph.spatial import Role, SnapError
from worst_route.presentation.places import TownPoint

if TYPE_CHECKING:
    from worst_route.api.app import Api

log = logging.getLogger(__name__)
router = APIRouter()

TYPES = ("long-way", "slow-way", "no-left", "bridges", "pilgrimage", "hard-mode", "worst-order")
ROLES: tuple[Role, ...] = ("start", "via", "via", "end")  # the worst order's four towns
NO_NODES = np.empty(0, dtype=np.int32)
# What each type sets up beyond the longest route as bad as possible (the pilgrimage its brand)
SETUPS: dict[str, dict[str, Any]] = {
    "slow-way": {"objective": "time"},
    "no-left": {"constraints": {"no_left": True, "strict": True}},
    "bridges": {"objective": "collect", "item": "bridge"},
    "hard-mode": {"constraints": {"hard": True}},
    "worst-order": {"reorder": True},
}
REFUSED = (SnapError, StopUnreachable, BrandRefused)  # what the pre-flight refuses
KEPT = TypeAdapter(list[ChallengeOut])  # a day's seven as the store keeps them

type DayKey = tuple[str, str, str]  # (date, region, graph_version)


class PlanFailed(Exception):  # noqa: N818 (the condition, as TooManyJobs names its own)
    """A challenge's plan ended without its route (a worker that failed, the time limit)."""


def challenge_seed(day: date, place: int) -> int:
    """The seed of the type at `place` in `TYPES` (1 to 7) on `day`: YYYYMMDD × 10 + place."""
    return daily_seed(day) * 10 + place


async def generate_day(
    api: "Api", loaded: Loaded, towns: tuple[TownPoint, ...], day: date
) -> list[ChallengeOut]:
    """The seven challenges of `day` on `loaded`'s build, between `towns` (the module's rule), each
    plan a job of `api`'s job manager; raises `TooManyJobs` when a plan finds the server full, and
    `PlanFailed` when one ends without its route."""
    started = time.monotonic()
    pool = TownPool(loaded, towns)
    chains = await asyncio.to_thread(_chains, loaded.data)
    made: list[ChallengeOut] = []
    for place, kind in enumerate(TYPES, start=1):
        seed = challenge_seed(day, place)
        challenge = await _challenge(api, loaded, pool, chains, kind, day, seed)
        if challenge is None:
            log.warning("no %s challenge on %s: no draw fits", kind, day)
        else:
            made.append(challenge)
    log.info("the challenges of %s drawn in %.1f s", day, time.monotonic() - started)
    return made


async def _challenge(
    api: "Api",
    loaded: Loaded,
    pool: TownPool,
    chains: list[int],
    kind: str,
    day: date,
    seed: int,
) -> ChallengeOut | None:
    """The challenge of type `kind` from its draws, or None when none of them fits."""
    rng = np.random.Generator(np.random.PCG64(seed))
    draws = _fours(pool, rng) if kind == "worst-order" else _pairs(pool, rng, kind)
    for _ in range(CHALLENGE_TRIES):
        towns = await asyncio.to_thread(next, draws, None)
        if towns is None:
            return None
        stops = [pool.stops[k] for k in towns]
        try:
            made = await _made(api, loaded, chains, kind, stops, rng, seed, day)
        except REFUSED as error:
            labels = ", ".join(s.label for s in stops)
            log.info("the %s challenge %s cannot be planned: %s", kind, labels, error)
            continue
        if made is not None:
            setup, goal = made
            title, blurb = _words(kind, setup, goal)
            return ChallengeOut(
                id=f"{kind}-{day}", type=kind, title=title, blurb=blurb, setup=setup, goal=goal
            )
    return None


def _pairs(pool: TownPool, rng: np.random.Generator, kind: str) -> Iterator[list[int]]:
    """The type's pairs, a start and an end within its band, in the order drawn."""
    count = len(pool.stops)
    if count < 2:
        return
    low, high = (km * 1000 for km in CHALLENGE_BANDS_KM[kind])
    for _ in range(CHALLENGE_DRAWS):
        a, b = (int(k) for k in rng.integers(count, size=2))
        fits = a != b and low <= math.dist(pool.xy(a), pool.xy(b)) <= high
        if fits and pool.near(a, "start") and pool.near(b, "end"):
            yield [a, b]


def _fours(pool: TownPool, rng: np.random.Generator) -> Iterator[list[int]]:
    """The worst order's four towns, every two within `CHALLENGE_ORDER_KM`, in the order drawn."""
    count = len(pool.stops)
    if count < 4:
        return
    xy = np.array([pool.xy(k) for k in range(count)])
    low, high = (km * 1000 for km in CHALLENGE_ORDER_KM)
    for _ in range(CHALLENGE_DRAWS):
        first = int(rng.integers(count))
        apart = np.hypot(*(xy - xy[first]).T)
        close = np.flatnonzero((apart >= low) & (apart <= high))
        if len(close) < 3:
            continue
        four = [first, *(int(k) for k in rng.choice(close, size=3, replace=False))]
        fits = all(
            low <= math.dist(pool.xy(p), pool.xy(q)) <= high
            for i, p in enumerate(four)
            for q in four[i + 1 :]
        )
        if fits and all(pool.near(k, role) for k, role in zip(four, ROLES, strict=True)):
            yield four


async def _made(
    api: "Api",
    loaded: Loaded,
    chains: list[int],
    kind: str,
    stops: list[DailyStop],
    rng: np.random.Generator,
    seed: int,
    day: date,
) -> tuple[ChallengeSetup, ChallengeGoal] | None:
    """The setup and goal of type `kind` on `stops` (pre-flighted, planned), or None when its plan
    finds too few (or makes too low a hard-mode bar or no-left ratio); raises what the pre-flight
    refuses."""
    if kind == "pilgrimage":
        return await _pilgrimage(api, loaded, chains, stops, rng, seed, day)
    setup = ChallengeSetup(stops=stops, **SETUPS.get(kind, {}))
    attempts = CHALLENGE_HARD_ATTEMPTS if kind == "hard-mode" else CHALLENGE_ATTEMPTS
    body = _body(loaded, setup, seed, attempts)
    prepared = await asyncio.to_thread(prepare, loaded, body.engine_request(seed))
    metrics = await _plan(api, loaded, prepared, body, kind, day)
    if kind == "no-left":
        ratio = metrics["ratio"]
        if ratio < CHALLENGE_NO_LEFT_MIN_RATIO:
            return None
        return setup, NoLeftGoal(min_ratio=math.floor(10 * CHALLENGE_NO_LEFT_SHARE * ratio) / 10)
    if kind == "slow-way":
        hours = (metrics["time_s"] + metrics["delay_s"]) / 3600
        return setup, DurationGoal(min_s=max(1, math.floor(CHALLENGE_BAR_SHARE * hours)) * 3600)
    if kind == "bridges":
        found = int(metrics["items"]["bridge"])
        if found < CHALLENGE_MIN_FOUND:
            return None
        return setup, ItemsGoal(item="bridge", min=_count_bar(found))
    bar = _ratio_bar(metrics["ratio"])
    if kind != "hard-mode":
        return setup, RatioGoal(min=bar)
    return None if bar < CHALLENGE_HARD_MIN_RATIO else (setup, HardGoal(min_ratio=bar))


async def _pilgrimage(
    api: "Api",
    loaded: Loaded,
    chains: list[int],
    stops: list[DailyStop],
    rng: np.random.Generator,
    seed: int,
    day: date,
) -> tuple[ChallengeSetup, ChallengeGoal] | None:
    """A pilgrimage on `stops` to a chain drawn among those with enough locations in the trip's
    area, another when its plan visits too few; None when none is left."""
    data = loaded.data
    trip = _body(loaded, ChallengeSetup(stops=stops), seed, CHALLENGE_ATTEMPTS)
    area = await asyncio.to_thread(request_masks, loaded.spatial, data, trip.engine_request(seed))
    candidates = [
        brand
        for brand in chains
        if brand_counts(data, brand, NO_NODES, area.active)["of"] >= CHALLENGE_MIN_LOCATIONS
    ]
    while candidates:
        brand = candidates.pop(int(rng.integers(len(candidates))))
        wikidata = data.strings.brand_wikidata[brand]
        assert wikidata is not None  # a chain is listed by its wikidata id
        named = ChallengeBrand(id=brand, name=data.strings.brands[brand], wikidata=wikidata)
        setup = ChallengeSetup(stops=stops, objective="brand", brand=named)
        body = _body(loaded, setup, seed, CHALLENGE_ATTEMPTS)
        prepared = await asyncio.to_thread(prepare, loaded, body.engine_request(seed))
        metrics = await _plan(api, loaded, prepared, body, "pilgrimage", day)
        visits = int(metrics["brand"]["visits"])
        if visits >= CHALLENGE_MIN_FOUND:
            return setup, BrandGoal(min_visits=_count_bar(visits))
    return None


async def _plan(
    api: "Api", loaded: Loaded, prepared: Prepared, body: RouteRequestIn, kind: str, day: date
) -> dict[str, Any]:
    """The metrics of the route the job manager plans for `prepared` (`body` its API request): a
    job with no client, as the daily route's, followed to its end; a warning when the clock ended
    it short of its attempts. Raises `TooManyJobs` when the server is full, `PlanFailed` when the
    job ends without its route."""
    job = api.jobs.submit(loaded, prepared, input=body.model_dump(mode="json"), client=None)
    subscription = api.jobs.subscribe(job)
    try:
        async for _ in subscription.events():
            pass
    finally:
        subscription.close()
    if job.route_id is None or job.stopped_because in ("cancelled", "timeout"):
        how = job.stopped_because if job.error is None else job.error.code
        raise PlanFailed(f"the {kind} challenge's plan for {day} ended without its route: {how}")
    stored = await asyncio.to_thread(api.store.get, job.route_id)
    assert stored is not None  # the job stored it
    run = stored.run
    if run["stopped_because"] == "budget" and run["attempts"] < body.max_iterations:
        message = "the %s challenge's plan for %s stopped on the clock after %d of %d attempts"
        log.warning(message, kind, day, run["attempts"], body.max_iterations)
    return stored.metrics_json


def _chains(data: GraphData) -> list[int]:
    """The build's brands of `CHALLENGE_BRANDS`, in its order: for each wikidata id, the brand of
    that id with the most locations (the first on a tie); none for an id the build lacks."""
    ids: dict[str, int] = {}
    for brand, wikidata in enumerate(data.strings.brand_wikidata):
        located = len(data.nodes_of_brand(brand))
        if wikidata in CHALLENGE_BRANDS and (
            wikidata not in ids or located > len(data.nodes_of_brand(ids[wikidata]))
        ):
            ids[wikidata] = brand
    return [ids[w] for w in CHALLENGE_BRANDS if w in ids]


def _body(loaded: Loaded, setup: ChallengeSetup, seed: int, attempts: int) -> RouteRequestIn:
    """The API request for `setup` as the frontend's would ask it, with `attempts` attempts, the
    largest budget and `seed`."""
    return RouteRequestIn(
        region=str(loaded.data.meta["region"]),
        stops=[StopIn(lat=s.lat, lon=s.lon, label=s.label) for s in setup.stops],
        objective=setup.objective,
        item=setup.item,
        brand=None if setup.brand is None else setup.brand.id,
        constraints=Constraints(**setup.constraints.model_dump()),
        reorder=setup.reorder,
        budget_s=BUDGET_MAX_S,
        max_iterations=attempts,
        seed=seed,
    )


def _ratio_bar(ratio: float) -> float:
    """`CHALLENGE_BAR_SHARE` of `ratio`, down to the half below 10× and the whole above, at
    least 1×."""
    share = CHALLENGE_BAR_SHARE * ratio
    return max(1.0, math.floor(share * 2) / 2 if share < 10 else float(math.floor(share)))


def _count_bar(found: int) -> int:
    """`CHALLENGE_BAR_SHARE` of `found`, down to the whole, at least `CHALLENGE_MIN_ITEMS`, and
    from `CHALLENGE_TENS_FROM` down to the ten."""
    bar = max(CHALLENGE_MIN_ITEMS, math.floor(CHALLENGE_BAR_SHARE * found))
    return bar if bar < CHALLENGE_TENS_FROM else bar - bar % 10


def _words(kind: str, setup: ChallengeSetup, goal: ChallengeGoal) -> tuple[str, str]:
    """The title (the trip and its bar) and the blurb (how to play it) of a challenge."""
    names = [s.label for s in setup.stops]
    trip, between = f"{names[0]} to {names[-1]}", f"{names[0]} and {names[-1]}"
    match goal:
        case RatioGoal(min=bar) if kind == "worst-order":
            towns = f"{', '.join(names[:-1])} and {names[-1]}"
            return (
                f"{towns} in the worst order at {bar:g}×",
                "Set it up fills in the four towns and the worst order; press Go and finish at "
                f"{bar:g}× the sensible drive or worse.",
            )
        case RatioGoal(min=bar):
            return (
                f"{trip} at {bar:g}×",
                "Set it up fills in the two towns and the longest route; press Go and finish at "
                f"{bar:g}× the sensible drive or worse.",
            )
        case DurationGoal(min_s=seconds):
            hours = seconds // 3600
            return (
                f"{trip} in {hours} h",
                "Set it up fills in the two towns and the slowest route; press Go and spend "
                f"{hours} hours or more at the wheel, signals and stop signs included.",
            )
        case NoLeftGoal(min_ratio=bar):
            return (
                f"{trip} with no left turns at {bar:g}×",
                "Set it up fills in the two towns and strict no-left; press Go and reach "
                f"{bar:g}× the sensible drive on right turns and straight on alone.",
            )
        case ItemsGoal(min=count):
            return (
                f"Cross {count} bridges between {between}",
                "Set it up fills in the two towns and the bridge collector; press Go and cross "
                f"{count} bridges or more on the way.",
            )
        case BrandGoal(min_visits=count):
            assert setup.brand is not None
            brand = setup.brand.name
            return (
                f"Visit {count} {brand} between {between}",
                f"Set it up fills in the two towns and a pilgrimage to {brand}; press Go and pass "
                f"{count} of them or more on the way.",
            )
        case HardGoal(min_ratio=bar):
            return (
                f"Hard mode: {trip} at {bar:g}×",
                "Set it up fills in the two towns and hard mode; press Go and reach "
                f"{bar:g}× the sensible drive with each road driven in one stretch.",
            )
    raise ValueError(f"no words for a {kind} challenge")


DAILY_WORSE_TITLE = "Make today's route worse"


def _daily_worse(day: date) -> ChallengeOut:
    """Today's route made worse, today's only."""
    return ChallengeOut(
        id=f"daily-worse-{day}",
        type="daily-worse",
        title=DAILY_WORSE_TITLE,
        blurb="Open today's route and make it worse once.",
        setup=None,
        goal=DailyWorseGoal(),
    )


@router.get("/challenges")
async def challenges(
    request: Request, day: Annotated[date | None, Query(alias="date")] = None
) -> ChallengesOut:
    """The challenges of `date` (today, the UTC date, by default): kept, else made."""
    api = api_of(request)
    today = daily_api.today()
    first = today - timedelta(days=CHALLENGE_DAYS)
    day = day or today
    if not first <= day <= today:
        message = f"challenges are for the days from {first} to {today}, not {day}"
        raise ApiError(422, "INVALID_REQUEST", message, field="date")
    region = daily_region(api)
    loaded = api.builds[region]
    key = (day.isoformat(), region, str(loaded.data.meta["graph_version"]))
    made = await _kept_or_made(api, key, loaded, day == today, first)
    return ChallengesOut(
        date=day.isoformat(),
        region=region,
        build_id=str(loaded.data.meta["build_id"]),
        challenges=made + ([_daily_worse(day)] if day == today else []),
    )


async def _kept_or_made(
    api: "Api", key: DayKey, loaded: Loaded, today: bool, first: date
) -> list[ChallengeOut]:
    """The date's seven: kept in memory, else in the store, else being made (waited for), else
    made by a task of the date's own."""
    made = api.challenge_days.get(key)
    if made is not None:
        return made
    task = api.challenge_tasks.get(key)
    if task is None:
        kept = await asyncio.to_thread(api.store.challenges, *key)
        if kept is not None:
            return _keep(api, key, KEPT.validate_json(kept), first)
        if (made := api.challenge_days.get(key)) is not None:  # made meanwhile
            return made
        task = api.challenge_tasks.get(key)  # or started meanwhile
    if task is None:
        if api.settings.public and not today:
            message = "that day's challenges are not kept here"
            raise ApiError(422, "INVALID_REQUEST", message, field="date")
        places = places_of(api, key[1])
        if places is None:
            message = f"no challenges: {key[1]} has no places index to draw their towns from"
            raise ApiError(404, "NOT_FOUND", message)
        if api.jobs.refusal() is not None:  # before anything is drawn
            raise _busy()
        task = asyncio.create_task(_make(api, key, loaded, places.town_points, first))
        api.challenge_tasks[key] = task
        task.add_done_callback(lambda done: _made_or_not(api, key, done))
    try:
        return await asyncio.shield(task)  # a caller gone leaves the date's task to finish
    except TooManyJobs:
        raise _busy() from None


async def _make(
    api: "Api",
    key: DayKey,
    loaded: Loaded,
    towns: Callable[[], tuple[TownPoint, ...]],
    first: date,
) -> list[ChallengeOut]:
    """Make the date's seven between `towns()` (worked out on first use), store them and keep
    them."""
    points = await asyncio.to_thread(towns)
    made = await generate_day(api, loaded, points, date.fromisoformat(key[0]))
    build_id = str(loaded.data.meta["build_id"])
    await asyncio.to_thread(api.store.put_challenges, *key, build_id, KEPT.dump_json(made).decode())
    return _keep(api, key, made, first)


def _keep(api: "Api", key: DayKey, made: list[ChallengeOut], first: date) -> list[ChallengeOut]:
    """Keep the date's seven in memory, forgetting the dates no longer answered."""
    for old in [k for k in api.challenge_days if k[0] < first.isoformat()]:
        del api.challenge_days[old]
    api.challenge_days[key] = made
    return made


def _made_or_not(api: "Api", key: DayKey, task: "asyncio.Task[list[ChallengeOut]]") -> None:
    """The date's task has ended: forget it, and log why when it made nothing."""
    api.challenge_tasks.pop(key, None)
    if not task.cancelled() and (error := task.exception()) is not None:
        log.warning("the challenges of %s were not made: %r", key[0], error)


def _busy() -> ApiError:
    """TOO_MANY_JOBS for the challenges' plans, the server's own: no client's counts."""
    return ApiError(429, "TOO_MANY_JOBS", "The server is busy. Try again in a minute.")
