"""Inefficiency targets and the goal a run pursues (`docs/PLAN.md` §3.4; `docs/reference/engine.md`,
**Targets**).

`Target(kind, value)` is what a request asks for: `max` (as bad as the budget allows; no value),
`ratio` (the route's primary quantity as `value` × the baseline's, more than 1: its length, or
its experienced time, driving and delays, under the slowest-time objective) or `duration`
(`value` seconds of experienced time, `time_s + delay_s`, at most `TARGET_MAX_DURATION_S`). A
target is reached within ±`TARGET_TOLERANCE` of its value: the band [T × (1 − tol), T × (1 +
tol)], ends included.

`Goal(target, data, primary=, baseline=)` is what the worsen loop asks about a route, by its
value: the score for `max`; for `ratio`, `primary(metrics)` (the objective's,
`Objective.primary`, which a caller reads from its class) over the baseline's
`primary(baseline)`; `time_s + delay_s` for `duration`. Its rules work on values, not routes,
so the API's follower can apply them to the score and metrics of each splice it is sent.

- `better(a, b)`: a higher score for `max`; for a target, no farther from it (ties go to a, the
  later route).
- `reached(v)` and `phase(v)`: `grow` below the band, `shrink` above it, `done` inside; `max` is
  always `grow` and never reached.
- `candidate(route, i, j, edges, gain)`: the value after replacing nodes[i..j] by a detour,
  from the window alone (`math.fsum` of the edges' lengths and times and of the delays at the
  nodes it adds and removes).
- `accepts(value, gain, candidate, held)`: for `max`, a gain (`gain > 0`). Growing, a gain that
  moves the value up without passing the band's top; shrinking, a loss of the objective's own
  (`gain − held < 0`, `held` being the constraints' part of the gain, `constraint_delta`) that
  moves the value down without passing the band's bottom and adds no penalised turn (`held ≥
  0`), so a soft constraint's turns never pile up while a route shrinks. Both value conditions
  hold at once for a ratio of the score's own quantity (the distance objective's length);
  otherwise (a duration under distance) they keep the value monotone when the score and the
  value disagree.
- `overshoots(value, gain, candidate)`: growing, a gain that would pass the band's top; the loop
  keeps the smallest one and takes it only when a whole cycle found nothing it accepts.
- `shrink_rank(candidate)`: shrinking, how close a detour would bring the route to the band (0
  inside it, infinity below it), by which `find_detour` ranks the local-exhaustive paths.
- `describe(value)`: `{kind, value, achieved, within_tolerance}` for a target, None for `max`.
"""

import math
from collections.abc import Callable
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

from worst_route.config import TARGET_MAX_DURATION_S, TARGET_TOLERANCE
from worst_route.engine.route import IntArray, Metrics, Route
from worst_route.graph.data import GraphData

TargetKind = Literal["max", "ratio", "duration"]
Phase = Literal["grow", "shrink", "done"]


class Target(BaseModel):
    """How much worse: as bad as possible, a ratio to the baseline, or a duration in seconds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TargetKind = "max"
    value: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _bounded(self) -> "Target":
        if self.kind == "max":
            if self.value is not None:
                raise ValueError("a max target takes no value")
        elif self.value is None:
            raise ValueError(f"a {self.kind} target needs a value")
        elif self.kind == "ratio" and not self.value > 1:
            raise ValueError(f"a ratio target must be more than 1, not {self.value:g}")
        elif self.kind == "duration" and not 0 < self.value <= TARGET_MAX_DURATION_S:
            raise ValueError(
                f"a duration target must be in (0, {TARGET_MAX_DURATION_S:g}] s, not {self.value:g}"
            )
        return self


class Goal:
    """What a run is after, for one build and baseline; see the module docstring."""

    def __init__(
        self,
        target: Target,
        data: GraphData,
        *,
        primary: Callable[[Metrics], float],
        baseline: Metrics,
    ) -> None:
        self.target = target
        self.primary = primary
        self.baseline = primary(baseline)
        self.maximise = target.kind == "max"
        self.value_wanted = target.value if target.value is not None else math.inf
        self.low = self.value_wanted * (1 - TARGET_TOLERANCE)
        self.high = self.value_wanted * (1 + TARGET_TOLERANCE)
        # the build's own arrays, read per window (float32 → Python float is exact)
        self._lengths, self._times = data.edge_length_m, data.edge_time_s
        self._delays, self._heads = data.node_delay_s, data.edge_v

    def value(self, score: float, metrics: Metrics) -> float:
        """The quantity this goal compares, of a route with this score and these metrics."""
        if self.target.kind == "ratio":
            return self.primary(metrics) / self.baseline
        if self.target.kind == "duration":
            return metrics.time_s + metrics.delay_s
        return score

    def better(self, a: float, b: float) -> bool:
        """Whether a route of value a is better than one of value b (a is the later one)."""
        if self.maximise:
            return a > b
        return abs(a - self.value_wanted) <= abs(b - self.value_wanted)

    def reached(self, value: float) -> bool:
        """Whether a route of this value is within the target's tolerance."""
        return not self.maximise and self.low <= value <= self.high

    def phase(self, value: float) -> Phase:
        """`grow` below the band (always, for max), `shrink` above it, `done` inside."""
        if self.maximise or value < self.low:
            return "grow"
        return "shrink" if value > self.high else "done"

    def candidate(self, route: Route, i: int, j: int, edges: IntArray, gain: float) -> float:
        """The value after replacing nodes[i..j] of `route` by a detour along `edges`."""
        if self.maximise:
            return route.score + gain
        removed = route.edges[i:j]
        passed = self._heads[edges[:-1]]  # the detour's interior nodes
        after = Metrics(
            route.metrics.length_m + _change(self._lengths, edges, removed),
            route.metrics.time_s + _change(self._times, edges, removed),
            delay_s=route.metrics.delay_s + _change(self._delays, passed, route.nodes[i + 1 : j]),
        )
        return self.value(route.score + gain, after)

    def accepts(self, value: float, gain: float, candidate: float, held: float = 0.0) -> bool:
        """Whether the loop takes a detour of this gain (`held` of it the constraints') and
        resulting value."""
        if self.maximise:
            return gain > 0
        phase = self.phase(value)
        if phase == "grow":
            return gain > 0 and value < candidate <= self.high
        if phase == "shrink":
            return held >= 0 and gain - held < 0 and self.low <= candidate < value
        return False

    def overshoots(self, value: float, gain: float, candidate: float) -> bool:
        """Whether a detour would carry a growing route past the band's top."""
        return (
            self.phase(value) == "grow" and not self.maximise and gain > 0 and candidate > self.high
        )

    def shrink_rank(self, candidate: float) -> float:
        """How a shrinking run ranks a detour of this value: 0 within the band, else how far above
        it, and last (infinity) below it, where the run would not take it."""
        if candidate < self.low:
            return math.inf
        return max(candidate - self.high, 0.0)

    def describe(self, value: float) -> dict[str, Any] | None:
        """The target and what was achieved, for a route's metrics; None for max."""
        if self.maximise:
            return None
        return {
            "kind": self.target.kind,
            "value": self.value_wanted,
            "achieved": value,
            "within_tolerance": self.reached(value),
        }


def _change(values: NDArray[np.float32], added: IntArray, removed: IntArray) -> float:
    """What replacing the `removed` edges (or nodes) by the `added` ones changes a sum over them
    by."""
    return math.fsum(values[added].tolist() + (-values[removed]).tolist())
