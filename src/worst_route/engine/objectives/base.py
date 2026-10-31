"""The objective interface and its base class (`docs/PLAN.md` §3.6; `docs/reference/engine.md`).

A mode is an `Objective`. Its score is a sum of terms:

    score = Σ node_terms[v] + Σ edge_terms[e] + Σ pair_term(e_k, e_k+1)
            + first_edge_term(e_0, closing) + global_term

node terms over the route's nodes (a round trip's start once), edge terms over its edges, pair
terms over each two consecutive edges and the first-edge term over its first edge (its pair
term after an edge that is not there; `closing` is a round trip's last edge, which comes back
to its start, else -1; both only when `has_pair`), the global term over the whole route (only
when `has_global`). Feasibility hooks (`feasible_pair`, `feasible_window`), per-window bans
(`banned_edges`) and the window the loop tries for the one it drew (`window`: as drawn, unless a
constraint widens it) constrain it, and `banned_turn_classes` names the turn classes its
`feasible_pair` refuses, so the searches never propose them (`graph/backend.py`);
`avoided_turn_classes` names those its pair terms penalise, which the searches try without
first (`engine/search.py`); search guidance (`search_weights`,
`waypoint_pool`, `initial_radius`, `search_budget`) steers the detours; `target_value` is the
mode's own quantity for target modes; `describe` gives mode-specific numbers. `primary(metrics)`
is the quantity a ratio target compares with the baseline's (the length for the base class and
distance, the experienced time, driving and delays, for slowest time), a static method, so a
caller can read it from the class without making the objective. `constraint_delta` is the part
of `delta` its constraints add (a soft one's penalties; 0 for a mode alone). Which route is
better, and when a run is done, is the run's goal (`engine.target.Goal`), not the objective's.
A constraint is an objective with hooks and no terms. The worsen loop sees only this
interface.

`global_term` takes the route's node and edge arrays rather than a `Route`, so a candidate can
be scored before it is spliced. `BaseObjective(data, baseline=, r_max=)` (the baseline's
`Metrics`) gives every hook a neutral default and caches its term vectors for the build. It
implements, once for every
mode, `evaluate(nodes, edges)` (the score and the `Metrics`: length, time and, by
`route_delay_s`, the delays at the route's nodes, whatever the mode), `score(route)` and
`delta(route, i, j, nodes, edges)`: the terms a splice removes and adds (node and edge terms
over the removed interior and the detour, pair terms at both boundaries and inside, the
first-edge term when the window starts the route or ends a round trip, the global term
re-evaluated), summed with `math.fsum`, so it equals `score(after) - score(before)` to rounding
and a change of nothing is exactly zero. Its `initial_radius` is RADIUS_INITIAL_FRACTION of the
baseline length, at least RADIUS_INITIAL_MIN_M, at most r_max.
"""

import math
from itertools import pairwise
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from worst_route.config import RADIUS_INITIAL_FRACTION, RADIUS_INITIAL_MIN_M, SEARCH_BUDGET_FACTOR
from worst_route.engine.route import IntArray, Metrics, Route, splice_arrays
from worst_route.graph.data import GraphData

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]


class Objective(Protocol):
    """What a mode tells the engine; `BaseObjective` implements every member."""

    key: str
    has_pair: bool
    has_global: bool
    banned_turn_classes: int  # the classes `feasible_pair` refuses, as the searches' class bits
    avoided_turn_classes: int  # the classes it penalises, which the searches try without first

    def node_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803 (the plan's G)
        """Per-node term, or None for none."""
        ...

    def edge_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803
        """Per-edge term, or None for none."""
        ...

    def pair_term(self, G: GraphData, e_in: int, e_out: int) -> float:  # noqa: N803
        """The term of turning from e_in to e_out (read only when `has_pair`)."""
        ...

    def first_edge_term(self, G: GraphData, e: int, closing: int) -> float:  # noqa: N803
        """The term of starting the route on e, after no edge; `closing` is the last edge of a
        round trip (which ends where it starts), else -1 (read only when `has_pair`)."""
        ...

    def feasible_pair(self, G: GraphData, e_in: int, e_out: int) -> bool:  # noqa: N803
        """Whether the turn from e_in to e_out is allowed by this mode."""
        ...

    def global_term(self, G: GraphData, nodes: IntArray, edges: IntArray) -> float:  # noqa: N803
        """The non-additive term of a whole route (read only when `has_global`)."""
        ...

    def feasible_window(
        self,
        G: GraphData,  # noqa: N803
        route: Route,
        i: int,
        j: int,
        nodes: IntArray,
        edges: IntArray,
    ) -> bool:
        """Whether replacing nodes[i..j] by the detour keeps the mode's constraints."""
        ...

    def banned_edges(self, G: GraphData, route: Route, i: int, j: int) -> BoolArray | None:  # noqa: N803
        """Edges this mode bans for a detour of nodes[i..j], beyond the route's own."""
        ...

    def window(self, route: Route, i: int, j: int) -> tuple[int, int]:
        """The window the loop tries for the nodes[i..j] it drew: the same, or one widened to
        where a detour this mode allows can start and end (never past a stop)."""
        ...

    def search_weights(self, G: GraphData) -> FloatArray:  # noqa: N803
        """The edge weights the detour searches use."""
        ...

    def waypoint_pool(self, G: GraphData, route: Route) -> IntArray | None:  # noqa: N803
        """The nodes a waypoint is drawn from, or None for any."""
        ...

    def initial_radius(self, route: Route) -> float:
        """The first ambition radius, metres."""
        ...

    def search_budget(self, radius: float) -> float:
        """The cost bound of a detour search at this radius."""
        ...

    def target_value(self, G: GraphData, route: Route) -> float:  # noqa: N803
        """The quantity a target mode compares."""
        ...

    def describe(self, G: GraphData, route: Route) -> dict[str, Any]:  # noqa: N803
        """Mode-specific values for the presentation."""
        ...

    def primary(self, metrics: Metrics) -> float:
        """The quantity a ratio target compares with the baseline's."""
        ...

    def constraint_delta(
        self, route: Route, i: int, j: int, nodes: IntArray, edges: IntArray
    ) -> float:
        """The part of `delta` the objective's constraints contribute (0 without any)."""
        ...

    def evaluate(self, nodes: IntArray, edges: IntArray) -> tuple[float, Metrics]:
        """The score and metrics of a route's arrays."""
        ...

    def score(self, route: Route) -> float:
        """The score of a route."""
        ...

    def delta(self, route: Route, i: int, j: int, nodes: IntArray, edges: IntArray) -> float:
        """score(after) - score(before) for replacing nodes[i..j] by the detour."""
        ...


class BaseObjective:
    """Neutral defaults for every hook, and the exact score and delta, for one build."""

    key = "base"
    has_pair = False
    has_global = False
    banned_turn_classes = 0
    avoided_turn_classes = 0

    def __init__(self, data: GraphData, *, baseline: Metrics, r_max: float) -> None:
        self.data = data
        self.baseline = baseline
        self.r_max = r_max
        self._lengths = data.edge_length_m.astype(np.float64)
        self._times = data.edge_time_s.astype(np.float64)
        self._node = self.node_terms(data)
        self._edge = self.edge_terms(data)

    def node_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803
        """No node terms."""
        return None

    def edge_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803
        """No edge terms."""
        return None

    def pair_term(self, G: GraphData, e_in: int, e_out: int) -> float:  # noqa: N803
        """No pair terms."""
        return 0.0

    def first_edge_term(self, G: GraphData, e: int, closing: int) -> float:  # noqa: N803
        """No first-edge term."""
        return 0.0

    def feasible_pair(self, G: GraphData, e_in: int, e_out: int) -> bool:  # noqa: N803
        """Every turn."""
        return True

    def global_term(self, G: GraphData, nodes: IntArray, edges: IntArray) -> float:  # noqa: N803
        """No global term."""
        return 0.0

    def feasible_window(
        self,
        G: GraphData,  # noqa: N803
        route: Route,
        i: int,
        j: int,
        nodes: IntArray,
        edges: IntArray,
    ) -> bool:
        """Every window."""
        return True

    def banned_edges(self, G: GraphData, route: Route, i: int, j: int) -> BoolArray | None:  # noqa: N803
        """No bans of its own."""
        return None

    def window(self, route: Route, i: int, j: int) -> tuple[int, int]:
        """The window as drawn."""
        return i, j

    def search_weights(self, G: GraphData) -> FloatArray:  # noqa: N803
        """Length."""
        return self._lengths

    def waypoint_pool(self, G: GraphData, route: Route) -> IntArray | None:  # noqa: N803
        """Any node."""
        return None

    def initial_radius(self, route: Route) -> float:
        """A quarter of the baseline, at least 1 km, at most r_max."""
        wanted = max(RADIUS_INITIAL_FRACTION * self.baseline.length_m, RADIUS_INITIAL_MIN_M)
        return min(wanted, self.r_max)

    def search_budget(self, radius: float) -> float:
        """Four times the radius."""
        return SEARCH_BUDGET_FACTOR * radius

    def target_value(self, G: GraphData, route: Route) -> float:  # noqa: N803
        """The score."""
        return route.score

    def describe(self, G: GraphData, route: Route) -> dict[str, Any]:  # noqa: N803
        """Nothing beyond the metrics."""
        return {}

    @staticmethod
    def primary(metrics: Metrics) -> float:
        """The length."""
        return metrics.length_m

    def constraint_delta(
        self, route: Route, i: int, j: int, nodes: IntArray, edges: IntArray
    ) -> float:
        """None: a mode alone has no constraints."""
        return 0.0

    def evaluate(self, nodes: IntArray, edges: IntArray) -> tuple[float, Metrics]:
        """The score and metrics of a route's arrays."""
        terms: list[float] = []
        if self._node is not None:
            terms += self._node[counted_nodes(nodes)].tolist()
        if self._edge is not None:
            terms += self._edge[edges].tolist()
        if self.has_pair:
            terms += [self.pair_term(self.data, a, b) for a, b in pairwise(edges.tolist())]
            if len(edges):
                closing = int(edges[-1]) if nodes[0] == nodes[-1] else -1
                terms.append(self.first_edge_term(self.data, int(edges[0]), closing))
        if self.has_global:
            terms.append(self.global_term(self.data, nodes, edges))
        return math.fsum(terms), self.metrics(nodes, edges)

    def metrics(self, nodes: IntArray, edges: IntArray) -> Metrics:
        """Length, time and delays, summed exactly."""
        return Metrics(
            length_m=math.fsum(self._lengths[edges].tolist()),
            time_s=math.fsum(self._times[edges].tolist()),
            delay_s=route_delay_s(self.data, nodes),
        )

    def score(self, route: Route) -> float:
        """The score of a route."""
        return self.evaluate(route.nodes, route.edges)[0]

    def delta(self, route: Route, i: int, j: int, nodes: IntArray, edges: IntArray) -> float:
        """score(after) - score(before) for replacing nodes[i..j] by the detour, exactly."""
        removed = route.edges[i:j]
        terms: list[float] = []
        if self._node is not None:
            terms += self._node[nodes[1:-1]].tolist()
            terms += (-self._node[route.nodes[i + 1 : j]]).tolist()
        if self._edge is not None:
            terms += self._edge[edges].tolist()
            terms += (-self._edge[removed]).tolist()
        if self.has_pair:
            before, after = route.edges[max(i - 1, 0) : i], route.edges[j : j + 1]
            new = np.concatenate((before, edges, after)).tolist()
            old = np.concatenate((before, removed, after)).tolist()
            terms += [self.pair_term(self.data, a, b) for a, b in pairwise(new)]
            terms += [-self.pair_term(self.data, a, b) for a, b in pairwise(old)]
            end, round_trip = len(route.edges), bool(route.nodes[0] == route.nodes[-1])
            if i == 0 or (round_trip and j == end):  # the first edge, or a round trip's last
                first = int(edges[0] if i == 0 else route.edges[0])
                last = int(edges[-1] if j == end else route.edges[-1]) if round_trip else -1
                was = int(route.edges[-1]) if round_trip else -1
                terms.append(self.first_edge_term(self.data, first, last))
                terms.append(-self.first_edge_term(self.data, int(route.edges[0]), was))
        if self.has_global:
            terms.append(self.global_term(self.data, *splice_arrays(route, i, j, nodes, edges)))
            terms.append(-self.global_term(self.data, route.nodes, route.edges))
        return math.fsum(terms)


def counted_nodes(nodes: IntArray) -> IntArray:
    """A route's nodes, a round trip's start once."""
    return nodes[:-1] if len(nodes) > 1 and nodes[0] == nodes[-1] else nodes


def route_delay_s(data: GraphData, nodes: IntArray) -> float:
    """The delays at a route's nodes (`node_delay_s`: signals, stop signs), its start and end
    included, a round trip's start once."""
    return math.fsum(data.node_delay_s[counted_nodes(nodes)].astype(np.float64).tolist())
