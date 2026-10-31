import math

import pytest
from pydantic import ValidationError

from worst_route.api.schemas import Done, RouteRequestIn, TargetMetrics
from worst_route.config import API_DEFAULT_MAX_ITERATIONS, DEFAULT_MAX_ITERATIONS
from worst_route.engine.request import RouteRequest
from worst_route.engine.target import Target

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


def test_a_pilgrimage_s_brand_reaches_the_engine() -> None:
    body = {"region": "portland", "stops": STOPS, "objective": "brand", "brand": 4}
    engine = RouteRequestIn.model_validate(body).engine_request(1)
    assert (engine.objective, engine.brand) == ("brand", 4)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        RouteRequestIn.model_validate({**body, "brand": -1})


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


@pytest.mark.parametrize(
    ("target", "engine"),
    [
        ({"kind": "max"}, Target()),
        ({"kind": "ratio", "value": 50}, Target(kind="ratio", value=50)),
        ({"kind": "duration", "seconds": 32400}, Target(kind="duration", value=32400)),
    ],
)
def test_the_target_is_the_engine_s(target: dict[str, object], engine: Target) -> None:
    req = RouteRequestIn.model_validate({"region": "portland", "stops": STOPS, "target": target})
    assert req.engine_request(1).target == engine


def test_done_carries_a_typed_target() -> None:
    turns = {"left": 2, "right": 3, "straight": 4, "u_turn": 0}
    metrics = {
        "length_m": 900.0,
        "time_s": 81.0,
        "ratio": 3.0,
        "time_ratio": 3.0,
        "delay_s": 0.0,
        "turns": turns,
    }
    target = {"kind": "ratio", "value": 3.0, "achieved": 3.0, "within_tolerance": True}
    done = Done(route_id="r", stopped_because="target", metrics={**metrics, "target": target})
    assert done.metrics.target == TargetMetrics(**target)
    plain = Done(route_id="r", stopped_because="budget", metrics=metrics)
    assert plain.metrics.target is None and plain.model_dump()["metrics"]["target"] is None
    with pytest.raises(ValidationError):
        Done(
            route_id="r", stopped_because="target", metrics={**metrics, "target": {"kind": "ratio"}}
        )


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
        {"target": {"kind": "max", "value": 2}},
        {"target": {"kind": "ratio"}},
        {"target": {"kind": "ratio", "value": 1}},
        {"target": {"kind": "ratio", "value": 5, "seconds": 60}},
        {"target": {"kind": "duration", "value": 60}},
        {"target": {"kind": "duration", "seconds": 7 * 24 * 3600 + 1}},
        {"vehicle": {"mpg": 0, "gas_price_usd": 3}},
        {"constraints": {"no_left": True, "no_right": True}},
        {"constraints": {"no_u_turns": True}},
    ],
)
def test_a_malformed_request_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RouteRequestIn.model_validate({"region": "portland", "stops": STOPS, **change})
