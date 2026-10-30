"""Slowest time (`docs/PLAN.md` §3.7): every second driven, or waited, counts.

The score is the route's time: edge terms `edge_time_s`, node terms `node_delay_s` (the
signals and stop signs it passes, its start and end included, a round trip's start once); no
pair or global terms. The detours search on length × speed / 60 km/h: a road at 60 km/h weighs
its length, a slower one less, a faster one more, so a search on its way to a waypoint prefers
slow roads. Waypoints are drawn from the slow nodes, those at least half of whose out-edges are
of class unclassified or slower (residential, living street, alley), worked out once. The
first radius is the base class's. Its primary quantity and target value, which a ratio target
compares with the baseline's, is the experienced time, `time_s + delay_s` (a duration target
measures the same, `engine/target.py`); `describe` gives the driving time, the delays and the
share of the route's length on the slow classes.
"""

import math

import numpy as np

from worst_route.config import HighwayClass
from worst_route.engine.objectives.base import BaseObjective, BoolArray, FloatArray
from worst_route.engine.route import IntArray, Metrics, Route
from worst_route.graph.data import GraphData

SLOW_CLASS = HighwayClass.UNCLASSIFIED  # this class and every one numbered after it
SEARCH_SPEED_KMH = 60.0  # a road at this speed weighs its length in the searches


class SlowestTime(BaseObjective):
    """The slower the better."""

    key = "time"

    def __init__(self, data: GraphData, *, baseline: Metrics, r_max: float) -> None:
        super().__init__(data, baseline=baseline, r_max=r_max)
        self._weights = self._lengths * (data.edge_speed_kmh.astype(np.float64) / SEARCH_SPEED_KMH)
        self._slow_edges: BoolArray = data.edge_highway >= SLOW_CLASS
        slow_out = np.bincount(data.edge_u[self._slow_edges], minlength=data.n)
        out_degree = np.diff(data.out_indptr)
        slow = (out_degree > 0) & (2 * slow_out >= out_degree)
        self._pool: IntArray = np.flatnonzero(slow).astype(np.int32)

    def node_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803 (the plan's G)
        """Each node's delay."""
        delays: FloatArray = G.node_delay_s.astype(np.float64)
        return delays

    def edge_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803
        """Each edge's driving time."""
        return self._times

    def search_weights(self, G: GraphData) -> FloatArray:  # noqa: N803
        """Length × speed / 60 km/h: the slow roads are the cheap ones."""
        return self._weights

    def waypoint_pool(self, G: GraphData, route: Route) -> IntArray | None:  # noqa: N803
        """The slow nodes."""
        return self._pool

    def target_value(self, G: GraphData, route: Route) -> float:  # noqa: N803
        """The experienced time."""
        return self.primary(route.metrics)

    def describe(self, G: GraphData, route: Route) -> dict[str, float]:  # noqa: N803
        """The driving time, the delays, and the share of the length on the slow classes."""
        slow = math.fsum(self._lengths[route.edges[self._slow_edges[route.edges]]].tolist())
        return {
            "time_s": route.metrics.time_s,
            "delay_s_total": route.metrics.delay_s,
            "slowest_edge_class_share": slow / route.metrics.length_m,
        }

    @staticmethod
    def primary(metrics: Metrics) -> float:
        """The experienced time: driving and the delays."""
        return metrics.time_s + metrics.delay_s
