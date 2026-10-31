"""Several builds served at once (`docs/reference/api.md`, **Running it**), on the `lands` data
directory's two regions: `/regions`, `/health` and `/ready` over both, a job on each, a stored
route drawn on its own build after a restart (served or not), the places index a region without
its own falls back to, and UNKNOWN_REGION."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from worst_route.api import routes_api
from worst_route.api.app import Api
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


# Within Westland, the grid's west half; and across both states, the big lattice's corners.
WEST_TRIP = {"region": "westland", "stops": [stop(GRID, 0), stop(GRID, 94)], "seed": 3}
CROSS_TRIP = {"region": "lands", "stops": [stop(BIG, 0), stop(BIG, 399)], "seed": 3}


async def planned(http: httpx.AsyncClient, body: dict[str, Any]) -> str:
    """Plan `body` to its end; its route's id."""
    created = await http.post("/jobs", json={**body, "max_iterations": 300})
    assert created.status_code == 202, created.text
    job = created.json()
    await http.get(job["events_url"])  # the whole stream: the job has ended
    status = (await http.get(f"/jobs/{job['job_id']}")).json()
    assert status["status"] == "done", status
    route_id: str = status["route_id"]
    return route_id


def places_detail(ready: httpx.Response) -> dict[str, Any]:
    [check] = [c for c in ready.json()["checks"] if c["name"] == "places"]
    return dict(check)


async def test_the_builds_are_served_in_their_order(lands: Path) -> None:
    async with hosted(lands, builds=BOTH) as (http, _):
        regions = (await http.get("/regions")).json()
        assert [(r["region"], r["build_id"]) for r in regions] == [
            ("westland", "w1"),
            ("lands", "l1"),
        ]
        assert [r["counts"]["nodes"] for r in regions] == [100, 400]
        health = (await http.get("/health")).json()
        assert [(b["region"], b["build_id"]) for b in health["builds"]] == [
            ("westland", "w1"),
            ("lands", "l1"),
        ]
        ready = await http.get("/ready")
        [build] = [c for c in ready.json()["checks"] if c["name"] == "build"]
        assert build == {"name": "build", "ok": True, "detail": "westland/w1, lands/l1"}
        unknown = await http.post("/jobs", json={**WEST_TRIP, "region": "atlantis"})
        assert unknown.status_code == 422
        assert unknown.json() == {
            "code": "UNKNOWN_REGION",
            "message": "region 'atlantis' is not served here (lands, westland)",
        }


async def test_a_job_runs_on_each_build(lands: Path) -> None:
    async with hosted(lands, builds=BOTH) as (http, app):
        api: Api = app.state.api
        for body, region, build_id in ((WEST_TRIP, "westland", "w1"), (CROSS_TRIP, "lands", "l1")):
            route_id = await planned(http, body)
            document = (await http.get(f"/routes/{route_id}")).json()
            assert (document["region"], document["build_id"]) == (region, build_id)
            assert document["request"]["region"] == region
            assert (await http.post(f"/routes/{route_id}/validate")).json()["ok"] is True
            stored = api.store.get(route_id)
            assert stored is not None
            assert routes_api.build_of(api, stored) is api.builds[region]  # the served one
        # The big lattice's far corner lies beyond the grid's nodes.
        outside = await http.post("/jobs", json={**CROSS_TRIP, "region": "westland"})
        assert outside.status_code == 422
        assert (outside.json()["code"], outside.json()["stop_index"]) == ("STOP_OUTSIDE_REGION", 1)


async def test_a_stored_route_is_drawn_on_its_own_build_after_a_restart(lands: Path) -> None:
    write_fixture_places(lands, region="lands")
    async with hosted(lands, builds=BOTH) as (http, _):
        cross = await planned(http, CROSS_TRIP)
        west = await planned(http, WEST_TRIP)
    # Served the other way round, lands now the default.
    async with hosted(lands, builds=tuple(reversed(BOTH))) as (http, _):
        document = (await http.get(f"/routes/{cross}")).json()
        assert (document["region"], document["build_id"]) == ("lands", "l1")
        assert document["metrics"]["states"] == ["Westland", "Eastland"]
        image = await http.get(f"/routes/{cross}/share.png")
        assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    # Lands alone: the westland route's build is read from disk, its places are lands's index.
    async with hosted(lands, builds=(("lands", "latest"),)) as (http, app):
        api: Api = app.state.api
        assert list(api.builds) == ["lands"]
        document = (await http.get(f"/routes/{west}")).json()
        assert (document["region"], document["build_id"]) == ("westland", "w1")
        assert document["metrics"]["states"][0] == "Westland"
        assert document["metrics"]["towns"] is not None
        assert (await http.get(f"/routes/{west}/share.png")).status_code == 200
        assert (await http.post(f"/routes/{west}/validate")).json()["ok"] is True


async def test_a_region_without_its_own_index_takes_a_covering_one(lands: Path) -> None:
    write_fixture_places(lands, region="lands")  # the four places of both states
    async with hosted(lands, builds=BOTH) as (http, app):
        api: Api = app.state.api
        assert api.places["westland"] is None and api.places["lands"] is not None
        assert routes_api.places_of(api, "westland") is api.places["lands"]
        ready = await http.get("/ready")
        assert ready.status_code == 200
        assert places_detail(ready) == {
            "name": "places",
            "ok": True,
            "detail": "westland: lands's index; lands: 4 towns",
        }
        document = (await http.get(f"/routes/{await planned(http, WEST_TRIP)}")).json()
        assert document["metrics"]["states"][0] == "Westland"
        assert "Westville" in document["metrics"]["towns"]
    write_fixture_places(lands, states=("91",), region="westland")  # its own: Westland's two
    async with hosted(lands, builds=BOTH) as (http, app):
        api = app.state.api
        assert routes_api.places_of(api, "westland") is api.places["westland"]
        detail = places_detail(await http.get("/ready"))["detail"]
        assert detail == "westland: 2 towns; lands: 4 towns"


async def test_a_region_clipped_to_a_bbox_covers_no_other_but_is_covered(
    lands: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Portland is clipped to its bbox, here over Westland: its index holds only the towns the
    # bbox meets, so it covers no other region, while a whole state's index covers it.
    portland = Region(name="portland", states=("westland",), bbox=GRID_BBOX, crs_epsg=32610)
    monkeypatch.setitem(REGIONS, "portland", portland)
    save_build(lands, GRID, "grid", bbox=GRID_BBOX)
    write_fixture_places(lands)
    async with hosted(lands, builds=(("portland", "latest"), ("westland", "latest"))) as (
        http,
        app,
    ):
        api: Api = app.state.api
        assert routes_api.places_of(api, "portland") is api.places["portland"]
        assert routes_api.places_of(api, "westland") is None
        ready = await http.get("/ready")
        assert ready.status_code == 503
        assert places_detail(ready) == {
            "name": "places",
            "ok": False,
            "detail": "no places index of westland: run `worst-route aux places --region westland`",
        }
        document = (await http.get(f"/routes/{await planned(http, WEST_TRIP)}")).json()
        assert document["metrics"]["states"] is None and document["metrics"]["towns"] is None
    (lands / "aux" / "places" / "portland.parquet").unlink()
    write_fixture_places(lands, region="lands")
    async with hosted(lands, builds=(("portland", "latest"), ("lands", "latest"))) as (_, app):
        api = app.state.api
        assert api.places["portland"] is None
        assert routes_api.places_of(api, "portland") is api.places["lands"]
