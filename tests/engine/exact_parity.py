"""scipy as the oracle of the certified area's reach, which it answered until the app stopped
carrying it: `scipy_reached` is `exact._reached` as it was, and `check_areas` holds the areas
and the automatic leashes of random certified requests to the ones it gives
(`tests/engine/test_exact.py` on lattices and the OSM fixtures' builds,
`tests/engine/test_exact_data.py` on the real builds)."""

from unittest import mock

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csr_array
from scipy.sparse.csgraph import breadth_first_order

from worst_route.engine import exact
from worst_route.engine.exact import AreaTooLarge, certified_area, certified_request
from worst_route.engine.request import RouteRequest, request_masks
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex

from .graphs import at_node


def scipy_reached(
    n: int, source: int, tail: NDArray[np.intp], head: NDArray[np.intp]
) -> NDArray[np.bool_]:
    """The nodes of [0, n) reached from `source` along the edges tail → head, by scipy."""
    graph = csr_array((np.ones(len(tail)), (tail, head)), shape=(n, n))
    reached = np.zeros(n, dtype=np.bool_)
    reached[breadth_first_order(graph, source, directed=True, return_predecessors=False)] = True
    return reached


def _area(g: GraphData, request: RouteRequest, spatial: SpatialIndex, stops: list[int]) -> object:
    area = certified_area(g, request_masks(spatial, g, request), stops)
    return None if area is None else (area.nodes.tobytes(), area.edges.tobytes())


def _leash(g: GraphData, request: RouteRequest, spatial: SpatialIndex) -> object:
    try:
        return certified_request(g, spatial, request).leash_km
    except AreaTooLarge as error:
        return str(error)


def check_areas(
    g: GraphData, count: int, seed: int, *, near_m: float, leash_m: float, auto_every: int = 5
) -> int:
    """`count` random certified requests: two or three stops (on nodes, each within `near_m` of
    the first), a leash up to `leash_m` or, one in `auto_every`, the automatic one. The area of
    each (or None) is the one scipy's reach gives, and so is the automatic leash (or the
    refusal). The number of requests whose stops an area joined."""
    rng = np.random.default_rng(seed)
    spatial = SpatialIndex(g)
    joined = 0
    for k in range(count):
        a = int(rng.integers(g.n))
        near = np.hypot(g.node_x - g.node_x[a], g.node_y - g.node_y[a]) <= near_m
        near[a] = False
        if not near.any():
            continue
        others = rng.choice(np.flatnonzero(near), min(int(rng.integers(1, 3)), int(near.sum())))
        stops = [a, *(int(v) for v in others)]
        auto = k % auto_every == 0
        request = RouteRequest(
            stops=[at_node(g, v) for v in stops],
            seed=1,
            certified=True,
            leash_km="auto" if auto else float(rng.uniform(20.0, leash_m)) / 1000,
        )
        if auto:
            found = _leash(g, request, spatial)
            with mock.patch.object(exact, "_reached", scipy_reached):
                assert found == _leash(g, request, spatial), (k, stops)
            if isinstance(found, float):
                request = request.model_copy(update={"leash_km": found})
        found = _area(g, request, spatial, stops)
        with mock.patch.object(exact, "_reached", scipy_reached):
            assert found == _area(g, request, spatial, stops), (k, stops, request.leash_km)
        joined += found is not None
    return joined
