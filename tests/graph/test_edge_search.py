"""The transition-aware searches: `dijkstra_edges` and the turn checks of `simple_paths_dfs`.

Against the semantics in graph/backend.py: the node-state `dijkstra` on graphs without
transition rows (same cost, a path whenever it finds one), a heap-free implementation of the
edge-state tie rule on graphs with random transition tables (the exact path), a networkx search
over the edge states (the cost), and a brute force over networkx's simple paths for the DFS.
"""

import dataclasses
import math
from collections.abc import Iterator
from dataclasses import dataclass

import networkx as nx
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from worst_route.graph.backend import Path
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.testing import edge_id, make_graph

EXAMPLES = settings(max_examples=200, deadline=None)
TIED_WEIGHTS = st.sampled_from([0.0, 0.5, 1.0, 2.0])  # equal costs common, zero ones included


@dataclass
class Case:
    g: GraphData
    weights: np.ndarray
    src: int
    dst: int
    entry: int
    exit: int
    node_mask: np.ndarray | None
    forbid: np.ndarray | None
    banned: np.ndarray | None

    def enterable(self) -> np.ndarray:
        enter = np.ones(self.g.n, dtype=bool) if self.node_mask is None else self.node_mask
        return enter if self.forbid is None else enter & ~self.forbid

    def is_banned(self) -> np.ndarray:
        return np.zeros(self.g.m, dtype=bool) if self.banned is None else self.banned

    def turn_ok(self, e: int, f: int) -> bool:
        g = self.g
        rows = g.trans_node == g.edge_v[e]
        if not rows.any():
            return True
        return bool(((g.trans_in == e) & (g.trans_out == f) & rows).any())

    def out_edges(self, v: int) -> list[int]:
        g = self.g
        return sorted(g.out_edges[g.out_indptr[v] : g.out_indptr[v + 1]].tolist())

    def masks(self) -> dict[str, np.ndarray | None]:
        return {
            "node_mask": self.node_mask,
            "forbid_nodes": self.forbid,
            "banned_edges": self.banned,
        }

    def turns(self) -> dict[str, int]:
        return {"entry_edge": self.entry, "exit_edge": self.exit}

    def state_moves(self) -> Iterator[tuple[int, int]]:
        """Every move between edge states the semantics allow: (from, to), from −1 at src."""
        g, enter, banned = self.g, self.enterable(), self.is_banned()
        for f in self.out_edges(self.src):
            w = int(g.edge_v[f])
            if w == self.src or banned[f] or not enter[w]:
                continue
            if self.entry < 0 or self.turn_ok(self.entry, f):
                yield -1, f
        for e in range(g.m):
            v = int(g.edge_v[e])
            if v == self.dst or v == self.src or not enter[v] or banned[e]:
                continue
            for f in self.out_edges(v):
                w = int(g.edge_v[f])
                if w in (self.src, v) or banned[f] or not enter[w]:
                    continue
                if self.turn_ok(e, f):
                    yield e, f

    def terminal(self, e: int) -> bool:
        return int(self.g.edge_v[e]) == self.dst and (self.exit < 0 or self.turn_ok(e, self.exit))


def optional_mask(size: int, *, default: bool) -> st.SearchStrategy[np.ndarray | None]:
    def build(flipped: set[int]) -> np.ndarray:
        mask = np.full(size, default, dtype=bool)
        mask[sorted(flipped)] = not default
        return mask

    flipped = st.sets(st.integers(0, size - 1), max_size=size // 3) if size else st.just(set())
    return st.none() | flipped.map(build)


@st.composite
def cases(
    draw: st.DrawFn,
    *,
    rows: bool,
    max_nodes: int = 8,
    max_edges: int = 28,
    weight: st.SearchStrategy[float] = TIED_WEIGHTS,
) -> Case:
    """A random multigraph (self-loops, parallel edges), weights, masks, turn edges and, when
    `rows`, a random transition table: some nodes list a random subset of their turns."""
    n = draw(st.integers(2, max_nodes))
    node = st.integers(0, n - 1)
    pairs = draw(st.lists(st.tuples(node, node), min_size=n, max_size=max_edges))
    lonlat = [(-122.0 + 0.001 * i, 45.0) for i in range(n)]
    g = make_graph(
        [(100.0 * i, 0.0) for i in range(n)], [(u, v, 1.0) for u, v in pairs], lonlat=lonlat
    )
    if rows:
        table: list[tuple[int, int, int]] = []
        for v in range(n):
            ins = g.in_edges[g.in_indptr[v] : g.in_indptr[v + 1]].tolist()
            outs = g.out_edges[g.out_indptr[v] : g.out_indptr[v + 1]].tolist()
            turns = [(v, a, b) for a in ins for b in outs]
            if turns and draw(st.booleans()):
                keep = draw(st.lists(st.booleans(), min_size=len(turns), max_size=len(turns)))
                table += [t for t, k in zip(turns, keep, strict=True) if k]
        table.sort()
        columns = np.array(table, dtype=np.int32).reshape(-1, 3)
        g = dataclasses.replace(
            g, trans_node=columns[:, 0], trans_in=columns[:, 1], trans_out=columns[:, 2]
        )
    weights = np.array(draw(st.lists(weight, min_size=g.m, max_size=g.m)), dtype=np.float64)
    src, dst = draw(node), draw(node)
    ins = g.in_edges[g.in_indptr[src] : g.in_indptr[src + 1]].tolist()
    outs = g.out_edges[g.out_indptr[dst] : g.out_indptr[dst + 1]].tolist()
    return Case(
        g=g,
        weights=weights.reshape(g.m),
        src=src,
        dst=dst,
        entry=draw(st.sampled_from([-1, *ins])),
        exit=draw(st.sampled_from([-1, *outs])),
        node_mask=draw(optional_mask(n, default=True)),
        forbid=draw(optional_mask(n, default=False)),
        banned=draw(optional_mask(g.m, default=False)),
    )


def assert_legal(case: Case, path: Path) -> None:
    nodes, edges = path
    g = case.g
    assert nodes.dtype == np.int32 and edges.dtype == np.int32
    assert len(edges) == len(nodes) - 1
    assert nodes[0] == case.src and nodes[-1] == case.dst
    assert np.array_equal(g.edge_u[edges], nodes[:-1])
    assert np.array_equal(g.edge_v[edges], nodes[1:])
    assert len(set(nodes.tolist())) == len(nodes)
    assert case.enterable()[nodes].all()
    assert not case.is_banned()[edges].any()
    turns = [case.entry, *edges.tolist(), case.exit]
    if len(edges) == 0 and (case.entry < 0 or case.exit < 0):
        return
    for e, f in zip(turns[:-1], turns[1:], strict=True):
        if e >= 0 and f >= 0:
            assert case.turn_ok(e, f), (e, f)


def tie_rule_reference(case: Case, max_cost: float) -> list[int] | None:
    """The semantics of graph/backend.py written without a heap: settle the open state of
    least (cost, edge id), relax its moves in ascending edge id, replace a predecessor only on a
    strictly smaller cost; answer the first terminal state settled, None if its path repeats a
    node. Returns the path's edges."""
    enter = case.enterable()
    if max_cost < 0 or not (enter[case.src] and enter[case.dst]):
        return None
    if case.src == case.dst:
        conflict = case.entry >= 0 and case.exit >= 0 and not case.turn_ok(case.entry, case.exit)
        return None if conflict else []
    moves: dict[int, list[int]] = {}
    for e, f in case.state_moves():
        moves.setdefault(e, []).append(f)
    cost: dict[int, float] = {}
    pred: dict[int, int] = {}
    for f in moves.get(-1, []):
        if case.weights[f] <= max_cost:
            cost[f], pred[f] = float(case.weights[f]), -1
    settled: set[int] = set()
    while True:
        open_states = [(c, e) for e, c in cost.items() if e not in settled]
        if not open_states:
            return None
        c, e = min(open_states)
        settled.add(e)
        if case.terminal(e):
            edges = [e]
            while pred[edges[-1]] >= 0:
                edges.append(pred[edges[-1]])
            edges.reverse()
            nodes = [case.src, *(int(case.g.edge_v[x]) for x in edges)]
            return edges if len(set(nodes)) == len(nodes) else None
        for f in moves.get(e, []):
            through = c + float(case.weights[f])
            if f not in settled and through <= max_cost and through < cost.get(f, math.inf):
                cost[f], pred[f] = through, e


def state_graph_cost(case: Case) -> float | None:
    """The least cost of any walk of edge states from src to an allowed end (networkx)."""
    graph = nx.DiGraph()
    for e, f in case.state_moves():
        graph.add_edge(e, f, weight=float(case.weights[f]))
    for e in list(graph.nodes):
        if e >= 0 and case.terminal(e):
            graph.add_edge(e, "end", weight=0.0)
    if -1 not in graph or "end" not in graph or not nx.has_path(graph, -1, "end"):
        return None
    return float(nx.dijkstra_path_length(graph, -1, "end"))


@EXAMPLES
@given(cases(rows=True), st.sampled_from([0.0, 1.0, 1.5, 2.0, 3.0, math.inf]))
def test_dijkstra_edges_follows_the_tie_rule(case: Case, max_cost: float) -> None:
    path = PythonBackend(case.g).dijkstra_edges(
        case.src, case.dst, case.weights, max_cost=max_cost, **case.turns(), **case.masks()
    )
    expected = tie_rule_reference(case, max_cost)
    if expected is None:
        assert path is None
        return
    assert path is not None
    assert path[1].tolist() == expected
    assert_legal(case, path)
    assert float(case.weights[path[1]].sum()) <= max_cost


@EXAMPLES
@given(cases(rows=True, weight=st.integers(1, 9).map(float)))
def test_dijkstra_edges_finds_the_cheapest_legal_walk(case: Case) -> None:
    path = PythonBackend(case.g).dijkstra_edges(
        case.src, case.dst, case.weights, **case.turns(), **case.masks()
    )
    if case.src == case.dst:
        return  # the one-node answer is the tie-rule test's
    best = state_graph_cost(case)
    if best is None:
        assert path is None
    elif path is not None:  # None only when the cheapest walk repeats a node
        assert_legal(case, path)
        assert float(case.weights[path[1]].sum()) == best


@EXAMPLES
@given(cases(rows=False, weight=st.sampled_from([0.5, 1.0, 2.0])))
def test_without_rows_it_matches_the_node_state_search(case: Case) -> None:
    backend = PythonBackend(case.g)
    nodes = backend.dijkstra(case.src, case.dst, case.weights, **case.masks())
    edges = backend.dijkstra_edges(case.src, case.dst, case.weights, **case.turns(), **case.masks())
    assert (nodes is None) == (edges is None)
    if nodes is None or edges is None:
        return
    assert_legal(case, edges)
    cost = float(case.weights[nodes[1]].sum())
    assert float(case.weights[edges[1]].sum()) == cost
    if case.src != case.dst and _cheapest_is_unique(case):
        assert np.array_equal(edges[1], nodes[1])
    at_cost = backend.dijkstra_edges(
        case.src, case.dst, case.weights, max_cost=cost, **case.turns(), **case.masks()
    )
    assert at_cost is not None and np.array_equal(at_cost[1], edges[1])


def _cheapest_is_unique(case: Case) -> bool:
    """Whether one cheapest src → dst path exists (positive weights: count the tight paths)."""
    enter, banned, g = case.enterable(), case.is_banned(), case.g
    graph = nx.MultiDiGraph()
    for e in range(g.m):
        u, v = int(g.edge_u[e]), int(g.edge_v[e])
        if enter[u] and enter[v] and not banned[e]:
            graph.add_edge(u, v, key=e, weight=float(case.weights[e]))
    cost = nx.single_source_dijkstra_path_length(graph, case.src)
    count = {case.src: 1}
    for v in sorted(cost, key=cost.__getitem__):
        if v != case.src:
            count[v] = sum(
                count[u]
                for u, _, w in graph.in_edges(v, data="weight")
                if u in cost and cost[u] + w == cost[v]
            )
    return count.get(case.dst, 0) == 1


@EXAMPLES
@given(cases(rows=True, max_nodes=6, max_edges=20), st.integers(0, 6), st.integers(1, 20))
def test_simple_paths_check_every_turn(case: Case, max_depth: int, max_paths: int) -> None:
    backend = PythonBackend(case.g)
    every = backend.simple_paths_dfs(
        case.src,
        case.dst,
        max_depth=max_depth,
        max_paths=10**6,
        **case.turns(),
        **case.masks(),
    )
    found = [tuple(p[1].tolist()) for p in every]
    enter, banned, g = case.enterable(), case.is_banned(), case.g
    if not (enter[case.src] and enter[case.dst]):
        expected: list[tuple[int, ...]] = []
    elif case.src == case.dst:
        conflict = case.entry >= 0 and case.exit >= 0 and not case.turn_ok(case.entry, case.exit)
        expected = [] if conflict else [()]
    else:
        graph = nx.MultiDiGraph()
        graph.add_nodes_from(int(v) for v in np.flatnonzero(enter))
        for e in range(g.m):
            u, v = int(g.edge_u[e]), int(g.edge_v[e])
            if enter[u] and enter[v] and not banned[e]:
                graph.add_edge(u, v, key=e)
        expected = []
        for p in nx.all_simple_edge_paths(graph, case.src, case.dst, cutoff=max_depth):
            edges = [e for _, _, e in p]
            turns = [case.entry, *edges, case.exit]
            pairs = zip(turns[:-1], turns[1:], strict=True)
            if all(e < 0 or f < 0 or case.turn_ok(e, f) for e, f in pairs):
                expected.append(tuple(edges))
    assert sorted(found) == sorted(expected) and found == sorted(found)
    for path in every:
        assert_legal(case, path)
    first = backend.simple_paths_dfs(
        case.src,
        case.dst,
        max_depth=max_depth,
        max_paths=max_paths,
        **case.turns(),
        **case.masks(),
    )
    assert [tuple(p[1].tolist()) for p in first] == found[:max_paths]


# Crafted cases. A plus-shaped junction J (node 0) with 100 m arms to N (1), E (2), S (3), W (4),
# all two-way, and a ring road of 300 m sides round it, N - E - S - W - N; J allows only going
# straight on.
PLUS_XY = [(0.0, 0.0), (0.0, 100.0), (100.0, 0.0), (0.0, -100.0), (-100.0, 0.0)]
ARMS = [(0, 1), (0, 2), (0, 3), (0, 4)]
RING = [(1, 2), (2, 3), (3, 4), (4, 1)]


def plus(straight_only: bool = True) -> GraphData:
    edges = [(a, b, 100.0) for a, b in ARMS] + [(b, a, 100.0) for a, b in ARMS]
    edges += [(a, b, 300.0) for a, b in RING] + [(b, a, 300.0) for a, b in RING]
    straight = [((1, 0), (0, 3)), ((3, 0), (0, 1)), ((2, 0), (0, 4)), ((4, 0), (0, 2))]
    return make_graph(PLUS_XY, edges, transitions=straight if straight_only else ())


def lengths(g: GraphData) -> np.ndarray:
    return g.edge_length_m.astype(np.float64)


def nodes_of(path: Path | None) -> list[int] | None:
    return None if path is None else path[0].tolist()


def test_a_forbidden_turn_is_driven_round() -> None:
    g = plus()
    b = PythonBackend(g)
    # S → J → E turns at J: the node-state search does it, the edge-state one goes round
    assert nodes_of(b.dijkstra(3, 2, lengths(g))) == [3, 0, 2]
    assert nodes_of(b.dijkstra_edges(3, 2, lengths(g))) == [3, 2]
    assert nodes_of(b.dijkstra_edges(3, 1, lengths(g))) == [3, 0, 1]  # straight on
    assert nodes_of(PythonBackend(plus(False)).dijkstra_edges(3, 2, lengths(g))) == [3, 0, 2]


def test_the_entry_and_exit_edges_constrain_the_ends() -> None:
    g = plus()
    b = PythonBackend(g)
    # arriving at J from S, J → E is a turn: the path from J must go straight, to N
    from_s = edge_id(g, 3, 0)
    assert nodes_of(b.dijkstra_edges(0, 2, lengths(g), entry_edge=from_s)) == [0, 1, 2]
    assert nodes_of(b.dijkstra_edges(0, 2, lengths(g))) == [0, 2]
    # handing over to J → N at J: only from S
    to_n = edge_id(g, 0, 1)
    assert nodes_of(b.dijkstra_edges(2, 0, lengths(g), exit_edge=to_n)) == [2, 3, 0]
    assert b.dijkstra_edges(2, 0, lengths(g), exit_edge=to_n, banned_edges=_ban(g, (3, 0))) is None


def _ban(g: GraphData, *pairs: tuple[int, int]) -> np.ndarray:
    banned = np.zeros(g.m, dtype=bool)
    banned[[edge_id(g, u, v) for u, v in pairs]] = True
    return banned


def test_from_a_node_to_itself() -> None:
    g = plus()
    b = PythonBackend(g)
    from_s, to_n, to_e = edge_id(g, 3, 0), edge_id(g, 0, 1), edge_id(g, 0, 2)
    assert nodes_of(b.dijkstra_edges(0, 0, lengths(g))) == [0]
    assert nodes_of(b.dijkstra_edges(0, 0, lengths(g), entry_edge=from_s, exit_edge=to_n)) == [0]
    assert b.dijkstra_edges(0, 0, lengths(g), entry_edge=from_s, exit_edge=to_e) is None
    assert nodes_of(b.dijkstra_edges(0, 0, lengths(g), entry_edge=from_s)) == [0]
    assert b.dijkstra_edges(0, 0, lengths(g), max_cost=-1.0) is None
    dfs = b.simple_paths_dfs(0, 0, entry_edge=from_s, exit_edge=to_e, max_depth=4, max_paths=9)
    assert dfs == []


def test_a_cheapest_walk_through_a_node_twice_is_none() -> None:
    # S (0) → A (1) → D (2) is shortest, but arriving from S, A allows only A → B (3); from
    # C (4), only A → D. The cheapest legal walk S A B C A D repeats A: no path.
    xy = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0), (150.0, 100.0), (50.0, 100.0)]
    g = make_graph(
        xy,
        [(0, 1, 1.0), (1, 2, 1.0), (1, 3, 1.0), (3, 4, 1.0), (4, 1, 1.0)],
        transitions=[((0, 1), (1, 3)), ((4, 1), (1, 2))],
    )
    b = PythonBackend(g)
    assert nodes_of(b.dijkstra(0, 2, lengths(g))) == [0, 1, 2]
    assert b.dijkstra_edges(0, 2, lengths(g)) is None


def test_no_state_returns_to_the_start() -> None:
    # Entering S (0) by P → S (1 → 0), S allows S → X (2) and S → Y (3); from X back to S, S → D
    # (4). S X S D (cost 3) passes S twice, so the search takes S Y Z D (cost 30) instead.
    xy = [(0.0, 0.0), (-100.0, 0.0), (0.0, 100.0), (0.0, -100.0), (100.0, 0.0), (100.0, -100.0)]
    edges = [(1, 0, 1.0), (0, 2, 1.0), (2, 0, 1.0), (0, 4, 1.0), (0, 3, 10.0), (3, 5, 10.0)]
    edges += [(5, 4, 10.0)]
    g = make_graph(xy, edges, transitions=[((1, 0), (0, 2)), ((1, 0), (0, 3)), ((2, 0), (0, 4))])
    path = PythonBackend(g).dijkstra_edges(0, 4, lengths(g), entry_edge=edge_id(g, 1, 0))
    assert nodes_of(path) == [0, 3, 5, 4]


def test_a_state_into_the_end_that_cannot_exit_is_a_dead_end() -> None:
    # S (0) → D (1) costs 1, but arriving from S, D lets the route go on only to Q (2), not
    # hand over to D → X (4); from R (3) it can. S D Q R D (cost 4) would pass D twice, so the
    # search does not go on past D, and takes S R D (cost 20).
    xy = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0), (100.0, 100.0), (100.0, -100.0)]
    edges = [(0, 1, 1.0), (1, 2, 1.0), (2, 3, 1.0), (3, 1, 1.0), (0, 3, 19.0), (1, 4, 1.0)]
    g = make_graph(xy, edges, transitions=[((0, 1), (1, 2)), ((3, 1), (1, 4))])
    path = PythonBackend(g).dijkstra_edges(0, 1, lengths(g), exit_edge=edge_id(g, 1, 4))
    assert nodes_of(path) == [0, 3, 1]


def test_max_cost_is_inclusive_and_masks_hold() -> None:
    g = plus()
    b = PythonBackend(g)
    assert nodes_of(b.dijkstra_edges(3, 2, lengths(g), max_cost=300.0)) == [3, 2]
    assert b.dijkstra_edges(3, 2, lengths(g), max_cost=299.9) is None
    assert b.dijkstra_edges(3, 2, lengths(g), banned_edges=_ban(g, (3, 2))) is not None
    forbid = np.zeros(g.n, dtype=bool)
    forbid[[0, 4]] = True
    assert (
        b.dijkstra_edges(3, 2, lengths(g), forbid_nodes=forbid, banned_edges=_ban(g, (3, 2)))
        is None
    )


def test_bad_turn_edges_are_rejected() -> None:
    g = plus()
    b = PythonBackend(g)
    for bad in (-2, g.m, edge_id(g, 0, 1)):  # out of range, or not ending at the start
        with pytest.raises(ValueError, match="entry_edge"):
            b.dijkstra_edges(0, 2, lengths(g), entry_edge=bad)
        with pytest.raises(ValueError, match="entry_edge"):
            b.simple_paths_dfs(0, 2, entry_edge=bad, max_depth=3, max_paths=1)
    with pytest.raises(ValueError, match="exit_edge"):
        b.dijkstra_edges(0, 2, lengths(g), exit_edge=edge_id(g, 0, 1))
    with pytest.raises(ValueError, match="weights"):
        b.dijkstra_edges(0, 2, lengths(g)[:-1])


def test_simple_paths_skip_forbidden_turns() -> None:
    g = plus()
    b = PythonBackend(g)
    turning = {(3, 0, 2), (3, 0, 4)}
    straight = [tuple(p[0].tolist()) for p in b.simple_paths_dfs(3, 2, max_depth=6, max_paths=99)]
    free = PythonBackend(plus(False)).simple_paths_dfs(3, 2, max_depth=6, max_paths=99)
    assert {tuple(p[0].tolist()) for p in free} > set(straight)
    assert not any(t in turning for p in straight for t in zip(p, p[1:], p[2:], strict=False))
    assert (3, 2) in straight and (3, 0, 2) not in straight


def test_the_csr_backend_has_no_turn_kernel_yet() -> None:
    from worst_route.graph.csr_backend import CsrBackend

    g = plus()
    b = CsrBackend(g)
    with pytest.raises(NotImplementedError, match="use --backend python"):
        b.dijkstra_edges(3, 2, lengths(g))
    with pytest.raises(NotImplementedError, match="use --backend python"):
        b.simple_paths_dfs(3, 2, max_depth=4, max_paths=9)
    with pytest.raises(ValueError, match="entry_edge"):  # the reference's checks come first
        b.simple_paths_dfs(0, 2, entry_edge=edge_id(g, 0, 1), max_depth=3, max_paths=1)
    # without rows the turn edges change nothing: the plain kernel answers as the reference
    free = plus(False)
    entry = edge_id(free, 3, 0)
    for backend in (CsrBackend(free), PythonBackend(free)):
        paths = backend.simple_paths_dfs(0, 2, entry_edge=entry, max_depth=4, max_paths=99)
        assert [p[1].tolist() for p in paths] == [
            p[1].tolist()
            for p in PythonBackend(free).simple_paths_dfs(0, 2, max_depth=4, max_paths=99)
        ]
