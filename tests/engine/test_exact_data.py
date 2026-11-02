# The certified area's reach against scipy's, on Portland's, Oregon's and the national T3 build,
# read through WORST_ROUTE_DATA: 200 random certified requests on each (two or three stops near
# each other, a leash up to 1 km or the automatic one), each area node for node and edge for
# edge and each automatic leash the ones scipy's breadth-first search gives. Run on demand with
# `uv run pytest -m data -k exact`; each build skipped when missing.
import pytest

from worst_route.graph.store import read_build
from worst_route.pipeline import layout

from .exact_parity import check_areas

pytestmark = pytest.mark.data


@pytest.mark.parametrize(
    ("region", "near_m", "auto_every"),
    [("portland", 2000.0, 5), ("oregon", 3000.0, 10), ("us-t3", 5000.0, 10)],
)
def test_the_areas_and_automatic_leashes_are_the_ones_scipy_s_reach_gives(
    region: str, near_m: float, auto_every: int
) -> None:
    latest = layout.latest_file(region)
    if not latest.exists():
        pytest.skip(f"no {region} build under {latest.parent}")
    build = read_build(layout.build_dir(region, latest.read_text().strip()))
    joined = check_areas(build, 200, seed=1, near_m=near_m, leash_m=1000.0, auto_every=auto_every)
    assert joined > 50
