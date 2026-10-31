"""The API's request and response shapes (`docs/reference/api.md`; `docs/PLAN.md` §4).

`RouteRequestIn`, the body of `POST /jobs`, mirrors `engine.request.RouteRequest` field for
field under the engine's names (the objective as any string, so a planned one is `NOT_YET`, and
the item `collect` collects), with the stops as `{lat, lon, label}` objects, the target as
`{kind: "max"}`, `{kind: "ratio", value}` or `{kind: "duration", seconds}` (the engine's
`Target`, whose bounds it checks) and the turn constraints as the engine's
`{no_left, no_right, strict}`, plus `region`, `vehicle` and `departure` (kept with the
route for the presentation) and `base_route_id` ("make it worse": the stops, tier and leash then
come from that route, so `stops` may be left out). `engine_request(seed)` is the engine's
request. `SnapIn` is the body of `POST /snap`.

The responses: `Health`, `RegionOut`, `SnapOut`, `JobCreated`, `JobStatus`, `RouteDocument`
(its metrics with `TargetMetrics` for a ratio or duration target, and `ItemCounts`, the route's
collectibles), `ValidationOut`, and `ErrorBody` (`{code, message}` and the error's own fields)
for every error. The document's metrics count the route's turns (`Turns`), its delays and its
collectibles, and with the presentation's numbers (fuel, states and towns, the share of the
roads) it carries its directions (`DirectionStep`s) and toasts; `Done`'s metrics
(`DoneMetrics`) carry the target, the turns and the delays too, and the stored route's other
metrics (its item counts, a collecting route's item) as extra fields.
The SSE events' payloads are `Snapped`, `BaselineOut`, `Progress` (with `SpliceOut`s),
`Snapshot`, `Done` and `ErrorBody`. Coordinates are `[lon, lat]` in GeoJSON and `{lat, lon}` in
requests; every number is metric.

`GeocodeOut`, with its `GeocodeCandidateOut`s, answers `GET /geocode`, and `DirectionsOut`
`GET /routes/{id}/directions` (at the end of the module).
"""

from datetime import datetime
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from worst_route.config import API_DEFAULT_MAX_ITERATIONS, DEFAULT_BUDGET_S, LEASH_MAX_KM
from worst_route.engine.constraints import NO_CONSTRAINTS, Constraints
from worst_route.engine.objectives.collect import CollectItem
from worst_route.engine.request import ObjectiveKey, RouteRequest, TierChoice
from worst_route.engine.target import Target as EngineTarget
from worst_route.presentation.directions import StepKind

# Modes the plan describes that the engine does not have yet: asked for, they are NOT_YET.
PLANNED_OBJECTIVES = ("brand", "alphabet")
StopReason = Literal["budget", "converged", "target", "cancelled", "timeout"]
JobState = Literal["queued", "running", "done", "error"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class StopIn(_In):
    """A stop as asked for."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str | None = Field(default=None, max_length=200)


class Target(_In):
    """How much worse: `max` (as bad as the budget allows), a `ratio` to the baseline (`value`,
    ×, more than 1) or a `duration` (`seconds`, at most 7 days), within ±2 %."""

    kind: Literal["max", "ratio", "duration"] = "max"
    value: float | None = Field(default=None, gt=0)  # ratio, ×
    seconds: float | None = Field(default=None, gt=0)  # duration

    @model_validator(mode="after")
    def _one_number(self) -> "Target":
        wanted = {"max": None, "ratio": "value", "duration": "seconds"}[self.kind]
        for name in ("value", "seconds"):
            if name != wanted and getattr(self, name) is not None:
                raise ValueError(f"a {self.kind} target takes no {name}")
        self.engine_target()  # its bounds
        return self

    def engine_target(self) -> EngineTarget:
        """The engine's target."""
        return EngineTarget(
            kind=self.kind, value=self.value if self.seconds is None else self.seconds
        )


class Vehicle(_In):
    """The car, for the presentation's fuel numbers (M4)."""

    mpg: float = Field(gt=0)
    gas_price_usd: float = Field(ge=0)


class RouteRequestIn(_In):
    """`POST /jobs`: the engine's request, the region, and what the presentation keeps."""

    region: str
    stops: list[StopIn] | None = Field(default=None, min_length=2)
    objective: str = "distance"
    item: CollectItem | None = None
    constraints: Constraints = NO_CONSTRAINTS
    tier: TierChoice = "auto"
    leash_km: Annotated[float, Field(gt=0, le=LEASH_MAX_KM)] | Literal["auto"] = "auto"
    budget_s: float = Field(default=DEFAULT_BUDGET_S, gt=0)
    max_iterations: int = Field(default=API_DEFAULT_MAX_ITERATIONS, ge=0)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    target: Target = Target()
    vehicle: Vehicle | None = None
    departure: datetime | None = None
    base_route_id: str | None = None

    @model_validator(mode="after")
    def _stops_or_base(self) -> "RouteRequestIn":
        if self.stops is None and self.base_route_id is None:
            raise ValueError("stops are needed unless base_route_id names the route to continue")
        return self

    def engine_request(self, seed: int) -> RouteRequest:
        """The engine's request for these stops with `seed`."""
        if self.stops is None:
            raise ValueError("no stops")
        return RouteRequest(
            stops=[(s.lat, s.lon) for s in self.stops],
            objective=cast(ObjectiveKey, self.objective),  # checked by the endpoint, and here
            item=self.item,
            constraints=self.constraints,
            tier=self.tier,
            leash_km=self.leash_km,
            budget_s=self.budget_s,
            max_iterations=self.max_iterations,
            seed=seed,
            target=self.target.engine_target(),
        )


class SnapIn(_In):
    """`POST /snap`: where a stop would go."""

    region: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    role: Literal["start", "via", "end"]
    tier: TierChoice = "auto"


class ErrorBody(_Out):
    """Every error: a code, a sentence, and the error's own fields (`stop_index`, `leg`, ...)."""

    model_config = ConfigDict(extra="allow")

    code: str
    message: str


class BuildOut(_Out):
    region: str
    build_id: str
    graph_version: str


class Health(_Out):
    status: Literal["ok"] = "ok"
    builds: list[BuildOut]


class TierOut(_Out):
    tier: str
    nodes: int
    edges: int


class RegionOut(_Out):
    region: str
    build_id: str
    graph_version: str
    bbox: tuple[float, float, float, float]  # min lon, min lat, max lon, max lat
    counts: dict[str, int]
    tiers: list[TierOut]


class SnapOut(_Out):
    node: int
    lat: float
    lon: float
    moved_m: float
    note: str | None


class JobCreated(_Out):
    job_id: str
    events_url: str
    seed: int


class LineString(_Out):
    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]]


class RouteMetrics(_Out):
    """A route's numbers while it grows; the objective's counters ride along as extra fields."""

    model_config = ConfigDict(extra="allow")

    length_m: float
    time_s: float
    ratio: float | None


class TargetMetrics(_Out):
    """A ratio or duration target and what was achieved (× for a ratio, seconds for a duration)."""

    kind: Literal["ratio", "duration"]
    value: float
    achieved: float
    within_tolerance: bool


class Turns(_Out):
    """A route's turns by class (`engine/turns.py`)."""

    left: int
    right: int
    straight: int
    u_turn: int


class DoneMetrics(RouteMetrics):
    """`done`'s metrics: the stored route's, with its experienced time's ratio to the
    baseline's, its target (null for max), its delays and its turns."""

    time_ratio: float | None
    target: TargetMetrics | None = None
    delay_s: float
    turns: Turns


class LatLon(_Out):
    lat: float
    lon: float


class StopSnapped(_Out):
    input: LatLon
    label: str | None
    node: int
    lat: float
    lon: float
    moved_m: float
    note: str | None


class Snapped(_Out):
    """The `snapped` event: the stops as the job resolved them, and the request's scale."""

    stops: list[StopSnapped]
    tier: str
    leash_km: float
    active_nodes: int


class BaselineOut(_Out):
    """The `baseline` event: the sensible route (no geometry for a round trip)."""

    length_m: float
    time_s: float
    geometry: LineString | None


class SpliceOut(_Out):
    """One accepted splice: replace route nodes i..j by `node_count` nodes, drawn by `coords`;
    `node_index[k]` is the index in `coords` of the detour's node k."""

    i: int
    j: int
    node_count: int
    coords: list[list[float]]
    node_index: list[int]


class Progress(_Out):
    """The `progress` event: every splice accepted since the last one, in order."""

    iteration: int
    elapsed_s: float
    score: float
    metrics: RouteMetrics
    splices: list[SpliceOut]


class Snapshot(_Out):
    """The `snapshot` event: the whole route, each edge simplified on its own so every node is a
    vertex; `node_index[k]` is the index in `geometry` of route node k."""

    iteration: int
    elapsed_s: float
    node_count: int
    stop_idx: list[int]
    geometry: LineString
    node_index: list[int]
    metrics: RouteMetrics


class Done(_Out):
    """The `done` event: the route is stored as `route_id`."""

    route_id: str
    stopped_because: StopReason
    metrics: DoneMetrics


class JobStatus(_Out):
    """`GET /jobs/{id}` (with the current snapshot) and `DELETE /jobs/{id}` (without)."""

    job_id: str
    status: JobState
    region: str
    build_id: str
    seed: int
    created: datetime
    started: datetime | None
    finished: datetime | None
    last_event_id: int
    iteration: int
    elapsed_s: float
    metrics: RouteMetrics | None
    route_id: str | None
    stopped_because: StopReason | None
    error: ErrorBody | None
    snapshot: Snapshot | None


class RouteStop(_Out):
    label: str | None
    node: int
    lat: float
    lon: float
    moved_m: float
    note: str | None
    route_index: int


class Leg(_Out):
    from_stop: int
    to_stop: int
    node_range: tuple[int, int]


class ItemCounts(_Out):
    """How many of each collectible a route passes (`objectives.collect.count_items`)."""

    toll_booth: int
    level_crossing: int
    roundabout: int
    bridge: int


class DocumentMetrics(_Out):
    length_m: float
    baseline_length_m: float
    ratio: float | None
    time_s: float
    baseline_time_s: float
    baseline_delay_s: float
    time_ratio: float | None
    departure: datetime | None
    arrival: datetime | None
    target: TargetMetrics | None
    delay_s: float
    turns: Turns
    items: ItemCounts
    # the presentation's (`presentation.stats`), null where they cannot be known
    extra_fuel_gal: float | None
    extra_cost_usd: float | None
    states: list[str] | None
    towns: list[str] | None
    towns_n: int | None
    network_fraction: float


class Run(_Out):
    attempts: int
    accepted: int
    stopped_because: StopReason
    elapsed_s: float
    backend: str


class Validation(_Out):
    ok: bool
    checked_at: datetime


class DirectionStep(_Out):
    """One step of a route's directions (`presentation.directions`): `edge_range` is half-open,
    [a, b) over the route's edge positions, empty for a stop, the arrival and a joke."""

    step: int
    text: str
    distance_m: float
    time_s: float
    edge_range: tuple[int, int]
    kind: StepKind


class RouteDocument(_Out):
    """`GET /routes/{id}`: a stored route, with its directions and its toasts."""

    route_id: str
    created: datetime
    graph_version: str
    engine_version: str
    region: str
    build_id: str
    seed: int
    request: RouteRequestIn
    stops: list[RouteStop]
    geometry: LineString
    legs: list[Leg]
    metrics: DocumentMetrics
    run: Run
    validation: Validation
    directions: list[DirectionStep]
    toasts: list[str]


class CheckOut(_Out):
    name: str
    ok: bool
    index: int | None
    detail: str


class ValidationOut(_Out):
    """`POST /routes/{id}/validate`: the validator's report on the stored route."""

    route_id: str
    graph_version: str
    ok: bool
    checks: list[CheckOut]


# --- Geocoding (`GET /geocode`; `docs/reference/api.md`, **Geocoding**) ---------------------


class GeocodeCandidateOut(_Out):
    """A place a query may mean: its label, where it is, its main OSM tag (`amenity/cafe`)."""

    label: str
    lat: float
    lon: float
    category: str


class GeocodeOut(_Out):
    """`GET /geocode`: the query as normalised, the region, at most `GEOCODE_LIMIT` candidates
    (best first), and whether the cache answered."""

    query: str
    region: str
    candidates: list[GeocodeCandidateOut]
    cached: bool


# --- Directions (`GET /routes/{id}/directions`) ---------------------------------------------


class DirectionsOut(_Out):
    """`GET /routes/{id}/directions`: the route's directions alone, as its document has them."""

    route_id: str
    directions: list[DirectionStep]
