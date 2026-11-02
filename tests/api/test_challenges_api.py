"""The daily challenges (`GET /challenges`) on the 20 × 20 lattice, its rows and columns named, with
bridges on three rows, four brands (two listed chains with enough locations, one without, one
unlisted), a places index of towns placed on it and the bands scaled to it, the day fixed, each
plan a job of the app's manager on inline workers: the seven in order with their setups, every draw
within its band (the worst order's four every two apart), the bars the plans' own rounded down
(hard mode's planned longer, a bar under its least passed over), the same day twice the same and
another day others, the next draw when the pre-flight refuses one, a type without a fitting draw
left out, too few bridges passed over, the pilgrimage's chain and its fallbacks, a warning for a
plan the clock ended; and over httpx in process, the answer kept per date (made once for two
concurrent first calls, today's route made worse today's only), kept in the store across a
restart, a date being made holding up no other, the same on two servers, the window of dates, a
public server making only today's, a full server, the server's stop ending a date's making, and
none without a places index."""

import asyncio
import json
import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from worst_route.api import challenges_api, daily_api
from worst_route.api.app import Api
from worst_route.api.challenges_api import TYPES, challenge_seed, generate_day
from worst_route.api.jobs import Prepared, TooManyJobs
from worst_route.api.schemas import ChallengeOut, RouteRequestIn
from worst_route.engine.constraints import Constraints
from worst_route.engine.objectives.brand import brand_counts
from worst_route.engine.objectives.collect import count_items
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.engine.snap import StopUnreachable
from worst_route.graph.data import EdgeFlag, GraphData
from worst_route.graph.loader import Loaded
from worst_route.presentation.places import TownPoint, places_dir, write_places

from ..engine.graphs import at_node, lattice
from ..presentation.support import STATES_FILE, WIDE
from .hosting import hosted
from .support import save_build

pytestmark = pytest.mark.anyio

DAY = date(2026, 11, 2)
REGION = "lattice"
SERVED = ((REGION, "latest"),)
COLS = 20
METRES_PER_DEGREE_LON = 78_000.0  # at the lattice's latitude, near enough


def node(row: int, col: int) -> int:
    return row * COLS + col


# Bridges along rows 4, 9 and 14; Alpha Mart (Q1) and Gamma Gas (Q3) at 40 nodes each, Beta
# Burger (Q2) at 3, Local (no wikidata) at 60.
BRIDGES = {
    (node(r, c), node(r, c + 1)): {"flags": EdgeFlag.BRIDGE} for r in (4, 9, 14) for c in range(19)
}
BRANDS = {
    "Alpha Mart": [node(r, c) for r in range(1, 19, 2) for c in range(2, 19, 4)][:40],
    "Beta Burger": [node(3, 3), node(10, 10), node(16, 16)],
    "Gamma Gas": [node(r, c) for r in range(2, 19, 2) for c in range(1, 19, 4)][:40],
    "Local": [node(r, c) for r in range(0, 20, 3) for c in range(0, 20, 2)][:60],
}
WIKIDATA = ["Q1", "Q2", "Q3", None]  # by brand id: the names in order


def grid(bridges: bool = True) -> GraphData:
    g = lattice(20, 20, named=True, roads=BRIDGES if bridges else None, brands=BRANDS)
    return replace(g, strings=replace(g.strings, brand_wikidata=WIKIDATA))


def town(name: str, row: int, col: int) -> TownPoint:
    lat, lon = at_node(grid(), node(row, col))
    return TownPoint(name, "WL", "25", lon + 20 / METRES_PER_DEGREE_LON, lat + 20 / 111_000)


TOWNS = [
    town("Alder", 1, 1),
    town("Birch", 1, 12),
    town("Cedar", 6, 17),
    town("Dogwood", 9, 9),
    town("Elm", 12, 2),
    town("Fir", 17, 8),
    town("Gum", 15, 17),
    town("Hazel", 4, 6),
]


def write_index(data: Path, towns: list[TownPoint] = TOWNS) -> None:
    """The places index of `towns`, each a square 40 m a side around its point."""
    half = 0.0002
    features = [
        {
            "type": "Feature",
            "properties": {
                "STATEFP": "91",
                "GEOID": f"91{k:05d}",
                "NAME": t.name,
                "NAMELSAD": t.name,
                "LSAD": t.lsad,
                "STUSPS": t.usps,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [t.lon - half, t.lat - half],
                        [t.lon + half, t.lat - half],
                        [t.lon + half, t.lat + half],
                        [t.lon - half, t.lat + half],
                        [t.lon - half, t.lat - half],
                    ]
                ],
            },
        }
        for k, t in enumerate(towns)
    ]
    path = data / "towns.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    write_places(
        REGION,
        WIDE,
        states=STATES_FILE,
        outlines=STATES_FILE,
        places=[path],
        out_dir=places_dir(data),
    )


def served_data(data: Path, g: GraphData | None = None, index: bool = True) -> Path:
    """`data` holding the lattice as the region's build (`g`, else the grid) and, with `index`,
    the towns' places index."""
    save_build(data, g or grid(), "grid", region=REGION)
    if index:
        write_index(data)
    return data


@pytest.fixture(autouse=True)
def scaled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bands scaled to the lattice (1.9 km a side), the plans short, the chains the
    fixture's, the day fixed."""
    bands = dict.fromkeys(challenges_api.CHALLENGE_BANDS_KM, (0.5, 1.5))
    monkeypatch.setattr(challenges_api, "CHALLENGE_BANDS_KM", bands)
    monkeypatch.setattr(challenges_api, "CHALLENGE_ORDER_KM", (0.3, 1.5))
    monkeypatch.setattr(challenges_api, "CHALLENGE_ATTEMPTS", 150)
    monkeypatch.setattr(challenges_api, "CHALLENGE_HARD_ATTEMPTS", 400)
    monkeypatch.setattr(challenges_api, "CHALLENGE_BRANDS", ("Q2", "Q1", "Q3"))
    monkeypatch.setattr(daily_api, "today", lambda: DAY)


@pytest.fixture
def served(tmp_path: Path) -> Path:
    return served_data(tmp_path / "data")


@asynccontextmanager
async def app_on(data: Path, **settings: Any) -> AsyncIterator[tuple[httpx.AsyncClient, Api]]:
    """The app serving the lattice from `data`, its jobs on inline workers."""
    async with hosted(data, builds=SERVED, **settings) as (http, app):
        yield http, app.state.api


async def generated(api: Api, day: date = DAY) -> list[ChallengeOut]:
    """The seven of `day`, made afresh (nothing kept)."""
    places = api.places[REGION]
    assert places is not None
    return await generate_day(api, api.builds[REGION], places.town_points(), day)


def by_type(made: list[ChallengeOut]) -> dict[str, ChallengeOut]:
    return {c.type: c for c in made}


# --- The generator ---------------------------------------------------------------------------


async def test_a_day_holds_the_seven_in_order_with_their_setups(served: Path) -> None:
    async with app_on(served) as (_, api):
        made = await generated(api)
    assert [c.type for c in made] == list(TYPES)
    assert [c.id for c in made] == [f"{t}-2026-11-02" for t in TYPES]
    setups = {c.type: c.setup for c in made}
    assert all(s is not None and s.target.kind == "max" for s in setups.values())
    assert {k: (s.objective, s.item) for k, s in setups.items() if s} == {
        "long-way": ("distance", None),
        "slow-way": ("time", None),
        "no-left": ("distance", None),
        "bridges": ("collect", "bridge"),
        "pilgrimage": ("brand", None),
        "hard-mode": ("distance", None),
        "worst-order": ("distance", None),
    }
    on = {
        k: [f for f, v in s.constraints.model_dump().items() if v] for k, s in setups.items() if s
    }
    assert on == {k: [] for k in TYPES} | {"no-left": ["no_left", "strict"], "hard-mode": ["hard"]}
    assert [k for k, s in setups.items() if s and s.reorder] == ["worst-order"]
    assert [len(s.stops) for s in setups.values() if s] == [2, 2, 2, 2, 2, 2, 4]
    labels = {f"{t.name}, WL" for t in TOWNS}
    assert all(stop.label in labels for s in setups.values() if s for stop in s.stops)
    stop = setups["long-way"].stops[0]  # type: ignore[union-attr]
    assert (stop.lat, stop.lon) == (round(stop.lat, 6), round(stop.lon, 6))
    no_left = by_type(made)["no-left"]
    assert no_left.goal.model_dump() == {"kind": "no_left", "min_ratio": 1.0}
    assert no_left.title.endswith("with no left turns")


async def test_every_draw_keeps_to_its_band(served: Path) -> None:
    async with app_on(served) as (_, api):
        spatial = api.builds[REGION].spatial
        for k in range(3):
            for challenge in await generated(api, DAY - timedelta(days=k)):
                assert challenge.setup is not None
                xy = [spatial.project(s.lat, s.lon) for s in challenge.setup.stops]
                apart = [math.dist(p, q) for i, p in enumerate(xy) for q in xy[i + 1 :]]
                low, high = (0.3, 1.5) if challenge.type == "worst-order" else (0.5, 1.5)
                assert all(low * 1000 <= d <= high * 1000 for d in apart), challenge.id
                assert len({s.label for s in challenge.setup.stops}) == len(xy)


def plan_of(loaded: Loaded, challenge: ChallengeOut, seed: int) -> Any:
    """The challenge's setup planned with the engine itself, written out here."""
    setup = challenge.setup
    assert setup is not None
    request = RouteRequest(
        stops=[(s.lat, s.lon) for s in setup.stops],
        objective=setup.objective,
        item=setup.item,
        brand=None if setup.brand is None else setup.brand.id,
        constraints=Constraints(**setup.constraints.model_dump()),
        reorder=setup.reorder,
        budget_s=120,
        max_iterations=400 if setup.constraints.hard else 150,
        seed=seed,
    )
    return plan_route(loaded.data, request, backend=loaded.backend, spatial=loaded.spatial)


async def test_each_bar_is_its_own_plan_s_result_rounded_down(served: Path) -> None:
    async with app_on(served) as (_, api):
        made = by_type(await generated(api))
        loaded = api.builds[REGION]
    for place, kind in enumerate(TYPES, start=1):
        if kind == "no-left":
            continue
        planned = plan_of(loaded, made[kind], challenge_seed(DAY, place))
        route, goal = planned.route, made[kind].goal.model_dump()
        share = 0.8 * route.metrics.length_m / planned.baseline.length_m
        bar = max(1.0, math.floor(share * 2) / 2 if share < 10 else float(math.floor(share)))
        if kind == "slow-way":
            hours = (route.metrics.time_s + route.metrics.delay_s) / 3600
            assert goal == {"kind": "duration", "min_s": max(1, math.floor(0.8 * hours)) * 3600}
        elif kind == "bridges":
            found = count_items(loaded.data, route.nodes, route.edges)["bridge"]
            assert goal == {
                "kind": "items",
                "item": "bridge",
                "min": max(3, math.floor(0.8 * found)),
            }
        elif kind == "pilgrimage":
            brand = made[kind].setup.brand  # type: ignore[union-attr]
            visits = brand_counts(loaded.data, brand.id, route.nodes)["visits"]
            assert goal == {"kind": "brand", "min_visits": max(3, math.floor(0.8 * visits))}
        elif kind == "hard-mode":
            assert goal == {"kind": "hard", "min_ratio": bar} and bar >= 1.5
        else:
            assert goal == {"kind": "ratio", "min": bar}
    long_way = made["long-way"]
    assert long_way.title.endswith(f"at {long_way.goal.model_dump()['min']:g}×")


def test_the_bars_round_down() -> None:
    ratio = challenges_api._ratio_bar
    assert [ratio(r) for r in (1.0, 2.0, 3.0, 9.0, 12.4, 12.5, 25.0)] == [
        1.0,  # 0.8× is under 1×
        1.5,
        2.0,
        7.0,
        9.5,
        10.0,
        20.0,
    ]
    count = challenges_api._count_bar
    assert [count(n) for n in (4, 5, 10, 11, 683)] == [3, 4, 8, 8, 546]


async def test_the_same_day_twice_is_the_same_and_another_day_another(served: Path) -> None:
    async with app_on(served) as (_, api):
        made = await generated(api)
        assert await generated(api) == made
        other = await generated(api, DAY - timedelta(days=1))
    assert [c.id for c in other][0] == "long-way-2026-11-01"
    assert [c.setup for c in other] != [c.setup for c in made]


async def test_a_refused_pre_flight_takes_the_next_draw(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with app_on(served) as (_, api):
        first = by_type(await generated(api))["long-way"]
        real, asked = challenges_api.prepare, []

        def refusing(loaded: Loaded, request: RouteRequest) -> Any:
            asked.append(request.stops)
            if len(asked) == 1:  # the long way's first draw
                raise StopUnreachable((0, 1), "refused for the test")
            return real(loaded, request)

        monkeypatch.setattr(challenges_api, "prepare", refusing)
        second = by_type(await generated(api))["long-way"]
    assert second.setup is not None and first.setup is not None
    assert [(s.lat, s.lon) for s in first.setup.stops] == list(asked[0])
    assert [(s.lat, s.lon) for s in second.setup.stops] == list(asked[1])
    assert asked[0] != asked[1]


async def test_a_type_without_a_fitting_draw_is_left_out(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bands = {**challenges_api.CHALLENGE_BANDS_KM, "long-way": (50.0, 60.0)}
    monkeypatch.setattr(challenges_api, "CHALLENGE_BANDS_KM", bands)
    monkeypatch.setattr(challenges_api, "CHALLENGE_ORDER_KM", (50.0, 60.0))
    async with app_on(served) as (_, api):
        assert [c.type for c in await generated(api)] == list(TYPES[1:6])


async def test_too_few_bridges_are_passed_over(tmp_path: Path) -> None:
    async with app_on(served_data(tmp_path / "data", grid(bridges=False))) as (_, api):
        assert "bridges" not in by_type(await generated(api))


def counting(monkeypatch: pytest.MonkeyPatch) -> list[RouteRequestIn]:
    """Count the plans submitted, the API request of each."""
    real, planned = challenges_api._plan, []

    async def count(
        api: Api, loaded: Loaded, prepared: Prepared, body: RouteRequestIn, *args: Any
    ) -> Any:
        planned.append(body)
        return await real(api, loaded, prepared, body, *args)

    monkeypatch.setattr(challenges_api, "_plan", count)
    return planned


async def test_hard_mode_plans_longer_and_passes_over_a_bar_under_its_least(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planned = counting(monkeypatch)
    async with app_on(served) as (_, api):
        await generated(api)
        assert {b.max_iterations for b in planned if b.constraints.hard} == {400}
        assert {b.max_iterations for b in planned if not b.constraints.hard} == {150}
        planned.clear()
        monkeypatch.setattr(challenges_api, "CHALLENGE_HARD_MIN_RATIO", 1000.0)  # none so high
        monkeypatch.setattr(challenges_api, "CHALLENGE_TRIES", 2)
        assert "hard-mode" not in by_type(await generated(api))
    assert [b.constraints.hard for b in planned].count(True) == 2  # each draw planned, passed over


async def test_the_pilgrimage_visits_a_listed_chain_with_enough_locations(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with app_on(served) as (_, api):
        pilgrimage = by_type(await generated(api))["pilgrimage"]
        assert pilgrimage.setup is not None and pilgrimage.setup.brand is not None
        brand = pilgrimage.setup.brand.model_dump()
        assert brand in (
            {"id": 0, "name": "Alpha Mart", "wikidata": "Q1"},
            {"id": 2, "name": "Gamma Gas", "wikidata": "Q3"},
        )
        assert pilgrimage.title.startswith("Visit ") and pilgrimage.title.endswith(brand["name"])
        # Beta Burger (3 locations) and Local (no wikidata) alone: no pilgrimage at all.
        monkeypatch.setattr(challenges_api, "CHALLENGE_BRANDS", ("Q2",))
        assert "pilgrimage" not in by_type(await generated(api))


async def test_a_chain_visited_too_little_falls_back_to_another_then_the_next_draw(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(challenges_api, "CHALLENGE_MIN_FOUND", 10**6)  # no plan finds enough
    monkeypatch.setattr(challenges_api, "CHALLENGE_TRIES", 2)
    planned = counting(monkeypatch)
    async with app_on(served) as (_, api):
        made = by_type(await generated(api))
    assert "pilgrimage" not in made and "bridges" not in made
    pilgrimages = [
        (b.brand, tuple((s.lat, s.lon) for s in b.stops or []))
        for b in planned
        if b.objective == "brand"
    ]
    assert len(pilgrimages) == 4  # both chains on each of the two draws
    assert {brand for brand, _ in pilgrimages} == {0, 2}
    assert len({stops for _, stops in pilgrimages}) == 2


async def test_a_plan_the_clock_ended_is_logged(
    served: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(challenges_api, "BUDGET_MAX_S", 0.05)  # the clock before the attempts
    monkeypatch.setattr(challenges_api, "CHALLENGE_ATTEMPTS", 10**6)
    monkeypatch.setattr(challenges_api, "CHALLENGE_HARD_ATTEMPTS", 10**6)
    with caplog.at_level(logging.WARNING, logger="worst_route.api.challenges_api"):
        async with app_on(served) as (_, api):
            await generated(api)
    clocked = [r.getMessage() for r in caplog.records if "stopped on the clock" in r.getMessage()]
    assert any(
        m.startswith("the long-way challenge's plan for 2026-11-02 stopped") for m in clocked
    )
    assert all(m.endswith("of 1000000 attempts") for m in clocked)


# --- The endpoint ----------------------------------------------------------------------------


def counted(monkeypatch: pytest.MonkeyPatch) -> list[date]:
    """Count the days made, one entry each."""
    real, days = challenges_api.generate_day, []

    async def counting(api: Api, loaded: Loaded, towns: Any, day: date) -> list[ChallengeOut]:
        days.append(day)
        return await real(api, loaded, towns, day)

    monkeypatch.setattr(challenges_api, "generate_day", counting)
    return days


async def test_a_day_is_made_on_its_first_call_and_kept(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    days = counted(monkeypatch)
    async with app_on(served) as (http, api):
        first, again = await asyncio.gather(http.get("/challenges"), http.get("/challenges"))
        assert first.status_code == again.status_code == 200
        assert first.json() == again.json() and days == [DAY]  # made once for both
        answer = first.json()
        drawn_on = (answer["date"], answer["region"], answer["build_id"])
        assert drawn_on == ("2026-11-02", REGION, "grid")
        assert [c["type"] for c in answer["challenges"]] == [*TYPES, "daily-worse"]
        worse = answer["challenges"][-1]
        assert worse == {
            "id": "daily-worse-2026-11-02",
            "type": "daily-worse",
            "title": "Make today's route worse",
            "blurb": "Open today's route and make it worse once.",
            "setup": None,
            "goal": {"kind": "daily_worse"},
        }
        long_way = answer["challenges"][0]
        assert set(long_way) == {"id", "type", "title", "blurb", "setup", "goal"}
        assert set(long_way["setup"]) == {
            "stops",
            "objective",
            "item",
            "brand",
            "constraints",
            "reorder",
            "target",
        }
        assert long_way["setup"]["constraints"] == {
            "no_left": False,
            "no_right": False,
            "strict": False,
            "hard": False,
        }
        assert long_way["setup"]["target"] == {"kind": "max"}
        assert (await http.get("/challenges", params={"date": "2026-11-02"})).json() == answer
        yesterday = (await http.get("/challenges", params={"date": "2026-11-01"})).json()
        assert yesterday["date"] == "2026-11-01" and days == [DAY, date(2026, 11, 1)]
        assert [c["type"] for c in yesterday["challenges"]] == list(TYPES)  # no daily-worse
        assert {k[0] for k in api.challenge_days} == {"2026-11-01", "2026-11-02"}
        assert api.challenge_tasks == {}
        # A month on, both have left the window, and are forgotten once another day is kept.
        monkeypatch.setattr(daily_api, "today", lambda: DAY + timedelta(days=31))
        assert (await http.get("/challenges")).status_code == 200
        assert {k[0] for k in api.challenge_days} == {"2026-12-03"}


async def test_a_day_kept_in_the_store_is_never_made_again(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with app_on(served) as (http, api):
        answer = (await http.get("/challenges")).json()
        version = str(api.builds[REGION].data.meta["graph_version"])
        kept = api.store.challenges("2026-11-02", REGION, version)
        assert kept is not None and [c["type"] for c in json.loads(kept)] == list(TYPES)
    days = counted(monkeypatch)
    async with app_on(served) as (http, api):  # a restart, the same store
        assert (await http.get("/challenges")).json() == answer
        assert days == [] and len(api.challenge_days) == 1


async def test_a_date_being_made_holds_up_no_other(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, started = asyncio.Event(), asyncio.Event()

    async def held(api: Api, loaded: Loaded, towns: Any, day: date) -> list[ChallengeOut]:
        if day == DAY:
            started.set()
            await release.wait()
        return []

    monkeypatch.setattr(challenges_api, "generate_day", held)
    async with app_on(served) as (http, api):
        today = asyncio.create_task(http.get("/challenges"))
        await started.wait()
        other = await http.get("/challenges", params={"date": "2026-11-01"})  # made meanwhile
        assert other.status_code == 200 and other.json()["challenges"] == []
        assert not today.done() and list(api.challenge_tasks) == [
            ("2026-11-02", REGION, str(api.builds[REGION].data.meta["graph_version"]))
        ]
        release.set()
        assert [c["type"] for c in (await today).json()["challenges"]] == ["daily-worse"]


async def test_the_server_s_stop_ends_a_date_s_making(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    async def endless(api: Api, loaded: Loaded, towns: Any, day: date) -> list[ChallengeOut]:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("never")

    monkeypatch.setattr(challenges_api, "generate_day", endless)
    async with app_on(served) as (http, api):
        asking = asyncio.create_task(http.get("/challenges"))
        await started.wait()
        making = list(api.challenge_tasks.values())
    assert len(making) == 1 and making[0].cancelled()  # the lifespan's stop cancelled it
    asking.cancel()
    await asyncio.gather(asking, return_exceptions=True)


async def test_two_servers_give_the_same_challenges(served: Path) -> None:
    answers = []
    for store in ("one.sqlite", "two.sqlite"):
        async with app_on(served, store_path=served / store) as (http, _):
            answers.append((await http.get("/challenges")).json())
    assert answers[0] == answers[1]


async def test_dates_outside_the_window_are_refused(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    days = counted(monkeypatch)
    async with app_on(served) as (http, _):
        for day in ("2026-11-03", "2026-10-02"):  # tomorrow; 31 days ago
            refused = await http.get("/challenges", params={"date": day})
            body = refused.json()
            assert refused.status_code == 422, body
            assert (body["code"], body["field"]) == ("INVALID_REQUEST", "date")
            window = "challenges are for the days from 2026-10-03 to 2026-11-02"
            assert body["message"] == f"{window}, not {day}"
        malformed = await http.get("/challenges", params={"date": "2026-13-01"})
        assert malformed.status_code == 422 and malformed.json()["code"] == "INVALID_REQUEST"
        assert "field" not in malformed.json()
    assert days == []


async def test_a_public_server_makes_only_today_s(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with app_on(served) as (http, _):  # a local server keeps 2026-10-30
        assert (await http.get("/challenges", params={"date": "2026-10-30"})).status_code == 200
    days = counted(monkeypatch)
    async with app_on(served, public=True) as (http, _):
        refused = await http.get("/challenges", params={"date": "2026-11-01"})
        assert refused.status_code == 422
        assert refused.json() == {
            "code": "INVALID_REQUEST",
            "message": "that day's challenges are not kept here",
            "field": "date",
        }
        kept = await http.get("/challenges", params={"date": "2026-10-30"})
        assert kept.status_code == 200 and len(kept.json()["challenges"]) == len(TYPES)
        assert (await http.get("/challenges")).status_code == 200
    assert days == [DAY]


async def test_a_full_server_makes_nothing(served: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def filling(api: Api, *args: Any) -> list[ChallengeOut]:
        raise TooManyJobs()  # a plan finding the server full

    async with app_on(served) as (http, api):
        version = str(api.builds[REGION].data.meta["graph_version"])
        with monkeypatch.context() as full:  # its own patches undone, the fixture's kept
            full.setattr(api.jobs, "refusal", lambda *args: TooManyJobs())
            busy = await http.get("/challenges")
            assert (busy.status_code, busy.json()["code"]) == (429, "TOO_MANY_JOBS")
        with monkeypatch.context() as filled:
            filled.setattr(challenges_api, "generate_day", filling)
            assert (await http.get("/challenges")).status_code == 429
        assert api.challenge_days == {} and api.challenge_tasks == {}
        assert api.store.challenges("2026-11-02", REGION, version) is None  # nothing kept
        made = await http.get("/challenges")
        assert made.status_code == 200
        assert [c["type"] for c in made.json()["challenges"]] == [*TYPES, "daily-worse"]
        assert api.store.challenges("2026-11-02", REGION, version) is not None


async def test_no_places_index_no_challenges(tmp_path: Path) -> None:
    async with app_on(served_data(tmp_path / "data", index=False)) as (http, _):
        refused = await http.get("/challenges")
        assert refused.status_code == 404
        assert refused.json() == {
            "code": "NOT_FOUND",
            "message": f"no challenges: {REGION} has no places index to draw their towns from",
        }
