import numpy as np
import pytest
from pyproj import Transformer

from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SnapError, SpatialIndex, active_mask
from worst_route.graph.testing import GRID_ORIGIN, grid_graph, make_graph

X0, Y0 = GRID_ORIGIN
TO_LONLAT = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)


def town() -> GraphData:
    """A two-way 3 × 3 grid (node r * 3 + c at (100 c, 100 r)), a cul-de-sac 5 ↔ 9 east of it,
    a node 10 south of node 1 that can only leave (10 → 1), and a node 11 north of node 7 that
    can only be reached (7 → 11; its self-loop does not count as a way out)."""
    nodes = [(X0 + 100.0 * c, Y0 + 100.0 * r) for r in range(3) for c in range(3)]
    nodes += [(X0 + 300.0, Y0 + 100.0), (X0 + 100.0, Y0 - 100.0), (X0 + 100.0, Y0 + 300.0)]
    streets = [(a, a + 1) for a in (0, 1, 3, 4, 6, 7)] + [(a, a + 3) for a in range(6)] + [(5, 9)]
    edges = [(a, b, 100.0) for a, b in streets] + [(b, a, 100.0) for a, b in streets]
    return make_graph(nodes, [*edges, (10, 1, 100.0), (7, 11, 100.0), (11, 11, 10.0)])


TOWN = town()
INDEX = SpatialIndex(TOWN)


def at(x: float, y: float) -> tuple[float, float]:
    """(lat, lon) of a point given in metres from GRID_ORIGIN."""
    lon, lat = TO_LONLAT.transform(X0 + x, Y0 + y)
    return lat, lon


def at_node(v: int) -> tuple[float, float]:
    return float(TOWN.node_lat[v]), float(TOWN.node_lon[v])


def reported(v: int) -> tuple[float, float]:
    """Node v's position as a `Snap` reports it: 7 decimals."""
    lat, lon = at_node(v)
    return round(lat, 7), round(lon, 7)


def without(*nodes: int) -> np.ndarray:
    mask = np.ones(TOWN.n, dtype=bool)
    mask[list(nodes)] = False
    return mask


def test_project_reproduces_node_coordinates() -> None:
    for v in range(TOWN.n):
        x, y = INDEX.project(*at_node(v))
        assert x == pytest.approx(TOWN.node_x[v], abs=1e-6)
        assert y == pytest.approx(TOWN.node_y[v], abs=1e-6)


def test_cul_de_sac_serves_start_and_end_but_not_via() -> None:
    for role in ("start", "end"):
        snap = INDEX.snap(*at_node(9), role)
        assert snap.node == 9 and snap.note is None and snap.moved_m < 1e-6
    via = INDEX.snap(*at_node(9), "via")
    assert via.node == 5
    assert via.moved_m == pytest.approx(100.0, abs=1e-6)
    assert via.note == "moved 100 m to the nearest through street"
    assert (via.lat, via.lon) == reported(5)


def test_one_way_stubs() -> None:
    assert INDEX.snap(*at_node(10), "start").node == 10
    end = INDEX.snap(*at_node(10), "end")
    assert end.node == 1 and end.note == "moved 100 m to the nearest street you can arrive by"
    assert INDEX.snap(*at_node(10), "via").node == 1
    assert INDEX.snap(*at_node(11), "end").node == 11
    start = INDEX.snap(*at_node(11), "start")
    assert start.node == 7 and start.note == "moved 100 m to the nearest street you can leave by"


def test_nearest_node_wins_without_a_note() -> None:
    snap = INDEX.snap(*at(130.0, 100.0), "via")
    assert snap.node == 4 and snap.note is None
    assert snap.moved_m == pytest.approx(30.0, abs=1e-6)
    assert (snap.lat, snap.lon) == reported(4)


def test_equal_distances_go_to_the_lower_node_id() -> None:
    # nodes 1 and 2 share a position, so their distances tie whatever the click
    g = make_graph(
        [(X0, Y0), (X0 + 100.0, Y0), (X0 + 100.0, Y0)],
        [(0, 1, 100.0), (1, 0, 100.0), (0, 2, 100.0), (2, 0, 100.0)],
    )
    index = SpatialIndex(g)
    assert index.snap(*at(90.0, 0.0), "start").node == 1
    mask = np.array([True, False, True])
    assert index.snap(*at(90.0, 0.0), "start", node_mask=mask).node == 2


def test_the_mask_limits_candidates_and_roles() -> None:
    # node 4 masked out: node 5 is the nearest candidate, so no note
    snap = INDEX.snap(*at(130.0, 100.0), "via", node_mask=without(4))
    assert snap.node == 5 and snap.note is None
    assert snap.moved_m == pytest.approx(70.0, abs=1e-6)
    # 9 cannot leave once 5 is masked; 2 can (to 1), 8 is masked
    start = INDEX.snap(*at_node(9), "start", node_mask=without(5, 8))
    assert start.node == 2 and start.note == "moved 141 m to the nearest street you can leave by"
    # node 2 is pass-through on the whole graph but has no neighbour left in the mask
    via = INDEX.snap(*at_node(9), "via", node_mask=without(1, 5, 8))
    assert via.node == 4 and via.note == "moved 200 m to the nearest through street"


def test_via_needs_two_distinct_neighbours_inside_the_mask() -> None:
    # node 2 is a corner (neighbours 1 and 5); without 5 its only way in and out is node 1
    g = grid_graph(3, 3, 100.0)
    index = SpatialIndex(g)
    mask = np.ones(9, dtype=bool)
    mask[5] = False
    where = float(g.node_lat[2]), float(g.node_lon[2])
    assert index.snap(*where, "via").node == 2
    snap = index.snap(*where, "via", node_mask=mask)
    assert snap.node == 1 and snap.note == "moved 100 m to the nearest through street"
    assert index.snap(*where, "start", node_mask=mask).node == 2
    assert index.snap(*where, "end", node_mask=mask).node == 2


def just(*nodes: int) -> np.ndarray:
    mask = np.zeros(TOWN.n, dtype=bool)
    mask[list(nodes)] = True
    return mask


def test_excluded_nodes_are_skipped_but_still_routable() -> None:
    # the next candidate after node 10, which is excluded, is node 1, now the nearest: no note
    snap = INDEX.snap(*at_node(10), "start", exclude=just(10))
    assert snap.node == 1 and snap.note is None
    assert snap.moved_m == pytest.approx(100.0, abs=1e-6)
    # node 9's way in is node 5: excluded, 5 still counts; masked out, it does not
    assert INDEX.snap(*at_node(9), "end", exclude=just(5)).node == 9
    assert INDEX.snap(*at_node(9), "end", node_mask=without(5)).node in (2, 8)
    # k counts only the candidates not excluded
    assert INDEX.snap(*at_node(9), "via", k=1, exclude=just(9)).node == 5


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"node_mask": np.ones(12, dtype=np.uint8)}, "node_mask: need bool of shape \\(12,\\)"),
        ({"node_mask": np.ones(11, dtype=bool)}, "node_mask"),
        ({"exclude": np.zeros(12, dtype=np.uint8)}, "exclude: need bool"),
        ({"exclude": np.zeros((12, 1), dtype=bool)}, "exclude"),
    ],
)
def test_masks_must_be_bool_of_length_n(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        INDEX.snap(*at_node(4), "via", **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "where"),
    [
        ({"k": 1}, (300.0, 100.0)),
        ({"max_radius_m": 50.0}, (300.0, 100.0)),
        ({}, (100.0, 6000.0)),
        ({"node_mask": np.zeros(12, dtype=bool)}, (100.0, 100.0)),
    ],
)
def test_nothing_qualifying_raises(kwargs: dict, where: tuple[float, float]) -> None:
    with pytest.raises(SnapError, match="via stop"):
        INDEX.snap(*at(*where), "via", **kwargs)


def test_the_error_names_the_role() -> None:
    with pytest.raises(SnapError, match="can be an end stop$"):
        INDEX.snap(*at(100.0, 6000.0), "end")
    with pytest.raises(SnapError, match="can be a start stop$"):
        INDEX.snap(*at(100.0, 6000.0), "start")


GRID = grid_graph(5, 5, 100.0)  # node r * 5 + c at (100 c, 100 r) from GRID_ORIGIN
GRID_INDEX = SpatialIndex(GRID)


def xy(*nodes: int) -> np.ndarray:
    return np.column_stack([GRID.node_x[list(nodes)], GRID.node_y[list(nodes)]])


def members(mask: np.ndarray) -> set[int]:
    return set(np.flatnonzero(mask).tolist())


def test_leash_along_a_segment() -> None:
    assert members(GRID_INDEX.leash_mask(xy(0, 4), 150.0)) == set(range(10))


def test_leash_around_a_single_stop() -> None:
    expected = {6, 7, 8, 11, 12, 13, 16, 17, 18}
    assert members(GRID_INDEX.leash_mask(xy(12), 150.0)) == expected
    assert members(GRID_INDEX.leash_mask(xy(12, 12), 150.0)) == expected


def test_leash_follows_the_polyline() -> None:
    expected = {0, 1, 2, 3, 4, 9, 14, 19, 24}
    assert members(GRID_INDEX.leash_mask(xy(0, 4, 24), 50.0)) == expected
    assert members(GRID_INDEX.leash_mask(xy(0, 4, 0), 50.0)) == {0, 1, 2, 3, 4}


def test_bubbles_around_each_stop() -> None:
    mask = GRID_INDEX.bubble_mask(xy(0, 24), 150.0)
    assert mask.dtype == np.bool_ and mask.shape == (25,)
    assert members(mask) == {0, 1, 5, 6, 18, 19, 23, 24}


def test_active_mask_is_leash_and_tier_or_bubble() -> None:
    tier = np.array([True, True, False, False, True, False])
    leash = np.array([True, True, True, True, False, False])
    bubbles = np.array([True, False, True, False, True, True])
    assert active_mask(tier, leash, bubbles).tolist() == [True, True, True, False, False, False]


def test_the_masked_balls_are_the_bubbles_within_the_mask() -> None:
    # centres on nodes, mid-block and off the grid, radii landing exactly on lattice nodes:
    # the scan measures as the KD-tree does
    rng = np.random.default_rng(0)
    masks = [np.ones(GRID.n, dtype=bool), rng.random(GRID.n) < 0.6]
    for mask in masks * 2:  # each mask twice in a row, then again after the other: the cache
        for cx, cy in [(200.0, 200.0), (150.0, 250.0), (-120.0, 430.0), (37.5, 81.25)]:
            centre = np.array([X0 + cx, Y0 + cy])
            for radius in (0.0, 50.0, 100.0, 141.4213562373095, 200.0, 250.0, 600.0):
                bubble = GRID_INDEX.bubble_mask(centre[None, :], radius) & mask
                nodes = GRID_INDEX.nodes_within(centre, radius, mask)
                assert nodes.tolist() == np.flatnonzero(bubble).tolist()
                edges = GRID_INDEX.edges_from_within(centre, radius, mask)
                assert edges.tolist() == np.flatnonzero(bubble[GRID.edge_u]).tolist()
    with pytest.raises(ValueError, match="node_mask"):
        GRID_INDEX.nodes_within(np.array([X0, Y0]), 100.0, np.ones(3, dtype=bool))
    cached = masks[0]
    GRID_INDEX.nodes_within(np.array([X0, Y0]), 100.0, cached)
    with pytest.raises(ValueError, match="node_mask"):  # checked before the cache is consulted
        GRID_INDEX.nodes_within(np.array([X0, Y0]), 100.0, cached.astype(np.uint8))
