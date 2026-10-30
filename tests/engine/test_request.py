import json

import numpy as np
import pytest
from pydantic import ValidationError

from worst_route.config import HighwayClass
from worst_route.engine.request import RouteRequest, auto_leash_m, auto_tier, request_masks
from worst_route.engine.target import Target
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import Edge, make_graph

from .graphs import X0, Y0, at_node, lattice

PORTLAND = [(45.5231, -122.6765), (45.4817, -122.6263)]


def test_defaults() -> None:
    request = RouteRequest(stops=PORTLAND, seed=1)
    assert request.objective == "distance"
    assert (request.tier, request.leash_km) == ("auto", "auto")
    assert (request.budget_s, request.max_iterations) == (20.0, 5000)
    assert request.target == Target(kind="max")


@pytest.mark.parametrize(
    "bad",
    [
        {"stops": PORTLAND[:1]},
        {"stops": [(91.0, 0.0), (0.0, 0.0)]},
        {"stops": [(0.0, 181.0), (0.0, 0.0)]},
        {"leash_km": 0},
        {"leash_km": 150.5},
        {"leash_km": float("inf")},
        {"leash_km": float("nan")},
        {"leash_km": "far"},
        {"tier": "T5"},
        {"seed": -1},
        {"budget_s": 0},
        {"budget_s": 120.5},
        {"budget_s": float("inf")},
        {"budget_s": float("nan")},
        {"stops": [(float("nan"), 0.0), (0.0, 0.0)]},
        {"max_iterations": -1},
        {"target": {"kind": "ratio", "value": 1}},
        {"target": {"kind": "duration", "value": -60}},
        {"colour": "red"},
    ],
)
def test_bad_requests_are_refused(bad: dict[str, object]) -> None:
    fields: dict[str, object] = {"stops": PORTLAND, "seed": 1, **bad}
    with pytest.raises(ValidationError):
        RouteRequest.model_validate(fields)


def test_the_bounds_themselves_are_allowed() -> None:
    request = RouteRequest(stops=PORTLAND, leash_km=150, budget_s=120, seed=0)
    assert (request.leash_km, request.budget_s) == (150, 120)
    assert "Infinity" not in json.dumps(request.model_dump(mode="json"))


def test_a_round_trip_has_no_ratio_target() -> None:
    loop = [PORTLAND[0], PORTLAND[0]]
    with pytest.raises(ValidationError, match="a round trip has no ratio target"):
        RouteRequest(stops=loop, seed=1, target=Target(kind="ratio", value=5))
    assert RouteRequest(stops=loop, seed=1, target=Target(kind="duration", value=600)).target
    assert RouteRequest(stops=PORTLAND, seed=1, target=Target(kind="ratio", value=5)).target


def test_round_trips_through_json() -> None:
    request = RouteRequest(
        stops=PORTLAND,
        tier="T3",
        leash_km=4.5,
        budget_s=3,
        seed=7,
        target=Target(kind="duration", value=32400),
    )
    again = RouteRequest.model_validate(json.loads(json.dumps(request.model_dump(mode="json"))))
    assert again == request
    auto = RouteRequest(stops=PORTLAND, seed=0)
    assert RouteRequest.model_validate(auto.model_dump(mode="json")) == auto


@pytest.mark.parametrize(
    ("trip_m", "tier"),
    [
        (0, "T4"),
        (14_999, "T4"),
        (15_000, "T3"),
        (59_999, "T3"),
        (60_000, "T2"),
        (250_000, "T1"),
        (800_000, "T0"),
    ],
)
def test_auto_tier(trip_m: float, tier: str) -> None:
    assert auto_tier(trip_m) == tier


def test_auto_leash() -> None:
    assert auto_leash_m(0) == 3000
    assert auto_leash_m(10_000) == pytest.approx(6000)
    assert auto_leash_m(1e6) == 150_000


def test_masks_of_a_short_trip_cover_the_grid() -> None:
    g = lattice(5, 5)
    request = RouteRequest(stops=[at_node(g, 0), at_node(g, 24)], seed=0)
    masks = request_masks(SpatialIndex(g), g, request)
    assert masks.tier == "T4"
    assert masks.trip_m == pytest.approx(400 * 2**0.5, abs=1e-6)
    assert masks.leash_m == 3000
    assert masks.active.all() and masks.bubbles.all() and not masks.banned.any()
    assert masks.extent_m == pytest.approx(400 * 2**0.5)
    for mask in (masks.leash, masks.bubbles, masks.active, masks.banned):
        assert not mask.flags.writeable


def test_a_given_leash_narrows_the_area() -> None:
    g = lattice(5, 5)
    request = RouteRequest(stops=[at_node(g, 0), at_node(g, 24)], leash_km=0.05, seed=0)
    masks = request_masks(SpatialIndex(g), g, request)
    assert np.flatnonzero(masks.active).tolist() == [0, 6, 12, 18, 24]
    assert masks.leash_m == 50


def highway() -> tuple[GraphData, np.ndarray]:
    """A 10 km primary road 0 - 1 - 2 - 3 - 4 with residential streets beside it.

    Residential 0 ↔ 1 lies in the start's bubble, 1 ↔ 2 runs outside every bubble, and node 5
    hangs off 2 by a residential street only.
    """
    xy = [(X0, Y0), (X0 + 2000, Y0), (X0 + 5000, Y0), (X0 + 8000, Y0), (X0 + 10_000, Y0)]
    xy.append((X0 + 5000, Y0 + 1000))
    edges = []
    for a, b in [(0, 1), (1, 2), (2, 3), (3, 4)]:
        edges += [
            Edge(a, b, highway=HighwayClass.PRIMARY),
            Edge(b, a, highway=HighwayClass.PRIMARY),
        ]
    for a, b in [(0, 1), (1, 2), (2, 5)]:
        edges += [Edge(a, b, length_m=10.0), Edge(b, a, length_m=10.0)]
    g = make_graph(xy, edges)
    residential = np.flatnonzero(g.edge_highway == HighwayClass.RESIDENTIAL)
    return g, residential


def test_a_tier_bans_lower_classes_outside_the_bubbles() -> None:
    g, residential = highway()
    stops = [at_node(g, 0), at_node(g, 4)]
    masks = request_masks(SpatialIndex(g), g, RouteRequest(stops=stops, tier="T1", seed=0))
    near = {(0, 1), (1, 0)}
    expected = [e for e in residential if (int(g.edge_u[e]), int(g.edge_v[e])) not in near]
    assert np.flatnonzero(masks.banned).tolist() == sorted(expected)
    assert np.flatnonzero(~masks.active).tolist() == [5]

    auto = request_masks(SpatialIndex(g), g, RouteRequest(stops=stops, seed=0))
    assert auto.tier == "T4" and not auto.banned.any() and auto.active.all()
