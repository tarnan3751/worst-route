# API

The HTTP API, the only contract between the backend and the frontend: the endpoints, the
request and error shapes, the job lifecycle and its worker processes, the progress stream over
Server-Sent Events, the route store, and `worst-route serve`. Code: `src/worst_route/api/` and
`graph/loader.py`. The design it implements is `docs/PLAN.md` §4 and §1.1; the conventions are
`CLAUDE.md`. It plans with the engine (`docs/reference/engine.md`, **Planning**) and nothing
else: every route it returns has passed the validator.

| Module | What it holds |
|---|---|
| `app.py` | `create_app`, `ApiSettings`, `Api` (the app's state), `geocoder_user_agent`, the error handlers |
| `routes_api.py` | the endpoints, the checks `POST /jobs` makes before a job exists, `ApiError`, `api_of`, `region_of`, `bbox_of`, `stored_route`, `build_of`, `stored_delays`, `presentation_of`, `directions_of` |
| `geocode_api.py` | `GET /geocode` (**Geocoding**) |
| `share_api.py` | `GET` and `HEAD /routes/{id}/share.png`, `SHARE_IMAGE_VERSION` (**Share image**) |
| `directions_api.py` | `GET /routes/{id}/directions` |
| `schemas.py` | the request and response models (pydantic), the SSE payloads |
| `jobs.py` | `JobManager`, `JobSettings`, `Job`, `Subscription`, `prepare`, `TooManyJobs` |
| `worker.py` | `run_job` (what a worker process runs), `JobSpec`, `RouteArrays`, the worker's messages |
| `sse.py` | `Event`, `path_points`, the payload builders, `server_sent` |
| `store.py` | `RouteStore`, `StoredRoute`, `route_id` |

The geocoding proxy's backend is the `geocode/` package (**Geocoding**); the share image's, the
directions' and the document's presentation fields are the `presentation/` package
(`docs/reference/presentation.md`).

Coordinates are `[lon, lat]` in GeoJSON and `{lat, lon}` in requests; every number is metric
(metres, seconds). There is no `/api` prefix: the frontend's dev server proxies to the root.

## Running it

```
worst-route serve --region R [--build-id ID] [--host 127.0.0.1] [--port 8000] [--workers N]
                  [--backend auto|csr|python] [--store PATH]
                  [--geocoder nominatim|none] [--nominatim-url URL]
```

It loads the region's build (`--build-id`, else the region's `latest`) before it listens, so a
missing build, or one saved before an array of the format existed (`docs/reference/graph.md`,
**The build format**), is one line on stderr and exit 1. Then uvicorn serves one process. The jobs live
in that process's memory, and each runs in a worker process of its own, `--workers` at once
(default `API_MAX_JOBS`, cores − 2). The routes go to `--store`, by default
`<WORST_ROUTE_DATA>/routes.sqlite`; a lane that reads the main tree's builds through
`WORST_ROUTE_DATA` points `--store` into its own tree, and runs with `--geocoder none` or
`--nominatim-url` pointed at a stub of its own (no agent calls Nominatim). The share images are
cached in `share/` beside the route store, so a lane's stay in its own tree too. At startup it
loads the region's places index if `worst-route aux places` has written it under
`<WORST_ROUTE_DATA>/aux/places/` (**Share image**). `--port 0` binds a free port, which
uvicorn's log line names. The job manager's own lines (a job cancelled for want of a subscriber,
a worker's traceback) go to the same log. `--backend` is the engine's
(`docs/reference/graph.md`, **Choosing a backend**), and `serve` prints the one it took as
`plan` does (`backend: csr`): `auto`, the default, is csr when numba imports.

`--geocoder` is the place search's (**Geocoding**): `nominatim`, the default, asks
`--nominatim-url` (default `https://nominatim.openstreetmap.org`); `none` turns `GET /geocode`
off. The geocoder's cache is `geocode.sqlite` beside the route store (in the same directory as
`--store`), so a lane's stays in its own tree. A contact for the User-Agent comes from
`WORST_ROUTE_CONTACT` (never written in the code), and `serve` prints, after the backend, the
geocoder and the User-Agent it will send:

```
backend: csr
geocoder: nominatim https://nominatim.openstreetmap.org as "worst-route/0.1.0 (https://github.com/tarnan3751/worst-route)"
```

`create_app(ApiSettings(data_dir, region, build_id="latest", backend, store_path, jobs,
geocoder="nominatim", nominatim_url, contact))` builds the same application in-process, and
`create_app(settings, geocode=service)` with a geocoding service of the caller's own (a
`FakeGeocoder` behind it, in the tests); the tests drive it over `httpx` (**Tests**).

## Requests and errors

**`POST /jobs`** takes the engine's `RouteRequest` field for field, under the engine's names, plus
what the API and the presentation need:

```json
{"region": "portland",
 "stops": [{"lat": 45.5231, "lon": -122.6765, "label": "Powell's"},
           {"lat": 45.4817, "lon": -122.6263, "label": "Reed College"}],
 "objective": "distance",
 "constraints": {"no_left": false, "no_right": false, "strict": false, "hard": false},
 "tier": "auto", "leash_km": "auto",
 "budget_s": 20, "max_iterations": 1000000, "seed": 12345,
 "target": {"kind": "max"},
 "vehicle": {"mpg": 24, "gas_price_usd": 3.89},
 "departure": "2026-10-31T09:00:00-07:00",
 "base_route_id": null}
```

- `stops`: at least two, in order; the same point first and last makes a round trip. `label` is
  kept for the presentation.
- `objective`: `distance` (the longest), `time` (the slowest: driving time and the delays at
  signals and stop signs), `collect` (the most of one item, then the longest) or `brand` (the
  most locations of one brand, then the longest; `docs/reference/engine.md`, **Objectives**);
  `alphabet` is `NOT_YET`.
- `item`: what `collect` collects, `toll_booth`, `level_crossing`, `roundabout` or `bridge`;
  needed with `collect`, refused with any other objective (null or left out). `collect` takes
  no ratio target: it counts items, not metres.
- `brand`: what `brand` visits, a brand's `id` from `GET /brands`; needed with `brand`, refused
  with any other objective (null or left out). An id the build does not have is
  `INVALID_REQUEST` ("unknown brand 999; GET /brands?region=portland lists the brands"), and so is
  a brand none of whose locations the request's area holds ("no Starbucks within reach", from
  the pre-flight), both on `field: "brand"`. `brand` takes no ratio target: it counts visits,
  not metres.
- `constraints`: `{no_left, no_right, strict, hard}`, all false by default (and left out of the
  engine's request then; `hard` is in a stored request only when set). `no_left` makes each left
  turn (and U-turn) cost the route a kilometre's worth of its score (1,000 m, or 90 s under `time`:
  `TURN_PENALTY_M`, `TURN_PENALTY_S`), so they are rare; `no_right` the same for right turns; both
  at once is `INVALID_REQUEST` ("straight only is not a route"). With `strict`, none at all: a trip
  that cannot be driven so, but can without `strict`, is `INFEASIBLE_TURN_CONSTRAINT`, which on a
  street grid is most trips (one that cannot be driven either way is
  `STOP_UNREACHABLE_WITHOUT_REPEAT`, as without the constraint) (`docs/reference/engine.md`,
  **Constraints**); `strict` without `no_left` or `no_right` is `INVALID_REQUEST` ("strict needs
  no_left or no_right"). Both name `field: "constraints"`. `hard` is hard mode: each road (a ref,
  else the normalised name) in one stretch, so once the route leaves a road it never comes back to
  it (`docs/reference/engine.md`, **Constraints**), alone or with a turn constraint; a trip whose
  seed the engine finds no way to build under it, but can without it, is
  `STOP_UNREACHABLE_WITHOUT_REPEAT` with the message "the seed found no way from stop 0 to stop 1
  under hard mode, …; try without hard" (the seed is a heuristic: a legal route may exist).
- `tier`: `auto` or `T0`..`T4`. `leash_km`: `auto` or in (0, 150].
- `budget_s`: in (0, 120] (over 120 is `BUDGET_TOO_LARGE`), default 20.
- `max_iterations`: default 1,000,000, so the seconds budget ends a run. The CLI keeps the
  engine's 5,000.
- `seed`: in [0, 2⁶³ − 1]; left out, a random one below 2³¹, returned in the 202.
- `target`: `{kind: "max"}` (the default: as bad as the budget allows), `{kind: "ratio",
  value}` (the route's length as `value` × the baseline's, more than 1; under `time` its
  experienced time, driving and the delays, against the baseline's; not for a round trip) or
  `{kind: "duration", seconds}` (at most 7 days, of experienced time); reached within ±2 %
  (`docs/reference/engine.md`, **Targets**). Each kind takes only its own number. A target out
  of these bounds is `INVALID_REQUEST`, and so is a ratio target for a round trip (its stops',
  or for "make it worse" its base route's): `{code: "INVALID_REQUEST", message: "a round trip
  has no ratio target: its baseline is 0 m", field: "target"}`, before any other check.
- `vehicle` and `departure` are kept with the route; `departure` gives the document's `arrival`.
- `base_route_id`: "make it worse" (**Make it worse**).
- An unknown field is refused.

**Every error** answers `{code, message}` and the error's own fields, at the top level (not
FastAPI's `detail`):

| Status | Code | When |
|---|---|---|
| 422 | `INVALID_REQUEST` (+ `errors`, pydantic's without their `input`, and `field` when every error lies in one field of the body; or + `field`) | a body or field that does not parse (a seed beyond 2⁶³ − 1 included, `Infinity` or `NaN` for a number), an unknown objective, a target out of bounds, a ratio target for a round trip (`field: "target"`), `collect` without an item or an item for another objective, or an unknown item (`field: "item"`), a ratio target under `collect` or `brand` (`field: "target"`), `brand` without a brand, a brand for another objective, one the build does not have or one the request's area does not hold (`field: "brand"`), both turn constraints or `strict` without one (`field: "constraints"`); a place search's query empty or over 200 characters once normalised (`field: "q"`); `/brands` without a region |
| 422 | `UNKNOWN_REGION` | a region this server does not serve |
| 422 | `STOP_OUTSIDE_REGION` (+ `stop_index`) | outside the region's bbox: the build's `params.bbox`, else its nodes' extent |
| 422 | `NO_NODE_NEAR` (+ `stop_index`, null when no single stop is to blame) | no node within 5 km can play the stop's role |
| 422 | `STOP_UNREACHABLE_WITHOUT_REPEAT` (+ `leg`, `stop_index`) | no route covers the leg without repeating a road, after the re-snap fallback (`stop_index` is the leg's later stop), with or without a strict turn constraint: under one, when the seed's legs cannot be built without it either; under hard mode, also when they can be built without hard mode (alone, or with the strict constraint alone), the message then "the seed found no way from stop 0 to stop 1 under hard mode, …; try without hard" |
| 422 | `INFEASIBLE_TURN_CONSTRAINT` (+ `leg`, `stop_index`, `constraint`) | under a strict turn constraint (`constraint`: `no_left` or `no_right`), no route covers the leg without repeating a road or turning that way, after the re-snap fallback, while the seed's legs can be built without the constraint (one more build, on failure only; under hard mode too, when they can be built under hard mode alone); the message ends "under the strict no-left constraint; try without strict" |
| 422 | `BUDGET_TOO_LARGE` (+ `max_s`) | `budget_s` over 120 |
| 422 | `NOT_YET` (+ `field`) | an objective the engine does not have yet |
| 422 | `UNKNOWN_ROUTE` (+ `base_route_id`) | a base route that is not stored |
| 422 | `BUILD_MISMATCH` | a base route planned on another region, `graph_version` or engine version; `/validate` on a route another engine version made (its stops could be re-derived differently: re-plan it), as `worst-route validate` refuses one |
| 422 | `BASE_ROUTE_MISMATCH` (+ `field`) | stops, tier or leash given that differ from the base route's; or a base route `plan.check_base` refuses under its request's resolved stops and masks (`field` `stops`: its stops are not where they resolve now; `route`: a node outside the area, an edge the request bans, a turn its strict constraint refuses, or under hard mode a road it comes back to) |
| 429 | `TOO_MANY_JOBS` | every worker busy and the queue full |
| 429 | `RATE_LIMITED` (+ `retry_after_s`) | a place search that finds the geocoder's queue full: five already queued for a turn (**Geocoding**); the message is "the geocoder is busy, try again in a few seconds" |
| 502 | `GEOCODER_ERROR` | Nominatim failed: an error status, a timeout, no connection, an answer that does not parse; or refused (429, 403), which pauses the geocoder 60 s, during which a search the cache cannot answer gets this at once ("the geocoder refused; paused for N s") |
| 503 | `GEOCODER_DISABLED` | a place search on a server whose geocoder is `none` |
| 404 | `NOT_FOUND` | an unknown (or expired) job, an unknown route (for a share image, also an id that is not 12 lowercase hex digits), an unknown path |
| 405 | `METHOD_NOT_ALLOWED` | a method a path does not take |
| 409 | `BUILD_UNAVAILABLE` | a stored route whose build is no longer on disk at its `graph_version` |
| any other | `HTTP_<status>` | any other HTTP error Starlette raises, its status in the code |

The SSE `error` event uses the same body, with the codes above that a worker can meet, and:
`CANCELLED` (cancelled while queued or before its route existed), `VALIDATION_FAILED` (a route
the validator refused: an engine bug, never stored), `WORKER_FAILED` (an exception, a worker
that did not start, or one that died without a result, partway through a message included) and
`WORKER_TIMEOUT` (still running `API_KILL_GRACE_S` after its budget, with no route yet).

`POST /jobs` refuses before a job exists, in this order: the body (the target's bounds
included), a ratio target for a round trip, the region, the objective (and whether its item,
brand and target fit it, and the brand is the build's), the budget, the job limit (429), the
base route, each stop against the bbox, then the **pre-flight** (`jobs.prepare`): the request's
masks, a pilgrimage's brand within them, and `resolve_stops` (snapping and the re-snap
fallback), in a worker thread, and for a base route `plan.check_base`. The worker repeats
them, deterministically, so a request that passes the pre-flight is not refused for its stops
later.

## Endpoints

| Method | Path | Answer |
|---|---|---|
| GET | `/health` | `{status: "ok", builds: [{region, build_id, graph_version}]}` |
| GET | `/regions` | `[{region, build_id, graph_version, bbox: [w, s, e, n], counts, tiers: [{tier, nodes, edges}]}]` |
| POST | `/snap` | `{region, lat, lon, role: start\|via\|end, tier?}` → `{node, lat, lon, moved_m, note}` |
| GET | `/brands` | `{region, brands: [{id, name, wikidata, locations}]}`, for `?region=` (below) |
| POST | `/jobs` | 202 `{job_id, events_url, seed}` |
| GET | `/jobs/{id}/events` | the job's events (**Events**); `Last-Event-ID` resumes |
| GET | `/jobs/{id}` | the job's status with its route's current snapshot (polling) |
| DELETE | `/jobs/{id}` | cancels it (**Jobs**); answers its status, without the snapshot |
| GET | `/routes/{id}` | the route document, with its directions and toasts |
| GET | `/routes/{id}/directions` | `{route_id, directions: [{step, text, distance_m, time_s, edge_range, kind}]}`: the document's directions alone |
| GET | `/routes/{id}/geojson` | the FeatureCollection `engine.io.feature_collection` makes: the route, the baseline (null for a round trip) and the stops |
| POST | `/routes/{id}/validate` | `{route_id, graph_version, ok, checks: [{name, ok, index, detail}]}`, the stops re-derived from the stored request (by this engine version only) |
| GET | `/geocode` | `{query, region, candidates: [{label, lat, lon, category}], cached}`, for `?q=&region=` (**Geocoding**) |
| GET, HEAD | `/routes/{id}/share.png` | the route's share image, a 1200 × 630 PNG (`image/png`), cached; HEAD the same headers without it (**Share image**) |

**`/snap`** judges the stop in the tier's nodes and every node within `BUBBLE_KM` (3 km) of it,
as a job's masks do near a stop (`auto` is T4); its `note` says why a stop moved ("moved 214 m
to the nearest through street" for a via at Reed College's cul-de-sac). It is a preview: a job
also keeps its stops apart and may re-snap them, and its `snapped` event is the authority.

**`/brands`** lists the brands of the region's build (its brand index,
`docs/reference/pipeline.md`, **enrich**), for the pilgrimage's choice: each brand's `id` (what
`POST /jobs` takes as `brand`), its display name, its wikidata id (null when its POIs carry
none) and its `locations`, the distinct nodes it is snapped to, which a route can visit; the most
located first, then by name. It is answered from the build in memory, the brands' names in
`strings.json` and their nodes in memory-mapped arrays. An unknown region is `UNKNOWN_REGION`.

**The job status** (`GET /jobs/{id}`): `{job_id, status, region, build_id, seed, created,
started, finished, last_event_id, iteration, elapsed_s, metrics, route_id, stopped_because,
error, snapshot}`. `status` is `queued`, `running`, `done` or `error`; `iteration` is the attempt
of the last splice applied; `elapsed_s` the wall time since it started.

**The route document** (`GET /routes/{id}`):

```
{route_id, created, graph_version, engine_version, region, build_id, seed,
 request:    the API request as planned (seed filled in; for "make it worse", base_route_id
             and the base route's stops, tier and leash),
 stops:      [{label, node, lat, lon, moved_m, note, route_index}],
 geometry:   LineString along every edge,
 legs:       [{from_stop, to_stop, node_range: [a, b]}],
 metrics:    {length_m, baseline_length_m, ratio, time_s, baseline_time_s, baseline_delay_s,
              time_ratio, departure, arrival, target, delay_s,
              turns: {left, right, straight, u_turn},
              items: {toll_booth, level_crossing, roundabout, bridge},
              brand: {id, name, visits, of},
              extra_fuel_gal, extra_cost_usd, states, towns, towns_n, network_fraction},
 run:        {attempts, accepted, stopped_because, elapsed_s, backend},
 validation: {ok: true, checked_at},
 directions: [{step, text, distance_m, time_s, edge_range: [a, b], kind}],
 toasts:     [text]}
```

Time, wherever a ratio or an arrival is shown, is the experienced time: the driving (`time_s`)
and the waiting at signals and stop signs (`delay_s`), which are reported apart. `time_ratio` is
the route's experienced time over the baseline's (`baseline_time_s` plus `baseline_delay_s`, the
baseline's delays, worked out from the build along its stored edges, so a route stored before
documents carried them has them too); `ratio` and `time_ratio` are null for a round trip (its
baseline is 0 m); `arrival` is `departure` plus the experienced time, null without one; `target` is `{kind, value, achieved,
within_tolerance}` for a ratio or duration target (the target; what the route achieved, its
ratio, of lengths or under `time` of experienced times, or its experienced time in seconds,
driving and the delays; and whether that is within ±2 %), null for `max`; `delay_s` is the waiting at the route's signals and
stop signs, and `turns` counts its turns by class (`docs/reference/engine.md`, **Turns**), both
worked out from the build, so a route stored before they existed has them too;
`items` counts the route's collectibles, whatever its objective (`docs/reference/engine.md`,
**Objectives**: `count_items`), recounted on the stored route when the document is asked for,
so a row of the current `graph_version` that lacks `items` in its stored metrics has them too;
a route stored before the collectibles was planned on a build without `node_roundabouts`, at an
older `graph_version`, so it answers `BUILD_UNAVAILABLE` (its build lacks the array, or is
rebuilt at another version); `brand` is a pilgrimage's brand (`id`, `name`), how many of its
locations the route passes (`visits`) and how many the request's area holds (`of`), recounted
on the stored route from the build's brand index and the request's masks when the document is
asked for (`objectives.brand.brand_counts`), null for every other objective;
`stopped_because` is `target` (within it),
`budget`, `converged`, `cancelled` or `timeout`.

The presentation's fields (`docs/reference/presentation.md`, **Directions**, **Jokes**, **Stats and
toasts**) are worked out again from the build on every request (`presentation.document.present`),
none of them stored, so a route stored before they existed has them too:

- `directions`, the route's turn-by-turn steps: `step` numbers them from 1; `kind` is `depart`,
  `continue`, `turn`, `ramp`, `roundabout`, `stop` (a stop between the ends), `arrive` or `joke`;
  `edge_range` is half-open, `[a, b)` over the route's edges, edge k running from route node k
  to node k + 1 (so a step covers nodes a to b, as `legs`' `node_range` counts them). The
  driving steps' ranges tile the route's edges in order; a `stop`, the `arrive` and a `joke`
  drive nothing, an empty range where they come. A step's `distance_m` and `time_s` are its
  edges', its time with the delays of the nodes it arrives at, so the steps add up to `length_m`
  and to `time_s` plus `delay_s`. The stops' labels name them ("Arrive at work"). The jokes are
  drawn from the route's seed: the same every time.
- `toasts`, at most twelve one-line facts about the route, the most striking first.
- `extra_fuel_gal` and `extra_cost_usd`, the length beyond the baseline's at the request's
  `vehicle`'s `mpg` and `gas_price_usd`, null without a vehicle; `states` and `towns`, the
  Census names of the states and towns the route passes through in the order it enters them,
  and `towns_n`, null when the region has no places index (**Share image**); and
  `network_fraction`, the share of the road units within the request's reach (its leash, tier
  and bubbles) that the route drives.

The document has no comparison line: the share image has it.

## Events

`GET /jobs/{id}/events` is a `text/event-stream` (sse-starlette, a comment ping every 15 s).
Each event has an `id`, counting up per job, an `event` name and a JSON `data`:

| Event | When | `data` |
|---|---|---|
| `snapped` | at the job's creation (from the pre-flight) | `{stops: [{input: {lat, lon}, label, node, lat, lon, moved_m, note}], tier, leash_km, active_nodes}` |
| `baseline` | when the worker has built the route the loop starts from | `{length_m, time_s, geometry}` (null for a round trip) |
| `snapshot` | right after `baseline` (iteration 0: the seed, or the base route), after every 50th `progress`, just before `done` when the stored route is not the one the splices drew (**Jobs**), and first on every (re)connect once the route exists | `{iteration, elapsed_s, node_count, stop_idx, geometry, node_index, metrics}` |
| `progress` | at most every 0.1 s while splices are accepted | `{iteration, elapsed_s, score, metrics: {length_m, time_s, ratio, item, items, brand: {id, name, visits, of}}, splices: [{i, j, node_count, coords, node_index}]}`: `item` and `items` only for `collect`, `brand` (its visits as the route grows) only for `brand` |
| `done` | the route is stored (or was already: its existing id, **The store**) | `{route_id, stopped_because, metrics}`: the stored route's `{length_m, time_s, ratio, time_ratio, target, delay_s, turns: {left, right, straight, u_turn}, items: {toll_booth, level_crossing, roundabout, bridge}, item, brand: {id, name, visits, of}}`, as in the document (`target` null for `max`; `item` only for `collect`, `brand` only for `brand`) |
| `error` | the job ended without a route | `{code, message, ...}` |

The stream ends after `done` or `error`. A browser's `EventSource` reconnects when a stream
ends, so the client closes it on either. A client subscribes when its stream starts running,
not when the request arrives, and unsubscribes when it ends, so a client gone before its stream
starts leaves no subscriber behind (which would keep the job from ever being an orphan).

**Applying the stream.** A route is drawn as `[lon, lat]` points along its edges; consecutive
edges share their junction point, kept once (true of every build), and `node_index[k]` is the
index of route node k among the points. A `snapshot` replaces the client's points and indices.
Each splice in a `progress` event, in order, replaces route nodes `i..j` (positions before the
splice) by `node_count` nodes:

```
a, b = index[i], index[j]
points = points[:a] + splice.coords + points[b + 1:]
index  = index[:i] + [a + k for k in splice.node_index]
       + [k + len(splice.coords) - (b - a + 1) for k in index[j + 1:]]
```

The snapshot that follows `baseline` is needed because the seed is not the baseline once there
are stops between the ends (the seed's legs avoid each other). A snapshot simplifies each edge
on its own by Douglas-Peucker (shapely) with a tolerance of `API_SIMPLIFY_M` (1 m) as degrees of
latitude, applied to lon/lat as they are: both ends of every edge stay, so every node is a
vertex, and the kept points are the original values to the last digit, so the points and
indices of the snapshot and of later splices agree exactly. Across a north-south line the
tolerance is 0.7 m at Portland's latitude. On Powell's → Reed (seed 1, converged: 4,913 nodes)
the snapshot `GET /jobs/{id}` returns has 7,262 points where the route document's geometry has
17,138; splices are not simplified.

**Ids and reconnecting.** Every event the job broadcasts takes the next id, and every splice
is sent before the job's `done` or `error`. A subscriber gets, first, the `snapped` and
`baseline` events it has not seen (all of them on a first connect; those after `Last-Event-ID`
on a reconnect), then, once the route exists, a `snapshot` of it carrying the id of the latest
event that changed the route (splices not yet sent are sent first, so no later `progress`
repeats one), then `done` or `error` if the job has ended, then live events; the ids it gets
rise strictly. So a reconnect after id 27 gets a snapshot with id 38 when events 28–37 were
missed, then 39, 40, ...; and a client that comes after a job's end gets its `snapped`,
`baseline`, a `snapshot` with the id of its last `progress` (or snapshot), then the end.

## Jobs

A job plans one request. `JobManager.submit` makes it after the pre-flight, emits `snapped`, and
starts its worker, or queues it (FIFO) when `API_MAX_JOBS` are running; with `API_MAX_QUEUED`
(8) waiting too, `TOO_MANY_JOBS`.

**The worker** (`worker.run_in_process`, around `worker.run_job`) runs in a `multiprocessing`
process started with `spawn`. It loads the build memory-mapped (`graph/loader.py`, so workers
share the OS page cache), calls `plan_route` with its `on_start` and `on_progress` hooks (and
`base` to continue a route), and sends small picklable messages down a one-way pipe
(`Pipe(duplex=False)`; the API process closes its copy of the write end once the worker has
started, so the worker holds the only one): `Started` (the route the loop starts from and the
baseline), `Spliced` per accepted splice, then `Finished` (the validated route as its
`route.json` document) or `Failed`. A send blocks while the pipe is full, so a slow reader slows
the search but never loses a message. On start and at every accepted splice the worker checks
that the API process is alive (`multiprocessing.parent_process()`); when it is not, or the pipe
has no reader left, it closes its end and exits (`os._exit`), so a crashed API never leaves
workers searching. Spawn re-imports the parent's main script, so under `worst-route serve` a
worker loads `worst_route.cli` (typer, pydantic-settings, `pipeline.layout`) besides the engine
and the graph package (shapely, scipy); never FastAPI, uvicorn or sse-starlette, and no numba on
the python backend. Its own imports take about 0.4 s; on the csr backend, the default, loading
numba and the cached kernels adds about 0.3 s.

**The follower**, an asyncio task per running job, reads that pipe in a thread (`poll` for at
most `API_READ_GRACE_S`, 0.1 s, then `recv`) and keeps the job's own copy of the route: the
starting route, then each splice applied in order, as a client would. It keeps the best route
so far apart, by the request's goal (`engine.target.Goal`, on each splice's score and metrics):
in a max mode every accepted splice raises the score, so the best is the copy; a target run's
best is the route closest to its target, which after an overshoot that shrinking could not
bring back is an earlier one (`docs/reference/engine.md`, **Targets**). It coalesces splices into
`progress` events (`API_COALESCE_S`, 0.1 s) with a `snapshot` every `API_SNAPSHOT_EVERY` (50).
On `Finished` the route (the engine's best) is stored and `done` sent; on `Failed`, `error`.
Whenever the stored route is not the one the splices drew (by its edges: two routes through the
same nodes may differ by a parallel edge), a `snapshot` of it comes just before `done`, and the
job's route becomes it, so every client, a late one too, and `GET /jobs/{id}` end on the stored
route.

**Ending early.**

- **Cancel** (`DELETE`): a queued job, or one whose route does not exist yet, ends in `error`
  `CANCELLED`. A running one has its worker terminated and its pipe never read again (a
  process killed while writing may leave half a message). The manager's best route so far is
  validated against the pre-flight's stops and masks (`validate_route`), stored with
  `stopped_because: "cancelled"` and reported in `done`, or ends in `VALIDATION_FAILED`. Its
  `run.attempts` is the attempt of the last splice applied, so planning the same request with that many attempts
  gives it back byte for byte (the prefix property, `docs/reference/engine.md`, **The loop**).
- **Time limit**: a job still running `API_KILL_GRACE_S` (30 s) after its budget is ended the
  same way, as `"timeout"` (`WORKER_TIMEOUT` before its route exists).
- **Orphans**: once a subscriber has connected, a job with none for `API_ORPHAN_S` (15 s) is
  cancelled, or dropped if still queued (`error` `CANCELLED`); the manager looks every
  `API_SWEEP_S` (1 s). A job nobody ever subscribed to runs to its end, so a script can
  `POST /jobs` and read the route later. The frontend also sends `DELETE` on `pagehide`
  (`fetch` with `keepalive`; `docs/reference/frontend.md`).
- **A dead worker**: one that exits without a result is `WORKER_FAILED`. Its death closes the
  pipe's only write end, so a read ends at once with end-of-file, even partway through a message
  (a 2 MB message cut short after 60 kB fails its job in well under a second, and the manager
  still shuts down cleanly). Every read waits at most `API_READ_GRACE_S`, so no thread is ever
  left blocked.
- **A worker that does not start** (spawning failed) ends its job as `WORKER_FAILED`, and the
  next queued job starts.
- **The sweep never stops on one job**: an error while it cancels an orphan (a store that fails
  to write, say) ends that job as `WORKER_FAILED`, freeing its slot, and the sweep goes on.

An ended job's record (status, its first events and its end) is kept `API_JOB_TTL_S` (1 h), then
forgotten (`NOT_FOUND`); its route stays in the store.

**Inline workers** (`JobSettings(inline_workers=True)`, tests only): the same `run_job` runs in
a thread on a `queue.Queue`, and stopping it sets an Event it checks at its next accepted splice
(it does not wait for it). Everything else is the same code, so events, cancellation and limits
are testable without spawning.

## Make it worse

`POST /jobs` with `base_route_id` continues a stored route: the worker passes it to
`plan_route(base=...)`, which starts the loop from it instead of the seed, with the new
request's seed, budget and target: with a target below the base route's value the run shrinks
toward it, above it grows, and within it ends at once with the base route (`"target"`). The
stops, tier and leash come from the base route's request; given ones must match (`BASE_ROUTE_MISMATCH`), the stops by their lat/lon. The base route must have
been planned on this region's build at its `graph_version` and by this engine version
(`BUILD_MISMATCH`), and `plan.check_base` must accept it under the request's resolved stops and
masks: its stop nodes where the stops resolve now, every node in the active area, no edge the
request bans, under a strict turn constraint no turn it refuses (a soft one takes any base),
under hard mode no road it comes back to (`BASE_ROUTE_MISMATCH`, in the pre-flight and again in
the worker). The objective and the
constraints are the new request's. The new
route's document records `base_route_id` in its `request`; it is reproduced by the base route,
the seed and the request. A continuation that changes nothing (no splice accepted, as with
`max_iterations: 0` or a base that has converged) produces the base route itself: its `done`
reports the base route's id, and that id's document stays the first record, the base's (its
labels, departure, seed and request, with no `base_route_id`; **The store**). "Reroll" is a new
job with a new seed.

## The store

`RouteStore(path)`, sqlite in WAL mode, a connection per call (any thread may use it). A row holds
what `route.json` holds plus the API request and the baseline's edges:

```
routes(id, created, region, build_id, graph_version, engine_version, seed,
       request_json (the engine's RouteRequest), input_json (the API request as planned),
       stops_json, nodes, edges (int32 little-endian blobs), stop_idx (JSON),
       baseline_edges (blob), metrics_json, run_json)
```

`route_id` is the first 12 hex digits of a sha256 over the `graph_version`, the node and edge ids
(int32 little-endian) and the stop positions (JSON): the id names the route's content, not the
request that found it. One route on one build therefore has one id whichever run found it, and
the row stored first is kept. A request that produces a route already stored (another seed that
lands on it, a rerun, a "make it worse" that accepts no splice) gets the existing id in its
`done`, and that id's document is the first record's: its request, labels, departure, seed and
run. `get` returns a `StoredRoute`, which the validator reads like a `Route`.

## Geocoding

The place search's backend (`docs/PLAN.md` §4.4): a place name to at most `GEOCODE_LIMIT` (5)
points in the region, asked of Nominatim behind a cache and a rate limit, so that the browser
never talks to Nominatim and the application keeps to its usage policy (at most one request a
second for the whole application, no autocomplete, an identifying User-Agent, results cached).
Code: `src/worst_route/geocode/` and the endpoint, `api/geocode_api.py`.

**`GET /geocode?q=<text>&region=<region>`** answers the places the text may mean in the region,
best first:

```
{"query": "powell's books", "region": "portland", "cached": false,
 "candidates": [{"label": "Powell's City of Books, 1005, West Burnside Street, …",
                 "lat": 45.5231165, "lon": -122.6812548, "category": "shop/books"}, …]}
```

`query` is the text as normalised; `cached` says whether the cache answered (a second search for
`  POWELL'S   Books` does). In order, it refuses: a missing `region` (FastAPI's own 422
`INVALID_REQUEST`, from the request's validation, before the endpoint runs); a query empty or
over `GEOCODE_MAX_QUERY_CHARS` (200) characters once normalised (422 `INVALID_REQUEST`, `field:
"q"`; a missing `q` is empty); a region this server does not serve (422 `UNKNOWN_REGION`); a
server whose geocoder is `none` (503 `GEOCODER_DISABLED`). Then the cache answers if it can;
else a paused geocoder (502 `GEOCODER_ERROR`, "the geocoder refused; paused for N s"), a full
queue (429 `RATE_LIMITED`, `retry_after_s`) and a failure upstream (502 `GEOCODER_ERROR`; a
refusal starts the pause) refuse. The region's bbox is the one `/regions` gives (the build's
`params.bbox`, else its nodes' extent). The service lives on the app's state
(`Api.geocode`, none for `none`), as the job manager does.

| Module | What it holds |
|---|---|
| `base.py` | `Candidate` (`label`, `lat`, `lon`, `category`), the `Geocoder` protocol, `GeocodeError`, `GeocodeRefusedError`, `normalise` |
| `nominatim.py` | `NominatimGeocoder`, `user_agent` |
| `fake.py` | `FakeGeocoder`: fixed answers, no network, the queries it was asked (tests) |
| `cache.py` | `GeocodeCache`, `key` |
| `limiter.py` | `RateLimiter`, `RateLimited` |
| `service.py` | `GeocodeService`, `Answer` |

**A search** (`GeocodeService.search(region, bbox, query)`) normalises the query (casefolded,
each run of whitespace one space, stripped) and answers from the cache when it holds it
(nothing sent, no turn taken, paused or not). Else it is refused at once while the geocoder is
paused (**The pause**), and otherwise queues for a turn of the rate limit. In its turn it reads
the cache again, so a query queued behind the same one in flight is answered from the cache and
leaves at once, taking no interval (three identical searches at once ask Nominatim once), and
checks the pause again; then it asks the geocoder, keeps at most `GEOCODE_LIMIT` (5) of what
comes back, and caches the answer, an empty one too, before its turn ends. A failure is never
cached.

**Nominatim** (`NominatimGeocoder`): one `GET <nominatim_url>/search` per query with
`format=jsonv2`, `q` (the normalised query), `limit`, `countrycodes=us`,
`viewbox=<lon_min>,<lat_min>,<lon_max>,<lat_max>` (the region's bbox), `bounded=1` and
`addressdetails=0`, under `User-Agent: worst-route/<version> (<repo url>)` (`Settings.user_agent`),
or `(<repo url>; <contact>)` with a contact (`WORST_ROUTE_CONTACT`), timing out after
`GEOCODE_TIMEOUT_S` (10 s). jsonv2 answers a list of places: a candidate's `label` is its
`display_name`, its `lat` and `lon` the place's (strings in jsonv2), its `category` jsonv2's
`category` and `type` joined (`amenity/cafe`, `shop/books`). A place outside the bbox is dropped,
though `bounded=1` should keep it out already. Nominatim's 429 or 403 (too many requests, or
blocked) is a `GeocodeRefusedError`, which pauses the geocoder; any other error status, a
timeout, a transport error or a body that is not a list of such places is a `GeocodeError`.
Nothing is retried. The field names are Nominatim's documented ones, held to the example of its
search documentation (`tests/fixtures/geocode/`).

**The cache** (`GeocodeCache`): a sqlite file, `geocode.sqlite`, beside the route store, in WAL
mode with a connection per call, one table:

```
queries(key TEXT PRIMARY KEY, created REAL, body TEXT)
```

The key is the sha256 of the region, its bbox's four numbers rounded to 4 decimals and the
normalised query, one per line: `Powell's   BOOKS` and `powell's books` share a row, and a
region rebuilt with another bbox asks again. `body` is the candidates as JSON and `created` the
time it was stored (seconds since the epoch). An answer is kept `GEOCODE_CACHE_TTL_S` (30 days);
an expired one is replaced on the next search for it.

**The rate limit** (`RateLimiter`): requests go upstream one at a time, each in a caller's
turn, which callers take in the order they came behind one `asyncio.Lock`, with at least one
second (1 / `GEOCODE_RPS`) between the previous answer (or failure) coming back and the next
request going: a caller in its turn sleeps until then, and once its request has answered the
next one's time is set from that moment. So two requests reach Nominatim at least the interval
plus the latency apart, whatever a new connection costs. A caller that finds `waiting` callers
already queued (the turn's holder not counted) is refused at once (`RateLimited`), taking no
place, when `waiting + 1` intervals exceed `GEOCODE_MAX_WAIT_S` (5 s), and told
`ceil((waiting + 1) × interval − 5)` seconds (at least 1). The cap counts intervals, not
latencies: the last caller let in waits about five seconds plus the answers ahead of it. Of a
burst of ten distinct queries six go upstream (one at once, five queued) and four are refused
at once. Only a cancelled task leaves the queue; one whose turn ends without a request (the
cache answered) takes no interval. An HTTP client that disconnects while its search is queued
does not cancel it (Starlette does not cancel a plain endpoint when its client goes away), so
the search keeps its place, goes upstream in its turn and caches its answer, which a retry then
finds.

**The pause**: when Nominatim refuses (429, too many requests; 403, blocked: the usage policy's
signals), the service asks it nothing more for `GEOCODE_PAUSE_S` (60 s) from when the refusal
came back. Meanwhile a search the cache cannot answer is refused at once, without queueing, as a
`GeocodeError` "the geocoder refused; paused for N s" (N the whole seconds left; the refused
search itself is answered the same, with 60). Like the rate limit, the pause is per process.

## Share image

**`GET /routes/{id}/share.png`** answers the route's share image (`docs/reference/presentation.md`,
**Share image**): a 1200 × 630 PNG drawn from our own data (the ratio, the comparison line, the
route and its baseline over the build's roads and the state outlines, no basemap), as `image/png`
with `Cache-Control: public, max-age=86400`. Its big number is the route's ratio of lengths, "less
efficient than the sensible route", except for a route planned for the slowest time (its request's
`objective` is `"time"`), which shows its document's `time_ratio`, "slower than the sensible route";
a round trip shows its length ("to end up where it started"). **`HEAD`** answers the same headers,
its `Content-Length` the image's, without the body (link unfurlers ask that way first); it renders
the image as a GET would, if it is not cached yet.

- An id that is not a route id (12 lowercase hex digits) is 404 `NOT_FOUND` before any file is
  looked at.
- An image already rendered is read from `share/<id>-v<SHARE_IMAGE_VERSION>.png` in the share
  directory, `share/` beside the route store (`Api.share_dir`), and served as it is, whatever has
  happened to its build since.
- Otherwise the route is read from the store (404 `NOT_FOUND`) and the endpoint takes the route's
  lock (`Api.share_locks`, an `asyncio.Lock` per route), so concurrent first requests render once:
  the first renders in a worker thread (`asyncio.to_thread`), on the route's build at its
  `graph_version` (409 `BUILD_UNAVAILABLE`, as `GET /routes/{id}` answers), with `comparison_line`
  of its length, the places index's outlines, its request's objective and the document's
  `time_ratio` (computed as `GET /routes/{id}` does: `stored_delays` and `sse.time_ratio`); it
  writes the file whole (a temporary name of its own, then a rename, so two writers, two servers on
  one store, never share a part file); the others then find the file. The lock is forgotten once the
  request that took it is done, whatever happened (an image written, a 409, a render that failed,
  which is a 500 and leaves no file): the next request starts afresh.
- The places index (`Api.places`) is loaded once, at startup, for the served region:
  `worst-route aux places --region R` writes it (`docs/reference/presentation.md`, **Places**).
  Without one the images have no state outlines; nothing fails.

The drawing (`presentation/share_image.py`) is imported, and with it matplotlib, by the first
render, never at startup: matplotlib's first import may build its font cache, which took 11 s on
a cold machine, and the API starts without it. So the first image after a start takes about two
seconds (**Measured**).

`SHARE_IMAGE_VERSION` (in `share_api.py`) goes up whenever the drawing changes, so images drawn
by an older version are left behind rather than served. A route's id names its content (**The
store**), so its image never needs drawing again for the same version.

## Measured

On Portland (build e4b3b4ad90db, engine version 2), `serve` with the default backend (`auto`,
which then took python: the CSR kernels did not search transition tables yet) on the dev
machine, with curl:

- **Powell's → Reed, seed 1, 30 s budget.**
  - `snapped` 0.08 s after the request; `baseline` and the seed's snapshot 0.34 s;
  - a `progress` every 0.10 s;
  - snapshots at events 54 and 105;
  - `done` at 11.8 s: converged at 62.43× after 82,841 attempts, the route
    `worst-route plan --seed 1` gives, byte for byte.
  - Replaying the 117 events as a client gives every node of the stored route (4,913) to the
    last digit.
- **Cancel at 4 s (seed 2).** `DELETE` answered in 23 ms with `done`, `cancelled`, 35.6×. The
  validator passes it, and `worst-route plan --seed 2 --max-iterations 4928` gives the same
  route.
- **Reconnect mid-run (seed 3).** After id 27, a reconnect with `Last-Event-ID: 27` got a
  snapshot with id 38, then live events from 39. Replaying from that snapshot alone gives the
  stored route exactly (57.9×, converged at 14.2 s).
- **"Make it worse" from the 62.43× route (seed 5).** It went to 62.70× with 11 more splices in
  1.0 s, the base having converged. Its `baseline` came 0.54 s after `snapped`, against 0.26 s
  for a fresh job: the worker's spawn and imports (its own start takes 0.04 s in-process).
- **Orphans.** On engine version 1 (before the engine fixes), a client that disconnected after
  2 s left a job that was cancelled about 15 s later, its route stored. On engine version 2 the
  Portland trips tried converge in 7–16 s, before that point, and end as `converged`. The rule is
  tested on the manager and over HTTP (**Tests**).
- **Refusals.** A 300 s budget is `BUDGET_TOO_LARGE` and a Seattle stop `STOP_OUTSIDE_REGION`,
  with its index. PDX → Beaverton, which engine version 1 refused for a turn the transition
  tables forbid, now plans (seed 2: 10.5×, converged in 6.7 s).
- **With the pipe** (after the review): Powell's → Reed, seed 1, gives the same route
  (`5ea844bffd40`), the client replay again exact; a cancel answers in 21 ms; a continuation of
  it with `max_iterations: 0` reports `5ea844bffd40` in its `done`.
- **Targets** (`serve --port 0`, the default backend, csr, 2026-10-30; Powell's → Reed): a 20×
  ratio, seed 1, `done` after 1.0 s with `stopped_because: "target"` at 19.60× (the route
  `worst-route plan --target ratio:20 --seed 1` gives); a 9 h duration (`seconds: 32400`), seed
  2, `done` after 1.8 s at 8 h 49 min of driving (37.9×; a duration counted only the driving
  then, the delays too since M3); a max run, seed 1, still `5ea844bffd40`; "make it
  worse" from it with a 30× ratio, seed 1, `done` after 5.7 s at 30.58×, every `progress` lower
  than the one before, its `/validate` ok. Each target job's `done` and document carry its
  `target`.
- **Brands** (`serve --port 0`, csr, 2026-10-30, build ffe2e7c881ca at pipeline version 3, load
  average about 20): `/brands?region=portland` answers 287 brands, the United States Postal
  Service first (174 locations), then Starbucks (126); an unknown brand id is `INVALID_REQUEST`
  on `field: "brand"` at once. Powell's → Reed for Starbucks (`brand: 229`), seed 1, a 30 s
  budget: 255 `progress` events, `metrics.brand.visits` rising 7 → 16 by attempt 2,441 → 17 by
  5,185, never falling; `done` after 30 s at 17 of the 19 the area holds, 59.03× (31,549
  attempts); the document's `brand` the same, its `/validate` ok.
- **Share images** (`serve --port 0`, csr, 2026-10-30, with the Portland places index, a warm
  font cache): Powell's → Reed, seed 1 (`5ea844bffd40`, 62.43×): 489 kB in 2.0 s for the first
  image after the start (matplotlib's import included), 8 ms from the cache (the same bytes), one
  file `share/5ea844bffd40-v1.png`, and `HEAD` the same headers with no body; Powell's → Hayden
  Island, seed 3 (52.40×), next, 1.4 s; an unknown id and `nope` 404 `NOT_FOUND`. A job's worker
  process had loaded neither matplotlib nor geopandas (nor FastAPI nor pandas).
- **Directions and toasts** (`serve --port 0`, csr, 2026-10-30, build 506f747d7458 with the
  Portland places index; other lanes' tests running, load average 13–19): Powell's → Reed with
  labels and a vehicle (24 mpg, $3.89), seed 1, 5,000 attempts (`e7ee723315fa`, 34.21×, 2,819
  edges): `GET /routes/{id}` answers in 28–70 ms once warm (0.11 s for the first), with 1,532
  steps from "Head east on West Burnside Street" to "Arrive at Reed College", ten toasts
  ("34.2× less efficient than the sensible route.", "Never leaves Portland. Sees all of it
  instead.", "An extra 6.6 gallons, or $25.80, for nothing.", ..., "Passes 12 level crossings,
  28 roundabouts and 10 bridges on the way."), `states` `["Oregon"]`, `towns` `["Portland"]`
  and `network_fraction` 0.23; `/directions` the same steps in 13–45 ms; an unknown id 404.
  Before the places index's polygons were prepared (`docs/reference/presentation.md`,
  **Places**) the document took 0.26–0.38 s on a quiet machine (build e4b3b4ad90db) and
  1.8–2.6 s at load average 30, nine tenths of it the places lookups.

## Tests

`tests/api/` (`support.py` writes lattice builds into a temporary data directory and holds the
helpers; the async tests run on anyio's pytest plugin, asyncio only):

| Test file | What it covers |
|---|---|
| `test_schemas.py` | the request mirrors the engine's (a pilgrimage's brand included), its defaults, the seed's bounds, stops left out only with a base, malformed requests refused (both turn constraints at once, an unknown one); the target as the engine's, each kind with its own number and within its bounds; `done`'s time ratio, target, delays and turns typed |
| `test_store.py` | the round trip (the stored route validates), unknown ids, the first record kept, what the id covers |
| `test_sse.py` | path points and node indices, the per-edge simplification keeping nodes and original values, splices, metrics, the event encoding |
| `test_jobs.py` | the manager with inline workers: event order and ids, a client replay equal to the stored route, late subscribers (after a worker that died with splices waiting too: they come before its `error`, ids rising strictly), `Last-Event-ID` resync, cancel (validated, stored, reproduced by its attempts), the queue and its limit, cancel before the route exists, continuations, the time limit, orphans running and queued, a worker that raises, a worker refusing a brand its request's area does not hold (`INVALID_REQUEST`), a store failure while cancelling (by hand and by the sweep, which goes on), a worker that does not start (the queue moves on), the TTL; a target run whose best is not its last route (an overshoot on a 5 × 5 lattice): a snapshot of the stored route before `done`, for live and late clients, and a cancel after the overshoot storing the closest route, reproduced by its attempts |
| `test_worker_process.py` (`slow`) | real spawned workers: a run to `done`, a cancel mid-run, a worker killed from outside (`WORKER_FAILED`), a fake worker that dies 60 kB into a 2 MB message (`WORKER_FAILED`, then a clean `stop()`), and a worker whose API process (a probe) is killed exiting within 3 s; about 4 s |
| `test_geocode_api.py` | `GET /geocode` with a `FakeGeocoder`: the answer's shape, at most five candidates, the cache across casing and spacing (one upstream call), each refusal (an empty, blank, missing or too long query; a missing or unknown region; `RATE_LIMITED` from a full queue with its `retry_after_s`, a cached query still answered; `GEOCODER_ERROR`; a 429 upstream, on an `httpx.MockTransport`, pausing the geocoder, the next search refused without a request; `GEOCODER_DISABLED`, with no cache file); the default service, its client on a `MockTransport` (`offline_clients`): Nominatim at its URL with the User-Agent and contact, the cache beside the store, the limits and the pause, its client closed by the lifespan. The other API tests serve with `geocoder="none"`, so they build no geocoder |
| `test_directions_api.py` | the document's directions (numbered, the stops' labels, the arrival's empty range at the end, the distances adding up), toasts (the ratio first, six to twelve), the fuel from the request's vehicle and the states and towns from the census fixture's index, all null without them, and the network fraction; `/directions` the document's directions, twice the same, jokes included; a route stored before them (its stored metrics without them, and one without its delays and turns) getting them; unknown routes 404 and a route whose build is gone 409 on both |
| `test_share_api.py` | `GET /routes/{id}/share.png` with and without a places index: a 1200 × 630 `image/png` with its Cache-Control, two concurrent first requests rendering once (a counted render) and getting the same bytes, one file, no lock left, a later request served from the file; `HEAD` first (it renders), its headers a GET's with no body; unknown and malformed ids (404, no file); a route whose build is gone (409, no lock left); a render that fails (no file, no lock, the next request draws it); two servers on one store rendering the same route at once, each part written before either rename (a barrier in `os.replace`), leaving one file and no part; on a lattice with signals, a slowest-time route's image headed by its document's `time_ratio`, "slower than the sensible route", and a distance route's by its `ratio`; `import worst_route.api.app` in a fresh interpreter leaving matplotlib out of `sys.modules` |
| `test_routes_api.py` | every endpoint over `httpx.AsyncClient` in-process, every refusal code, cancel over HTTP, a closed stream cancelling its job, a stream that subscribes only once it runs, make it worse and its mismatches (a base outside its request's masks included), a continuation that changes nothing reporting the stored route; ratio and duration jobs ending `target` with the target in `done` and the document (none for max), a target out of bounds or a ratio for a round trip refused, make it worse with a smaller target shrinking; a stored route differing from the drawn one by a parallel edge sent as a snapshot before `done`, with `GET /jobs/{id}` agreeing; a ratio target for a round trip refused before anything else, for a base route too; `Infinity` and `NaN` in a body refused; a slowest-time job under a soft no-left constraint, its turns and delays in `done` and the document; a strict constraint refusing a base route that turns left, a soft one taking it; a leg a strict constraint cannot drive answered `INFEASIBLE_TURN_CONSTRAINT` with its leg and constraint, one no route covers without it either (two one-way rings) `STOP_UNREACHABLE_WITHOUT_REPEAT` with strict set too; a body cut short refused without a `field`; `strict` alone and both constraints refused on `field: "constraints"`; on a lattice with signals, the document's `time_ratio`, `done`'s, and `arrival` counting the delays, the route's and the baseline's, for a route stored before the documents carried `baseline_delay_s` too; a collect job counting its bridges in every `progress` and in `done`, the document's `items` agreeing (a distance job's too), and the item and target refusals naming their field; `/brands` (its order, an unknown or missing region), a pilgrimage counting its visits in every `progress` and in `done`, the document's `brand` agreeing and a distance route's null, and the brand refusals (missing, misplaced, a ratio, unknown, out of reach) naming their field |

`tests/engine/test_plan.py` covers `plan_route`'s hooks, `tests/graph/test_loader.py` the loader,
and `tests/unit/test_cli.py` `serve`'s errors, options (the geocoder's included, with the line
it prints) and its taking `csr` on a build with transition tables. `tests/geocode/` covers the
geocoding package: `normalise`; the cache and its TTL on a fake clock; the rate limit on a fake
clock (a burst of ten serving six and refusing four, requests the interval plus a 300 ms
latency apart, the interval from the answer, a failure spacing the next request too, a turn
without a request taking no interval, `retry_after_s`, a refused caller taking no place and a
cancelled one leaving the queue); Nominatim's request and its parse of jsonv2 (the example of
Nominatim's documentation and a hand-written Portland body, `tests/fixtures/geocode/`), the
bbox filter, each failure and the refusals (429, 403), all on an `httpx.MockTransport`; and the
service's order (a cached answer taking no turn, identical queries queued behind one in flight
asking once, at most five candidates whatever the geocoder answers, the pause after a 429 or
403 and its end). httpx's ASGI transport hands a response over once the app has finished it, so
an SSE stream is read whole after its end; reconnecting mid-run is tested on the manager, and
over a real socket by hand (**Measured**).

## Limitations

- **One process.** Jobs live in the API process's memory, so uvicorn runs one process; a restart
  loses running jobs (their routes so far are not stored) and ended jobs' records.
- **No public limits** beyond the job cap and queue: no per-client limits, no authentication,
  no CORS (the frontend's dev server proxies). `docs/PLAN.md` §4.5 lists what public hosting
  adds.
- **The geocoder's rate limit is per process**: one request at a time from this server, a
  second from each answer to the next request, which is the application's only while uvicorn
  runs one process and one server asks Nominatim (two servers, as the coordinator's and a lane's
  pointed at the real service, would share the policy's second). The pause after a refusal is
  per process too. One client can take every slot: there is no per-client share. Nominatim
  itself is for light use; public hosting moves to Photon or a paid geocoder (`docs/PLAN.md`
  §4.4).
- **An abandoned search holds its place in the geocoder's queue**: a search whose client has
  disconnected still waits for its turn, goes upstream and caches its answer (**Geocoding**).
  And the queue's cap counts intervals, not upstream latencies: each request ahead may take up
  to the 10 s upstream timeout as well as its second, so the last caller let into the queue
  (four queued ahead of it, one in flight) can wait about a minute in the worst case, during
  which the frontend shows "Searching…".
- **Spawn cost**: a job's first events after `snapped` wait for a fresh process (0.26–1.4 s
  measured).
- **Share images are kept forever** in `share/`, one per route and drawing version; nothing
  removes them, and an image drawn before the places index was written keeps no outlines until
  `share/` is cleared.
