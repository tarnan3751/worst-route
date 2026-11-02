"""scipy as the oracle of what replaced it in the graph package: the KD-tree the spatial index
was, and the strongly connected components `largest_scc_mask` labelled. scipy stays in the
tests' environment (the pipeline uses it); the app never imports it.

`queries` draws points and radii about a build; `check_balls` holds the grid's balls to
`cKDTree.query_ball_point`'s, and `check_snaps` holds `snap` and `bubble_mask` to an index whose
balls are the tree's (`TreeIndex`), on random roles, masks, exclusions and limits. Both are run
on the synthetic builds below and the OSM fixtures (`tests/graph/test_spatial.py`) and on the
real builds (`tests/graph/test_parity_data.py`).
"""

import numpy as np
from numpy.typing import NDArray
from pyproj import Transformer
from scipy.spatial import cKDTree

from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SnapError, SpatialIndex
from worst_route.graph.testing import GRID_ORIGIN, make_graph

X0, Y0 = GRID_ORIGIN


def cloud() -> GraphData:
    """3,000 nodes within 8 km: a dense cluster, a sparse spread, and 300 of them twice."""
    rng = np.random.default_rng(3)
    points = np.vstack([rng.normal(2000.0, 150.0, (1500, 2)), rng.uniform(0, 8000, (1200, 2))])
    points = np.vstack([points, points[rng.choice(len(points), 300, replace=False)]])
    return make_graph([(X0 + x, Y0 + y) for x, y in points], [(0, 1, 1.0)])


def line() -> GraphData:
    """400 nodes 37.5 m apart on one east-west line: a box with no height."""
    nodes = [(X0 + 37.5 * k, Y0) for k in range(400)]
    return make_graph(nodes, [(k, k + 1, 37.5) for k in range(399)])


def queries(
    g: GraphData, count: int, rng: np.random.Generator, scale: float | None = None
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """`count` points and radii about a build: a fifth anywhere in its box grown by a quarter
    (some outside it), a fifth on nodes, the rest scattered about nodes; radii up to `scale`
    (default the box's size), mostly small, one in forty 0 and one in forty twice `scale`."""
    xy = np.column_stack([g.node_x, g.node_y])
    low, high = xy.min(axis=0), xy.max(axis=0)
    size = max(float((high - low).max()), 100.0)
    scale = size if scale is None else scale
    near = xy[rng.integers(0, g.n, count)]
    points = near + rng.normal(0.0, scale / 20, (count, 2))
    fifth = count // 5
    points[:fifth] = rng.uniform(low - size / 4, high + size / 4, (fifth, 2))
    points[fifth : 2 * fifth] = near[fifth : 2 * fifth]
    radii = scale * rng.random(count) ** 3
    radii[::40] = 0.0
    radii[1::40] = 2 * scale
    return points, radii


def check_balls(g: GraphData, count: int, seed: int, scale: float | None = None) -> None:
    """Every ball of `count` random ones is the KD-tree's, node for node."""
    index = SpatialIndex(g)
    tree = cKDTree(np.column_stack([g.node_x, g.node_y]))
    points, radii = queries(g, count, np.random.default_rng(seed), scale)
    for point, r, hits in zip(points, radii, tree.query_ball_point(points, r=radii), strict=True):
        assert np.sort(index._ball(point, r)).tolist() == sorted(hits), (point.tolist(), r)


class TreeIndex(SpatialIndex):
    """The index as it was: scipy's KD-tree answers the balls."""

    def __init__(self, data: GraphData) -> None:
        super().__init__(data)
        self._tree = cKDTree(self._xy)

    def _ball(self, point: NDArray[np.float64], radius_m: float) -> NDArray[np.int64]:
        return np.array(self._tree.query_ball_point(point, r=radius_m), dtype=np.int64)


def check_snaps(g: GraphData, count: int, seed: int, scale: float | None = None) -> None:
    """`snap` (a random role, node mask, exclusion and k, the radius a random one) and
    `bubble_mask` (around one to three of the points) are the KD-tree index's, `count` times."""
    rng = np.random.default_rng(seed)
    index, tree = SpatialIndex(g), TreeIndex(g)
    to_lonlat = Transformer.from_crs(f"EPSG:{g.meta['crs_epsg']}", "EPSG:4326", always_xy=True)
    points, radii = queries(g, count, rng, scale)
    for k, (point, r) in enumerate(zip(points, radii, strict=True)):
        lon, lat = to_lonlat.transform(*point)
        role = ("start", "via", "end")[k % 3]
        mask = None if k % 4 == 0 else rng.random(g.n) < rng.uniform(0.3, 1.0)
        exclude = None if k % 5 else rng.random(g.n) < 0.05
        limits = {"k": int(rng.integers(1, 11)), "max_radius_m": float(r)}
        found = []
        for each in (index, tree):
            try:
                found.append(each.snap(lat, lon, role, mask, exclude=exclude, **limits))
            except SnapError as error:
                found.append(str(error))
        assert found[0] == found[1], (k, point.tolist(), r)
        stops = points[rng.choice(count, int(rng.integers(1, 4)))]
        assert np.array_equal(index.bubble_mask(stops, r), tree.bubble_mask(stops, r))
