import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
import pytest

from worst_route.engine.alphabet_search import alphabet_counts
from worst_route.engine.objectives.collect import count_items
from worst_route.engine.route import Metrics
from worst_route.engine.validator import NAMES, ValidationFailed, ValidationReport, validate_route
from worst_route.graph.backend import TURN_LEFT, TURN_RIGHT, TURN_U
from worst_route.graph.data import GraphData
from worst_route.graph.testing import edge_id, make_graph

from .graphs import edges_of, lattice, named_roads, route_on

# A two-way 3 × 3 lattice of 100 m streets: node r * 3 + c, row 0 the southmost.
#   6 - 7 - 8
#   3 - 4 - 5
#   0 - 1 - 2
GRID = lattice(3, 3)
ALL_NODES = np.ones(GRID.n, dtype=bool)
NO_BANS = np.zeros(GRID.m, dtype=bool)


@dataclass
class Drawn:
    """A route as the validator reads it, legal or not."""

    nodes: np.ndarray
    edges: np.ndarray
    stop_idx: tuple[int, ...]
    metrics: Metrics


def drawn(data: GraphData, nodes: Sequence[int], stop_idx: Sequence[int] | None = None) -> Drawn:
    edges = edges_of(data, nodes)
    length = float(data.edge_length_m[edges].astype(np.float64).sum())
    time = float(data.edge_time_s[edges].astype(np.float64).sum())
    stops = tuple(stop_idx) if stop_idx is not None else (0, len(nodes) - 1)
    return Drawn(np.array(nodes, dtype=np.int32), edges, stops, Metrics(length, time))


def check(
    route: Drawn,
    data: GraphData = GRID,
    *,
    stop_nodes: Sequence[int] | None = None,
    node_mask: np.ndarray | None = None,
    banned: np.ndarray | None = None,
) -> dict[str, bool]:
    stops = stop_nodes if stop_nodes is not None else route.nodes[list(route.stop_idx)].tolist()
    report = validate_route(
        data,
        route,
        stop_nodes=stops,
        node_mask=node_mask if node_mask is not None else np.ones(data.n, dtype=bool),
        banned_edges=banned if banned is not None else np.zeros(data.m, dtype=bool),
    )
    assert [c.name for c in report.checks] == list(NAMES)
    assert report.ok == all(c.ok for c in report.checks)
    return {c.name: c.ok for c in report.checks}


def failed(result: dict[str, bool]) -> list[str]:
    return [name for name, ok in result.items() if not ok]


def test_a_legal_route_passes_every_check() -> None:
    route = drawn(GRID, [0, 1, 4, 3, 6, 7, 8], stop_idx=(0, 2, 6))
    assert failed(check(route)) == []
    report = validate_route(
        GRID, route, stop_nodes=[0, 4, 8], node_mask=ALL_NODES, banned_edges=NO_BANS
    )
    assert report.ok and report.lines()[0] == "chain: ok"
    assert report.as_dict()["ok"] is True


def test_an_engine_route_passes() -> None:
    route = route_on(GRID, [0, 1, 2, 5, 4, 3, 6, 7, 8])
    edges = route.edges
    metrics = Metrics(
        float(GRID.edge_length_m[edges].astype(np.float64).sum()),
        float(GRID.edge_time_s[edges].astype(np.float64).sum()),
    )
    route = replace(route, metrics=metrics)
    report = validate_route(
        GRID, route, stop_nodes=[0, 8], node_mask=ALL_NODES, banned_edges=NO_BANS
    )
    assert report.ok


def test_a_round_trip_passes() -> None:
    assert failed(check(drawn(GRID, [0, 1, 4, 3, 0]))) == []


def test_repeated_node() -> None:
    result = check(drawn(GRID, [0, 1, 4, 3, 0, 1, 2]))
    assert failed(result) == ["vertex_simple", "edge_simple", "osm_ids"]
    report = validate_route(
        GRID,
        drawn(GRID, [0, 1, 4, 3, 0, 1, 2]),
        stop_nodes=[0, 2],
        node_mask=ALL_NODES,
        banned_edges=NO_BANS,
    )
    assert report.check("vertex_simple").index == 4


def test_repeated_edge() -> None:
    route = drawn(GRID, [0, 1, 4, 3, 0, 1, 2])
    report = validate_route(
        GRID, route, stop_nodes=[0, 2], node_mask=ALL_NODES, banned_edges=NO_BANS
    )
    assert not report.check("edge_simple").ok
    assert report.check("edge_simple").index == 4
    assert "driven twice" in report.check("edge_simple").detail


def test_twin_reuse() -> None:
    g = lattice(3, 3, twins=[((0, 1), (7, 6))])
    result = check(drawn(g, [0, 1, 4, 7, 6]), g)
    assert failed(result) == ["edge_simple"]


def test_one_road_out_and_back() -> None:
    unit = GRID.edge_unit.copy()
    unit[edge_id(GRID, 1, 0)] = unit[edge_id(GRID, 0, 1)]
    one_road = replace(GRID, edge_unit=unit)
    assert failed(check(drawn(one_road, [0, 1, 0]), one_road)) == ["edge_simple"]
    assert failed(check(drawn(GRID, [0, 1, 0]))) == []


def test_bad_chain_and_wrong_direction() -> None:
    route = drawn(GRID, [0, 1, 2, 5])
    route.edges = route.edges.copy()
    route.edges[1] = edge_id(GRID, 4, 5)
    assert "chain" in failed(check(route))

    one_way = make_graph([(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)], [(0, 1, 100.0), (2, 1, 100.0)])
    backwards = Drawn(
        np.array([0, 1, 2], dtype=np.int32),
        np.array([edge_id(one_way, 0, 1), edge_id(one_way, 2, 1)], dtype=np.int32),
        (0, 2),
        Metrics(200.0, float(one_way.edge_time_s.astype(np.float64).sum())),
    )
    report = validate_route(
        one_way,
        backwards,
        stop_nodes=[0, 2],
        node_mask=np.ones(3, bool),
        banned_edges=np.zeros(2, bool),
    )
    assert not report.check("chain").ok and report.check("chain").index == 1


def test_invalid_ids_fail_chain_and_skip_the_rest() -> None:
    route = drawn(GRID, [0, 1, 2])
    route.edges = np.array([0, GRID.m + 5], dtype=np.int32)
    report = validate_route(
        GRID, route, stop_nodes=[0, 2], node_mask=ALL_NODES, banned_edges=NO_BANS
    )
    assert not report.ok
    assert report.check("chain").index == 1
    assert all(not c.ok and c.detail.startswith("skipped") for c in report.checks[1:])


def test_forbidden_transition() -> None:
    straight = [((3, 4), (4, 5)), ((5, 4), (4, 3)), ((1, 4), (4, 7)), ((7, 4), (4, 1))]
    g = lattice(3, 3, transitions=straight)
    assert failed(check(drawn(g, [3, 4, 5]))) == []
    report = validate_route(
        g,
        drawn(g, [0, 3, 4, 7]),
        stop_nodes=[0, 7],
        node_mask=np.ones(g.n, bool),
        banned_edges=np.zeros(g.m, bool),
    )
    assert [c.name for c in report.checks if not c.ok] == ["transitions"]
    assert report.check("transitions").index == 2


def test_stop_out_of_order() -> None:
    route = drawn(GRID, [0, 1, 4, 5, 8], stop_idx=(0, 2, 3, 4))
    assert failed(check(route, stop_nodes=[0, 5, 4, 8])) == ["stops"]
    assert failed(check(route, stop_nodes=[0, 4, 5, 8])) == []
    route.stop_idx = (0, 3, 2, 4)
    assert failed(check(route, stop_nodes=[0, 5, 4, 8])) == ["stops"]
    route.stop_idx = (0, 2, 3)
    assert failed(check(route, stop_nodes=[0, 4, 5])) == ["stops"]


def test_node_outside_the_mask_and_banned_edge() -> None:
    route = drawn(GRID, [0, 1, 4, 5, 8])
    mask = ALL_NODES.copy()
    mask[4] = False
    assert failed(check(route, node_mask=mask)) == ["chain", "constraints"]
    banned = NO_BANS.copy()
    banned[edge_id(GRID, 4, 5)] = True
    report = validate_route(
        GRID, route, stop_nodes=[0, 8], node_mask=ALL_NODES, banned_edges=banned
    )
    assert [c.name for c in report.checks if not c.ok] == ["constraints"]
    assert report.check("constraints").index == 2 and "tier" in report.check("constraints").detail


def test_wrong_metrics() -> None:
    route = drawn(GRID, [0, 1, 2, 5])
    route.metrics = Metrics(route.metrics.length_m + 1.0, route.metrics.time_s)
    assert failed(check(route)) == ["metrics"]
    report = validate_route(
        GRID, route, stop_nodes=[0, 5], node_mask=ALL_NODES, banned_edges=NO_BANS
    )
    assert report.lines()[-1] == "metrics: FAILED: length_m reported 301.0, recomputed 300.0"
    route.metrics = Metrics(300.0 * (1 + 1e-7), route.metrics.time_s)  # within the tolerance
    assert failed(check(route)) == []


def test_reported_items_are_recounted() -> None:
    route = drawn(GRID, [0, 1, 2, 5])
    length, time = route.metrics.length_m, route.metrics.time_s
    items = count_items(GRID, route.nodes, route.edges)
    route.metrics = Metrics(length, time, {"items": items})
    assert failed(check(route)) == []
    route.metrics = Metrics(length, time, {"items": {**items, "bridge": 999}})
    assert failed(check(route)) == ["metrics"]
    report = validate_route(
        GRID, route, stop_nodes=[0, 5], node_mask=ALL_NODES, banned_edges=NO_BANS
    )
    assert report.lines()[-1].startswith("metrics: FAILED: items reported {")
    assert "'bridge': 999" in report.lines()[-1] and "recounted" in report.lines()[-1]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_metrics_must_be_finite(bad: float) -> None:
    route = drawn(GRID, [0, 1, 2, 5])
    length, time = route.metrics.length_m, route.metrics.time_s
    route.metrics = Metrics(bad, time)
    assert failed(check(route)) == ["metrics"]
    route.metrics = Metrics(length, bad)
    assert failed(check(route)) == ["metrics"]


def with_members(data: GraphData, members: dict[int, list[int]]) -> GraphData:
    """`data` with more original OSM ids merged into some nodes, unchecked: an id two nodes
    hold is a build `validate` refuses, which the validator must catch on its own."""
    held = [
        data.member_osm_ids[data.member_indptr[v] : data.member_indptr[v + 1]].tolist()
        + members.get(v, [])
        for v in range(data.n)
    ]
    return replace(
        data,
        member_indptr=np.concatenate(([0], np.cumsum([len(h) for h in held]))).astype(np.int64),
        member_osm_ids=np.array([i for h in held for i in h], dtype=np.int64),
    )


def test_one_osm_junction_held_by_two_build_nodes_is_passed_twice() -> None:
    # node 4 is a cluster of OSM nodes 5 and 100, and node 3 a stub the consolidation left
    # holding OSM node 100 too: the route passes that junction at node 3, then at node 4
    g = with_members(GRID, {4: [100], 3: [100]})
    route = drawn(g, [1, 0, 3, 6, 7, 4, 5])
    report = validate_route(g, route, stop_nodes=[1, 5], node_mask=ALL_NODES, banned_edges=NO_BANS)
    assert [c.name for c in report.checks if not c.ok] == ["osm_ids"]
    assert report.check("osm_ids").index == 4  # the edge into node 4
    assert report.check("osm_ids").detail == "original OSM node 100 passed twice"
    assert failed(check(drawn(g, [1, 4, 5]), g)) == []  # each of them once is fine
    assert failed(check(drawn(g, [0, 3, 6]), g)) == []


def test_a_cluster_s_members_are_one_visit_and_a_round_trip_closes_on_its_start() -> None:
    g = with_members(GRID, {0: [100, 101], 4: [102]})
    assert failed(check(drawn(g, [0, 1, 4, 3, 0]), g)) == []
    assert failed(check(drawn(g, [1, 4, 3, 0, 1, 2]), g)) == ["vertex_simple", "osm_ids"]


def test_validation_failed_carries_the_report() -> None:
    route = drawn(GRID, [0, 1, 2])
    route.metrics = Metrics(1.0, 1.0)
    report = validate_route(
        GRID, route, stop_nodes=[0, 2], node_mask=ALL_NODES, banned_edges=NO_BANS
    )
    error = ValidationFailed(report)
    assert error.report is report and "metrics" in str(error)
    with pytest.raises(ValidationFailed):
        raise error


def test_a_strict_turn_constraint_is_proven_from_the_bearings() -> None:
    # 0 → 1 → 4 → 5: east, a left turn north at 1, a right turn east at 4
    route = drawn(GRID, [0, 1, 4, 5])
    no_left, no_right = TURN_LEFT | TURN_U, TURN_RIGHT | TURN_U

    def report(classes: int) -> ValidationReport:
        return validate_route(
            GRID,
            route,
            stop_nodes=[0, 5],
            node_mask=ALL_NODES,
            banned_edges=NO_BANS,
            banned_turn_classes=classes,
        )

    assert report(0).ok  # no constraint, or a soft one: nothing to prove about turns
    strict_left = report(no_left)
    assert [c.name for c in strict_left.checks if not c.ok] == ["constraints"]
    assert strict_left.check("constraints").index == 1
    assert strict_left.check("constraints").detail == (
        "a left turn at node 1, which the request's strict constraint refuses"
    )
    strict_right = report(no_right).check("constraints")
    assert (strict_right.ok, strict_right.index) == (False, 2)
    assert strict_right.detail.startswith("a right turn at node 4")
    straight = drawn(GRID, [0, 1, 2])
    assert validate_route(
        GRID, straight, stop_nodes=[0, 2], node_mask=ALL_NODES, banned_edges=NO_BANS,
        banned_turn_classes=no_left | no_right,
    ).ok  # fmt: skip


# The alphabet route: a 3 × 4 lattice whose rows are Elm (south), Alder and Birch Streets, column
# 3 "Ash Street" (A too) and the other columns numbered avenues (free).
#   8 - 9 - 10 - 11   Birch
#   4 - 5 -  6 -  7   Alder
#   0 - 1 -  2 -  3   Elm
ABC = lattice(
    3,
    4,
    roads=named_roads(
        3, 4, ["Elm Street", "Alder Street", "Birch Street"],
        ["1st Avenue", "2nd Avenue", "3rd Avenue", "Ash Street"],
    ),
)  # fmt: skip


def lettered(nodes: Sequence[int], last_leg: int, **changes: object) -> Drawn:
    """A route on ABC reporting its alphabet counts with its last leg from edge `last_leg`
    (recounted, then `changes` applied)."""
    route = drawn(ABC, nodes)
    counts = {**alphabet_counts(ABC, route.edges, last_leg), **changes}
    return replace(route, metrics=replace(route.metrics, counters={"alphabet": counts}))


def alphabet_report(route: Drawn, *, alphabet: bool = True) -> ValidationReport:
    stops = route.nodes[list(route.stop_idx)].tolist()
    return validate_route(
        ABC,
        route,
        stop_nodes=stops,
        node_mask=np.ones(ABC.n, dtype=bool),
        banned_edges=np.zeros(ABC.m, dtype=bool),
        alphabet=alphabet,
    )


def test_an_alphabet_route_passes_with_free_first_and_last_legs() -> None:
    # Elm (free first leg), up 2nd Avenue, Alder, Ash (A again), Birch; then Birch on, free
    route = lettered([0, 1, 5, 6, 7, 11, 10, 9, 8], 7)
    assert route.metrics.counters["alphabet"]["sequence"] == "AB"
    report = alphabet_report(route)
    assert report.ok, report.lines()
    assert [c.name for c in report.checks] == [*NAMES, "alphabet"]
    detail = report.check("alphabet").detail
    assert "AB on edges 2 to 7" in detail and "free: any roads" in detail
    assert [c.name for c in alphabet_report(route, alphabet=False).checks] == list(NAMES)


def test_a_route_out_of_order_fails_the_alphabet_check() -> None:
    # Alder, then Elm (E after A), then Birch: reported as recounted, so metrics hold
    route = lettered([4, 5, 1, 2, 6, 10, 11], 6)
    assert route.metrics.counters["alphabet"]["sequence"] == "AEB"
    report = alphabet_report(route)
    assert report.check("metrics").ok
    bad = report.check("alphabet")
    assert not bad.ok and bad.index == 2 and "Elm Street (E)" in bad.detail
    assert "want B" in bad.detail


def test_a_wrong_report_fails_the_metrics_check() -> None:
    nodes = [0, 1, 5, 6, 7, 11, 10, 9, 8]
    for changes in (
        {"sequence": "ABC"},
        {"letters": 3},
        {"roads": ["Alder Street", "Ash Street"]},
        {"alpha_from": 0},
        {"free_from": 3},  # in range, but the recount up to it is A alone
    ):
        report = alphabet_report(lettered(nodes, 7, **changes))
        assert failed({c.name: c.ok for c in report.checks}) == ["metrics"], changes


@pytest.mark.parametrize("free_from", [-1, 9, 2.0, True, None])
def test_a_free_from_outside_the_route_fails(free_from: object) -> None:
    route = lettered([0, 1, 5, 6, 7, 11, 10, 9, 8], 7, free_from=free_from)
    report = alphabet_report(route)
    assert failed({c.name: c.ok for c in report.checks}) == ["metrics", "alphabet"]


def test_an_alphabet_request_needs_the_route_s_letters() -> None:
    report = alphabet_report(drawn(ABC, [0, 1, 5, 6]))
    assert failed({c.name: c.ok for c in report.checks}) == ["alphabet"]
    assert "reports no letters" in report.check("alphabet").detail
