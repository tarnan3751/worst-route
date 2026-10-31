"""The daily route (`GET /daily`) over httpx in process, on the 20 × 20 lattice with a places index
of towns placed on it, the day fixed: the draw (the band's both ends, the incorporated places, the
build's bbox, a node near each town; the same seed the same towns; the next trip when the
pre-flight refuses one), the region with the most states,
planning then done, the same stops and route twice and on two servers, a failed plan or pre-flight
recorded and planned again, a cancelled job and a planning row of a restarted server planned again,
the job never an orphan, a new build's own row, no client's share taken on a public server, and no
daily route without a places index."""

import asyncio
import json
import shutil
from datetime import date, datetime, timedelta
from itertools import islice
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from worst_route.api import daily_api
from worst_route.api import worker as worker_module
from worst_route.api.app import Api
from worst_route.api.daily_api import daily_region, draw_trips
from worst_route.api.store import DailyRow, RouteStore
from worst_route.config import REGIONS, Region
from worst_route.engine.snap import StopUnreachable
from worst_route.graph import loader
from worst_route.graph.loader import Loaded, load_build
from worst_route.presentation.places import TownPoint, places_dir, write_places

from ..engine.graphs import at_node, lattice
from ..presentation.support import STATES_FILE, WIDE
from .hosting import hosted
from .support import BIG, save_build, slow_splices, until

pytestmark = pytest.mark.anyio

DAY = date(2026, 10, 31)
SERVED = (("portland", "latest"),)
METRES_PER_DEGREE_LON = 78_000.0  # at the lattice's latitude, near enough


def town(name: str, lsad: str, v: int, east_m: float = 0.0, north_m: float = 0.0) -> TownPoint:
    """A town at node `v` of the big lattice, moved east and north by so many metres."""
    lat, lon = at_node(BIG, v)
    return TownPoint(
        name, "WL", lsad, lon + east_m / METRES_PER_DEGREE_LON, lat + north_m / 111_000
    )


# On the big lattice (1.9 km a side; the nodes' extent its bbox, whose diagonal is 2.69 km, so a
# trip is 0.40-1.21 km long), the towns at nodes (row, column): Alpha (1, 1) - Charlie (9, 9) and
# Bravo (17, 17) - Charlie, 1.13 km each, fit; Alpha - Bravo, 2.26 km, is too long; Delta (5, 5), a
# CDP, and Echo (5, 13), a borough, 0.57 km from Charlie, are not incorporated places of the kinds
# drawn; Outside lies 150 m beyond the lattice's east edge on row 9, 1.15 km from Charlie, outside
# the build's bbox.
TOWNS = [
    town("Alpha", "25", 21),
    town("Bravo", "43", 357),
    town("Charlie", "47", 189),
    town("Delta", "57", 105),
    town("Echo", "21", 113),
    town("Outside", "25", 199, east_m=150),
]
FITTING = {("Alpha, WL", "Charlie, WL"), ("Bravo, WL", "Charlie, WL")}


def big_build(data: Path, build_id: str = "big", **params: Any) -> Loaded:
    save_build(data, BIG, build_id, **params)
    return load_build(data, "portland", build_id, backend="python")


def write_towns(data: Path, towns: list[TownPoint] = TOWNS) -> None:
    """The places index of `towns`, each a square 40 m a side around its point, over the census
    fixture's two states."""
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
        "portland",
        WIDE,
        states=STATES_FILE,
        outlines=STATES_FILE,
        places=[path],
        out_dir=places_dir(data),
    )


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data directory with the big lattice as portland's latest build and the towns' index;
    the day fixed and the daily route's attempts few."""
    data = tmp_path / "data"
    big_build(data)
    write_towns(data)
    monkeypatch.setattr(daily_api, "today", lambda: DAY)
    monkeypatch.setattr(daily_api, "DAILY_MAX_ITERATIONS", 300)
    return data


def trip(answer: dict[str, Any]) -> tuple[str, str]:
    start, end = answer["stops"]
    return start["label"], end["label"]


async def planned_daily(http: httpx.AsyncClient, api: Api) -> dict[str, Any]:
    """`GET /daily` on a day not planned yet, its job followed to its end, then `GET /daily`."""
    first = (await http.get("/daily")).json()
    assert first["status"] == "planning", first
    await http.get(first["events_url"])  # the whole stream: the job has ended
    await until(lambda: status_of(api) == "done")  # recorded by the server's follower
    return dict((await http.get("/daily")).json())


def status_of(api: Api, graph_version: str | None = None) -> str | None:
    """The day's row's status on the served build (or at `graph_version`)."""
    return None if (row := row_of(api, graph_version)) is None else row.status


def row_of(api: Api, graph_version: str | None = None) -> DailyRow | None:
    """The day's row on the served build (or at `graph_version`)."""
    version = graph_version or str(api.builds["portland"].data.meta["graph_version"])
    return api.store.daily("2026-10-31", "portland", version)


def counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the day's draws (`plannable`), one entry each."""
    real, calls = daily_api.plannable, []

    def counting(*args: Any) -> Any:
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(daily_api, "plannable", counting)
    return calls


# --- The draw --------------------------------------------------------------------------------


def test_the_draw_keeps_to_the_band_and_the_incorporated_places(tmp_path: Path) -> None:
    loaded = big_build(tmp_path / "data")
    drawn = set()
    for seed in range(20261001, 20261031):
        pair = next(draw_trips(loaded, TOWNS, seed), None)
        assert pair is not None
        labels = (pair[0].label, pair[1].label)
        assert tuple(sorted(labels)) in FITTING, labels
        drawn.add(labels)
    assert {tuple(sorted(d)) for d in drawn} == FITTING  # both pairs, never another
    assert len(drawn) > 2  # either way round
    trips = list(islice(draw_trips(loaded, TOWNS, 20261031), 5))
    assert trips == list(islice(draw_trips(loaded, TOWNS, 20261031), 5))  # the same seed, the same
    assert len(trips) == 5 and all(tuple(sorted(s.label for s in t)) in FITTING for t in trips)
    start, _ = trips[0]
    assert start.lat == round(start.lat, 6)  # at 6 decimals


def test_a_town_without_a_node_near_is_never_drawn(tmp_path: Path) -> None:
    # The build's bbox 0.1° around the lattice, its diagonal about 23 km: a trip of 3.4-10.4 km.
    # Faraway lies 8 km north of Alpha, inside the bbox but 6 km from every node; Nearby
    # 4.5 km east of the lattice's north-east corner, within 5 km of it.
    lon, lat = (float(BIG.node_lon.min()), float(BIG.node_lat.min()))
    bbox = [lon - 0.1, lat - 0.1, float(BIG.node_lon.max()) + 0.1, float(BIG.node_lat.max()) + 0.1]
    loaded = big_build(tmp_path / "data", bbox=bbox)
    towns = [
        town("Alpha", "25", 21),
        town("Faraway", "25", 21, north_m=8_000),
        town("Nearby", "43", 399, east_m=4_500),
    ]
    for seed in range(20261001, 20261011):
        pair = next(draw_trips(loaded, towns, seed), None)
        assert pair is not None and {s.label for s in pair} == {"Alpha, WL", "Nearby, WL"}
    assert list(draw_trips(loaded, towns[:2], 20261031)) == []  # nothing fits


def test_two_towns_closer_than_the_band_are_never_drawn(tmp_path: Path) -> None:
    # Alpha (1, 1) and Near (1, 4), 0.30 km apart, under the band's 0.40 km: no trip at all.
    loaded = big_build(tmp_path / "data")
    near = [town("Alpha", "25", 21), town("Near", "43", 24)]
    assert list(draw_trips(loaded, near, 20261031)) == []
    # Far (13, 1), 1.20 km from Alpha: a trip, then.
    pair = next(draw_trips(loaded, [*near, town("Far", "25", 261)], 20261031))
    assert {s.label for s in pair} == {"Alpha, WL", "Far, WL"}


def test_the_region_with_the_most_states_is_the_daily_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        REGIONS, "two", Region(name="two", states=("oregon", "washington"), crs_epsg=1)
    )

    def of(*regions: str) -> str:
        return daily_region(cast(Api, SimpleNamespace(builds=dict.fromkeys(regions))))

    assert of("portland", "two", "oregon") == "two"
    assert of("portland", "oregon") == "portland"  # a tie: the first served
    assert of("unknown", "oregon") == "oregon"
    assert of("unknown", "other") == "unknown"


# --- The endpoint ----------------------------------------------------------------------------


async def test_the_first_call_plans_the_day_and_the_next_find_it_done(served: Path) -> None:
    async with hosted(served, builds=SERVED) as (http, app):
        api: Api = app.state.api
        first = (await http.get("/daily")).json()
        assert first["status"] == "planning" and first["route_id"] is None
        assert (first["date"], first["region"], first["build_id"]) == (
            "2026-10-31",
            "portland",
            "big",
        )
        assert (first["seed"], first["max_iterations"]) == (20261031, 300)
        assert tuple(sorted(trip(first))) in FITTING
        assert first["events_url"].startswith("/jobs/") and first["events_url"].endswith("/events")
        stream = await http.get(first["events_url"])
        assert "event: done" in stream.text
        await until(lambda: status_of(api) == "done")
        done = (await http.get("/daily")).json()
        assert done["status"] == "done" and done["events_url"] is None
        assert done["stops"] == first["stops"] and done["seed"] == 20261031
        assert done["ratio"] > 1 and done["stopped_because"] in ("budget", "converged")
        assert done["attempts"] <= 300
        assert (await http.get("/daily")).json() == done  # the same, call after call
        route = (await http.get(f"/routes/{done['route_id']}")).json()
        assert [s["label"] for s in route["stops"]] == list(trip(done))
        assert route["request"]["max_iterations"] == 300 and route["seed"] == 20261031
        assert route["request"]["budget_s"] == 120  # the server's largest, a safety net
        assert route["request"]["vehicle"] == {"mpg": 24.0, "gas_price_usd": 3.89}  # the default
        departure = datetime.fromisoformat(route["request"]["departure"])  # when it was planned
        assert departure.utcoffset() == timedelta(0) and departure.microsecond == 0
        assert route["metrics"]["extra_fuel_gal"] > 0 and route["metrics"]["arrival"] is not None
        assert route["metrics"]["ratio"] == done["ratio"]
        assert (await http.post(f"/routes/{done['route_id']}/validate")).json()["ok"] is True
        row = row_of(api)
        assert row is not None and (row.status, row.route_id) == ("done", done["route_id"])
        assert len(api.jobs.jobs) == 1  # planned once


async def test_two_servers_plan_the_same_day_alike(served: Path, tmp_path: Path) -> None:
    answers = []
    for k in range(2):
        store = tmp_path / f"server{k}" / "routes.sqlite"
        async with hosted(served, builds=SERVED, store_path=store) as (http, app):
            answers.append(await planned_daily(http, app.state.api))
    assert answers[0] == answers[1]
    assert answers[0]["status"] == "done" and answers[0]["stopped_because"] == "budget"
    assert answers[0]["attempts"] == 300  # the iterations ended it: reproducible


async def test_concurrent_first_calls_plan_once(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)  # still planning when the last call is answered
    async with hosted(served, builds=SERVED) as (http, app):
        answers = await asyncio.gather(*(http.get("/daily") for _ in range(4)))
        urls = {a.json()["events_url"] for a in answers}
        assert len(urls) == 1 and all(a.json()["status"] == "planning" for a in answers)
        assert len(app.state.api.jobs.jobs) == 1


async def test_a_failed_job_is_recorded_and_planned_again(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = worker_module.plan_route

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_module, "plan_route", broken)
    draws = counted(monkeypatch)
    async with hosted(served, builds=SERVED) as (http, app):
        api: Api = app.state.api
        first = (await http.get("/daily")).json()
        assert first["status"] == "planning"
        assert "event: error" in (await http.get(first["events_url"])).text
        await until(lambda: status_of(api) == "failed")
        monkeypatch.setattr(worker_module, "plan_route", real)
        again = (await http.get("/daily")).json()
        assert again["status"] == "planning" and again["events_url"] != first["events_url"]
        assert again["stops"] == first["stops"]
        assert len(draws) == 1  # submitted again, not drawn again
        await http.get(again["events_url"])
        await until(lambda: status_of(api) == "done")


async def test_a_trip_the_pre_flight_refuses_gives_way_to_the_next(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = daily_api.prepare
    tried: list[Any] = []

    def first_unreachable(loaded: Loaded, request: Any) -> Any:
        tried.append(request.stops)
        if len(tried) == 1:
            raise StopUnreachable((0, 1), "after moving stop 1 up to 10 times")
        return real(loaded, request)

    monkeypatch.setattr(daily_api, "prepare", first_unreachable)
    async with hosted(served, builds=SERVED) as (http, _):
        answer = (await http.get("/daily")).json()
        assert answer["status"] == "planning"
    first, second = list(
        islice(draw_trips(load_build(served, "portland", "big"), TOWNS, 20261031), 2)
    )
    assert tried == [[(s.lat, s.lon) for s in t] for t in (first, second)]
    assert trip(answer) == (second[0].label, second[1].label)


async def test_a_day_whose_trips_the_pre_flight_refuses_is_failed_and_stays_so(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tried: list[Any] = []

    def unreachable(loaded: Loaded, request: Any) -> None:
        tried.append(request.stops)
        raise StopUnreachable((0, 1), "after moving stop 1 up to 10 times")

    monkeypatch.setattr(daily_api, "prepare", unreachable)
    async with hosted(served, builds=SERVED) as (http, app):
        failed = (await http.get("/daily")).json()
        assert failed["status"] == "failed" and failed["events_url"] is None
        assert len(tried) == 10  # DAILY_TRIES, the first trip's stops answered
        assert [(s["lat"], s["lon"]) for s in failed["stops"]] == tried[0]
        assert status_of(app.state.api) == "failed" and not app.state.api.jobs.jobs
        again = (await http.get("/daily")).json()  # answered from the drawn plan
        assert again == failed and len(tried) == 10


async def test_a_full_server_draws_nothing_until_a_slot_frees(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draws = counted(monkeypatch)
    async with hosted(served, builds=SERVED, jobs={"max_jobs": 1, "max_queued": 0}) as (http, app):
        body = {
            "region": "portland",
            "stops": [dict(zip(("lat", "lon"), at_node(BIG, v), strict=True)) for v in (0, 399)],
            "seed": 3,
            "max_iterations": 300,
        }
        job = (await http.post("/jobs", json=body)).json()  # takes the only slot
        for _ in range(3):
            busy = await http.get("/daily")
            assert busy.status_code == 429 and busy.json()["code"] == "TOO_MANY_JOBS"
        assert draws == [] and status_of(app.state.api) is None  # nothing drawn, nothing recorded
        await http.get(job["events_url"])
        await until(lambda: app.state.api.jobs.refusal() is None)
        assert (await http.get("/daily")).json()["status"] == "planning"
        assert len(draws) == 1


async def test_no_client_cancels_the_daily_job(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with hosted(served, builds=SERVED) as (http, app):
        api: Api = app.state.api
        first = (await http.get("/daily")).json()
        job_id = first["events_url"].split("/")[2]
        await until(lambda: api.jobs.jobs[job_id].accepted >= 2)
        refused = await http.delete(f"/jobs/{job_id}")
        assert refused.status_code == 409 and refused.json()["code"] == "DAILY_JOB"
        assert api.jobs.jobs[job_id].end is None  # still planning
        await until(lambda: status_of(api) == "done")
        assert not api.daily_jobs  # forgotten once ended: a DELETE then only reports it
        assert (await http.delete(f"/jobs/{job_id}")).json()["stopped_because"] == "budget"


async def test_a_daily_job_cancelled_by_the_server_is_planned_again(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_splices(monkeypatch)
    async with hosted(served, builds=SERVED) as (http, app):
        api: Api = app.state.api
        first = (await http.get("/daily")).json()
        job = api.jobs.jobs[first["events_url"].split("/")[2]]
        await until(lambda: job.accepted >= 2)
        await api.jobs.cancel(job)  # as the time limit ends a job
        await until(lambda: status_of(api) == "failed")  # a cancelled route is not the day's
        again = (await http.get("/daily")).json()
        assert again["status"] == "planning" and again["events_url"] != first["events_url"]


async def test_the_job_outlives_its_viewers(served: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slow_splices(monkeypatch)
    jobs = {"orphan_s": 0.05, "sweep_s": 0.01}
    async with hosted(served, builds=SERVED, jobs=jobs) as (http, app):
        api: Api = app.state.api
        first = (await http.get("/daily")).json()
        job = api.jobs.jobs[first["events_url"].split("/")[2]]
        assert job.client is None and len(job.subscribers) == 1  # the server's own
        viewer = api.jobs.subscribe(job)
        await until(lambda: job.accepted >= 2)
        viewer.close()  # a viewer gone: the job is no orphan, the server follows it
        await until(lambda: job.end is not None)
        assert job.stopped_because == "budget"
        await until(lambda: status_of(api) == "done")


async def test_a_call_after_the_job_ends_finds_it_done_before_its_follower(served: Path) -> None:
    async with hosted(served, builds=SERVED) as (http, app):
        api: Api = app.state.api
        first = (await http.get("/daily")).json()
        for task in list(api.daily_tasks):  # the follower held back
            task.cancel()
        await http.get(first["events_url"])  # the job has ended
        assert status_of(api) == "planning"
        done = (await http.get("/daily")).json()
        assert done["status"] == "done" and status_of(api) == "done"


async def test_a_follower_never_overwrites_a_later_plan(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = worker_module.plan_route

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_module, "plan_route", broken)
    async with hosted(served, builds=SERVED) as (http, app):
        api: Api = app.state.api
        first = (await http.get("/daily")).json()
        failed_row = row_of(api)
        failed_job = api.jobs.jobs[first["events_url"].split("/")[2]]
        for task in list(api.daily_tasks):  # its follower held back
            task.cancel()
        await http.get(first["events_url"])
        monkeypatch.setattr(worker_module, "plan_route", real)
        slow_splices(monkeypatch)  # still planning when the first job's follower comes
        again = (await http.get("/daily")).json()  # failed, recorded, planned again
        assert again["status"] == "planning" and again["events_url"] != first["events_url"]
        assert failed_row is not None
        now = await daily_api._record_end(api, failed_row, failed_job)  # the first job's follower
        assert now is not None and now.status == "planning" and now.job_id != failed_job.id
        assert status_of(api) == "planning"


async def test_a_planning_row_without_its_job_is_planned_again(served: Path) -> None:
    version = str(load_build(served, "portland", "big").data.meta["graph_version"])
    RouteStore(served / "routes.sqlite").put_daily(
        DailyRow("2026-10-31", "portland", "big", version, None, "planning", "gone")  # a restart's
    )
    async with hosted(served, builds=SERVED) as (http, _):
        answer = (await http.get("/daily")).json()
        assert answer["status"] == "planning" and "gone" not in answer["events_url"]


async def test_a_build_replaced_under_its_id_has_its_own_row(served: Path) -> None:
    async with hosted(served, builds=SERVED) as (http, app):
        old = await planned_daily(http, app.state.api)
        old_version = str(app.state.api.builds["portland"].data.meta["graph_version"])
    # `pipeline save --force`: the same build id, another graph_version (the same nodes, so the
    # same towns, with signals)
    shutil.rmtree(served / "graphs" / "portland" / "big")
    save_build(served, lattice(20, 20, primary_every=5), "big")
    loader._load.cache_clear()
    async with hosted(served, builds=SERVED) as (http, app):
        new = (await http.get("/daily")).json()
        assert new["status"] == "planning" and new["build_id"] == "big"
        assert new["stops"] == old["stops"]
        await http.get(new["events_url"])
        await until(lambda: status_of(app.state.api) == "done")
        done = (await http.get("/daily")).json()
        assert done["route_id"] != old["route_id"]  # planned on the build served, not a 409
        assert (await http.get(f"/routes/{done['route_id']}")).status_code == 200
        assert status_of(app.state.api, old_version) == "done"  # the old build's row stays


async def test_a_public_server_takes_no_client_s_share(served: Path) -> None:
    async with hosted(served, builds=SERVED, public=True) as (http, app):
        answer = (await http.get("/daily")).json()
        job = app.state.api.jobs.jobs[answer["events_url"].split("/")[2]]
        assert job.client is None and job.request.budget_s == 60  # the public budget


async def test_an_index_without_incorporated_places_has_no_daily_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    big_build(data)
    write_towns(
        data, [town("Delta", "57", 21), town("Echo", "21", 189), town("Foxtrot", "57", 357)]
    )
    monkeypatch.setattr(daily_api, "today", lambda: DAY)
    async with hosted(data, builds=SERVED) as (http, _):
        answer = await http.get("/daily")
        assert answer.status_code == 404 and answer.json()["code"] == "NOT_FOUND"
        assert "no two towns" in answer.json()["message"]


async def test_no_places_index_no_daily_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    big_build(data)
    monkeypatch.setattr(daily_api, "today", lambda: DAY)
    async with hosted(data, builds=SERVED) as (http, _):
        answer = await http.get("/daily")
        assert answer.status_code == 404 and answer.json()["code"] == "NOT_FOUND"
        assert "places index" in answer.json()["message"]
