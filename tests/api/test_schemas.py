import math

import pytest
from pydantic import ValidationError

from worst_route.api.schemas import RouteRequestIn
from worst_route.config import API_DEFAULT_MAX_ITERATIONS, DEFAULT_MAX_ITERATIONS
from worst_route.engine.request import RouteRequest

STOPS = [
    {"lat": 45.5231, "lon": -122.6765, "label": "Powell's"},
    {"lat": 45.4817, "lon": -122.6263},
]


def test_the_request_mirrors_the_engine_s() -> None:
    req = RouteRequestIn.model_validate(
        {"region": "portland", "stops": STOPS, "tier": "T3", "leash_km": 8, "budget_s": 15}
    )
    assert req.engine_request(7) == RouteRequest(
        stops=[(45.5231, -122.6765), (45.4817, -122.6263)],
        objective="distance",
        tier="T3",
        leash_km=8,
        budget_s=15,
        max_iterations=API_DEFAULT_MAX_ITERATIONS,
        seed=7,
    )


def test_a_seed_is_at_most_the_largest_int64() -> None:
    req = RouteRequestIn.model_validate({"region": "portland", "stops": STOPS, "seed": 2**63 - 1})
    assert req.engine_request(2**63 - 1).seed == 2**63 - 1


def test_the_defaults_leave_the_seconds_budget_to_end_a_run() -> None:
    req = RouteRequestIn.model_validate({"region": "portland", "stops": STOPS})
    assert req.max_iterations == API_DEFAULT_MAX_ITERATIONS > DEFAULT_MAX_ITERATIONS
    assert (req.budget_s, req.tier, req.leash_km, req.objective) == (20, "auto", "auto", "distance")
    assert req.target.kind == "max" and req.seed is None and req.base_route_id is None
    again = RouteRequestIn.model_validate(req.model_dump(mode="json"))
    assert again == req


def test_stops_may_be_left_out_only_to_continue_a_route() -> None:
    with pytest.raises(ValidationError, match="stops are needed"):
        RouteRequestIn.model_validate({"region": "portland"})
    assert (
        RouteRequestIn.model_validate({"region": "portland", "base_route_id": "abc"}).stops is None
    )


@pytest.mark.parametrize(
    "change",
    [
        {"stops": [{"lat": 91, "lon": 0}, {"lat": 0, "lon": 0}]},
        {"stops": [{"lat": math.nan, "lon": 0}, {"lat": 0, "lon": 0}]},
        {"stops": STOPS[:1]},
        {"leash_km": 151},
        {"budget_s": 0},
        {"max_iterations": -1},
        {"seed": -1},
        {"seed": 2**64},
        {"tier": "T9"},
        {"target": {"kind": "slowly"}},
        {"vehicle": {"mpg": 0, "gas_price_usd": 3}},
        {"constraints": {"no_left": True}},
    ],
)
def test_a_malformed_request_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RouteRequestIn.model_validate({"region": "portland", "stops": STOPS, **change})
