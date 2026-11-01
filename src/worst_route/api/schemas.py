"""The API's request and response shapes (`docs/reference/api.md`; `docs/PLAN.md` §4).

`RouteRequestIn`, the body of `POST /jobs`, mirrors `engine.request.RouteRequest` field for
field under the engine's names (the objective as any string, which the endpoint checks, the
item `collect` collects and the brand `brand` visits), with the stops as `{lat, lon, label}`
objects, the target as
`{kind: "max"}`, `{kind: "ratio", value}` or `{kind: "duration", seconds}` (the engine's
`Target`, whose bounds it checks), the turn constraints as the engine's
`{no_left, no_right, strict}` and `reorder` (the stops between the ends in the worst order), plus
`region`, `vehicle` and `departure` (kept with the route for the presentation) and
`base_route_id` ("make it worse": the stops, in the order that route visits them, tier and leash
then come from that route, so `stops` may be left out, and `reorder` is off).
`engine_request(seed)` is the engine's request. `SnapIn` is the body of `POST /snap`.

The responses: `Health`, `RegionOut`, `SnapOut`, `JobCreated`, `JobStatus`, `RouteDocument`
(its metrics with `TargetMetrics` for a ratio or duration target, `ItemCounts`, the route's
collectibles, `BrandVisits` for a pilgrimage and `AlphabetLetters` for an alphabet route),
`ValidationOut`, `BrandsOut` (`GET /brands`, with its `BrandOut`s), and `ErrorBody` (`{code,
message}` and the error's own fields) for every error. The document's metrics count the route's
turns (`Turns`), its delays and its collectibles, and with the presentation's numbers (fuel,
states and towns, the share of the roads) it carries its directions (`DirectionStep`s) and
toasts; the metrics of a route while it grows (`RouteMetrics`: `progress`, `snapshot`, the job
status) carry its delays, and `Done`'s (`DoneMetrics`) the target and the turns too, and the
stored route's other metrics (its item counts, a collecting route's item, a pilgrimage's brand,
an alphabet route's letters) as extra fields.
A route document and the `snapped` event carry `order`, each stop's position among the stops
the job was given, in the order the route visits them (the identity without `reorder`).
The SSE events' payloads are `Snapped`, `BaselineOut`, `Progress` (with `SpliceOut`s),
`Snapshot`, `Done` and `ErrorBody`. Coordinates are `[lon, lat]` in GeoJSON and `{lat, lon}` in
requests; every number is metric.

`GeocodeOut`, with its `GeocodeCandidateOut`s, answers `GET /geocode`, and `DirectionsOut`
`GET /routes/{id}/directions`; `RegionPickIn` is the body of `POST /regions/pick`, which
`RegionPickOut` answers, and `DailyOut`, with its `DailyStop`s, answers `GET /daily`; `DrawIn` is
the body of `POST /draw` and `POST /draw/finish` (a request as `POST /jobs` takes it and the path
drawn), which `DrawOut` (with `DrawAt` and its `DrawMove`s) and `DrawFinished` answer (at the end
of the module). A route drawn by hand stops because it was `drawn`, which only its run says.
"""

from datetime import datetime
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from worst_route.api.store import DailyStatus
from worst_route.config import (
    API_DEFAULT_MAX_ITERATIONS,
    DEFAULT_BUDGET_S,
    LEASH_MAX_KM,
    MAX_DRAW_EDGES,
)
from worst_route.engine.constraints import NO_CONSTRAINTS, Constraints
from worst_route.engine.objectives.collect import CollectItem
from worst_route.engine.request import ObjectiveKey, RouteRequest, TierChoice
from worst_route.engine.target import Target as EngineTarget
from worst_route.presentation.directions import StepKind

StopReason = Literal["budget", "converged", "target", "cancelled", "timeout", "drawn"]
TurnName = Literal["left", "right", "straight", "u_turn"]  # `engine.turns.TURN_NAMES`
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
    brand: int | None = Field(default=None, ge=0)
    constraints: Constraints = NO_CONSTRAINTS
    reorder: bool = False
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
            brand=self.brand,
            constraints=self.constraints,
            reorder=self.reorder,
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
    name: str  # what the frontend calls it (`config.Region.display`)
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
    """A route's numbers while it grows, the delays at its signals and stop signs among them;
    the objective's counters ride along as extra fields."""

    model_config = ConfigDict(extra="allow")

    length_m: float
    time_s: float
    delay_s: float
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
    baseline's, its target (null for max) and its turns."""

    time_ratio: float | None
    target: TargetMetrics | None = None
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
    """The `snapped` event: the stops as the job resolved them, in the order the route visits
    them, `order` (each one's position among the stops given), and the request's scale."""

    stops: list[StopSnapped]
    order: list[int]
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
    """The `progress` event: every splice accepted since the last one, in order, and the best
    route's score and numbers so far (a max run that anneals takes shorter splices too, so its
    current route may be below its best)."""

    iteration: int
    elapsed_s: float
    score: float
    metrics: RouteMetrics
    splices: list[SpliceOut]
    best_score: float
    best_metrics: RouteMetrics


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


class BrandVisits(_Out):
    """A pilgrimage's brand, how many of its locations the route passes and how many the
    request's area holds (`objectives.brand.brand_counts`)."""

    id: int
    name: str
    visits: int
    of: int


class AlphabetLetters(_Out):
    """An alphabet route's letters (`alphabet_search.alphabet_counts`): how many, in order, the
    first road of each, and where its alphabet part starts and ends (edge positions: its first
    A road's first edge, and the first edge of its free last leg)."""

    letters: int
    sequence: str
    roads: list[str]
    alpha_from: int
    free_from: int


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
    brand: BrandVisits | None = None  # a pilgrimage's; null for every other objective
    alphabet: AlphabetLetters | None = None  # an alphabet route's; null for every other one
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
    order: list[int]
    geometry: LineString
    legs: list[Leg]
    metrics: DocumentMetrics
    run: Run
    validation: Validation
    directions: list[DirectionStep]
    toasts: list[str]


class BrandOut(_Out):
    """A brand of the build's index: its id, name, wikidata id and its locations (nodes)."""

    id: int
    name: str
    wikidata: str | None
    locations: int


class BrandsOut(_Out):
    """`GET /brands`: the region's brands, the most located first, then by name."""

    region: str
    brands: list[BrandOut]


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


# --- Readiness (`GET /ready`) -----------------------------------------------------------------


class ReadyCheck(_Out):
    """One thing a deployment needs: its name, whether it is there, and what was found."""

    name: str
    ok: bool
    detail: str


class ReadyOut(_Out):
    """`GET /ready`: ready when every check passes (200), else not ready (503)."""

    status: Literal["ready", "not_ready"]
    checks: list[ReadyCheck]


# --- The client's configuration (`GET /config`) ---------------------------------------------


class BasemapOut(_Out):
    """The basemap to draw: OpenStreetMap's raster tiles, Protomaps' vector tiles from the
    server (`tiles`), with the labels' fonts (`glyphs`) and icons (`sprite`) when it has them,
    paths the API's; or OpenFreeMap's vector tiles, drawn in the style at `style` (a URL)."""

    kind: Literal["raster", "pmtiles", "openfreemap"]
    tiles: str | None = None
    glyphs: str | None = None
    sprite: str | None = None
    style: str | None = None


class LimitsOut(_Out):
    """The limits a request meets on this server."""

    budget_max_s: float
    leash_max_km: float


class ConfigOut(_Out):
    """`GET /config`: whether the server is public, its basemap and its limits."""

    public: bool
    basemap: BasemapOut
    limits: LimitsOut


# --- Which build a trip runs on (`POST /regions/pick`) --------------------------------------


class RegionPickIn(_In):
    """`POST /regions/pick`: a trip's stops, one or more."""

    stops: list[StopIn] = Field(min_length=1)


class RegionPickOut(_Out):
    """The served region a trip with those stops runs on, its build, and why, for the UI."""

    region: str
    build_id: str
    reason: str


# --- The daily route (`GET /daily`) ------------------------------------------------------------


class DailyStop(_Out):
    """One of the day's two towns: where the trip starts or ends, and its name ("Bend, OR")."""

    lat: float
    lon: float
    label: str


class DailyOut(_Out):
    """`GET /daily`: the day's trip (the UTC date, the region and build it runs on, its towns, its
    seed and attempts), and how its plan stands: planning (its job's events), done (its route,
    ratio, and what ended the run after how many attempts) or failed (the next call plans it
    again)."""

    date: str  # YYYY-MM-DD
    region: str
    build_id: str
    stops: list[DailyStop]
    seed: int
    max_iterations: int
    status: DailyStatus
    route_id: str | None = None
    events_url: str | None = None
    ratio: float | None = None
    stopped_because: StopReason | None = None
    attempts: int | None = None


# --- A route drawn by hand (`POST /draw`, `POST /draw/finish`) --------------------------------


class DrawIn(_In):
    """`POST /draw` and `POST /draw/finish`: the request drawn on, as `POST /jobs` takes it (its
    region among its fields), and the path drawn, its edges in order from the start (none yet:
    the start)."""

    request: RouteRequestIn
    path: list[Annotated[int, Field(ge=0, le=2**31 - 1)]] = Field(max_length=MAX_DRAW_EDGES)


class DrawAt(_Out):
    """The node a drawing is at."""

    node: int
    lat: float
    lon: float


class DrawMove(_Out):
    """A legal next move: its edge, the node it leads to, the road's name (else its ref), its
    length, the turn into it from the last edge (null for the first move), the stop it reaches
    (its index, or null) and its shape (`[lon, lat]` points, 7 decimals)."""

    edge: int
    node: int
    lat: float
    lon: float
    name: str | None
    length_m: float
    turn: TurnName | None
    stop: int | None
    coords: list[list[float]]


class DrawOut(_Out):
    """`POST /draw`: the drawing, always legal (an illegal path is refused): the stops as the
    engine resolves them, where it is, the positions along it of the stops reached in order, its
    length against the baseline's, its ratio once done (null before, and for a round trip), its
    line (null for no edge yet), its legal next moves and whether it has reached the end."""

    stops: list[StopSnapped]
    at: DrawAt
    reached: list[int]
    legal: Literal[True] = True
    length_m: float
    baseline_length_m: float
    ratio: float | None
    geometry: LineString | None
    next: list[DrawMove]
    done: bool


class DrawFinished(_Out):
    """`POST /draw/finish`: the finished drawing stored as a route, and its ratio."""

    route_id: str
    ratio: float | None
