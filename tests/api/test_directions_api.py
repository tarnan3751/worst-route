"""The presentation in the route document and `GET /routes/{id}/directions`, over httpx in
process: the directions with the stops' labels, the toasts, the fuel from the request's vehicle,
the states and towns from the places index (null without either), the share of the roads; the
directions alone the same as the document's; a route stored before the documents carried them
getting them; unknown routes and a route whose build is gone."""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from worst_route.api.app import ApiSettings, create_app
from worst_route.api.jobs import JobSettings
from worst_route.api.store import RouteStore
from worst_route.config import TOASTS_MAX
from worst_route.engine.io import route_document
from worst_route.engine.plan import plan_route

from ..presentation.support import write_fixture_places
from .support import GRID, PORTLAND, api_input, loaded, request

pytestmark = pytest.mark.anyio

EXTRA = ("extra_fuel_gal", "extra_cost_usd", "states", "towns", "towns_n", "network_fraction")


def store_route(data: Path, *, vehicle: dict[str, float] | None = None, **document: Any) -> str:
    """Plan a route on the lattice build through a via and store it, as a job would (with
    `document`'s fields changed)."""
    build = loaded(data)
    req = request(GRID, [10, 45, 99], seed=3, max_iterations=200)
    result = plan_route(build.data, req, backend=build.backend)
    doc = {**route_document(build.data, result), **document}
    asked = {**api_input(req), **({"vehicle": vehicle} if vehicle else {})}
    store = RouteStore(data / "routes.sqlite")
    return store.put(doc, input=asked, baseline_edges=result.baseline.edges)


@asynccontextmanager
async def serving(data: Path) -> AsyncIterator[httpx.AsyncClient]:
    config = ApiSettings(
        data_dir=data,
        builds=PORTLAND,
        backend="python",
        jobs=JobSettings(inline_workers=True),
        geocoder="none",
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_the_document_carries_directions_toasts_and_the_presentation_s_numbers(
    data: Path,
) -> None:
    write_fixture_places(data)
    route_id = store_route(data, vehicle={"mpg": 24, "gas_price_usd": 3.89})
    async with serving(data) as client:
        doc = (await client.get(f"/routes/{route_id}")).json()
    steps, m = doc["directions"], doc["metrics"]
    assert [s["step"] for s in steps] == list(range(1, len(steps) + 1))
    assert steps[0]["kind"] == "depart" and steps[0]["text"].startswith("Head ")
    assert steps[-1] == {
        "step": len(steps), "text": "Arrive at stop 2", "distance_m": 0.0, "time_s": 0.0,
        "edge_range": [len(doc["geometry"]["coordinates"]) - 1] * 2, "kind": "arrive",
    }  # fmt: skip
    assert [s["text"] for s in steps if s["kind"] == "stop"] == ["Arrive at stop 1; continue"]
    driving = [s for s in steps if s["edge_range"][0] < s["edge_range"][1]]
    assert sum(s["distance_m"] for s in driving) == pytest.approx(m["length_m"])
    assert doc["toasts"][0] == f"{m['ratio']:.1f}× longer than it had to be."
    assert 4 <= len(doc["toasts"]) <= TOASTS_MAX
    miles = (m["length_m"] - m["baseline_length_m"]) / 1609.344
    assert m["extra_fuel_gal"] == pytest.approx(miles / 24)
    assert m["extra_cost_usd"] == pytest.approx(miles / 24 * 3.89)
    assert m["states"] == ["Westland", "Eastland"]
    assert m["towns"][0] == "Westville" and m["towns_n"] == len(m["towns"]) >= 2
    assert 0 < m["network_fraction"] <= 1
    assert any(t.startswith("An extra ") for t in doc["toasts"])
    assert any(t.startswith("Passes through ") for t in doc["toasts"])


async def test_without_a_vehicle_or_places_those_numbers_are_null(data: Path) -> None:
    route_id = store_route(data)
    async with serving(data) as client:
        m = (await client.get(f"/routes/{route_id}")).json()["metrics"]
    assert [m[k] for k in EXTRA[:5]] == [None] * 5
    assert m["network_fraction"] > 0


async def test_directions_alone_are_the_document_s(data: Path) -> None:
    route_id = store_route(data)
    async with serving(data) as client:
        doc = (await client.get(f"/routes/{route_id}")).json()
        alone = (await client.get(f"/routes/{route_id}/directions")).json()
        again = (await client.get(f"/routes/{route_id}/directions")).json()
    assert alone == {"route_id": route_id, "directions": doc["directions"]} == again
    assert any(s["kind"] == "joke" for s in alone["directions"])


async def test_a_route_stored_before_the_documents_carried_them_gets_them(data: Path) -> None:
    # every stored route has the layout route.json had before the presentation (nothing of it
    # is stored); one older still, without its delays and turns, gets them too
    route_id = store_route(data)
    with sqlite3.connect(data / "routes.sqlite") as con:
        (metrics,) = con.execute("SELECT metrics_json FROM routes").fetchone()
    assert not any(k in metrics for k in (*EXTRA, "directions", "toasts"))
    with sqlite3.connect(data / "routes.sqlite") as con:
        con.execute(
            "UPDATE routes SET metrics_json = json_remove(metrics_json, '$.delay_s', '$.turns')"
        )
    async with serving(data) as client:
        doc = (await client.get(f"/routes/{route_id}")).json()
    assert doc["directions"] and doc["toasts"] and doc["metrics"]["network_fraction"] > 0


async def test_unknown_routes_and_gone_builds(data: Path) -> None:
    gone = store_route(data, graph_version="another build")
    async with serving(data) as client:
        for path in ("/routes/0123456789ab/directions", "/routes/nope/directions"):
            r = await client.get(path)
            assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
        for path in (f"/routes/{gone}", f"/routes/{gone}/directions"):
            r = await client.get(path)
            assert r.status_code == 409 and r.json()["code"] == "BUILD_UNAVAILABLE"
