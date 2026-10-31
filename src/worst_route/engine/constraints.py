"""The constraints: few left turns, or right turns, or none at all, and hard mode
(`docs/PLAN.md` §3.7; `docs/reference/engine.md`, **Constraints**).

`Constraints(no_left, no_right, strict, hard)` is what a request adds to its objective (`hard`
in its JSON only when asked, so a request without it reads as before it existed); `no_left` and
`no_right` at once is refused ("straight only is not a route"). Each turn constraint is an
objective with no
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
`Composite` of it and theirs, the soft ones penalising at `turn_penalty_for(objective)`, and
`HardMode` under hard mode.

**Hard mode** (`HardMode`): each road, an identity (`edge_identity`: a ref, else the
normalised name), in one stretch at most, so once the route leaves a road it never comes back
to it. The rule is over the positions of the whole route's edges: those of each identity form
one run. An edge without one (−1) is free, but not transparent (a road on both sides of an
unnamed edge is two stretches); a stop splits nothing; a round trip is read from its start to
its end, so coming home on the road it left by is a return. It has no terms. Its
`banned_edges` ban, per window, every edge whose identity is on the route outside it, but those
of the edges either side of it, which a detour may extend; a window inside one stretch (the
same identity on both sides) may only be replaced along that road, so every other edge is
banned. Its `feasible_window` is exact: given a legal route, the splice is legal iff the
detour with the window's two neighbours has one stretch per identity, and every identity of
the detour that is on the route outside the window is a neighbour's. Its `window` widens a
window drawn inside one stretch out to the stretch's ends, no farther than the stops either
side, so the loop tries a window a detour can leave the road from rather than one only that
road could replace. `stretches` and `returning` read the rule off a route's edges for the seed
and the base route. The seed keeps the rule itself (`seed_route`, a heuristic), refusing a leg
it found no way to build under it, but can build without it, with `InfeasibleHardMode` ("the seed
found no way from stop 0 to stop 1 under hard mode": a legal route may exist); a base route that
breaks it is refused (`plan.check_base`).
"""

from bisect import bisect_left, bisect_right
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import (
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from worst_route.config import TURN_PENALTY_M, TURN_PENALTY_S
from worst_route.engine.objectives.base import BaseObjective, Objective
from worst_route.engine.objectives.composite import Composite
from worst_route.engine.route import BoolArray, IntArray, Metrics, Route
from worst_route.engine.snap import InfeasibleHardMode, InfeasibleTurnConstraint, StopUnreachable
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


class HardMode(BaseObjective):
    """Each road in one stretch at most; no terms, its hooks keep the rule (see the module)."""

    key = "hard"

    def __init__(self, data: GraphData) -> None:
        super().__init__(data, baseline=Metrics(0.0, 0.0), r_max=0.0)
        self._ids = np.asarray(data.edge_identity)  # not the memmap: indexed per candidate
        self._slot = data.edge_identity.astype(np.intp) + 1  # −1 at slot 0
        count = np.bincount(self._slot, minlength=len(data.strings.identities) + 1)
        self._by_id = np.argsort(self._slot, kind="stable")  # identity x's edges: _edges(x)
        self._first = np.concatenate(([0], np.cumsum(count)))
        self._route: Route | None = None
        self._count = np.zeros(len(count), dtype=np.intp)  # route edges per identity slot
        self._on = np.zeros(data.m, dtype=np.bool_)  # edges whose identity the route drives

    def banned_edges(self, G: GraphData, route: Route, i: int, j: int) -> BoolArray | None:  # noqa: N803
        """The edges of every identity on the route outside the window, but its neighbours';
        inside one stretch, every edge of another identity."""
        self._refresh(route)
        a, b = self._neighbours(route, i, j)
        if a == b >= 0:
            only: BoolArray = self._ids != a
            return only
        bans = self._on.copy()
        inside = self._ids[route.edges[i:j]]
        ids, n = np.unique(inside[inside >= 0], return_counts=True)
        for x in (*ids[self._count[ids + 1] == n].tolist(), a, b):  # the window's own, freed
            if x >= 0:
                bans[self._edges(x)] = False
        return bans

    def feasible_window(
        self,
        G: GraphData,  # noqa: N803
        route: Route,
        i: int,
        j: int,
        nodes: IntArray,
        edges: IntArray,
    ) -> bool:
        """Whether every road stays one stretch once the window is replaced by the detour."""
        self._refresh(route)
        a, b = self._neighbours(route, i, j)
        chain = np.concatenate((route.edges[max(i - 1, 0) : i], edges, route.edges[j : j + 1]))
        ids = self._ids[chain]
        starts = ids[np.concatenate(([True], ids[1:] != ids[:-1])) & (ids >= 0)]
        if len(np.unique(starts)) < len(starts):  # a road twice along the chain
            return False
        new = np.unique(self._ids[edges])
        new = new[new >= 0]
        inside = np.sort(self._ids[route.edges[i:j]])
        there = np.searchsorted(inside, new, side="right") - np.searchsorted(inside, new)
        outside = self._count[new + 1] - there
        return not ((outside > 0) & (new != a) & (new != b)).any()

    def window(self, route: Route, i: int, j: int) -> tuple[int, int]:
        """A window inside one road's stretch (the same identity either side), out to the
        stretch's ends, no farther than the stops either side; any other as drawn."""
        a, b = self._neighbours(route, i, j)
        if a != b or a < 0:
            return i, j
        stops = route.stop_idx
        first, last = stops[bisect_right(stops, i) - 1], stops[bisect_left(stops, j)]
        while i > first and self._ids[route.edges[i - 1]] == a:
            i -= 1
        while j < last and self._ids[route.edges[j]] == a:
            j += 1
        return i, j

    def _refresh(self, route: Route) -> None:
        """The route's identities, counted once per route (every splice makes a new one)."""
        if route is not self._route:
            count = np.bincount(self._slot[route.edges], minlength=len(self._count))
            count[0] = 0
            self._count, self._on, self._route = count, (count > 0)[self._slot], route

    def _neighbours(self, route: Route, i: int, j: int) -> tuple[int, int]:
        """The identities of the edges either side of the window, −1 where there is none."""
        a = int(self._ids[route.edges[i - 1]]) if i > 0 else -1
        b = int(self._ids[route.edges[j]]) if j < len(route.edges) else -1
        return a, b

    def _edges(self, x: int) -> NDArray[np.intp]:
        edges: NDArray[np.intp] = self._by_id[self._first[x + 1] : self._first[x + 2]]
        return edges


def stretches(data: GraphData, edges: IntArray) -> tuple[IntArray, IntArray, IntArray]:
    """Each maximal run of edges of one identity (not −1) along `edges`, in order: its
    identity, its first position and its end (exclusive); none along no edges."""
    ids = data.edge_identity[edges]
    if len(ids) == 0:
        none = np.zeros(0, dtype=np.int32)
        return none, none, none
    start = np.flatnonzero(np.concatenate(([True], ids[1:] != ids[:-1])))
    end = np.concatenate((start[1:], [len(ids)]))
    named = ids[start] >= 0
    return ids[start][named], start[named].astype(np.int32), end[named].astype(np.int32)


def returning(data: GraphData, edges: IntArray) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """The first stretch of a road `edges` came back to, after leaving it, and the stretch of
    it before, as ((start, end) before, (start, end) again), positions in `edges`, ends
    exclusive; None when each road is one stretch."""
    ids, start, end = stretches(data, edges)
    first = np.zeros(len(ids), dtype=np.bool_)
    first[np.unique(ids, return_index=True)[1]] = True
    again = np.flatnonzero(~first)
    if len(again) == 0:
        return None
    k = int(again[0])
    before = int(np.flatnonzero(ids[:k] == ids[k])[-1])
    return (int(start[before]), int(end[before])), (int(start[k]), int(end[k]))


class Constraints(BaseModel):
    """The turn constraints and hard mode a request adds to its objective."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    no_left: bool = False
    no_right: bool = False
    strict: bool = False
    hard: bool = False

    @model_validator(mode="after")
    def _a_route(self) -> "Constraints":
        if self.no_left and self.no_right:
            raise ValueError("straight only is not a route: no_left and no_right together")
        if self.strict and not (self.no_left or self.no_right):
            raise ValueError("strict needs no_left or no_right")
        return self

    @model_serializer(mode="wrap")
    def _hard_only_when_asked(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        fields: dict[str, Any] = handler(self)
        if not self.hard:
            del fields["hard"]
        return fields

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
        """What an unreachable leg's error, or a stop moved for it, adds: "under hard mode",
        "under the strict no-left constraint", both ("under hard mode and the strict no-left
        constraint"), or nothing."""
        kinds = self.kinds
        parts = ["hard mode"] if self.hard else []
        parts += [f"the strict {kinds[0].label} constraint"] if kinds and self.strict else []
        return f"under {' and '.join(parts)}" if parts else ""

    def unreachable(self, leg: tuple[int, int], detail: str = "") -> StopUnreachable:
        """The error for a leg no route covers under these constraints: `InfeasibleTurnConstraint`
        under a strict one (its message ending with `note`), else `InfeasibleHardMode` under
        hard mode ("the seed found no way from stop 0 to stop 1 under hard mode"), else
        `StopUnreachable`. The seed asks it of the constraints to blame, once the leg is known
        to be buildable without them (`seed_route`)."""
        if self.strict:
            message = ", ".join(filter(None, [detail, self.note]))
            return InfeasibleTurnConstraint(leg, message, self.kinds[0].key)
        if self.hard:
            return InfeasibleHardMode(leg, detail)
        return StopUnreachable(leg, detail)

    def objectives(self, data: GraphData, penalty: float) -> list[BaseObjective]:
        """The constraints, as objectives on this build, penalising a turn at `penalty`, and
        hard mode."""
        turns: list[BaseObjective] = [
            kind(data, strict=self.strict, penalty=penalty) for kind in self.kinds
        ]
        return [*turns, HardMode(data)] if self.hard else turns


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
