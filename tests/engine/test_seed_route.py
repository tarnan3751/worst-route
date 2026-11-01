import math
from collections.abc import Sequence
from dataclasses import replace
from itertools import permutations

import numpy as np
import pytest

from worst_route.engine import seed_route as seed_route_module
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.route import Metrics, Route, check_route
from worst_route.engine.seed_route import (
    SeedRoute,
    baseline_route,
    choose_order,
    resolve_stops,
    seed_route,
)
from worst_route.engine.snap import StopUnreachable
from worst_route.engine.validator import validate_route
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import edge_id, make_graph

from .graphs import X0, Y0, at_node, lattice


def plan_seed(
    g: GraphData,
    stops: Sequence[tuple[float, float]],
    banned: np.ndarray | None = None,
    seed: int = 0,
) -> SeedRoute:
    """resolve_stops then seed_route, every node active and only the given bans."""
    spatial, backend = SpatialIndex(g), PythonBackend(g)
    request = RouteRequest(stops=list(stops), seed=seed)
    bans = banned if banned is not None else np.zeros(g.m, dtype=bool)
    masks = replace(request_masks(spatial, g, request), active=np.ones(g.n, bool), banned=bans)
    snaps = resolve_stops(g, spatial, backend, request, masks)
    assert resolve_stops(g, spatial, backend, request, masks) == snaps  # deterministic
    rng = np.random.Generator(np.random.PCG64(seed))
    found = seed_route(backend, spatial, g, snaps, masks.active, masks.banned, rng, r_max=10_000.0)
    check_route(g, found.nodes, found.edges, found.stop_idx, bans)
    assert list(found.snaps) == snaps
    assert found.nodes[list(found.stop_idx)].tolist() == [s.node for s in snaps]
    return found


def two_way(xy: list[tuple[float, float]], streets: list[tuple[int, int, float]]) -> GraphData:
    return make_graph(xy, [*streets, *((b, a, length) for a, b, length in streets)])


def test_legs_through_vias_are_node_disjoint() -> None:
    g = lattice(5, 5)
    stops = [at_node(g, v) for v in (0, 12, 4, 20)]
    seed = plan_seed(g, stops)
    assert [s.node for s in seed.snaps] == [0, 12, 4, 20]
    legs = [seed.nodes[a : b + 1] for a, b in zip(seed.stop_idx, seed.stop_idx[1:], strict=False)]
    for k, leg in enumerate(legs):
        for other in legs[k + 1 :]:
            shared = np.intersect1d(leg, other).tolist()
            assert shared in ([], [int(leg[-1])])  # only the stop they share, when adjacent
    assert len(np.unique(seed.nodes)) == len(seed.nodes)
    route = Route.new(
        g, seed.nodes, seed.edges, seed.stop_idx, score=0.0, metrics=measured(g, seed.edges), seed=0
    )
    report = validate_route(
        g,
        route,
        stop_nodes=[0, 12, 4, 20],
        node_mask=np.ones(g.n, dtype=bool),
        banned_edges=np.zeros(g.m, dtype=bool),
    )
    assert report.ok, report.lines()


def measured(g: GraphData, edges: np.ndarray) -> Metrics:
    return Metrics(
        math.fsum(g.edge_length_m[edges].astype(np.float64).tolist()),
        math.fsum(g.edge_time_s[edges].astype(np.float64).tolist()),
    )


def test_legs_avoid_the_later_stops() -> None:
    # the shortest 0 → 2 passes the end, 1; the leg goes round instead
    g = lattice(3, 3)
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 2), at_node(g, 1)])
    assert seed.nodes.tolist() == [0, 3, 4, 5, 2, 1]
    assert seed.stop_idx == (0, 4, 5)


def corridor() -> GraphData:
    """A (0) reaches B (2) through X (1), or the long way through Y (3) and Z (4); from B the
    only way on to C (5) is back through X.

        A ── X ── B          A → X → B: 200 m
        │    │ ╲             A → Y → Z → B: 450 m
        Y ── Z  C            B → X → C: 200 m, the only way
    """
    xy = [
        (X0, Y0),
        (X0 + 100, Y0),
        (X0 + 200, Y0),
        (X0, Y0 - 150),
        (X0 + 150, Y0 - 150),
        (X0 + 200, Y0 - 100),
    ]
    edges = [(0, 1, 100.0), (1, 2, 100.0), (2, 1, 100.0), (1, 5, 100.0)]
    edges += [(0, 3, 150.0), (3, 4, 150.0), (4, 2, 150.0)]
    return make_graph(xy, edges)


def test_the_penalty_resolve_moves_an_earlier_leg_off_a_corridor() -> None:
    g = corridor()
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 2), at_node(g, 5)])
    assert seed.nodes.tolist() == [0, 3, 4, 2, 1, 5]
    assert [s.node for s in seed.snaps] == [0, 2, 5] and all(s.note is None for s in seed.snaps)


def test_the_baseline_is_unconstrained() -> None:
    g = corridor()
    everywhere = np.ones(g.n, dtype=bool)
    baseline = baseline_route(PythonBackend(g), g, [0, 2, 5], everywhere, np.zeros(g.m, dtype=bool))
    assert baseline.nodes.tolist() == [0, 1, 2, 1, 5]  # the legs share X
    assert baseline.length_m == 400.0
    assert baseline.time_s == pytest.approx(400.0 / (40 / 3.6), rel=1e-6)
    assert baseline.edges.tolist() == [
        edge_id(g, 0, 1),
        edge_id(g, 1, 2),
        edge_id(g, 2, 1),
        edge_id(g, 1, 5),
    ]


def test_the_baseline_respects_the_request_masks() -> None:
    g = lattice(3, 3)
    banned = np.zeros(g.m, dtype=bool)
    banned[edge_id(g, 1, 2)] = True
    baseline = baseline_route(PythonBackend(g), g, [0, 2], np.ones(g.n, bool), banned)
    assert baseline.nodes.tolist() == [0, 1, 4, 5, 2]
    with pytest.raises(StopUnreachable) as caught:
        mask = np.ones(g.n, dtype=bool)
        mask[[1, 4, 7]] = False
        baseline_route(PythonBackend(g), g, [0, 2], mask, np.zeros(g.m, bool))
    assert caught.value.leg == (0, 1) and caught.value.stop_index == 1
    assert str(caught.value).endswith("nor any route in the request's area.")


def test_the_seed_respects_the_request_bans() -> None:
    g = lattice(3, 3)
    banned = np.zeros(g.m, dtype=bool)
    banned[[edge_id(g, 0, 1), edge_id(g, 4, 5)]] = True
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 2)], banned)
    assert not banned[seed.edges].any()
    assert seed.nodes.tolist() == [0, 3, 4, 1, 2]


def subdivision() -> GraphData:
    """A main street 0 - 1 - 2 - 3 - 4 and a ring 5 - 6 - 7 whose one entrance is 2 ↔ 5.

             6 ─ 7
              ╲ ╱
               5
               │
    0 ─ 1 ─ 2 ─ 3 ─ 4
    """
    xy = [(X0 + 100.0 * k, Y0) for k in range(5)]
    xy += [(X0 + 200, Y0 + 100), (X0 + 170, Y0 + 200), (X0 + 270, Y0 + 200)]
    streets = [(0, 1, 100.0), (1, 2, 100.0), (2, 3, 100.0), (3, 4, 100.0), (2, 5, 100.0)]
    streets += [(5, 6, 104.0), (6, 7, 100.0), (7, 5, 112.0)]
    return two_way(xy, streets)


def test_a_trapped_via_walks_out_and_the_end_stays() -> None:
    # the via at 6 can be entered but not left: the failing leg is via → end, so the via moves
    # first; 7 and 5 fail (the ring has one way in and out), 2 on the main street works
    g = subdivision()
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 6), at_node(g, 4)])
    start, via, end = seed.snaps
    assert (start.node, via.node, end.node) == (0, 2, 4)
    assert via.note == "moved 202 m: nearer nodes cannot be reached without repeating one"
    assert start.note is None and end.note is None
    assert seed.nodes.tolist() == [0, 1, 2, 3, 4]


def test_the_start_never_moves() -> None:
    # the via at 5 hangs off the end, 3: the first leg cannot reach it without passing the end,
    # and the start does not move, so the via does (6 fails the same way, 2 works)
    #           5 ─ 6
    #            ╲ ╱
    # 0 ─ 1 ─ 2 ─ 3 ─ 4
    xy = [(X0 + 100.0 * k, Y0) for k in range(5)] + [(X0 + 280, Y0 + 100), (X0 + 380, Y0 + 120)]
    streets = [(0, 1, 100.0), (1, 2, 100.0), (2, 3, 100.0), (3, 4, 100.0)]
    streets += [(3, 5, 100.0), (5, 6, 100.0), (6, 3, 130.0)]
    g = two_way(xy, streets)
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 5), at_node(g, 3)])
    assert [s.node for s in seed.snaps] == [0, 2, 3]
    assert seed.snaps[1].note == "moved 128 m: nearer nodes cannot be reached without repeating one"
    assert seed.nodes.tolist() == [0, 1, 2, 3]


def test_a_stop_nothing_reaches_is_named() -> None:
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 8000, Y0), (X0 + 8100, Y0)]
    g = two_way(xy, [(0, 1, 100.0), (2, 3, 100.0)])
    with pytest.raises(StopUnreachable) as caught:
        plan_seed(g, [at_node(g, 0), at_node(g, 3)])
    assert caught.value.leg == (0, 1) and caught.value.stop_index == 1
    assert str(caught.value) == (
        "No route from stop 1 to stop 2 without repeating a road, "
        "after moving stop 2 up to 10 times."
    )


def test_a_point_to_point_trip_never_collapses_onto_the_start() -> None:
    # A ↔ X two-way and Y → B one-way: no road from A reaches B. The end, near B, may not move
    # onto A (the request is not a round trip) and nothing else near B can end a trip.
    #   B ← Y     A ↔ ... 6 km ... ↔ X
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 300, Y0), (X0 + 6300, Y0)]  # B, Y, A, X
    g = make_graph(xy, [(1, 0, 100.0), (2, 3, 6000.0), (3, 2, 6000.0)])
    with pytest.raises(StopUnreachable) as caught:
        plan_seed(g, [at_node(g, 2), at_node(g, 0)])
    assert caught.value.leg == (0, 1)
    assert str(caught.value).endswith("after moving stop 2 up to 10 times.")


def test_a_leg_between_two_stops_on_one_node_is_a_failed_leg() -> None:
    g = lattice(3, 3)
    snaps = [SpatialIndex(g).snap(*at_node(g, v), "via") for v in (0, 4, 4)]
    nowhere = np.zeros(g.m, dtype=bool)
    with pytest.raises(StopUnreachable) as caught:
        seed_route(
            PythonBackend(g), SpatialIndex(g), g, snaps, np.ones(g.n, bool), nowhere,
            np.random.Generator(np.random.PCG64(0)), r_max=1e4,
        )  # fmt: skip
    assert caught.value.leg == (1, 2)


def test_a_leg_nothing_covers_names_both_ends() -> None:
    # the via at 1 and the end at 4 are on two islands 8 km apart; neither can move far enough
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0), (X0 + 8000, Y0), (X0 + 8100, Y0)]
    g = two_way(xy, [(0, 1, 100.0), (1, 2, 100.0), (3, 4, 100.0)])
    with pytest.raises(StopUnreachable) as caught:
        plan_seed(g, [at_node(g, 0), at_node(g, 1), at_node(g, 4)])
    assert caught.value.leg == (1, 2) and caught.value.stop_index == 2
    assert str(caught.value) == (
        "No route from stop 2 to stop 3 without repeating a road, "
        "after moving stop 2 and stop 3 up to 10 times each."
    )


STRAIGHT_AT_4 = [((3, 4), (4, 5)), ((5, 4), (4, 3)), ((1, 4), (4, 7)), ((7, 4), (4, 1))]


def test_a_leg_turns_only_where_the_table_allows() -> None:
    g = lattice(3, 3, transitions=STRAIGHT_AT_4)
    # 3 → 4 → 7 is shortest but turns at 4, which goes only straight on: the leg goes round
    # by 6, and the end stays (plan_seed checks the result is legal)
    seed = plan_seed(g, [at_node(g, 3), at_node(g, 7)])
    assert seed.nodes.tolist() == [3, 6, 7]
    assert [(s.node, s.note) for s in seed.snaps] == [(3, None), (7, None)]


def test_the_turn_at_a_via_is_one_the_table_allows() -> None:
    # arriving at the via 4 from 3, the next leg must leave straight on, to 5, not up to 7
    g = lattice(3, 3, transitions=STRAIGHT_AT_4)
    seed = plan_seed(g, [at_node(g, 3), at_node(g, 4), at_node(g, 7)])
    assert seed.nodes.tolist() == [3, 4, 5, 8, 7]
    assert seed.stop_idx == (0, 1, 4)
    assert all(s.note is None for s in seed.snaps)


def test_a_baseline_leg_turns_legally_and_falls_back_to_the_blind_path() -> None:
    g = lattice(3, 3, transitions=STRAIGHT_AT_4)
    everywhere, nothing = np.ones(g.n, dtype=bool), np.zeros(g.m, dtype=bool)
    baseline = baseline_route(PythonBackend(g), g, [3, 4, 7], everywhere, nothing)
    assert baseline.nodes.tolist() == [3, 4, 7]  # independent legs: a lower bound at the via
    assert baseline_route(PythonBackend(g), g, [3, 7], everywhere, nothing).length_m == 200.0
    # S (0) → A (1) → D (2) turns at A only towards B (3); the legal way, S A B C A D, passes
    # A twice: the baseline takes the turn-blind path
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0), (X0 + 150, Y0 + 100), (X0 + 50, Y0 + 100)]
    loop = make_graph(
        xy,
        [(0, 1, 100.0), (1, 2, 100.0), (1, 3, 100.0), (3, 4, 100.0), (4, 1, 100.0)],
        transitions=[((0, 1), (1, 3)), ((4, 1), (1, 2))],
    )
    blind = baseline_route(
        PythonBackend(loop), loop, [0, 2], np.ones(loop.n, bool), np.zeros(loop.m, bool)
    )
    assert blind.nodes.tolist() == [0, 1, 2]


def test_a_leg_whose_cheapest_legal_walk_loops_takes_a_simple_path() -> None:
    # S → A → D turns at A only towards B, so the cheapest legal walk, S A B C A D, enters A
    # twice: the leg is searched again without C → A and takes the bypass S X D, and the end
    # stays (before, the end moved); the baseline keeps the turn-blind S A D, a lower bound
    from .test_search import loop

    g = loop(bypass=True)
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 2)])
    assert seed.nodes.tolist() == [0, 5, 2]
    assert all(s.note is None for s in seed.snaps)
    everywhere, nothing = np.ones(g.n, dtype=bool), np.zeros(g.m, dtype=bool)
    baseline = baseline_route(PythonBackend(g), g, [0, 2], everywhere, nothing)
    assert baseline.nodes.tolist() == [0, 1, 2]


def test_a_leg_the_chain_refused_enters_the_node_the_way_its_walk_came_back() -> None:
    # S A B C A D loops at A, and without C → A nothing is left: the leg's tree bans S → A
    # instead and takes S Y C A D, and the end stays where the chain of rounds refused the leg
    from .test_search import entered_again

    g = entered_again()
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 2)])
    assert seed.nodes.tolist() == [0, 5, 4, 1, 2]
    assert all(s.note is None for s in seed.snaps)


def test_a_leg_driving_both_carriageways_goes_round() -> None:
    # the shortest S → T drives a carriageway and its twin: the leg is searched again without
    # the later one, and the end stays (before, the leg failed and the end moved)
    from .test_search import divided

    g = divided()
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 3)])
    assert seed.nodes.tolist() == [0, 4, 5, 6, 3]
    assert all(s.note is None for s in seed.snaps)


def one_road(g: GraphData, a: int, b: int) -> GraphData:
    """g with a → b and b → a made the two directions of one road."""
    unit = g.edge_unit.copy()
    unit[edge_id(g, b, a)] = unit[edge_id(g, a, b)]
    return replace(g, edge_unit=unit)


def test_a_round_trip_through_a_neighbour_is_not_one_road_out_and_back() -> None:
    g = one_road(lattice(3, 3), 0, 1)
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 1), at_node(g, 0)])
    assert seed.nodes.tolist() == [0, 1, 4, 3, 0]
    assert all(s.note is None for s in seed.snaps)


def test_a_loop_does_not_come_back_along_its_way_out() -> None:
    # A (0) ↔ W (1) is one 600 m road and W → X → A the way round; the only waypoint 500 m out
    # is W, and the loop must not drive A → W → A
    xy = [(X0, Y0), (X0 + 600, Y0), (X0 + 300, Y0 + 300)]
    g = one_road(make_graph(xy, [(0, 1, 600.0), (1, 0, 600.0), (1, 2, 424.0), (2, 0, 424.0)]), 0, 1)
    loop = plan_seed(g, [at_node(g, 0), at_node(g, 0)])
    assert loop.nodes.tolist() == [0, 1, 2, 0]


@pytest.mark.parametrize("seed", range(5))
def test_a_round_trip_without_vias_is_a_loop_through_a_far_waypoint(seed: int) -> None:
    g = lattice(15, 15)
    centre = 7 * 15 + 7
    loop = plan_seed(g, [at_node(g, centre), at_node(g, centre)], seed=seed)
    assert loop.nodes[0] == loop.nodes[-1] == centre and loop.stop_idx == (0, len(loop.nodes) - 1)
    far = np.hypot(g.node_x[loop.nodes] - g.node_x[centre], g.node_y[loop.nodes] - g.node_y[centre])
    assert far.max() >= 500.0
    assert len(np.unique(loop.nodes[:-1])) == len(loop.nodes) - 1


def test_the_same_seed_gives_the_same_loop() -> None:
    g = lattice(15, 15)
    stops = [at_node(g, 112), at_node(g, 112)]
    assert plan_seed(g, stops, seed=3).nodes.tolist() == plan_seed(g, stops, seed=3).nodes.tolist()


def test_a_round_trip_with_no_loop_is_unreachable() -> None:
    xy = [(X0 + 300.0 * k, Y0) for k in range(8)]
    g = two_way(xy, [(k, k + 1, 300.0) for k in range(7)])  # a dead straight road
    with pytest.raises(StopUnreachable) as caught:
        plan_seed(g, [at_node(g, 3), at_node(g, 3)])
    assert caught.value.leg == (0, 1) and caught.value.stop_index == 1
    assert str(caught.value) == (
        "No route from stop 1 to stop 2 without repeating a road, "
        "after 20 tries at a loop through a waypoint."
    )


def test_a_round_trip_through_a_via() -> None:
    g = lattice(3, 3)
    seed = plan_seed(g, [at_node(g, 0), at_node(g, 8), at_node(g, 0)])
    assert seed.nodes[0] == seed.nodes[-1] == 0
    assert seed.nodes[seed.stop_idx[1]] == 8
    assert len(np.unique(seed.nodes[:-1])) == len(seed.nodes) - 1


# The worst order (`choose_order`). Each order's seed is found here as planning finds it for a
# request in that order (its own masks, `resolve_stops` with its fallback, `seed_route`): its
# length, whether a stop moved to fit it, or None where no seed fits.
Lengths = dict[tuple[int, ...], tuple[float, bool] | None]


def seed_lengths(g: GraphData, request: RouteRequest) -> Lengths:
    spatial, backend = SpatialIndex(g), PythonBackend(g)
    stops, lengths = request.stops, {}
    for between in permutations(range(1, len(stops) - 1)):
        order = (0, *between, len(stops) - 1)
        ordered = request.model_copy(update={"stops": [stops[k] for k in order]})
        masks = request_masks(spatial, g, ordered)
        try:
            snaps = resolve_stops(g, spatial, backend, ordered, masks)
        except StopUnreachable:
            lengths[order] = None
            continue
        rng = np.random.Generator(np.random.PCG64(0))
        found = seed_route(backend, spatial, g, snaps, masks.active, masks.banned, rng, r_max=1e4)
        moved = any(s.note is not None and "nearer nodes" in s.note for s in snaps)
        lengths[order] = (math.fsum(g.edge_length_m[found.edges].astype(float).tolist()), moved)
    return lengths


def first_longest(lengths: Lengths) -> tuple[int, ...]:
    """The first order, as `permutations` gives them, of the longest seed."""
    longest = max(found[0] for found in lengths.values() if found is not None)
    return next(o for o, found in lengths.items() if found is not None and found[0] == longest)


def reordered(g: GraphData, stops: Sequence[int]) -> tuple[RouteRequest, tuple[int, ...]]:
    request = RouteRequest(stops=[at_node(g, v) for v in stops], seed=0, reorder=True)
    return choose_order(g, SpatialIndex(g), PythonBackend(g), request)


@pytest.mark.parametrize(
    ("stops", "tied", "infeasible"),
    [
        ((0, 6, 18, 8, 24), 1, 0),
        ((0, 7, 17, 13, 24), 1, 0),
        ((12, 0, 4, 20, 24), 4, 0),  # four orders tie: the first wins
        ((0, 4, 20, 24, 0), 2, 0),  # a round trip, two tied
        ((0, 2, 22, 14, 10, 0), 1, 8),  # a round trip four stops round: 8 of 24 orders fit none
    ],
)
def test_the_worst_order_is_the_first_of_the_longest_seeds(
    stops: tuple[int, ...], tied: int, infeasible: int
) -> None:
    g = lattice(5, 5)
    request, order = reordered(g, stops)
    lengths = seed_lengths(g, RouteRequest(stops=[at_node(g, v) for v in stops], seed=0))
    longest = max(found[0] for found in lengths.values() if found is not None)
    assert sum(found is not None and found[0] == longest for found in lengths.values()) == tied
    assert sum(found is None for found in lengths.values()) == infeasible
    assert order == first_longest(lengths)
    assert order[0] == 0 and order[-1] == len(stops) - 1  # the start and the end stay
    assert request.stops == [at_node(g, stops[k]) for k in order]
    assert request.reorder and request.seed == 0


def test_a_stop_the_fallback_moves_counts_in_every_order() -> None:
    # the via at 17 lies in a ring whose one entrance is a road to lattice node 5: no seed fits
    # it in either order until it moves out, and the order the given one is not wins
    g = ring_town()
    request, order = reordered(g, (0, 17, 10, 15))
    lengths = seed_lengths(g, RouteRequest(stops=[at_node(g, v) for v in (0, 17, 10, 15)], seed=0))
    assert lengths == {(0, 1, 2, 3): (600.0, True), (0, 2, 1, 3): (1200.0, True)}
    assert order == (0, 2, 1, 3)


def test_the_given_order_s_refusal_is_raised_as_without_the_worst_order() -> None:
    # (0, 22, 2, 14, 10, 0) fits no seed in its own order, while others do: refused, with the
    # error `resolve_stops` gives, so a request is plannable in its order or not at all
    g = lattice(5, 5)
    request = RouteRequest(stops=[at_node(g, v) for v in (0, 22, 2, 14, 10, 0)], seed=0)
    spatial, backend = SpatialIndex(g), PythonBackend(g)
    with pytest.raises(StopUnreachable) as plain:
        resolve_stops(g, spatial, backend, request, request_masks(spatial, g, request))
    with pytest.raises(StopUnreachable) as caught:
        choose_order(g, spatial, backend, request.model_copy(update={"reorder": True}))
    assert (caught.value.leg, str(caught.value)) == (plain.value.leg, str(plain.value))


def test_the_worst_order_is_the_same_twice_and_on_both_backends() -> None:
    g = lattice(5, 5)
    request = RouteRequest(stops=[at_node(g, v) for v in (12, 0, 4, 20, 24)], seed=0, reorder=True)
    spatial = SpatialIndex(g)
    found = [choose_order(g, spatial, b(g), request) for b in (PythonBackend, CsrBackend) * 2]
    assert found[0][1] == (0, 2, 1, 3, 4)
    assert all(f == found[0] for f in found)


def test_one_stop_between_the_ends_has_one_order() -> None:
    g = lattice(3, 3)
    request = RouteRequest(stops=[at_node(g, v) for v in (0, 4, 8)], seed=0, reorder=True)
    assert choose_order(g, SpatialIndex(g), PythonBackend(g), request) == (request, (0, 1, 2))


def test_a_pilgrimage_skips_an_order_whose_area_lacks_its_brand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # every other order's area refused (the given order's area is planning's to check): the
    # given order stays, though (0, 3, 2, 1, 4) has the longest seed
    g = lattice(5, 5, brands={"Acme": [12]})
    asked: list[int] = []

    def out_of_reach(data: GraphData, brand: int, area: np.ndarray) -> str:
        asked.append(brand)
        return "none of its locations is in the area"

    monkeypatch.setattr(seed_route_module, "brand_problem", out_of_reach)
    stops = [at_node(g, v) for v in (0, 7, 17, 13, 24)]
    request = RouteRequest(stops=stops, seed=0, objective="brand", brand=0, reorder=True)
    assert choose_order(g, SpatialIndex(g), PythonBackend(g), request) == (request, (0, 1, 2, 3, 4))
    assert asked == [0] * 5
    assert first_longest(seed_lengths(g, request.model_copy(update={"reorder": False}))) == (
        0, 3, 2, 1, 4,
    )  # fmt: skip


def ring_town() -> GraphData:
    """A 4 x 4 lattice of 100 m streets (node r * 4 + c) and a ring 16 - 17 - 18 below node 5,
    whose one entrance is the road 5 - 16."""
    xy = [(X0 + c * 100.0, Y0 + r * 100.0) for r in range(4) for c in range(4)]
    streets = [(v, v + 1, 100.0) for v in range(16) if v % 4 < 3]
    streets += [(v, v + 4, 100.0) for v in range(12)]
    ax, ay = xy[5]
    xy += [(ax + 50, ay - 60), (ax + 20, ay - 150), (ax + 110, ay - 140)]
    streets += [(5, 16, 80.0), (16, 17, 95.0), (17, 18, 92.0), (18, 16, 90.0)]
    return two_way(xy, streets)
