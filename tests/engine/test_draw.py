"""A route drawn by hand (`engine/draw.py`): every refusal at its edge, the earliest of two
refused with the path before it legal, the next moves against the check on random walks, and a
finished drawing as a validated route document."""

from collections.abc import Sequence

import numpy as np
import pytest

from worst_route.engine.draw import (
    DrawingUnfinished,
    DrawRefused,
    DrawRules,
    check_drawing,
    drawing_problem,
    finish_drawing,
    next_moves,
)
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.seed_route import baseline_route, resolve_stops
from worst_route.graph.backend import TURN_LEFT, TURN_RIGHT, TURN_U
from worst_route.graph.backends import make_backend
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import edge_id

from .graphs import at_node, board

BOARD = board()


def rules(
    g: GraphData,
    stops: Sequence[int],
    *,
    outside: Sequence[int] = (),
    banned: Sequence[tuple[int, int]] = (),
    classes: int = 0,
    hard: bool = False,
) -> DrawRules:
    mask = np.ones(g.n, dtype=np.bool_)
    mask[list(outside)] = False
    bans = np.zeros(g.m, dtype=np.bool_)
    for u, v in banned:
        bans[edge_id(g, u, v)] = True
    return DrawRules(tuple(stops), mask, bans, classes, hard)


def path(nodes: Sequence[int], g: GraphData = BOARD) -> list[int]:
    return [edge_id(g, u, v) for u, v in zip(nodes[:-1], nodes[1:], strict=True)]


def refusal(r: DrawRules, edges: Sequence[int], g: GraphData = BOARD) -> tuple[str, int]:
    with pytest.raises(DrawRefused) as caught:
        check_drawing(g, r, edges)
    return caught.value.code, caught.value.index


def legal(g: GraphData, r: DrawRules, edges: Sequence[int]) -> bool:
    try:
        check_drawing(g, r, edges)
    except DrawRefused:
        return False
    return True


def test_an_empty_path_is_at_the_start_with_its_legal_ways_out() -> None:
    r = rules(BOARD, [6, 18], outside=[11], banned=[(6, 1)])
    drawing = check_drawing(BOARD, r, [])
    assert (drawing.at, drawing.reached, drawing.done) == (6, (0,), False)
    # 6 → 5 does not exist (row 1 runs east), 6 → 1 is banned, 11 is outside the area
    assert [int(BOARD.edge_v[e]) for e in next_moves(BOARD, r, drawing)] == [7]


def test_a_path_reaches_its_stops_in_order_and_ends_there() -> None:
    r = rules(BOARD, [0, 2, 22])
    drawing = check_drawing(BOARD, r, path([0, 1, 2, 7, 12, 17, 22]))
    assert drawing.reached == (0, 2, 6)
    assert drawing.done and drawing.at == 22
    assert len(next_moves(BOARD, r, drawing)) == 0
    halfway = check_drawing(BOARD, r, path([0, 1, 2, 7]))
    assert (halfway.reached, halfway.done) == ((0, 2), False)


def test_each_refusal_names_the_first_illegal_edge() -> None:
    plain = rules(BOARD, [0, 24])
    assert refusal(plain, path([0, 5, 6, 1, 0])) == ("NODE_REUSED", 3)  # the start again
    assert refusal(plain, path([0, 1, 6, 11, 10, 5, 6])) == ("NODE_REUSED", 5)
    # node 12 lets 7 → 12 go on to 17 only
    assert refusal(plain, path([0, 1, 2, 7, 12, 11])) == ("TRANSITION_FORBIDDEN", 4)
    # 12 → 11 driven, then 16 → 17, its twin
    assert refusal(rules(BOARD, [13, 24]), path([13, 12, 11, 16, 17])) == ("EDGE_BANNED", 3)
    assert refusal(rules(BOARD, [0, 24], banned=[(1, 2)]), path([0, 1, 2])) == ("EDGE_BANNED", 1)
    assert refusal(rules(BOARD, [0, 0]), path([0, 1, 0])) == ("EDGE_BANNED", 1)  # out and back
    assert refusal(rules(BOARD, [0, 2, 0]), path([0, 5, 6, 1, 0])) == (
        "ROUND_TRIP_START_REUSED",
        3,
    )
    assert refusal(rules(BOARD, [0, 4, 2]), path([0, 1, 2])) == ("STOP_SKIPPED", 1)
    assert refusal(rules(BOARD, [0, 2]), path([0, 1, 2, 7])) == ("PAST_END", 2)
    assert refusal(rules(BOARD, [0, 24], outside=[2]), path([0, 1, 2])) == ("OUTSIDE_AREA", 1)
    strict = rules(BOARD, [0, 24], classes=TURN_LEFT | TURN_U)
    assert refusal(strict, path([0, 1, 6])) == ("TURN_REFUSED", 1)  # east, then north: a left
    hard = rules(BOARD, [0, 24], hard=True)
    assert refusal(hard, path([0, 1, 6, 7, 2, 3])) == ("ROAD_AGAIN", 4)  # Row 0 again


def test_an_edge_that_does_not_leave_the_node_is_a_broken_chain() -> None:
    r = rules(BOARD, [5, 24])
    # at 7, the one-way 6 → 7 taken back toward 6: not an edge leaving 7
    assert refusal(r, path([5, 6, 7]) + path([6, 7])) == ("BROKEN_CHAIN", 2)
    assert refusal(r, path([5, 6]) + [BOARD.m]) == ("BROKEN_CHAIN", 1)
    assert refusal(r, [-1]) == ("BROKEN_CHAIN", 0)
    assert refusal(r, path([0, 1])) == ("BROKEN_CHAIN", 0)  # not from the start


def test_of_two_refusals_the_earlier_is_reported_and_the_path_before_it_is_legal() -> None:
    r = rules(BOARD, [0, 24])
    # a turn node 12 forbids at edge 4, then node 1 again at edge 6: the route check finds the
    # node first, its rule being checked before the turns'
    edges = path([0, 1, 2, 7, 12, 11, 6, 1])
    assert refusal(r, edges) == ("TRANSITION_FORBIDDEN", 4)
    assert legal(BOARD, r, edges[:4])
    # outside the area at edge 1, then the end reached before the via at edge 3
    edges = path([0, 1, 2, 3, 4])
    assert refusal(rules(BOARD, [0, 9, 4], outside=[1]), edges) == ("OUTSIDE_AREA", 0)
    assert refusal(rules(BOARD, [0, 9, 3], outside=[2]), edges) == ("OUTSIDE_AREA", 1)
    assert legal(BOARD, rules(BOARD, [0, 9, 3], outside=[2]), edges[:1])


def test_a_round_trip_closes_at_its_start_once_its_stops_are_reached() -> None:
    loop = rules(BOARD, [0, 0])
    drawing = check_drawing(BOARD, loop, path([0, 5, 6, 1]))
    assert 0 in BOARD.edge_v[next_moves(BOARD, loop, drawing)]
    closed = check_drawing(BOARD, loop, path([0, 5, 6, 1, 0]))
    assert closed.done and closed.reached == (0, 4)
    via = rules(BOARD, [0, 7, 0])
    early = check_drawing(BOARD, via, path([0, 5, 6, 1]))
    assert 0 not in BOARD.edge_v[next_moves(BOARD, via, early)]
    # two edges out and back along one road are refused, a road of its own back is not
    first = check_drawing(BOARD, loop, path([0, 1]))
    assert 0 not in BOARD.edge_v[next_moves(BOARD, loop, first)]
    g = board(parallel=True)
    bypass = int(np.flatnonzero((g.edge_u == 0) & (g.edge_length_m == 140.0))[0])  # to 1
    back = check_drawing(g, rules(g, [0, 0]), [bypass])
    assert 0 in g.edge_v[next_moves(g, rules(g, [0, 0]), back)]


def test_the_next_moves_are_exactly_the_edges_the_check_takes_on_random_walks() -> None:
    rng = np.random.Generator(np.random.PCG64(2026))
    g = board(parallel=True)
    classes = (0, TURN_LEFT | TURN_U, TURN_RIGHT | TURN_U)
    checked, codes = 0, set()
    for _ in range(150):
        nodes = [int(v) for v in rng.choice(g.n, size=3, replace=False)]
        stops = {0: nodes[:2], 1: nodes, 2: [nodes[0], nodes[1], nodes[0]], 3: [nodes[0]] * 2}
        others = [v for v in range(g.n) if v not in nodes]
        r = rules(
            g,
            stops[int(rng.integers(4))],
            outside=[int(v) for v in rng.choice(others, size=2, replace=False)],
            classes=int(rng.choice(classes)),
            hard=bool(rng.integers(2)),
        )
        r.banned_edges[rng.choice(g.m, size=3, replace=False)] = True
        edges: list[int] = []
        drawing = check_drawing(g, r, edges)
        while True:
            moves = next_moves(g, r, drawing)
            at = drawing.at
            for e in g.out_edges[g.out_indptr[at] : g.out_indptr[at + 1]].tolist():
                checked += 1
                taken = legal(g, r, [*edges, e])
                assert (e in moves) == taken, (r, edges, e)
                if not taken:
                    code, index = refusal(r, [*edges, e], g)
                    assert index == len(edges)
                    codes.add(code)
            if len(moves) == 0:
                break
            edges.append(int(rng.choice(moves)))
            drawing = check_drawing(g, r, edges)
    assert checked > 3000
    assert codes == {
        "NODE_REUSED",
        "ROUND_TRIP_START_REUSED",
        "EDGE_BANNED",
        "TRANSITION_FORBIDDEN",
        "OUTSIDE_AREA",
        "STOP_SKIPPED",
        "PAST_END",
        "TURN_REFUSED",
        "ROAD_AGAIN",
    }


def test_any_walk_is_refused_at_an_edge_whose_path_before_is_legal() -> None:
    rng = np.random.Generator(np.random.PCG64(7))
    g = board(parallel=True)
    for _ in range(300):
        start, end, via = (int(v) for v in rng.choice(g.n, size=3, replace=False))
        r = rules(g, [start, via, end], hard=bool(rng.integers(2)), classes=TURN_LEFT | TURN_U)
        at, edges = start, []
        for _ in range(int(rng.integers(1, 30))):  # wherever the graph goes, rules or not
            out = g.out_edges[g.out_indptr[at] : g.out_indptr[at + 1]]
            edges.append(int(rng.choice(out)))
            at = int(g.edge_v[edges[-1]])
        try:
            check_drawing(g, r, edges)
        except DrawRefused as error:
            assert legal(g, r, edges[: error.index])
            assert refusal(r, edges[: error.index + 1], g) == (error.code, error.index)


def planned(stops: Sequence[int], **fields: object) -> tuple[RouteRequest, DrawRules, dict]:
    """A request on BOARD from node to node, its rules, and what finishing needs."""
    request = RouteRequest(stops=[at_node(BOARD, v) for v in stops], seed=5, **fields)
    spatial, backend = SpatialIndex(BOARD), make_backend(BOARD, "python")
    masks = request_masks(spatial, BOARD, request)
    snaps = resolve_stops(BOARD, spatial, backend, request, masks)
    nodes = [s.node for s in snaps]
    baseline = baseline_route(backend, BOARD, nodes, masks.active, masks.banned)
    r = DrawRules.of(masks, nodes, request.constraints)
    parts = {"snaps": snaps, "baseline": baseline, "r_max": min(masks.leash_m, masks.extent_m)}
    return request, r, parts


def test_a_finished_drawing_is_a_validated_route_drawn_by_a_human() -> None:
    request, r, parts = planned([0, 24])
    assert r.stop_nodes == (0, 24)
    drawn = path([0, 1, 6, 11, 10, 15, 16, 17, 18, 19, 24])
    doc = finish_drawing(BOARD, request, r, path=drawn, **parts)
    assert doc["edges"] == drawn and doc["stop_idx"] == [0, 10]
    assert doc["run"] == {
        "attempts": 10,
        "accepted": 10,
        "stopped_because": "drawn",
        "elapsed_s": 0.0,
        "backend": "human",
    }
    assert doc["metrics"]["length_m"] == pytest.approx(1000.0)
    assert doc["metrics"]["ratio"] == pytest.approx(1000.0 / 800.0)
    assert doc["request"]["objective"] == "distance" and doc["seed"] == 5
    assert "target" not in doc["metrics"]


def test_a_round_trip_drawn_has_no_ratio() -> None:
    request, r, parts = planned([0, 0])
    doc = finish_drawing(BOARD, request, r, path=path([0, 5, 6, 1, 0]), **parts)
    assert doc["stop_idx"] == [0, 4] and doc["metrics"]["ratio"] is None


def test_an_unfinished_drawing_is_not_a_route() -> None:
    request, r, parts = planned([0, 24])
    with pytest.raises(DrawingUnfinished) as caught:
        finish_drawing(BOARD, request, r, path=path([0, 1, 2]), **parts)
    assert caught.value.reached == 1
    with pytest.raises(DrawRefused):
        finish_drawing(BOARD, request, r, path=path([0, 1, 0]), **parts)


def test_a_drawing_is_the_longest_route_with_no_target() -> None:
    assert drawing_problem("distance", "max") is None
    assert drawing_problem("time", "max") == (
        "objective",
        "a drawing is the longest route: objective distance only",
    )
    assert drawing_problem("distance", "ratio") == (
        "target",
        "a drawing takes no target: it is as bad as you make it",
    )
