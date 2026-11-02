# A portfolio on Portland's build through the API (WORST_ROUTE_DATA), its two searches in spawned
# worker processes: Powell's to Reed College with seeds 1 and 2 at 1,500 attempts each stores the
# better of the two routes `plan_route` gives for those seeds, under the winner's seed. Run on
# demand with `uv run pytest -m data`; skipped without the build.
from pathlib import Path

import httpx
import pytest

from worst_route.api.app import create_app
from worst_route.api.jobs import JobSettings
from worst_route.api.store import route_id
from worst_route.config import settings
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.graph.loader import load_build
from worst_route.pipeline import layout

from .support import api_settings
from .test_routes_api import events_of

pytestmark = [pytest.mark.data, pytest.mark.anyio]

POWELLS, REED = (45.5231, -122.6765), (45.4817, -122.6263)


async def test_a_portfolio_on_portland_stores_the_better_of_its_seeds_routes(
    tmp_path: Path,
) -> None:
    if not layout.latest_file("portland").exists():
        pytest.skip(f"no portland build under {settings().data_dir}")
    build = load_build(settings().data_dir, "portland")
    asked = RouteRequest(stops=[POWELLS, REED], seed=1, max_iterations=1500, budget_s=120)
    plans = [
        plan_route(
            build.data,
            asked.model_copy(update={"seed": seed}),
            backend=build.backend,
            spatial=build.spatial,
        )
        for seed in (1, 2)
    ]
    best = max(plans, key=lambda p: p.route.score)  # the first of the best
    config = api_settings(
        settings().data_dir,
        builds=(("portland", "latest"),),
        store_path=tmp_path / "routes.sqlite",
        jobs=JobSettings(inline_workers=False),
        geocoder="none",
    )
    app = create_app(config)
    body = {
        "region": "portland",
        "stops": [{"lat": lat, "lon": lon} for lat, lon in (POWELLS, REED)],
        "seed": 1,
        "max_iterations": 1500,
        "budget_s": 120,
        "portfolio": 2,
    }
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/jobs", json=body)
            assert created.status_code == 202, created.text
            events = events_of((await client.get(created.json()["events_url"])).text)
            assert events[-1]["event"] == "done", events[-1]
            doc = (await client.get(f"/routes/{events[-1]['data']['route_id']}")).json()
    graph_version = build.data.meta["graph_version"]
    r = best.route
    assert doc["route_id"] == route_id(graph_version, r.nodes, r.edges, r.stop_idx)
    assert doc["seed"] == best.request.seed
    assert doc["run"]["portfolio"]["seeds"] == [1, 2]
    assert doc["run"]["attempts"] == best.run.attempts == 1500
    assert doc["metrics"]["length_m"] == best.route.metrics.length_m
    assert doc["validation"]["ok"] is True
    progress = [e["data"] for e in events if e["event"] == "progress"]
    assert progress and all(p["portfolio"]["k"] == 2 for p in progress)
