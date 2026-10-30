# API

The HTTP API, the only contract between the backend and the frontend: the endpoints, the
request and error shapes, the job lifecycle and its worker processes, the progress stream over
Server-Sent Events, the route store, and `worst-route serve`. Code: `src/worst_route/api/` and
`graph/loader.py`. The design it implements is `docs/PLAN.md` §4 and §1.1; the conventions are
`CLAUDE.md`. It plans with the engine (`docs/reference/engine.md`, **Planning**) and nothing
else: every route it returns has passed the validator.

| Module | What it holds |
|---|---|
| `app.py` | `create_app`, `ApiSettings`, `Api` (the app's state), the error handlers |
| `routes_api.py` | the endpoints, the checks `POST /jobs` makes before a job exists, `ApiError` |
| `schemas.py` | the request and response models (pydantic), the SSE payloads |
| `jobs.py` | `JobManager`, `JobSettings`, `Job`, `Subscription`, `prepare`, `TooManyJobs` |
| `worker.py` | `run_job` (what a worker process runs), `JobSpec`, `RouteArrays`, the worker's messages |
| `sse.py` | `Event`, `path_points`, the payload builders, `server_sent` |
| `store.py` | `RouteStore`, `StoredRoute`, `route_id` |

Coordinates are `[lon, lat]` in GeoJSON and `{lat, lon}` in requests; every number is metric
(metres, seconds). There is no `/api` prefix: the frontend's dev server proxies to the root.

## Running it

```
worst-route serve --region R [--build-id ID] [--host 127.0.0.1] [--port 8000] [--workers N]
                  [--backend auto|csr|python] [--store PATH]
```

It loads the region's build (`--build-id`, else the region's `latest`) before it listens, so a
missing build is one line on stderr and exit 1. Then uvicorn serves one process. The jobs live
in that process's memory, and each runs in a worker process of its own, `--workers` at once
(default `API_MAX_JOBS`, cores − 2). The routes go to `--store`, by default
`<WORST_ROUTE_DATA>/routes.sqlite`; a lane that reads the main tree's builds through
`WORST_ROUTE_DATA` points `--store` into its own tree. `--port 0` binds a free port, which
uvicorn's log line names. The job manager's own lines (a job cancelled for want of a subscriber,
a worker's traceback) go to the same log. `--backend` is the engine's
(`docs/reference/graph.md`, **Choosing a backend**), and `serve` prints the one it took as
`plan` does (`backend: csr`): `auto`, the default, is csr when numba imports.

`create_app(ApiSettings(data_dir, region, build_id="latest", backend, store_path, jobs))` builds
the same application in-process; the tests drive it over `httpx` (**Tests**).

## Requests and errors

**`POST /jobs`** takes the engine's `RouteRequest` field for field, under the engine's names, plus
what the API and the presentation need:

```json
{"region": "portland",
 "stops": [{"lat": 45.5231, "lon": -122.6765, "label": "Powell's"},
           {"lat": 45.4817, "lon": -122.6263, "label": "Reed College"}],
 "objective": "distance", "tier": "auto", "leash_km": "auto",
 "budget_s": 20, "max_iterations": 1000000, "seed": 12345,
 "target": {"kind": "max"},
 "vehicle": {"mpg": 24, "gas_price_usd": 3.89},
 "departure": "2026-10-31T09:00:00-07:00",
 "base_route_id": null}
```

- `stops`: at least two, in order; the same point first and last makes a round trip. `label` is
  kept for the presentation.
- `objective`: `distance`; `time`, `collect`, `brand` and `alphabet` are `NOT_YET`.
- `tier`: `auto` or `T0`..`T4`. `leash_km`: `auto` or in (0, 150].
- `budget_s`: in (0, 120] (over 120 is `BUDGET_TOO_LARGE`), default 20.
- `max_iterations`: default 1,000,000, so the seconds budget ends a run. The CLI keeps the
  engine's 5,000.
- `seed`: in [0, 2⁶³ − 1]; left out, a random one below 2³¹, returned in the 202.
- `target`: `{kind: "max"}` (the default: as bad as the budget allows), `{kind: "ratio",
  value}` (the route's length as `value` × the baseline's, more than 1; not for a round trip)
  or `{kind: "duration", seconds}` (at most 7 days); reached within ±2 %
  (`docs/reference/engine.md`, **Targets**). Each kind takes only its own number. A target out
  of these bounds is `INVALID_REQUEST`, and so is a ratio target for a round trip (its stops',
  or for "make it worse" its base route's): `{code: "INVALID_REQUEST", message: "a round trip
  has no ratio target: its baseline is 0 m", field: "target"}`, before any other check.
- `vehicle` and `departure` are kept with the route; `departure` gives the document's `arrival`.
- `base_route_id`: "make it worse" (**Make it worse**).
- An unknown field is refused, so `constraints` is `INVALID_REQUEST` until M3.

**Every error** answers `{code, message}` and the error's own fields, at the top level (not
FastAPI's `detail`):

| Status | Code | When |
|---|---|---|
| 422 | `INVALID_REQUEST` (+ `errors`, pydantic's without their `input`; or + `field`) | a body or field that does not parse (a seed beyond 2⁶³ − 1 included, `Infinity` or `NaN` for a number), an unknown objective, a target out of bounds, a ratio target for a round trip (`field: "target"`) |
| 422 | `UNKNOWN_REGION` | a region this server does not serve |
| 422 | `STOP_OUTSIDE_REGION` (+ `stop_index`) | outside the region's bbox: the build's `params.bbox`, else its nodes' extent |
| 422 | `NO_NODE_NEAR` (+ `stop_index`, null when no single stop is to blame) | no node within 5 km can play the stop's role |
| 422 | `STOP_UNREACHABLE_WITHOUT_REPEAT` (+ `leg`, `stop_index`) | no route covers the leg without repeating a road, after the re-snap fallback (`stop_index` is the leg's later stop) |
| 422 | `BUDGET_TOO_LARGE` (+ `max_s`) | `budget_s` over 120 |
| 422 | `NOT_YET` (+ `field`) | an objective the engine does not have yet |
| 422 | `UNKNOWN_ROUTE` (+ `base_route_id`) | a base route that is not stored |
| 422 | `BUILD_MISMATCH` | a base route planned on another region, `graph_version` or engine version; `/validate` on a route another engine version made (its stops could be re-derived differently: re-plan it), as `worst-route validate` refuses one |
| 422 | `BASE_ROUTE_MISMATCH` (+ `field`) | stops, tier or leash given that differ from the base route's; or a base route `plan.check_base` refuses under its request's resolved stops and masks (`field` `stops`: its stops are not where they resolve now; `route`: a node outside the area or an edge the request bans) |
| 429 | `TOO_MANY_JOBS` | every worker busy and the queue full |
| 404 | `NOT_FOUND` | an unknown (or expired) job, an unknown route, an unknown path |
| 405 | `METHOD_NOT_ALLOWED` | a method a path does not take |
| 409 | `BUILD_UNAVAILABLE` | a stored route whose build is no longer on disk at its `graph_version` |
| any other | `HTTP_<status>` | any other HTTP error Starlette raises, its status in the code |

The SSE `error` event uses the same body, with the codes above that a worker can meet, and:
`CANCELLED` (cancelled while queued or before its route existed), `VALIDATION_FAILED` (a route
the validator refused: an engine bug, never stored), `WORKER_FAILED` (an exception, a worker
that did not start, or one that died without a result, partway through a message included) and
`WORKER_TIMEOUT` (still running `API_KILL_GRACE_S` after its budget, with no route yet).

`POST /jobs` refuses before a job exists, in this order: the body (the target's bounds
included), a ratio target for a round trip, the region, the objective, the budget, the job
limit (429), the base route, each stop against the bbox, then the
**pre-flight** (`jobs.prepare`): the request's masks and `resolve_stops` (snapping and the
re-snap fallback), in a worker thread, and for a base route `plan.check_base`. The worker repeats
them, deterministically, so a request that passes the pre-flight is not refused for its stops
later.

## Endpoints

| Method | Path | Answer |
|---|---|---|
| GET | `/health` | `{status: "ok", builds: [{region, build_id, graph_version}]}` |
| GET | `/regions` | `[{region, build_id, graph_version, bbox: [w, s, e, n], counts, tiers: [{tier, nodes, edges}]}]` |
| POST | `/snap` | `{region, lat, lon, role: start\|via\|end, tier?}` → `{node, lat, lon, moved_m, note}` |
| POST | `/jobs` | 202 `{job_id, events_url, seed}` |
| GET | `/jobs/{id}/events` | the job's events (**Events**); `Last-Event-ID` resumes |
| GET | `/jobs/{id}` | the job's status with its route's current snapshot (polling) |
| DELETE | `/jobs/{id}` | cancels it (**Jobs**); answers its status, without the snapshot |
| GET | `/routes/{id}` | the route document |
| GET | `/routes/{id}/geojson` | the FeatureCollection `engine.io.feature_collection` makes: the route, the baseline (null for a round trip) and the stops |
| POST | `/routes/{id}/validate` | `{route_id, graph_version, ok, checks: [{name, ok, index, detail}]}`, the stops re-derived from the stored request (by this engine version only) |

**`/snap`** judges the stop in the tier's nodes and every node within `BUBBLE_KM` (3 km) of it,
as a job's masks do near a stop (`auto` is T4); its `note` says why a stop moved ("moved 214 m
to the nearest through street" for a via at Reed College's cul-de-sac). It is a preview: a job
also keeps its stops apart and may re-snap them, and its `snapped` event is the authority.

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
 metrics:    {length_m, baseline_length_m, ratio, time_s, baseline_time_s, time_ratio,
              departure, arrival, target},
 run:        {attempts, accepted, stopped_because, elapsed_s, backend},
 validation: {ok: true, checked_at}}
```

`ratio` and `time_ratio` are null for a round trip (its baseline is 0 m); `arrival` is
`departure` plus `time_s`, null without one; `target` is `{kind, value, achieved,
within_tolerance}` for a ratio or duration target (the target, the route's ratio or seconds, and
whether that is within ±2 %), null for `max`; `stopped_because` is `target` (within it),
`budget`, `converged`, `cancelled` or `timeout`. The presentation's fields (directions, toasts, comparisons, fuel,
states, towns) arrive with M4.

## Events

`GET /jobs/{id}/events` is a `text/event-stream` (sse-starlette, a comment ping every 15 s).
Each event has an `id`, counting up per job, an `event` name and a JSON `data`:

| Event | When | `data` |
|---|---|---|
| `snapped` | at the job's creation (from the pre-flight) | `{stops: [{input: {lat, lon}, label, node, lat, lon, moved_m, note}], tier, leash_km, active_nodes}` |
| `baseline` | when the worker has built the route the loop starts from | `{length_m, time_s, geometry}` (null for a round trip) |
| `snapshot` | right after `baseline` (iteration 0: the seed, or the base route), after every 50th `progress`, just before `done` when the stored route is not the one the splices drew (**Jobs**), and first on every (re)connect once the route exists | `{iteration, elapsed_s, node_count, stop_idx, geometry, node_index, metrics}` |
| `progress` | at most every 0.1 s while splices are accepted | `{iteration, elapsed_s, score, metrics: {length_m, time_s, ratio}, splices: [{i, j, node_count, coords, node_index}]}` |
| `done` | the route is stored (or was already: its existing id, **The store**) | `{route_id, stopped_because, metrics}`: the stored route's `{length_m, time_s, ratio, target}`, `target` as in the document (null for `max`) |
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
  `POST /jobs` and read the route later. The frontend, when built, also sends `DELETE` on
  `pagehide` (`fetch` with `keepalive`).
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
request bans (`BASE_ROUTE_MISMATCH`, in the pre-flight and again in the worker). The new
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
  2, `done` after 1.8 s at 8 h 49 min (37.9×); a max run, seed 1, still `5ea844bffd40`; "make it
  worse" from it with a 30× ratio, seed 1, `done` after 5.7 s at 30.58×, every `progress` lower
  than the one before, its `/validate` ok. Each target job's `done` and document carry its
  `target`.

## Tests

`tests/api/` (`support.py` writes lattice builds into a temporary data directory and holds the
helpers; the async tests run on anyio's pytest plugin, asyncio only):

| Test file | What it covers |
|---|---|
| `test_schemas.py` | the request mirrors the engine's, its defaults, the seed's bounds, stops left out only with a base, malformed requests refused; the target as the engine's, each kind with its own number and within its bounds; `done`'s target typed |
| `test_store.py` | the round trip (the stored route validates), unknown ids, the first record kept, what the id covers |
| `test_sse.py` | path points and node indices, the per-edge simplification keeping nodes and original values, splices, metrics, the event encoding |
| `test_jobs.py` | the manager with inline workers: event order and ids, a client replay equal to the stored route, late subscribers (after a worker that died with splices waiting too: they come before its `error`, ids rising strictly), `Last-Event-ID` resync, cancel (validated, stored, reproduced by its attempts), the queue and its limit, cancel before the route exists, continuations, the time limit, orphans running and queued, a worker that raises, a store failure while cancelling (by hand and by the sweep, which goes on), a worker that does not start (the queue moves on), the TTL; a target run whose best is not its last route (an overshoot on a 5 × 5 lattice): a snapshot of the stored route before `done`, for live and late clients, and a cancel after the overshoot storing the closest route, reproduced by its attempts |
| `test_worker_process.py` (`slow`) | real spawned workers: a run to `done`, a cancel mid-run, a worker killed from outside (`WORKER_FAILED`), a fake worker that dies 60 kB into a 2 MB message (`WORKER_FAILED`, then a clean `stop()`), and a worker whose API process (a probe) is killed exiting within 3 s; about 4 s |
| `test_routes_api.py` | every endpoint over `httpx.AsyncClient` in-process, every refusal code, cancel over HTTP, a closed stream cancelling its job, a stream that subscribes only once it runs, make it worse and its mismatches (a base outside its request's masks included), a continuation that changes nothing reporting the stored route; ratio and duration jobs ending `target` with the target in `done` and the document (none for max), a target out of bounds or a ratio for a round trip refused, make it worse with a smaller target shrinking; a stored route differing from the drawn one by a parallel edge sent as a snapshot before `done`, with `GET /jobs/{id}` agreeing; a ratio target for a round trip refused before anything else, for a base route too; `Infinity` and `NaN` in a body refused |

`tests/engine/test_plan.py` covers `plan_route`'s hooks, `tests/graph/test_loader.py` the loader,
and `tests/unit/test_cli.py` `serve`'s errors, options and its taking `csr` on a build with
transition tables. httpx's ASGI transport hands a response over once the app has finished it, so
an SSE stream is read whole after its end; reconnecting mid-run is tested on the manager, and
over a real socket by hand (**Measured**).

## Limitations

- **One process.** Jobs live in the API process's memory, so uvicorn runs one process; a restart
  loses running jobs (their routes so far are not stored) and ended jobs' records.
- **No public limits** beyond the job cap and queue: no per-client limits, no authentication,
  no CORS (the frontend's dev server proxies). `docs/PLAN.md` §4.5 lists what public hosting
  adds.
- **Spawn cost**: a job's first events after `snapped` wait for a fresh process (0.26–1.4 s
  measured).
