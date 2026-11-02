"""Where a route meets what it counts, for the map (`docs/reference/api.md`, **The route
document**).

`marks(data, geometry, nodes, edges, *, item, brand)` lists a pilgrimage's visits to its brand
(`brand`, a brand id) and a collecting route's places of its item (`item`), in the order the route
meets them, one mark a place, where the engine counts them (`objectives.brand.brand_counts`,
`objectives.collect.count_items`):

- a brand: each of its nodes the route passes (a round trip's start once), at the node, labelled
  with the brand's name: as many as the counts' `visits`;
- `roundabout`: each super-junction with rings the route passes, at the node (an interchange
  holding two rings is one mark);
- `toll_booth`, `level_crossing`: each edge the route drives that holds some, at the middle of
  its shape (the build keeps how many an edge holds, not where along it; a toll plaza of many
  booths is one mark);
- `bridge`: each run of bridge edges, at the middle of the run's shapes by length, as many as
  the counts' `bridge`: a run through a round trip's start is one, met at the route's end, where
  the route enters it (a round trip made only of bridge edges has none, as it counts none).

Every other route has none. A mark is `Mark(kind, lat, lon, label)`, its position projected from
the build's CRS (`geometry.xy_to_lonlat`) at 7 decimals, as the route's line; `label` the brand's
name, None for an item.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from worst_route.engine.objectives.base import counted_nodes
from worst_route.engine.objectives.collect import CollectItem
from worst_route.engine.route import IntArray
from worst_route.graph.data import EdgeFlag, GraphData
from worst_route.graph.geometry import Geometry, edge_xy, xy_to_lonlat

MarkKind = Literal["brand", "bridge", "toll_booth", "roundabout", "level_crossing"]
Positions = NDArray[np.int64]


@dataclass(frozen=True)
class Mark:
    """A place the route counts: its kind, position and label (a brand's name)."""

    kind: MarkKind
    lat: float
    lon: float
    label: str | None


def marks(
    data: GraphData,
    geometry: Geometry,
    nodes: IntArray,
    edges: IntArray,
    *,
    item: CollectItem | None,
    brand: int | None,
) -> list[Mark]:
    """The places a pilgrimage to `brand`, or a route collecting `item`, counts, in route order;
    none when it is neither."""
    counted = counted_nodes(nodes)
    if brand is not None:
        visited = counted[np.isin(counted, data.nodes_of_brand(brand))]
        x, y = data.node_x[visited], data.node_y[visited]
        return _marks(data, "brand", x, y, data.strings.brands[brand])
    if item == "roundabout":
        rings = counted[data.node_roundabouts[counted] > 0]
        return _marks(data, item, data.node_x[rings], data.node_y[rings], None)
    if item == "toll_booth" or item == "level_crossing":
        held = data.edge_toll_booths if item == "toll_booth" else data.edge_level_crossings
        at = np.flatnonzero(held[edges] > 0)
        x, y = _middles(data, geometry, edges[at], np.arange(len(at)))
        return _marks(data, item, x, y, None)
    if item == "bridge":
        run_edges, run_of = _bridge_runs(data, nodes, edges)
        x, y = _middles(data, geometry, run_edges, run_of)
        return _marks(data, item, x, y, None)
    return []


def _bridge_runs(data: GraphData, nodes: IntArray, edges: IntArray) -> tuple[Positions, Positions]:
    """The route's runs of bridge edges as `count_items` counts them, in the order the route
    enters them: their edges one run after another, and the run of each."""
    bridge = (data.edge_flags[edges] & EdgeFlag.BRIDGE) > 0
    padded = np.concatenate(([False], bridge, [False]))
    starts, ends = np.flatnonzero(padded[1:] != padded[:-1]).reshape(-1, 2).T
    lengths = ends - starts
    round_trip = len(nodes) > 1 and nodes[0] == nodes[-1]
    if round_trip and len(starts) and starts[0] == 0 and ends[-1] == len(edges):
        if len(starts) == 1:  # only bridge edges: none counted
            starts, lengths = starts[:0], lengths[:0]
        else:  # the first run is the last one's, on through the start
            starts, lengths = starts[1:], np.concatenate((lengths[1:-1], [lengths[-1] + ends[0]]))
    run_of = np.repeat(np.arange(len(starts)), lengths)
    within = np.arange(len(run_of)) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    at = (np.repeat(starts, lengths) + within) % max(len(edges), 1)
    return edges[at].astype(np.int64), run_of


def _middles(
    data: GraphData, geometry: Geometry, edges: NDArray[np.integer], run_of: Positions
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """The point halfway along each run of `edges` (consecutive edges of the route, `run_of`
    each one's run, ascending from 0) by the length of their shapes, in the build's CRS."""
    if len(edges) == 0:
        return np.empty(0), np.empty(0)
    points, counts = edge_xy(data, geometry, edges)
    run = np.repeat(run_of, counts)
    step = np.hypot(*np.diff(points, axis=0).T)
    step[run[1:] != run[:-1]] = 0.0  # nothing between one run's last point and the next's first
    along = np.concatenate(([0.0], np.cumsum(step)))
    first = np.flatnonzero(np.concatenate(([True], run[1:] != run[:-1])))
    last = np.concatenate((first[1:], [len(run)])) - 1
    half = (along[first] + along[last]) / 2
    at = np.clip(np.searchsorted(along, half, side="left"), first, last)
    before = np.maximum(at - 1, first)
    span = along[at] - along[before]
    share = np.divide(half - along[before], span, out=np.zeros_like(span), where=span > 0)
    x = points[before, 0] + share * (points[at, 0] - points[before, 0])
    y = points[before, 1] + share * (points[at, 1] - points[before, 1])
    return x, y


def _marks(
    data: GraphData,
    kind: MarkKind,
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    label: str | None,
) -> list[Mark]:
    """A mark of `kind` at each point, in lon/lat to 7 decimals."""
    if len(x) == 0:
        return []
    lon, lat = xy_to_lonlat(data, np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
    return [
        Mark(kind, la, lo, label)
        for la, lo in zip(np.round(lat, 7).tolist(), np.round(lon, 7).tolist(), strict=True)
    ]
