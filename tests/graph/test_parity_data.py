# scipy's answers against what replaced them in the graph package, on Portland's, Oregon's and
# the national T3 build, read through WORST_ROUTE_DATA: 10,000 random balls node for node and
# 1,000 snaps and bubble masks against the KD-tree. Run on demand with
# `uv run pytest -m data -k parity`; each build skipped when missing.
import pytest

from worst_route.graph.data import GraphData
from worst_route.graph.store import read_build
from worst_route.pipeline import layout

from .scipy_parity import check_balls, check_snaps

pytestmark = pytest.mark.data

REGIONS = ["portland", "oregon", "us-t3"]


@pytest.fixture(scope="module", params=REGIONS)
def build(request: pytest.FixtureRequest) -> GraphData:
    region: str = request.param
    latest = layout.latest_file(region)
    if not latest.exists():
        pytest.skip(f"no {region} build under {latest.parent}")
    return read_build(layout.build_dir(region, latest.read_text().strip()))


def test_the_balls_snaps_and_bubbles_are_the_kd_tree_s(build: GraphData) -> None:
    # balls up to 40 km about nodes and anywhere in the build's box; snaps within 10 km
    check_balls(build, 10_000, seed=1, scale=20_000.0)
    check_snaps(build, 1_000, seed=2, scale=5_000.0)
