import math
from dataclasses import replace
from itertools import permutations

import numpy as np
import pytest

from worst_route.engine import search
from worst_route.engine.search import shortest_path, simple_paths
from worst_route.graph.backend import Path
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.testing import edge_id, grid_graph, make_graph

from ..graph.test_edge_search import (
    Case,
    assert_legal,
    assert_same,
    legal_simple_paths,
    town_with_tables,
)
from .graphs import X0, Y0, lattice

STRAIGHT_AT_4 = [((3, 4), (4, 5)), ((5, 4), (4, 3)), ((1, 4), (4, 7)), ((7, 4), (4, 1))]
BACKENDS = pytest.mark.parametrize(
    "backend_type", [PythonBackend, CsrBackend], ids=["python", "csr"]
)


def nodes_of(path: Path | None) -> list[int] | None:
    return None if path is None else path[0].tolist()


def lengths(g: GraphData) -> np.ndarray:
    return g.edge_length_m.astype(np.float64)


def test_turn_edges_count_only_on_a_build_with_rows() -> None:
    for g, expected in [(lattice(3, 3), [4, 7]), (lattice(3, 3, transitions=STRAIGHT_AT_4), None)]:
        b, entry = PythonBackend(g), edge_id(g, 3, 4)
        path = shortest_path(b, 4, 7, lengths(g), entry_edge=entry)
        paths = simple_paths(b, 4, 7, entry_edge=entry, max_depth=3, max_paths=9)
        if expected is not None:  # no rows: every turn allowed, the entry edge ignored
            assert nodes_of(path) == expected
            assert [4, 7] in [p[0].tolist() for p in paths]
        else:  # arriving from 3, the junction allows only straight on, to 5
            assert nodes_of(path) == [4, 5, 8, 7]
            assert [p[0].tolist() for p in paths] == [[4, 5, 8, 7]]


def divided() -> GraphData:
    """S (0) to T (3) is shortest east on one carriageway, S → P, across, P → Q, and back west on
    the other, Q → T, the twin of S → P; the long way round is S V W X T.

        X ─────── T ← Q
        │           ↑
        │      S → P
        │      ↓
        W ──── V
    """
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 100, Y0 + 20), (X0, Y0 + 20)]
    xy += [(X0, Y0 - 100), (X0 - 100, Y0 - 100), (X0 - 100, Y0 + 20)]
    edges = [(0, 1, 100.0), (1, 2, 20.0), (2, 3, 100.0)]
    edges += [(0, 4, 100.0), (4, 5, 100.0), (5, 6, 120.0), (6, 3, 100.0)]
    return make_graph(xy, edges, twins=[((0, 1), (2, 3))])


def test_a_path_driving_both_carriageways_is_searched_again() -> None:
    g = divided()
    b = PythonBackend(g)
    assert nodes_of(shortest_path(b, 0, 3, lengths(g))) == [0, 1, 2, 3]
    assert nodes_of(shortest_path(b, 0, 3, lengths(g), avoid_twins=True)) == [0, 4, 5, 6, 3]
    banned = np.zeros(g.m, dtype=bool)
    banned[edge_id(g, 4, 5)] = True
    assert shortest_path(b, 0, 3, lengths(g), banned_edges=banned, avoid_twins=True) is None
    assert not banned[edge_id(g, 2, 3)]  # the caller's mask is left alone


def test_the_search_gives_up_after_its_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    g = divided()
    monkeypatch.setattr(search, "TWIN_RESEARCH_ROUNDS", 0)
    assert shortest_path(PythonBackend(g), 0, 3, lengths(g), avoid_twins=True) is None


def loop(bypass: bool = False) -> GraphData:
    """S (0) → A (1) → D (2) is shortest, but arriving from S, A allows only A → B (3), and from
    C (4) only A → D: the cheapest legal walk, S A B C A D, enters A twice. With `bypass`, S → X
    (5) → D, 1 km, is the one legal path that does not."""
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0), (X0 + 150, Y0 + 100), (X0 + 50, Y0 + 100)]
    edges = [(0, 1, 100.0), (1, 2, 100.0), (1, 3, 100.0), (3, 4, 100.0), (4, 1, 100.0)]
    if bypass:
        xy.append((X0 + 100, Y0 - 300))
        edges += [(0, 5, 500.0), (5, 2, 500.0)]
    return make_graph(xy, edges, transitions=[((0, 1), (1, 3)), ((4, 1), (1, 2))])


@BACKENDS
def test_a_walk_entering_a_node_twice_is_searched_again(backend_type) -> None:
    g = loop()
    b = backend_type(g)
    assert nodes_of(b.dijkstra_edges(0, 2, lengths(g))) == [0, 1, 3, 4, 1, 2]
    # with C → A, the edge coming back to A, banned there is no walk: no node-simple path exists
    assert shortest_path(b, 0, 2, lengths(g)) is None
    g = loop(bypass=True)
    b = backend_type(g)
    banned = np.zeros(g.m, dtype=bool)
    assert nodes_of(shortest_path(b, 0, 2, lengths(g), banned_edges=banned)) == [0, 5, 2]
    assert not banned.any()  # the caller's mask is left alone
    assert shortest_path(b, 0, 2, lengths(g), simple_retry=False) is None  # the baseline's


def loops(count: int, *, bypass: bool) -> GraphData:
    """S (0) → A (1) → D (2) is shortest, but arriving from S, A allows only A → B (3), and from
    each of the `count` nodes C (4, 5, ...) B leads to, only A → D: each loop B C A, the cheapest
    first, makes the cheapest legal walk enter A twice. With `bypass`, S → X → D, 1 km."""
    cs = list(range(4, 4 + count))
    xy = [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0), (X0 + 100, Y0 + 100)]
    xy += [(X0 + 50 * k, Y0 + 200) for k in range(count)]
    edges = [(0, 1, 100.0), (1, 2, 100.0), (1, 3, 100.0)]
    edges += [(3, c, 100.0 + k) for k, c in enumerate(cs)] + [(c, 1, 100.0) for c in cs]
    if bypass:
        xy.append((X0 + 100, Y0 - 300))
        edges += [(0, len(xy) - 1, 500.0), (len(xy) - 1, 2, 500.0)]
    transitions = [((0, 1), (1, 3)), *(((c, 1), (1, 2)) for c in cs)]
    return make_graph(xy, edges, transitions=transitions)


@BACKENDS
def test_the_search_again_gives_up_after_its_rounds(backend_type, monkeypatch) -> None:
    rounds = search.SIMPLE_RETRY_ROUNDS
    # a loop banned a round: the bypass is found after as many loops as rounds, not one more
    g = loops(rounds, bypass=True)
    assert nodes_of(shortest_path(backend_type(g), 0, 2, lengths(g))) == [0, g.n - 1, 2]
    g = loops(rounds + 1, bypass=True)
    assert shortest_path(backend_type(g), 0, 2, lengths(g)) is None
    # without the bypass no node-simple path exists, and the rounds run out
    g = loops(rounds + 1, bypass=False)
    assert shortest_path(backend_type(g), 0, 2, lengths(g)) is None
    monkeypatch.setattr(search, "SIMPLE_RETRY_ROUNDS", 0)
    g = loop(bypass=True)
    assert shortest_path(backend_type(g), 0, 2, lengths(g)) is None


def small_town(seed: int) -> GraphData:
    """A 4 × 5 lattice of two-way streets, every junction allowing a random 60% of its turns."""
    g = grid_graph(4, 5, 100.0)
    rng = np.random.default_rng(seed)
    table: list[tuple[int, int, int]] = []
    for v in range(g.n):
        ins = g.in_edges[g.in_indptr[v] : g.in_indptr[v + 1]].tolist()
        outs = g.out_edges[g.out_indptr[v] : g.out_indptr[v + 1]].tolist()
        table += [(v, a, b) for a in ins for b in outs if rng.random() < 0.6]
    columns = np.array(table, dtype=np.int32).reshape(-1, 3)
    return replace(g, trans_node=columns[:, 0], trans_in=columns[:, 1], trans_out=columns[:, 2])


def test_a_node_simple_path_is_found_where_the_cheapest_walk_loops() -> None:
    # every pair of nodes of two small towns with tables, weights of 1, 2 or 3: where the
    # cheapest legal walk is simple it is the answer; where it enters a node twice, the answer
    # is a legal simple path (by brute force over networkx's) costing at least the walk, or None,
    # always None when there is none; the same bytes on both backends
    found = longer = missed = 0
    for seed in range(2):
        g = small_town(seed)
        weights = np.random.default_rng(seed + 100).integers(1, 4, size=g.m).astype(np.float64)
        backends = [PythonBackend(g), CsrBackend(g)]
        for src, dst in permutations(range(g.n), 2):
            walk = backends[0].dijkstra_edges(src, dst, weights)
            paths = [shortest_path(b, src, dst, weights) for b in backends]
            assert_same(paths)
            path = paths[0]
            if walk is None or len(set(walk[0].tolist())) == len(walk[0]):
                assert nodes_of(path) == nodes_of(walk)
                continue
            case = Case(g, weights, src, dst, -1, -1, None, None, None)
            legal = legal_simple_paths(case, g.n)
            if path is None:
                missed += bool(legal)
                continue
            assert tuple(path[1].tolist()) in legal
            assert_legal(case, path)
            cost = float(weights[path[1]].sum())
            assert cost >= float(weights[walk[1]].sum())
            found += 1
            longer += cost > float(weights[walk[1]].sum())
    # 66 found, 48 of them longer than the walk; a heuristic, so 41 missed
    assert found > 50 and longer > 30 and missed > 0


def test_the_backends_agree_on_searching_again() -> None:
    # the town of tables the edge-state parity tests use, turn edges, masks and budgets: the
    # searches made again (heaps that grow, longer walks) give the same bytes on both backends
    g = town_with_tables(0)
    rng = np.random.default_rng(1)
    weights = rng.integers(1, 4, size=g.m).astype(np.float64)
    backends = [PythonBackend(g), CsrBackend(g)]
    again = 0
    for _ in range(300):
        src, dst = (int(v) for v in rng.integers(g.n, size=2))
        ins = g.in_edges[g.in_indptr[src] : g.in_indptr[src + 1]].tolist()
        outs = g.out_edges[g.out_indptr[dst] : g.out_indptr[dst + 1]].tolist()
        turns = {
            "entry_edge": int(rng.choice([-1, *ins])),
            "exit_edge": int(rng.choice([-1, *outs])),
        }
        forbid = rng.random(g.n) < 0.05
        forbid[[src, dst]] = False
        masks = {"forbid_nodes": forbid, "banned_edges": rng.random(g.m) < 0.05}
        max_cost = float(rng.choice([math.inf, 30.0, 60.0]))
        walk = backends[0].dijkstra_edges(src, dst, weights, max_cost=max_cost, **turns, **masks)
        paths = [
            shortest_path(b, src, dst, weights, max_cost=max_cost, **turns, **masks)
            for b in backends
        ]
        assert_same(paths)
        if walk is not None and len(set(walk[0].tolist())) < len(walk[0]):
            again += paths[0] is not None
    assert again > 0  # 5 paths found by searching again
