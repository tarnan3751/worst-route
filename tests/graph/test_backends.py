"""The backends against networkx (the oracle), the semantics in graph/backend.py and each other.

Every test of the reference's semantics runs on each backend (`backend_type`); the parity tests
then ask `CsrBackend` for the reference's exact bytes, ties and Nones included.
"""

import math
import sys
from dataclasses import dataclass

import networkx as nx
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from worst_route.config import DEFAULT_BACKEND
from worst_route.graph.backend import GraphBackend, Path
from worst_route.graph.backends import make_backend
from worst_route.graph.csr_backend import CsrBackend
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.testing import edge_id, grid_graph, make_graph

EXAMPLES = settings(max_examples=200, deadline=None)
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
    node_mask: np.ndarray | None
    forbid: np.ndarray | None
    banned: np.ndarray | None

    def enterable(self) -> np.ndarray:
        enter = np.ones(self.g.n, dtype=bool) if self.node_mask is None else self.node_mask
        return enter if self.forbid is None else enter & ~self.forbid

    def oracle(self) -> nx.MultiDiGraph:
        """The graph the masks leave, as networkx sees it; edge keys are edge ids."""
        enter = self.enterable()
        banned = np.zeros(self.g.m, dtype=bool) if self.banned is None else self.banned
        graph = nx.MultiDiGraph()
        graph.add_nodes_from(int(v) for v in np.flatnonzero(enter))
        for e in range(self.g.m):
            u, v = int(self.g.edge_u[e]), int(self.g.edge_v[e])
            if enter[u] and enter[v] and not banned[e]:
                graph.add_edge(u, v, key=e, weight=float(self.weights[e]))
        return graph

    def masks(self) -> dict[str, np.ndarray | None]:
        return {
            "node_mask": self.node_mask,
            "forbid_nodes": self.forbid,
            "banned_edges": self.banned,
        }


def optional_mask(size: int, *, default: bool) -> st.SearchStrategy[np.ndarray | None]:
    """None, or a mask that is `default` except at up to a third of its positions."""

    def build(flipped: set[int]) -> np.ndarray:
        mask = np.full(size, default, dtype=bool)
        mask[sorted(flipped)] = not default
        return mask

    flipped = st.sets(st.integers(0, size - 1), max_size=size // 3) if size else st.just(set())
    return st.none() | flipped.map(build)


WHOLE_WEIGHTS = st.integers(1, 9).map(float)


@st.composite
def cases(
    draw: st.DrawFn,
    max_nodes: int,
    max_edges: int,
    *,
    min_nodes: int = 1,
    edges_per_node: int = 1,
    weight: st.SearchStrategy[float] = WHOLE_WEIGHTS,
    distinct_ends: bool = False,
) -> Case:
    """A random multigraph (self-loops and parallel edges included), weights, masks."""
    n = draw(st.integers(min_nodes, max_nodes))
    node = st.integers(0, n - 1)
    at_least = min(edges_per_node * n, max_edges)
    pairs = draw(st.lists(st.tuples(node, node), min_size=at_least, max_size=max_edges))
    lonlat = [(-122.0 + 0.001 * i, 45.0) for i in range(n)]
    g = make_graph(
        [(100.0 * i, 0.0) for i in range(n)], [(u, v, 1.0) for u, v in pairs], lonlat=lonlat
    )
    weights = np.array(draw(st.lists(weight, min_size=g.m, max_size=g.m)), dtype=np.float64)
    src = draw(node)
    return Case(
        g=g,
        weights=weights.reshape(g.m),
        src=src,
        dst=(src + draw(st.integers(1, n - 1))) % n if distinct_ends else draw(node),
        node_mask=draw(optional_mask(n, default=True)),
        forbid=draw(optional_mask(n, default=False)),
        banned=draw(optional_mask(g.m, default=False)),
    )


def edge_sequences(paths: list[Path]) -> list[tuple[int, ...]]:
    return [tuple(edges.tolist()) for _, edges in paths]


def assert_legal(case: Case, path: Path) -> None:
    nodes, edges = path
    g = case.g
    assert len(edges) == len(nodes) - 1
    assert nodes[0] == case.src and nodes[-1] == case.dst
    assert np.array_equal(g.edge_u[edges], nodes[:-1]) and np.array_equal(
        g.edge_v[edges], nodes[1:]
    )
    assert len(set(nodes.tolist())) == len(nodes)
    assert case.enterable()[nodes].all()
    assert case.banned is None or not case.banned[edges].any()


def unique_cheapest_edges(case: Case, graph: nx.MultiDiGraph) -> list[int] | None:
    """The edges of the cheapest src → dst path if exactly one exists (weights are positive)."""
    cost = nx.single_source_dijkstra_path_length(graph, case.src)
    count = {case.src: 1}
    via: dict[int, int] = {}
    for v in sorted(cost, key=cost.__getitem__):
        if v == case.src:
            continue
        tight = [
            (u, e)
            for u, _, e, w in graph.in_edges(v, keys=True, data="weight")
            if u in cost and cost[u] + w == cost[v]
        ]
        count[v] = sum(count[u] for u, _ in tight)
        via[v] = tight[0][1]
    if count[case.dst] != 1:
        return None
    edges, v = [], case.dst
    while v != case.src:
        edges.append(via[v])
        v = int(case.g.edge_u[via[v]])
    return edges[::-1]


@EXAMPLES
@given(cases(max_nodes=10, max_edges=30))
def test_dijkstra_matches_networkx(backend_type, case: Case) -> None:
    backend = backend_type(case.g)
    path = backend.dijkstra(case.src, case.dst, case.weights, **case.masks())
    graph = case.oracle()
    reachable = case.src in graph and case.dst in graph and nx.has_path(graph, case.src, case.dst)
    if not reachable:
        assert path is None
        return
    assert path is not None
    assert_legal(case, path)
    cost = float(case.weights[path[1]].sum())
    assert cost == nx.dijkstra_path_length(graph, case.src, case.dst)
    unique = unique_cheapest_edges(case, graph) if case.src != case.dst else []
    if unique is not None:
        assert path[1].tolist() == unique
    # max_cost: the optimum is found at its own cost and nowhere below it
    at_cost = backend.dijkstra(case.src, case.dst, case.weights, max_cost=cost, **case.masks())
    assert at_cost is not None and np.array_equal(at_cost[1], path[1])
    if case.src != case.dst:
        assert (
            backend.dijkstra(case.src, case.dst, case.weights, max_cost=cost - 0.5, **case.masks())
            is None
        )


def tie_rule_dijkstra(case: Case, max_cost: float) -> list[int] | None:
    """The tie rule of graph/backend.py, written without a heap: settle the open node with the
    least (cost, node), relax its out-edges in ascending edge id, and replace a predecessor
    only on a strictly smaller cost. Returns the path's edges."""
    g, enter = case.g, case.enterable()
    banned = np.zeros(g.m, dtype=bool) if case.banned is None else case.banned
    if max_cost < 0 or not (enter[case.src] and enter[case.dst]):
        return None
    cost: dict[int, float] = {case.src: 0.0}
    pred: dict[int, int] = {}
    settled: set[int] = set()
    while True:
        open_nodes = [(c, v) for v, c in cost.items() if v not in settled]
        if not open_nodes:
            return None
        c, v = min(open_nodes)
        if v == case.dst:
            break
        settled.add(v)
        for e in sorted(g.out_edges[g.out_indptr[v] : g.out_indptr[v + 1]].tolist()):
            w = int(g.edge_v[e])
            if w in settled or banned[e] or not enter[w]:
                continue
            through = c + float(case.weights[e])
            if through <= max_cost and through < cost.get(w, math.inf):
                cost[w], pred[w] = through, e
    edges: list[int] = []
    while v != case.src:
        edges.append(pred[v])
        v = int(g.edge_u[pred[v]])
    return edges[::-1]


@EXAMPLES
@given(
    # weights in {0, 0.5, 1, 2} on dense graphs make equal costs common, zero-cost edges included
    cases(
        max_nodes=8,
        max_edges=32,
        min_nodes=2,
        edges_per_node=3,
        weight=st.sampled_from([0.0, 0.5, 1.0, 2.0]),
        distinct_ends=True,
    ),
    st.sampled_from([0.0, 1.0, 1.5, 2.0, 3.0, math.inf]),
)
def test_dijkstra_follows_the_tie_rule(backend_type, case: Case, max_cost: float) -> None:
    path = backend_type(case.g).dijkstra(
        case.src, case.dst, case.weights, max_cost=max_cost, **case.masks()
    )
    expected = tie_rule_dijkstra(case, max_cost)
    if expected is None:
        assert path is None
        return
    assert path is not None
    assert path[1].tolist() == expected
    assert_legal(case, path)


@EXAMPLES
@given(cases(max_nodes=6, max_edges=24), st.integers(0, 6).map(lambda d: 6 - d), st.integers(1, 20))
def test_simple_paths_match_networkx(
    backend_type, case: Case, max_depth: int, max_paths: int
) -> None:
    backend = backend_type(case.g)
    every = backend.simple_paths_dfs(
        case.src, case.dst, max_depth=max_depth, max_paths=10**6, **case.masks()
    )
    graph = case.oracle()
    if case.src in graph and case.dst in graph:
        expected = {
            tuple(e for _, _, e in p)
            for p in nx.all_simple_edge_paths(graph, case.src, case.dst, cutoff=max_depth)
        }
    else:
        expected = set()
    found = edge_sequences(every)
    assert set(found) == expected and len(found) == len(expected)
    assert found == sorted(found)
    for path in every:
        assert_legal(case, path)
    first = backend.simple_paths_dfs(
        case.src, case.dst, max_depth=max_depth, max_paths=max_paths, **case.masks()
    )
    assert edge_sequences(first) == found[:max_paths]


@EXAMPLES
@given(cases(max_nodes=12, max_edges=36))
def test_largest_scc_matches_networkx(backend_type, case: Case) -> None:
    edge_mask = None if case.banned is None else ~case.banned  # keep most edges
    mask = backend_type(case.g).largest_scc_mask(edge_mask)
    graph = nx.DiGraph()
    graph.add_nodes_from(range(case.g.n))
    keep = np.ones(case.g.m, dtype=bool) if edge_mask is None else edge_mask
    graph.add_edges_from(
        zip(case.g.edge_u[keep].tolist(), case.g.edge_v[keep].tolist(), strict=True)
    )
    components = sorted(nx.strongly_connected_components(graph), key=len, reverse=True)
    assert mask.dtype == np.bool_ and mask.shape == (case.g.n,)
    assert mask.sum() == len(components[0])
    assert set(np.flatnonzero(mask).tolist()) in components
    if len(components) == 1 or len(components[1]) < len(components[0]):
        assert set(np.flatnonzero(mask).tolist()) == components[0]


def test_the_backends_are_graph_backends() -> None:
    for backend, name in [(PythonBackend(GRID), "python"), (CsrBackend(GRID), "csr")]:
        typed: GraphBackend = backend
        assert typed.data.n == 9 and typed.name == name


def test_make_backend_makes_the_one_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DEFAULT_BACKEND == "auto"
    assert isinstance(make_backend(GRID), CsrBackend)  # numba imports here
    assert isinstance(make_backend(GRID, "csr"), CsrBackend)
    assert isinstance(make_backend(GRID, "python"), PythonBackend)
    with pytest.raises(ValueError, match="unknown backend 'numpy'"):
        make_backend(GRID, "numpy")  # type: ignore[arg-type]
    monkeypatch.setitem(sys.modules, "worst_route.graph.csr_backend", None)  # as if no numba
    assert make_backend(GRID).name == "python"
    with pytest.raises(ImportError):
        make_backend(GRID, "csr")


def test_auto_is_csr_on_a_build_with_turn_tables_too() -> None:
    # the CSR kernels search transition tables, so auto takes them on every build
    g = grid_graph(3, 3, 100.0)
    tables = make_graph(
        [(100.0 * (v % 3), 100.0 * (v // 3)) for v in range(9)],
        [(int(u), int(v), 100.0) for u, v in zip(g.edge_u, g.edge_v, strict=True)],
        transitions=[((3, 4), (4, 5)), ((5, 4), (4, 3)), ((1, 4), (4, 7)), ((7, 4), (4, 1))],
    )
    assert make_backend(g).name == "csr"
    assert make_backend(tables).name == "csr"


def test_ties_go_to_the_first_node_settled_then_the_lowest_edge(backend_type) -> None:
    # A diamond 0 → {1, 2} → 3 with equal costs: node 1 settles before node 2, so 1 → 3 wins.
    nodes = [(0.0, 0.0), (100.0, 100.0), (100.0, -100.0), (200.0, 0.0)]
    g = make_graph(nodes, [(0, 1, 1.0), (0, 2, 1.0), (1, 3, 1.0), (2, 3, 1.0)])
    path = backend_type(g).dijkstra(0, 3, g.edge_length_m.astype(np.float64))
    assert path is not None
    assert path[0].tolist() == [0, 1, 3]
    assert path[1].tolist() == [edge_id(g, 0, 1), edge_id(g, 1, 3)]


def test_equal_parallel_edges_take_the_lower_id(backend_type) -> None:
    g = make_graph([(0.0, 0.0), (100.0, 0.0)], [(0, 1, 5.0), (0, 1, 5.0)])
    path = backend_type(g).dijkstra(0, 1, g.edge_length_m.astype(np.float64))
    assert path is not None and path[1].tolist() == [0]


def test_a_later_equal_cost_never_replaces_a_predecessor(backend_type) -> None:
    # 0 → 2 directly (2) and 0 → 1 → 2 (1 + 1): 2 is first reached by the direct edge.
    g = make_graph(
        [(0.0, 0.0), (50.0, 50.0), (100.0, 0.0)], [(0, 1, 1.0), (0, 2, 2.0), (1, 2, 1.0)]
    )
    path = backend_type(g).dijkstra(0, 2, g.edge_length_m.astype(np.float64))
    assert path is not None
    assert path[0].tolist() == [0, 2] and path[1].tolist() == [edge_id(g, 0, 2)]


GRID = grid_graph(3, 3, 100.0)  # node r * 3 + c; 0 south-west, 8 north-east
LENGTHS = GRID.edge_length_m.astype(np.float64)


def nodes_of(path: Path | None) -> list[int] | None:
    return None if path is None else path[0].tolist()


def test_forbidden_nodes_and_banned_edges_are_avoided(backend_type) -> None:
    b = backend_type(GRID)
    forbid = np.zeros(9, dtype=bool)
    forbid[[1, 4]] = True
    assert nodes_of(b.dijkstra(0, 2, LENGTHS, forbid_nodes=forbid)) == [0, 3, 6, 7, 8, 5, 2]
    banned = np.zeros(GRID.m, dtype=bool)
    banned[[edge_id(GRID, 0, 1), edge_id(GRID, 3, 4)]] = True
    assert nodes_of(b.dijkstra(0, 2, LENGTHS, forbid_nodes=forbid, banned_edges=banned)) == [
        0,
        3,
        6,
        7,
        8,
        5,
        2,
    ]
    banned[edge_id(GRID, 6, 7)] = True
    assert b.dijkstra(0, 2, LENGTHS, forbid_nodes=forbid, banned_edges=banned) is None
    mask = np.ones(9, dtype=bool)
    mask[[3, 4, 5]] = False
    assert b.dijkstra(0, 8, LENGTHS, node_mask=mask) is None
    assert nodes_of(b.dijkstra(0, 2, LENGTHS, node_mask=mask)) == [0, 1, 2]


def test_endpoints_obey_the_masks(backend_type) -> None:
    b = backend_type(GRID)
    forbid = np.zeros(9, dtype=bool)
    forbid[0] = True
    assert b.dijkstra(0, 8, LENGTHS, forbid_nodes=forbid) is None
    assert b.dijkstra(8, 0, LENGTHS, forbid_nodes=forbid) is None
    assert b.simple_paths_dfs(0, 8, forbid_nodes=forbid, max_depth=8, max_paths=10) == []
    assert b.dijkstra(0, 0, LENGTHS, forbid_nodes=forbid) is None


def test_source_equals_destination(backend_type) -> None:
    b = backend_type(GRID)
    path = b.dijkstra(4, 4, LENGTHS)
    assert path is not None and path[0].tolist() == [4] and path[1].tolist() == []
    assert nodes_of(b.dijkstra(4, 4, LENGTHS, max_cost=0.0)) == [4]
    assert b.dijkstra(4, 4, LENGTHS, max_cost=-1.0) is None
    assert b.dijkstra(4, 5, LENGTHS, max_cost=-1.0) is None
    assert path[0].dtype == np.int32 and path[1].dtype == np.int32
    paths = b.simple_paths_dfs(4, 4, max_depth=3, max_paths=5)
    assert [(p[0].tolist(), p[1].tolist()) for p in paths] == [([4], [])]


def test_max_cost_cuts_the_search(backend_type) -> None:
    b = backend_type(GRID)
    assert nodes_of(b.dijkstra(0, 8, LENGTHS, max_cost=400.0)) is not None
    assert b.dijkstra(0, 8, LENGTHS, max_cost=399.9) is None
    assert nodes_of(b.dijkstra(0, 1, LENGTHS, max_cost=100.0)) == [0, 1]


def test_one_way_streets_are_respected(backend_type) -> None:
    g = grid_graph(2, 3, 100.0, oneway_rows="+-", oneway_cols="...")
    b = backend_type(g)
    lengths = g.edge_length_m.astype(np.float64)
    assert nodes_of(b.dijkstra(0, 2, lengths)) == [0, 1, 2]
    assert nodes_of(b.dijkstra(2, 0, lengths)) == [2, 5, 4, 3, 0]


def test_bad_arguments_are_rejected(backend_type) -> None:
    b = backend_type(GRID)
    with pytest.raises(ValueError, match="weights"):
        b.dijkstra(0, 8, LENGTHS[:-1])
    with pytest.raises(ValueError, match="weights"):
        b.dijkstra(0, 8, LENGTHS.astype(np.float32))
    for bad in (np.nan, -1.0):
        weights = LENGTHS.copy()
        weights[3] = bad
        with pytest.raises(ValueError, match="weights: must be non-negative and not NaN"):
            b.dijkstra(0, 8, weights)
    for src, dst in [(-1, 8), (0, -1), (9, 0), (0, 9)]:
        with pytest.raises(ValueError, match=r"src, dst: node ids must lie in \[0, 9\)"):
            b.dijkstra(src, dst, LENGTHS)
        with pytest.raises(ValueError, match=r"src, dst: node ids must lie in \[0, 9\)"):
            b.simple_paths_dfs(src, dst, max_depth=4, max_paths=1)
    with pytest.raises(ValueError, match="forbid_nodes"):
        b.dijkstra(0, 8, LENGTHS, forbid_nodes=np.zeros(9, dtype=np.uint8))
    with pytest.raises(ValueError, match="node_mask"):
        b.simple_paths_dfs(0, 8, node_mask=np.ones(8, dtype=bool), max_depth=4, max_paths=1)
    with pytest.raises(ValueError, match="edge_mask"):
        b.largest_scc_mask(np.ones(3, dtype=bool))


def test_simple_paths_on_a_grid_match_networkx(backend_type) -> None:
    b = backend_type(GRID)
    paths = b.simple_paths_dfs(0, 8, max_depth=100, max_paths=10**6)
    graph = nx.DiGraph(zip(GRID.edge_u.tolist(), GRID.edge_v.tolist(), strict=True))
    expected = {tuple(p) for p in nx.all_simple_paths(graph, 0, 8)}
    found = [tuple(p[0].tolist()) for p in paths]
    assert set(found) == expected and len(found) == len(expected) == 12
    assert edge_sequences(paths) == sorted(edge_sequences(paths))


def test_simple_paths_depth_and_count_limits(backend_type) -> None:
    b = backend_type(GRID)
    every = b.simple_paths_dfs(0, 8, max_depth=100, max_paths=10**6)
    for depth in range(9):
        within = b.simple_paths_dfs(0, 8, max_depth=depth, max_paths=10**6)
        assert edge_sequences(within) == [s for s in edge_sequences(every) if len(s) <= depth]
    assert len(b.simple_paths_dfs(0, 8, max_depth=4, max_paths=10**6)) == 6
    assert b.simple_paths_dfs(0, 8, max_depth=3, max_paths=10**6) == []
    assert (
        edge_sequences(b.simple_paths_dfs(0, 8, max_depth=100, max_paths=5))
        == edge_sequences(every)[:5]
    )
    assert b.simple_paths_dfs(0, 8, max_depth=100, max_paths=0) == []


def test_largest_scc_drops_one_way_stubs_and_masked_edges(backend_type) -> None:
    # The 2 × 2 square 0-1-3-2 two-way, a one-way stub 4 → 0 and a dead end 1 → 5.
    nodes = [(0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0), (-100.0, 0.0), (200.0, 0.0)]
    two_way = [(0, 1), (1, 3), (3, 2), (2, 0)]
    edges = [(a, b, 100.0) for a, b in two_way] + [(b, a, 100.0) for a, b in two_way]
    g = make_graph(nodes, [*edges, (4, 0, 100.0), (1, 5, 100.0)])
    b = backend_type(g)
    assert b.largest_scc_mask().tolist() == [True, True, True, True, False, False]
    keep = np.ones(g.m, dtype=bool)
    keep[[edge_id(g, 0, 1), edge_id(g, 0, 2)]] = False  # node 0 can no longer be left
    assert b.largest_scc_mask(keep).tolist() == [False, True, True, True, False, False]


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
    # dense graphs and weights in {0, 0.5, 1, 2, inf}: equal costs everywhere, zero and
    # unreachable-by-weight edges included; src == dst included
    cases(
        max_nodes=10,
        max_edges=40,
        edges_per_node=2,
        weight=st.sampled_from([0.0, 0.5, 1.0, 2.0, math.inf]),
    ),
    st.sampled_from([-1.0, 0.0, 1.0, 1.5, 2.0, 3.0, math.inf]),
    st.integers(-1, 7),
    st.sampled_from([-1, 0, 1, 2, 5, 10**6]),
)
def test_the_backends_agree(case: Case, max_cost: float, max_depth: int, max_paths: int) -> None:
    backends = [make(case.g) for make in BACKENDS.values()]
    assert_same(
        [
            b.dijkstra(case.src, case.dst, case.weights, max_cost=max_cost, **case.masks())
            for b in backends
        ]
    )
    reference, *others = (
        b.simple_paths_dfs(
            case.src, case.dst, max_depth=max_depth, max_paths=max_paths, **case.masks()
        )
        for b in backends
    )
    for paths in others:
        assert len(paths) == len(reference)
        for ours, theirs in zip(paths, reference, strict=True):
            assert_same([theirs, ours])


def test_the_backends_agree_on_a_town() -> None:
    # A 30 × 30 grid with one-way rows and columns, weights of 1, 2 or 3 (ties everywhere),
    # random masks and budgets: larger heaps and longer paths than the hypothesis graphs.
    g = grid_graph(30, 30, 100.0, oneway_rows="+.-." * 7 + "..", oneway_cols=".+-" * 10)
    rng = np.random.default_rng(0)
    weights = rng.integers(1, 4, size=g.m).astype(np.float64)
    backends = [make(g) for make in BACKENDS.values()]
    found = 0
    for _ in range(300):
        src, dst = (int(v) for v in rng.integers(g.n, size=2))
        node_mask = rng.random(g.n) < 0.95
        forbid = rng.random(g.n) < 0.05
        forbid[[src, dst]] = False
        banned = rng.random(g.m) < 0.05
        max_cost = float(rng.choice([math.inf, 30.0, 60.0]))
        paths = [
            b.dijkstra(
                src,
                dst,
                weights,
                node_mask=node_mask,
                forbid_nodes=forbid,
                banned_edges=banned,
                max_cost=max_cost,
            )
            for b in backends
        ]
        assert_same(paths)
        found += paths[0] is not None
    assert 100 < found < 300  # both kinds of answer were compared


def test_a_float32_budget_compares_in_float64(backend_type) -> None:
    # 0.1000000015 > float32(0.1) = 0.10000000149011612 in float64, but not in float32
    g = make_graph([(0.0, 0.0), (100.0, 0.0)], [(0, 1, 1.0)])
    weights = np.array([0.1000000015])
    b = backend_type(g)
    assert b.dijkstra(0, 1, weights, max_cost=np.float32(0.1)) is None
    assert nodes_of(b.dijkstra(0, 1, weights, max_cost=np.float32(0.25))) == [0, 1]
    assert nodes_of(b.dijkstra(0, 1, weights, max_cost=0.1000000015)) == [0, 1]
    assert b.dijkstra(0, 0, weights, max_cost=np.float32(-0.5)) is None


def test_weights_are_scanned_once_per_array(backend_type) -> None:
    b = backend_type(GRID)
    weights = LENGTHS.copy()
    assert b.dijkstra(0, 8, weights) is not None
    weights[3] = np.nan  # changed in place after it passed: not scanned again, by design
    assert b.dijkstra(0, 8, weights) is not None
    with pytest.raises(ValueError, match="weights: must be non-negative and not NaN"):
        b.dijkstra(0, 8, weights.copy())  # a new array is
    with pytest.raises(ValueError, match="weights: must be non-negative and not NaN"):
        backend_type(GRID).dijkstra(0, 8, weights)  # and so is the array on another backend


def test_the_backends_agree_on_every_path_of_a_grid() -> None:
    # 8512 simple paths corner to corner on a 5 × 5 grid: the kernel's output buffers grow
    # many times over; then the same under masks, a depth limit and a count limit.
    g = grid_graph(5, 5, 100.0)
    forbid = np.zeros(g.n, dtype=bool)
    forbid[12] = True
    banned = np.zeros(g.m, dtype=bool)
    banned[[edge_id(g, 1, 2), edge_id(g, 19, 24)]] = True
    for masks, max_depth, max_paths in [
        ({}, 100, 10**6),
        ({"forbid_nodes": forbid, "banned_edges": banned}, 100, 10**6),
        ({"forbid_nodes": forbid}, 12, 100),
    ]:
        reference, ours = (
            make(g).simple_paths_dfs(0, 24, max_depth=max_depth, max_paths=max_paths, **masks)
            for make in BACKENDS.values()
        )
        assert len(ours) == len(reference) >= 100
        for a, b in zip(reference, ours, strict=True):
            assert_same([a, b])


def test_limits_are_ints_in_the_int64_range(backend_type) -> None:
    b = backend_type(GRID)
    every = edge_sequences(b.simple_paths_dfs(0, 8, max_depth=100, max_paths=10**6))
    assert len(every) == 12
    for depth, paths in [(2**63, 2**63), (2**100, 10**6), (np.int64(100), np.int32(1000))]:
        assert edge_sequences(b.simple_paths_dfs(0, 8, max_depth=depth, max_paths=paths)) == every
    assert b.simple_paths_dfs(0, 8, max_depth=100, max_paths=-(2**70)) == []
    assert b.simple_paths_dfs(0, 8, max_depth=-(2**70), max_paths=5) == []
    for depth, paths in [
        (100, 2.5),
        (2.5, 5),
        (100, math.inf),
        (math.inf, 5),
        (100, np.float64(5)),
    ]:
        with pytest.raises(TypeError):
            b.simple_paths_dfs(0, 8, max_depth=depth, max_paths=paths)
