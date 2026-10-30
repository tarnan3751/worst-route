from dataclasses import replace

import numpy as np
import pytest

from worst_route.engine.route import (
    BrokenChain,
    EdgeBanned,
    EndpointMismatch,
    Metrics,
    NodeReused,
    RoundTripStartReused,
    Route,
    StopInterior,
    TransitionForbidden,
    check_route,
    twins_of,
)
from worst_route.graph.data import GraphData
from worst_route.graph.testing import edge_id, make_graph

from .graphs import NO_METRICS, edges_of, lattice, route_on

# A two-way 3 × 3 lattice: node r * 3 + c, row 0 the southmost.
#   6 - 7 - 8
#   3 - 4 - 5
#   0 - 1 - 2
GRID = lattice(3, 3)
NEW = Metrics(length_m=1.0, time_s=2.0)


def splice(route: Route, data: GraphData, i: int, j: int, nodes: list[int]) -> Route:
    path = np.array(nodes, dtype=np.int32)
    return route.splice(data, i, j, path, edges_of(data, nodes), score=7.0, metrics=NEW)


def test_a_legal_detour_is_spliced_in() -> None:
    route = route_on(GRID, [0, 1, 2, 5, 8], stop_idx=(0, 2, 4))
    after = splice(route, GRID, 0, 2, [0, 3, 4, 1, 2])
    assert after.nodes.tolist() == [0, 3, 4, 1, 2, 5, 8]
    assert after.edges.tolist() == edges_of(GRID, [0, 3, 4, 1, 2, 5, 8]).tolist()
    assert after.stop_idx == (0, 4, 6)
    assert np.flatnonzero(after.used_nodes).tolist() == [0, 1, 2, 3, 4, 5, 8]
    assert np.flatnonzero(after.banned_edges).tolist() == sorted(after.edges.tolist())
    assert (after.score, after.metrics, after.seed) == (7.0, NEW, route.seed)
    assert after.graph_version == GRID.meta["graph_version"]
    assert route.nodes.tolist() == [0, 1, 2, 5, 8]  # the original is untouched
    for a in (after.nodes, after.edges, after.used_nodes, after.banned_edges):
        assert not a.flags.writeable


def test_the_released_interior_may_be_reused() -> None:
    route = route_on(GRID, [0, 1, 2])
    after = splice(route, GRID, 0, 2, [0, 3, 4, 1, 2])
    assert after.nodes.tolist() == [0, 3, 4, 1, 2]


def test_endpoint_mismatch() -> None:
    route = route_on(GRID, [0, 1, 2])
    with pytest.raises(EndpointMismatch):
        splice(route, GRID, 0, 1, [3, 4, 1])
    with pytest.raises(EndpointMismatch):
        splice(route, GRID, 0, 1, [0, 3, 4])
    with pytest.raises(EndpointMismatch):
        route.splice(GRID, 0, 1, np.array([0]), np.array([], dtype=np.int32), score=0, metrics=NEW)


def test_broken_chain() -> None:
    route = route_on(GRID, [0, 1, 2])
    nodes = np.array([0, 3, 4, 1], dtype=np.int32)
    wrong = edges_of(GRID, [0, 3, 6, 7])
    with pytest.raises(BrokenChain) as caught:
        route.splice(GRID, 0, 1, nodes, wrong, score=0, metrics=NEW)
    assert caught.value.index == 1
    with pytest.raises(BrokenChain):
        route.splice(GRID, 0, 1, nodes, edges_of(GRID, [0, 3, 4]), score=0, metrics=NEW)


def test_broken_chain_on_a_self_loop() -> None:
    g = make_graph(
        [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)], [(0, 1, 100.0), (1, 1, 5.0), (1, 2, 100.0)]
    )
    route = route_on(g, [0, 1, 2])
    loop = edge_id(g, 1, 1)
    edges = np.array([loop, edge_id(g, 1, 2)], dtype=np.int32)
    with pytest.raises(BrokenChain, match="self-loop"):
        route.splice(g, 1, 2, np.array([1, 1, 2]), edges, score=0, metrics=NEW)


def test_node_reused() -> None:
    route = route_on(GRID, [0, 1, 2, 5])
    with pytest.raises(NodeReused) as caught:  # 5 and 2 are still on the route
        splice(route, GRID, 0, 1, [0, 3, 4, 5, 2, 1])
    assert caught.value.index == 3
    assert not isinstance(caught.value, RoundTripStartReused)


def test_node_reused_inside_the_detour() -> None:
    g = lattice(3, 4)  # node r * 4 + c
    route = route_on(g, [0, 1])
    with pytest.raises(NodeReused) as caught:  # enters 4 twice
        splice(route, g, 0, 1, [0, 4, 5, 9, 8, 4, 5, 1])
    assert caught.value.index == 5


def test_stop_interior() -> None:
    route = route_on(GRID, [0, 1, 2, 5, 8], stop_idx=(0, 2, 4))
    with pytest.raises(StopInterior) as caught:
        splice(route, GRID, 1, 3, [1, 4, 5])
    assert caught.value.index == 2
    after = splice(route, GRID, 2, 4, [2, 5, 4, 7, 8])  # a stop at either end of the run is fine
    assert after.nodes.tolist() == [0, 1, 2, 5, 4, 7, 8]
    assert after.stop_idx == (0, 2, 6)


def carriageways() -> GraphData:
    """A road A → B → C (0 → 1 → 2) whose first edge is twinned with D → E (3 → 4).

    A (0) → B (1) → C (2)
    ↓     ↗  ↘     ↑
    D (3) → E (4) ─┘
    """
    nodes = [(0.0, 100.0), (100.0, 100.0), (200.0, 100.0), (0.0, 0.0), (100.0, 0.0)]
    edges = [(0, 1, 100.0), (1, 2, 100.0), (0, 3, 100.0), (3, 4, 100.0), (4, 1, 141.0)]
    edges += [(1, 3, 141.0), (4, 2, 141.0)]
    return make_graph(nodes, edges, twins=[((0, 1), (3, 4))])


def test_edge_banned_by_a_kept_twin() -> None:
    g = carriageways()
    route = route_on(g, [0, 1, 2])
    with pytest.raises(EdgeBanned) as caught:
        splice(route, g, 1, 2, [1, 3, 4, 2])
    assert caught.value.index == 2


def test_removing_an_edge_releases_its_twin() -> None:
    g = carriageways()
    route = route_on(g, [0, 1, 2])
    after = splice(route, g, 0, 1, [0, 3, 4, 1])
    assert after.nodes.tolist() == [0, 3, 4, 1, 2]
    assert after.banned_edges[edge_id(g, 0, 1)]  # now the twin of a route edge


def test_a_ban_held_by_another_kept_edge_stays() -> None:
    nodes = [(0.0, 100.0), (100.0, 100.0), (200.0, 100.0), (0.0, 0.0), (100.0, 0.0)]
    edges = [(0, 1, 100.0), (1, 2, 100.0), (0, 3, 100.0), (3, 4, 100.0), (4, 1, 141.0)]
    g = make_graph(nodes, edges, twins=[((0, 1), (3, 4)), ((1, 2), (3, 4))])
    route = route_on(g, [0, 1, 2])
    with pytest.raises(EdgeBanned):
        splice(route, g, 0, 1, [0, 3, 4, 1])
    _, banned = route.release(g, 0, 1)
    assert banned[edge_id(g, 3, 4)] and not banned[edge_id(g, 0, 1)]


def test_edge_banned_by_the_caller() -> None:
    route = route_on(GRID, [0, 1, 2])
    extra = np.zeros(GRID.m, dtype=bool)
    extra[edge_id(GRID, 3, 4)] = True
    nodes = [0, 3, 4, 1]
    with pytest.raises(EdgeBanned) as caught:
        route.splice(
            GRID,
            0,
            1,
            np.array(nodes),
            edges_of(GRID, nodes),
            score=0,
            metrics=NEW,
            extra_banned=extra,
        )
    assert caught.value.index == 1


def test_edge_banned_by_a_twin_inside_the_detour() -> None:
    g = make_graph(
        [(0.0, 100.0), (100.0, 100.0), (200.0, 100.0), (0.0, 0.0), (100.0, 0.0)],
        [(0, 1, 100.0), (1, 2, 100.0), (0, 3, 100.0), (3, 4, 100.0), (4, 1, 141.0)],
        twins=[((0, 3), (4, 1))],
    )
    route = route_on(g, [0, 1, 2])
    with pytest.raises(EdgeBanned) as caught:
        splice(route, g, 0, 1, [0, 3, 4, 1])
    assert caught.value.index == 2
    with pytest.raises(EdgeBanned):
        route_on(g, [0, 3, 4, 1, 2])


def test_transition_refused_at_an_inner_and_a_boundary_junction() -> None:
    straight = [((3, 4), (4, 5)), ((5, 4), (4, 3)), ((1, 4), (4, 7)), ((7, 4), (4, 1))]
    g = lattice(3, 3, transitions=straight)
    with pytest.raises(TransitionForbidden) as caught:
        route_on(g, [3, 4, 7])
    assert caught.value.index == 1

    route = route_on(g, [0, 1, 2])
    assert splice(route, g, 0, 2, [0, 3, 4, 5, 2]).nodes.tolist() == [0, 3, 4, 5, 2]
    with pytest.raises(TransitionForbidden) as caught:
        splice(route, g, 0, 2, [0, 3, 4, 1, 2])
    assert caught.value.index == 2

    route = route_on(g, [3, 4, 5])
    with pytest.raises(TransitionForbidden) as caught:  # arrives from 1, leaves on the kept 4 → 5
        splice(route, g, 0, 1, [3, 0, 1, 4])
    assert caught.value.index == 3


def test_round_trip_is_a_route() -> None:
    route = route_on(GRID, [0, 1, 4, 3, 0])
    assert route.is_round_trip and route.stop_idx == (0, 4)
    after = splice(route, GRID, 1, 2, [1, 2, 5, 4])
    assert after.nodes.tolist() == [0, 1, 2, 5, 4, 3, 0]
    whole = splice(route, GRID, 0, 4, [0, 3, 6, 7, 4, 1, 0])
    assert whole.nodes.tolist() == [0, 3, 6, 7, 4, 1, 0]


def test_round_trip_start_reused() -> None:
    route = route_on(GRID, [0, 1, 4, 3, 0])
    with pytest.raises(RoundTripStartReused) as caught:
        splice(route, GRID, 1, 3, [1, 0, 3])
    assert caught.value.index == 2
    assert isinstance(caught.value, NodeReused)
    with pytest.raises(RoundTripStartReused):
        route_on(GRID, [0, 1, 0, 3, 0])


def test_a_two_edge_round_trip_may_not_drive_one_road_twice() -> None:
    assert route_on(GRID, [0, 1, 0]).edges.size == 2  # make_graph gives every edge its own unit
    unit = GRID.edge_unit.copy()
    unit[edge_id(GRID, 1, 0)] = unit[edge_id(GRID, 0, 1)]
    one_road = replace(GRID, edge_unit=unit)
    with pytest.raises(EdgeBanned, match="out and back"):
        route_on(one_road, [0, 1, 0])
    route = route_on(one_road, [0, 1, 4, 3, 0])
    with pytest.raises(EdgeBanned, match="out and back"):
        splice(route, one_road, 0, 4, [0, 1, 0])


def test_release_gives_the_search_masks() -> None:
    route = route_on(GRID, [0, 1, 2, 5, 8])
    forbid, banned = route.release(GRID, 1, 3)
    assert np.flatnonzero(forbid).tolist() == [0, 8]
    kept = [edge_id(GRID, 0, 1), edge_id(GRID, 5, 8)]
    assert np.flatnonzero(banned).tolist() == sorted(kept)
    assert route.used_nodes.sum() == 5  # untouched


SNAKE = [0, 1, 2, 3, 7, 6, 5, 4, 8, 9, 10, 11, 15, 14, 13, 12]


@pytest.mark.parametrize("seed", range(20))
def test_release_matches_a_brute_force_on_random_twins(seed: int) -> None:
    rng = np.random.default_rng(seed)
    plain = lattice(4, 4)
    snake = edges_of(plain, SNAKE)
    off = np.setdiff1d(np.arange(plain.m), snake)
    # each of 8 edges off the snake twinned with 2 snake edges: releasing one keeps the ban
    pairs = [
        (a, b)
        for a in rng.choice(off, 8, replace=False)
        for b in rng.choice(snake, 2, replace=False)
    ]
    u, v = plain.edge_u, plain.edge_v
    named = [((int(u[a]), int(v[a])), (int(u[b]), int(v[b]))) for a, b in pairs]
    g = lattice(4, 4, twins=named)
    route = route_on(g, SNAKE)
    count = np.zeros(g.m, dtype=np.int32)  # each route edge bans itself and its twins once
    for e in route.edges.tolist():
        count[[e, *g.twin_edges[g.twin_indptr[e] : g.twin_indptr[e + 1]].tolist()]] += 1
    assert np.array_equal(route.ban_count, count)
    assert np.array_equal(route.banned_edges, count > 0)
    for _ in range(10):
        i = int(rng.integers(len(route.edges)))
        j = int(rng.integers(i + 1, len(route.edges) + 1))
        forbid, banned = route.release(g, i, j)
        kept = np.concatenate((route.edges[:i], route.edges[j:])).astype(np.int32)
        expected = np.zeros(g.m, dtype=bool)
        expected[kept] = True
        expected[twins_of(g, kept)[1]] = True
        assert np.array_equal(banned, expected)
        free = np.zeros(g.n, dtype=bool)
        free[np.concatenate((route.nodes[:i], route.nodes[j + 1 :]))] = True
        assert np.array_equal(forbid, free)


def test_check_route_and_new_refuse_what_splice_refuses() -> None:
    with pytest.raises(NodeReused) as caught:
        route_on(GRID, [3, 4, 1, 0, 3, 4])
    assert caught.value.index == 4
    nodes = np.array([0, 1, 2], dtype=np.int32)
    with pytest.raises(BrokenChain) as caught_chain:
        wrong = np.array([edge_id(GRID, 0, 1), edge_id(GRID, 4, 5)], dtype=np.int32)
        check_route(GRID, nodes, wrong, (0, 2))
    assert caught_chain.value.index == 1
    with pytest.raises(ValueError, match="stop_idx"):
        check_route(GRID, nodes, edges_of(GRID, [0, 1, 2]), (0, 1))


def test_bad_windows_and_builds_are_errors() -> None:
    route = route_on(GRID, [0, 1, 2])
    for i, j in [(1, 1), (2, 1), (-1, 1), (0, 3)]:
        with pytest.raises(ValueError, match="window"):
            route.release(GRID, i, j)
    other = lattice(3, 3, spacing_m=90.0)
    with pytest.raises(ValueError, match="another build"):
        route.release(other, 0, 1)


def test_new_takes_score_metrics_and_seed() -> None:
    nodes = np.array([0, 1, 2])
    route = Route.new(
        GRID, nodes, edges_of(GRID, [0, 1, 2]), (0, 2), score=3.5, metrics=NEW, seed=9
    )
    assert (route.score, route.metrics, route.seed) == (3.5, NEW, 9)
    assert route.nodes.dtype == np.int32 and route.edges.dtype == np.int32
    assert NO_METRICS.counters == {}
