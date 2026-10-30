"""Longest distance, the default mode (`docs/PLAN.md` §3.7): every metre driven counts.

The score is the route's length (edge terms = `edge_length_m`); there are no node, pair or
global terms. The detours search on length and draw waypoints anywhere; the first radius is the
base class's, a quarter of the baseline within [1 km, r_max]. Its target value and its one
described number are the length.
"""

from worst_route.engine.objectives.base import BaseObjective, FloatArray
from worst_route.engine.route import Route
from worst_route.graph.data import GraphData


class Distance(BaseObjective):
    """The longer the better."""

    key = "distance"

    def edge_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803 (the plan's G)
        """Each edge's length."""
        return self._lengths

    def target_value(self, G: GraphData, route: Route) -> float:  # noqa: N803
        """The length."""
        return route.metrics.length_m

    def describe(self, G: GraphData, route: Route) -> dict[str, float]:  # noqa: N803
        """The length."""
        return {"length_m": route.metrics.length_m}
