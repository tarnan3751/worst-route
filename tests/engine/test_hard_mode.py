"""Hard mode: each road (an identity) in one stretch at most. Its window check against a full
check of the spliced route, the bans it gives the searches per window, its terms (none), the
window it widens, `find_detour` keeping only the paths it allows; the request's flag, the seed
re-solving legs that come back to a road, what an unreachable leg blames, a base route that
breaks it, and the loop under it on both backends."""

import numpy as np
import pytest
from pydantic import ValidationError

from worst_route.engine.constraints import (
    NO_CONSTRAINTS,
    Constraints,
    HardMode,
    NoLeft,
    constrain,
    returning,
    stretches,
)
from worst_route.engine.detour import find_detour, local_exhaustive_paths
from worst_route.engine.objectives.composite import Composite
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.plan import BaseRouteMismatch, check_base, plan_route
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.route import Metrics, Route, splice_arrays
from worst_route.engine.seed_route import resolve_stops, seed_route
from worst_route.engine.snap import InfeasibleHardMode, InfeasibleTurnConstraint, StopUnreachable
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import Edge, edge_id, make_graph

from .graphs import X0, Y0, at_node, edges_of, lattice, left_only, route_on, two_rings
from .test_constraints import seeded

# node r * 6 + c, row 0 the southmost; rows "Row r", columns "Col c"; the road 8–9 has no name
GRID = lattice(6, 6, named=True, roads={(8, 9): {"name": None}})
# Row 0 east, Col 5 north, Row 1 west to Col 2, Col 2 north, Row 3 east: one stretch each
SNAKE = [0, 1, 2, 3, 4, 5, 11, 10, 9, 8, 14, 20, 21, 22]


def ident(g: GraphData, name: str) -> int:
    return g.strings.identities.index(name)


def one_stretch_each(g: GraphData, edges: np.ndarray) -> bool:
    """The rule, checked from scratch: each identity's positions are one interval."""
    ids = g.edge_identity[edges]
    for x in np.unique(ids[ids >= 0]):
        at = np.flatnonzero(ids == x)
        if at[-1] - at[0] + 1 != len(at):
            return False
    return True


def path(g: GraphData, nodes: list[int]) -> tuple[np.ndarray, np.ndarray]:
    return np.array(nodes, dtype=np.int32), edges_of(g, nodes)


def test_the_rule_reads_stretches_off_the_edges() -> None:
    edges = edges_of(GRID, SNAKE)
    ids, start, end = stretches(GRID, edges)
    names = [GRID.strings.identities[x] for x in ids]
    assert names == ["Row 0", "Col 5", "Row 1", "Col 2", "Row 3"]
    assert start.tolist() == [0, 5, 6, 9, 11] and end.tolist() == [5, 6, 8, 11, 13]
    assert returning(GRID, edges) is None
    # along Row 1 from 6 through the unnamed 8–9 and on: Row 1 on both sides is two stretches
    assert returning(GRID, edges_of(GRID, [6, 7, 8, 9, 10])) == ((0, 2), (3, 4))
    # Row 0, Col 1, Row 1, Col 0, Row 2: each once
    assert returning(GRID, edges_of(GRID, [0, 1, 7, 6, 12, 13])) is None
    # Row 0, Col 1, Row 1, down Col 2, Row 0 again
    assert returning(GRID, edges_of(GRID, [0, 1, 7, 8, 2, 3])) == ((0, 1), (4, 5))
    # no edges: no stretch, nothing comes back
    empty = np.zeros(0, dtype=np.int32)
    assert [len(a) for a in stretches(GRID, empty)] == [0, 0, 0]
    assert returning(GRID, empty) is None


def test_a_round_trip_coming_home_on_the_road_it_left_by_returns() -> None:
    # round a block from node 1: east on Row 0, up Col 2, west on Row 1, down Col 1 home
    assert returning(GRID, edges_of(GRID, [1, 2, 8, 7, 1])) is None
    # from node 2: east on Row 0, ..., home along Row 0 from node 1: Row 0 twice
    assert returning(GRID, edges_of(GRID, [2, 3, 9, 8, 7, 1, 2])) == ((0, 1), (5, 6))


def test_a_window_returning_to_a_road_or_splitting_a_stretch_is_refused() -> None:
    hard = HardMode(GRID)
    row2 = route_on(GRID, [12, 13, 14, 15, 16, 17])
    # inside Row 2: up Col 2, along Row 3, down Col 3 splits it in two
    assert not hard.feasible_window(GRID, row2, 2, 3, *path(GRID, [14, 20, 21, 15]))
    # at its end: off Row 2 onto fresh roads, never back
    assert hard.feasible_window(GRID, row2, 4, 5, *path(GRID, [16, 22, 23, 17]))
    # Row 2, Col 2, Row 3; Row 3's last edge replaced by a way down Col 3 to Row 2 and up
    # Col 4: back on Row 2, which the route left
    route = route_on(GRID, [12, 13, 14, 20, 21, 22])
    assert not hard.feasible_window(GRID, route, 4, 5, *path(GRID, [21, 15, 16, 22]))
    # by up Col 3, along Row 4 and down Col 4 instead: fresh roads
    assert hard.feasible_window(GRID, route, 4, 5, *path(GRID, [21, 27, 28, 22]))


def test_a_detour_extends_the_roads_either_side_of_its_window() -> None:
    hard = HardMode(GRID)
    # Col 0, Row 1, Col 2, Row 2: the window 6..14 (Row 1, Col 2) between Col 0 and Row 2
    route = route_on(GRID, [0, 6, 7, 8, 14, 15, 16])
    # on up Col 0, then Row 2 from 12: both neighbours extended
    assert hard.feasible_window(GRID, route, 1, 4, *path(GRID, [6, 12, 13, 14]))
    # on up Col 0, Row 2 to 13, down Col 1, Row 1 to 8 and up Col 2: Row 2 is left for Col 1
    # and met again after the window
    assert not hard.feasible_window(GRID, route, 1, 4, *path(GRID, [6, 12, 13, 7, 8, 14]))


def test_the_window_check_agrees_with_a_full_check_of_the_spliced_route() -> None:
    # every window of up to four edges of three legal routes, each replaced by the simple
    # paths a search could give it: the check answers as a full check of the spliced route
    backend, hard = PythonBackend(GRID), HardMode(GRID)
    seen = {True: 0, False: 0}
    for nodes in (SNAKE, [0, 1, 7, 8, 14, 15, 21, 22, 28, 29, 35], [0, 6, 12, 18, 19, 20, 21, 27]):
        route = route_on(GRID, nodes)
        assert one_stretch_each(GRID, route.edges)
        for i in range(len(route.edges)):
            for j in range(i + 1, min(i + 4, len(route.edges)) + 1):
                forbid, banned = route.release(GRID, i, j)
                paths = backend.simple_paths_dfs(
                    int(route.nodes[i]),
                    int(route.nodes[j]),
                    forbid_nodes=forbid,
                    banned_edges=banned,
                    max_depth=8,
                    max_paths=40,
                )
                for p in paths:
                    if np.array_equal(p[1], route.edges[i:j]):
                        continue
                    route.check_splice(GRID, i, j, *p)
                    full = one_stretch_each(GRID, splice_arrays(route, i, j, *p)[1])
                    assert hard.feasible_window(GRID, route, i, j, *p) == full, (nodes, i, j, p)
                    seen[full] += 1
    assert seen[True] > 100 and seen[False] > 100


def expected_bans(g: GraphData, route: Route, i: int, j: int) -> np.ndarray:
    ids = g.edge_identity[route.edges]
    a = ids[i - 1] if i > 0 else -1
    b = ids[j] if j < len(ids) else -1
    if a == b >= 0:
        return g.edge_identity != a
    outside = set(np.concatenate((ids[:i], ids[j:])).tolist()) - {-1, int(a), int(b)}
    return np.isin(g.edge_identity, sorted(outside))


def test_the_bans_are_every_road_on_the_route_outside_the_window_but_its_neighbours() -> None:
    hard, route = HardMode(GRID), route_on(GRID, SNAKE)
    for i in range(len(route.edges)):
        for j in range(i + 1, len(route.edges) + 1):
            bans = hard.banned_edges(GRID, route, i, j)
            assert bans is not None
            assert np.array_equal(bans, expected_bans(GRID, route, i, j)), (i, j)
    # inside Row 0's stretch only Row 0 may replace it: every other edge is banned
    inside = hard.banned_edges(GRID, route, 2, 3)
    assert inside is not None
    assert (
        np.flatnonzero(~inside).tolist()
        == np.flatnonzero(GRID.edge_identity == ident(GRID, "Row 0")).tolist()
    )
    # a fresh array each time: the caller may change it
    first = hard.banned_edges(GRID, route, 5, 6)
    assert first is not None
    first[:] = True
    again = hard.banned_edges(GRID, route, 5, 6)
    assert again is not None and np.array_equal(again, expected_bans(GRID, route, 5, 6))


def test_the_bans_follow_the_route_after_a_splice() -> None:
    hard, route = HardMode(GRID), route_on(GRID, SNAKE)
    before = hard.banned_edges(GRID, route, 12, 13)
    assert before is not None
    detour = path(GRID, [21, 27, 28, 22])
    after = route.splice(GRID, 12, 13, *detour, score=0.0, metrics=Metrics(0.0, 0.0))
    for i, j in ((0, 1), (3, 5), (12, 15)):
        bans = hard.banned_edges(GRID, after, i, j)
        assert bans is not None and np.array_equal(bans, expected_bans(GRID, after, i, j))
    row4 = edge_id(GRID, 27, 28)
    assert not before[row4] and after.edges.tolist().count(row4) == 1


def test_hard_mode_has_no_terms() -> None:
    hard, route = HardMode(GRID), route_on(GRID, SNAKE)
    assert hard.evaluate(route.nodes, route.edges)[0] == 0.0
    assert hard.delta(route, 12, 13, *path(GRID, [21, 27, 28, 22])) == 0.0
    assert not hard.has_pair and not hard.has_global
    assert hard.banned_turn_classes == hard.avoided_turn_classes == 0


@pytest.mark.parametrize(("i", "j"), [(3, 6), (5, 8)])
def test_local_exhaustive_gives_the_best_path_hard_mode_allows(i: int, j: int) -> None:
    # on a staircase, the longest path of these windows in the ball comes back to a road;
    # find_detour skips it for the longest that does not
    g = lattice(8, 8, named=True)
    route = route_on(g, [0, 8, 9, 17, 18, 26, 27, 35, 36, 44])
    distance = Distance(g, baseline=Metrics(1000.0, 90.0), r_max=5000.0)
    objective = Composite(distance, [HardMode(g)])
    backend, spatial = PythonBackend(g), SpatialIndex(g)
    forbid, banned = route.release(g, i, j)
    bans = objective.banned_edges(g, route, i, j)
    assert bans is not None
    banned |= bans
    everywhere = np.ones(g.n, dtype=bool)
    paths = local_exhaustive_paths(
        backend, spatial, g, int(route.nodes[i]), int(route.nodes[j]),
        forbid=forbid, banned=banned, node_mask=everywhere, weights=g.edge_length_m.astype(float),
        entry_edge=int(route.edges[i - 1]), exit_edge=int(route.edges[j]),
    )  # fmt: skip
    ranked = sorted(paths, key=lambda p: -distance.delta(route, i, j, *p))
    allowed = [p for p in ranked if objective.feasible_window(g, route, i, j, *p)]
    assert allowed and not objective.feasible_window(g, route, i, j, *ranked[0])
    found = find_detour(
        backend, spatial, g, route, i, j, forbid=forbid, banned=banned, node_mask=everywhere,
        radius=300.0, objective=objective, rng=np.random.Generator(np.random.PCG64(0)),
        entry_edge=int(route.edges[i - 1]), exit_edge=int(route.edges[j]),
        strategy="local_exhaustive",
    )  # fmt: skip
    assert found is not None
    gain = distance.delta(route, i, j, *found)
    assert gain == distance.delta(route, i, j, *allowed[0]) > 0
    assert gain < distance.delta(route, i, j, *ranked[0])
    assert objective.feasible_window(g, route, i, j, *found)


def test_a_window_inside_a_stretch_is_widened_to_its_ends_never_past_a_stop() -> None:
    hard, route = HardMode(GRID), route_on(GRID, SNAKE)
    assert hard.window(route, 2, 3) == hard.window(route, 1, 2) == (0, 5)  # all of Row 0
    for drawn in ((4, 6), (6, 7), (7, 8), (11, 13)):  # different roads either side
        assert hard.window(route, *drawn) == drawn
    via = route_on(GRID, SNAKE, stop_idx=(0, 3, 13))  # a via at node 3, on Row 0
    assert hard.window(via, 1, 2) == (0, 3) and hard.window(via, 3, 4) == (3, 5)
    distance = Distance(GRID, baseline=Metrics(1000.0, 90.0), r_max=5000.0)
    assert distance.window(route, 2, 3) == (2, 3)
    assert Composite(distance, [HardMode(GRID)]).window(route, 2, 3) == (0, 5)


HARD = Constraints(hard=True)
# Row 0 of a 3 × 5 lattice with its middle road named Bridge St: the shortest way along it
# leaves Row 0 for the bridge and comes back
BRIDGED = lattice(3, 5, named=True, roads={(2, 3): {"name": "Bridge St"}})


def main_street() -> GraphData:
    """One way east along Main St, Bridge St in its middle and Main St again, nothing else."""
    xy = [(X0 + 100 * k, Y0) for k in range(5)]
    names = ["Main St", "Main St", "Bridge St", "Main St"]
    return make_graph(xy, [Edge(k, k + 1, 100.0, name=n) for k, n in enumerate(names)])


def test_the_request_asks_for_hard_mode_on_its_own() -> None:
    assert HARD.hard and HARD.kinds == [] and HARD.banned_turn_classes == 0
    assert HARD.note == "under hard mode"
    both = Constraints(no_left=True, strict=True, hard=True)
    assert both.note == "under hard mode and the strict no-left constraint"
    assert Constraints.model_validate(HARD.model_dump(mode="json")) == HARD
    assert HARD.model_dump(mode="json") == {
        "no_left": False,
        "no_right": False,
        "strict": False,
        "hard": True,
    }
    # without it, a request's JSON reads as it did before hard mode existed
    assert Constraints(no_left=True).model_dump(mode="json") == {
        "no_left": True,
        "no_right": False,
        "strict": False,
    }
    request = RouteRequest(stops=[(45.5, -122.6), (45.6, -122.7)], seed=1)
    assert "constraints" not in request.model_dump(mode="json")
    with pytest.raises(ValidationError, match="strict needs no_left or no_right"):
        Constraints(hard=True, strict=True)
    parts = HARD.objectives(GRID, 1000.0)
    assert [type(p) for p in parts] == [HardMode]
    soft = Constraints(no_left=True, hard=True).objectives(GRID, 1000.0)
    assert [type(p) for p in soft] == [NoLeft, HardMode]
    distance = Distance(GRID, baseline=Metrics(1000.0, 90.0), r_max=5000.0)
    made = constrain(distance, HARD, GRID)
    assert isinstance(made, Composite) and isinstance(made.constraints[0], HardMode)


def test_a_seed_leg_that_comes_back_to_its_road_is_searched_again() -> None:
    plain = seeded(BRIDGED, [0, 4], NO_CONSTRAINTS)
    assert plain.nodes.tolist() == [0, 1, 2, 3, 4]
    assert returning(BRIDGED, plain.edges) == ((0, 2), (3, 4))
    # Row 0 banned but for its stretch before the bridge: over the bridge, then Col 3 and Row 1
    hard = seeded(BRIDGED, [0, 4], HARD)
    assert hard.nodes.tolist() == [0, 1, 2, 3, 8, 9, 4]
    assert one_stretch_each(BRIDGED, hard.edges)


def test_a_road_coming_back_after_a_via_moves_the_earlier_leg_when_the_later_cannot() -> None:
    # 0 → 3 → 2 on a 5 × 5 lattice: the plain seed leaves on Row 0, comes back to the via at 3
    # and on along Row 0; the leg 3 → 2 has no other way, so the leg 0 → 3 drops Row 0
    g = lattice(5, 5, named=True)
    plain = seeded(g, [0, 3, 2], NO_CONSTRAINTS)
    assert plain.nodes.tolist() == [0, 1, 6, 7, 8, 3, 2]
    assert returning(g, plain.edges) == ((0, 1), (5, 6))
    hard = seeded(g, [0, 3, 2], HARD)
    assert hard.nodes.tolist() == [0, 5, 6, 7, 8, 3, 2] and hard.stop_idx == (0, 5, 6)
    assert one_stretch_each(g, hard.edges)


def test_the_seed_tries_once_more_with_only_the_last_return_s_ban() -> None:
    # a 5 × 5 lattice whose rows are Row 0 and Row 1 by turns, its columns Col 0 and Col 1, two
    # roads unnamed: from 24 to 1 the rounds pile up bans (Col 0, Row 0 but for 14 → 13, Col 1)
    # until no way is left, though [24, 19, 14, 13, 12, 11, 6, 1] is legal; the retry, with only
    # the last return's ban, finds a legal seed and the end stays on node 1
    roads: dict[tuple[int, int], dict[str, str | None]] = {}
    for r in range(5):
        for c in range(4):
            roads[(r * 5 + c, r * 5 + c + 1)] = {"name": f"Row {r % 2}"}
    for c in range(5):
        for r in range(4):
            roads[(r * 5 + c, (r + 1) * 5 + c)] = {"name": f"Col {c % 2}"}
    roads[(14, 19)] = roads[(21, 22)] = {"name": None}
    g = lattice(5, 5, named=True, roads=roads)
    found = seeded(g, [24, 1], HARD)
    assert found.nodes.tolist() == [24, 19, 18, 13, 8, 3, 2, 1]
    assert one_stretch_each(g, found.edges)
    request = RouteRequest(
        stops=[at_node(g, 24), at_node(g, 1)], seed=1, max_iterations=0, constraints=HARD
    )
    planned = plan_route(g, request)
    assert [(s.node, s.note) for s in planned.snaps] == [(24, None), (1, None)]


def test_a_round_trip_s_loop_never_comes_back_to_a_road() -> None:
    g = lattice(8, 8, named=True)
    for seed in range(5):
        found = seeded(g, [27, 27], HARD, seed=seed)
        assert found.nodes[0] == found.nodes[-1] == 27
        assert one_stretch_each(g, found.edges)


def test_what_an_unreachable_leg_blames() -> None:
    # Main St, Bridge St, Main St: no way without coming back to Main St, a way without hard
    # mode; straight on, so the strict no-left constraint alone plans it
    main = main_street()
    assert seeded(main, [0, 4], NO_CONSTRAINTS).nodes.tolist() == [0, 1, 2, 3, 4]
    for constraints in (HARD, Constraints(no_left=True, strict=True, hard=True)):
        with pytest.raises(InfeasibleHardMode) as refused:
            seeded(main, [0, 4], constraints)
        assert str(refused.value) == "the seed found no way from stop 0 to stop 1 under hard mode"
    # a left turn the only way, no names: hard mode alone plans, the strict constraint is why
    with pytest.raises(InfeasibleTurnConstraint) as turned:
        seeded(left_only(), [0, 14], Constraints(no_left=True, strict=True, hard=True))
    assert str(turned.value).endswith(", under the strict no-left constraint")
    # no route at all: nothing to blame
    for constraints in (HARD, Constraints(no_left=True, strict=True, hard=True)):
        with pytest.raises(StopUnreachable) as plain:
            seeded(two_rings(), [0, 12], constraints)
        assert type(plain.value) is StopUnreachable


def test_a_stop_moved_for_hard_mode_says_so() -> None:
    # the end on Main St's last node is out of reach; the fallback moves it back along the
    # street, to the bridge's far end, which the route reaches without coming back
    main = main_street()
    request = RouteRequest(stops=[at_node(main, 0), at_node(main, 4)], seed=1, constraints=HARD)
    spatial = SpatialIndex(main)
    snaps = resolve_stops(
        main, spatial, PythonBackend(main), request, request_masks(spatial, main, request)
    )
    assert snaps[1].node == 3
    assert snaps[1].note == "moved 100 m: the seed found no way to nearer nodes under hard mode"


def test_a_base_route_that_comes_back_to_a_road_is_refused_under_hard_mode() -> None:
    plan = plan_route(
        BRIDGED,
        RouteRequest(stops=[at_node(BRIDGED, 0), at_node(BRIDGED, 4)], seed=1, max_iterations=0),
    )
    base = plan.route
    assert returning(BRIDGED, base.edges) is not None  # the seed itself, along Row 0
    stops = [int(base.nodes[0]), int(base.nodes[-1])]
    check_base(base, plan.masks, stops, data=BRIDGED, constraints=NO_CONSTRAINTS)
    with pytest.raises(BaseRouteMismatch, match="comes back to Row 0 at edge 3") as refused:
        check_base(base, plan.masks, stops, data=BRIDGED, constraints=HARD)
    assert refused.value.field == "route"
    hard = plan_route(
        BRIDGED,
        RouteRequest(
            stops=[at_node(BRIDGED, 0), at_node(BRIDGED, 4)],
            seed=1,
            max_iterations=0,
            constraints=HARD,
        ),
    )
    check_base(hard.route, hard.masks, stops, data=BRIDGED, constraints=HARD)


@pytest.mark.parametrize("constraints", [HARD, Constraints(no_left=True, hard=True)])
def test_the_loop_under_hard_mode_keeps_every_road_one_stretch_on_both_backends(
    constraints: Constraints,
) -> None:
    g = lattice(10, 10, named=True)
    request = RouteRequest(
        stops=[at_node(g, 22), at_node(g, 45), at_node(g, 77)],
        seed=3,
        max_iterations=1500,
        budget_s=120,
        constraints=constraints,
    )
    one, two = (
        plan_route(g, request, backend=b(g), check_delta=True) for b in (PythonBackend, CsrBackend)
    )
    assert one.route.edges.tobytes() == two.route.edges.tobytes()
    assert (one.run.attempts, one.run.accepted) == (two.run.attempts, two.run.accepted)
    assert one.run.accepted > 5
    assert one.route.metrics.length_m > 2 * one.baseline.length_m
    route = Route.new(
        g, *_seed_of(g, request), score=0.0, metrics=Metrics(0.0, 0.0), seed=request.seed
    )
    assert one_stretch_each(g, route.edges)
    for event in one.run.events:  # every route along the way, not only the last
        s = event.splice
        route = route.splice(
            g, s.i, s.j, s.nodes, s.edges, score=event.score, metrics=event.metrics
        )
        assert one_stretch_each(g, route.edges)
    assert route.edges.tobytes() == one.route.edges.tobytes()  # max: the best is the last


def _seed_of(g: GraphData, request: RouteRequest) -> tuple[np.ndarray, np.ndarray, tuple[int, ...]]:
    spatial = SpatialIndex(g)
    masks = request_masks(spatial, g, request)
    backend = PythonBackend(g)
    snaps = resolve_stops(g, spatial, backend, request, masks)
    rng = np.random.Generator(np.random.PCG64(request.seed))
    r_max = min(masks.leash_m, masks.extent_m)
    found = seed_route(
        backend, spatial, g, snaps, masks.active, masks.banned, rng, r_max=r_max,
        constraints=request.constraints,
    )  # fmt: skip
    return found.nodes, found.edges, found.stop_idx
