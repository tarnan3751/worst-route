# The places index on Portland's latest build, read through WORST_ROUTE_DATA, with the index
# `worst-route aux places --region portland` wrote there (Oregon's and Washington's places). Run
# on demand with `uv run pytest -m data`; skipped when the build or the index is missing.
import pytest
from shapely import LineString

from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.graph.backends import make_backend
from worst_route.graph.data import GraphData
from worst_route.graph.geometry import Geometry, read_geometry
from worst_route.graph.store import read_build
from worst_route.pipeline import layout
from worst_route.presentation.places import PlaceIndex, route_line

pytestmark = pytest.mark.data

POWELLS, REED = (45.5231, -122.6765), (45.4817, -122.6263)
VANCOUVER = (45.6270, -122.6727)  # Esther Short Park, across the Columbia
PORTLAND_CITY, VANCOUVER_CITY = "4159000", "5374060"


@pytest.fixture(scope="module")
def portland() -> GraphData:
    latest = layout.latest_file("portland")
    if not latest.exists():
        pytest.skip(f"no Portland build under {latest.parent}")
    return read_build(layout.build_dir("portland", latest.read_text().strip()))


@pytest.fixture(scope="module")
def geometry(portland: GraphData) -> Geometry:
    return read_geometry(layout.build_dir("portland", portland.meta["build_id"]), portland.m)


@pytest.fixture(scope="module")
def index() -> PlaceIndex:
    loaded = PlaceIndex.load("portland")
    if loaded is None:
        pytest.skip("no places index: run `worst-route aux places --region portland`")
    return loaded


def test_powells_to_reed_stays_in_oregon_and_in_portland(
    portland: GraphData, geometry: Geometry, index: PlaceIndex
) -> None:
    request = RouteRequest(stops=[POWELLS, REED], seed=1, max_iterations=2000, budget_s=120)
    line = route_line(
        portland,
        geometry,
        plan_route(portland, request, backend=make_backend(portland)).route.edges,
    )
    assert [s.usps for s in index.states_crossed(line)] == ["OR"]
    towns = index.towns_passed(line)
    assert towns[0].geoid == PORTLAND_CITY
    assert (towns[0].name, towns[0].lsad, towns[0].usps) == ("Portland", "25", "OR")


def test_the_index_covers_vancouver_across_the_columbia(index: PlaceIndex) -> None:
    # A straight line: the build itself holds few Washington streets (the Oregon extract stops
    # just past the river), so a planned route's Vancouver stop snaps back to Hayden Island.
    lat, lon = POWELLS
    line = LineString([(lon, lat), (VANCOUVER[1], VANCOUVER[0])])
    assert [s.usps for s in index.states_crossed(line)] == ["OR", "WA"]
    towns = index.towns_passed(line)
    assert towns[0].geoid == PORTLAND_CITY
    assert towns[-1].geoid == VANCOUVER_CITY and towns[-1].usps == "WA"
