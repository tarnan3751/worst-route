# The share image's network on Portland's latest build, read through WORST_ROUTE_DATA: the roads
# a full decode finds, on the map of Powell's Books → Reed College and on smaller maps inside it.
# Run on demand with `uv run pytest -m data`; skipped when there is no build.
import numpy as np
import pytest

from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.graph.data import GraphData
from worst_route.graph.geometry import Geometry, read_geometry
from worst_route.graph.store import read_build
from worst_route.pipeline import layout
from worst_route.presentation import share_image
from worst_route.presentation.geojson import line_coordinates

from .test_share_image import by_full_decode

pytestmark = pytest.mark.data

POWELLS, REED = (45.5231, -122.6765), (45.4817, -122.6263)


@pytest.fixture(scope="module")
def portland() -> GraphData:
    latest = layout.latest_file("portland")
    if not latest.exists():
        pytest.skip(f"no Portland build under {latest.parent}")
    return read_build(layout.build_dir("portland", latest.read_text().strip()))


@pytest.fixture(scope="module")
def geometry(portland: GraphData) -> Geometry:
    return read_geometry(layout.build_dir("portland", portland.meta["build_id"]), portland.m)


def test_the_network_finds_the_roads_a_full_decode_finds(
    portland: GraphData, geometry: Geometry
) -> None:
    request = RouteRequest(stops=[POWELLS, REED], seed=1, max_iterations=2000, budget_s=120)
    route = plan_route(portland, request).route
    line = np.asarray(line_coordinates(portland, geometry, route.edges))
    aspect = (1 - share_image.COLUMN) * share_image.WIDTH_PX / share_image.HEIGHT_PX
    west, east, south, north = share_image._extent(line, aspect)
    x, y = (west + east) / 2, (south + north) / 2
    for f in (1.0, 0.3, 0.05):  # the route's map, then smaller ones about its middle
        bbox = (x - (x - west) * f, y - (y - south) * f, x + (east - x) * f, y + (north - y) * f)
        lines = share_image._network(portland, geometry, bbox)
        full = by_full_decode(portland, geometry, bbox)
        assert 0 < len(lines) == len(full) <= share_image.MAX_NETWORK_EDGES
        assert all(map(np.array_equal, lines, full))
