"""An objective with constraints (`docs/PLAN.md` §3.7; `docs/reference/engine.md`,
**Constraints**).

`Composite(objective, constraints)` is the one `Objective` the engine runs when a request adds
constraints (`engine/constraints.py`) to its objective. Its score is the sum of theirs (the
objective's terms, plus a soft constraint's pair terms, its penalties), and so is `delta`; its
metrics are the objective's. A turn (`feasible_pair`) or a window (`feasible_window`) is
feasible only when the objective and every constraint agree; the bans of a window are the
union of theirs, and so are the turn classes the searches refuse (`banned_turn_classes`) or try
without first (`avoided_turn_classes`); the search weights, the waypoint pool, the radii, the
target value, `describe` and the primary quantity are the objective's; `constraint_delta` is
the constraints' part of `delta`, which a run shrinking toward a target reads (`Goal.accepts`).
The worsen loop cannot tell it from the objective alone.
"""

import math
from collections.abc import Sequence

from worst_route.engine.objectives.base import BoolArray, FloatArray, Objective
from worst_route.engine.route import IntArray, Metrics, Route
from worst_route.graph.data import GraphData


class Composite:
    """The objective's guidance, every part's terms, feasibility and bans."""

    def __init__(self, objective: Objective, constraints: Sequence[Objective]) -> None:
        self.objective = objective
        self.constraints = tuple(constraints)
        self.key = objective.key
        self._all = (objective, *self.constraints)
        self.has_pair = any(part.has_pair for part in self._all)
        self.has_global = any(part.has_global for part in self._all)
        self.banned_turn_classes = self.avoided_turn_classes = 0
        for part in self._all:
            self.banned_turn_classes |= part.banned_turn_classes
            self.avoided_turn_classes |= part.avoided_turn_classes

    def node_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803 (the plan's G)
        """The objective's."""
        return self.objective.node_terms(G)

    def edge_terms(self, G: GraphData) -> FloatArray | None:  # noqa: N803
        """The objective's."""
        return self.objective.edge_terms(G)

    def pair_term(self, G: GraphData, e_in: int, e_out: int) -> float:  # noqa: N803
        """Every part's, summed."""
        return math.fsum(part.pair_term(G, e_in, e_out) for part in self._all)

    def feasible_pair(self, G: GraphData, e_in: int, e_out: int) -> bool:  # noqa: N803
        """Whether the objective and every constraint allow the turn."""
        return all(part.feasible_pair(G, e_in, e_out) for part in self._all)

    def global_term(self, G: GraphData, nodes: IntArray, edges: IntArray) -> float:  # noqa: N803
        """The objective's."""
        return self.objective.global_term(G, nodes, edges)

    def feasible_window(
        self,
        G: GraphData,  # noqa: N803
        route: Route,
        i: int,
        j: int,
        nodes: IntArray,
        edges: IntArray,
    ) -> bool:
        """Whether the objective and every constraint allow the window."""
        return all(part.feasible_window(G, route, i, j, nodes, edges) for part in self._all)

    def banned_edges(self, G: GraphData, route: Route, i: int, j: int) -> BoolArray | None:  # noqa: N803
        """Every edge the objective or a constraint bans for the window, or None for none."""
        union: BoolArray | None = None
        for part in self._all:
            bans = part.banned_edges(G, route, i, j)
            if bans is not None:
                union = bans.copy() if union is None else union | bans
        return union

    def search_weights(self, G: GraphData) -> FloatArray:  # noqa: N803
        """The objective's."""
        return self.objective.search_weights(G)

    def waypoint_pool(self, G: GraphData, route: Route) -> IntArray | None:  # noqa: N803
        """The objective's."""
        return self.objective.waypoint_pool(G, route)

    def initial_radius(self, route: Route) -> float:
        """The objective's."""
        return self.objective.initial_radius(route)

    def search_budget(self, radius: float) -> float:
        """The objective's."""
        return self.objective.search_budget(radius)

    def target_value(self, G: GraphData, route: Route) -> float:  # noqa: N803
        """The objective's."""
        return self.objective.target_value(G, route)

    def describe(self, G: GraphData, route: Route) -> dict[str, float]:  # noqa: N803
        """The objective's."""
        return self.objective.describe(G, route)

    def primary(self, metrics: Metrics) -> float:
        """The objective's."""
        return self.objective.primary(metrics)

    def constraint_delta(
        self, route: Route, i: int, j: int, nodes: IntArray, edges: IntArray
    ) -> float:
        """The constraints' part of `delta`: their deltas, summed."""
        return math.fsum(part.delta(route, i, j, nodes, edges) for part in self.constraints)

    def evaluate(self, nodes: IntArray, edges: IntArray) -> tuple[float, Metrics]:
        """Every part's score, summed, and the objective's metrics."""
        score, metrics = self.objective.evaluate(nodes, edges)
        scores = [part.evaluate(nodes, edges)[0] for part in self.constraints]
        return math.fsum([score, *scores]), metrics

    def score(self, route: Route) -> float:
        """Every part's, summed."""
        return self.evaluate(route.nodes, route.edges)[0]

    def delta(self, route: Route, i: int, j: int, nodes: IntArray, edges: IntArray) -> float:
        """Every part's, summed."""
        return math.fsum(part.delta(route, i, j, nodes, edges) for part in self._all)
