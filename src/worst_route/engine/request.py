"""A route request and the masks it implies (`docs/reference/engine.md`, **Requests**).

`RouteRequest` is what a caller asks for: the stops as (lat, lon) in order (the first the start,
the last the end; the same point first and last makes a round trip), the objective's key (one
of `objectives.OBJECTIVES`), the tier (`"auto"` or `"T0"`..`"T4"`), the leash in km (`"auto"` or
a number, at most 150), the budget in seconds (at most 120) and iterations, and the seed. Every
number is finite, so it round-trips through JSON (`model_dump(mode="json")`) without
`Infinity`.

`request_masks(spatial, data, request)` turns it into `RequestMasks`, from the request alone,
so the validator can recompute them from a saved route. The stops are projected and the trip
length is the straight-line length of the polyline through them.

- tier auto: below 15 km T4, below 60 km T3, below 250 km T2, below 800 km T1, else T0
  (`config.TIER_AUTO_KM`);
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
from typing import Annotated, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
from worst_route.engine.objectives import OBJECTIVES
from worst_route.graph.data import GraphData
from worst_route.graph.spatial import SpatialIndex, active_mask

TierChoice = Literal["auto", "T0", "T1", "T2", "T3", "T4"]


class RouteRequest(BaseModel):
    """What to plan: stops in order, the objective, the scale, the budget and the seed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stops: list[tuple[float, float]] = Field(min_length=2)
    objective: str = "distance"
    tier: TierChoice = "auto"
    leash_km: Annotated[float, Field(gt=0, le=LEASH_MAX_KM)] | Literal["auto"] = "auto"
    budget_s: float = Field(default=DEFAULT_BUDGET_S, gt=0, le=BUDGET_MAX_S)
    max_iterations: int = Field(default=DEFAULT_MAX_ITERATIONS, ge=0)
    seed: int = Field(ge=0)

    @field_validator("objective")
    @classmethod
    def _known(cls, objective: str) -> str:
        if objective not in OBJECTIVES:
            raise ValueError(f"unknown objective {objective!r}; known: {', '.join(OBJECTIVES)}")
        return objective

    @field_validator("stops")
    @classmethod
    def _on_earth(cls, stops: list[tuple[float, float]]) -> list[tuple[float, float]]:
        for lat, lon in stops:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"({lat}, {lon}) is not a latitude and longitude")
        return stops


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
