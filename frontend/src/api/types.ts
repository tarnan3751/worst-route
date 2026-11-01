// The API's shapes, written by hand from docs/reference/api.md (the server's are
// src/worst_route/api/schemas.py). types.test.ts holds them to the reference's examples and to a
// stream recorded from the server. Coordinates are [lon, lat] in geometry and {lat, lon} in
// requests; every number is metric.

export type LonLat = [number, number];
export type Bbox = [number, number, number, number]; // west, south, east, north

export interface LineString {
  type: "LineString";
  coordinates: LonLat[];
}

export type Tier = "auto" | "T0" | "T1" | "T2" | "T3" | "T4";
export type Objective = "distance" | "time" | "collect" | "brand" | "alphabet";
export type CollectItem = "toll_booth" | "level_crossing" | "roundabout" | "bridge";
export type TargetKind = "max" | "ratio" | "duration";
/** How a run ended; `drawn`, a route drawn by hand (its run's backend `human`); `optimal`, a
 * certified route the solver proved. */
export type StopReason =
  "budget" | "converged" | "target" | "cancelled" | "timeout" | "drawn" | "optimal";
export type JobState = "queued" | "running" | "done" | "error";

/** `{code, message}` and the error's own fields (`stop_index`, `leg`, `field`, `constraint`,
 * `max_s`, `retry_after_s`, `running` and `queued` (a public server's TOO_MANY_JOBS), ...). */
export interface ErrorBody {
  code: string;
  message: string;
  [field: string]: unknown;
}

// --- Requests ---------------------------------------------------------------------------

export interface StopIn {
  lat: number;
  lon: number;
  label?: string | null;
}

/** `{kind: "max"}`, `{kind: "ratio", value}` (× the baseline, more than 1) or
 * `{kind: "duration", seconds}` (at most 7 days); reached within ±2 %. */
export interface Target {
  kind: TargetKind;
  value?: number | null;
  seconds?: number | null;
}

export interface Vehicle {
  mpg: number;
  gas_price_usd: number;
}

/** The turn constraints: left (or right) turns at a kilometre each, or with `strict` none; and
 * hard mode, each road in one stretch (in a route's request only when asked). */
export interface Constraints {
  no_left?: boolean;
  no_right?: boolean;
  strict?: boolean;
  hard?: boolean;
}

/** `POST /jobs`; a route document's `request` has every field but a `portfolio` of 1 and a
 * `certified` false. `reorder` visits the stops between the first and the last in the worst order
 * (a route document's request then has its stops in the order the route visits them); `portfolio`
 * runs that many seeds at once, from `seed`, and keeps the best; `certified` asks for the provably
 * longest route in a small area (the distance, no target, no worst order, no turn constraint or
 * hard mode, one seed, a start that is not the end). */
export interface RouteRequest {
  region: string;
  stops?: StopIn[] | null;
  objective?: Objective;
  constraints?: Constraints;
  reorder?: boolean;
  certified?: boolean;
  item?: CollectItem | null; // what `collect` collects; only with it
  brand?: number | null; // the id of the brand `brand` visits (GET /brands); only with it
  tier?: Tier;
  leash_km?: number | "auto";
  budget_s?: number;
  max_iterations?: number;
  seed?: number | null;
  target?: Target;
  vehicle?: Vehicle | null;
  departure?: string | null;
  base_route_id?: string | null;
  portfolio?: number;
}

// --- Responses --------------------------------------------------------------------------

export interface TierCount {
  tier: string;
  nodes: number;
  edges: number;
}

/** One entry of `GET /regions`, in the order served: `name` is what the app calls it ("Oregon",
 * "United States"; absent from servers older than it, the recordings' among them). */
export interface Region {
  region: string;
  name?: string;
  build_id: string;
  graph_version: string;
  bbox: Bbox;
  counts: Record<string, number>;
  tiers: TierCount[];
}

/** `POST /regions/pick`: the served region a trip with the stops sent runs on, its build, and why
 * ("all stops in Oregon", "stops in Oregon and Illinois"). */
export interface RegionPick {
  region: string;
  build_id: string;
  reason: string;
}

/** `POST /jobs`, 202. */
export interface JobCreated {
  job_id: string;
  events_url: string;
  seed: number;
}

/** How many of each collectible a route passes. */
export interface ItemCounts {
  toll_booth: number;
  level_crossing: number;
  roundabout: number;
  bridge: number;
}

/** A pilgrimage's brand, how many of its locations the route passes and how many the request's
 * area holds. */
export interface BrandVisits {
  id: number;
  name: string;
  visits: number;
  of: number;
}

/** An alphabet route's letters: how many, in order, the first road of each, and where its
 * alphabet part starts and ends (edge positions: its first A road's first edge, and the first
 * edge of its free last leg). */
export interface AlphabetLetters {
  letters: number;
  sequence: string;
  roads: string[];
  alpha_from: number;
  free_from: number;
}

/** A route's numbers while it grows, its delays so far among them (absent from servers older than
 * them); the objective's counters ride along (a collecting route's `item` and `items`, a
 * pilgrimage's `brand`, an alphabet route's `alphabet`). */
export interface Metrics {
  length_m: number;
  time_s: number;
  delay_s?: number;
  ratio: number | null;
  item?: CollectItem;
  items?: ItemCounts;
  brand?: BrandVisits;
  alphabet?: AlphabetLetters;
  [counter: string]: unknown;
}

/** A ratio or duration target and what was achieved (× for a ratio, seconds for a duration). */
export interface TargetMetrics {
  kind: "ratio" | "duration";
  value: number;
  achieved: number;
  within_tolerance: boolean;
}

/** A route's turns by class. */
export interface Turns {
  left: number;
  right: number;
  straight: number;
  u_turn: number;
}

/** `done`'s metrics: the stored route's, with its experienced time's ratio to the baseline's,
 * the run's target (null for max), its delays and its turns (each absent from servers older than
 * it). */
export interface DoneMetrics extends Metrics {
  time_ratio?: number | null;
  target?: TargetMetrics | null;
  delay_s?: number;
  turns?: Turns;
}

/** `GET /jobs/{id}` (with the snapshot) and `DELETE /jobs/{id}` (without). */
export interface JobStatus {
  job_id: string;
  status: JobState;
  region: string;
  build_id: string;
  seed: number;
  created: string;
  started: string | null;
  finished: string | null;
  last_event_id: number;
  iteration: number;
  elapsed_s: number;
  metrics: Metrics | null;
  route_id: string | null;
  stopped_because: StopReason | null;
  error: ErrorBody | null;
  snapshot: Snapshot | null;
}

export interface RouteStop {
  label: string | null;
  node: number;
  lat: number;
  lon: number;
  moved_m: number;
  note: string | null;
  route_index: number;
}

export interface Leg {
  from_stop: number;
  to_stop: number;
  node_range: [number, number];
}

export interface DocumentMetrics {
  length_m: number;
  baseline_length_m: number;
  ratio: number | null;
  time_s: number;
  baseline_time_s: number;
  baseline_delay_s?: number; // absent from servers older than it
  time_ratio: number | null; // experienced: driving and delays
  departure: string | null;
  arrival: string | null;
  target?: TargetMetrics | null; // null for max
  delay_s?: number; // absent from servers older than the turn counts
  turns?: Turns;
  items?: ItemCounts; // absent from servers older than the collectibles
  brand?: BrandVisits | null; // a pilgrimage's; null for other objectives, absent from older servers
  alphabet?: AlphabetLetters | null; // an alphabet route's; null for others, absent from older servers
  // The presentation's, absent from servers older than it; null where unknown: the fuel
  // without the request's vehicle, the states and towns without the places index (the share
  // of the roads is always known).
  extra_fuel_gal?: number | null;
  extra_cost_usd?: number | null;
  states?: string[] | null;
  towns?: string[] | null;
  towns_n?: number | null;
  network_fraction?: number;
}

export type StepKind =
  "depart" | "continue" | "turn" | "ramp" | "roundabout" | "stop" | "arrive" | "joke";

/** One turn-by-turn step; `edge_range` is half-open, [a, b) over the route's edges, empty for a
 * stop, the arrival and a joke. */
export interface DirectionStep {
  step: number;
  text: string;
  distance_m: number;
  time_s: number;
  edge_range: [number, number];
  kind: StepKind;
}

/** A portfolio route's searches: how many, their seeds, and the index of the one whose route it
 * is (`seeds[winner]` is the document's `seed`). */
export interface PortfolioRun {
  k: number;
  seeds: number[];
  winner: number;
}

/** A certified route's certificate: `optimal` when the solver proved no legal route in its area
 * is longer, else `feasible`, with the bound on the longest one and the gap, the fraction by which
 * it may exceed the route (null when the solver had none yet); the area's nodes and edges, the
 * leash used, the solve's seconds and threads. */
export interface CertifiedRun {
  status: "optimal" | "feasible";
  bound_m: number | null;
  gap: number | null;
  nodes: number;
  edges: number;
  leash_m: number;
  solve_s: number;
  workers: number;
}

export interface Run {
  attempts: number;
  accepted: number;
  stopped_because: StopReason;
  elapsed_s: number;
  backend: string;
  portfolio?: PortfolioRun; // a portfolio's only
  certified?: CertifiedRun; // a certified route's only
}

export interface Validation {
  ok: boolean;
  checked_at: string;
}

/** `GET /routes/{id}`. */
export interface RouteDocument {
  route_id: string;
  created: string;
  graph_version: string;
  engine_version: string;
  region: string;
  build_id: string;
  seed: number;
  request: RouteRequest;
  stops: RouteStop[];
  order?: number[]; // each stop's position among those asked for; absent from older servers
  geometry: LineString;
  legs: Leg[];
  metrics: DocumentMetrics;
  run: Run;
  validation: Validation;
  directions?: DirectionStep[]; // absent from servers older than them
  toasts?: string[];
}

/** `GET /routes/{id}/directions`. */
export interface DirectionsResult {
  route_id: string;
  directions: DirectionStep[];
}

/** One feature of `GET /routes/{id}/geojson`: the route, the baseline or a stop. */
export interface RouteFeature {
  type: "Feature";
  geometry: LineString | { type: "Point"; coordinates: LonLat } | null;
  properties: { kind: "route" | "baseline" | "stop"; [property: string]: unknown };
}

export interface RouteFeatureCollection {
  type: "FeatureCollection";
  features: RouteFeature[];
}

// --- Events (GET /jobs/{id}/events) --------------------------------------------------------

export interface SnappedStop {
  input: { lat: number; lon: number };
  label: string | null;
  node: number;
  lat: number;
  lon: number;
  moved_m: number;
  note: string | null;
}

/** `snapped`: the stops in the order the route visits them, `order` each one's position among
 * those the job was given (the identity without `reorder`; absent from older servers). */
export interface Snapped {
  stops: SnappedStop[];
  order?: number[];
  tier: string;
  leash_km: number;
  active_nodes: number;
}

export interface Baseline {
  length_m: number;
  time_s: number;
  geometry: LineString | null;
}

/** Replace route nodes i..j by `node_count` nodes drawn by `coords`; `node_index[k]` is the
 * index in `coords` of the detour's node k. */
export interface Splice {
  i: number;
  j: number;
  node_count: number;
  coords: LonLat[];
  node_index: number[];
}

/** How a portfolio's searches stand: how many, the index of the leader (whose seed is the job's
 * plus it) and each one's best ratio so far (null with none, or after a failure). */
export interface Portfolio {
  k: number;
  leader: number;
  best_ratios: (number | null)[];
}

/** How a certified route's solve stands: the bound on the longest legal route in the area so far,
 * and the fraction by which it may exceed the route (both null when the bound a better route came
 * with lay below it). */
export interface CertifiedProgress {
  bound_m: number | null;
  gap: number | null;
}

/** A portfolio's numbers are its leader's, and it says how its searches stand; a certified
 * route's say how its solve stands. */
export interface Progress {
  iteration: number;
  elapsed_s: number;
  score: number;
  metrics: Metrics;
  splices: Splice[];
  /** The best route's so far: an annealing run's current route may be below it. Optional for
   * the streams recorded before the API sent them. */
  best_score?: number;
  best_metrics?: Metrics;
  portfolio?: Portfolio;
  certified?: CertifiedProgress;
}

/** The whole route; `node_index[k]` is the index in `geometry` of route node k. */
export interface Snapshot {
  iteration: number;
  elapsed_s: number;
  node_count: number;
  stop_idx: number[];
  geometry: LineString;
  node_index: number[];
  metrics: Metrics;
}

export interface Done {
  route_id: string;
  stopped_because: StopReason;
  metrics: DoneMetrics;
}

// --- Brands (GET /brands) -------------------------------------------------------------------

/** One brand of `GET /brands?region=`: `locations` is the number of places a route can visit. */
export interface BrandEntry {
  id: number;
  name: string;
  wikidata: string | null;
  locations: number;
}

/** `GET /brands?region=`: the region's brands, the most located first, then by name. */
export interface BrandList {
  region: string;
  brands: BrandEntry[];
}

// --- Geocoding (GET /geocode) ---------------------------------------------------------------

/** A place a search may mean: its label, where it is, its main OSM tag (`amenity/cafe`). */
export interface GeocodeCandidate {
  label: string;
  lat: number;
  lon: number;
  category: string;
}

/** `GET /geocode?q=&region=`: the query as normalised, at most five candidates (best first),
 * and whether the server's cache answered. */
export interface GeocodeResult {
  query: string;
  region: string;
  candidates: GeocodeCandidate[];
  cached: boolean;
}

// --- The server (GET /config) ---------------------------------------------------------------

/** The basemap to draw: OpenStreetMap's raster tiles; Protomaps' vector tiles from the server
 * (`tiles`), with the labels' fonts (`glyphs`, a MapLibre template) and icons (`sprite`) when it
 * has them, the paths the API's (under /api); or OpenFreeMap's vector tiles, drawn in the style
 * at `style` (a URL). */
export interface BasemapConfig {
  kind: "raster" | "pmtiles" | "openfreemap";
  tiles: string | null;
  glyphs: string | null;
  sprite: string | null;
  style: string | null;
}

/** The limits a request meets on this server (`BUDGET_TOO_LARGE` above `budget_max_s`,
 * `PORTFOLIO_TOO_LARGE` above `portfolio_max`, which servers older than portfolios leave out). */
export interface ServerLimits {
  budget_max_s: number;
  leash_max_km: number;
  portfolio_max?: number;
}

/** `GET /config`: whether the server is public, its basemap and its limits. */
export interface ServerConfig {
  public: boolean;
  basemap: BasemapConfig;
  limits: ServerLimits;
}

// --- The daily route (GET /daily) ---------------------------------------------------------

/** One of the day's two towns, labelled with its state's code ("Bend, OR"). */
export interface DailyStop {
  lat: number;
  lon: number;
  label: string;
}

export type DailyStatus = "planning" | "done" | "failed";

/** `GET /daily`: the day's trip (the UTC date, YYYY-MM-DD; the region and build it runs on; its
 * towns, seed and attempts) and how its plan stands: while it is planned its job's `events_url`;
 * once done its `route_id`, `ratio`, and what ended the run after how many `attempts`; failed,
 * the next call plans it again. */
export interface Daily {
  date: string;
  region: string;
  build_id: string;
  stops: DailyStop[];
  seed: number;
  max_iterations: number;
  status: DailyStatus;
  route_id: string | null;
  events_url: string | null;
  ratio: number | null;
  stopped_because: StopReason | null;
  attempts: number | null;
}

// --- A route drawn by hand (POST /draw, POST /draw/finish) --------------------------------

/** The class of a turn from one edge into the next. */
export type TurnName = "left" | "right" | "straight" | "u_turn";

/** `POST /draw` and `POST /draw/finish`: the request drawn on, as `POST /jobs` takes it (a
 * drawing's objective `distance` and target `max` only), and the path, its edges in order. */
export interface DrawRequest {
  request: RouteRequest;
  path: number[];
}

/** A legal next move: its edge, the node it leads to, the road's name (else its ref), its length,
 * the turn into it (null for the first move), the stop it reaches (its index) and its shape. */
export interface DrawMove {
  edge: number;
  node: number;
  lat: number;
  lon: number;
  name: string | null;
  length_m: number;
  turn: TurnName | null;
  stop: number | null;
  coords: LonLat[];
}

/** `POST /draw`: the drawing so far (always legal: an illegal path is ILLEGAL_PATH, with its
 * `refusal` and `index`, the first illegal edge), its legal next moves, and its ratio once done
 * (null before, and for a round trip). */
export interface DrawAnswer {
  stops: SnappedStop[];
  at: { node: number; lat: number; lon: number };
  reached: number[];
  legal: true;
  length_m: number;
  baseline_length_m: number;
  ratio: number | null;
  geometry: LineString | null;
  next: DrawMove[];
  done: boolean;
}

/** `POST /draw/finish`: the finished drawing, stored as a route. */
export interface DrawFinished {
  route_id: string;
  ratio: number | null;
}
