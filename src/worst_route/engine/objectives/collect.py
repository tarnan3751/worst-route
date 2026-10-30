"""Collecting (`docs/PLAN.md` §3.7): pass as many of one item as possible, then drive as far.

`Collect(data, item, ...)` collects one of `ITEMS`:

- `toll_booth` and `level_crossing`: the counts on the edges the route drives
  (`edge_toll_booths`, `edge_level_crossings`). One inside a two-way road is on both its edges,
  of which a route drives one; one at a junction is on one of the junction's out-edges
  (`docs/reference/pipeline.md`, **enrich**).
- `roundabout`: the rings of the super-junctions the route passes (`node_roundabouts`). A
  roundabout is one super-junction, entered once, so its ring counts once.
- `bridge`: the runs of bridge edges (`EdgeFlag.BRIDGE`), so a bridge split by a junction counts
  once: a pair term for each bridge edge entered from an edge that is not one, and the
  first-edge term for a route that starts on one (unless it is a round trip that also ends on
  one: a run through its start counts once). A round trip made only of bridge edges would count
  none.

The score is items first and length second: items × COLLECT_ITEM_WEIGHT + length_m, the items
as edge terms beside the length (toll booths, level crossings), as node terms (roundabouts) or
as the pair and first-edge terms (bridges). The detours search on length. A waypoint is drawn
from the places of the items the route has not collected (the ends of the edges holding one,
the super-junctions holding a ring) within `area` (the request's active nodes; the whole build
when None), less the route's own nodes, worked out once per route; once none is left, from any
node (and a detour whose waypoint ball holds none of them draws from the ball's nodes,
`detour.waypoint_path`). The first radius is COLLECT_RADIUS_FRACTION of the baseline, at least
COLLECT_RADIUS_MIN_M, at most r_max: items are sparse.

`count_items(data, nodes, edges)` counts every item on a route the same way; the route files
give it for every mode. A collecting route's counters are its `item` and `items`, that count;
its target value is the count of its item, and `describe` gives the item, its count and how
many the area holds (`candidates_in_area`: an item inside a two-way road once, a bridge once per
road it carries, so a bridge split by junctions several times).
"""

from dataclasses import replace
from typing import Any, Literal, get_args

import numpy as np

from worst_route.config import COLLECT_ITEM_WEIGHT, COLLECT_RADIUS_FRACTION, COLLECT_RADIUS_MIN_M
from worst_route.engine.objectives.base import BaseObjective, BoolArray, FloatArray
from worst_route.engine.route import IntArray, Metrics, Route
from worst_route.graph.data import EdgeFlag, GraphData

CollectItem = Literal["toll_booth", "level_crossing", "roundabout", "bridge"]
ITEMS: tuple[CollectItem, ...] = get_args(CollectItem)


def count_items(data: GraphData, nodes: IntArray, edges: IntArray) -> dict[str, int]:
    """How many of each item a route passes: toll booths and level crossings on its edges,
    roundabout rings at its nodes, and runs of bridge edges (a round trip's start, and a run
    through it, once)."""
    round_trip = len(nodes) > 1 and nodes[0] == nodes[-1]
    counted = nodes[:-1] if round_trip else nodes
    bridge = (data.edge_flags[edges] & EdgeFlag.BRIDGE) > 0
    opens = bridge[:1] & ~(bridge[-1:] & round_trip)  # a run through a round trip's start
    return {
        "toll_booth": int(data.edge_toll_booths[edges].sum()),
        "level_crossing": int(data.edge_level_crossings[edges].sum()),
        "roundabout": int(data.node_roundabouts[counted].sum()),
        "bridge": int(opens.sum() + (bridge[1:] & ~bridge[:-1]).sum()),
    }


class Collect(BaseObjective):
    """The more of one item the better, then the longer."""

    key = "collect"

    def __init__(
        self,
        data: GraphData,
        item: CollectItem,
        *,
        baseline: Metrics,
        r_max: float,
        area: BoolArray | None = None,
    ) -> None:
        self.item = item
        self.has_pair = item == "bridge"
        bridges = (data.edge_flags & EdgeFlag.BRIDGE) > 0
        self._bridge: list[bool] = bridges.tolist() if self.has_pair else [False] * data.m
        super().__init__(data, baseline=baseline, r_max=r_max)
        within = np.ones(data.n, dtype=np.bool_) if area is None else area
        if item == "roundabout":
            places = data.node_roundabouts > 0
            self._in_area = int(data.node_roundabouts[within].sum())
        else:
            held = {
                "toll_booth": data.edge_toll_booths,
                "level_crossing": data.edge_level_crossings,
                "bridge": bridges.astype(np.uint8),
            }[item]
            holders = held > 0
            places = np.zeros(data.n, dtype=np.bool_)
            places[data.edge_u[holders]] = places[data.edge_v[holders]] = True
            inside = holders & within[data.edge_u] & within[data.edge_v]
            per_road = np.zeros(data.m, dtype=np.int64)
            np.maximum.at(per_road, data.edge_unit[inside], held[inside])
            self._in_area = int(per_road.sum())
        self._places: BoolArray = places & within
        self._pool_of: Route | None = None
        self._pool: IntArray | None = None

    def node_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803 (the plan's G)
        """Each super-junction's rings, weighted, when collecting roundabouts."""
        if self.item != "roundabout":
            return None
        terms: FloatArray = COLLECT_ITEM_WEIGHT * G.node_roundabouts.astype(np.float64)
        return terms

    def edge_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803
        """Each edge's length, plus its toll booths or level crossings, weighted."""
        if self.item == "toll_booth":
            return self._lengths + COLLECT_ITEM_WEIGHT * G.edge_toll_booths
        if self.item == "level_crossing":
            return self._lengths + COLLECT_ITEM_WEIGHT * G.edge_level_crossings
        return self._lengths

    def pair_term(self, G: GraphData, e_in: int, e_out: int) -> float:  # noqa: N803
        """An item for a bridge entered from an edge that is not one."""
        return COLLECT_ITEM_WEIGHT if self._bridge[e_out] and not self._bridge[e_in] else 0.0

    def first_edge_term(self, G: GraphData, e: int, closing: int) -> float:  # noqa: N803
        """An item for a route that starts on a bridge, unless it is a round trip that ends on
        one: that run, through the start, was counted when the route entered it at its end."""
        counted = closing >= 0 and self._bridge[closing]
        return COLLECT_ITEM_WEIGHT if self._bridge[e] and not counted else 0.0

    def metrics(self, nodes: IntArray, edges: IntArray) -> Metrics:
        """Length and time, the item and the route's count of every item."""
        counters = {"item": self.item, "items": count_items(self.data, nodes, edges)}
        return replace(super().metrics(nodes, edges), counters=counters)

    def waypoint_pool(self, G: GraphData, route: Route) -> IntArray | None:  # noqa: N803
        """The places of the items in the area the route has not collected; any node once none is
        left."""
        if route is not self._pool_of:
            left = np.flatnonzero(self._places & ~route.used_nodes).astype(np.int32)
            self._pool, self._pool_of = (left if len(left) else None), route
        return self._pool

    def initial_radius(self, route: Route) -> float:
        """Half the baseline, at least 2 km, at most r_max."""
        wanted = max(COLLECT_RADIUS_FRACTION * self.baseline.length_m, COLLECT_RADIUS_MIN_M)
        return min(wanted, self.r_max)

    def target_value(self, G: GraphData, route: Route) -> float:  # noqa: N803
        """How many of the item the route passes."""
        return count_items(G, route.nodes, route.edges)[self.item]

    def describe(self, G: GraphData, route: Route) -> dict[str, Any]:  # noqa: N803
        """The item, how many the route passes and how many the area holds."""
        return {
            "item": self.item,
            "count": count_items(G, route.nodes, route.edges)[self.item],
            "candidates_in_area": self._in_area,
        }
