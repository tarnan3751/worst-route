"""`POST /regions/pick` on the `lands` data directory's regions (Westland alone, and Westland
with Eastland), over httpx in process: stops in one state, in both, in no state, without a
places index, outside every region and outside any one region; a region clipped to a bbox first,
the smallest bbox first, which must name the stops' states when a places index is loaded; the
reason when a smaller build's bbox misses a stop in its state; ties to the region served first;
the body's refusals; each client's picks counted on a public server; a job planned on the region
picked; and the refusal's sentence, naming the lower 48 or the regions served."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from worst_route.api.routes_api import outside_sentence
from worst_route.config import REGIONS, Region
from worst_route.graph.data import GraphData

from ..engine.graphs import at_node
from ..presentation.support import write_fixture_places
from .hosting import hosted
from .support import BIG, GRID, GRID_BBOX, save_build

pytestmark = pytest.mark.anyio

BOTH = (("westland", "latest"), ("lands", "latest"))


def stop(g: GraphData, v: int) -> dict[str, float]:
    lat, lon = at_node(g, v)
    return {"lat": lat, "lon": lon}


# Inside the grid (its bbox is its nodes' extent, which a corner's rounded stop may leave): in its
# west half, Westland; and across it, Westland then Eastland.
WEST = [stop(GRID, 11), stop(GRID, 83)]
ACROSS = [stop(GRID, 11), stop(GRID, 88)]
NOWHERE = {"lat": 40.0, "lon": -100.0}  # in no state, and outside every build


async def picked(http: httpx.AsyncClient, stops: list[dict[str, float]]) -> Any:
    answer = await http.post("/regions/pick", json={"stops": stops})
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_stops_in_one_state_pick_the_region_of_that_state(lands: Path) -> None:
    write_fixture_places(lands, region="lands")
    for builds in (BOTH, tuple(reversed(BOTH))):  # the fewest states, whatever the order
        async with hosted(lands, builds=builds) as (http, _):
            assert await picked(http, WEST) == {
                "region": "westland",
                "build_id": "w1",
                "reason": "all stops in Westland",
            }
            assert (await picked(http, WEST[:1]))["region"] == "westland"  # one stop will do


async def test_stops_in_two_states_pick_the_region_of_both(lands: Path) -> None:
    write_fixture_places(lands, region="lands")
    async with hosted(lands, builds=BOTH) as (http, _):
        # The grid's east half lies in westland's bbox, but not in Westland.
        assert await picked(http, ACROSS) == {
            "region": "lands",
            "build_id": "l1",
            "reason": "stops in Westland and Eastland",
        }
    write_fixture_places(lands, states=("91",), region="westland")  # its states as any index's
    async with hosted(lands, builds=(("westland", "latest"),)) as (http, _):
        # No region of both states served: the one whose bbox holds the stops.
        assert await picked(http, ACROSS) == {
            "region": "westland",
            "build_id": "w1",
            "reason": "stops in Westland and Eastland, by bounding box",
        }


async def test_a_stop_in_no_state_picks_the_widest_region_holding_it(lands: Path) -> None:
    write_fixture_places(lands, region="lands")
    save_build(lands, BIG, "l2", region="lands", bbox=[-123.0, 45.0, -122.0, 46.0])
    async with hosted(lands, builds=BOTH) as (http, _):
        at_sea = {"lat": 45.8, "lon": -122.5}  # in lands' bbox, north of both states
        assert await picked(http, [*WEST, at_sea]) == {
            "region": "lands",
            "build_id": "l2",
            "reason": "a stop in no state",
        }


async def test_without_a_places_index_the_pick_goes_by_bounding_box(lands: Path) -> None:
    async with hosted(lands, builds=BOTH) as (http, _):
        assert await picked(http, WEST) == {
            "region": "lands",  # both bboxes hold the stops: the most states
            "build_id": "l1",
            "reason": "no places index: by bounding box",
        }


async def test_a_region_clipped_to_a_bbox_comes_first_the_smallest_first(
    lands: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_fixture_places(lands, region="lands")
    near = [
        round(float(GRID.node_lon.min()) - 0.001, 7),
        round(float(GRID.node_lat.min()) - 0.001, 7),
        round(float(GRID.node_lon.max()) + 0.001, 7),
        round(float(GRID.node_lat.max()) + 0.001, 7),
    ]
    # Two regions clipped to bboxes in Westland: downtown, the grid, and portland, 0.1° around it.
    for name, bbox in (("downtown", near), ("portland", GRID_BBOX)):
        clipped = Region(name=name, states=("westland",), bbox=tuple(bbox), crs_epsg=32610)
        monkeypatch.setitem(REGIONS, name, clipped)
    save_build(lands, GRID, "grid", bbox=GRID_BBOX)
    save_build(lands, GRID, "d1", region="downtown", bbox=near)
    served = (("lands", "latest"), ("portland", "latest"), ("downtown", "latest"))
    async with hosted(lands, builds=served) as (http, _):
        assert await picked(http, WEST) == {
            "region": "downtown",
            "build_id": "d1",
            "reason": "all stops within Downtown",
        }
        beyond = {"lat": near[3] + 0.01, "lon": near[0]}  # out of downtown, in portland's bbox
        assert await picked(http, [*WEST, beyond]) == {
            "region": "portland",
            "build_id": "grid",
            "reason": "all stops within Portland",
        }
        # Both bboxes hold a stop in Eastland, but neither region names Eastland.
        assert await picked(http, ACROSS) == {
            "region": "lands",
            "build_id": "l1",
            "reason": "stops in Westland and Eastland",
        }
    (lands / "aux" / "places" / "lands.parquet").unlink()  # no places index: the bbox decides
    async with hosted(lands, builds=served) as (http, _):
        assert (await picked(http, ACROSS))["region"] == "downtown"


async def test_a_stop_beyond_the_smaller_build_is_said(lands: Path) -> None:
    write_fixture_places(lands, region="lands")
    north = {"lat": 45.526, "lon": -122.677}  # in Westland, north of the grid, in the big one
    async with hosted(lands, builds=BOTH) as (http, _):
        assert await picked(http, [WEST[0], north]) == {
            "region": "lands",
            "build_id": "l1",
            "reason": "all stops in Westland, one beyond Westland's build",
        }


async def test_a_public_server_counts_each_client_s_picks(lands: Path) -> None:
    async with hosted(lands, builds=BOTH) as (http, app):
        assert app.state.api.pick_quota is None  # a local server counts nothing
    async with hosted(lands, builds=BOTH, public=True) as (http, app):
        api = app.state.api
        assert api.pick_quota is not None and api.pick_quota is not api.quota  # apart from searches
        for _ in range(60):
            assert (await http.post("/regions/pick", json={"stops": WEST})).status_code == 200
        refused = await http.post("/regions/pick", json={"stops": WEST})
        assert refused.status_code == 429
        body = refused.json()
        assert (body["code"], body["retry_after_s"] >= 1) == ("RATE_LIMITED", True)
        wait = body["retry_after_s"]
        assert body["message"] == f"at most 60 picks a minute; try again in {wait} s"
        bad = await http.post("/regions/pick", json={"stops": []})  # the body comes first
        assert (bad.status_code, bad.json()["code"]) == (422, "INVALID_REQUEST")


async def test_a_tie_goes_to_the_region_served_first(
    lands: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    twin = Region(name="twin", states=("westland", "eastland"), crs_epsg=32610)
    monkeypatch.setitem(REGIONS, "twin", twin)
    save_build(lands, BIG, "t1", region="twin")
    write_fixture_places(lands, region="lands")
    for first, second in (("lands", "twin"), ("twin", "lands")):
        async with hosted(lands, builds=((first, "latest"), (second, "latest"))) as (http, _):
            assert (await picked(http, ACROSS))["region"] == first


def test_the_refusal_says_where_the_maps_are() -> None:
    def where(*served: str) -> str:
        sentence = outside_sentence(served)
        return sentence.removeprefix("That stop is outside the map. Try somewhere in ")

    # a national build served: the lower 48, whatever else is
    for served in (("us-t3",), ("oregon", "us-t3", "portland"), ("us-t1",), ("us",)):
        assert where(*served) == "the lower 48."
    # else the regions served, by their names, in the order served, each name once
    assert where("portland") == "Portland."
    assert where("oregon", "portland") == "Oregon or Portland."
    assert where("west-coast", "oregon", "portland") == "West Coast, Oregon or Portland."
    assert where("somewhere") == "somewhere."  # a region config does not name: its id
    assert where("oregon-washington", "oregon-washington-t1") == "Oregon and Washington."


async def test_a_stop_no_region_holds_is_named(
    lands: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_fixture_places(lands, region="lands")
    async with hosted(lands, builds=BOTH) as (http, _):
        outside = await http.post("/regions/pick", json={"stops": [*WEST, NOWHERE]})
        assert outside.status_code == 422
        assert outside.json() == {
            "code": "STOP_OUTSIDE_REGION",
            "message": "That stop is outside the map. Try somewhere in Westland or Lands.",
            "stop_index": 2,
            "lat": 40.0,
            "lon": -100.0,
        }
    # Each stop in some region, none holding all: the stop outside the one holding the most.
    far = Region(name="portland", states=("oregon",), bbox=(-101, 39, -99, 41), crs_epsg=32610)
    monkeypatch.setitem(REGIONS, "portland", far)
    save_build(lands, GRID, "grid", bbox=[-101, 39, -99, 41])
    async with hosted(lands, builds=(("portland", "latest"), ("westland", "latest"))) as (http, _):
        split = await http.post("/regions/pick", json={"stops": [WEST[0], NOWHERE, WEST[1]]})
        assert split.status_code == 422
        assert split.json() == {
            "code": "STOP_OUTSIDE_REGION",
            "message": "These stops are on different maps. Keep them on one map.",
            "stop_index": 1,
            "lat": 40.0,
            "lon": -100.0,
        }


async def test_the_body_needs_a_stop(lands: Path) -> None:
    bodies: list[dict[str, Any]] = [
        {"stops": []},
        {},
        {"stops": [{"lat": 95, "lon": 0}]},
        {"stops": WEST, "x": 1},
    ]
    async with hosted(lands, builds=BOTH) as (http, _):
        for body in bodies:
            answer = await http.post("/regions/pick", json=body)
            assert answer.status_code == 422 and answer.json()["code"] == "INVALID_REQUEST"
        empty = await http.post("/regions/pick", json={"stops": []})
        assert empty.json()["field"] == "stops"


async def test_a_job_plans_on_the_region_picked(lands: Path) -> None:
    write_fixture_places(lands, region="lands")
    async with hosted(lands, builds=BOTH) as (http, _):
        for stops, region in ((WEST, "westland"), (ACROSS, "lands")):
            pick = await picked(http, stops)
            assert pick["region"] == region
            body = {"region": pick["region"], "stops": stops, "seed": 3, "max_iterations": 300}
            created = await http.post("/jobs", json=body)
            assert created.status_code == 202, created.text
            job = created.json()
            await http.get(job["events_url"])
            status = (await http.get(f"/jobs/{job['job_id']}")).json()
            assert (status["status"], status["region"], status["build_id"]) == (
                "done",
                region,
                pick["build_id"],
            )
