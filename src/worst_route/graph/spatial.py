"""Spatial helpers: projecting stops, snapping them to nodes, the leash and bubble masks.

`docs/reference/graph.md`, **Spatial**; `docs/PLAN.md` §2.6, §3.2, §3.9.

`SpatialIndex(data)` builds, once per build, a cKDTree on every node's projected (x, y) and a
transformer from lon/lat (EPSG:4326) to the build's CRS. Masks filter at query time, so one
tree serves every request.

`snap(lat, lon, role, node_mask, k, max_radius_m, exclude)` picks the node a stop goes to. The
candidates are the nodes in the mask and not in `exclude` within `max_radius_m`, nearest first
(ties by node id), at most `k` of them; the first that can play the role wins. Roles are judged
within the mask, self-loops ignored: a start needs an out-neighbour in the mask, an end an
in-neighbour, a via stop an in-neighbour u and an out-neighbour w with u ≠ w, so the route can
pass through it without turning back (the build-wide `node_pass_through` flag is not used here).
Excluded nodes are never chosen but still count as neighbours. The `Snap` carries a `note` when
a nearer candidate was rejected for its role ("moved 140 m to the nearest through street");
when none qualifies, `SnapError`. A caller that needs the next candidate (a leg that cannot be
built, `docs/PLAN.md` §3.2) snaps again with the nodes it has tried as `exclude`.

Masks here are bool arrays of length n (`ValueError` otherwise); uint8 masks are the M2
kernels' internal business.

`leash_mask(stops_xy, leash_m)`: the nodes within `leash_m` of the polyline through the stops
(a point for a single stop). `bubble_mask(stops_xy, radius_m)`: the nodes within `radius_m` of
any stop. `active_mask(tier, leash, bubbles)` = leash & (tier | bubbles), a request's node mask.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np
import shapely
from numpy.typing import NDArray
from pyproj import Transformer
from scipy.spatial import cKDTree

from worst_route.graph.data import GraphData

Role = Literal["start", "via", "end"]

_WHERE = {
    "start": "the nearest street you can leave by",
    "via": "the nearest through street",
    "end": "the nearest street you can arrive by",
}


class SnapError(Exception):
    """No node within reach can play the stop's role."""


@dataclass(frozen=True)
class Snap:
    """Where a stop went: the node, how far it moved, the node's position, and why if moved."""

    node: int
    moved_m: float
    lat: float
    lon: float
    note: str | None


class SpatialIndex:
    """The nodes of one build, indexed by projected position."""

    def __init__(self, data: GraphData) -> None:
        self.data = data
        self._xy = np.column_stack([data.node_x, data.node_y])
        self._tree = cKDTree(self._xy)
        crs = f"EPSG:{data.meta['crs_epsg']}"
        self._to_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    def project(self, lat: float, lon: float) -> tuple[float, float]:
        """(x, y) in the build's CRS, metres."""
        x, y = self._to_xy.transform(lon, lat)
        return float(x), float(y)

    def snap(
        self,
        lat: float,
        lon: float,
        role: Role,
        node_mask: NDArray[np.bool_] | None = None,
        k: int = 10,
        max_radius_m: float = 5000.0,
        exclude: NDArray[np.bool_] | None = None,
    ) -> Snap:
        """The nearest node in the mask, not excluded, that can play `role`, of the `k` nearest."""
        n = self.data.n
        for name, mask in (("node_mask", node_mask), ("exclude", exclude)):
            if mask is not None and (mask.dtype != np.bool_ or mask.shape != (n,)):
                raise ValueError(f"{name}: need bool of shape ({n},)")
        point = np.array(self.project(lat, lon))
        near = np.array(self._tree.query_ball_point(point, r=max_radius_m), dtype=np.int64)
        if node_mask is not None:
            near = near[node_mask[near]]
        if exclude is not None:
            near = near[~exclude[near]]
        distance = np.hypot(*(self._xy[near] - point).T)
        for rank, i in enumerate(np.lexsort((near, distance))[:k]):
            v = int(near[i])
            if self._plays(v, role, node_mask):
                moved = float(distance[i])
                note = None if rank == 0 else f"moved {moved:.0f} m to {_WHERE[role]}"
                lat_v, lon_v = float(self.data.node_lat[v]), float(self.data.node_lon[v])
                return Snap(node=v, moved_m=moved, lat=lat_v, lon=lon_v, note=note)
        raise SnapError(
            f"no node within {max_radius_m:.0f} m of ({lat}, {lon}) can be a {role} stop"
        )

    def leash_mask(self, stops_xy: NDArray[np.float64], leash_m: float) -> NDArray[np.bool_]:
        """The nodes within `leash_m` of the polyline through the stops."""
        stops = np.asarray(stops_xy, dtype=np.float64).reshape(-1, 2)
        moved = np.ones(len(stops), dtype=np.bool_)
        moved[1:] = (np.diff(stops, axis=0) != 0).any(axis=1)
        stops = stops[moved]
        path = shapely.points(stops[0]) if len(stops) == 1 else shapely.linestrings(stops)
        lo, hi = stops.min(axis=0) - leash_m, stops.max(axis=0) + leash_m
        near = np.flatnonzero(((self._xy >= lo) & (self._xy <= hi)).all(axis=1))
        mask = np.zeros(self.data.n, dtype=np.bool_)
        mask[near] = shapely.distance(shapely.points(self._xy[near]), path) <= leash_m
        return mask

    def bubble_mask(self, stops_xy: NDArray[np.float64], radius_m: float) -> NDArray[np.bool_]:
        """The nodes within `radius_m` of any stop."""
        stops = np.asarray(stops_xy, dtype=np.float64).reshape(-1, 2)
        mask = np.zeros(self.data.n, dtype=np.bool_)
        for hits in self._tree.query_ball_point(stops, r=radius_m):
            mask[hits] = True
        return mask

    def _plays(self, v: int, role: Role, node_mask: NDArray[np.bool_] | None) -> bool:
        d = self.data
        heads = d.edge_v[d.out_edges[d.out_indptr[v] : d.out_indptr[v + 1]]]
        tails = d.edge_u[d.in_edges[d.in_indptr[v] : d.in_indptr[v + 1]]]
        heads, tails = heads[heads != v], tails[tails != v]
        if node_mask is not None:
            heads, tails = heads[node_mask[heads]], tails[node_mask[tails]]
        if role == "start":
            return bool(heads.size)
        if role == "end":
            return bool(tails.size)
        return bool(heads.size and tails.size and np.union1d(heads, tails).size >= 2)


def active_mask(
    tier_mask: NDArray[np.bool_], leash: NDArray[np.bool_], bubbles: NDArray[np.bool_]
) -> NDArray[np.bool_]:
    """A request's node mask: inside the leash, and in the tier or a bubble around a stop."""
    mask: NDArray[np.bool_] = leash & (tier_mask | bubbles)
    return mask
