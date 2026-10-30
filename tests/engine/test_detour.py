from collections import Counter

import numpy as np
import pytest

from worst_route.engine.detour import (
    STRATEGIES,
    Strategy,
    find_detour,
    local_exhaustive_paths,
    pick_strategy,
    random_weight_path,
    waypoint_path,
)
from worst_route.engine.objectives.distance import Distance
from worst_route.engine.route import Route
from worst_route.graph.backend import Path
from worst_route.graph.data import GraphData
from worst_route.graph.python_backend import PythonBackend
from worst_route.graph.spatial import SpatialIndex
from worst_route.graph.testing import edge_id, make_graph

from .graphs import X0, Y0, edges_of, lattice, route_on

# An 8 × 8 lattice of 100 m streets, node r * 8 + c; the route runs along the bottom row.
GRID = lattice(8, 8)
BACKEND, SPATIAL = PythonBackend(GRID), SpatialIndex(GRID)
EVERYWHERE = np.ones(GRID.n, dtype=bool)
BOTTOM = list(range(8))


def rng(seed: int) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(seed))


def detour(
    route: Route,
    i: int,
    j: int,
    strategy: Strategy | None,
    seed: int,
    data: GraphData = GRID,
    extra: np.ndarray | None = None,
) -> Path | None:
    forbid, banned = route.release(data, i, j)
    if extra is not None:
        banned |= extra
    return find_detour(
        PythonBackend(data) if data is not GRID else BACKEND,
        SpatialIndex(data) if data is not GRID else SPATIAL,
        data,
        route,
        i,
        j,
        forbid=forbid,
        banned=banned,
        node_mask=np.ones(data.n, dtype=bool),
        radius=300.0,
        objective=Distance(data, baseline_length_m=700.0, r_max=5000.0),
        rng=rng(seed),
        entry_edge=-1,
        exit_edge=-1,
        strategy=strategy,
    )


def test_strategies_are_drawn_by_weight_and_local_only_for_close_ends() -> None:
    draws = rng(0)
    near = Counter(pick_strategy(300.0, draws) for _ in range(4000))
    assert set(near) == set(STRATEGIES)
    assert near["waypoint"] / 4000 == pytest.approx(0.6, abs=0.03)
    assert near["random_weight"] / 4000 == pytest.approx(0.3, abs=0.03)
    assert near["local_exhaustive"] / 4000 == pytest.approx(0.1, abs=0.02)
    far = Counter(pick_strategy(800.0, draws) for _ in range(3000))
    assert set(far) == {"waypoint", "random_weight"}
    assert far["waypoint"] / 3000 == pytest.approx(2 / 3, abs=0.03)


# On short windows of a straight route the randomly weighted direct edge nearly always wins,
# so random weight rarely finds a new path there; the others usually do.
AT_LEAST = {"waypoint": 16, "random_weight": 1, "local_exhaustive": 48}


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_each_strategy_finds_legal_detours(strategy: Strategy) -> None:
    route = route_on(GRID, BOTTOM)
    found = 0
    for seed in range(16):
        for i, j in [(2, 3), (3, 5), (0, 1), (6, 7)]:
            path = detour(route, i, j, strategy, seed)
            if path is None:
                continue
            nodes, edges = path
            assert nodes[0] == route.nodes[i] and nodes[-1] == route.nodes[j]
            route.check_splice(GRID, i, j, nodes, edges)  # raises if illegal
            assert not np.isin(nodes[1:-1], np.delete(route.nodes, range(i, j + 1))).any()
            found += 1
    assert found >= AT_LEAST[strategy]


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_no_detour_on_a_dead_straight_road(strategy: Strategy) -> None:
    xy = [(X0 + 100.0 * k, Y0) for k in range(6)]
    streets = [(k, k + 1, 100.0) for k in range(5)]
    g = make_graph(xy, [*streets, *((b, a, w) for a, b, w in streets)])
    route = route_on(g, [0, 1, 2, 3, 4, 5])
    for seed in range(5):
        assert detour(route, 2, 3, strategy, seed, data=g) is None


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_no_detour_when_the_only_way_round_is_banned(strategy: Strategy) -> None:
    g = lattice(2, 3)  # 0 - 1 - 2 below 3 - 4 - 5
    route = route_on(g, [0, 1, 2])
    extra = np.zeros(g.m, dtype=bool)
    extra[edge_id(g, 4, 1)] = True
    extra[edge_id(g, 5, 2)] = True
    for seed in range(5):
        assert detour(route, 1, 2, strategy, seed, data=g, extra=extra) is None


def test_local_exhaustive_returns_the_longest_legal_path() -> None:
    g = lattice(3, 3)
    route = route_on(g, [0, 1])
    forbid, banned = route.release(g, 0, 1)
    weights = g.edge_length_m.astype(np.float64)
    paths = local_exhaustive_paths(
        PythonBackend(g),
        SpatialIndex(g),
        g,
        0,
        1,
        forbid=forbid,
        banned=banned,
        node_mask=np.ones(g.n, bool),
        weights=weights,
    )
    best = max(float(weights[e].sum()) for _, e in paths)
    found = detour(route, 0, 1, "local_exhaustive", 0, data=g)
    assert found is not None
    # the lattice is bipartite and 0, 1 differ in colour: the longest path leaves one node out
    assert float(weights[found[1]].sum()) == best == 700.0


def test_local_exhaustive_skips_a_candidate_the_splice_refuses() -> None:
    g = lattice(3, 3)
    route = route_on(g, [0, 1])
    first = detour(route, 0, 1, "local_exhaustive", 0, data=g)
    assert first is not None
    a, b = first[1][0], first[1][-1]
    pair = ((int(g.edge_u[a]), int(g.edge_v[a])), (int(g.edge_u[b]), int(g.edge_v[b])))
    twinned = lattice(3, 3, twins=[pair])  # the best path now drives two twins
    route = route_on(twinned, [0, 1])
    second = detour(route, 0, 1, "local_exhaustive", 0, data=twinned)
    assert second is not None
    assert not (np.isin(a, second[1]) and np.isin(b, second[1]))
    route.check_splice(twinned, 0, 1, *second)


def test_a_transition_the_search_cannot_see_fails_the_attempt() -> None:
    straight = [((3, 4), (4, 5)), ((5, 4), (4, 3)), ((1, 4), (4, 7)), ((7, 4), (4, 1))]
    g = lattice(3, 3, transitions=straight)
    route = route_on(g, [0, 1, 2])
    for strategy in STRATEGIES:
        for seed in range(6):
            found = detour(route, 0, 2, strategy, seed, data=g)
            if found is not None:
                route.check_splice(g, 0, 2, *found)


def test_the_local_probe_skips_an_unreachable_end() -> None:
    route = route_on(GRID, BOTTOM)
    forbid, banned = route.release(GRID, 2, 3)
    banned[[edge_id(GRID, 2, 3), edge_id(GRID, 11, 3), edge_id(GRID, 4, 3)]] = True
    paths = local_exhaustive_paths(
        BACKEND,
        SPATIAL,
        GRID,
        2,
        3,
        forbid=forbid,
        banned=banned,
        node_mask=EVERYWHERE,
        weights=GRID.edge_length_m.astype(np.float64),
    )
    assert paths == []


def test_the_waypoint_honours_its_pool_and_distance() -> None:
    nothing = np.zeros(GRID.n, dtype=bool)
    no_bans = np.zeros(GRID.m, dtype=bool)
    weights = GRID.edge_length_m.astype(np.float64)
    kwargs = {"forbid": nothing, "banned": no_bans, "node_mask": EVERYWHERE, "weights": weights}
    path = waypoint_path(
        BACKEND,
        SPATIAL,
        GRID,
        0,
        7,
        radius=300.0,
        max_cost=1e9,
        rng=rng(1),
        pool=np.array([36], dtype=np.int32),
        **kwargs,
    )
    assert path is not None and 36 in path[0].tolist()
    for seed in range(5):
        loop = waypoint_path(
            BACKEND,
            SPATIAL,
            GRID,
            27,
            27,
            radius=300.0,
            max_cost=1e9,
            rng=rng(seed),
            min_dist_m=350.0,
            **kwargs,
        )
        assert loop is not None and loop[0][0] == loop[0][-1] == 27
        far = np.hypot(
            GRID.node_x[loop[0]] - GRID.node_x[27], GRID.node_y[loop[0]] - GRID.node_y[27]
        )
        assert far.max() >= 350.0
        Route.new(
            GRID,
            loop[0],
            loop[1],
            (0, len(loop[0]) - 1),
            score=0.0,
            metrics=route_on(GRID, [0, 1]).metrics,
            seed=0,
        )


def random_path(seed: int | np.random.Generator, radius: float = 300.0) -> list[int]:
    found = random_weight_path(
        BACKEND,
        SPATIAL,
        GRID,
        0,
        63,
        forbid=np.zeros(GRID.n, dtype=bool),
        banned=np.zeros(GRID.m, dtype=bool),
        node_mask=EVERYWHERE,
        weights=GRID.edge_length_m.astype(np.float64),
        radius=radius,
        max_cost=1e9,
        rng=seed if isinstance(seed, np.random.Generator) else rng(seed),
    )
    assert found is not None
    return found[0].tolist()


def test_random_weights_are_the_rngs() -> None:
    assert random_path(4) == random_path(4)
    assert len({tuple(random_path(seed)) for seed in range(6)}) > 1
    assert edges_of(GRID, random_path(4)).size == len(random_path(4)) - 1


def test_random_weights_are_drawn_only_near_the_window() -> None:
    # 0 and 63 are opposite corners: the midpoint is the centre, and a 100 m radius draws for
    # the edges leaving the nodes within 400 m of it
    middle = np.array([[GRID.node_x[[0, 63]].mean(), GRID.node_y[[0, 63]].mean()]])
    near = SPATIAL.bubble_mask(middle, 400.0)
    drawn = int(near[GRID.edge_u].sum())
    assert 0 < drawn < GRID.m
    used, fresh = rng(9), rng(9)
    random_path(used, radius=100.0)
    fresh.uniform(size=drawn)
    assert used.random() == fresh.random()  # exactly `drawn` factors were taken
