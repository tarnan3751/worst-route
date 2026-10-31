"""The brand pilgrimage (`docs/PLAN.md` §3.7): visit as many locations of one brand as
possible, then drive as far.

`Brand(data, brand, ...)` counts the nodes of one brand of the build's brand index
(`GraphData.nodes_of_brand`; `docs/reference/pipeline.md`, **enrich**: each a node a route can
pass through, near one or more of the brand's POIs) that the route passes. A route never repeats
a node, so each counts once (a round trip's start once). The score is visits first and length
second: visits × COLLECT_ITEM_WEIGHT + length_m, the visits as node terms beside the length's
edge terms; no pair or global term. The detours search on length. A waypoint is drawn from the
brand's nodes within `area` (the request's active nodes; the whole build when None) the route
has not visited, worked out once per route; once none is left, from any node (and a detour
whose waypoint ball holds none of them draws from the ball's nodes, `detour.waypoint_path`).
The first radius is COLLECT_RADIUS_FRACTION of the baseline, at least COLLECT_RADIUS_MIN_M, at
most r_max, as collecting's: a brand's locations are sparse.

`brand_counts(data, brand, nodes, area)` is `{id, name, visits, of}`: the brand, its name, how
many of its nodes the route passes and how many lie within `area`. A pilgrimage's counters are
`{"brand": brand_counts(...)}`, its target value the visits, and `describe` the same counts; the
validator and the API recount them with this function from the route and the build alone.

`brand_problem(data, brand, area)` is why a pilgrimage to `brand` cannot be planned on this
build ("unknown brand 999"), or within `area` ("no Starbucks within reach"), else None; planning
raises it as `BrandRefused`, a `ValueError` whose `field` is `"brand"`.
"""

from dataclasses import replace
from typing import Any

import numpy as np

from worst_route.config import COLLECT_ITEM_WEIGHT, COLLECT_RADIUS_FRACTION, COLLECT_RADIUS_MIN_M
from worst_route.engine.objectives.base import BaseObjective, BoolArray, FloatArray, counted_nodes
from worst_route.engine.route import IntArray, Metrics, Route
from worst_route.graph.data import GraphData


class BrandRefused(ValueError):  # noqa: N818 (the request's refusal, as the API names it)
    """A brand the build does not have, or none of whose locations the request's area holds."""

    field = "brand"


def brand_problem(data: GraphData, brand: int, area: BoolArray | None = None) -> str | None:
    """Why a pilgrimage to `brand` cannot be planned on this build within `area`, or None."""
    if not 0 <= brand < len(data.strings.brands):
        return f"unknown brand {brand}"
    if area is not None and not area[data.nodes_of_brand(brand)].any():
        return f"no {data.strings.brands[brand]} within reach"
    return None


def brand_counts(
    data: GraphData, brand: int, nodes: IntArray, area: BoolArray | None = None
) -> dict[str, Any]:
    """The brand, its name, how many of its nodes the route passes and how many lie in `area`."""
    at = data.nodes_of_brand(brand)
    return {
        "id": brand,
        "name": data.strings.brands[brand],
        "visits": int(np.isin(at, nodes).sum()),
        "of": len(at) if area is None else int(area[at].sum()),
    }


class Brand(BaseObjective):
    """The more of one brand's locations the better, then the longer."""

    key = "brand"

    def __init__(
        self,
        data: GraphData,
        brand: int,
        *,
        baseline: Metrics,
        r_max: float,
        area: BoolArray | None = None,
    ) -> None:
        if not 0 <= brand < len(data.strings.brands):
            raise ValueError(f"unknown brand {brand}")
        self.brand = brand
        self._at: BoolArray = np.zeros(data.n, dtype=np.bool_)
        self._at[data.nodes_of_brand(brand)] = True
        super().__init__(data, baseline=baseline, r_max=r_max)
        self._places: BoolArray = self._at if area is None else self._at & area
        self._of = int(self._places.sum())
        self._pool_of: Route | None = None
        self._pool: IntArray | None = None

    def node_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803 (the plan's G)
        """A visit, weighted, at each of the brand's nodes."""
        terms: FloatArray = COLLECT_ITEM_WEIGHT * self._at.astype(np.float64)
        return terms

    def edge_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803
        """Each edge's length."""
        return self._lengths

    def metrics(self, nodes: IntArray, edges: IntArray) -> Metrics:
        """Length and time, and the brand's counts."""
        counts = {
            "id": self.brand,
            "name": self.data.strings.brands[self.brand],
            "visits": int(self._at[counted_nodes(nodes)].sum()),
            "of": self._of,
        }
        return replace(super().metrics(nodes, edges), counters={"brand": counts})

    def waypoint_pool(self, G: GraphData, route: Route) -> IntArray | None:  # noqa: N803
        """The brand's nodes in the area the route has not visited; any node once none is left."""
        if route is not self._pool_of:
            left = np.flatnonzero(self._places & ~route.used_nodes).astype(np.int32)
            self._pool, self._pool_of = (left if len(left) else None), route
        return self._pool

    def initial_radius(self, route: Route) -> float:
        """Half the baseline, at least 2 km, at most r_max."""
        wanted = max(COLLECT_RADIUS_FRACTION * self.baseline.length_m, COLLECT_RADIUS_MIN_M)
        return min(wanted, self.r_max)

    def target_value(self, G: GraphData, route: Route) -> float:  # noqa: N803
        """How many of the brand's nodes the route passes."""
        return int(self._at[counted_nodes(route.nodes)].sum())

    def describe(self, G: GraphData, route: Route) -> dict[str, Any]:  # noqa: N803
        """The brand, its name, the route's visits and the area's locations."""
        return dict(self.metrics(route.nodes, route.edges).counters["brand"])
