"""The alphabet route: the letters, the counts and the constructive search."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from worst_route.config import ALPHABET_LETTER_WEIGHT, HighwayClass
from worst_route.engine import alphabet_search
from worst_route.engine.alphabet_search import (
    alphabet_counts,
    alphabet_route,
    alphabet_scored,
    edge_letters,
)
from worst_route.engine.constraints import NO_CONSTRAINTS, Constraints, constrain, returning
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.engine.route import Route, check_route
from worst_route.engine.seed_route import baseline_route, resolve_stops, seed_route
from worst_route.engine.turns import count_turns
from worst_route.engine.validator import validate_route
from worst_route.engine.worsen import Budget, WorsenResult
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import EdgeFlag, GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import Edge, make_graph

from .graphs import TREES, X0, Y0, at_node, avenue, edges_of, lattice, named_roads

Backend = type[PythonBackend] | type[CsrBackend]
BACKENDS = [PythonBackend, CsrBackend]


def streets(n: int, order: Sequence[int] | None = None) -> list[str]:
    """Rows named after the first n trees (A, B, ...), in `order` when given."""
    names = [f"{TREES[k]} Street" for k in range(n)]
    return names if order is None else [names[k] for k in order]


def town(n: int, order: Sequence[int] | None = None, **kwargs: Any) -> GraphData:
    """An n × n lattice whose rows are lettered streets and whose columns numbered avenues."""
    roads = named_roads(n, n, streets(n, order), [avenue(c + 1) for c in range(n)])
    return lattice(n, n, roads=roads, **kwargs)


ORDERED = town(10)  # rows A (south) to J (north)
SHUFFLED = town(20, np.random.default_rng(0).permutation(20).tolist())


# The letters ---------------------------------------------------------------------------------


def test_edge_letters_free_unnamed_links_and_numbered_streets() -> None:
    xy = [(X0 + 100.0 * k, Y0) for k in range(6)]
    g = make_graph(
        xy,
        [
            Edge(0, 1, name="Southwest Alder Street"),
            Edge(1, 2),  # unnamed
            Edge(2, 3, name="Northeast 82nd Avenue"),
            Edge(3, 4, name="Birch Street", highway=HighwayClass.PRIMARY_LINK),  # a ramp
            Edge(4, 5, name="Zelkova Way", flags=EdgeFlag.BRIDGE),
        ],
    )
    assert edge_letters(g).tolist() == [1, 0, 0, 0, 26]


def test_the_counts_read_the_alphabet_part_letter_by_letter() -> None:
    # along row 0 (Alder) then up column 3 and along rows 1 (Birch) and 2 (Cedar) of a lattice
    # whose column 3 is "Ash Street" (A too) and column 1 "Elm Street"
    names = named_roads(4, 4, streets(4), [None, "Elm Street", None, "Ash Street"])
    g = lattice(4, 4, roads=names)
    nodes = [0, 1, 2, 3, 7, 6, 5, 9, 10, 11, 15]
    edges = edges_of(g, nodes)
    letters = edge_letters(g)
    counts = alphabet_counts(g, edges, 8, letters)
    # Alder, Ash (one A), Birch, Elm (E after B, counted as it comes), Cedar
    assert counts == {
        "letters": 4,
        "sequence": "ABEC",
        "roads": ["Alder Street", "Birch Street", "Elm Street", "Cedar Street"],
        "alpha_from": 0,
        "free_from": 8,
    }
    assert alphabet_counts(g, edges, 4)["sequence"] == "A"  # Ash's edge 3 → 7 in the part too
    assert alphabet_counts(g, edges, 0) == {
        "letters": 0, "sequence": "", "roads": [], "alpha_from": 0, "free_from": 0,
    }  # fmt: skip


def test_the_free_first_leg_carries_any_road_up_to_the_first_a() -> None:
    names = named_roads(4, 4, ["Elm Street", "Alder Street", "Birch Street", None], [None] * 4)
    g = lattice(4, 4, roads=names)
    nodes = [0, 1, 5, 6, 10, 11, 15]  # Elm, (free), Alder, (free), Birch, (free)
    edges = edges_of(g, nodes)
    counts = alphabet_counts(g, edges, 5)
    assert counts["sequence"] == "AB" and counts["alpha_from"] == 2
    assert counts["roads"] == ["Alder Street", "Birch Street"]
    # no A road before the free last leg: no letter, the part empty at its end
    assert alphabet_counts(g, edges, 2) == {
        "letters": 0, "sequence": "", "roads": [], "alpha_from": 2, "free_from": 2,
    }  # fmt: skip


def test_the_letters_wrap_from_z_to_a() -> None:
    # a street of 27 blocks named after the 26 trees, then Ash Street: A to Z, then A again
    names = named_roads(1, 28, [None], [None] * 28)
    for k in range(27):
        names[(k, k + 1)] = {"name": f"{TREES[k % 26] if k < 26 else 'Ash'} Street"}
    g = lattice(1, 28, roads=names)
    edges = edges_of(g, list(range(28)))
    counts = alphabet_counts(g, edges, 27)
    assert counts["sequence"] == "ABCDEFGHIJKLMNOPQRSTUVWXYZA" and counts["letters"] == 27
    assert counts["roads"][-2:] == ["Zelkova Street", "Ash Street"]


# The search ----------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Run:
    g: GraphData
    start: Route
    result: WorsenResult
    stop_nodes: list[int]
    request: RouteRequest


def run(
    g: GraphData,
    stops: tuple[int, int],
    *,
    seed: int = 7,
    restarts: int = 30,
    backend_type: Backend = PythonBackend,
    constraints: Constraints = NO_CONSTRAINTS,
    letters: np.ndarray | None = None,
) -> Run:
    """The alphabet search between two nodes, from the seed route, validated."""
    backend, spatial = backend_type(g), SpatialIndex(g)
    request = RouteRequest(stops=[at_node(g, v) for v in stops], seed=seed, constraints=constraints)
    masks = request_masks(spatial, g, request)
    r_max = min(masks.leash_m, masks.extent_m)
    rng = np.random.Generator(np.random.PCG64(seed))
    snaps = resolve_stops(g, spatial, backend, request, masks)
    found = seed_route(
        backend, spatial, g, snaps, masks.active, masks.banned, rng, r_max=r_max,
        constraints=constraints,
    )  # fmt: skip
    baseline = baseline_route(backend, g, [s.node for s in snaps], masks.active, masks.banned)
    objective = constrain(Distance(g, baseline=baseline.metrics, r_max=r_max), constraints, g)
    score, metrics = alphabet_scored(objective, g, edge_letters(g), found.nodes, found.edges, 0)
    start = Route.new(
        g, found.nodes, found.edges, found.stop_idx, score=score, metrics=metrics, seed=seed,
        extra_banned=masks.banned,
    )  # fmt: skip
    result = alphabet_route(
        backend, g, start, objective, Budget(seconds=120.0, max_iterations=restarts), rng,
        masks.active, r_max=r_max, banned_edges=masks.banned, letters=letters,
    )  # fmt: skip
    stop_nodes = [s.node for s in snaps]
    report = validate_route(
        g,
        result.best,
        stop_nodes=stop_nodes,
        node_mask=masks.active,
        banned_edges=masks.banned,
        banned_turn_classes=constraints.banned_turn_classes,
        hard=constraints.hard,
    )
    assert report.ok, report.lines()
    return Run(g, start, result, stop_nodes, request)


def letters_of(route: Route) -> dict[str, Any]:
    counts: dict[str, Any] = route.metrics.counters["alphabet"]
    return counts


def replayed(r: Run) -> list[Route]:
    """The seed, then each better route, each event's splice applied to the one before."""
    routes = [r.start]
    for event in r.result.events:
        s = event.splice
        assert (s.i, s.j) == (0, len(routes[-1].edges))
        routes.append(
            routes[-1].splice(
                r.g, s.i, s.j, s.nodes, s.edges, score=event.score, metrics=event.metrics
            )
        )
    assert routes[-1].edges.tobytes() == r.result.best.edges.tobytes()
    return routes


def test_an_ordered_town_is_driven_a_to_j() -> None:
    r = run(ORDERED, (0, 99))
    best = r.result.best
    counts = letters_of(best)
    assert counts["sequence"] == "ABCDEFGHIJ" and counts["letters"] == 10
    assert counts["roads"] == streets(10)
    assert best.nodes[0] == 0 and best.nodes[-1] == 99
    assert best.score == 10 * ALPHABET_LETTER_WEIGHT + best.metrics.length_m
    assert counts == alphabet_counts(ORDERED, best.edges, int(counts["free_from"]))


def test_every_route_along_the_way_is_legal_and_better_than_the_last() -> None:
    r = run(SHUFFLED, (0, 399), restarts=60)
    routes = replayed(r)
    assert len(routes) > 3
    for before, after in zip(routes, routes[1:], strict=False):
        check_route(r.g, after.nodes, after.edges, after.stop_idx)
        assert after.score > before.score
        counts = letters_of(after)
        assert counts["sequence"] == "ABCDEFGHIJKLMNOPQRST"[: counts["letters"]]
    assert letters_of(r.result.best)["letters"] >= 5
    assert r.result.attempts == 60 and r.result.accepted == len(r.result.events)
    assert r.result.stopped_because == "budget"


@pytest.mark.parametrize("backend_type", BACKENDS, ids=["python", "csr"])
def test_a_seed_gives_the_same_route_twice_and_on_both_backends(backend_type: Backend) -> None:
    first = run(SHUFFLED, (0, 399), seed=3, restarts=25, backend_type=PythonBackend)
    again = run(SHUFFLED, (0, 399), seed=3, restarts=25, backend_type=backend_type)
    assert again.result.best.edges.tobytes() == first.result.best.edges.tobytes()
    assert [e.iteration for e in again.result.events] == [e.iteration for e in first.result.events]
    other = run(SHUFFLED, (0, 399), seed=4, restarts=25, backend_type=backend_type)
    assert other.result.best.edges.tobytes() != first.result.best.edges.tobytes()


# 0 → 1 Alder, 1 → 2 Ash, 2 → 3 Birch, 3 → 4 unnamed, one way: Birch is reached only along Ash.
LINE = make_graph(
    [(X0 + 100.0 * k, Y0) for k in range(5)],
    [
        Edge(0, 1, name="Alder Street"),
        Edge(1, 2, name="Ash Street"),
        Edge(2, 3, name="Birch Street"),
        Edge(3, 4),
    ],
)


def test_a_connector_may_drive_a_road_of_the_letter_just_completed() -> None:
    best = run(LINE, (0, 4), restarts=3).result.best
    assert letters_of(best) == {
        "letters": 2,
        "sequence": "AB",
        "roads": ["Alder Street", "Birch Street"],
        "alpha_from": 0,
        "free_from": 3,
    }


def test_the_first_leg_drives_any_road_to_the_first_a() -> None:
    # rows E (south), A, B, C...: the start's row is Elm, so the first leg drives it or an avenue
    g = town(6, [4, 0, 1, 2, 3, 5])
    r = run(g, (0, 35))
    counts = letters_of(r.result.best)
    assert counts["sequence"] == "ABCDEF"[: counts["letters"]] and counts["letters"] >= 3
    assert counts["alpha_from"] > 0


def test_a_town_without_an_a_road_keeps_the_seed_and_converges_at_once() -> None:
    # the first step finds no candidate and draws nothing: every restart would be the same
    g = lattice(5, 5, roads=named_roads(5, 5, ["Birch Street"] * 5, [None] * 5))
    r = run(g, (0, 24), restarts=1000)
    assert r.result.stopped_because == "converged" and r.result.attempts == 1
    assert r.result.best is r.start and r.result.events == ()
    assert letters_of(r.result.best)["letters"] == 0


def test_a_run_converges_after_its_patience(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alphabet_search, "ALPHABET_PATIENCE", 3)
    r = run(LINE, (0, 4), restarts=1000)  # the first restart finds the best there is
    assert r.result.stopped_because == "converged" and r.result.attempts == 4
    assert letters_of(r.result.best)["letters"] == 2


def test_the_letters_given_are_the_letters_used() -> None:
    # a caller that has the build's letters passes them; the run is the same
    given = run(SHUFFLED, (0, 399), seed=3, restarts=10, letters=edge_letters(SHUFFLED))
    worked_out = run(SHUFFLED, (0, 399), seed=3, restarts=10)
    assert given.result.best.edges.tobytes() == worked_out.result.best.edges.tobytes()


def test_no_restart_returns_the_seed() -> None:
    r = run(ORDERED, (0, 99), restarts=0)
    assert r.result.best is r.start and r.result.attempts == 0
    assert r.result.stopped_because == "budget"


def test_a_round_trip_comes_back_with_letters() -> None:
    r = run(ORDERED, (44, 44), restarts=20)
    best = r.result.best
    assert best.nodes[0] == best.nodes[-1] == 44 and best.is_round_trip
    assert letters_of(best)["letters"] >= 2


def test_a_strict_constraint_holds_on_every_route() -> None:
    strict = Constraints(no_left=True, strict=True)
    r = run(ORDERED, (11, 88), restarts=20, constraints=strict)
    assert letters_of(r.result.best)["letters"] >= 1
    for route in replayed(r):
        turns = count_turns(r.g, route.edges)
        assert turns["left"] == 0 and turns["u_turn"] == 0


def test_a_soft_constraint_composes() -> None:
    soft = Constraints(no_left=True)
    r = run(ORDERED, (0, 99), restarts=20, constraints=soft)
    assert letters_of(r.result.best)["letters"] >= 5


def test_hard_mode_composes() -> None:
    # hard mode's bans for each window and its window check apply to every step, so no route
    # along the way comes back to a road it left
    for g, stops, letters in ((ORDERED, (0, 99), 10), (SHUFFLED, (0, 399), 5)):
        r = run(g, stops, restarts=30, constraints=Constraints(hard=True))
        assert letters_of(r.result.best)["letters"] >= letters
        for route in replayed(r):
            assert returning(r.g, route.edges) is None


def test_turn_tables_are_kept() -> None:
    # straight on only at nodes 8, 9 and 10, inside row 1 (Birch)
    straight = []
    for v in (8, 9, 10):
        straight += [((v - 1, v), (v, v + 1)), ((v + 1, v), (v, v - 1))]
        straight += [((v - 6, v), (v, v + 6)), ((v + 6, v), (v, v - 6))]
    r = run(town(6, transitions=straight), (0, 35), restarts=20)
    assert r.result.refused_transitions == 0
    assert letters_of(r.result.best)["letters"] >= 2
