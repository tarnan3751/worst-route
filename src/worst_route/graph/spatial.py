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
Excluded nodes are never chosen but still count as neighbours. The `Snap` carries the node's
lat/lon rounded to 7 decimals (the build derives them from x/y, so the last bits are the
projection's; OSM's own positions have 7), and a `note` when a nearer candidate was rejected for
its role ("moved 140 m to the nearest through street");
when none qualifies, `SnapError`. A caller that needs the next candidate (a leg that cannot be
built, `docs/PLAN.md` §3.2) snaps again with the nodes it has tried as `exclude`.

Masks here are bool arrays of length n (`ValueError` otherwise); uint8 masks are the M2
kernels' internal business.

`leash_mask(stops_xy, leash_m)`: the nodes within `leash_m` of the polyline through the stops
(a point for a single stop), shapely's answer to the last bit: NumPy measures every node's
distance to each segment, and shapely measures again the few within `LEASH_BAND_M` of the leash,
whose answer it decides (the two formulas differ by nanometres). `bubble_mask(stops_xy,
radius_m)`: the nodes within `radius_m` of any stop. `active_mask(tier, leash, bubbles)` = leash &
(tier | bubbles), a request's node mask.

`nodes_within(center, radius_m, node_mask)` and `edges_from_within(...)` are the detours'
balls: the nodes of a mask within `radius_m` of a point, and the edges leaving them, ascending.
A node is within when dx² + dy² ≤ r², the same rule as `bubble_mask` up to floating point at
the exact boundary (the KD-tree computes its own distances), so the nodes are `bubble_mask`'s
within the mask. They scan the mask's nodes and their out-edges, kept for the last mask seen
(compared by content, after the mask's dtype and length are checked), instead of querying the
whole build: a request's mask holds a few thousand nodes at city scale where the build holds
tens of thousands, and a KD-tree query returns its hits as a Python list, which a ball
covering the area makes cost a millisecond.
"""

from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

import numpy as np
import shapely
from numpy.typing import NDArray

from worst_route.graph.data import GraphData

Role = Literal["start", "via", "end"]

# Nodes whose NumPy distance lies within this of the leash are measured again by shapely.
LEASH_BAND_M = 1.0

_WHERE = {
    "start": "the nearest street you can leave by",
    "via": "the nearest through street",
    "end": "the nearest street you can arrive by",
}


class SnapError(Exception):
    """No node within reach can play the stop's role."""


@dataclass(frozen=True)
class Snap:
    """Where a stop went: the node, how far it moved, the node's position (7 decimals), and why
    if moved."""

    node: int
    moved_m: float
    lat: float
    lon: float
    note: str | None


class SpatialIndex:
    """The nodes of one build, indexed by projected position."""

    def __init__(self, data: GraphData) -> None:
        # imported when an index is made, not with the module, which the API imports at its start
        from pyproj import Transformer
        from scipy.spatial import cKDTree

        self.data = data
        self._xy = np.column_stack([data.node_x, data.node_y])
        self._tree = cKDTree(self._xy)
        self._masked: _Masked | None = None
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
                lat_v = round(float(self.data.node_lat[v]), 7)
                lon_v = round(float(self.data.node_lon[v]), 7)
                return Snap(node=v, moved_m=moved, lat=lat_v, lon=lon_v, note=note)
        raise SnapError(
            f"no node within {max_radius_m:.0f} m of ({lat}, {lon}) can be "
            f"{'an' if role == 'end' else 'a'} {role} stop"
        )

    def leash_mask(self, stops_xy: NDArray[np.float64], leash_m: float) -> NDArray[np.bool_]:
        """The nodes within `leash_m` of the polyline through the stops."""
        stops = np.asarray(stops_xy, dtype=np.float64).reshape(-1, 2)
        moved = np.ones(len(stops), dtype=np.bool_)
        moved[1:] = (np.diff(stops, axis=0) != 0).any(axis=1)
        stops = stops[moved]
        x, y = self.data.node_x, self.data.node_y
        if len(stops) == 1:
            distance = np.hypot(x - stops[0, 0], y - stops[0, 1])
        else:
            distance = np.full(self.data.n, np.inf)
        for (ax, ay), (bx, by) in pairwise(stops):  # each node to its nearest point of a segment
            dx, dy = bx - ax, by - ay
            rx, ry = x - ax, y - ay
            t = np.clip((rx * dx + ry * dy) / (dx * dx + dy * dy), 0.0, 1.0)
            np.minimum(distance, np.hypot(rx - t * dx, ry - t * dy), out=distance)
        mask: NDArray[np.bool_] = distance <= leash_m
        unsure = np.flatnonzero(np.abs(distance - leash_m) < LEASH_BAND_M)
        if len(unsure):
            path = shapely.points(stops[0]) if len(stops) == 1 else shapely.linestrings(stops)
            mask[unsure] = shapely.distance(shapely.points(self._xy[unsure]), path) <= leash_m
        return mask

    def bubble_mask(self, stops_xy: NDArray[np.float64], radius_m: float) -> NDArray[np.bool_]:
        """The nodes within `radius_m` of any stop."""
        stops = np.asarray(stops_xy, dtype=np.float64).reshape(-1, 2)
        mask = np.zeros(self.data.n, dtype=np.bool_)
        for hits in self._tree.query_ball_point(stops, r=radius_m):
            mask[hits] = True
        return mask

    def nodes_within(
        self, center: NDArray[np.float64], radius_m: float, node_mask: NDArray[np.bool_]
    ) -> NDArray[np.intp]:
        """The nodes of `node_mask` within `radius_m` of `center` (x, y), ascending."""
        m = self._masked_by(node_mask)
        d2 = (m.node_x - center[0]) ** 2 + (m.node_y - center[1]) ** 2
        within: NDArray[np.intp] = m.nodes[d2 <= radius_m * radius_m]
        return within

    def edges_from_within(
        self, center: NDArray[np.float64], radius_m: float, node_mask: NDArray[np.bool_]
    ) -> NDArray[np.intp]:
        """The edges leaving the nodes `nodes_within` gives, ascending."""
        m = self._masked_by(node_mask)
        d2 = (m.tail_x - center[0]) ** 2 + (m.tail_y - center[1]) ** 2
        leaving: NDArray[np.intp] = m.edges[d2 <= radius_m * radius_m]
        return leaving

    def _masked_by(self, node_mask: NDArray[np.bool_]) -> "_Masked":
        d = self.data
        if node_mask.dtype != np.bool_ or node_mask.shape != (d.n,):
            raise ValueError(f"node_mask: need bool of shape ({d.n},)")
        cached = self._masked
        if cached is not None and np.array_equal(cached.mask, node_mask):
            return cached
        nodes = np.flatnonzero(node_mask)
        edges = np.flatnonzero(node_mask[d.edge_u])
        tails = d.edge_u[edges]
        made = _Masked(
            node_mask.copy(), nodes, d.node_x[nodes], d.node_y[nodes], edges,
            d.node_x[tails], d.node_y[tails],
        )  # fmt: skip
        self._masked = made  # one assignment: a reader sees the old cache or the new
        return made

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


@dataclass(frozen=True, eq=False)
class _Masked:
    """A node mask's nodes and the edges leaving them, with their positions."""

    mask: NDArray[np.bool_]
    nodes: NDArray[np.intp]
    node_x: NDArray[np.float64]
    node_y: NDArray[np.float64]
    edges: NDArray[np.intp]
    tail_x: NDArray[np.float64]
    tail_y: NDArray[np.float64]


def active_mask(
    tier_mask: NDArray[np.bool_], leash: NDArray[np.bool_], bubbles: NDArray[np.bool_]
) -> NDArray[np.bool_]:
    """A request's node mask: inside the leash, and in the tier or a bubble around a stop."""
    mask: NDArray[np.bool_] = leash & (tier_mask | bubbles)
    return mask
