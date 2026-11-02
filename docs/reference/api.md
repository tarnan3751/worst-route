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
| `routes_api.py` | the endpoints, the checks `POST /jobs` makes before a job exists (`check_inside`, `preflight`, which `POST /draw` makes too), `ApiError`, `api_of`, `region_of`, `bbox_of`, `holds`, `display_of`, `stored_route`, `build_of`, `places_of`, `stored_delays`, `presentation_of`, `directions_of` |
| `pick_api.py` | `POST /regions/pick`, `pick_region` (**Picking a build**) |
| `geocode_api.py` | `GET /geocode` (**Geocoding**) |
| `share_api.py` | `GET` and `HEAD /routes/{id}/share.png`, `SHARE_IMAGE_VERSION` (**Share image**) |
| `daily_api.py` | `GET /daily`, `today`, `daily_seed`, `daily_region`, `TownPool` (the towns a trip of the day runs between), `draw_trips`, `plannable` (**The daily route**) |
| `challenges_api.py` | `GET /challenges`, `generate_day`, `challenge_seed`, `TYPES` (**The daily challenges**) |
| `directions_api.py` | `GET /routes/{id}/directions` |
| `draw_api.py` | `POST /draw`, `POST /draw/finish`, `DrawContexts` (**Drawing a route**) |
| `ready_api.py` | `GET /ready` (**Endpoints**) |
| `basemap_api.py` | `GET /config`, the basemap's files under `/basemap/` (`BasemapFiles`), `basemap_of` (**Basemap**) |
| `logs.py` | `RequestLog` (request ids, access lines), `JsonFormatter`, `ContextFilter`, `json_log_config` (**Logs**) |
| `static.py` | `create_site`: the frontend's build at `/` and the API under `/api` (`serve --frontend`) |
| `limits.py` | `Limits` (`LOCAL`, `PUBLIC`), `GeocodeQuota`, `client_of` (who a client is), `client_hash` (**Public limits**, **Logs**) |
| `schemas.py` | the request and response models (pydantic), the SSE payloads |
| `jobs.py` | `JobManager`, `JobSettings`, `Job`, `Search`, `Subscription`, `prepare`, `TooManyJobs` |
| `worker.py` | `run_job` (what a worker process runs), `JobSpec`, `RouteArrays`, the worker's messages |
| `sse.py` | `Event`, `path_points`, the payload builders, `server_sent` |
| `store.py` | `RouteStore`, `StoredRoute`, `route_id`; the daily route's rows, `DailyRow`; the daily challenges' rows |

The geocoding proxy's backend is the `geocode/` package (**Geocoding**); the share image's, the
directions' and the document's presentation fields are the `presentation/` package
(`docs/reference/presentation.md`).

Coordinates are `[lon, lat]` in GeoJSON and `{lat, lon}` in requests; every number is metric
(metres, seconds). The API's paths have no `/api` prefix: the frontend's dev server proxies
`/api` to the root, and `serve --frontend` mounts the API under `/api` beside the frontend's build
(`api/static.py`, below).

## Running it

```
worst-route serve --region R [--region R2 ...] [--build-id ID] [--host 127.0.0.1] [--port 8000]
                  [--workers N] [--backend auto|csr|python] [--store PATH]
                  [--geocoder nominatim|none] [--nominatim-url URL]
                  [--trust-proxy] [--log-format text|json] [--public] [--frontend DIR]
                  [--basemap-name NAME]
```

It serves the regions `--region` names, one or several (`--region oregon --region us-t1`: a
state's all roads beside the national T1 build), each a region of `config.REGIONS`, given once
(twice is exit 2), the first the default (the basemap's name). Each trip runs on one of them,
which the frontend asks the server to pick (**Picking a build**) and names in its request. It
loads each region's build (`--build-id` names the first region's; the others, and the first
without it, serve their `latest`) and its geometry before it listens, so a missing build, one saved before an array of the format existed
(`docs/reference/graph.md`, **The build format**), or one without its geometry (the `save
--force` that writes it again) is one line on stderr and exit 1. Then uvicorn serves one process,
which Ctrl+C stops within 5 s (a graceful shutdown of 5 s, as the desktop app's, so an open request
or event stream does not hold the exit). Each build's spatial index and backend are made in a thread once it starts (`app.warm_builds`;
`docs/reference/graph.md`, **Loading builds**), so it listens without waiting for them: on its
first start after `kernels.py` changes, or with a new `NUMBA_CACHE_DIR`, the Numba kernels compile
then, and a job asked for meanwhile starts once they have. The jobs live
in that process's memory, and each search of a job (one, or one a seed for a portfolio:
**Portfolios**) runs in a worker process of its own, `--workers` at once (default
`API_MAX_JOBS`, cores − 2). The routes go to `--store`, by default
`<WORST_ROUTE_DATA>/routes.sqlite`; a lane that reads the main tree's builds through
`WORST_ROUTE_DATA` points `--store` into its own tree, and runs with `--geocoder none` or
`--nominatim-url` pointed at a stub of its own (no agent calls Nominatim). The share images are
cached in `share/` beside the route store, so a lane's stay in its own tree too. At startup it
loads each region's places index that `worst-route aux places` has written under
`<WORST_ROUTE_DATA>/aux/places/` (one it cannot read, as format 1's parquet files, is one line on
stderr and exit 1, before the route store opens: `docs/reference/presentation.md`, **Places**); a
route on a region without its own uses that of a served
region whose states hold all of the route's region's (`config.Region.states`), the fewest states
first, never one clipped to a bbox, whose index holds only its bbox's towns
(`routes_api.places_of`): the national T1 build's covers every state's (**Share image**). `--port 0` binds a free port, which
uvicorn's log line names. The job manager's own lines (a job cancelled for want of a subscriber,
a worker's traceback) go to the same log. `--backend` is the engine's
(`docs/reference/graph.md`, **Choosing a backend**), and `serve` prints the one it took as
`plan` does (`backend: csr`), named without making it (`backends.backend_name`, which imports
numba, 0.1–0.2 s, but compiles nothing): `auto`, the default, is csr when numba imports, and
`csr` without a numba that imports is one line and exit 1, before it listens.

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

`--trust-proxy` is for a server behind a reverse proxy (`docs/reference/hosting.md`): the client
is then the last address of `X-Forwarded-For`, the one the proxy appended, and a request's id
the proxy's `X-Request-ID` (**Logs**); without it both headers are ignored, and so is uvicorn's
own handling of them (`proxy_headers` off), so the client is always the peer. It prints
`trusting the proxy's X-Forwarded-For and X-Request-ID` after the geocoder's line.
`--log-format json` logs one JSON object a line on stdout (**Logs**); `text`, the default, is
uvicorn's format, as before. `--public` applies the public limits (**Public limits**) and prints
them last: `public: a client runs 1 job at once with 3 waiting, budgets up to 60 s, 10 place
searches, 60 picks of a build, 240 drawing steps and 10 finished drawings a minute`. The basemap the frontend draws is `WORST_ROUTE_BASEMAP`'s, `raster` (the
default) or `pmtiles` (**Basemap**), and with `pmtiles` `serve` prints the file it serves,
`basemap: pmtiles <data>/basemap/<name>.pmtiles`, `--basemap-name`'s name, by default the first
region's, before the proxy's and the public lines.

An answer of 1,024 bytes or more goes gzipped, with `Vary: Accept-Encoding`, to a client that
accepts it (`GZipMiddleware`, `config.GZIP_MINIMUM`), but never the event stream, which goes out
event by event, nor the share image or the basemap's tiles, compressed already
(`app.GZIP_EXCLUDED`; `docs/reference/hosting.md`, **Compression and caching**).

`--frontend DIR` serves the frontend's build (`frontend/dist`, `npm run build`) too, from the
same process: `api/static.py`'s `create_site` mounts the API, its paths unchanged, under `/api`
(the prefix the frontend calls) and the build at `/` (`index.html` with `Cache-Control:
no-cache`, the content-hashed `assets/` with `public, max-age=31536000, immutable`, both gzipped
as the API's answers are; a path the build does not have is 404). The site's lifespan runs the
API's. A directory without
`index.html` is one line (``no frontend build at DIR: run `npm run build` under frontend/``)
and exit 1; it prints `frontend: DIR at /, the API under /api` last. The container runs so
(`docs/reference/hosting.md`). The access lines then name the whole path, `/api/...`; the
build's own files are not logged.

`create_app(ApiSettings(data_dir, builds=((region, build_id), ...), backend, store_path, jobs,
geocoder="nominatim", nominatim_url, user_agent=None, contact, trust_proxy=False, public=False,
basemap="raster", basemap_name=None, basemap_dir=None))` builds the same application in-process
(a build id `"latest"` for the region's latest; `user_agent` the geocoder's in place of
`config.Settings.user_agent`, the contact still added to it; `basemap_dir` the basemap's
directory in place of `<data_dir>/basemap/`: both the desktop app's, `docs/reference/desktop.md`),
and `create_app(settings, geocode=service)` with a geocoding service of the caller's own (a
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
 "reorder": false, "certified": false,
 "tier": "auto", "leash_km": "auto",
 "budget_s": 20, "max_iterations": 1000000, "seed": 12345,
 "target": {"kind": "max"},
 "vehicle": {"mpg": 24, "gas_price_usd": 3.89},
 "departure": "2026-10-31T09:00:00-07:00",
 "base_route_id": null, "portfolio": 1}
```

- `stops`: at least two, in order; the same point first and last makes a round trip. `label` is
  kept for the presentation.
- `objective`: `distance` (the longest), `time` (the slowest: driving time and the delays at
  signals and stop signs), `collect` (the most of one item, then the longest), `brand` (the
  most locations of one brand, then the longest; `docs/reference/engine.md`, **Objectives**) or
  `alphabet` (named roads whose letters run A, B, C, ..., the most letters, then the longest;
  `docs/reference/engine.md`, **Alphabet**). `alphabet` takes a start and an end only (a stop
  between them is `INVALID_REQUEST` on `field: "stops"`, "the alphabet route takes a start and
  an end"), no target but `max` (`field: "target"`, "the alphabet route takes no target: it
  counts letters"), and cannot be continued (`base_route_id` is `INVALID_REQUEST` on `field:
  "base_route_id"`, "the alphabet route cannot be continued", before the base route is looked
  up); a round trip is a start and an end. A stored alphabet route may still be the base of a
  request with another objective: "make it worse" continues its edges like any route's, under
  the new request's objective. The CLI differs because its `--base` takes the objective from
  the file, so there an alphabet route's file is refused.
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
  `STOP_UNREACHABLE_WITHOUT_REPEAT` with the message "No route from stop 1 to stop 2 without
  repeating a road and with no road twice, …. Try without it." (the seed is a heuristic: a legal
  route may still exist).
- `reorder`: false by default (and left out of the engine's request then); true visits the stops
  between the first and the last in the worst order: every order of them is tried, the start
  and the end fixed (a round trip's start is its end), and the one whose seed is the longest
  is planned (`docs/reference/engine.md`, **Seed and baseline**, **The worst order**). At most 7
  stops, 5 on a build of more than 100,000 nodes (`us-t3`, Oregon): more is
  `TOO_MANY_STOPS_TO_REORDER` with the build's `max_stops` ("the worst order takes at most 5
  stops on us-t3, not 6"); under `alphabet` it is `INVALID_REQUEST` on `field: "reorder"` ("the
  alphabet route takes no worst order: it has no stop to reorder"); with `base_route_id` it is
  ignored, a continuation keeping its base route's order (**Make it worse**). The pre-flight
  checks the stops in their given order, so a trip that cannot be planned in its own order is
  refused as without `reorder`, and the job's worker runs the search (with 7 stops on Portland's
  build about 12 s, on `us-t3` with 5 stops up to about 7 s, before the loop's budget starts).
  The route visits the stops in that order: the `snapped` event, the route document and its
  request say so (`order`, each stop's position among those given), and the ratio is against the
  sensible route through the stops in that order.
- `certified`: false by default (and left out of the request as stored then); true asks for the
  certified worst, the provably longest legal route for the stops within the request's area,
  found by CP-SAT (`docs/reference/engine.md`, **Certified worst**): proven (`run.certified.status:
  "optimal"`, `stopped_because: "optimal"`) or, when the budget ends first, the longest found with
  the solver's bound. It takes the distance objective only, no target, no worst order, no turn
  constraint, no hard mode, no round trip, one seed and no base route: each of those is
  `INVALID_REQUEST` on `field: "certified"`, naming the conflict ("a certified route takes no
  target: it is the longest there is"; "a certified route runs one search: no portfolio"; "a
  certified route is planned from its stops: no base route"), and a drawing refuses it ("a drawing
  is drawn by hand: no certificate"). Its area (the edges the request's masks allow that lie on
  some walk from a stop to the next, and the nodes those walks pass) holds at most 300 nodes and
  1,000 edges: more is `AREA_TOO_LARGE_TO_CERTIFY` on `field: "leash"`. With `leash_km: "auto"`
  the leash is the widest, in 10 m steps from 50 m, whose area holds at most 300 nodes and 600
  edges (so a default request is proven within the default budget), which the `snapped` event's
  `leash_km` and the document's `run.certified.leash_m` say, the request as stored keeping
  `"auto"`; an explicit leash is used as given. `max_iterations` does not apply.
- `tier`: `auto` or `T0`..`T4`. `leash_km`: `auto` or in (0, 5000].
- `budget_s`: in (0, 120] (over 120 is `BUDGET_TOO_LARGE`; on a public server in (0, 60],
  **Public limits**), default 20.
- `max_iterations`: default 1,000,000, so the seconds budget ends a run. The CLI keeps the
  engine's 5,000.
- `seed`: in [0, 2⁶³ − 1]; left out, a random one below 2³¹, returned in the 202.
- `target`: `{kind: "max"}` (the default: as bad as the budget allows), `{kind: "ratio",
  value}` (the route's length as `value` × the baseline's, more than 1; under `time` its
  experienced time, driving and the delays, against the baseline's; not for a round trip) or
  `{kind: "duration", seconds}` (at most 7 days, of experienced time); reached within ±2 %
  (`docs/reference/engine.md`, **Targets**). Each kind takes only its own number. A target out
  of these bounds is `INVALID_REQUEST`, and so is a ratio target for a round trip (its stops',
  or for "make it worse" its base route's): `{code: "INVALID_REQUEST", message: "A round trip
  has no ratio to aim for. Set the goal to a drive time.", field: "target"}`, before any other check.
- `vehicle` and `departure` are kept with the route; `departure` gives the document's `arrival`.
- `base_route_id`: "make it worse" (**Make it worse**).
- `portfolio`: 1 by default (and left out of the request as stored then); from 2 up, that many
  searches of the request at once, seeds `seed` to `seed + portfolio − 1`, each in a worker of
  its own, the best of them kept (**Portfolios**). At most `MAX_PORTFOLIO` (4; more is
  `INVALID_REQUEST` on `field: "portfolio"`), 2 on a public server (**Public limits**), and
  never more than the server's workers (`--workers`): more is `PORTFOLIO_TOO_LARGE` with `max`,
  the most this server runs, which `GET /config` gives as `limits.portfolio_max`. The alphabet
  route and a continuation run one search: `portfolio` above 1 with `objective: "alphabet"` or
  a `base_route_id` is `INVALID_REQUEST` on `field: "portfolio"` ("a continuation runs one
  search: no portfolio"), and so is a drawing's (**Drawing a route**). `seed + portfolio − 1`
  must stay within 2⁶³ − 1. With `reorder`, every search visits the stops in the same order:
  the worst order does not depend on the seed.
- An unknown field is refused.

**Every error** answers `{code, message}` and the error's own fields, at the top level (not
FastAPI's `detail`), but for the basemap's files' range errors: `/basemap/` answers a malformed
`Range` 400 and a range starting past the end 416, both as Starlette's `text/plain` (**Basemap**):

| Status | Code | When |
|---|---|---|
| 422 | `INVALID_REQUEST` (+ `errors`, pydantic's without their `input`, and `field` when every error lies in one field of the body; or + `field`) | a drawing's request asking for an objective but `distance`, a target but `max`, the worst order, a base route, a portfolio or a certificate (`field` `objective`, `target`, `reorder`, `base_route_id`, `portfolio`, `certified`), its path over `MAX_DRAW_EDGES` edges or with an id that is not a whole number in [0, 2³¹) (`field: "path"`; **Drawing a route**); a body or field that does not parse (a seed beyond 2⁶³ − 1 included, or a portfolio's last seed, `Infinity` or `NaN` for a number, a portfolio above 4 on `field: "portfolio"`), an unknown objective, a target out of bounds, a ratio target for a round trip (`field: "target"`), `collect` without an item or an item for another objective, or an unknown item (`field: "item"`), a ratio target under `collect` or `brand` (`field: "target"`), `brand` without a brand, a brand for another objective, one the build does not have or one the request's area does not hold (`field: "brand"`), `alphabet` with a stop between its ends (`field: "stops"`), a target (`field: "target"`), a base route (`field: "base_route_id"`) or `reorder` (`field: "reorder"`), a portfolio above 1 under `alphabet` or with a base route (`field: "portfolio"`), `certified` with another objective, a target, the worst order, a turn constraint or hard mode, a round trip, a portfolio or a base route (`field: "certified"`), both turn constraints or `strict` without one (`field: "constraints"`); a place search's query empty or over 200 characters once normalised (`field: "q"`); a share image's theme that is not one of the ten (`field: "theme"`; **Share image**); `/brands` without a region; a challenges' date that does not parse (pydantic's, with no `field`), or one outside today and the 30 days before, or on a public server one before today that is not kept (`field: "date"`; **The daily challenges**) |
| 422 | `UNKNOWN_REGION` | a region this server does not serve |
| 422 | `STOP_OUTSIDE_REGION` (+ `lat`, `lon`, and `stop_index` but for `/snap`) | outside the region's bbox: the build's `params.bbox`, else its nodes' extent; for `POST /regions/pick`, a stop no served region's bbox holds with the others (**Picking a build**). The message is "That stop is outside the map." and where to try instead, by the regions served: "Try somewhere in the lower 48." when one holds every contiguous state (the national builds), else their names, "Try somewhere in Oregon or Portland." (`outside_sentence`); the stop's coordinates are in `lat` and `lon` |
| 422 | `NO_NODE_NEAR` (+ `stop_index`, null when no single stop is to blame) | no node within 5 km can play the stop's role |
| 422 | `STOP_UNREACHABLE_WITHOUT_REPEAT` (+ `leg`, `stop_index`) | no route covers the leg without repeating a road, after the re-snap fallback (`stop_index` is the leg's later stop), with or without a strict turn constraint: under one, when the seed's legs cannot be built without it either; under hard mode, also when they can be built without hard mode (alone, or with the strict constraint alone), the message then "No route from stop 1 to stop 2 without repeating a road and with no road twice, …. Try without it." (its stable ending the frontend's retry matches). Every such message is one sentence, its stops numbered from 1 as the app's markers are: "No route from stop 1 to stop 2 without repeating a road, after moving stop 2 up to 10 times." |
| 422 | `INFEASIBLE_TURN_CONSTRAINT` (+ `leg`, `stop_index`, `constraint`) | under a strict turn constraint (`constraint`: `no_left` or `no_right`), no route covers the leg without repeating a road or turning that way, after the re-snap fallback, while the seed's legs can be built without the constraint (one more build, on failure only; under hard mode too, when they can be built under hard mode alone); the message "No route from stop 1 to stop 2 without repeating a road and with no left turns, …. Try without strict." |
| 422 | `AREA_TOO_LARGE_TO_CERTIFY` (+ `nodes`, `edges`, `max_nodes`, `max_edges`, `leash_m`, `field: "leash"`) | a certified request whose area, at its leash (the one asked for, or with `auto` the narrowest that joins its stops when no small enough one does), holds more than 300 nodes or 1,000 edges; from the pre-flight. The message names the counts and the caps: "This area is too big to prove. It holds 1,132 junctions and 3,865 roads; a proof allows 300 and 1,000. Shorten the corridor or bring the stops closer." ("Even the narrowest corridor that joins the stops, 1.5 km, holds …. Bring the stops closer." when no leash small enough joins them) |
| 422 | `TOO_MANY_STOPS_TO_REORDER` (+ `max_stops`) | `reorder` with more stops than the region's build takes: 7, or 5 on a build of more than 100,000 nodes; checked after the objective, before the budget |
| 422 | `BUDGET_TOO_LARGE` (+ `max_s`) | `budget_s` over 120, or over 60 on a public server; the message "The time budget is at most 120 s." |
| 422 | `PORTFOLIO_TOO_LARGE` (+ `max`) | `portfolio` over the most this server runs: 4, 2 on a public server, and never more than its workers (`--workers`); checked after the budget; the message "At most 4 runs at once on this server." |
| 422 | `UNKNOWN_ROUTE` (+ `base_route_id`) | a base route that is not stored |
| 422 | `BUILD_MISMATCH` | a base route planned on another region, `graph_version` or engine version; `/validate` on a route another engine version made (its stops could be re-derived differently: re-plan it), as `worst-route validate` refuses one |
| 422 | `BASE_ROUTE_MISMATCH` (+ `field`) | stops, tier or leash given that differ from the base route's; or a base route `plan.check_base` refuses under its request's resolved stops and masks (`field` `stops`: its stops are not where they resolve now; `route`: a node outside the area, an edge the request bans, a turn its strict constraint refuses, or under hard mode a road it comes back to) |
| 422 | `ILLEGAL_PATH` (+ `refusal`, `index`) | a drawing's path that is not a legal route's beginning: `refusal` says why (`BROKEN_CHAIN`, `OUTSIDE_AREA`, `NODE_REUSED`, `ROUND_TRIP_START_REUSED`, `EDGE_BANNED`, `TRANSITION_FORBIDDEN`, `STOP_SKIPPED`, `PAST_END`, `TURN_REFUSED`, `ROAD_AGAIN`), `index` is its first illegal edge, the path before it legal (**Drawing a route**) |
| 422 | `DRAWING_UNFINISHED` (+ `reached`) | `POST /draw/finish` on a legal path that has not reached the end; `reached` counts the stops it has |
| 429 | `TOO_MANY_JOBS` (+ `running`, `queued` when the client's own jobs are why) | every worker busy and the queue full (for `GET /daily` too, when the day's job is still to submit: **The daily route**; and for `GET /challenges`, when a date is to be made or one of its plans finds the server full: **The daily challenges**); on a public server also a client whose own jobs fill its share, one running and three waiting (**Public limits**): `running` and `queued` are its counts |
| 429 | `RATE_LIMITED` (+ `retry_after_s`) | a place search that finds the geocoder's queue full: five already queued for a turn (**Geocoding**); the message is "Place search is busy. Try again in a few seconds."; on a public server also a client's eleventh search within a minute, its sixty-first pick of a build (`POST /regions/pick`), its 241st drawing step (`POST /draw` and `POST /draw/finish`) or its eleventh finished drawing (`POST /draw/finish`) within a minute (**Public limits**) |
| 502 | `GEOCODER_ERROR` | Nominatim failed: an error status, a timeout, no connection, an answer that does not parse; or refused (429, 403), which pauses the geocoder 60 s, during which a search the cache cannot answer gets this at once ("the geocoder refused; paused for N s") |
| 503 | `GEOCODER_DISABLED` | a place search on a server whose geocoder is `none` |
| 404 | `NOT_FOUND` | an unknown (or expired) job, an unknown route (for a share image, also an id that is not 12 lowercase hex digits), an unknown path; no daily route on this server (its region has no places index, or no two of its towns fit a trip: **The daily route**); no challenges (the region has no places index: **The daily challenges**) |
| 405 | `METHOD_NOT_ALLOWED` | a method a path does not take |
| 409 | `DAILY_JOB` | `DELETE /jobs/{id}` on the daily route's job while it runs: the server's own, planning today's route for everyone (**The daily route**) |
| 409 | `BUILD_UNAVAILABLE` | a stored route whose build is no longer on disk at its `graph_version`, or has lost its geometry (`geometry/`, which drawing it needs) |
| any other | `HTTP_<status>` | any other HTTP error Starlette raises, its status in the code |
| 500 | `VALIDATION_FAILED` | `POST /draw/finish` on a drawing its checks passed and the validator refuses (a bug; logged, never stored) |
| 500 | `INTERNAL_ERROR` | an exception nothing else answered (a bug): the message is only "internal error", the traceback goes to the log with the request's id, and the answer carries `X-Request-ID` (**Logs**) |

The SSE `error` event uses the same body, with the codes above that a worker can meet, and:
`CANCELLED` (cancelled while queued or before its route existed), `VALIDATION_FAILED` (a route
the validator refused: an engine bug, never stored), `WORKER_FAILED` (an exception, a worker
that did not start, or one that died without a result, partway through a message included; for
a portfolio, an error ends the job only once every search has failed: **Portfolios**) and
`WORKER_TIMEOUT` (still running `API_KILL_GRACE_S` after its budget, with no route yet; a
reorder job still searching `REORDER_SEARCH_MAX_S` beyond that).

`POST /jobs` refuses before a job exists, in this order: the body (the target's bounds
included), a ratio target for a round trip, the region, the objective (and whether its item,
brand and target fit it, and the brand is the build's; a portfolio under the alphabet route or
with a base route), a certified route's settings, the worst order's stops (`TOO_MANY_STOPS_TO_REORDER`, against the region's
build), the budget, the portfolio (`PORTFOLIO_TOO_LARGE`), the job limit (429, a portfolio's
workers counted), the base route, each stop against the bbox, then the **pre-flight** (`jobs.prepare`): for a certified route its leash first
(`exact.certified_request`), the request's masks, a pilgrimage's brand within them,
`resolve_stops` (snapping and the re-snap fallback), and for a certified route its area against
the caps (`AREA_TOO_LARGE_TO_CERTIFY`), in a worker thread, and for a base route
`plan.check_base`. The worker repeats
them, deterministically, so a request that passes the pre-flight is not refused for its stops
later. A request a strict turn constraint refuses can cost several seconds of CPU there on a
state build, since every failing seed search looks again for a node-simple path
(`docs/reference/engine.md`, **Limitations**).

## Endpoints

| Method | Path | Answer |
|---|---|---|
| GET | `/health` | `{status: "ok", builds: [{region, build_id, graph_version}]}` |
| GET | `/ready` | `{status: "ready"\|"not_ready", checks: [{name, ok, detail}]}`: 200 when every check passes, else 503 (below) |
| GET | `/config` | `{public, basemap: {kind, tiles, glyphs, sprite, style}, limits: {budget_max_s, leash_max_km, portfolio_max}}` (**Basemap**) |
| GET, HEAD | `/basemap/{path}` | the files under the basemap's directory (`<WORST_ROUTE_DATA>/basemap/`, or `ApiSettings.basemap_dir`), with byte ranges (**Basemap**) |
| GET | `/regions` | `[{region, name, build_id, graph_version, bbox: [w, s, e, n], counts, tiers: [{tier, nodes, edges}]}]`, in the order served; `name` is what the frontend calls the region (`config.Region.display`: "Oregon", "United States"; the id for a region `config.REGIONS` does not name) |
| POST | `/regions/pick` | `{stops: [{lat, lon}]}` (one or more) → `{region, build_id, reason}`: the served region a trip with those stops runs on (**Picking a build**) |
| POST | `/snap` | `{region, lat, lon, role: start\|via\|end, tier?}` → `{node, lat, lon, moved_m, note}` |
| GET | `/brands` | `{region, brands: [{id, name, wikidata, locations}]}`, for `?region=` (below) |
| POST | `/jobs` | 202 `{job_id, events_url, seed}` |
| GET | `/jobs/{id}/events` | the job's events (**Events**); `Last-Event-ID` resumes |
| GET | `/jobs/{id}` | the job's status with its route's current snapshot (polling) |
| DELETE | `/jobs/{id}` | cancels it (**Jobs**); answers its status, without the snapshot; the daily route's job is refused (409 `DAILY_JOB`) |
| GET | `/routes/{id}` | the route document, with its directions, toasts and marks |
| GET | `/routes/{id}/directions` | `{route_id, directions: [{step, text, distance_m, time_s, edge_range, kind}]}`: the document's directions alone |
| GET | `/routes/{id}/geojson` | the FeatureCollection `presentation.geojson.feature_collection` makes: the route, the baseline (null for a round trip) and the stops |
| POST | `/routes/{id}/validate` | `{route_id, graph_version, ok, checks: [{name, ok, index, detail}]}`, the stops re-derived from the stored request (by this engine version only); an alphabet route's checks end with `alphabet` |
| GET | `/geocode` | `{query, region, candidates: [{label, lat, lon, category}], cached}`, for `?q=&region=` (**Geocoding**) |
| GET, HEAD | `/routes/{id}/share.png` | the route's share image, a 1200 × 630 PNG (`image/png`), cached; `?theme=<id>` in that theme's colours, classic's without; HEAD the same headers without it (**Share image**) |
| GET | `/daily` | `{date, region, build_id, stops: [{lat, lon, label}], seed, max_iterations, status, route_id, events_url, ratio, stopped_because, attempts}`: today's route, planned on the day's first call (**The daily route**) |
| GET | `/challenges` | `{date, region, build_id, challenges: [{id, type, title, blurb, setup, goal}]}`, for `?date=` (today by default): the day's challenges, made on its first call (**The daily challenges**) |
| POST | `/draw` | `{request, path}` → `{stops, at, reached, legal, length_m, baseline_length_m, ratio, geometry, next, done}`: a route drawn by hand so far, with its legal next moves (**Drawing a route**) |
| POST | `/draw/finish` | `{request, path}` → `{route_id, ratio}`: the finished drawing, validated and stored (**Drawing a route**) |

**`/ready`** says whether the deployment is whole, where `/health` says only that the process
lives: `build`, each served build still on disk at the `graph_version` it loaded (a build
replaced since, or gone, fails: restart to serve the new one); `store`, the route store
answering a query; `places`, a places index for each served region, its own loaded at startup
or a served region's that covers it (**Running it**; the detail says which: `oregon: 534 towns;
us-t1: 31540 towns`, or `westland: lands's index`; a region with neither fails, the check naming
`worst-route aux places --region R`); `basemap`, with the `pmtiles` basemap its file (missing,
the check names `tools/fetch_basemap.sh NAME`, the basemap's name; its detail says whether the labels' fonts
and icons are there too, which are optional), with `raster` nothing. A check that fails makes
the answer 503 with `status: "not_ready"`; each check's `detail` says what it found, or what is
missing and its remedy, never a path on the server or an error of its system (those go to the
log, as warnings of `worst_route.api.ready_api`). Its use is a container's health check and a
deploy's last step (`docs/reference/hosting.md`).

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
             and the base route's stops, tier and leash; with reorder, its stops and their
             labels in the order the route visits them; for a portfolio, its first seed and
             portfolio, the document's seed being the winner's),
 stops:      [{label, node, lat, lon, moved_m, note, route_index}],
 order:      [each stop's position among those asked for] (the stops, like the request's,
             in the order the route visits them; the identity without reorder, and for a
             route stored before the worst order),
 geometry:   LineString along every edge's shape in the build's geometry,
 legs:       [{from_stop, to_stop, node_range: [a, b]}],
 metrics:    {length_m, baseline_length_m, ratio, time_s, baseline_time_s, baseline_delay_s,
              time_ratio, departure, arrival, target, delay_s,
              turns: {left, right, straight, u_turn},
              items: {toll_booth, level_crossing, roundabout, bridge},
              brand: {id, name, visits, of},
              alphabet: {letters, sequence, roads, alpha_from, free_from},
              extra_fuel_gal, extra_cost_usd, states, towns, towns_n, network_fraction},
 run:        {attempts, accepted, stopped_because, elapsed_s, backend,
              portfolio: {k, seeds, winner},
              certified: {status, bound_m, gap, nodes, edges, leash_m, solve_s, workers}},
 validation: {ok: true, checked_at},
 directions: [{step, text, distance_m, time_s, edge_range: [a, b], kind}],
 toasts:     [text],
 marks:      [{kind, lat, lon, label}]}
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
asked for (`objectives.brand.brand_counts`), null for every other objective; `alphabet` is an
alphabet route's letters: how many (`letters`, equal letters in a row once), in order
(`sequence`), the first road of each (`roads`), and the edge positions where its alphabet part
starts (`alpha_from`, its first A road's first edge: before it, the free first leg) and where
its free last leg starts (`free_from`), recounted on the stored route up to its stored
`free_from` when the document is asked for (`alphabet_search.alphabet_counts`, on the build's
edge letters, worked out once per build and kept by `graph_version`), null for every other
objective;
`stopped_because` is `target` (within it),
`budget`, `converged`, `cancelled` or `timeout`, `drawn` for a route drawn by hand, whose
`backend` is `human` (**Drawing a route**), and `optimal` for a certified route the solver
proved. `run.portfolio` is a portfolio's (**Portfolios**),
absent for one search: how many searches, their seeds, and `winner`, the index of the one whose
route it is, so `seeds[winner]` is the document's `seed` and `run.attempts` and `accepted` are
that search's own; the document's `request.seed` is the first of the seeds. `run.certified` is a
certified route's certificate, absent for any other (**Certified routes**): `status`, `optimal`
when the solver proved no legal route in the area is longer, else `feasible`; `bound_m`, the
longest any legal route in the area can be by the solver's bound (the route's length when
proven), and `gap`, the fraction by which it may exceed the route (0 when proven), both null
when the solver ended without a route of its own (the time limit before it took in the seed:
the route is then the seed, its `stopped_because` `budget`) or, for a cancelled job, had sent
no bound yet, or when the solver's last bound lay below its route's value (a guard: CP-SAT's
bound never should); the area's `nodes` and `edges`, the `leash_m` used, the solve's
seconds (`solve_s`) and its threads (`workers`). A certified route's `run.attempts` counts the
solutions the solver reported, `accepted` the better routes it streamed.

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
- `toasts`, at most ten one-line facts about the route, the most striking first.
- `extra_fuel_gal` and `extra_cost_usd`, the length beyond the baseline's at the request's
  `vehicle`'s `mpg` and `gas_price_usd`, null without a vehicle; `states` and `towns`, the
  Census names of the states and towns the route passes through in the order it enters them,
  and `towns_n`, null when the route's region has no places index, its own or a served one that
  covers it (**Running it**); and
  `network_fraction`, the share of the road units within the request's reach (its leash, tier
  and bubbles) that the route drives.

The document has no comparison line: the share image has it.

`marks` are the places a pilgrimage or a collecting route counts, for the map
(`presentation.marks`, `docs/reference/presentation.md`, **Marks**), in the order the route meets
them, one a place: a pilgrimage's each of its brand's locations it visits (`kind: "brand"`, at
the node, `label` the brand's name), as many as `metrics.brand.visits`; a collecting route's
places of its item (`bridge`, `toll_booth`, `roundabout`, `level_crossing`, `label` null): each
bridge it crosses (a run of bridge edges, as `metrics.items` counts them, at the middle of the
run), as many as `metrics.items.bridge`, each edge holding toll booths or level crossings (at
the middle of its shape) and each super-junction holding rings (at the node), where `items`
counts every booth, crossing and ring, so a toll plaza of several booths is one mark. Every other
route's list is empty. They are worked out from the stored route on every request, as `items`
and `brand` are, never stored, so a route stored before them has them too.

## Picking a build

**`POST /regions/pick`** names the served region a trip runs on (`api/pick_api.py`), so a server
of several builds (**Running it**) plans each trip on the smallest that holds it: a trip within
Oregon on Oregon's all-roads build, Portland → Chicago on the national one. The body is the
trip's stops, one or more, as `POST /jobs` takes them (`{stops: [{lat, lon}, ...]}`, a `label`
allowed and ignored); the answer:

```
{"region": "oregon", "build_id": "ea3b18096e8b", "reason": "all stops in Oregon"}
```

Of the served regions whose bbox holds every stop (the bbox `POST /jobs` checks a stop against:
the build's `params.bbox`, else its nodes' extent, worked out once a build: `Loaded.bbox`), in
order, where a stop's state is the one whose Census polygon holds it (a touch counts; on a border
the first by FIPS code), from any places index the server has loaded (`PlaceIndex.states_at`;
they all hold the same states):

1. a region clipped to a bbox (`config.Region.bbox`, as portland is), the smallest bbox first:
   "all stops within Portland". With a places index loaded it must also name every stop's state
   among its states (`config.Region.states`): Portland → Vancouver, WA, both in portland's bbox,
   goes to a region holding Washington, not onto Oregon's roads near the river. Without an
   index, its bbox alone decides, and so it does for a region `config.REGIONS` does not name;
2. a region of whole states whose states hold every stop's state, the fewest states first: "all
   stops in Oregon", "stops in Oregon and Illinois" (two states, in the order the stops first
   enter them), "stops in 3 states". When a served region of fewer states holds the stops' states
   but its bbox misses a stop (Oregon's build ends short of Oregon's line in places), the reason
   says so: "all stops in Oregon, one beyond Oregon's build";
3. else the one with the most states (the national one): "a stop in no state" when one lies in
   none (at sea, abroad), "no places index: by bounding box" when the server has loaded no places
   index, else, when no region served holds the stops' states, "stops in Oregon and Washington,
   by bounding box".

A tie goes to the region served first. No region's bbox holding every stop is 422
`STOP_OUTSIDE_REGION`, `stop_index` the first stop outside the region whose bbox holds the most of
them (the first served on a tie), with its `lat` and `lon`: for a stop no served region holds,
the message `POST /jobs` gives (**Every error**: "That stop is outside the map. Try somewhere in
the lower 48." on a server with a national build, else the served regions by name); when each
stop lies in some region and none holds them all, "These stops are on different maps. Keep them
on one map." A body without a stop is 422
`INVALID_REQUEST` (`field: "stops"`); a public server answers a client's sixty-first pick in a
minute `RATE_LIMITED` (**Public limits**). The pick is the server's; `POST /jobs` still names its
region, and the frontend sends the one picked (`docs/reference/frontend.md`, **Using it**).

## Events

`GET /jobs/{id}/events` is a `text/event-stream` (sse-starlette, a comment ping every 15 s).
Each event has an `id`, counting up per job, an `event` name and a JSON `data`:

| Event | When | `data` |
|---|---|---|
| `snapped` | at the job's creation (from the pre-flight); with `reorder`, once the worker has chosen the order, just before `baseline` | `{stops: [{input: {lat, lon}, label, node, lat, lon, moved_m, note}], order, tier, leash_km, active_nodes}`: the stops in the order the route visits them, `order` each one's position among those given (`[0, 1, 3, 2, 4]`; the identity without `reorder`), the tier and leash those of that order |
| `baseline` | when the worker has built the route the loop starts from | `{length_m, time_s, geometry}` (null for a round trip) |
| `snapshot` | right after `baseline` (iteration 0: the seed, or the base route), after every 50th `progress`, on every better alphabet or certified route (just before its `progress`), when another of a portfolio's searches takes the lead (its route, just before its `progress`; **Portfolios**), just before `done` when the stored route is not the one the splices drew (**Jobs**), and first on every (re)connect once the route exists | `{iteration, elapsed_s, node_count, stop_idx, geometry, node_index, metrics}` |
| `progress` | at most every 0.1 s while splices are accepted; for the alphabet route and a certified route, once per better route, right after its `snapshot`, with no splice, and for a certified route, between better routes, one with no splice when the solver's bound moves, at most every second; for a portfolio, the leader's splices only, and when another search takes the lead, once right after its `snapshot`, with no splice, and while its leader has finished and others still search, one with no splice every second | `{iteration, elapsed_s, score, metrics: {length_m, time_s, delay_s, ratio, item, items, brand: {id, name, visits, of}, alphabet: {letters, sequence, roads, alpha_from, free_from}}, splices: [{i, j, node_count, coords, node_index}], best_score, best_metrics: {length_m, time_s, delay_s, ratio, item, items, brand: {id, name, visits, of}, alphabet: {letters, sequence, roads, alpha_from, free_from}}, portfolio: {k, leader, best_ratios}, certified: {bound_m, gap}}`: a portfolio's numbers its leader's, `portfolio` only for a portfolio (**Portfolios**); `certified` only for a certified route, the solver's latest bound on the longest legal route in the area and the fraction by which it may exceed the route, both null on a better route whose bound lay below its own value (a guard: CP-SAT's bound never should) (`iteration` counts the solutions it has reported; a bound's `elapsed_s` is when the solver reached it; **Certified routes**); `delay_s` the waiting at the route's signals and stop signs so far, as in a `snapshot`'s metrics and the job status's; `item` and `items` only for `collect`, `brand` (its visits as the route grows) only for `brand`, `alphabet` (its letters) only for `alphabet`, whose `iteration` counts restarts; `best_score` and `best_metrics` the best route's so far by the request's goal (the highest score for max, the closest to a target), which is the one stored: a max run that anneals (engine version 6, `docs/reference/engine.md`, **The loop**) takes shorter splices too, so `score` and `metrics`, the route the splices draw, may be below them |
| `done` | the route is stored (or was already: its existing id, **The store**) | `{route_id, stopped_because, metrics}`: the stored route's `{length_m, time_s, ratio, time_ratio, target, delay_s, turns: {left, right, straight, u_turn}, items: {toll_booth, level_crossing, roundabout, bridge}, item, brand: {id, name, visits, of}, alphabet: {letters, sequence, roads, alpha_from, free_from}}`, as in the document (`target` null for `max`; `item` only for `collect`, `brand` only for `brand`, `alphabet` only for `alphabet`) |
| `error` | the job ended without a route | `{code, message, ...}` |

The stream ends after `done` or `error`. A browser's `EventSource` reconnects when a stream
ends, so the client closes it on either. A client subscribes when its stream starts running,
not when the request arrives, and unsubscribes when it ends, so a client gone before its stream
starts leaves no subscriber behind (which would keep the job from ever being an orphan).

**Applying the stream.** A route is drawn as `[lon, lat]` points along its edges' shapes in the
build's geometry (`docs/reference/graph.md`, **Geometry**: simplified to 2 m at `save` and
stored to the metre, so a drawn line lies within 2.71 m of the road; the API process reads it,
never a job's worker); consecutive edges share their junction point, a node's position exactly,
kept once, and `node_index[k]` is the index of route node k among the points. The route
document, its GeoJSON and the share image draw the same points. A `snapshot` replaces the client's points and indices.
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
17,138 (format 3, whose shapes were unsimplified float32 lon/lat; with format 4's geometry the
document's lines have about a third of the points: 32,131 where format 3 had 87,551 over the
twenty acceptance routes at 2,000 attempts); splices are not simplified.

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
starts its worker (a portfolio's workers, one a search), or queues it (FIFO) when the workers it
needs are not free: at most `API_MAX_JOBS` workers run at once, a running job holding one for
each of its searches that has not ended, and a job waiting for workers holds up the jobs queued
after it, so none starves (one waiting only for its own client's running job, on a public server,
does not); with `API_MAX_QUEUED` (8) waiting too, `TOO_MANY_JOBS`.

A job with `reorder` emits no `snapped` at its creation: the pre-flight checked its stops in the
given order, and the worker's `plan_route` chooses the order. Its `Started` message carries the
order and the stops resolved in it; the manager then plans as the worker does (the job's request
takes its stops in that order, its masks are worked out again for it in a thread, its stops are
the worker's, and the stops of its API request are put in that order, labels with them, so the
stored route's document, labels and directions follow the route) and emits `snapped` with the
order, before `baseline`. A cancel during the search ends the job in `error` `CANCELLED`, as for
any job whose route does not exist yet; one after it stores the route so far, validated against
the chosen order's masks and stops. The search does not eat into the job's time: until its
`Started`, a reorder job's deadline is `REORDER_SEARCH_MAX_S` (60 s) beyond the usual one
(`budget_s` plus `API_KILL_GRACE_S`, 30 s, after its start), and from `Started` it is `budget_s`
plus `API_KILL_GRACE_S` again, counted from then. A search that outlasts its allowance ends the
job as `WORKER_TIMEOUT`, with no route, as any job whose route does not exist by its deadline.
The caps on its stops keep the search to seconds (12–14 s for 7 stops on Portland's build at a
load of 7–12), but beside another job and a browser rendering the map it took over 30 s.

**Portfolios.** A job with `portfolio` K above 1 runs K searches of its request, seeds `seed`
to `seed + K − 1` (`Search`: each with its own worker, its own copy of the route and its best so
far by the goal), and its clients see one route, its leader's. The first search to start leads.
After each batch of messages, a search whose best route so far is strictly better by the goal
takes the lead (the first of the best among several; a tie keeps the leader), once the leader
has led `API_LEADER_HOLD_S` (2 s), or at once when the leader has failed (the hold never keeps a
failed leader: the first search to have a route after it takes the lead): its route is sent as a
`snapshot`, then its numbers as a `progress` with no splice. Only the leader's splices are ever
sent, and an old leader's not sent yet are dropped, the snapshot replacing them, so a client
applying the stream as for one search draws the leader. The hold keeps a long route's snapshot
(3.5 MB of JSON and a quarter of a second of the API's time for a 33,710-node route on `us-t3`)
from going out several times a second while two searches run neck and neck. A portfolio's
`progress` events carry `portfolio: {k, leader, best_ratios}`: how many searches, the leader's
index (its seed is the job's `seed` plus it) and each search's best route so far as the ratio a
`progress` event's `metrics.ratio` gives (null with no route yet, after a failure, or for a round
trip). While the leader has finished and other searches still run behind it, a `progress` with no
splice comes every `API_PORTFOLIO_TICK_S` (1 s), the leader's last numbers with the job's elapsed
time and the `portfolio` block, so the stat line and the ratios keep moving. The searches share the job's baseline and, but for a round trip without legs, their seed
route, which no seed changes; so the first `snapshot` is the same whichever starts first.

A search that fails (its worker's `Failed`, or a worker gone) drops out, logged with its seed:
it no longer leads, and its route counts for nothing; the job goes on with the others, and ends
in the error of the first search's failure (by index) only when every search has failed. Each
search's worker is free for another job as soon as its search ends. The job is complete once
every search has ended, or as soon as one reaches its target (`stopped_because: "target"`), the
others then stopped: the best route the searches finished with, by the goal (the first of the
best), is stored, its document its search's own (its seed, its engine request's seed, its
attempts and accepted splices) with `run.portfolio = {k, seeds, winner}`, and sent as a
`snapshot` before `done` when it is not the route the clients have drawn, whatever the hold. A
portfolio's route therefore reproduces from the winner's seed: `worst-route plan --seed
<winner's seed>` with the same request plans it (with `--max-iterations` its `run.attempts` when
time ended its run, as for any route), and so does a job of that seed alone. Which
search wins is deterministic when every search ends on its iterations or by converging; when a
target or the budget ends them, which seed wins the race depends on timing, each winner still
reproducing from its own seed. A cancel or the time limit stores the best route so far of the
search ahead (**Ending early**). With `reorder` each search's worker runs the same search for
the order, which does not depend on the seed (`docs/reference/engine.md`, **Planning**): the
manager reorders the job on the first `Started`, and a later one whose order or stops differ is a
bug that ends the job in `WORKER_FAILED`. The job status (`GET /jobs/{id}`) gives the leader's
numbers and the job's `seed`, the first.

**The worker** (`worker.run_in_process`, around `worker.run_job`) runs in a `multiprocessing`
process started with `spawn`. It loads the build memory-mapped (`graph/loader.py`, so workers
share the OS page cache), calls `plan_route` with its `on_start` and `on_progress` hooks (and
`base` to continue a route), and sends small picklable messages down a one-way pipe
(`Pipe(duplex=False)`; the API process closes its copy of the write end once the worker has
started, so the worker holds the only one): `Started` (the route the loop starts from, the
baseline, and the order of the stops with the stops resolved in it), `Spliced` per accepted splice (for the alphabet route, `Improved` per better route
instead: the whole route, its restart count, score and metrics), then `Finished` (the validated
route as its `route.json` document, with its score and metrics) or `Failed`. A send blocks while the pipe is full, so a slow reader slows
the search but never loses a message. On start and at every accepted splice the worker checks
that the API process is alive (`multiprocessing.parent_process()`); when it is not, or the pipe
has no reader left, it closes its end and exits (`os._exit`), so a crashed API never leaves
workers searching. Spawn re-imports the parent's main script, so under `worst-route serve` a
worker loads `worst_route.cli` (typer, pydantic-settings, `pipeline.layout`) besides the engine
and the graph package (shapely); never FastAPI, uvicorn or sse-starlette, and no numba on
the python backend. Its own imports take about 0.4 s; on the csr backend, the default, loading
numba (and scipy, which numba imports when it is installed, as it is outside the desktop app)
and the cached kernels adds about 0.3 s.

**The follower**, an asyncio task per running job, reads its workers' pipes, each in a thread
(`poll` for at most `API_READ_GRACE_S`, 0.1 s, then `recv`), and keeps each search's own copy of the
route: the starting route, then each splice applied in order, as a client would. It keeps the best
route so far apart, by the request's goal (`engine.target.Goal`, on each splice's score and
metrics): in a max mode the highest score, which is the copy until the run converges and anneals
(engine version 6: its reheats take shorter splices too, `docs/reference/engine.md`, **Annealing**)
and may be an earlier route after; a target run's best is the route closest to its target, which
after an overshoot that shrinking could not bring back is an earlier one
(`docs/reference/engine.md`, **Targets**). It coalesces splices into `progress` events
(`API_COALESCE_S`, 0.1 s), each with the best route's score and metrics (`best_score`,
`best_metrics`), with a `snapshot` every `API_SNAPSHOT_EVERY` (50).
An alphabet route's `Improved` replaces the copy and the best at once, sent as a `snapshot` and
then a `progress` with its numbers and no splice: its better routes are whole new routes, not
splices of the last. So does a certified route's, which carries its stops' positions and the
solver's bound and gap; its `Bounded`, between better routes, is a `progress` with no splice
(**Certified routes**). On `Finished` the route (the engine's best) is stored and `done` sent; on `Failed`, `error`.
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
  A portfolio's workers are all stopped, and the route stored is the best route so far of the
  search ahead (the leader, unless another's best is strictly better; never one that failed),
  with that search's seed, attempts and accepted splices and `run.portfolio`, sent as a
  `snapshot` before `done` when the clients have drawn another. A certified route's is its last
  better route, with `run.certified` `feasible`, the last bound and gap its worker sent (null
  when it sent none) and the pre-flight's area and leash, its `solve_s`, as `run.elapsed_s`, the
  time of the last better route or bound.
- **Time limit**: a job still running `API_KILL_GRACE_S` (30 s) after its budget is ended the
  same way, as `"timeout"` (`WORKER_TIMEOUT` before its route exists); for a reorder job the
  budget and the grace run from the end of its search, which may take `REORDER_SEARCH_MAX_S`
  (60 s) beyond them (**Jobs**).
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

## Certified routes

A request with `certified` (**Requests and errors**) is planned by the certified driver
(`docs/reference/engine.md`, **Certified worst**): CP-SAT over the request's area, from the seed
route, within the budget. The pre-flight chooses its leash (with `auto`, the widest whose area
holds at most 300 nodes and 600 edges) and refuses an area over 300 nodes or 1,000 edges
(`AREA_TOO_LARGE_TO_CERTIFY`, on `field: "leash"`); the job's request carries the leash as a
number, so the worker plans the same area and the stored request (the engine's, `request_json`)
re-derives it, while the document's `request` keeps what was asked. The worker sends each better
route the solver finds whole, with its stops' positions and the solver's bound and gap
(`Improved`), and between them the bound, at most once a second (`Bounded`), whose delivery is
where a cancelled job or a dead API process is noticed while the solver proves; each becomes a
`snapshot` and a `progress`, or a `progress` alone, every `progress` of the job carrying
`certified: {bound_m, gap}`, the latest. The solver runs 4 threads in the job's one worker (a
public server counts it as one job, as any other), and stops half a second before the budget;
the job ends `done` with `stopped_because: "optimal"` when proven, else `budget`, its document's
`run.certified` the certificate. A certified route is not continued by a certified request (no
base route); a plain continuation of one ("make it worse") is planned by the loop within the
stored leash, which a proven route's length bounds (the frontend offers none: its Make it worse
is disabled on a certified route).

## Make it worse

`POST /jobs` with `base_route_id` continues a stored route: the worker passes it to
`plan_route(base=...)`, which starts the loop from it instead of the seed, with the new
request's seed, budget and target: with a target below the base route's value the run shrinks
toward it, above it grows, and within it ends at once with the base route (`"target"`). The
stops, tier and leash come from the base route's request; given ones must match
(`BASE_ROUTE_MISMATCH`), the stops by their lat/lon. A base route planned with `reorder` is
continued in the order it visits its stops (its request's, with their labels), and `reorder` is
off whatever the body says: the continuation's `order` is the identity, and stops given must be
the base's in that order. The base route must have
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
       baseline_edges (blob), metrics_json, run_json, order_json (route.json's order))
```

A portfolio's route keeps its winner's document, `run.portfolio` in its `run_json`, and its API
request as asked (the first seed and `portfolio`). A route planned with `reorder` keeps its API
request with the stops in the order the route visits them, as its engine request and its stops are; `order_json` keeps how they map to the stops asked
for. A store opened by a server from before the worst order gains `order_json` when it opens
(`ALTER TABLE`, the check and the change under one write lock, `BEGIN IMMEDIATE`, so servers
opening it at once add it once; a column found added all the same counts as there); its rows, and
a document without `order`, read as the identity. The change is one way: a server from before
the series stores a row by its 16 columns' positions, which the store then refuses ("table routes
has 17 columns but 16 values were supplied"). To go back, drop the column (`ALTER TABLE routes
DROP COLUMN order_json`, SQLite 3.35 and later) or start a fresh store file; the desktop app has a
store of its own, and a public server runs one version at a time.

`route_id` is the first 12 hex digits of a sha256 over the `graph_version`, the node and edge ids
(int32 little-endian) and the stop positions (JSON): the id names the route's content, not the
request that found it. One route on one build therefore has one id whichever run found it, and
the row stored first is kept. A request that produces a route already stored (another seed that
lands on it, a rerun, a "make it worse" that accepts no splice) gets the existing id in its
`done`, and that id's document is the first record's: its request, labels, departure, seed and
run. `get` returns a `StoredRoute`, which the validator reads like a `Route`.

The daily route's rows are a second table of the same file, one a day, region and
`graph_version` (**The daily route**):

```
daily(date (UTC, YYYY-MM-DD), region, build_id, graph_version, route_id, status, job_id)
```

`status` is `planning` (`job_id` its job), `done` (`route_id` its route) or `failed`;
`RouteStore.daily(date, region, graph_version)` reads a row (a `DailyRow`) and `put_daily(row)`
replaces the day's. A store opened by an older server gains the table when it opens.

The daily challenges are a third (**The daily challenges**), one row a date, region and
`graph_version` once that day's are made:

```
challenges(date, region, graph_version, build_id, challenges_json)
```

`challenges_json` is the seven as `GET /challenges` answers them, today's route made worse left
out; `RouteStore.challenges(date, region, graph_version)` reads it and `put_challenges` replaces
it. A store opened by an older server gains the table when it opens, and an older server never
reads it. Each challenge's plan is a route of the first table like any other.

## The daily route

**`GET /daily`** answers today's route: one trip a day, the same for everyone on a server, planned
once on the server's main build (`api/daily_api.py`):

```
{"date": "2026-10-31", "region": "us-t3", "build_id": "281a455e3d15",
 "stops": [{"lat": 42.567243, "lon": -90.717431, "label": "Sageville, IA"},
           {"lat": 34.053245, "lon": -84.663677, "label": "Acworth, GA"}],
 "seed": 20261031, "max_iterations": 5000, "status": "done",
 "route_id": "a076702bbc25", "events_url": null, "ratio": 6.581601563148372,
 "stopped_because": "budget", "attempts": 5000}
```

- **The day** is the UTC date (`date`), its seed the date as a number, YYYYMMDD (`seed`).
- **The region** is the served one with the most states (`config.Region.states`: the national
  build when it is served), else the first served, a tie going to the first served.
- **The towns** come from that region's places index (its own, else a served one covering it, as
  a route's do: **Running it**; `PlaceIndex.town_points`, each town at a point inside it,
  `docs/reference/presentation.md`, **Places**): the incorporated places (`DAILY_LSAD`: LSAD 25,
  a city, 43, a town, or 47, a village; not a CDP or a borough), their points at 6 decimals, inside
  the build's bbox (the bbox `POST /jobs` checks a stop against), each with a node within 5 km
  (`DAILY_NEAR_KM`) that can play its stop's role (`SpatialIndex.snap` with no mask: a node the
  start can leave by, one the end can arrive by), labelled with the name and the state's code
  ("Handley, WV"). One `numpy.random.Generator(PCG64(seed))` draws a pair of them (two uniformly,
  start first) again and again, at most `DAILY_DRAWS` (10,000) times, and keeps each pair whose
  straight-line distance in the build's CRS lies within `DAILY_BAND` (15 % to 45 %) of the
  diagonal of the build's bbox; the trip is the first pair kept that the request's pre-flight
  accepts (`jobs.prepare`, as `POST /jobs` runs it), at most `DAILY_TRIES` (10) tried, so the
  day's route is the one a user planning that trip at the automatic tier would get. On `us-t3`
  the diagonal is 5,661 km (EPSG:5070), so a trip is 849 to 2,547 km in a straight line (the band
  reached 60 % at first, 3,397 km, whose longest trips could not make their attempts within the
  public budget: **Measured**). The pre-flight is what makes a pair plannable: beyond 800 km the
  automatic tier is the primary roads and every road within 3 km of a stop
  (`docs/reference/engine.md`, **Requests**), and a small town whose roads meet no primary road
  within that is refused (`STOP_UNREACHABLE_WITHOUT_REPEAT`; a pre-flight takes 0.0–1.3 s).
  Of the first pairs of the 30 days from 2026-10-31 the pre-flight accepted 17, and every one of
  the 30 days had a pair it accepted among its first ten (1,008–2,267 km in a straight line); on
  2026-10-31, Handley, WV → Falls City, NE was refused and Sageville, IA → Acworth, GA, the next
  pair, planned.
- **The request** is the longest route as bad as possible: `{region, stops, objective:
  "distance", target: {kind: "max"}, tier: "auto", leash_km: "auto", seed, max_iterations:
  DAILY_MAX_ITERATIONS, budget_s, vehicle, departure}`, the budget the server's largest
  (`Limits.budget_max_s`: 120 s, 60 s on a public server), the vehicle the frontend's default
  (`DAILY_MPG`, 24 mpg, and `DAILY_GAS_PRICE_USD`, $3.89 a gallon), the departure the moment the
  day's job is submitted, UTC to the second, as the frontend's empty departure is the moment Go is
  pressed: so the route's document has its extra fuel and cost and its arrival. Neither enters
  the engine's request, so neither changes the route; a second server's row has its own
  departure.
- **Determinism.** The same date, build (`graph_version`), places index and `DAILY_*` settings
  draw the same towns on any server, and the engine's rule (`docs/reference/engine.md`, **The
  loop**, **Determinism**) makes the route the same when the iterations end the run (or it
  converges, or reaches a target) within the budget: the same route id on every such server. The
  budget is a safety net: a run the clock ends is a prefix of that sequence, which differs from
  one machine to another. The answer says which ended it: `stopped_because` from the route's run,
  `budget` with `attempts` equal to `max_iterations` for the iterations, fewer for the clock.
- **Planning.** The day's first call draws the trip and submits its job through the job manager with
  no client, so it takes one of the server's slots (`API_MAX_JOBS` running, `API_MAX_QUEUED`
  waiting) and never a client's share on a public server. A server whose slots and queue are full
  answers 429 `TOO_MANY_JOBS` before anything is drawn ("The server is busy. Try again in a
  minute."), and records nothing. The trip is drawn, and its
  pre-flights run, once a date, region and `graph_version` (`Api.daily_plans`, the request and the
  pre-flight's result kept in the process, another date's dropped): planning the day again only
  submits its job again. The calls of a day take a lock per date and region (`Api.daily_locks`)
  while they read and change its row, so concurrent first calls plan once. The server subscribes to
  the job itself and follows it to its end (`Api.daily_tasks`), so the job is never an orphan
  (**Jobs**) however the clients watching it come and go, and no client cancels it: while it runs
  its id is in `Api.daily_jobs`, and `DELETE /jobs/{id}` answers 409 `DAILY_JOB` (once it has ended,
  the `DELETE` only reports it, as for any ended job). It records how the job ended: `done` with its
  route, or `failed` for a job that ends without the day's route (an error, the time limit, a cancel
  from within the server). It records nothing over a row that has moved on, and a call that finds
  the job ended first records it itself.
- **The answer** by the row: `planning`, with the job's `events_url` (the stream of
  **Events**, which the panel follows: `docs/reference/frontend.md`, **Today's route**) and the
  stops of its request; `done`, with the stored route's `route_id`, `ratio`, `stopped_because` and
  `attempts` (`GET /routes/{id}` and its share image are the route's own); `failed`, with no
  route and no stream: for a job that failed (a worker that failed, the time limit), the next call
  submits it again; a day whose first ten trips the pre-flight refuses stays failed, answered
  from its drawn plan. A planning row whose job the server does not know (it has restarted
  since) is planned again. The other fields are null where the status has none. A row is its
  build's at its `graph_version`: a new build, or one `pipeline save --force` replaced under the
  same id (another `graph_version`, on which the old row's route would be `BUILD_UNAVAILABLE`),
  makes a new row for the same date, and the old one stays.
- **No daily route** (404 `NOT_FOUND`): the region has no places index, or no two of its towns
  fit a trip.

After the day's first call, `GET /daily` reads its row, and a done one's stored route, from
sqlite under the lock; a failed job's day submits its drawn request again, and a full server
answers 429 at once: no call draws or runs a pre-flight but the day's first on a build (0.4–0.7 s
on `us-t3`; up to about 13 s if the pre-flight refused all ten trips). There is no quota of its
own.

`DAILY_MAX_ITERATIONS` is 5,000 (**Measured**): on `us-t3` the plans of the seven days from
2026-10-31, at the automatic tier on the dev machine at load averages of 6–14, made their 5,000
attempts in 8.6–38.9 s at 4.29–10.05× (baselines of 1,273–2,245 km), well under the public budget
of 60 s. 20,000 attempts took 70–83 s on long trips (9.53–10.99×), over it. The band's longest
trips come close to the public budget on a busy machine: of the 30 days from 2026-10-31, the four
longest, planned with the public budget at load averages of 10–14, made their 5,000 attempts in
23.2 s (Wheatland, IA → Hansen, ID, 7.32×), 35.9 s (Cleveland, NY → Clayton, LA, 11.00×) and
58.1 s (Vista, MO → Franklin, NH, 8.90×, which took 27.3 s at load 6–8), and the longest, Jasper,
TX → Hagerman, ID (2,267 km in a straight line, a 2,794 km baseline), made 4,613 when the clock
ended it at 60 s (4.85×): that day, on a server as busy, is the server's own. Under the first
band, 60 %, Goldendale, WA → Brandonville, WV (3,394 km in a straight line) took 73 s for its
first 2,500 attempts.

**A later option: all roads.** Planned at T4 (all of the build's roads within the leash) instead
of the automatic tier, the pre-flight accepted all 30 first pairs and the routes were 17.2–31.3×
where the automatic tier's were 4–11× on the same trips (20,000 attempts or 120 s), but each
attempt took 1.7–3.4 times as long (the first 2,500 attempts in 13.8–61.5 s), so a day would need
about 1,000–2,000 attempts to end within the public budget, and the day's route would no longer be
the one a user planning the trip at the automatic tier gets.

## The daily challenges

**`GET /challenges`** answers the day's challenges: eight a day, the same on every server with the
main build, seven of them drawn from the date and the eighth today's route made worse
(`api/challenges_api.py`). `?date=YYYY-MM-DD` asks for another day's, today's (the UTC date, as for
**The daily route**) by default:

```
{"date": "2026-11-02", "region": "us-t3", "build_id": "6adae6917be1",
 "challenges": [
  {"id": "long-way-2026-11-02", "type": "long-way",
   "title": "Losantville, IN to Wallace, IN at 15×",
   "blurb": "Set it up fills in the two towns and the longest route; press Go and finish at 15× the sensible drive or worse.",
   "setup": {"stops": [{"lat": 40.02301, "lon": -85.183899, "label": "Losantville, IN"},
                       {"lat": 39.98733, "lon": -87.147376, "label": "Wallace, IN"}],
             "objective": "distance", "item": null, "brand": null,
             "constraints": {"no_left": false, "no_right": false, "strict": false, "hard": false},
             "reorder": false, "target": {"kind": "max"}},
   "goal": {"kind": "ratio", "min": 15.0}},
  {"id": "slow-way-2026-11-02", "type": "slow-way", "title": "Leawood, MO to Crowder, OK in 50 h",
   "setup": {"objective": "time", ...}, "goal": {"kind": "duration", "min_s": 180000}, ...},
  {"id": "no-left-2026-11-02",
   "title": "Olive Hill, KY to Rising Sun, IN with no left turns at 2×",
   "setup": {"constraints": {"no_left": true, "strict": true, ...}, ...},
   "goal": {"kind": "no_left", "min_ratio": 2.0}, ...},
  {"id": "bridges-2026-11-02", "title": "Cross 600 bridges between Hanover, OH and Milford, OH",
   "setup": {"objective": "collect", "item": "bridge", ...},
   "goal": {"kind": "items", "item": "bridge", "min": 600}, ...},
  {"id": "pilgrimage-2026-11-02",
   "title": "Visit 11 Dollar General between Glenfield, ND and Webster, SD",
   "setup": {"objective": "brand", "brand": {"id": 858, "name": "Dollar General",
                                             "wikidata": "Q145168"}, ...},
   "goal": {"kind": "brand", "min_visits": 11}, ...},
  {"id": "hard-mode-2026-11-02", "title": "Hard mode: Bondurant, IA to Brayton, IA at 2×",
   "setup": {"constraints": {"hard": true, ...}, ...}, "goal": {"kind": "hard", "min_ratio": 2.0}},
  {"id": "worst-order-2026-11-02",
   "title": "Josephville, MO, West City, IL, Cambria, IL and Stewardson, IL in the worst order at 11×",
   "setup": {"stops": [four towns], "reorder": true, ...}, "goal": {"kind": "ratio", "min": 11.0}},
  {"id": "daily-worse-2026-11-02", "type": "daily-worse", "title": "Make today's route worse",
   "blurb": "Open today's route and make it worse once.", "setup": null,
   "goal": {"kind": "daily_worse"}}]}
```

- **A challenge** is its `id` (its type and the date: a link to it names its day), its `type`, its
  `title` (its trip, by its towns' labels, and its bar: "A to B at 15×", "A to B in 50 h", "A to B
  with no left turns at 1.4×", "Cross 600 bridges between A and B", "Visit 11 Dollar General
  between A and B", "Hard mode: A to B at 2×", "A, B, C and D in the worst order at 11×", the
  four in the setup's order) and `blurb` (how to play it, one sentence: "Set it up fills in the two
  towns and the longest route; press Go and finish at 15× the sensible drive or worse."), its
  `setup` and its `goal`. The challenges come
  in the order of their types (`TYPES`, then `daily-worse`); a type with no draw that fits that day
  is left out, so a day may hold fewer. Today's route made worse is today's only: an earlier date's
  list holds the seven at most.
- **The setup** is what setting the challenge up puts in, in `POST /jobs`'s names: its towns as the
  stops (6 decimals, labelled "Town, ST"; four for the worst order, else two), the objective with
  its item or brand (`{id, name, wikidata}`, the id the build's), the constraints (all four
  named), the worst order and the target (as bad as possible). The tier and the leash are the
  automatic ones and the region is the answer's (the main build, which a setup pins); today's route
  made worse has none (it opens today's route).

| Type | Setup | Goal | Its towns |
|---|---|---|---|
| `long-way` | the longest | `{kind: "ratio", min}`: the route's ratio at least `min` | 150–500 km apart |
| `slow-way` | the slowest (`objective: "time"`) | `{kind: "duration", min_s}`: its `time_s + delay_s` (driving and the delays at signals and stop signs) at least `min_s`, whole hours | 80–250 km |
| `no-left` | the longest, `no_left` and `strict` | `{kind: "no_left", min_ratio}`: no left turn (`metrics.turns.left` 0), the ratio at least `min_ratio` (never under 1.2) | 100–300 km |
| `bridges` | `collect` `bridge` | `{kind: "items", item: "bridge", min}`: at least `min` bridges (`metrics.items`) | 30–200 km |
| `pilgrimage` | `brand`, one of the chains | `{kind: "brand", min_visits}`: at least `min_visits` of its locations (`metrics.brand.visits`) | 30–250 km |
| `hard-mode` | the longest, `hard` | `{kind: "hard", min_ratio}`: a request under hard mode, its ratio at least `min_ratio` (never under 1.5) | 100–300 km |
| `worst-order` | the longest, `reorder`, four towns | `{kind: "ratio", min}` | every two 25–200 km apart |
| `daily-worse` | none | `{kind: "daily_worse"}`: a route whose `base_route_id` is today's route (`GET /daily`), its ratio at least the day's | – |

The browser grades every route it shows against the challenges whose trip it is (the region, the
objective with its item or brand, the worst order as the setup sets it, the stops within 500 m of
the setup's) by the goal (`docs/reference/frontend.md`, **Challenges**).

**The draw.** The towns are the daily route's (`daily_api.TownPool`: the incorporated places of the
main build's places index at 6 decimals inside its bbox, each with a node within 5 km that can play
its stop's role). Each type draws with its own `numpy.random.Generator(PCG64(seed))`, the seed the
date as YYYYMMDD times ten plus the type's place in `TYPES`, 1 to 7 (`challenge_seed`: 202611021 to
202611027 on 2026-11-02, never a daily route's). Two towns, the start and the end, are drawn
uniformly again and again, at most `CHALLENGE_DRAWS` (10,000) times, and kept when they differ, lie
within the type's band of straight-line distances in the build's CRS (`CHALLENGE_BANDS_KM`, the
table's) and each has its node near; the worst order's first town is drawn uniformly, then three
among the towns 25–200 km from it (`CHALLENGE_ORDER_KM`), kept when every two of the four lie that
far apart (start, via, via, end). Each draw kept, in order and at most `CHALLENGE_TRIES` (20), is
the challenge when its setup's request, as the frontend sends it, passes the pre-flight `POST /jobs`
runs (`jobs.prepare`, in a thread) and its plan finds enough (below); a refusal is logged and the
next draw tried, and a type whose draws all fail is left out of the day (logged).

**The bars** come from the trip itself. Each setup is planned once, as the daily route is: a job of the job manager with no client, in one of its worker processes (it takes one of the
server's slots, never a client's share), followed to its end by the generation, its route stored
like any other; with the type's seed, `CHALLENGE_ATTEMPTS` attempts, 2,500, hard mode's 8,000
(`CHALLENGE_HARD_ATTEMPTS`, since it converges slowly), and the largest budget, 120 s, as a safety
net. A plan the clock ended short of its attempts logs a warning naming the date and the type
(another server may set another bar). Its bar is 80 % (`CHALLENGE_BAR_SHARE`) of what the plan
reached, rounded down: a ratio
(the long way, hard mode, the worst order, in its worst order's baseline) to the half below 10× and
to the whole from 10×, at least 1×; the slowest route's driving and delays to the whole hour;
bridges and visits to the whole, at least 3 (`CHALLENGE_MIN_ITEMS`), and from 100
(`CHALLENGE_TENS_FROM`) to the ten (606 asks for 600), a draw whose plan finds fewer than 4
(`CHALLENGE_MIN_FOUND`) passed over, and so is a hard-mode draw whose bar would be under 1.5×
(`CHALLENGE_HARD_MIN_RATIO`: a plan under 1.875×), so no hard-mode challenge asks for less.
Strict no-left converges near the baseline (1.21–1.76× on four trips, below), so its bar is 90 %
(`CHALLENGE_NO_LEFT_SHARE`) of its plan's ratio, down to the tenth, and a draw whose plan makes
less than 1.35× (`CHALLENGE_NO_LEFT_MIN_RATIO`) is passed over: a no-left challenge asks for at
least 1.2× with no left turn. A user's run at the
default 20 s budget makes several times 2,500 attempts, so a bar is within reach of the first run
on the trip, with a fifth to spare for a seed's own luck.

**The pilgrimage's chain** is one of `CHALLENGE_BRANDS`, twenty chains by wikidata id (Starbucks,
McDonald's, Casey's, Dollar General, Walmart, Subway, Taco Bell, Wendy's, Burger King, Dunkin',
Waffle House, Chick-fil-A, Love's, Pilot, Dairy Queen, Sonic, Walgreens, CVS, Kroger, 7-Eleven),
each the build's brand of that id with the most locations (on `us-t3`, Walmart's Q483551 rather
than the Q62606411 some of its stores carry, and Dairy Queen as "DQ Grill & Chill"). Those with at
least 6 locations (`CHALLENGE_MIN_LOCATIONS`) in the trip's area (the request's active nodes,
`request_masks`, counted by `brand_counts` as the engine counts them) are the candidates; one is
drawn with the type's generator, pre-flighted and planned, another drawn when its plan visits
fewer than 4, and the next draw of towns tried once none is left.

**Determinism.** The same date, build (`graph_version`), places index and `CHALLENGE_*` settings
draw the same towns and plans on any server, and the engine's rule (`docs/reference/engine.md`,
**Determinism**) makes each plan, and so each bar, the same when its attempts end it (or it
converges) within its budget, as for the daily route; the backends give the same paths, so the
pre-flight's verdicts are the same on either.

**Making and keeping.** A date's seven are kept in memory (`Api.challenge_days`, by date, region
and `graph_version`; a few kilobytes a date, those of dates no longer answered dropped as another
is kept) and in the route store (its `challenges` table, **The store**), so a restart, or a desktop
app's next start, never makes a day again. A call reads memory, then the store, before it waits on
anything. A date kept in neither is made on its first call by a task of its own
(`Api.challenge_tasks`), which every call for that date waits for (a caller gone leaves it to
finish) while calls for other dates are answered, kept or made by tasks of their own: the town
pool and the draws and pre-flights in threads, the plans in the job manager's workers (about a
minute on `us-t3`, below), then stored and kept. Stopping the server ends a date's making within
5 s: `serve` (as the desktop app's server) gives uvicorn a graceful shutdown of 5 s, after which
the open requests close, the lifespan cancels the tasks and the job manager's stop kills the
workers; nothing of the date is kept then. The dates answered are today
and the 30 days before it (`CHALLENGE_DAYS`), so a link to an earlier day's challenge keeps working
for a month; a public server makes only today's (**Public limits**).

**Refusals.** A date outside the window is 422 `INVALID_REQUEST` on `field: "date"` ("challenges
are for the days from 2026-10-03 to 2026-11-02, not 2026-11-03"); one that does not parse is 422
`INVALID_REQUEST` from the request's validation, with pydantic's `errors` and no `field` (the date
is a query parameter, not a field of a body); on a public server, an earlier date not kept is 422
`INVALID_REQUEST` on `field: "date"` ("that day's challenges are not kept here"). A date to make on
a server whose slots and queue are full is 429 `TOO_MANY_JOBS` ("The server is busy. Try again in a
minute.") before anything is drawn, and so is one whose plan finds them full meanwhile: nothing is
kept, and the next call makes it again. A main build without a places index is 404 `NOT_FOUND`
("no challenges: us-t3 has no places index to draw their towns from"). A plan that ends without
its route (a worker that failed, the time limit) fails the date's making, 500 `INTERNAL_ERROR`,
logged, nothing kept.

**Measured** (`us-t3` 6adae6917be1, the dev machine at load averages of 3–7):

- **Making a day**, as built: the three days from 2026-11-02 through `GET /challenges`, each plan a
  job in a worker process, and the first of them again on a second server with a store of its own,
  took 206.5 s in all (about 52 s a day, at load averages of 2.4–3.6): the seven every day, each
  challenge's stops passing the pre-flight, the first day the same on both servers, no plan ended
  by the clock (`tests/api/test_challenges_data.py`). With the engine in the measuring process
  (the same plans), 2026-11-02 took 60.8 s at a load of 3.5–3.9, its hard-mode plan 24.6 s of it
  (8,000 attempts) and the other five 4.8–7.7 s each. Hard mode adds about 20 s to a day, and a
  hard-mode draw passed over costs a plan more: on 2026-11-03 the first (Carl Junction, MO →
  Albion, OK) made 1.55× and was passed over, the second (Bolivar, MO → Meadville, MO) 1.91×, its
  bar 1.5×, the two plans 59.8 s; on 2026-11-04 the first, Gaylord, KS → Waterloo, NE, made 2.83×
  (bar 2×) in 23 s. Before the bars were 80 % and hard mode had attempts of its own (90 %, every
  type at 2,500 attempts), a day took 29.1–33.6 s. No-left's strict pre-flight refused the first
  draw on two of three days (it accepted 19 of the first 70 draws of seven days); the others' first
  draws all passed.
- **Against a user's run.** Two setups of 2026-11-02 planned through `POST /jobs` at the default
  20 s with seed 7 (`serve --region us-t3`): the long way 16.28× (12,444 attempts) against its 15×
  (the calibration's seed 1 made 22.36×: a seed's own luck moves a run's ratio by more than a
  tenth), the pilgrimage 15 Dollar General against its 11.
- **The bars against the default budget.** Before the bars came from the trip, a survey and 34
  runs of 30 s (seed 1 or 2, the best route at 20 s and at 30 s, every route validated) measured
  what a user's run reaches on draws of the same rule; the draws the generator makes on the same
  days are some of those trips, so they show where its bars sit:

  | Trip (2026-11-02's) | Bar | 20 s | 30 s |
  |---|---|---|---|
  | Losantville, IN → Wallace, IN (166 km) | 15× | 22.36× | 23.07× |
  | Leawood, MO → Crowder, OK, slowest (239 km) | 50 h | 76.7 h | 78.9 h |
  | Bondurant, IA → Brayton, IA, hard mode (123 km) | 2× | 3.88× | 4.31× |
  | Josephville, MO and three Illinois towns, worst order | 11× | 13.66× | 15.41× |

  Carl Junction, MO → Albion, OK, which hard mode passes over on 2026-11-03 (1.55× at 8,000
  attempts), made 2.06× at 20 s and 2.12× at 30 s.

  The other runs: the long way 9.89–22.57× at 20 s on six trips (one sparse rural trip, Kansas,
  OK → Summersville, MO, 9.89×, the others 19.94–22.57×), the slowest 31.9–76.7 h on six (0.26–0.44
  hours per straight-line km), hard mode 2.06–3.88× on four, the worst order 6.74–21.36× on three;
  strict no-left converged at 1.21–1.76× on four trips in 4–14 s with no left turn, where the soft
  constraint on two of them reached 20.75× and 20.85× at 20 s with 555 and 699 left turns at the
  end; 114 of 376, 409 of 1,901 and 640 of 5,615 bridges in the area (11–30 %), and 30 of 33
  Casey's, 64 of 110 Flock Safety and 158 of 427 McDonald's (37–91 %). Of the first ten draws of
  each type on seven days, the pre-flight accepted all 70 of the long way's, the slowest's, the
  bridges' and the pilgrimage's, 69 of the worst order's, 62 of hard mode's (8
  `InfeasibleHardMode`) and 19 of strict no-left's; every day had one of each among them.

## Drawing a route

A player draws a route junction by junction on the map, under the engine's rules, against the
engine's route for the same stops (the frontend starts a normal `POST /jobs` with the same request
and the target `{kind: "ratio", value: 10}`, a round trip's as it is, the par:
`docs/reference/frontend.md`, **Drawing**). The server keeps the drawing legal and
holds nothing of it: each call sends the request and the whole path (`api/draw_api.py`; the
checks are the engine's, `docs/reference/engine.md`, **Drawing**).

**`POST /draw`**:

```
{"request": {POST /jobs' body as it is, its region among its fields},
 "path":    [edge ids in order from the start]}      none yet: the start; at most MAX_DRAW_EDGES (10,000)
```

answers

```
{"stops":   [{input, label, node, lat, lon, moved_m, note}],   the stops as a job's `snapped` event has them
 "at":      {node, lat, lon},                               the node the drawing is at
 "reached": [0, 17],                                        the positions along the path of the stops reached, in order
 "legal": true,
 "length_m": 3120.5, "baseline_length_m": 2210.0,
 "ratio":   null,                                           the length over the baseline's once done; null before, and for a round trip
 "geometry": {"type": "LineString", "coordinates": [...]},  the path drawn; null for no edge yet
 "next":    [{edge, node, lat, lon, name, length_m, turn, stop, coords}],
 "done":    false}
```

`next` is the legal next moves, the edges leaving `at` that extend the path legally, ascending
(none once done; none at a dead end, where the player backs up): the edge, the node it leads to and
its position, the road's name (else its ref, else null), its length, the turn into it from the
last edge (`left`, `right`, `straight` or `u_turn`, by the engine's classes; null for the first
move), the stop it reaches (its index, or null: only the stop due next can be) and its shape as
`[lon, lat]` points (7 decimals). The stops are those the engine resolves for the request
(`resolve_stops`), the same as its job's.

A drawing is the longest route as bad as the player makes it: its request takes the objective
`distance` and the target `max` only (`INVALID_REQUEST` on `objective` or `target`; the item or
brand of another objective too), no worst order (`reorder`, refused on its field: its stops are
drawn in the order given), no `base_route_id` (it starts from its stops) and no `certified`
(refused on its field: it is drawn by hand). It is refused
as `POST /jobs` refuses it otherwise: `UNKNOWN_REGION`, each stop against the region's bbox
(`STOP_OUTSIDE_REGION`), then the pre-flight (`NO_NODE_NEAR`, `STOP_UNREACHABLE_WITHOUT_REPEAT`,
`INFEASIBLE_TURN_CONSTRAINT`); the budget, the vehicle and the departure are kept with a finished
drawing and change nothing in it. A strict turn constraint and hard mode are rules of the drawing; a
soft constraint changes nothing in it (it only penalises the par). A path that is not a legal
route's beginning is 422 `ILLEGAL_PATH`, with `refusal` (why) and `index` (its first illegal edge):
the path up to `index` is legal, so a client cuts it there.

```json
{"code": "ILLEGAL_PATH", "refusal": "TRANSITION_FORBIDDEN", "index": 4,
 "message": "turn 23 → 41 not allowed at node 12 (edge 4 of the path)"}
```

| `refusal` | When |
|---|---|
| `BROKEN_CHAIN` | an id that is no edge of the build, an edge that does not leave the node the path is at (a one-way's only edge taken from its head), a self-loop |
| `OUTSIDE_AREA` | an edge into a node outside the request's area (its leash, tier and bubbles) |
| `NODE_REUSED`, `ROUND_TRIP_START_REUSED` | a node the path has passed; a round trip's start only as its end, once every stop between was reached |
| `EDGE_BANNED` | an edge the request's tier bans, the twin of one the path drives, a two-edge round trip out and back along one road |
| `TRANSITION_FORBIDDEN` | a turn the junction's transition rows do not list |
| `STOP_SKIPPED` | a stop reached before the one before it, the end included |
| `PAST_END` | an edge after the end |
| `TURN_REFUSED` | a turn the strict turn constraint refuses |
| `ROAD_AGAIN` | under hard mode, a road the path left and comes back to |

**`POST /draw/finish`**, the same body, answers `{route_id, ratio}` once the path has reached the
end, every stop on the way in order (else 422 `DRAWING_UNFINISHED`, with the stops `reached`; an
illegal path is `ILLEGAL_PATH` as above). The drawing becomes a route (`Route.new`, scored as the
distance objective with the request's constraints), proven by the validator on the real stops (a
route the validator refuses is a bug: 500 `VALIDATION_FAILED`, logged, never stored), and is
stored like any other (**The store**): its id the content hash, its `input` the request as asked
with its seed filled in as `POST /jobs` fills it (a random one below 2³¹ when it has none), its
baseline's edges the request's. Its run is `{attempts: N, accepted: N, stopped_because: "drawn",
elapsed_s: 0, backend: "human"}`, N its edges: the document's shape is otherwise every route's, and
its document, GeoJSON, share image, directions and `/validate` are every route's. Make it worse
may continue it, as the engine worsening a player's drawing. The same drawing finished twice is the
same route, the same id.

What every step on one request needs, the request's area and bans, its resolved stops and its
baseline, is worked out on its first step and kept for the last `DRAW_CONTEXTS` (8) requests drawn
on (`DrawContexts`, by build and by the request's stops, tier, leash and constraints, under a lock;
a miss works it out again, so the answers never depend on it): about 6.6 MB a request on the
national T3 build. Measured on `us-t3` (build 6adae6917be1: 2,154,510 nodes, 4,412,843 edges)
over HTTP on a lane's server, at load averages of 11–14: a step costs 2 ms with no edge yet, 3–4 ms
at 200 edges, 8–11 ms at 2,000 (a 145 kB answer) and 27–41 ms at 10,000 (729 kB), most of it the
line drawn; a request's first step (a miss) 37–100 ms for Powell's → Reed, 58–101 ms for Portland
→ Seattle and 88–148 ms for Chicago → Denver (a few times that for the first trip into a part of
the build not read yet), the masks (22–24 ms) and the pre-flight's seed and baseline. On the
version 7 build at a quieter hour the same were 8 ms and 26 ms a step, and 26, 51–53 and 62–84 ms a
miss.

On a public server a client takes at most `PUBLIC_DRAW_PER_MINUTE` (240) steps of the two
endpoints together in any minute, and finishes at most `PUBLIC_FINISH_PER_MINUTE` (10) drawings,
since each finish may store a route (**Public limits**).

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
with `Cache-Control: public, max-age=86400`. Its big number is the route's ratio of lengths,
"longer than it had to be", except for a route planned for the slowest time (its request's
`objective` is `"time"`), which shows its document's `time_ratio`, "slower than it had to be";
a round trip shows its length ("to end up where it started"). **`HEAD`** answers the same headers,
its `Content-Length` the image's, without the body (link unfurlers ask that way first); it renders
the image as a GET would, if it is not cached yet.

**`?theme=<id>`** draws it in one of the frontend's themes (`classic`, `studio`, `night`,
`transit`, `topo`, `arcade`, `dashboard`, `atlas`, `sign`, `ledger`), its colours the theme's
(`presentation/share_palettes.py`; `docs/reference/presentation.md`, **Share image**), the fonts
the image's own; without it, classic's, the image's colours from before the themes. The
frontend's "Open share image" link carries the theme the page shows, `auto` resolved by the
system's scheme as the page resolves it (`docs/reference/frontend.md`, **Links**).

- An id that is not a route id (12 lowercase hex digits) is 404 `NOT_FOUND` before any file is
  looked at; then a theme that is not one of the ten (`auto` included: a client passes the theme it
  shows) is 422 `INVALID_REQUEST` on `field: "theme"` ("unknown theme 'neon'; known: classic,
  studio, ...").
- An image already rendered is read from `share/<id>-<theme>-v<SHARE_IMAGE_VERSION>.png` in the
  share directory, `share/` beside the route store (`Api.share_dir`), and served as it is, whatever
  has happened to its build since: a route's image in each theme is a file of its own, the one
  without a theme classic's.
- Otherwise the route is read from the store (404 `NOT_FOUND`) and the endpoint takes the image's
  lock (`Api.share_locks`, an `asyncio.Lock` per route and theme), so concurrent first requests for
  an image render it once: the first renders in a worker thread (`asyncio.to_thread`), in the
  theme's colours, on the route's build at its `graph_version` (409 `BUILD_UNAVAILABLE`, as `GET
  /routes/{id}` answers), with `comparison_line` of its length, its region's places index's
  outlines, its request's objective and the document's `time_ratio` (computed as `GET
  /routes/{id}` does: `stored_delays` and `sse.time_ratio`); it writes the file whole (a temporary
  name of its own, then a rename, so two writers, two servers on one store, never share a part
  file); the others then find the file. The lock is forgotten once the request that took it is
  done, whatever happened (an image written, a 409, a render that failed, which is a 500 and leaves
  no file): the next request starts afresh.
- The places indexes (`Api.places`, by region) are loaded once, at startup, one for each served
  region whose index `worst-route aux places --region R` has written
  (`docs/reference/presentation.md`, **Places**); a route's is its region's own, else a served
  one that covers it (**Running it**). Without one the images have no state outlines; nothing
  fails.

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
- **Oregon** (`serve --region oregon --port 0`, csr, engine version 3, 2026-10-31, build
  5913ade7a2df, 237,581 nodes and a 174 MB build, with the Oregon places index; load average
  8–13). The API process holds 170–300 MB once the build is loaded (its pages of the mapped
  arrays count) and 445–455 MB after drawing a share image. A job's worker peaks at 374–399 MB
  RSS, most of it pages of the same mapped build, which workers share through the page cache.
  Medford → Portland, seed 1, a 30 s budget: 265 `progress` events, `done` after 31.9 s at 9.27×
  (4,116.9 km, `budget`); `GET /routes/{id}` 3.4 MB in 0.63–0.89 s, with 5,335 steps, 123 towns
  and `["Oregon"]`; `/directions` 106 ms; `/share.png` 1.10 s to draw (174 kB; the route's
  bbox, 186 × 387 km, meets 211,534 road units, so the network is thinned to T0–T2's 31,568), 1
  ms from the cache. Portland → Bend with a 2.72× ratio target, seed 1: `done` after 7.3 s at
  2.73× (700.5 km, `target`); the document 0.6 MB in 0.08–0.15 s with 723 steps and 33 towns,
  `/directions` 11 ms, `/share.png` 0.54 s (115,836 road units, thinned to 17,050).
- **Oregon beside the national T1 build** (`serve --region oregon --region us-t1 --port 0
  --geocoder none`, csr, engine version 5, 2026-10-31, builds ea3b18096e8b and c4a85b3d36d0, with
  both places indexes, load average about 3): `/ready` ready, its places "oregon: 534 towns;
  us-t1: 31540 towns"; `/regions/pick` put Portland → Bend on `oregon` ("all stops in Oregon"),
  Portland → Chicago on `us-t1` ("stops in Oregon and Illinois") and refused Portland → Honolulu
  (`STOP_OUTSIDE_REGION`, stop 1). Planned on the builds picked, seed 1, a 30 s budget: Portland
  → Bend on Oregon's all roads (T4, a 117.1 km leash), `done` after 30.9 s at 17.70× (4,534.7 km,
  `budget`), `["Oregon"]` and 120 towns; Portland → Chicago on T1 (a 565.1 km leash), `done`
  after 31.6 s at 9.69× (32,801.3 km, 20 states, 1,770 towns); each `/validate` ok and each
  share image drawn. The API process held 249 MB afterwards.
- **The daily route** (`serve --region us-t3 --region oregon --region portland --geocoder none` on a
  port of the lane's own, csr, 2026-10-31, builds 281a455e3d15, ea3b18096e8b and a453e105582b with
  their places indexes, a store of the lane's own, load average 6–14): the day's first `GET /daily`
  answered `planning` in 0.74 s (the towns' points, the draw, a pre-flight refusing Handley, WV →
  Falls City, NE and one accepting Sageville, IA → Acworth, GA, the job submitted), the next calls
  in 3–45 ms. The job ended on its 5,000 attempts after 15.3–32.0 s (`budget`, 39 splices accepted):
  `a076702bbc25`, 8,600 km against a 1,307 km baseline, 6.58×, through 8 states and 564 towns, its
  `/validate` ok. Three servers on three fresh stores, two local (a 120 s budget) and one `--public`
  (60 s), planned the same route, `a076702bbc25`, byte for byte. Once the band was 15–45 % and the
  request carried the default vehicle and its departure, a fourth server on a fresh store planned
  the same day to the same route, `a076702bbc25`, in 13.7 s: its document 188.8 gallons ($734.56)
  beyond the sensible route's and an arrival on 2026-11-05, from a departure of
  2026-10-31T22:12:35Z; a `DELETE` of its job answered 409 `DAILY_JOB`; its share image drew in
  1.1 s (357 kB).
- **Portfolios** (`serve --region portland --region us-t3 --port 0 --geocoder none`, csr,
  engine version 5, 2026-11-01, builds 0b72253e5e8a and 6adae6917be1, a store of the lane's own,
  ten cores; at version 6 seed 3 alone at 60,000 attempts plans the same route, 63.01×, those runs
  ending on their attempts before any reheat; another
  lane's jobs and an emulator beside it, the load average 7.6–15, the machine's swap 12.2–12.9 GB
  used of 13.3–14.3 GB and under 0.6 GB free).
  - **Powell's → Reed, K = 4, seeds 1–4, 60,000 attempts, a 30 s budget** (load 9.5–11): `done`
    after 14.5 s, each search 13.5 s, every one ended by its attempts; the winner seed 3, 63.01×
    (486.2 km), `run.portfolio {k: 4, seeds: [1, 2, 3, 4], winner: 2}`. The four seeds as plain jobs
    at the same 60,000 attempts: 59.43×, 61.14×, 63.01× and 62.32×, 8.1–8.2 s each, seed 3's the
    same route (`913698e28e0f`, its `done` the portfolio's stored id), so the portfolio is their
    best, in 14.5 s against 35 s for the four one after another. The lead: seed 1 at 0.9 s, then
    seed 3 at 2.8 s, seed 1 at 4.9 s, seed 4 at 6.9 s and seed 3 at 8.9 s, at least the hold's 2 s
    apart; seven snapshots in 142 events, the first 3.3 kB, those of a new leader 116–175 kB, two
    every 50th `progress` (157 and 184 kB); 699 splices sent of the leaders', 132 `progress` events.
    At 15,000 attempts: 5.1 s, seed 4 at 50.93×, one change of leader.
  - **New York → Los Angeles on `us-t3`, a 60 s budget** (budget-bound, load 7.6–12): seed 1 alone
    made 1,593 attempts, 3.81×, twice the same route; seeds 1 and 2 as two plain jobs at once, 287
    and 97 attempts, 3.79× and 4.12×; K = 2, seeds 1 and 2, twice: seed 1 the winner at 3.51× (57
    attempts; seed 2's best 3.27×), then seed 2 at 4.12× (112 attempts; seed 1's 3.81×), the lead
    changing six and seven times, its snapshots 0.44–2.04 MB. Two `us-t3` workers at once, a
    portfolio's or two jobs', each ran at a fifth of one alone or less on this machine at that
    time, both near a whole core (94–95 %): so a portfolio on the national build needs memory for
    its workers to beat one seed in the same seconds, and here it did not.
  - **The follower's reads** (load 7.0–7.9; `serve --region portland`, a fresh store): it waits
    on every live worker's pipe at once each round, at most `API_READ_GRACE_S`, so a quiet search
    could hold up a splicing one whose pipe is full. Powell's → Reed at 15,000 attempts, a
    portfolio against plain jobs at the same seeds started together, four rounds alternating which
    went first: K = 2 3.26–3.54 s a search (a mean 3.27 s) against 2.92–3.59 s (3.33 s), K = 4
    3.58–3.72 s against 3.65–3.94 s, each search's own time from its worker, so about 4,000–5,300
    attempts a second either way: no throttling.
  - **Workers' resident memory** (each process's, the build's mapped pages counted in each):
    183–187 MB a Portland search (four at once); on `us-t3` 855–874 MB alone, 816–846 MB two plain
    jobs at once, 621–651 MB a portfolio's two.
  - **A public server** (`--public --trust-proxy --workers 3`): `/config`'s `portfolio_max` 2; a
    portfolio of 3 refused `PORTFOLIO_TOO_LARGE` with `max: 2`, one of 5 `INVALID_REQUEST` on
    `field: "portfolio"`; one client's two-seed job running, another client's two-seed job queued
    with one worker free, the first client's next queued on its own and the second's next behind
    its first; the first client's portfolio cancelled, the second's portfolio and the first's next
    started together on the three workers, the second's next still queued.

## Basemap

The frontend draws the basemap the server names (`docs/reference/frontend.md`, **Basemap**;
`docs/reference/hosting.md`). `WORST_ROUTE_BASEMAP` (`config.Settings.basemap`,
`ApiSettings.basemap`) picks it: `raster`, the default, OpenStreetMap's raster tiles, which their
usage policy allows at hobby volume and so only locally; `openfreemap`, OpenFreeMap's vector tiles
from its public instance (no key, no limits; https://openfreemap.org), drawn in the style
`config.OPENFREEMAP_STYLE` names, its Positron (the desktop app's until its own file arrives,
`docs/reference/desktop.md`); or `pmtiles`, Protomaps' vector tiles from one file the server
holds, `<WORST_ROUTE_DATA>/basemap/<name>.pmtiles`, which `tools/fetch_basemap.sh` cuts from a Protomaps build (`docs/reference/hosting.md`); the name is
`serve --basemap-name`'s (`ApiSettings.basemap_name`), by default the first region served, so
one file, a national extract, may serve every build.

**`GET /config`** answers what the frontend needs of the server:

```
{"public": false,
 "basemap": {"kind": "pmtiles", "tiles": "/basemap/portland.pmtiles",
             "glyphs": "/basemap/fonts/{fontstack}/{range}.pbf",
             "sprite": "/basemap/sprites/grayscale", "style": null},
 "limits": {"budget_max_s": 120.0, "leash_max_km": 5000.0, "portfolio_max": 4}}
```

`public` is `--public`'s, `limits` the budget, leash and portfolio a request may ask for
(**Public limits**; `portfolio_max` is `MAX_PORTFOLIO`, 2 on a public server, and never more
than the server's workers, so a server with one worker runs no portfolio); an auto leash on a trip of more than 750 km is longer than `leash_max_km`.
`basemap.kind` is `raster`, `openfreemap` or `pmtiles`; with `raster` the other four are null,
and with `openfreemap` all but `style`, the style's URL
(`https://tiles.openfreemap.org/styles/positron`), which the frontend fetches (under a theme
with dark tiles, the same URL with `dark` for `positron`: `docs/reference/frontend.md`,
**Themes**). With `pmtiles`,
`style` is null and `tiles` is the file's path; `glyphs` (a MapLibre template of the labels'
fonts) is given when the basemap's directory (`basemap_api.basemap_dir`: `<data>/basemap/`, or
`ApiSettings.basemap_dir`) holds `fonts/`, and `sprite` (the base of the icons' files, of
Protomaps' grayscale flavor, the one the classic and studio themes draw, whose icons the light
and dark flavors use too: `basemap_api.SPRITE`) when it holds
`sprites/grayscale.json`, both from `tools/fetch_basemap.sh --assets`; null,
the frontend draws the basemap without labels. Every path is the API's own: the frontend reaches
it under `/api`.

**`/basemap/{path}`** serves `<data>/basemap/` (or `ApiSettings.basemap_dir`) as static files
(`basemap_api.BasemapFiles`, on Starlette's `StaticFiles`): `GET` and `HEAD`; byte ranges, as the PMTiles reader asks for every
read (`Accept-Ranges: bytes`; one range is a 206 with `Content-Range`, a range running past
the end cut at it, so the reader's first read of 16 kB is all of a smaller file; several are a
`multipart/byteranges` 206; a range starting past the end is a 416 with `Content-Range: bytes
*/<size>`, and a malformed one a 400, both `text/plain`, not an error body); `ETag` and
`Last-Modified`,
with `If-Range` (a range of a file that has changed since is the whole file) and
`If-None-Match` (304); `Cache-Control: public, max-age=86400`; `application/vnd.pmtiles` for a
`.pmtiles` and `application/x-protobuf` for a font's `.pbf`; a whole `.pmtiles` never gzipped, its
tiles compressed already (a font's glyphs and the icons' JSON are, as any answer over a kilobyte:
**Running it**). A file that is not there is 404
`NOT_FOUND`, and so is every file when the directory is missing; a path that would leave the
directory is 404. The directory is the operator's and holds only the basemap, so a symlink in it
is followed: the file may be kept elsewhere and linked in, which `/ready` accepts too. A
replaced file has a new `ETag`, which the PMTiles reader notices and reads
again. In a deployment behind a reverse proxy, the proxy may serve `/api/basemap/` from the same
directory itself (`docs/reference/hosting.md`).

## Public limits

`serve --public` (`ApiSettings.public`) applies `limits.PUBLIC`, the `config.PUBLIC_*` numbers,
for a server open to anyone (`docs/reference/hosting.md`); without it, `limits.LOCAL`, as the CLI
and a local server have always had them. Off by default.

- **A budget of at most 60 s** (`PUBLIC_BUDGET_MAX_S`, against `BUDGET_MAX_S`'s 120): more is
  `BUDGET_TOO_LARGE` with `max_s: 60`, in the same place among the checks.
- **A request's leash stays at most 5,000 km** (`LEASH_MAX_KM`; 150 km until 2026-11-01), the
  request's schema's limit for every server; a public server adds nothing to it (the plan's public
  limit since 2026-10-31: a request's own leash capped, the automatic one following the trip within
  `LEASH_AUTO_MAX_KM`, 150 km). An auto leash is a
  fifth of the trip from 750 km on (`docs/reference/engine.md`, **Requests**), so a
  cross-country job's is longer, up to about 900 km, its searches larger; the budget still
  bounds the job's loop. A job asking for the worst order searches for it first, for up to
  `REORDER_SEARCH_MAX_S` (60 s) beyond the budget and the grace, and its budget and grace run
  from the end of that search (**Jobs**): a public job is killed at most 90 s after its start, or
  150 s while a worst-order job still searches and 90 s after its search ends, 240 s in all at
  the very worst.
- **A portfolio of at most two seeds** (`PUBLIC_MAX_PORTFOLIO`, against `MAX_PORTFOLIO`'s
  four): more is `PORTFOLIO_TOO_LARGE` with `max: 2` (**Portfolios**). Each seed is one of the
  server's workers (`API_MAX_JOBS`), while a client's share below counts jobs: a client running
  a job of two seeds runs one job, on two workers. A certified job is one job on one worker,
  whose solver runs 4 threads (`CERTIFIED_WORKERS`); a public server gives it no other limit
  (**Certified routes**), so a busy one may run more threads than it has cores.
- **Each client's share of the jobs**: a client (`limits.client_of`: the peer, or behind a
  trusted proxy the last address of `X-Forwarded-For`, an IPv6 one by its /64; **Logs**) runs at
  most one job at once (`PUBLIC_RUNNING_PER_CLIENT`) and has at most three more waiting
  (`PUBLIC_QUEUED_PER_CLIENT`). Its next job waits in the queue even while workers are free, and
  starts once its running one ends; when a worker frees, the first job in the queue whose client
  may run one starts once its workers are free (a job waiting for workers holds up those after
  it, **Jobs**), so a job waiting on its own client's never holds up another client's. A
  fifth is `TOO_MANY_JOBS` with the client's counts, `{code: "TOO_MANY_JOBS", message: "You already
  have a route planning. Wait for it to finish.", running: 1, queued: 3}`. The
  server's own limits stay (`API_MAX_JOBS` workers running, `API_MAX_QUEUED` jobs waiting): a job refused for
  them is `TOO_MANY_JOBS` without counts ("The server is busy. Try again in a minute."). The job manager counts each client's jobs
  (`JobSettings.running_per_client`, `queued_per_client`); a job's client is in its log lines,
  hashed.
- **The daily challenges of today only** (`GET /challenges`, **The daily challenges**): a public
  server makes the challenges of today's date and of no other, since making a date costs about a
  minute of the server's time (on `us-t3`: six plans, one at a time, each one of its workers for 5
  to 30 s, the draws and pre-flights in the API process's threads), and a client walking the window
  of 31 dates could otherwise keep a worker busy for half an hour. An earlier date is answered only
  when it is kept (made while it was today, in memory or in the route store), else 422
  `INVALID_REQUEST` on `field: "date"`, "that day's challenges are not kept here". A local server
  and the desktop app make any date of the window.
- **Each client's share of the place searches**: at most ten in any minute
  (`PUBLIC_GEOCODE_PER_MINUTE`, `limits.GeocodeQuota`, a sliding minute), a search the cache
  answers counted too; the eleventh is `RATE_LIMITED` with `retry_after_s`, the whole seconds
  until its first search leaves the minute (at least 1), and the message "at most 10 searches a
  minute; try again in N s". It is checked after the query, the region and `GEOCODER_DISABLED`,
  before the cache, and a refused search counts nothing. Nominatim's own rate limit (one request
  a second for the whole server) stays as it is (**Geocoding**).
- **Each client's share of the picks of a build** (`POST /regions/pick`, **Picking a build**): at
  most sixty in any minute (`PUBLIC_PICK_PER_MINUTE`, a `GeocodeQuota` of its own, so picks never
  take a place search's share); the sixty-first is `RATE_LIMITED` with `retry_after_s` and the
  message "at most 60 picks a minute; try again in N s", checked after the body. The frontend
  asks once a change of the stops, 300 ms after the last.
- **Each client's drawing steps** (`POST /draw` and `POST /draw/finish` together, **Drawing a
  route**): at most 240 in any minute (`PUBLIC_DRAW_PER_MINUTE`, a `GeocodeQuota` of its own); the
  241st is `RATE_LIMITED` with `retry_after_s` and the message "at most 240 drawing steps a minute;
  try again in N s", counted once the body parses.
- **Each client's finished drawings** (`POST /draw/finish`): at most ten in any minute
  (`PUBLIC_FINISH_PER_MINUTE`, a `GeocodeQuota` of its own), since each finish of another path
  stores another route and the steps' share alone would let a client fill the store far faster
  than its jobs could; the eleventh is `RATE_LIMITED` with `retry_after_s` and the message "at most
  10 finished drawings a minute; try again in N s". A finish counts as a step too, the steps'
  share asked first; both are counted once the body parses, before the drawing is checked.

The daily route's job (**The daily route**) has no client: it takes one of the server's slots,
never a client's share, and `GET /daily` has no quota of its own.

The CLI has none of these limits; `worst-route plan` plans any budget the engine takes.

## Logs

Every HTTP request gets an id (`logs.RequestLog`, the app's middleware): 16 hex digits, or with
`--trust-proxy` the proxy's `X-Request-ID` when it is 1 to 64 letters, digits, dots, dashes and
underscores. The answer carries it as `X-Request-ID`, an unhandled error's 500 `INTERNAL_ERROR`
too (the app's handler answers outside the middleware, and finds the id in the request's scope,
`logs.request_id_of`; it logs the error with its traceback and the id, and uvicorn logs the
traceback again as the server's error). While the request is served the id is in a context
variable, which the log lines written meanwhile carry. A job keeps the id of the `POST /jobs`
that submitted it, so every line about it carries that id, whatever task writes it: a queued
job is started by whatever freed its slot (another job's follower, a cancel, the sweep), in
that one's context. When the response ends (a stream's at its end), one line goes to
the logger `worst_route.access`: the method, the path without its query (a place search's text
never reaches the logs), the status (500 when the app raised before answering), the duration in
milliseconds and the client as `client_hash`, 12 hex digits of a sha256 under a salt drawn at
startup, so one client's lines can be followed within a run and its address is never written.
The client (`limits.client_of`) is the peer's address, or with `--trust-proxy` the last address
of `X-Forwarded-For` without the port a proxy may append (`203.0.113.9:4711`,
`[2001:db8::1]:4711`); an IPv6 client is named by its /64, an IPv4-mapped one by its IPv4
address.

Every log line about a job carries its `job_id` and its `request_id`, and each job's end is one
INFO line from `worst_route.api.jobs`: `job <id>: <status> (<outcome>) after <s> s`, with the
fields `status` (`done` or `error`), `outcome` (the stop reason, or the error's code),
`route_id` and `elapsed_s`.

With `--log-format json` (`logs.json_log_config`) every record of uvicorn and of the package is
one JSON object a line on stdout:

```
{"ts": "2026-10-31T07:27:21.591Z", "level": "INFO", "logger": "worst_route.access",
 "message": "POST /jobs 202 31.2 ms", "request_id": "19550fcad0f0f870", "method": "POST",
 "path": "/jobs", "status": 202, "duration_ms": 31.2, "client": "c60f7d8f1b3c"}
```

(on one line): `ts` (UTC, milliseconds), `level`, `logger`, `message`, the record's own fields,
the request's id when one is being served, and `exc`, the traceback, after an exception.
uvicorn's access lines, which carry the peer's address, are off; `worst_route.access` takes
their place. In the text format uvicorn's access lines stay and `worst_route.access` is silent.

## Tests

`tests/api/` (`support.py` writes lattice builds into a temporary data directory and holds the
helpers; the async tests run on anyio's pytest plugin, asyncio only):

| Test file | What it covers |
|---|---|
| `test_schemas.py` | the request mirrors the engine's (a pilgrimage's brand included), its defaults, the seed's bounds, stops left out only with a base, malformed requests refused (both turn constraints at once, an unknown one); the target as the engine's, each kind with its own number and within its bounds; `done`'s time ratio, target, delays and turns typed |
| `test_logs.py` | who a client is (the peer; the last `X-Forwarded-For` address only when trusted, without a port; IPv6 by its /64), its hash; a request id and one access line per request, without the address or the query; a proxy's request id kept only when trusted and well formed; a job's end line with its id, outcome and route and the id of the request that started it; a queued job's lines with its own request's id, not the one that freed its slot; an unhandled error's 500 `INTERNAL_ERROR` with the request's id, logged with it; a JSON line's fields and traceback |
| `test_limits.py` | each client's searches on a fake clock (ten in any minute, `retry_after_s`, other clients apart, idle ones forgotten); the manager's per-client jobs (one running and three waiting while workers are free, the fifth refused with its counts, another client and a job without a client unaffected, the next of a client starting in order when its running one ends, a waiting job whose client is busy letting the next one start, a full server's refusal without counts); over HTTP the public budget (and 120 s otherwise), a client's share of the jobs from one peer and behind a trusted proxy (the last forwarded address counting, an untrusted header ignored), a client's eleventh search refused, cached answers counted, none of it when not public; a public portfolio of at most two seeds (`PORTFOLIO_TOO_LARGE` with `max: 2`), each a worker: one client's two-seed job running, another's waiting for two workers with one free, the first client's next waiting on its own |
| `test_basemap_api.py` | `/config` for each basemap (OpenFreeMap's with its style, and `/ready` passing it), with and without the labels' assets, and its public flag and limits (the most seeds a job runs: 4, 2 when public, never more than the workers); the basemap's file: whole, its first 127 bytes, a suffix, a range running past the end (cut), one starting past it (416 with the length), `If-Range` current and stale, `HEAD`, the media types and caching; a font; a missing file, a missing directory and a path leaving it, 404; a linked file served and ready, and a small one's first 16 kB read whole; a basemap directory of its own (`basemap_dir`), the data's ignored; `/ready`'s basemap check, failing without the file (no path in its detail) and naming the labels; one file named for every build served, by `basemap_name` or the first region |
| `test_several_builds.py` | two regions served at once (`lands` in `conftest.py`: one fake state's region and one of two, `config.REGIONS` and `STATES` given them): `/regions` (in the order served, each with its `name`; a region config does not name by its id), `/health` and `/ready`'s build check over both, `UNKNOWN_REGION` naming them; a job on each build, its document, `/validate` and the stored route's build the served one; a stop beyond one build's bbox; a stored route's document and share image after a restart serving the regions the other way round, and serving only another region (its build read from disk, its places from a covering index); a region without its own places index taking a covering one (`/ready`'s detail saying so), then its own; a region clipped to a bbox covering no other but covered by a whole state's |
| `test_pick_api.py` | `POST /regions/pick` on the same regions: stops in one state (whatever the order served, one stop too), in both, in both with only the one-state region served (by bounding box), a stop in no state, no places index; a region clipped to a bbox first, the smallest first, passed over for a stop in a state it does not name while a places index is loaded, by its bbox alone without one; the reason naming a stop beyond the smaller build; a tie to the region served first; a public server's sixty picks a minute a client, apart from its searches; `STOP_OUTSIDE_REGION` for a stop outside every region and for one outside every region, its sentence naming the regions served ("Westland or Lands"), or the lower 48 when one of them is national, and "These stops are on different maps." for stops each on some map but on none together; the body's refusals (no stop, a bad one, an unknown field); a job planned on the region picked |
| `test_pick_data.py` (`data`) | the pick with Portland's, Oregon's and the national T1 build served from `WORST_ROUTE_DATA` with their places indexes: Powell's → Reed on `portland`, Portland → Bend on `oregon` ("all stops in Oregon"), Portland → Chicago, Portland → Bend → Vancouver (WA) and Portland → Vancouver (both in Portland's bbox) on `us-t1`, Honolulu refused |
| `test_ready_api.py` | `/ready`: 200 with the build, the store and the places index; 503 without the index, and once the build is replaced or gone (while `/health` answers), its details without a path on the server |
| `test_static.py` | the site: the build at `/` and its caching, a module's media type, `/health` not at the root, the API under `/api` with its errors, a job run to its end through it (the lifespan ran), the access lines' whole paths; compression: a 256 kB script gzipped to a client accepting it (one `Vary`, its caching, the bytes on the wire gzip's), plain without `Accept-Encoding` (one `Vary` and its length), its 304 with the one `Vary` whatever the client accepts; a page under a kilobyte and a WOFF2 font plain, without a `Vary`, their 304s too; a job's stream, accepting gzip, driven at the ASGI level: plain `text/event-stream`, one event a message from `snapped` to `done`; the route's document gzipped, with its compressed length, and plain; the share image and a whole `.pmtiles` not gzipped |
| `test_store.py` | the round trip (the stored route validates), unknown ids, the first record kept, what the id covers; a day's row, its graph_version's (the same build id at another one has another), replaced in place; a route's order kept, and a store from before the worst order gaining `order_json`, its rows the identity, once however often it is opened, a column another server added meanwhile counting as there and any other error raised |
| `test_daily_api.py` | the daily route on the 20 × 20 lattice with a places index of towns placed on it, the day fixed: the draw (every trip within the band, two towns closer than its lower bound never drawn, between incorporated places of the kinds drawn, inside the build's bbox, never a town without a node within 5 km; the same seed the same trips; none when nothing fits), the region with the most states; `GET /daily` planning, then done with the stored route's ratio, stop and attempts, the same answer call after call, the route's labels, seed, attempts, budget, default vehicle and departure (UTC, to the second), with its fuel and arrival, its `/validate` ok; two servers on two stores answering alike, the iterations having ended the run; concurrent first calls planning once; a failed job recorded and submitted again, the day drawn once; a trip the pre-flight refuses giving way to the next, and a day whose ten trips it refuses failed and answered so again without a pre-flight; a full server answering 429 `TOO_MANY_JOBS` with nothing drawn or recorded until a slot frees; a client's `DELETE` refused with `DAILY_JOB` while the job runs, and only reporting it once ended; a job the server cancels failing the day, then planned again; the job outliving a viewer that leaves (no orphan); a call after the job's end recording it before its follower, and a follower never overwriting a later plan; a planning row of a restarted server planned again; a build replaced under its id (`save --force`) planned again on its own row, its route served, the old row kept; a public server's job without a client, at its budget; an index without incorporated places, and no places index, 404 |
| `test_challenges_api.py` | the daily challenges on the 20 × 20 lattice, its rows and columns named, with bridges on three rows, four brands (two listed chains with enough locations, one listed with three, one without a wikidata id), a places index of towns placed on it and the bands scaled to it, the day fixed, each plan a job of the app's manager on inline workers: the seven in order with their ids and setups (objective, item, constraints, worst order, four stops for it, labels, 6 decimals, the target); the titles naming each trip and its bar, the blurbs one sentence each; every draw within its band and the worst order's every two apart, over three days; each bar the plan of its setup by the engine itself (written out in the test) rounded down; hard mode planned with its own attempts, a bar under its least passed over; no-left planned with the others' attempts, its bar 90 % of its ratio down to the tenth, a run under 1.35× passed over; the rounding itself (a count of 100 or more to the ten); the same day twice the same, another day other towns; a refused pre-flight taking the next draw; a type without a fitting draw left out; too few bridges passed over; the pilgrimage's chain one with enough locations, none from the chains without; a chain visited too little falling back to the other, then the next draw; a plan the clock ended logged with its date and type; `GET /challenges` made once for two concurrent first calls and kept, its shape, today's route made worse today's only (another date made once more, without it; both forgotten a month on); a day kept in the store answered after a restart without being made; a date being made holding up no other; the server's stop cancelling a date's making; the same on two servers; the window's refusals and a malformed date (no `field`); a public server making only today's and answering an earlier date only when kept; a full server's 429 before anything is made and from a plan meanwhile, nothing kept, then made; `NOT_FOUND` without a places index |
| `test_challenges_data.py` (`data`) | three days of challenges on `us-t3` from `WORST_ROUTE_DATA` with its places index, from 2026-11-02, through `GET /challenges` with the plans in worker processes: the seven each day and today's route made worse on the last (today) only, every challenge's towns within its band, its request accepted by the pre-flight, its bar at its floor or above (hard mode's 1.5×, no-left's 1.2×), its title naming its first town; the first day again on a server with a store of its own, the same (about a minute a day; a timeout of 900 s) |
| `test_draw_api.py` | `POST /draw` and `POST /draw/finish` on the drawing board (`tests/engine/graphs.py`): an empty path the start with its legal ways out (the out-edges by hand: a one-way's absent), their names, lengths and shapes; steps with their turns, the stop a move reaches, done with its ratio and line; each refusal with its code and first illegal edge (a node passed, a twin, a turn the straight-only junction forbids, the end before a via, a one-way taken from its head, past the end, an id outside the graph, a strict turn, a road again under hard mode), the path before it drawn, a soft constraint's left turn taken; a finished drawing stored under its content hash with its run drawn by a human, its seed and labels, its document, directions, share image and `/validate`, the same id when finished again, and continued by make it worse; a round trip finished with a seed drawn for it and no ratio; an unfinished drawing and an illegal one refused at finish; the stops those a job's `snapped` event gives; the refusals of the request (an objective, a target, the worst order, a base route, a portfolio, a certificate, an item), an unknown region, a stop outside the bbox, a path over the cap and a negative id; a public server's 240 steps a minute over both endpoints and its ten finished drawings a minute (the same drawing the same route, steps going on); a request's context worked out once whatever the seed and the path, and the last ones kept |
| `test_draw_data.py` (`data`) | on Portland's build, a route drawn by hand from Powell's round a few blocks to Pioneer Courthouse Square from the moves the server offers (backing up from dead ends), more than twice the sensible route, finished, stored, validated, its share image rendered |
| `test_sse.py` | path points and node indices along a bent geometry, the per-edge simplification keeping nodes and original values, splices, metrics, the event encoding |
| `test_jobs.py` | the manager with inline workers: event order and ids, a client replay equal to the stored route, late subscribers (after a worker that died with splices waiting too: they come before its `error`, ids rising strictly), `Last-Event-ID` resync, cancel (validated, stored, reproduced by its attempts), the queue and its limit, cancel before the route exists, continuations, the time limit, orphans running and queued, a worker that raises, a worker refusing a brand its request's area does not hold (`INVALID_REQUEST`), a store failure while cancelling (by hand and by the sweep, which goes on), a worker that does not start (the queue moves on), the TTL; a target run whose best is not its last route (an overshoot on a 5 × 5 lattice): a snapshot of the stored route before `done`, for live and late clients, and a cancel after the overshoot storing the closest route, reproduced by its attempts; a reorder job's `snapped` only once its worker has chosen the order (a plain job's at once, its order the identity), its stops, labels and inputs in that order and the stored route's request and input with them, a reorder job cancelled during its search ending `CANCELLED` and one cancelled while it runs storing its route validated in its order; a reorder job whose search outlasts its budget and grace finishing on its own budget, counted from `Started`, past the search's allowance, and one whose search outlasts the allowance too ending `WORKER_TIMEOUT`; an annealing job on a town of uneven streets: the best route's score and metrics in every `progress`, never falling, the current route ending below them, and the best sent as the snapshot before `done` |
| `test_certified.py` | a certified job over HTTP: its events (`snapped` with its leash, the seed's `snapshot`, each better route a `snapshot` then a `progress` with no splice, every `progress` with `certified`, lengths rising, `done` with `stopped_because: "optimal"`), its document's `run.certified` (`optimal`, gap 0, the bound its length, the area's 30 nodes, the 250 m leash, 4 threads) and request, `/validate`; a plain job's events and document without `certified`; the automatic leash narrowed to an area within a monkeypatched cap, said by `snapped` and `run.certified.leash_m`, the request keeping `auto`; every refusal on `field: "certified"` (another objective, a target, the worst order, a turn constraint, hard mode, a round trip, a portfolio, a base route); `AREA_TOO_LARGE_TO_CERTIFY` with its fields before a job exists; with a fed worker, a `Bounded` sent as a `progress` with no splice at its own time, and a cancel storing the last better route with a `feasible` certificate from the last bound, its time and the pre-flight's area; a better route whose bound CP-SAT puts below it (monkeypatched) sent as a `progress` whose `certified` bound and gap are null, the job still proven; a drawing refusing `certified` |
| `test_portfolio.py` | portfolios in the manager, with fed workers replaying real runs' messages when the test hands them over: the lead passing to the search ahead (a tie keeping it), its `snapshot` then a `progress` with no splice, only the leader's splices sent, the replay equal to the stored winner, its seed, request, attempts and `run.portfolio`; the hold keeping the leader while another is ahead, the winner still sent as a `snapshot` before `done`; a failing leader handing the lead on at once, its ratio null, the job going on; every search failing, the first's error; a cancel storing the best route so far of the search ahead, reproduced by its seed and attempts; the first search to reach its target ending the job and stopping the other; with inline workers, the best of three seeds' runs stored, one search's events and run without `portfolio`; a portfolio in the worst order choosing it once, and a search that chose another order failing the job; the workers each search holds, freed as it ends, the queue in order (a job waiting for two workers holding up one behind it); a client running one job whatever its searches; a full server refusing a portfolio it cannot queue |
| `test_portfolio_data.py` (`data`) | on Portland's build, Powell's → Reed with seeds 1 and 2 at 1,500 attempts in spawned workers: the route stored is the better of `plan_route`'s two for those seeds, under its seed, validated |
| `test_worker_process.py` (`slow`) | real spawned workers: a run to `done`, a cancel mid-run, a worker killed from outside (`WORKER_FAILED`), a portfolio's two searches in two processes (the better route stored), a fake worker that dies 60 kB into a 2 MB message (`WORKER_FAILED`, then a clean `stop()`), a worker whose API process (a probe) is killed exiting within 3 s, and a worker run alone in a fresh interpreter on a build without its geometry, planning and never importing the geometry's or the GeoJSON's module; about 5 s |
| `test_geocode_api.py` | `GET /geocode` with a `FakeGeocoder`: the answer's shape, at most five candidates, the cache across casing and spacing (one upstream call), each refusal (an empty, blank, missing or too long query; a missing or unknown region; `RATE_LIMITED` from a full queue with its `retry_after_s`, a cached query still answered; `GEOCODER_ERROR`; a 429 upstream, on an `httpx.MockTransport`, pausing the geocoder, the next search refused without a request; `GEOCODER_DISABLED`, with no cache file); the default service, its client on a `MockTransport` (`offline_clients`): Nominatim at its URL with the User-Agent and contact (and an embedding's own User-Agent with the contact), the cache beside the store, the limits and the pause, its client closed by the lifespan. The other API tests serve with `geocoder="none"`, so they build no geocoder |
| `test_directions_api.py` | the document's directions (numbered, the stops' labels, the arrival's empty range at the end, the distances adding up), toasts (the ratio first, four to ten), the fuel from the request's vehicle and the states and towns from the census fixture's index, all null without them, and the network fraction; `/directions` the document's directions, twice the same, jokes included; a route stored before them (its stored metrics without them, and one without its delays and turns) getting them; unknown routes 404 and a route whose build is gone 409 on both |
| `test_share_api.py` | `GET /routes/{id}/share.png` with and without a places index: a 1200 × 630 `image/png` with its Cache-Control, two concurrent first requests rendering once (a counted render) and getting the same bytes, one file (`<id>-classic-v3.png`), no lock left, a later request with or without `theme=classic` served from the file, then `theme=night` rendered once in night's palette into a second file beside it; night's lock (by route and theme) still known while its render waits after classic's request is done, so a later night request waits on it and night renders once; `HEAD` first (it renders), its headers a GET's with no body; unknown and malformed ids (404, no file); unknown themes (`neon`, `Night`, `auto`, empty: 422 `INVALID_REQUEST` on `field: "theme"`, for HEAD too, no file), a malformed id with one still 404; a route whose build is gone (409, no lock left); a render that fails (no file, no lock, the next request draws it); two servers on one store rendering the same route at once, each part written before either rename (a barrier in `os.replace`), leaving one file and no part; on a lattice with signals, a slowest-time route's image headed by its document's `time_ratio`, "slower than it had to be", and a distance route's by its `ratio`; `import worst_route.api.app` in a fresh interpreter leaving matplotlib out of `sys.modules` |
| `test_routes_api.py` | every endpoint over `httpx.AsyncClient` in-process, every refusal code, a stored route drawn along its build's bent geometry (its document, GeoJSON and share image) and `BUILD_UNAVAILABLE` for a build without one, cancel over HTTP, a closed stream cancelling its job, a stream that subscribes only once it runs, make it worse and its mismatches (a base outside its request's masks included), a continuation that changes nothing reporting the stored route; ratio and duration jobs ending `target` with the target in `done` and the document (none for max), a target out of bounds or a ratio for a round trip refused, make it worse with a smaller target shrinking; a stored route differing from the drawn one by a parallel edge sent as a snapshot before `done`, with `GET /jobs/{id}` agreeing; a ratio target for a round trip refused before anything else, for a base route too; `Infinity` and `NaN` in a body refused; a slowest-time job under a soft no-left constraint, its turns and delays in `done` and the document; a strict constraint refusing a base route that turns left, a soft one taking it; a leg a strict constraint cannot drive answered `INFEASIBLE_TURN_CONSTRAINT` with its leg and constraint, one no route covers without it either (two one-way rings) `STOP_UNREACHABLE_WITHOUT_REPEAT` with strict set too; a body cut short refused without a `field`; `strict` alone and both constraints refused on `field: "constraints"`; on a lattice with signals, the document's `time_ratio`, `done`'s, and `arrival` counting the delays, the route's and the baseline's, for a route stored before the documents carried `baseline_delay_s` too; a collect job counting its bridges in every `progress` and in `done`, the document's `items` agreeing (a distance job's too) and its marks a bridge each (a distance route's none), and the item and target refusals naming their field; `/brands` (its order, an unknown or missing region), a pilgrimage counting its visits in every `progress` and in `done`, the document's `brand` agreeing and a distance route's null, its marks a visit each at the brand's nodes in the order the route meets them (a distance route's none), and the brand refusals (missing, misplaced, a ratio, unknown, out of reach) naming their field; an alphabet job sending each better route as a `snapshot` then a `progress` without splices, its letters and score rising, `done` and the document's `alphabet` agreeing (a distance route's null), `/validate` ending with its `alphabet` check, the build's letters worked out once over two document requests; cancelling one, its best route so far stored and validated; the alphabet refusals (a via, a target, a base route) naming their field; a reorder job's stream and document in the order it visits the stops (its `order`, its request's stops and labels, its stops, the directions' arrivals; a plain job's order the identity), make it worse continuing a reordered route in its order with `reorder` off (the stops asked for in their own order a mismatch), `TOO_MANY_STOPS_TO_REORDER` with the build's `max_stops` (7, and 5 on a large build) and `reorder` under the alphabet route refused on its field; a portfolio job's `progress` events with their `portfolio`, its document with the winner's seed, the first in its request with `portfolio`, and `run.portfolio`, the winner's seed alone planning the same route (already stored), a one-search route's request and run without `portfolio`; a portfolio above 4, under the alphabet route or with a base route refused on `field: "portfolio"`, one whose last seed passes 2⁶³ − 1 refused, `PORTFOLIO_TOO_LARGE` beyond the server's workers; a portfolio in the worst order with one `snapped`, its document in that order |

`tests/engine/test_plan.py` covers `plan_route`'s hooks, `tests/graph/test_loader.py` the loader,
and `tests/unit/test_cli.py` `serve`'s errors, options (the geocoder's included, with the line
it prints; `--trust-proxy`, `--log-format`, `--public`, `WORST_ROUTE_BASEMAP` and `--frontend`,
uvicorn's proxy headers off; several `--region`s, `--build-id` the first's, `--basemap-name`, a
region given twice or unknown refused) and its taking `csr` on a build with transition tables. `tests/geocode/` covers the
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
- **The pick knows states and bboxes, not roads** (**Picking a build**): a trip goes to the
  smallest region whose states hold its stops, whether or not its build has a road near each, so
  a stop the smaller build cannot snap is `NO_NODE_NEAR` from `POST /jobs` where the national
  build might have served it. A stop past a build's nodes' extent, as on Oregon's edges, goes to
  the next region up. "Make it worse" continues a route on its own build, whatever the pick
  would give its stops now.
- **The public limits are per process and per address** (**Public limits**): a restart forgets
  them, and a client with many addresses has many shares. There is no authentication and no
  CORS (the frontend is on the API's origin: the dev server proxies, `serve --frontend` serves
  both).
- **The geocoder's rate limit is per process**: one request at a time from this server, a
  second from each answer to the next request, which is the application's only while uvicorn
  runs one process and one server asks Nominatim (two servers, as the coordinator's and a lane's
  pointed at the real service, would share the policy's second). The pause after a refusal is
  per process too. One client can take every slot, unless the server is public (**Public
limits**: ten searches a minute a client). Nominatim
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
- **The daily route is the same everywhere only when the iterations end it**: a run the clock
  ends (a slower or busier machine, one of the band's longest trips on a public server) is that
  server's own, and `attempts` below `max_iterations` says so (**The daily route**, **Measured**).
- **A drawing's first step pays for its request** (**Drawing a route**): a request not among the
  last eight drawn on costs its masks and pre-flight again, 37–148 ms on the national T3 build, so a
  client varying its request on every step (within its 240 steps a minute on a public server) costs
  the server that much a step.
- **Share images are kept forever** in `share/`, one per route and drawing version; nothing
  removes them, and an image drawn before the places index was written keeps no outlines until
  `share/` is cleared.
