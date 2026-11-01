import json
from typing import get_args

import numpy as np
import pytest
from pydantic import ValidationError

from worst_route.config import HighwayClass
from worst_route.engine import request as request_module
from worst_route.engine.constraints import NO_CONSTRAINTS
from worst_route.engine.objectives import OBJECTIVE_KEYS, OBJECTIVES
from worst_route.engine.objectives.collect import ITEMS
from worst_route.engine.request import (
    ObjectiveKey,
    RouteRequest,
    auto_leash_m,
    auto_tier,
    max_reorder_stops,
    objective_problem,
    reorder_problem,
    request_masks,
)
from worst_route.engine.target import Target
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import Edge, make_graph

from .graphs import X0, Y0, at_node, exactly_at_node, lattice

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


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"objective": "collect"}, "the collect objective needs an item: toll_booth, "),
        ({"item": "bridge"}, "only the collect objective takes an item"),
        ({"objective": "collect", "item": "ufo"}, "Input should be 'toll_booth'"),
        (
            {"objective": "collect", "item": "bridge", "target": {"kind": "ratio", "value": 3}},
            "no ratio",
        ),
        (
            {"objective": "slowest"},
            "unknown objective 'slowest'; known: distance, time, collect, brand",
        ),
    ],
)
def test_only_collect_takes_an_item_and_it_takes_no_ratio(
    fields: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        RouteRequest.model_validate({"stops": PORTLAND, "seed": 1, **fields})


def test_a_collecting_request_round_trips_through_json() -> None:
    duration = Target(kind="duration", value=3600)
    for item in ITEMS:
        request = RouteRequest(
            stops=PORTLAND, seed=1, objective="collect", item=item, target=duration
        )
        dumped = request.model_dump(mode="json")
        assert (dumped["objective"], dumped["item"]) == ("collect", item)
        assert RouteRequest.model_validate(json.loads(json.dumps(dumped))) == request
    assert RouteRequest(stops=PORTLAND, seed=1).model_dump(mode="json")["item"] is None


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"objective": "brand"}, "the brand objective needs a brand"),
        ({"brand": 3}, "only the brand objective takes a brand"),
        ({"objective": "collect", "item": "bridge", "brand": 3}, "only the brand objective"),
        ({"objective": "brand", "brand": -1}, "greater than or equal to 0"),
        (
            {"objective": "brand", "brand": 3, "target": {"kind": "ratio", "value": 3}},
            "the brand objective takes no ratio target: it counts visits, not metres",
        ),
    ],
)
def test_only_the_brand_objective_takes_a_brand_and_it_takes_no_ratio(
    fields: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        RouteRequest.model_validate({"stops": PORTLAND, "seed": 1, **fields})


def test_a_pilgrimage_round_trips_through_json_and_others_leave_the_brand_out() -> None:
    duration = Target(kind="duration", value=3600)
    request = RouteRequest(stops=PORTLAND, seed=1, objective="brand", brand=3, target=duration)
    dumped = request.model_dump(mode="json")
    assert (dumped["objective"], dumped["brand"]) == ("brand", 3)
    assert RouteRequest.model_validate(json.loads(json.dumps(dumped))) == request
    plain = RouteRequest(stops=PORTLAND, seed=1)
    assert "brand" not in plain.model_dump(mode="json")  # as before brands existed
    assert plain.brand is None


def test_the_alphabet_route_takes_a_start_and_an_end_and_no_target() -> None:
    request = RouteRequest(stops=PORTLAND, seed=1, objective="alphabet")
    assert RouteRequest.model_validate(request.model_dump(mode="json")) == request
    loop = [PORTLAND[0], PORTLAND[0]]
    assert RouteRequest(stops=loop, seed=1, objective="alphabet").objective == "alphabet"
    via = [PORTLAND[0], (45.5, -122.65), PORTLAND[1]]
    with pytest.raises(ValidationError, match="the alphabet route takes a start and an end"):
        RouteRequest(stops=via, seed=1, objective="alphabet")
    for target in (Target(kind="duration", value=600), Target(kind="ratio", value=3)):
        with pytest.raises(ValidationError, match="the alphabet route takes no target"):
            RouteRequest(stops=PORTLAND, seed=1, objective="alphabet", target=target)
    assert objective_problem("alphabet", None, "max", None, 3) == (
        "stops",
        "the alphabet route takes a start and an end",
    )
    assert objective_problem("alphabet", None, "ratio") == (
        "target",
        "the alphabet route takes no target: it counts letters",
    )
    assert objective_problem("alphabet", "bridge", "max") == (
        "item",
        "only the collect objective takes an item",
    )
    assert objective_problem("distance", None, "max", None, 3) is None


def test_the_worst_order_is_asked_for_up_to_seven_stops_and_not_by_the_alphabet_route() -> None:
    seven = [(45.5 + k / 100, -122.6) for k in range(6)] + [PORTLAND[1]]
    request = RouteRequest(stops=seven, seed=1, reorder=True)
    assert request.model_dump(mode="json")["reorder"] is True
    assert RouteRequest.model_validate(json.loads(json.dumps(request.model_dump(mode="json")))) == (
        request
    )
    plain = RouteRequest(stops=PORTLAND, seed=1)
    assert not plain.reorder and "reorder" not in plain.model_dump(mode="json")  # as before
    with pytest.raises(ValidationError, match="the worst order takes at most 7 stops, not 8"):
        RouteRequest(stops=[*seven, PORTLAND[0]], seed=1, reorder=True)
    assert RouteRequest(stops=[*seven, PORTLAND[0]], seed=1).stops[-1] == PORTLAND[0]
    with pytest.raises(ValidationError, match="the alphabet route takes no worst order"):
        RouteRequest(stops=PORTLAND, seed=1, objective="alphabet", reorder=True)
    assert objective_problem("alphabet", None, "max", None, 2, True) == (
        "reorder",
        "the alphabet route takes no worst order: it has no stop to reorder",
    )
    assert objective_problem("distance", None, "max", None, 7, True) is None


def test_a_large_build_reorders_fewer_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    g = lattice(3, 3)
    assert max_reorder_stops(g) == 7 and reorder_problem(7, g) is None
    monkeypatch.setattr(request_module, "REORDER_LARGE_NODES", g.n - 1)  # 9 nodes now large
    assert max_reorder_stops(g) == 5 and reorder_problem(5, g) is None
    assert reorder_problem(6, g) == "the worst order takes at most 5 stops on test, not 6"
    assert reorder_problem(7) is None  # without a build, the most any build takes
    assert reorder_problem(8) == "the worst order takes at most 7 stops, not 8"


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
        (15_000, "T4"),
        (799_999, "T4"),
        (800_000, "T1"),
        (5_000_000, "T1"),
    ],
)
def test_auto_tier(trip_m: float, tier: str) -> None:
    assert auto_tier(trip_m) == tier


def test_auto_leash() -> None:
    assert auto_leash_m(0) == 3000
    assert auto_leash_m(10_000) == pytest.approx(6000)
    assert auto_leash_m(250_000) == 150_000
    assert auto_leash_m(750_000) == 150_000  # a fifth of the trip from here on
    assert auto_leash_m(1e6) == pytest.approx(200_000)
    assert auto_leash_m(4.4e6) == pytest.approx(880_000)


def test_masks_of_a_short_trip_cover_the_grid() -> None:
    g = lattice(5, 5)
    request = RouteRequest(stops=[exactly_at_node(g, 0), exactly_at_node(g, 24)], seed=0)
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


def test_the_objective_and_the_turn_constraints() -> None:
    assert set(get_args(ObjectiveKey)) == set(OBJECTIVE_KEYS) == {*OBJECTIVES, "alphabet"}
    assert RouteRequest(stops=PORTLAND, seed=1, objective="time").objective == "time"
    with pytest.raises(ValidationError, match="unknown objective 'slowest'; known: distance, time"):
        RouteRequest(stops=PORTLAND, seed=1, objective="slowest")
    plain = RouteRequest(stops=PORTLAND, seed=1)
    assert plain.constraints == NO_CONSTRAINTS
    assert "constraints" not in plain.model_dump(mode="json")  # as before constraints existed
    assert RouteRequest.model_validate(plain.model_dump(mode="json")) == plain
    soft = RouteRequest(stops=PORTLAND, seed=1, constraints={"no_left": True})
    written = json.loads(soft.model_dump_json())
    assert written["constraints"] == {"no_left": True, "no_right": False, "strict": False}
    assert RouteRequest.model_validate(written) == soft
    with pytest.raises(ValidationError, match="straight only is not a route"):
        RouteRequest(stops=PORTLAND, seed=1, constraints={"no_left": True, "no_right": True})
