"""The turn constraints: few left turns, or right turns, or none at all (`docs/PLAN.md` §3.7;
`docs/reference/engine.md`, **Constraints**).

`Constraints(no_left, no_right, strict)` is what a request adds to its objective; `no_left` and
`no_right` at once is refused ("straight only is not a route"). Each is an objective with no
node, edge or global terms (`TurnConstraint`) about two turn classes (`engine/turns.py`):
`NoLeft` about left turns and U-turns, `NoRight` about right turns and U-turns.

- **Soft** (the default): "few left turns". A turn of those classes is a pair term of minus
  `turn_penalty_for(objective)`, a kilometre's worth in the objective's score units: 1,000 m
  (`TURN_PENALTY_M`), or 90 s under the slowest time (`TURN_PENALTY_S`, a kilometre at
  40 km/h). So the loop takes a detour that adds one only for more than a kilometre's worth,
  and one that removes one even if it loses up to that. The classes are the constraint's
  `avoided_turn_classes`: every search for a detour looks first without them and, when that
  finds nothing, again with them (`engine/search.py`); the splice is then decided by the
  score. The seed is not constrained, and the route's metrics count its turns.
- **Strict**: no turn of those classes at all. The classes are its `banned_turn_classes`, which
  every search for the route refuses (`graph/backend.py`): the seed's legs, the round-trip loop
  and every detour strategy; `feasible_pair` and `feasible_window` (the turns at the window's
  two boundaries included) refuse them too, as the safety net. A leg that cannot be built, but
  can without the constraint, is `InfeasibleTurnConstraint` (`unreachable`), its message ending
  with `note` ("under the strict no-left constraint"; the seed asks, `seed_route`); `strict`
  without a constraint is refused. On a street grid this is
  rarely satisfiable together with the rule that no junction repeats: a net left turn without
  left turns is three rights round a block, which comes back through the junction it left
  (`docs/reference/engine.md`, **Constraints**).

The baseline is never constrained (a sane GPS turns left). `constrain(objective, constraints,
data)` is the objective the engine runs: the objective itself without constraints, else a
`Composite` of it and theirs, the soft ones penalising at `turn_penalty_for(objective)`.
"""

import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator

from worst_route.config import TURN_PENALTY_M, TURN_PENALTY_S
from worst_route.engine.objectives.base import BaseObjective, Objective
from worst_route.engine.objectives.composite import Composite
from worst_route.engine.route import IntArray, Metrics, Route
from worst_route.engine.snap import InfeasibleTurnConstraint, StopUnreachable
from worst_route.engine.turns import turn_classes
from worst_route.graph.backend import TURN_CLASS, TURN_LEFT, TURN_RIGHT, TURN_U
from worst_route.graph.data import GraphData


class TurnConstraint(BaseObjective):
    """Penalises (soft, at `penalty` in its objective's score units) or refuses (strict) the
    turns of its classes, `turns`."""

    label = ""  # the request's name for it, as in "no-left"
    turns = 0  # the turn classes it is about, as class bits

    def __init__(self, data: GraphData, *, strict: bool, penalty: float) -> None:
        super().__init__(data, baseline=Metrics(0.0, 0.0), r_max=0.0)
        self.strict = strict
        self.penalty = penalty
        self.has_pair = not strict
        self.banned_turn_classes = self.turns if strict else 0
        self.avoided_turn_classes = 0 if strict else self.turns

    def _refused(self, G: GraphData, e_in: int, e_out: int) -> bool:  # noqa: N803
        delta = (int(G.edge_bearing_out[e_out]) - int(G.edge_bearing_in[e_in])) % 360
        return bool(TURN_CLASS[delta] & self.turns)

    def pair_term(self, G: GraphData, e_in: int, e_out: int) -> float:  # noqa: N803
        """Minus the penalty for a turn of its classes (read when soft)."""
        return -self.penalty if self._refused(G, e_in, e_out) else 0.0

    def feasible_pair(self, G: GraphData, e_in: int, e_out: int) -> bool:  # noqa: N803
        """Strict: whether the turn is of a class it allows; soft: always."""
        return not (self.strict and self._refused(G, e_in, e_out))

    def feasible_window(
        self,
        G: GraphData,  # noqa: N803
        route: Route,
        i: int,
        j: int,
        nodes: IntArray,
        edges: IntArray,
    ) -> bool:
        """Strict: whether every turn of the detour, its two boundaries included, is allowed;
        soft: always."""
        if not self.strict:
            return True
        before, after = route.edges[max(i - 1, 0) : i], route.edges[j : j + 1]
        chain = np.concatenate((before, edges, after))
        return not (turn_classes(G, chain[:-1], chain[1:]) & self.turns).any()


class NoLeft(TurnConstraint):
    """Left turns (and U-turns) at a kilometre each, or, strict, none."""

    key = "no_left"
    label = "no-left"
    turns = TURN_LEFT | TURN_U


class NoRight(TurnConstraint):
    """Right turns (and U-turns) at a kilometre each, or, strict, none."""

    key = "no_right"
    label = "no-right"
    turns = TURN_RIGHT | TURN_U


class Constraints(BaseModel):
    """The turn constraints a request adds to its objective."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    no_left: bool = False
    no_right: bool = False
    strict: bool = False

    @model_validator(mode="after")
    def _a_route(self) -> "Constraints":
        if self.no_left and self.no_right:
            raise ValueError("straight only is not a route: no_left and no_right together")
        if self.strict and not (self.no_left or self.no_right):
            raise ValueError("strict needs no_left or no_right")
        return self

    @property
    def kinds(self) -> list[type[TurnConstraint]]:
        """The constraints asked for."""
        return [kind for kind, on in ((NoLeft, self.no_left), (NoRight, self.no_right)) if on]

    @property
    def banned_turn_classes(self) -> int:
        """The turn classes no search may take: a strict constraint's, else none."""
        classes = 0
        for kind in self.kinds if self.strict else []:
            classes |= kind.turns
        return classes

    @property
    def note(self) -> str:
        """What an unreachable leg's error adds: "under the strict no-left constraint" when
        strict, else nothing."""
        kinds = self.kinds
        return f"under the strict {kinds[0].label} constraint" if kinds and self.strict else ""

    def unreachable(self, leg: tuple[int, int], detail: str = "") -> StopUnreachable:
        """The error for a leg no route covers under these constraints: `InfeasibleTurnConstraint`
        under a strict one (its message ending with `note`), else `StopUnreachable`. The seed asks
        it only once the leg is known to be buildable without them (`seed_route`)."""
        if not self.note:
            return StopUnreachable(leg, detail)
        message = ", ".join(filter(None, [detail, self.note]))
        return InfeasibleTurnConstraint(leg, message, self.kinds[0].key)

    def objectives(self, data: GraphData, penalty: float) -> list[TurnConstraint]:
        """The constraints, as objectives on this build, penalising a turn at `penalty`."""
        return [kind(data, strict=self.strict, penalty=penalty) for kind in self.kinds]


NO_CONSTRAINTS = Constraints()


def turn_penalty_for(objective: Objective) -> float:
    """What a soft constraint charges a turn under `objective`, in its score's units: by its
    primary quantity, `TURN_PENALTY_S` when that is a time (the slowest time's experienced time),
    else `TURN_PENALTY_M` (a length)."""
    in_seconds = objective.primary(Metrics(length_m=0.0, time_s=1.0)) != 0.0
    return TURN_PENALTY_S if in_seconds else TURN_PENALTY_M


def constrain(objective: Objective, constraints: Constraints, data: GraphData) -> Objective:
    """The objective the engine runs: `objective`, with the constraints when there are any."""
    parts = constraints.objectives(data, turn_penalty_for(objective))
    return Composite(objective, parts) if parts else objective
