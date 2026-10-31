"""A route request and the masks it implies (`docs/reference/engine.md`, **Requests**).

`RouteRequest` is what a caller asks for: the stops as (lat, lon) in order (the first the start,
the last the end; the same point first and last makes a round trip), the objective's key
(`"distance"`, `"time"`, `"collect"`, `"brand"` or `"alphabet"`, `objectives.OBJECTIVE_KEYS`;
the alphabet route takes a start and an end only, and no target), the item
it collects (`collect` only, which needs one), the brand it visits (`brand` only, which needs
one: its id in the build's brand index), the turn constraints (`constraints.Constraints`:
none by default), the tier (`"auto"` or `"T0"`..`"T4"`), the leash in km (`"auto"` or a number,
at most 150), the budget in seconds (at most 120) and iterations, the seed, and the target
(`target.Target`: max by default; a ratio target is refused for a round trip, whose baseline is
0 m, and for `collect` and `brand`, which count items and visits rather than metres;
`objective_problem` names what is wrong with an objective, its item, its brand, its target kind
and its number of stops, for the API to refuse it as early). Every number is finite, so it
round-trips through JSON (`model_dump(mode="json")`) without `Infinity`; its JSON leaves the
constraints out when there are none, and the brand when there is none, so a request without them
reads as it did before they existed.

`request_masks(spatial, data, request)` turns it into `RequestMasks`, from the request alone,
so the validator can recompute them from a saved route. The stops are projected and the trip
length is the straight-line length of the polyline through them.

- tier auto: below 800 km T4, else T0 (`config.TIER_AUTO_KM`, four thresholds, T4 below the
  first and T0 from the last; they coincide since the Oregon build's tuning, so T1-T3 are
  override-only);
- leash auto: 0.6 × the trip length within [3, 150] km; `leash` the nodes within it of the
  polyline;
- `bubbles`: the nodes within 3 km of a stop;
- `active`, the node mask every search runs in: leash & (the tier's nodes | bubbles);
- `banned`, the edges every search and every splice refuses: those outside the tier's edge
  mask, unless both their ends lie in a bubble (the local streets near a stop stay usable);
- `extent_m`: the diagonal of the active nodes' bounding box, which with the leash bounds how
  far a detour may wander.

Every array is read-only.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from worst_route.config import (
    BUBBLE_KM,
    BUDGET_MAX_S,
    DEFAULT_BUDGET_S,
    DEFAULT_MAX_ITERATIONS,
    LEASH_FACTOR,
    LEASH_MAX_KM,
    LEASH_MIN_KM,
    TIER_AUTO_KM,
)
from worst_route.engine.constraints import NO_CONSTRAINTS, Constraints
from worst_route.engine.objectives import OBJECTIVE_KEYS
from worst_route.engine.objectives.collect import ITEMS, CollectItem
from worst_route.engine.snap import is_round_trip
from worst_route.engine.target import Target, TargetKind
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex, active_mask

TierChoice = Literal["auto", "T0", "T1", "T2", "T3", "T4"]
ObjectiveKey = Literal["distance", "time", "collect", "brand", "alphabet"]  # OBJECTIVE_KEYS


class RouteRequest(BaseModel):
    """What to plan: stops in order, the objective and its constraints, the scale, the budget,
    the seed and the target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stops: list[tuple[float, float]] = Field(min_length=2)
    objective: ObjectiveKey = "distance"
    item: CollectItem | None = None
    brand: int | None = Field(default=None, ge=0)
    constraints: Constraints = NO_CONSTRAINTS
    tier: TierChoice = "auto"
    leash_km: Annotated[float, Field(gt=0, le=LEASH_MAX_KM)] | Literal["auto"] = "auto"
    budget_s: float = Field(default=DEFAULT_BUDGET_S, gt=0, le=BUDGET_MAX_S)
    max_iterations: int = Field(default=DEFAULT_MAX_ITERATIONS, ge=0)
    seed: int = Field(ge=0)
    target: Target = Target()

    @field_validator("objective", mode="before")
    @classmethod
    def _known(cls, objective: object) -> object:
        if isinstance(objective, str) and objective not in OBJECTIVE_KEYS:
            known = ", ".join(OBJECTIVE_KEYS)
            raise ValueError(f"unknown objective {objective!r}; known: {known}")
        return objective

    @field_validator("stops")
    @classmethod
    def _on_earth(cls, stops: list[tuple[float, float]]) -> list[tuple[float, float]]:
        for lat, lon in stops:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"({lat}, {lon}) is not a latitude and longitude")
        return stops

    @model_validator(mode="after")
    def _ratio_has_a_baseline(self) -> "RouteRequest":
        if self.target.kind == "ratio" and is_round_trip(self.stops):
            raise ValueError("a round trip has no ratio target: its baseline is 0 m")
        return self

    @model_validator(mode="after")
    def _item_fits(self) -> "RouteRequest":
        problem = objective_problem(
            self.objective, self.item, self.target.kind, self.brand, len(self.stops)
        )
        if problem is not None:
            raise ValueError(problem[1])
        return self

    @model_serializer(mode="wrap")
    def _no_constraints_left_out(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        fields: dict[str, Any] = handler(self)
        if self.constraints == NO_CONSTRAINTS:
            del fields["constraints"]
        if self.brand is None:
            del fields["brand"]
        return fields


def objective_problem(
    objective: str,
    item: str | None,
    target: TargetKind,
    brand: int | None = None,
    stops: int = 2,
) -> tuple[str, str] | None:
    """What is wrong with this objective, item, kind of target, brand and number of stops, as
    (the field at fault, a sentence), or None: `collect` needs an item, only it takes one, and it
    takes no ratio; the same for `brand` and its brand; the alphabet route takes no via and no
    target."""
    if objective == "collect" and item is None:
        return "item", f"the collect objective needs an item: {', '.join(ITEMS)}"
    if objective != "collect" and item is not None:
        return "item", "only the collect objective takes an item"
    if objective == "collect" and target == "ratio":
        return "target", "the collect objective takes no ratio target: it counts items, not metres"
    if objective == "brand" and brand is None:
        return "brand", "the brand objective needs a brand"
    if objective != "brand" and brand is not None:
        return "brand", "only the brand objective takes a brand"
    if objective == "brand" and target == "ratio":
        return "target", "the brand objective takes no ratio target: it counts visits, not metres"
    if objective == "alphabet" and stops > 2:
        return "stops", "the alphabet route takes a start and an end"
    if objective == "alphabet" and target != "max":
        return "target", "the alphabet route takes no target: it counts letters"
    return None


@dataclass(frozen=True, eq=False)
class RequestMasks:
    """The tier, the leash and the masks a request implies; see the module docstring."""

    tier: str
    trip_m: float
    leash_m: float
    extent_m: float
    leash: NDArray[np.bool_]
    bubbles: NDArray[np.bool_]
    active: NDArray[np.bool_]
    banned: NDArray[np.bool_]


def auto_tier(trip_m: float) -> str:
    """The tier for a trip of `trip_m` straight-line metres."""
    passed = sum(trip_m >= km * 1000 for km in TIER_AUTO_KM)
    return f"T{len(TIER_AUTO_KM) - passed}"


def auto_leash_m(trip_m: float) -> float:
    """The leash for a trip of `trip_m` straight-line metres."""
    return min(max(LEASH_FACTOR * trip_m, LEASH_MIN_KM * 1000), LEASH_MAX_KM * 1000)


def request_masks(spatial: SpatialIndex, data: GraphData, request: RouteRequest) -> RequestMasks:
    """The masks `request` implies on this build."""
    xy = np.array([spatial.project(lat, lon) for lat, lon in request.stops], dtype=np.float64)
    trip_m = float(np.hypot(*np.diff(xy, axis=0).T).sum())
    tier = auto_tier(trip_m) if request.tier == "auto" else request.tier
    t = int(tier[1])
    leash_m = auto_leash_m(trip_m) if request.leash_km == "auto" else request.leash_km * 1000
    leash = spatial.leash_mask(xy, leash_m)
    bubbles = spatial.bubble_mask(xy, BUBBLE_KM * 1000)
    active = active_mask(data.tier_node_mask[t], leash, bubbles)
    banned = ~data.tier_edge_mask[t] & ~(bubbles[data.edge_u] & bubbles[data.edge_v])
    extent_m = 0.0
    if active.any():
        x, y = data.node_x[active], data.node_y[active]
        extent_m = float(np.hypot(x.max() - x.min(), y.max() - y.min()))
    for mask in (leash, bubbles, active, banned):
        mask.flags.writeable = False
    return RequestMasks(tier, trip_m, leash_m, extent_m, leash, bubbles, active, banned)
