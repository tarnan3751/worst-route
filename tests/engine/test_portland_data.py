# Powell's Books → Reed College on Portland's latest build, read through WORST_ROUTE_DATA, on both
# backends. Run on demand with `uv run pytest -m data`; skipped when there is no build.
import pytest

from worst_route.engine.plan import plan_route
from worst_route.engine.request import RouteRequest
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.store import read_build
from worst_route.pipeline import layout

pytestmark = pytest.mark.data

POWELLS, REED = (45.5231, -122.6765), (45.4817, -122.6263)


@pytest.fixture(scope="module")
def portland() -> GraphData:
    latest = layout.latest_file("portland")
    if not latest.exists():
        pytest.skip(f"no Portland build under {latest.parent}")
    return read_build(layout.build_dir("portland", latest.read_text().strip()))


def test_both_backends_plan_the_same_route_on_portland(portland: GraphData) -> None:
    # Portland has transition tables, so every search is the transition-aware one: 4,000
    # attempts give the same bytes on both backends, splice for splice
    assert len(portland.trans_node) > 0
    request = RouteRequest(stops=[POWELLS, REED], seed=1, max_iterations=4000, budget_s=120)
    one, two = (
        plan_route(portland, request, backend=b(portland)) for b in (PythonBackend, CsrBackend)
    )
    assert (one.backend, two.backend) == ("python", "csr")
    assert one.run.attempts == two.run.attempts == 4000
    assert one.route.nodes.tobytes() == two.route.nodes.tobytes()
    assert one.route.edges.tobytes() == two.route.edges.tobytes()
    assert one.run.accepted == two.run.accepted > 0
    assert one.run.refused_transitions == two.run.refused_transitions == 0
    assert [e.splice.edges.tobytes() for e in one.run.events] == [
        e.splice.edges.tobytes() for e in two.run.events
    ]
    assert [s.node for s in one.snaps] == [s.node for s in two.snaps]
    assert one.baseline.length_m == two.baseline.length_m
