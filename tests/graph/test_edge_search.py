"""The transition-aware searches: `dijkstra_edges`, its tree and the DFS's turn checks.

Against the semantics in graph/backend.py: the node-state `dijkstra` on graphs without
transition rows (same cost, a path whenever it finds one), a heap-free implementation of the
edge-state tie rule on graphs with random transition tables (the exact path), a networkx search
over the edge states (the cost), and a brute force over networkx's simple paths for the DFS.
`dijkstra_edges` answers a walk, which may enter a node twice; the DFS's paths are simple.
The same again with random bearings and a random set of banned turn classes, classed here by
the rule written out (`Case.turn_class`). The tree search (`dijkstra_edges_tree`) against a
heap-free implementation of its tie rule (the exact states, costs and parents) and networkx over
the edge states (every state at its least cost). The tests of the semantics run on each backend
(`backend_type`); the parity tests then ask `CsrBackend` for the reference's exact bytes, ties
and Nones included.
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

from worst_route.graph.backend import TURN_LEFT, TURN_RIGHT, TURN_U, Path
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.testing import edge_id, grid_graph, make_graph

EXAMPLES = settings(max_examples=200, deadline=None)
TIED_WEIGHTS = st.sampled_from([0.0, 0.5, 1.0, 2.0])  # equal costs common, zero ones included
BACKENDS = {"python": PythonBackend, "csr": CsrBackend}


@pytest.fixture(scope="module", params=list(BACKENDS), ids=list(BACKENDS))
def backend_type(request: pytest.FixtureRequest) -> type[PythonBackend] | type[CsrBackend]:
    """Each backend in turn (module scope: hypothesis runs many examples per test)."""
    return BACKENDS[request.param]


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
    banned_turns: int = 0

    def enterable(self) -> np.ndarray:
        enter = np.ones(self.g.n, dtype=bool) if self.node_mask is None else self.node_mask
        return enter if self.forbid is None else enter & ~self.forbid

    def is_banned(self) -> np.ndarray:
        return np.zeros(self.g.m, dtype=bool) if self.banned is None else self.banned

    def turn_ok(self, e: int, f: int) -> bool:
        g = self.g
        if self.turn_class(e, f) & self.banned_turns:
            return False
        rows = g.trans_node == g.edge_v[e]
        if not rows.any():
            return True
        return bool(((g.trans_in == e) & (g.trans_out == f) & rows).any())

    def turn_class(self, e: int, f: int) -> int:
        """Straight 0, left 1, right 2, U-turn 4, by the bearing change wrapped to (−180, 180]."""
        delta = (int(self.g.edge_bearing_out[f]) - int(self.g.edge_bearing_in[e])) % 360
        delta = delta - 360 if delta > 180 else delta
        if abs(delta) <= 30:
            return 0
        if 30 < delta <= 160:
            return 2
        return 1 if -160 <= delta < -30 else 4

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

    def turns_and_classes(self) -> dict[str, int]:
        return {**self.turns(), "banned_turn_classes": self.banned_turns}

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
    classes: bool = False,
) -> Case:
    """A random multigraph (self-loops, parallel edges), weights, masks, turn edges and, when
    `rows`, a random transition table: some nodes list a random subset of their turns; with
    `classes`, random bearings (the class bands' edges often) and banned turn classes."""
    n = draw(st.integers(2, max_nodes))
    node = st.integers(0, n - 1)
    pairs = draw(st.lists(st.tuples(node, node), min_size=n, max_size=max_edges))
    g = make_graph([(100.0 * i, 0.0) for i in range(n)], [(u, v, 1.0) for u, v in pairs])
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
    banned_turns = 0
    if classes:
        bearing = st.integers(0, 359) | st.sampled_from([0, 30, 160, 180, 200, 330])
        bearings = [
            np.array(draw(st.lists(bearing, min_size=g.m, max_size=g.m)), dtype=np.int16)
            for _ in range(2)
        ]
        g = dataclasses.replace(
            g, edge_bearing_in=bearings[0].reshape(g.m), edge_bearing_out=bearings[1].reshape(g.m)
        )
        banned_turns = draw(st.integers(0, 7))
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
        banned_turns=banned_turns,
    )


def assert_legal(case: Case, path: Path) -> None:
    """A legal walk that enters no node twice."""
    assert_legal_walk(case, path)
    assert len(set(path[0].tolist())) == len(path[0])


def assert_legal_walk(case: Case, path: Path) -> None:
    """A walk from src to dst within the masks whose every turn is allowed."""
    nodes, edges = path
    g = case.g
    assert nodes.dtype == np.int32 and edges.dtype == np.int32
    assert len(edges) == len(nodes) - 1
    assert nodes[0] == case.src and nodes[-1] == case.dst
    assert np.array_equal(g.edge_u[edges], nodes[:-1])
    assert np.array_equal(g.edge_v[edges], nodes[1:])
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
    strictly smaller cost; answer the first terminal state settled. Returns its walk's edges."""
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
            return edges[::-1]
        for f in moves.get(e, []):
            through = c + float(case.weights[f])
            if f not in settled and through <= max_cost and through < cost.get(f, math.inf):
                cost[f], pred[f] = through, e


def state_graph_cost(case: Case) -> float | None:
    """The least cost of any walk of edge states from src to an allowed end (networkx)."""
    if not case.enterable()[case.src]:
        return None
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
def test_dijkstra_edges_follows_the_tie_rule(backend_type, case: Case, max_cost: float) -> None:
    path = backend_type(case.g).dijkstra_edges(
        case.src, case.dst, case.weights, max_cost=max_cost, **case.turns(), **case.masks()
    )
    expected = tie_rule_reference(case, max_cost)
    if expected is None:
        assert path is None
        return
    assert path is not None
    assert path[1].tolist() == expected
    assert_legal_walk(case, path)
    assert float(case.weights[path[1]].sum()) <= max_cost


@EXAMPLES
@given(cases(rows=True, weight=st.integers(1, 9).map(float)))
def test_dijkstra_edges_finds_the_cheapest_legal_walk(backend_type, case: Case) -> None:
    path = backend_type(case.g).dijkstra_edges(
        case.src, case.dst, case.weights, **case.turns(), **case.masks()
    )
    if case.src == case.dst:
        return  # the one-node answer is the tie-rule test's
    best = state_graph_cost(case)
    if best is None:
        assert path is None
    else:
        assert path is not None
        assert_legal_walk(case, path)
        assert float(case.weights[path[1]].sum()) == best


@EXAMPLES
@given(cases(rows=False, weight=st.sampled_from([0.5, 1.0, 2.0])))
def test_without_rows_it_matches_the_node_state_search(backend_type, case: Case) -> None:
    backend = backend_type(case.g)
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
def test_simple_paths_check_every_turn(
    backend_type, case: Case, max_depth: int, max_paths: int
) -> None:
    backend = backend_type(case.g)
    every = backend.simple_paths_dfs(
        case.src,
        case.dst,
        max_depth=max_depth,
        max_paths=10**6,
        **case.turns(),
        **case.masks(),
    )
    found = [tuple(p[1].tolist()) for p in every]
    expected = legal_simple_paths(case, max_depth)
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


def legal_simple_paths(case: Case, max_depth: int) -> list[tuple[int, ...]]:
    """Every simple path's edges, by brute force over networkx's, whose every turn is allowed."""
    enter, banned, g = case.enterable(), case.is_banned(), case.g
    if not (enter[case.src] and enter[case.dst]):
        return []
    if case.src == case.dst:
        conflict = case.entry >= 0 and case.exit >= 0 and not case.turn_ok(case.entry, case.exit)
        return [] if conflict else [()]
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
    return expected


# Banned turn classes: the same semantics, every turn also of a class not banned.


@EXAMPLES
@given(cases(rows=True, classes=True), st.sampled_from([0.0, 1.0, 2.0, 3.0, math.inf]))
def test_banned_turn_classes_follow_the_tie_rule(backend_type, case: Case, max_cost: float) -> None:
    path = backend_type(case.g).dijkstra_edges(
        case.src, case.dst, case.weights, max_cost=max_cost, **case.turns_and_classes(),
        **case.masks(),
    )  # fmt: skip
    expected = tie_rule_reference(case, max_cost)
    assert (None if path is None else path[1].tolist()) == expected
    if path is not None:
        assert_legal_walk(case, path)


@EXAMPLES
@given(cases(rows=True, classes=True, weight=st.integers(1, 9).map(float)))
def test_banned_turn_classes_give_the_cheapest_legal_walk(backend_type, case: Case) -> None:
    path = backend_type(case.g).dijkstra_edges(
        case.src, case.dst, case.weights, **case.turns_and_classes(), **case.masks()
    )
    if case.src == case.dst:
        return
    best = state_graph_cost(case)
    if best is None:
        assert path is None
    else:
        assert path is not None
        assert_legal_walk(case, path)
        assert float(case.weights[path[1]].sum()) == best


@EXAMPLES
@given(cases(rows=True, classes=True, max_nodes=6, max_edges=20), st.integers(0, 6))
def test_simple_paths_skip_banned_turn_classes(backend_type, case: Case, max_depth: int) -> None:
    every = backend_type(case.g).simple_paths_dfs(
        case.src, case.dst, max_depth=max_depth, max_paths=10**6, **case.turns_and_classes(),
        **case.masks(),
    )  # fmt: skip
    found = [tuple(p[1].tolist()) for p in every]
    assert found == sorted(legal_simple_paths(case, max_depth))
    for path in every:
        assert_legal(case, path)


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


def test_a_forbidden_turn_is_driven_round(backend_type) -> None:
    g = plus()
    b = backend_type(g)
    # S → J → E turns at J: the node-state search does it, the edge-state one goes round
    assert nodes_of(b.dijkstra(3, 2, lengths(g))) == [3, 0, 2]
    assert nodes_of(b.dijkstra_edges(3, 2, lengths(g))) == [3, 2]
    assert nodes_of(b.dijkstra_edges(3, 1, lengths(g))) == [3, 0, 1]  # straight on
    assert nodes_of(backend_type(plus(False)).dijkstra_edges(3, 2, lengths(g))) == [3, 0, 2]


def test_the_entry_and_exit_edges_constrain_the_ends(backend_type) -> None:
    g = plus()
    b = backend_type(g)
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


def test_from_a_node_to_itself(backend_type) -> None:
    g = plus()
    b = backend_type(g)
    from_s, to_n, to_e = edge_id(g, 3, 0), edge_id(g, 0, 1), edge_id(g, 0, 2)
    assert nodes_of(b.dijkstra_edges(0, 0, lengths(g))) == [0]
    assert nodes_of(b.dijkstra_edges(0, 0, lengths(g), entry_edge=from_s, exit_edge=to_n)) == [0]
    assert b.dijkstra_edges(0, 0, lengths(g), entry_edge=from_s, exit_edge=to_e) is None
    assert nodes_of(b.dijkstra_edges(0, 0, lengths(g), entry_edge=from_s)) == [0]
    assert b.dijkstra_edges(0, 0, lengths(g), max_cost=-1.0) is None
    dfs = b.simple_paths_dfs(0, 0, entry_edge=from_s, exit_edge=to_e, max_depth=4, max_paths=9)
    assert dfs == []


def test_a_cheapest_walk_through_a_node_twice_is_the_answer(backend_type) -> None:
    # S (0) → A (1) → D (2) is shortest, but arriving from S, A allows only A → B (3); from
    # C (4), only A → D. The cheapest legal walk S A B C A D enters A twice: it is the answer,
    # for the caller to check.
    xy = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0), (150.0, 100.0), (50.0, 100.0)]
    g = make_graph(
        xy,
        [(0, 1, 1.0), (1, 2, 1.0), (1, 3, 1.0), (3, 4, 1.0), (4, 1, 1.0)],
        transitions=[((0, 1), (1, 3)), ((4, 1), (1, 2))],
    )
    b = backend_type(g)
    assert nodes_of(b.dijkstra(0, 2, lengths(g))) == [0, 1, 2]
    assert nodes_of(b.dijkstra_edges(0, 2, lengths(g))) == [0, 1, 3, 4, 1, 2]


def test_no_state_returns_to_the_start(backend_type) -> None:
    # Entering S (0) by P → S (1 → 0), S allows S → X (2) and S → Y (3); from X back to S, S → D
    # (4). S X S D (cost 3) passes S twice, so the search takes S Y Z D (cost 30) instead.
    xy = [(0.0, 0.0), (-100.0, 0.0), (0.0, 100.0), (0.0, -100.0), (100.0, 0.0), (100.0, -100.0)]
    edges = [(1, 0, 1.0), (0, 2, 1.0), (2, 0, 1.0), (0, 4, 1.0), (0, 3, 10.0), (3, 5, 10.0)]
    edges += [(5, 4, 10.0)]
    g = make_graph(xy, edges, transitions=[((1, 0), (0, 2)), ((1, 0), (0, 3)), ((2, 0), (0, 4))])
    path = backend_type(g).dijkstra_edges(0, 4, lengths(g), entry_edge=edge_id(g, 1, 0))
    assert nodes_of(path) == [0, 3, 5, 4]


def test_a_state_into_the_end_that_cannot_exit_is_a_dead_end(backend_type) -> None:
    # S (0) → D (1) costs 1, but arriving from S, D lets the route go on only to Q (2), not
    # hand over to D → X (4); from R (3) it can. S D Q R D (cost 4) would pass D twice, so the
    # search does not go on past D, and takes S R D (cost 20).
    xy = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0), (100.0, 100.0), (100.0, -100.0)]
    edges = [(0, 1, 1.0), (1, 2, 1.0), (2, 3, 1.0), (3, 1, 1.0), (0, 3, 19.0), (1, 4, 1.0)]
    g = make_graph(xy, edges, transitions=[((0, 1), (1, 2)), ((3, 1), (1, 4))])
    path = backend_type(g).dijkstra_edges(0, 1, lengths(g), exit_edge=edge_id(g, 1, 4))
    assert nodes_of(path) == [0, 3, 1]


def test_max_cost_is_inclusive_and_masks_hold(backend_type) -> None:
    g = plus()
    b = backend_type(g)
    assert nodes_of(b.dijkstra_edges(3, 2, lengths(g), max_cost=300.0)) == [3, 2]
    assert b.dijkstra_edges(3, 2, lengths(g), max_cost=299.9) is None
    assert b.dijkstra_edges(3, 2, lengths(g), banned_edges=_ban(g, (3, 2))) is not None
    forbid = np.zeros(g.n, dtype=bool)
    forbid[[0, 4]] = True
    assert (
        b.dijkstra_edges(3, 2, lengths(g), forbid_nodes=forbid, banned_edges=_ban(g, (3, 2)))
        is None
    )


def test_bad_turn_edges_are_rejected(backend_type) -> None:
    g = plus()
    b = backend_type(g)
    for bad in (-2, g.m, edge_id(g, 0, 1)):  # out of range, or not ending at the start
        with pytest.raises(ValueError, match="entry_edge"):
            b.dijkstra_edges(0, 2, lengths(g), entry_edge=bad)
        with pytest.raises(ValueError, match="entry_edge"):
            b.simple_paths_dfs(0, 2, entry_edge=bad, max_depth=3, max_paths=1)
    with pytest.raises(ValueError, match="exit_edge"):
        b.dijkstra_edges(0, 2, lengths(g), exit_edge=edge_id(g, 0, 1))
    with pytest.raises(ValueError, match="weights"):
        b.dijkstra_edges(0, 2, lengths(g)[:-1])


def test_simple_paths_skip_forbidden_turns(backend_type) -> None:
    g = plus()
    b = backend_type(g)
    turning = {(3, 0, 2), (3, 0, 4)}
    straight = [tuple(p[0].tolist()) for p in b.simple_paths_dfs(3, 2, max_depth=6, max_paths=99)]
    free = backend_type(plus(False)).simple_paths_dfs(3, 2, max_depth=6, max_paths=99)
    assert {tuple(p[0].tolist()) for p in free} > set(straight)
    assert not any(t in turning for p in straight for t in zip(p, p[1:], p[2:], strict=False))
    assert (3, 2) in straight and (3, 0, 2) not in straight


def assert_same(paths: list[Path | None]) -> None:
    """Every backend's answer is the reference's, byte for byte (None for None)."""
    reference, *others = paths
    for path in others:
        if reference is None:
            assert path is None
            continue
        assert path is not None
        for ours, theirs in zip(path, reference, strict=True):
            assert ours.dtype == theirs.dtype == np.int32
            assert ours.tobytes() == theirs.tobytes()


@settings(max_examples=300, deadline=None)
@given(
    # with or without a table; weights in {0, 0.5, 1, 2, inf}: equal costs everywhere, zero and
    # unreachable-by-weight edges included; src == dst, turn edges and masks drawn by `cases`
    st.booleans().flatmap(
        lambda rows: cases(
            rows=rows,
            max_nodes=10,
            max_edges=40,
            weight=st.sampled_from([0.0, 0.5, 1.0, 2.0, math.inf]),
        )
    ),
    st.sampled_from([-1.0, 0.0, 1.0, 1.5, 2.0, 3.0, math.inf]),
    st.integers(-1, 7),
    st.sampled_from([-1, 0, 1, 2, 5, 10**6]),
)
def test_the_backends_agree_on_edge_states(
    case: Case, max_cost: float, max_depth: int, max_paths: int
) -> None:
    backends = [make(case.g) for make in BACKENDS.values()]
    assert_same(
        [
            b.dijkstra_edges(
                case.src, case.dst, case.weights, max_cost=max_cost, **case.turns(), **case.masks()
            )
            for b in backends
        ]
    )
    reference, *others = (
        b.simple_paths_dfs(
            case.src,
            case.dst,
            max_depth=max_depth,
            max_paths=max_paths,
            **case.turns(),
            **case.masks(),
        )
        for b in backends
    )
    for paths in others:
        assert len(paths) == len(reference)
        for ours, theirs in zip(paths, reference, strict=True):
            assert_same([theirs, ours])


@settings(max_examples=300, deadline=None)
@given(
    st.booleans().flatmap(
        lambda rows: cases(
            rows=rows,
            max_nodes=10,
            max_edges=40,
            weight=st.sampled_from([0.0, 0.5, 1.0, 2.0, math.inf]),
            classes=True,
        )
    ),
    st.sampled_from([-1.0, 0.0, 1.0, 1.5, 2.0, 3.0, math.inf]),
    st.integers(-1, 7),
    st.sampled_from([-1, 0, 1, 2, 5, 10**6]),
)
def test_the_backends_agree_with_banned_turn_classes(
    case: Case, max_cost: float, max_depth: int, max_paths: int
) -> None:
    backends = [make(case.g) for make in BACKENDS.values()]
    turns = case.turns_and_classes()
    found = [
        b.dijkstra_edges(
            case.src, case.dst, case.weights, max_cost=max_cost, **turns, **case.masks()
        )
        for b in backends
    ]
    if case.banned_turns == 0:  # no class banned: the call without the argument
        found.append(
            backends[0].dijkstra_edges(
                case.src, case.dst, case.weights, max_cost=max_cost, **case.turns(), **case.masks()
            )
        )
    assert_same(found)
    reference, *others = (
        b.simple_paths_dfs(
            case.src, case.dst, max_depth=max_depth, max_paths=max_paths, **turns, **case.masks()
        )
        for b in backends
    )
    for paths in others:
        assert len(paths) == len(reference)
        for ours, theirs in zip(paths, reference, strict=True):
            assert_same([theirs, ours])


def town_with_tables(seed: int) -> GraphData:
    """A 30 × 30 grid with one-way rows and columns, and a transition table at a third of its
    junctions, each allowing a random 60% of its turns."""
    g = grid_graph(30, 30, 100.0, oneway_rows="+.-." * 7 + "..", oneway_cols=".+-" * 10)
    rng = np.random.default_rng(seed)
    table: list[tuple[int, int, int]] = []
    for v in sorted(rng.permutation(g.n)[: g.n // 3].tolist()):
        ins = g.in_edges[g.in_indptr[v] : g.in_indptr[v + 1]].tolist()
        outs = g.out_edges[g.out_indptr[v] : g.out_indptr[v + 1]].tolist()
        table += [(v, a, b) for a in ins for b in outs if rng.random() < 0.6]
    columns = np.array(sorted(table), dtype=np.int32).reshape(-1, 3)
    return dataclasses.replace(
        g, trans_node=columns[:, 0], trans_in=columns[:, 1], trans_out=columns[:, 2]
    )


def test_the_backends_agree_on_a_town_with_tables() -> None:
    # weights of 1, 2 or 3 (ties everywhere), random turn edges, masks and budgets: larger
    # heaps (which grow) and longer paths than the hypothesis graphs, and walks entering a node
    # twice among them
    g = town_with_tables(0)
    rng = np.random.default_rng(1)
    weights = rng.integers(1, 4, size=g.m).astype(np.float64)
    backends = [make(g) for make in BACKENDS.values()]
    found = repeats = 0
    for _ in range(300):
        src, dst = (int(v) for v in rng.integers(g.n, size=2))
        ins = g.in_edges[g.in_indptr[src] : g.in_indptr[src + 1]].tolist()
        outs = g.out_edges[g.out_indptr[dst] : g.out_indptr[dst + 1]].tolist()
        turns = {
            "entry_edge": int(rng.choice([-1, *ins])),
            "exit_edge": int(rng.choice([-1, *outs])),
        }
        node_mask = rng.random(g.n) < 0.95
        forbid = rng.random(g.n) < 0.05
        forbid[[src, dst]] = False
        banned = rng.random(g.m) < 0.05
        max_cost = float(rng.choice([math.inf, 30.0, 60.0]))
        masks = {"node_mask": node_mask, "forbid_nodes": forbid, "banned_edges": banned}
        paths = [
            b.dijkstra_edges(src, dst, weights, max_cost=max_cost, **turns, **masks)
            for b in backends
        ]
        assert_same(paths)
        walk = paths[0]
        found += walk is not None
        if walk is not None and len(set(walk[0].tolist())) < len(walk[0]):
            repeats += 1  # still the cheapest legal walk, against networkx over the edge states
            entry, exit_ = turns["entry_edge"], turns["exit_edge"]
            case = Case(g, weights, src, dst, entry, exit_, node_mask, forbid, banned)
            assert_legal_walk(case, walk)
            assert float(weights[walk[1]].sum()) == state_graph_cost(case)
    assert 100 < found < 300  # both kinds of answer were compared
    assert repeats > 10  # walks entering a node twice among them (21 of 157)


def test_the_backends_agree_on_every_turning_path_of_a_grid() -> None:
    # the simple paths across a 5 × 5 grid whose nodes 6, 12 and 18 allow only straight on,
    # also between two of them entered and left through turn edges: the kernel's output
    # buffers grow many times over
    g = grid_graph(5, 5, 100.0)
    columns = []
    for v in (6, 12, 18):
        ins = g.in_edges[g.in_indptr[v] : g.in_indptr[v + 1]].tolist()
        outs = g.out_edges[g.out_indptr[v] : g.out_indptr[v + 1]].tolist()
        columns += [
            (v, a, b)
            for a in ins
            for b in outs
            if (g.node_x[g.edge_u[a]] == g.node_x[g.edge_v[b]])
            or (g.node_y[g.edge_u[a]] == g.node_y[g.edge_v[b]])
        ]
    table = np.array(sorted(columns), dtype=np.int32)
    g = dataclasses.replace(g, trans_node=table[:, 0], trans_in=table[:, 1], trans_out=table[:, 2])
    forbid = np.zeros(g.n, dtype=bool)
    forbid[13] = True
    for src, dst, turns, masks, max_depth, max_paths in [
        (0, 24, {}, {}, 100, 10**6),
        (6, 18, {"entry_edge": edge_id(g, 5, 6), "exit_edge": edge_id(g, 18, 19)}, {}, 100, 10**6),
        (12, 4, {"entry_edge": edge_id(g, 11, 12)}, {"forbid_nodes": forbid}, 14, 40),
    ]:
        reference, ours = (
            make(g).simple_paths_dfs(
                src, dst, max_depth=max_depth, max_paths=max_paths, **turns, **masks
            )
            for make in BACKENDS.values()
        )
        assert len(ours) == len(reference) >= min(max_paths, 100)
        for a, b in zip(reference, ours, strict=True):
            assert_same([a, b])


def test_the_backends_agree_on_a_town_with_tables_and_banned_turn_classes() -> None:
    # the town's real bearings (0, 90, 180, 270: straight, right, left and U-turns), a set of
    # banned classes drawn per search, from turn edges and without
    g = town_with_tables(2)
    rng = np.random.default_rng(3)
    weights = rng.integers(1, 4, size=g.m).astype(np.float64)
    backends = [make(g) for make in BACKENDS.values()]
    found = 0
    for _ in range(300):
        src, dst = (int(v) for v in rng.integers(g.n, size=2))
        ins = g.in_edges[g.in_indptr[src] : g.in_indptr[src + 1]].tolist()
        outs = g.out_edges[g.out_indptr[dst] : g.out_indptr[dst + 1]].tolist()
        turns = {
            "entry_edge": int(rng.choice([-1, *ins])),
            "exit_edge": int(rng.choice([-1, *outs])),
            "banned_turn_classes": int(rng.integers(8)),
        }
        forbid = rng.random(g.n) < 0.05
        forbid[[src, dst]] = False
        masks = {"forbid_nodes": forbid, "banned_edges": rng.random(g.m) < 0.05}
        paths = [b.dijkstra_edges(src, dst, weights, **turns, **masks) for b in backends]
        assert_same(paths)
        found += paths[0] is not None
        if paths[0] is not None and found % 10 == 1:  # every tenth found path: the DFS too
            near = [
                b.simple_paths_dfs(src, dst, max_depth=8, max_paths=50, **turns, **masks)
                for b in backends
            ]
            assert len(near[0]) == len(near[1])
            for a, b in zip(*near, strict=True):
                assert_same([a, b])
    assert 50 < found < 300  # both kinds of answer were compared


def test_a_banned_turn_class_is_driven_round(backend_type) -> None:
    g = plus(False)  # J allows every turn
    b = backend_type(g)
    from_s = edge_id(g, 3, 0)
    # S → J → W turns left at J, S → J → E right; banned, each goes along the ring instead
    assert nodes_of(b.dijkstra_edges(3, 4, lengths(g))) == [3, 0, 4]
    assert nodes_of(b.dijkstra_edges(3, 4, lengths(g), banned_turn_classes=TURN_LEFT)) == [3, 4]
    assert nodes_of(b.dijkstra_edges(3, 2, lengths(g), banned_turn_classes=TURN_RIGHT)) == [3, 2]
    # arriving at J from S, never turning left: round three rights, J → E → S → W
    no_left = {"entry_edge": from_s, "banned_turn_classes": TURN_LEFT | TURN_U}
    assert nodes_of(b.dijkstra_edges(0, 4, lengths(g), entry_edge=from_s)) == [0, 4]
    assert nodes_of(b.dijkstra_edges(0, 4, lengths(g), **no_left)) == [0, 2, 3, 4]
    # J → S after S → J is a U-turn: allowed, unless U-turns are banned
    assert nodes_of(b.dijkstra_edges(0, 3, lengths(g), entry_edge=from_s)) == [0, 3]
    assert nodes_of(b.dijkstra_edges(0, 3, lengths(g), **no_left)) == [0, 2, 3]
    paths = b.simple_paths_dfs(0, 4, max_depth=4, max_paths=99, **no_left)
    assert sorted(p[0].tolist() for p in paths) == [[0, 1, 2, 3, 4], [0, 2, 3, 4]]  # rights only
    to_w = edge_id(g, 0, 4)
    assert b.dijkstra_edges(0, 0, lengths(g), entry_edge=from_s, exit_edge=to_w) is not None
    assert b.dijkstra_edges(0, 0, lengths(g), exit_edge=to_w, **no_left) is None


def test_bad_banned_turn_classes_are_rejected(backend_type) -> None:
    g = plus()
    b = backend_type(g)
    for bad in (-1, 8):
        with pytest.raises(ValueError, match="banned_turn_classes"):
            b.dijkstra_edges(0, 2, lengths(g), banned_turn_classes=bad)
        with pytest.raises(ValueError, match="banned_turn_classes"):
            b.simple_paths_dfs(0, 2, max_depth=3, max_paths=1, banned_turn_classes=bad)


# The tree search (`dijkstra_edges_tree`): the same states and moves without a destination, every
# state settled in (cost, edge id) order, final states never expanded.


def tree_moves(case: Case, final: np.ndarray) -> Iterator[tuple[int, int]]:
    """`Case.state_moves` without a destination, a final state never moving on."""
    g, enter, banned = case.g, case.enterable(), case.is_banned()
    for f in case.out_edges(case.src):
        w = int(g.edge_v[f])
        if w == case.src or banned[f] or not enter[w]:
            continue
        if case.entry < 0 or case.turn_ok(case.entry, f):
            yield -1, f
    for e in range(g.m):
        v = int(g.edge_v[e])
        if final[e] or v == case.src or not enter[v] or banned[e]:
            continue
        for f in case.out_edges(v):
            w = int(g.edge_v[f])
            if w in (case.src, v) or banned[f] or not enter[w]:
                continue
            if case.turn_ok(e, f):
                yield e, f


def tree_reference(
    case: Case, final: np.ndarray, max_cost: float
) -> tuple[list[int], list[float], list[int]]:
    """The tree's semantics without a heap: settle the open state of least (cost, edge id),
    record its parent's position, relax its moves in ascending edge id (none for a final state),
    replacing a predecessor only on a strictly smaller cost."""
    if max_cost < 0 or not case.enterable()[case.src]:
        return [], [], []
    moves: dict[int, list[int]] = {}
    for e, f in tree_moves(case, final):
        moves.setdefault(e, []).append(f)
    cost: dict[int, float] = {}
    pred: dict[int, int] = {}
    for f in moves.get(-1, []):
        if case.weights[f] <= max_cost:
            cost[f], pred[f] = float(case.weights[f]), -1
    edges: list[int] = []
    costs: list[float] = []
    parents: list[int] = []
    slot: dict[int, int] = {}
    while True:
        open_states = [(c, e) for e, c in cost.items() if e not in slot]
        if not open_states:
            return edges, costs, parents
        c, e = min(open_states)
        slot[e] = len(edges)
        edges.append(e)
        costs.append(c)
        parents.append(slot[pred[e]] if pred[e] >= 0 else -1)
        for f in moves.get(e, []):
            through = c + float(case.weights[f])
            if f not in slot and through <= max_cost and through < cost.get(f, math.inf):
                cost[f], pred[f] = through, e


def final_edges(size: int) -> st.SearchStrategy[np.ndarray | None]:
    return optional_mask(size, default=False)


@EXAMPLES
@given(
    st.booleans()
    .flatmap(lambda classes: cases(rows=True, classes=classes))
    .flatmap(lambda case: st.tuples(st.just(case), final_edges(case.g.m))),
    st.sampled_from([-1.0, 0.0, 1.0, 1.5, 2.0, 3.0, math.inf]),
)
def test_dijkstra_edges_tree_follows_the_tie_rule(
    backend_type, drawn: tuple[Case, np.ndarray | None], max_cost: float
) -> None:
    case, final = drawn
    edges, costs, parents = backend_type(case.g).dijkstra_edges_tree(
        case.src,
        case.weights,
        entry_edge=case.entry,
        final_edges=final,
        max_cost=max_cost,
        banned_turn_classes=case.banned_turns,
        **case.masks(),
    )
    finals = np.zeros(case.g.m, dtype=bool) if final is None else final
    expected = tree_reference(case, finals, max_cost)
    assert (edges.dtype, costs.dtype, parents.dtype) == (np.int32, np.float64, np.int32)
    assert (edges.tolist(), costs.tolist(), parents.tolist()) == expected
    for k, (e, p) in enumerate(zip(edges.tolist(), parents.tolist(), strict=True)):
        assert costs[k] <= max_cost
        if p < 0:  # a start state: leaves src, its cost its own weight
            assert case.g.edge_u[e] == case.src and costs[k] == case.weights[e]
        else:  # a move from its parent, settled before it, which is not final
            assert p < k and not finals[edges[p]]
            assert case.g.edge_v[edges[p]] == case.g.edge_u[e]
            assert case.turn_ok(int(edges[p]), e)
            assert costs[k] == costs[p] + case.weights[e]


@EXAMPLES
@given(
    cases(rows=True, classes=True, weight=st.integers(1, 9).map(float)).flatmap(
        lambda case: st.tuples(st.just(case), final_edges(case.g.m))
    ),
    st.sampled_from([4.0, 9.0, math.inf]),
)
def test_dijkstra_edges_tree_settles_every_state_at_its_least_cost(
    backend_type, drawn: tuple[Case, np.ndarray | None], max_cost: float
) -> None:
    case, final = drawn
    finals = np.zeros(case.g.m, dtype=bool) if final is None else final
    edges, costs, _ = backend_type(case.g).dijkstra_edges_tree(
        case.src,
        case.weights,
        entry_edge=case.entry,
        final_edges=final,
        max_cost=max_cost,
        banned_turn_classes=case.banned_turns,
        **case.masks(),
    )
    graph = nx.DiGraph()
    graph.add_node(-1)
    for e, f in tree_moves(case, finals):
        graph.add_edge(e, f, weight=float(case.weights[f]))
    least = (
        {} if not case.enterable()[case.src] else nx.single_source_dijkstra_path_length(graph, -1)
    )
    expected = {e: c for e, c in least.items() if e >= 0 and c <= max_cost}
    assert dict(zip(edges.tolist(), costs.tolist(), strict=True)) == expected


@settings(max_examples=300, deadline=None)
@given(
    st.booleans()
    .flatmap(
        lambda rows: cases(
            rows=rows,
            max_nodes=10,
            max_edges=40,
            weight=st.sampled_from([0.0, 0.5, 1.0, 2.0, math.inf]),
            classes=True,
        )
    )
    .flatmap(lambda case: st.tuples(st.just(case), final_edges(case.g.m))),
    st.sampled_from([-1.0, 0.0, 1.0, 1.5, 2.0, 3.0, math.inf]),
)
def test_the_backends_agree_on_edge_trees(
    drawn: tuple[Case, np.ndarray | None], max_cost: float
) -> None:
    case, final = drawn
    reference, ours = (
        make(case.g).dijkstra_edges_tree(
            case.src,
            case.weights,
            entry_edge=case.entry,
            final_edges=final,
            max_cost=max_cost,
            banned_turn_classes=case.banned_turns,
            **case.masks(),
        )
        for make in BACKENDS.values()
    )
    for a, b in zip(reference, ours, strict=True):
        assert a.dtype == b.dtype and a.tobytes() == b.tobytes()


def test_the_backends_agree_on_edge_trees_of_a_town_with_tables() -> None:
    # larger trees (heaps that grow), with ties, turn edges, masks, final edges and classes
    g = town_with_tables(4)
    rng = np.random.default_rng(5)
    weights = rng.integers(1, 4, size=g.m).astype(np.float64)
    backends = [make(g) for make in BACKENDS.values()]
    sizes = []
    for _ in range(100):
        src = int(rng.integers(g.n))
        ins = g.in_edges[g.in_indptr[src] : g.in_indptr[src + 1]].tolist()
        forbid = rng.random(g.n) < 0.05
        forbid[src] = False
        kwargs = {
            "entry_edge": int(rng.choice([-1, *ins])),
            "forbid_nodes": forbid,
            "banned_edges": rng.random(g.m) < 0.05,
            "final_edges": rng.random(g.m) < 0.2,
            "max_cost": float(rng.choice([math.inf, 10.0, 30.0])),
            "banned_turn_classes": int(rng.integers(8)),
        }
        reference, ours = (b.dijkstra_edges_tree(src, weights, **kwargs) for b in backends)
        for a, b in zip(reference, ours, strict=True):
            assert a.tobytes() == b.tobytes()
        sizes.append(len(reference[0]))
    assert max(sizes) > 500 and min(sizes) < 50  # big and small trees compared


def test_a_final_state_is_settled_but_not_expanded(backend_type) -> None:
    g = plus(False)
    b = backend_type(g)
    final = np.zeros(g.m, dtype=bool)
    final[edge_id(g, 0, 1)] = True  # J → N ends there
    edges, costs, parents = b.dijkstra_edges_tree(0, lengths(g), final_edges=final)
    heads = [int(g.edge_v[e]) for e in edges.tolist()]
    assert heads[:4] == [1, 2, 3, 4] and costs[:4].tolist() == [100.0] * 4
    # N is reached from J and never left from there: no state's parent is J → N
    assert 0 not in parents.tolist()
    # N → E is reached through the ring instead, J → E → N → E at 700 m (E → N settles before
    # W → N, an equal cost): a tree's path may enter a node twice
    k = edges.tolist().index(edge_id(g, 1, 2))
    assert costs[k] == 700.0 and edges[parents[k]] == edge_id(g, 2, 1)
    assert edges[parents[parents[k]]] == edge_id(g, 0, 2)
    # no state returns to J, the start
    assert 0 not in heads


def test_a_tree_turns_only_where_the_table_allows(backend_type) -> None:
    g = plus()  # J allows only straight on
    from_s = edge_id(g, 3, 0)
    edges, _, parents = backend_type(g).dijkstra_edges_tree(0, lengths(g), entry_edge=from_s)
    assert int(g.edge_v[edges[0]]) == 1  # arriving from S, only N
    assert parents[0] == -1 and (parents[1:] >= 0).all()


def test_an_empty_tree(backend_type) -> None:
    g = plus()
    b = backend_type(g)
    forbid = np.zeros(g.n, dtype=bool)
    forbid[0] = True
    for kwargs in ({"forbid_nodes": forbid}, {"max_cost": -1.0}):
        edges, costs, parents = b.dijkstra_edges_tree(0, lengths(g), **kwargs)
        assert (len(edges), len(costs), len(parents)) == (0, 0, 0)
        assert (edges.dtype, costs.dtype, parents.dtype) == (np.int32, np.float64, np.int32)
    edges, costs, _ = b.dijkstra_edges_tree(0, lengths(g), max_cost=100.0)  # inclusive
    assert costs.tolist() == [100.0] * 4


def test_bad_tree_arguments_are_rejected(backend_type) -> None:
    g = plus()
    b = backend_type(g)
    with pytest.raises(ValueError, match="entry_edge"):
        b.dijkstra_edges_tree(0, lengths(g), entry_edge=edge_id(g, 0, 1))  # does not end at 0
    with pytest.raises(ValueError, match="final_edges"):
        b.dijkstra_edges_tree(0, lengths(g), final_edges=np.zeros(g.m, dtype=np.uint8))
    with pytest.raises(ValueError, match="banned_turn_classes"):
        b.dijkstra_edges_tree(0, lengths(g), banned_turn_classes=8)
    with pytest.raises(ValueError, match="src"):
        b.dijkstra_edges_tree(g.n, lengths(g))
