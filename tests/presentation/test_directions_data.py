# Powell's Books → Reed College on Portland's latest build, read through WORST_ROUTE_DATA: its
# directions and its toasts, with the places index when `aux places` wrote one beside the build.
# Run on demand with `uv run pytest -m data`; skipped when there is no build.
import pytest

from worst_route.config import TOASTS_MAX
from worst_route.engine.io import route_document
from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.graph.data import GraphData
from worst_route.graph.geometry import Geometry, read_geometry
from worst_route.graph.store import read_build
from worst_route.pipeline import layout
from worst_route.presentation.document import route_directions
from worst_route.presentation.places import PlaceIndex
from worst_route.presentation.stats import stats, toasts

pytestmark = pytest.mark.data

POWELLS, REED = (45.5231, -122.6765), (45.4817, -122.6263)
# The roads at Powell's City of Books (W Burnside St, NW Couch St, NW 10th and 11th Ave), as OSM
# names them
AT_POWELLS = (
    "West Burnside Street",
    "Northwest Couch Street",
    "Northwest 10th Avenue",
    "Northwest 11th Avenue",
    "Southwest 10th Avenue",
    "Southwest 11th Avenue",
)


@pytest.fixture(scope="module")
def portland() -> GraphData:
    latest = layout.latest_file("portland")
    if not latest.exists():
        pytest.skip(f"no Portland build under {latest.parent}")
    return read_build(layout.build_dir("portland", latest.read_text().strip()))


@pytest.fixture(scope="module")
def geometry(portland: GraphData) -> Geometry:
    return read_geometry(layout.build_dir("portland", portland.meta["build_id"]), portland.m)


def test_powells_to_reed_reads_as_directions_and_toasts(
    portland: GraphData, geometry: Geometry
) -> None:
    request = RouteRequest(stops=[POWELLS, REED], seed=1, max_iterations=4000, budget_s=120)
    result = plan_route(portland, request)
    route = result.route
    metrics = route_document(portland, result)["metrics"]
    steps = route_directions(
        portland, route.nodes, route.edges, route.stop_idx, labels=["Powell's", "Reed"],
        seed=1, ratio=metrics["ratio"],
    )  # fmt: skip
    first = steps[0].text
    assert first.startswith("Head ") and first.split(" on ", 1)[1] in AT_POWELLS, first
    assert all(s.text.strip() for s in steps)
    assert steps[-1].text == "Arrive at Reed"
    driving = [s.edge_range for s in steps if s.edge_range[0] < s.edge_range[1]]
    assert [a for a, _ in driving] == [0, *(b for _, b in driving[:-1])]
    assert driving[-1][1] == len(route.edges)
    assert sum(s.kind == "joke" for s in steps) > 5

    numbers = stats(
        portland, route.edges, length_m=route.metrics.length_m,
        baseline_length_m=result.baseline.length_m, vehicle=None,
        places=PlaceIndex.load("portland"), geometry=geometry, active=result.masks.active,
        banned=result.masks.banned,
    )  # fmt: skip
    lines = toasts(numbers, metrics, nodes=route.nodes, edges=route.edges)
    assert lines[0] == f"{metrics['ratio']:.1f}× longer than it had to be."
    assert 4 <= len(lines) <= TOASTS_MAX
    assert 0 < numbers.network_fraction < 1
