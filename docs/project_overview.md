# Worst Route — project overview

Last updated: 2026-10-31. Snapshot taken after the M2 and M3 acceptance runs: the plan
is written; the project scaffolding, every pipeline step, the graph package, the engine, the API
and the frontend are built, and so is the presentation (M4): the geocoding proxy, the places
index, the comparison lines, the share image, the directions with their jokes, and the stats and
toasts, in the route documents and in the frontend; Portland and Oregon have saved builds, and
the M1, M2 and M3 acceptance checks pass (§2.2): M1 to M4 are built; of M5,
Oregon and the public-hosting preparation are; of M6, the desktop app is, the daily worst route,
beat the engine, the challenges, and the certified worst.

This file answers three questions: what the project is meant to become, how far along it is, and
what has been decided. It is written for a human reader and for an AI agent picking up the
codebase.

It is a summary, not the reference. The design the roadmap builds towards (architecture,
pipeline, engine, API, frontend, performance, milestones, risks) is in `docs/PLAN.md`. The
reference files under `docs/reference/` and the code's module header comments are the authority
on how each built mechanism works, and the map, `CLAUDE.md`, on the conventions to follow when
changing code. Where they disagree, check the code.

## Status vocabulary

| Status      | Meaning                                           |
| ----------- | ------------------------------------------------- |
| Built       | Implemented and working at HEAD.                  |
| In progress | Partly implemented. The remaining work is listed. |
| Planned     | In scope with a known shape. Not started.         |

## 1. What the project is

A joke web app on OpenStreetMap data. The user sets a start, an end and optional stops in the
US; the app computes a hilariously inefficient route between them that never repeats an
intersection and never drives the same road twice, respecting one-way streets, and presents it
with deadpan GPS directions, stat toasts and a shareable image. Built by a solo developer with
Claude: Python for the pipeline, engine, API and rendering; TypeScript + MapLibre for the map.

### 1.1 Scope

- **Data pipeline** (In progress): Geofabrik state extracts → filtered roads → graph → cleanup
  (simplify, consolidate junctions and interchanges, pair divided carriageways, keep the largest
  strongly connected component) → enrichment → tiers → an on-disk build. `docs/PLAN.md` §2.
- **Engine** (In progress): a shortest route through the stops, then a seeded detour-splice
  heuristic that makes it worse under a pluggable objective, with a validator that proves the
  result legal. `docs/PLAN.md` §3. Built for M1 with the distance mode, and searched with the
  CSR + Numba backend since M2's first step; targets by ratio or duration since M2's fourth;
  every other mode, the turn constraints and hard mode since M3.
- **API and frontend** (Built): FastAPI jobs with Server-Sent Events progress, a MapLibre map
  that shows the route getting worse live, settings, reroll, make it worse. `docs/PLAN.md` §4–5.
  The API and the frontend are built for the distance, slowest-time, collect and brand modes,
  the alphabet route, the turn constraints, hard mode and the targets (`docs/reference/api.md`,
  `docs/reference/frontend.md`), and M2's acceptance passes (§2.2).
- **Modes** (Built): longest distance, slowest time, never/only turn left, collect toll
  booths / rail crossings / roundabouts / bridges, brand pilgrimage, alphabet route, hard mode.
  Built: longest distance, slowest time, and never turn left / right as constraints on either
  (soft: each such turn costs a kilometre; strict: none), with targets by ratio or duration, and
  collecting toll booths, level crossings, roundabouts or bridges (targets by duration); the
  frontend offers each of them (its Mode and Turns rows). Hard mode (each road in one stretch) is
  built in the engine, the CLI and the API, with any of them, and the frontend offers it (a
  checkbox in its Turns row), as it does the brand pilgrimage (a Brand mode with a brand picker
  from `GET /brands`) and the alphabet route (an Alphabet mode). M3's acceptance passes (§2.2).
- **Presentation** (Built): directions, stat toasts, comparisons, states and towns, share
  image. Built: the states and towns a route passes through (the Census places index), the
  comparison lines, the share image, and the directions with deadpan jokes, the stats and the
  toasts in the route documents (`docs/reference/presentation.md`), which the frontend shows: a
  directions panel whose steps highlight on the map, rotating toasts under the map, the extra
  stats with the arrival time, a vehicle and a departure setting, and the route's GeoJSON to
  download (`docs/reference/frontend.md`).
- **Scale** (In progress): Portland → Oregon → national at a tier to be decided at M5. Oregon
  is built, and so is what public hosting needs on one machine: an image, the vector basemap,
  per-client limits, logs and readiness (`docs/reference/hosting.md`).

Out of scope for now: social features, accounts, mobile apps.

### 1.2 Design philosophy

- **The joke must be true.** A route that repeats anything is a bug, not a rounding error, so
  legality is enforced in one place (`Route.splice`) and proven in another (the validator) from
  the raw data.
- **Reproducible silliness.** A seed gives the same route forever on the same build; "reroll"
  is a new seed, "make it worse" continues a route.
- **Heuristics, not optimality.** Longest simple path is NP-hard; the engine is a splice loop
  under a time budget that always returns its best so far. A certified mode exists only for
  tiny areas.
- **One engine, many modes.** Every mode is an objective or a constraint on the same loop; the
  one exception (alphabet) is documented as such.
- **Masks, not copies.** The graph is loaded once and memory-mapped; tiers, leash, bubbles and
  the no-repeat bookkeeping are masks.
- **Policies respected.** Nominatim's 1 request/s and no-autocomplete rules, OSM's tile policy,
  attribution.

## 2. Status

### 2.1 Built

The pipeline's first four steps are built (`docs/reference/pipeline.md`): `pipeline download`
fetches and md5-verifies a Geofabrik state extract, `filter` keeps the drivable roads, clipped
to the region's bbox, and a POI table, and `build` makes the raw directed graph with the one-way
rules applied, saved as an intermediate stage. `cleanup` turns that graph into the one the
no-repeat rule rests on: degree-2 chains collapsed, signal clusters and divided crossings merged
(OSMnx, stage A), interchanges and roundabouts made super-junctions with their allowed
transitions (stage B), divided carriageways paired as twins, and the largest strongly connected
component kept, with a report of what was dropped. `enrich` adds speeds and times, identities,
road units, flags, bearings, toll booths and level crossings per edge, and delays and
pass-through per node; `tiers` keeps each tier's largest strongly connected component; `save`
writes the build in the graph package's format, with the configuration it was made with in its
`graph_version` (the compact format 4 since 2026-10-31: only the arrays the engine and the
validator read, each road's shape simplified to 2 m in a geometry beside them); `qa` writes the build's report and GeoJSON layers and checks its thresholds.
Portland's whole pipeline runs from the Oregon extract in under a minute and a half and
passes QA.
The graph package is built (`docs/reference/graph.md`): a build's arrays and their
memory-mapped on-disk format with its `graph_version`, the `GraphBackend` protocol and its
pure-Python reference backend, snapping with the leash and bubble masks, and the builders tests
use for small graphs. The CSR + Numba production backend (M2 step 1) is built too: Numba kernels
for the masked Dijkstra and the simple-path search, node-state and transition-aware,
byte-identical to the reference and 8–13× faster on large searches; whole runs gain about 1.2×
on Portland and 1.5–1.9× on a build without transition tables. The engine and
`worst-route plan` / `validate` use it by default on every build (`--backend auto|csr|python`).
The engine is built (`docs/reference/engine.md`): the route model whose splice is its only
mutation and refuses any repeat, the request with its tier, leash and bubble masks (a tier also
bans lower-class roads away from the stops), snapping with the re-snap fallback, the baseline
and the seed route with node-disjoint legs (and loops for round trips), searches that see the
transition tables and avoid driving both carriageways of a road, the three detour strategies, the worsen loop with its ambition radius and budget, the objective interface with
the distance mode, the validator that proves a route from the original OSM ids, route files,
and `worst-route plan` / `validate`, which run on any saved build. Inefficiency targets are built
too (M2 step 4): a ratio to the baseline or a duration, reached within ±2 % by growing, or by
shrinking a route already past it, through `plan --target` (and `--base` to continue a saved
route) and the API's `target`; on Portland, Powell's → Reed reaches 20× or 3 h in under half a
second and 9 h in about a second, and a 62.43× route shrinks to 30.58× in 5 s. Golden routes
freeze the engine's output on a test lattice, a max run and a run to each kind of target.
The slowest-time objective and the turn constraints are built (M3 steps 1–2;
`docs/reference/engine.md`, **Objectives**, **Turns**, **Constraints**): `plan --objective
time` and the API's `objective: "time"` score driving time plus the delays at signals and
stop signs (on Portland, Powell's → Reed reaches 52.18× the baseline's experienced time, 67.55×
its driving time, and a 9 h duration within ±2 %); turns are
classed by their change of bearing and counted in every route's metrics; `--no-left` /
`--no-right` (the API's `constraints`) make each such turn cost a kilometre of score, and
`--strict` allows none, refused in every search including the Numba kernels', which take a set of
banned turn classes. Two more golden routes freeze the slowest time and soft no-left.
The collect mode is built (M3 step 3): `objective: "collect"` with an `item` (toll booths,
level crossings, roundabouts or bridges) passes as many of it as it can, then drives as far,
through `plan --objective collect --item …` and the API, and every route file and route
document counts all four items; the build keeps each super-junction's roundabout rings
(`node_roundabouts`). On Portland, Powell's → Reed passes 24–30 bridges against the distance
route's 16–17, and Portland's two toll booths, on roads into parks, can be passed only with a
stop beyond them. A sixth golden route freezes a run collecting bridges.
The brand pilgrimage is built (M3 step 4): `enrich` indexes every POI with a brand, snapped to
the nearest node a route can pass through within 100 m and grouped by its wikidata id (else its
name), keeping the brands on at least three nodes, and the build stores the index as arrays
(pipeline version 3; Portland: 287 brands on 2,411 nodes, the Postal Service on 174 and
Starbucks on 126). `objective: "brand"` with a brand's id visits as many of its locations as it
can, then drives as far, through `plan --objective brand --brand <id or name>` and the API,
whose `GET /brands?region=` lists the brands; the counts ride in `metrics.brand` and the
validator recounts them. On Portland, Powell's → Reed (seed 1, 30 s) visits 17 of the 19
Starbucks in reach at 61.90×. A seventh golden route freezes a pilgrimage.
Hard mode is built (M3 step 5; `docs/reference/engine.md`, **Constraints**): `plan --hard` and
the API's `constraints.hard` keep each road (a ref, else the normalised name) in one stretch, so
once the route leaves a road it never comes back to it, alone or with a turn constraint: the
searches are banned the roads the route drives outside each window, the window check is exact,
a window drawn inside a road's stretch is widened to the stretch's ends (`Objective.window`),
the seed re-solves a leg that comes back to a road (a heuristic, which may refuse a trip or
move a stop where a legal route exists, and says only that it found no way: "try without
--hard"), and the validator proves the rule from the build's identities. On
Portland, Powell's → Reed reaches 9.68–11.97× under it (against 57.9–62.4× without), and 19 of
the 20 acceptance pairs plan. An eighth golden route freezes a hard-mode run.
The alphabet route is built (M3 step 6; `docs/reference/engine.md`, **Alphabet**):
`objective: "alphabet"` drives named roads whose letters run A, B, C, … (wrapping Z → A), the
first leg up to the first A road and the last leg to the destination free, equal letters in a
row counted once, through `plan --objective alphabet` and the API, which streams each better
route whole. It is a constructive search of its own beside the worsen loop: restarts from the
seed, each growing the route a letter a step by one `Route.splice`, over a new tree search on
both backends (`GraphBackend.dijkstra_edges_tree`); the validator recounts the letters and
proves their order. On Portland (seed 1, 30 s) Powell's → Reed rides A to M and Powell's → PDX
the NW Alphabet District, A to W; the 20 acceptance pairs all plan and validate, at a median of
1 letter (1–9). A ninth golden route freezes one.
The API is built (`docs/reference/api.md`, M2 step 2): `worst-route serve` runs a FastAPI app
that plans each request as a job in a worker process of its own, streams the route as it grows
over Server-Sent Events (splices, periodic snapshots, resumable with `Last-Event-ID`), stores
finished routes in sqlite, continues a stored route ("make it worse"), and on a cancel, a time
limit or a closed stream stores the best route so far once the validator passes it. On
Portland, Powell's → Reed streams to 62.4× in 11.8 s through it, the engine's own result for
that seed; a 9 h target from the same 13-minute drive ends within ±2 % in 1.8 s.
The frontend is built (`docs/reference/frontend.md`, M2 step 3): a Vite + TypeScript app
without a framework on MapLibre GL JS, in which the user clicks a start, an end and stops along
the way on the region's map, picks a mode (the longest, the slowest, collecting an item, a
brand's pilgrimage or the alphabet route), turns to avoid (left or right, soft or strict), hard
mode and a target (as bad as possible, a ratio or a drive time), runs a job and watches the
route grow from the stream (each splice applied and flashed, a colour gradient from start to
end, reconnecting with `Last-Event-ID`; the alphabet route's better routes each drawn in place
of the last), cancels, rerolls, makes the route worse, and opens a stored route by its
`?route=` link; the settings and stops live in the URL, every API error shows in a banner (a
strict turn constraint's with a button to try without strict, a refusal under hard mode's with
one to try without it), and leaving the page cancels a running job. The stat line shows the
slowest time's ratio of experienced times with the drive and the delays, the items collected,
a pilgrimage's visits ("17 of 19 Starbucks") and an alphabet route's letters (all live), and
the turns. On Portland, Powell's → Reed grows to 60.0× in 12 s on the map, and to "40.1× slower"
in 8 s under the slowest time; its tests replay streams recorded from the server, node for
node.
The geocoding proxy is built (M4's first piece; `docs/reference/api.md`, **Geocoding**): a
place search in the stop list (a field and a button, no type-ahead) asks `GET /geocode`, which
answers up to five places in the region from Nominatim behind a 30-day sqlite cache and a limit
of one request a second, under the project's User-Agent; a pick places the next stop as a map
click does. No test or agent run calls Nominatim: the tests use a fake geocoder or a mocked
transport, and every test refuses httpx's real transports.
The places index, the comparison lines and the share image are built (M4;
`docs/reference/presentation.md`): `tools/fetch_census.sh` fetches the Census cartographic
boundary files (2025, sha256-pinned), run by a person; `worst-route aux places` turns them into
the states, their outlines and the region's places (Portland: 68, Oregon's and Washington's),
which answer the states and towns a route passes through in route order, CDPs included; a
route's length picks one of fifteen deadpan comparisons; and `GET /routes/{id}/share.png` draws
the route as a 1200 × 630 PNG (the ratio, the comparison, the route and its baseline over the
build's roads and the state outlines, no basemap), once, then from a cache, with a link to it in
the share section.
The directions, the stats and the toasts are built (M4; `docs/reference/presentation.md`,
**Directions**, **Jokes**, **Stats and toasts**, **The document**): a route reads as GPS-style
steps ("Head east on West Burnside Street", "Turn left onto ...", "Slight right to stay on
North Multnomah Street", "Take the ramp toward I 5", "At the roundabout, take the exit onto
..."), each with its distance and time and its
range of edges, with forty deadpan jokes about every eighth step, drawn from the route's seed;
its extra fuel and cost (from the request's vehicle), the states and towns it passes through,
its share of the roads within reach, and up to ten toasts, the most striking first ("34.2× longer
than it had to be."). `GET /routes/{id}` carries them, worked out again on every
request and never stored, `GET /routes/{id}/directions` the directions alone, `worst-route plan
--out` writes them into route.json, and `worst-route directions route.json` prints the steps.
On Portland, Powell's → Reed (34.21×, 2,819 edges) reads as 1,532 steps, and its document
answers in under 0.1 s once the places index's polygons were prepared (0.26–0.38 s before).
The frontend shows them (`docs/reference/frontend.md`, **Using it**): a collapsible Directions
section lists the steps ("Turn left onto X · 0.3 mi · 2 min", stops bold, jokes in italics),
hovering one draws its edges on the map and clicking it pans there; a strip under the map
rotates waiting lines and the live ratio during a run, then the route's toasts; the stat line
gains the arrival time, the towns, the states, the extra fuel and its cost, and the share of
the roads within reach; a Vehicle row (24 mpg, $3.89 by default) and a Departure (empty: now) go
into every request and the URL; and the share section downloads the route's GeoJSON. With them
M4 is built.

The whole of Oregon is built too (M5.1; `docs/reference/pipeline.md`, **Oregon**;
`docs/reference/engine.md`, **On Oregon**): the same pipeline builds the state from its
Geofabrik extract in 6–11 minutes (237,581 nodes, 593,555 edges, a 174 MB build; cleanup peaks at
2.6–3.9 GB), and the build passes QA once connectivity is judged by a 98% share and by its
largest dropped component (at most 200 nodes) rather than a 99% share. `tools/bench_build.py`
runs a region's steps, each in a process of its own, and reports each one's wall and CPU time,
peak memory and output sizes as a table and a JSON report under `tools/out/`; the gate never
runs it. The automatic tier is the all-roads graph below 800 km (engine version 3), on which the
named trips plan at 4.03–14.00× in 30 s and 8 of 10 random statewide pairs at a median of 8.88×;
`worst-route serve --region oregon` keeps a job's worker under 400 MB.
The public-hosting preparation is built (M5.4; `docs/reference/hosting.md`): a container image
(`Dockerfile`, `compose.yaml`) serves the built frontend at `/` and the API under `/api` from one
process (`worst-route serve --frontend`), the data mounted read only and its state in a volume;
the map draws Protomaps' vector basemap from a file the API serves by byte ranges
(`WORST_ROUTE_BASEMAP=pmtiles`, named by `GET /config`; `tools/fetch_basemap.sh` cuts it from a
Protomaps build, with its labels' fonts and icons), on MapLibre GL JS 6, which carries no
advisory; `serve --public` gives each client one running job, three waiting and ten place
searches a minute, and budgets up to 60 s; every request has an id and, with `--log-format json`,
a JSON line with the client hashed, never its address; `GET /ready` says whether the build, the
store, the places index and the basemap are there; the map's attribution control and a footer
credit OpenStreetMap, Protomaps and the Census Bureau. Built and run with Docker on the dev
machine (1.57 GB), it serves Portland from the main tree's data mounted read only.
Serving several builds at once is built (M5; `docs/reference/api.md`, **Running it**, **Picking
a build**; `docs/reference/frontend.md`, **Using it**): `worst-route serve --region oregon
--region us-t1` loads a state's all-roads build beside the national T1 one, and `POST
/regions/pick` names the smallest served region that holds a trip's stops, by bbox and by the
Census state polygons: Portland → Bend runs on Oregon's all roads ("all stops in Oregon"),
Portland → Chicago on the national build ("stops in Oregon and Illinois"); the frontend asks it as
the stops change, names the build over the stop list ("Oregon · all roads", "United States ·
primary roads and up"), plans on it, reloads the brands with it, and lets the settings pin a
region; `/regions` names each region; each region's places index is its own, else a served one
covering its states; one basemap file serves every build. Through it, seed 1 in 30 s, Portland
→ Bend reached 17.70× on Oregon and Portland → Chicago 9.69× on T1, both validated.
The daily worst route is built (M6, the game loop's first piece; `docs/reference/api.md`, **The
daily route**; `docs/reference/frontend.md`, **Using it**): `GET /daily` gives one trip a day, the
same for everyone on a server, between two towns of the main build's places index drawn from the
UTC date, planned once on the day's first call with a fixed number of attempts (5,000), so every
server with the same build gets the same route, and recorded in the route store; the panel opens
with it ("Today's route": the date, the two towns, the ratio so far while the server plans it,
then the ratio, the share image and "Open it"), collapses once the user plans a route of their
own, and `?daily` opens it. On `us-t3` on 2026-10-31, Sageville, IA → Acworth, GA planned in
14–32 s to 6.58×, the same route on four servers.
Beat the engine is built (M6, the game loop's second piece; `docs/reference/api.md`, **Drawing a
route**; `docs/reference/engine.md`, **Drawing**; `docs/reference/frontend.md`, **Drawing**): the
player draws a route junction by junction under the engine's rules, against the engine's route for
the same stops. `POST /draw` answers each step from the path alone (the engine's `check_drawing`,
on `route.check_route`), with the legal next moves or `ILLEGAL_PATH` at the first illegal edge;
`POST /draw/finish` stores a finished drawing as a route like any other, validated, its run drawn
by a human; "Draw it" in the frontend shows the moves, takes the one clicked, and gives the verdict
against the par, a normal job. A step costs 8–11 ms at 2,000 edges on `us-t3`; a route drawn round
Powell's on Portland's version 8 build, 116 edges, came to 14.27×, and validated.
The challenges are built (M6, the game loop's third piece; `docs/reference/frontend.md`,
**Challenges**): a fixed list of ten in the frontend, each a region, a trip or any trip in the
region, the settings it fixes and a goal measured on a finished route, from 20 bridges in Portland
and its alphabet A to W (its only X roads are dead ends) to 10 Starbucks in Oregon, Portland to
Chicago at 4× and today's route made worse; "Set it up" (or `?challenge=<id>`) loads one for Go,
or Draw it for beating the engine by hand; every route shown and every drawing's verdict is graded
in the browser, a challenge completed kept with its day in the browser's storage, with a toast and
a badge. Each was set up and completed through a lane's server in headless Chrome, on stops and
thresholds measured at the panel's defaults.
The desktop app is built (M6; `docs/reference/desktop.md`): `worst-route-desktop` serves the
site's API and frontend on 127.0.0.1 at its own port (17429, a free one when another program has
it, so the web view's storage lasts between starts), only to a `Host` of 127.0.0.1 or localhost,
in a pywebview window on the system's web view (WKWebView on macOS, WebView2 on Windows; the
browser on Linux, and wherever the web view lacks WebGL 2), its job workers its own processes,
stopped with the window or Cmd+Q; frozen by PyInstaller in one folder with `us-t3`
build and the places indexes inside (a 1.0 GB app, a 313 MB disk image on macOS on Apple
Silicon, after pyarrow's 319 MB, an Inno Setup installer on Windows, a tarball on Linux), it compiles Numba's kernels and
builds matplotlib's font cache once, into the user's directory (a first start 4–22 s on the dev
machine, a second 1.7–5 s), draws OpenFreeMap's vector tiles until it has fetched its own
basemap (a Protomaps extract of the contiguous US to zoom 11, 684 MB, resumed and checked), asks
Nominatim through the API's proxy as one client under its own User-Agent, and plans the day's
route itself, the servers' own (`a076702bbc25` on 2026-10-31). Frozen on the dev machine, it
plans Portland → Bend at 10.5–11.6× and New York → Los Angeles at 3.9–4.7× in 30 s on `us-t3`,
every route validated, and stops leaving no process behind; `.github/workflows/release.yml`
builds and smoke-tests it on each platform on a tag (not yet run: the data pack is not uploaded).
OSM's turn restrictions are honoured (M6, pipeline version 8; `docs/reference/pipeline.md`,
**Turn restrictions**): `filter` keeps the restriction relations with one via node over kept
roads, and cleanup makes each a turn table at the junction holding its via node, exactly at a
plain node, through the edges a cluster absorbed, and inside a super-junction by stage B's walk
over its edges, which carries the twins along the paths a restriction leaves; the engine, the
splice and the validator, unchanged, read the tables as they read a super-junction's. On Portland
3,357 of 3,643 restrictions apply, on Oregon 6,057 of 6,493, on the national T3 build 174,534 of
230,695; a Portland trip from SE Tacoma St to SE 13th Ave under strict no-right, whose seed took
the left turn relation 1542924 bans, now goes on along Tacoma and comes round by 11th Ave and
Tenino St.
The worst order is built (M6; `docs/reference/engine.md`, **Seed and baseline**, **The worst
order**): a request's `reorder` (`plan --reorder`, the API's `reorder`, the frontend's "Worst
order" box) tries every order of the stops between the start and the end, each resolved in full
as a request in that order would be, and plans the one whose seed is the longest, the first
among equals, the given order's error raised as without it; the ratio is against the sensible
route through the stops in that order. At most 7 stops on a build of at most 100,000 nodes and 5
on a larger one, as measured (120 orders took 11.8–14 s on Portland's build, 10–102 s on
`us-t3`, 6 orders at most 7.3 s there). The route document, the `snapped` event (which waits for
the search) and route.json record the order, the stored route keeps its stops in it, make it
worse continues it without searching again, and the frontend's stop list says where each stop is
visited ("visited 3rd"). On Portland a 5-stop trip in town is visited 0, 3, 2, 1, 4 at every
seed, 655–700 km at 15.5–16.6× against 527–580 km at 14.6–16.1× in the given order; on `us-t3`
Portland, Seattle, Spokane, Bend, Boise becomes Portland, Spokane, Bend, Seattle, Boise, 3,743–5,539
km at 1.48–2.20× against 3,460–5,168 km at 1.84–2.75×, its baseline 2,522 km against 1,877 km.
Simulated-annealing acceptance is built (M6, engine version 6; `docs/reference/engine.md`,
**Annealing**): a max run that would stop as converged reheats instead, up to 3 times, taking a
detour that loses with probability exp(delta / T), T from 1 % of the baseline's primary
quantity, cooling ×0.99 per accepted splice and frozen below a thousandth of its start; until
its first reheat the run is the greedy one, draw for draw, so a run that stops on its budget
first is unchanged. Progress events carry the best route so far (`best_score`, `best_metrics`),
and the frontend's stat line shows "best 14.1×" while a heat dips below it. On Portland's
acceptance pairs (30 s) it makes a median of 29.96× against greedy's 26.05×, every pair at
least greedy's, most runs now using their whole budget.
Portfolio runs are built (M6; `docs/reference/api.md`, **Portfolios**): a job's `portfolio` K (2
to 4, 2 on a public server, never more than the server's workers) runs K searches of its request
at once, seeds `seed` to `seed + K − 1`, each in a worker of its own, the engine unchanged; the
client sees one route, the leader's, a search whose best is strictly better taking the lead at most
once in 2 s (a snapshot of its route, then only its splices), and the job stores the best route the
searches finish with, or the first to reach its target, under the winner's seed, so `plan --seed
<winner>` reproduces it; each search holds one of the server's workers until it ends, a public
client still running one job. The frontend's "Runs at once" setting sends it, and its stat line
names the leader's seed while it runs ("best of 4 runs · seed 42"). On Portland four seeds at 60,000
attempts each ended in 14.5 s on the best of the four seeds' own routes (63.01×, against 35 s for
the four one after another); on `us-t3`, with the dev machine swapping, two searches at once,
a portfolio's or two jobs', each ran at a fifth of one alone, so a portfolio of two did not beat
one seed in 60 s there (3.51× and 4.12× against 3.81×): on the national build a portfolio needs
memory for its workers.
The workflow scaffolding exists: `CLAUDE.md`, the four agents,
`tools/gates.sh`, `tools/land_series.sh`, this overview, `docs/PLAN.md`. The M1 bootstrap is
built: the uv project (Python 3.12, `src` layout, the M1 dependencies), the gate running ruff,
mypy and pytest for real, locally and in GitHub Actions, and the `worst-route` CLI (`version`,
and `pipeline <step>` for every step).

### 2.2 Roadmap

Milestones as `docs/PLAN.md` §7 lays them out; each ends with something runnable.

| Milestone | Status | Summary |
|---|---|---|
| M1 — Portland, distance mode, CLI | Built | bootstrap, every pipeline step (download to qa; Portland's build passes QA), the graph package and the engine (snapping, seed route, transition-aware searches, splice loop, distance objective, validator, `worst-route plan` / `validate`). The acceptance run (`docs/PLAN.md` §7 M1 step 9; `uv run python tools/acceptance.py --region portland --pair-seed 2026 --pairs 20 --seed 1 --budget 30` on build e4b3b4ad90db, `graph_version` 45f650e868fa, the reference backend: 20 random Portland pairs 3–15 km apart): all 20 plan and the validator passes all 20; the median ratio is 21.5× (3.3–45.2×); seeds 1 and 2 share 10–16% of their edges (Jaccard on 5 pairs, all < 0.3); the cul-de-sac via moves 86 m to the nearest through street with its note. Powell's → Reed gives 58–62×, PDX → Beaverton 7.3× (`docs/reference/engine.md`, **On Portland**) |
| M2 — API + map, live growth | Built | CSR + Numba backend built, its transition-aware kernels included (the default on every build; byte-identical to the reference: on Portland the acceptance's 20 routes and Powell's → Reed reproduce exactly, at 7,200–8,200 attempts/s); the API built (jobs in worker processes, SSE progress, the route store, make it worse, `worst-route serve`); targets by ratio and duration built (engine, CLI `--target`/`--base`, API), a 13-minute Portland drive to 9 h within ±2 % in 1.8 s over the API; the MapLibre frontend built (stops by click, the route growing live, run controls, settings with the targets, links). The acceptance (`docs/PLAN.md` §7 M2 step 5; 2026-10-31, the version 6 build 3a4a3df6269f, engine version 4, `worst-route plan --target duration:9h --budget 30`, `docs/reference/engine.md`, **Targets**): Powell's → Reed, a 13-minute drive (22 minutes with its delays), becomes 8 h 56 min of experienced time (−0.7 %, within ±2 %) at seed 1 after 3,354 attempts in 1.3–2.0 s of search (the whole `plan` process 3.1 s, 38 s once at a load average of 113), 8 h 51 min and 8 h 50 min at seeds 2 and 3 in 1.0–1.2 s, and Powell's → Multnomah Village (45.4790, −122.7080), a 14-minute drive, 8 h 49 min (−1.9 %) in 1.6 s at the API's 1,000,000 attempts (the CLI's 5,000 stop it at 8 h 34 min), every route validated, while two rural acceptance pairs of 13.5 and 16 minutes at the region's south-west edge converge short, at 7 h 13 min and 3 h 14 min, their leashed area holding no more; the route drawn growing and the job cancelled when the tab closes are proven by the frontend's recorded-stream replay (`map/routeLayer.test.ts`, "replaying the recorded stream") and its "leaving the page" tests (`run/controls.test.ts`, run on `pagehide`), and by the API's `tests/api/test_routes_api.py::test_closing_the_stream_cancels_the_job` |
| M3 — Modes and constraints | Built | built: the turn classes, never turn left / right (soft by default, `strict` for none; engine, CLI, API), the kernels refusing banned turn classes, slowest time (engine, CLI, API), collectibles (the collect objective for toll booths, level crossings, roundabouts and bridges in the engine, `plan --objective collect --item` and the API; `node_roundabouts` in the build; on Portland 24–30 bridges where the distance route passes 16–17); the frontend's controls for them (the Mode and Turns rows, the request, the URL, the stat line's time ratio, delays, items and turns, the strict refusal's retry); the brand pilgrimage (the brand index at `enrich`, saved as build arrays at pipeline version 3; `plan --objective brand --brand`, the API's `brand` and `GET /brands`; on Portland 17 of the 19 Starbucks in reach); the frontend's controls for the brand pilgrimage, the alphabet route and hard mode (a Brand mode with its picker, an Alphabet mode, a Hard mode checkbox, the request, the URL, the visits and letters live in the stat line, the alphabet route's better routes drawn in place, the hard-mode refusal's retry); on Portland strict no-left plans 7 of the 20 acceptance pairs (engine version 4) and soft no-left 20 at a median of 5.33× (§2.3; on a build re-enriched at pipeline version 2, whose bearings describe the roads, not cleanup's stubs); hard mode (engine, CLI, API; on Portland 19 of the 20 acceptance pairs plan under it, at a median of 4.73×); the alphabet route (its own search, a new tree search on both backends, `plan --objective alphabet` and the API; on Portland Powell's → Reed A to M, Powell's → PDX A to W, the acceptance pairs a median of 1 letter); the search honouring stage B transitions was built at M1. The acceptance (`docs/PLAN.md` §7 M3 step 7; 2026-10-31): each mode's invariant has its fixture tests, on every route along a run, and the validator's `constraints`, `alphabet` and `metrics` checks prove it of any route: no left turn under strict no-left (and no right under strict no-right; `tests/engine/test_constraints.py::test_the_strict_loop_never_turns_the_way_the_constraint_refuses`, `tests/engine/test_validator.py::test_a_strict_turn_constraint_is_proven_from_the_bearings`), each road in one stretch (`tests/engine/test_hard_mode.py::test_the_loop_under_hard_mode_keeps_every_road_one_stretch_on_both_backends`, `tests/engine/test_hard_mode.py::test_the_validator_proves_each_road_one_stretch`), the letters strictly in order (`tests/engine/test_alphabet.py::test_every_route_along_the_way_is_legal_and_better_than_the_last`, `tests/engine/test_validator.py::test_a_route_out_of_order_fails_the_alphabet_check`), and every counted item and brand visit on the route's own edges and nodes (`tests/engine/test_collect.py::test_count_items_counts_every_item_on_a_route`, `tests/engine/test_collect.py::test_a_run_collects_and_never_gives_an_item_back`, `tests/engine/test_validator.py::test_reported_items_are_recounted`, `tests/engine/test_brand.py::test_the_validator_recounts_a_reported_brand`); the goldens `grid20_noleft` (soft, 7 left turns kept at M3; 5 since engine version 5 widened the windows), `grid20_hard`, `grid20_alphabet`, `grid20_collect_bridge` and `grid20_brand` hold on both backends; `uv run pytest tests/engine -k "constraint or hard or alphabet or collect or brand"` passes 186 (8 skipped, the Portland data tests, which pass all 13 of `tests/engine/test_portland_data.py` on build 3a4a3df6269f), `uv run pytest tests/golden` 18 |
| M4 — Presentation | Built | directions, stats, toasts, comparisons, share image, geocoding proxy, share links; built: the geocoding proxy (`GET /geocode`, Nominatim behind a cache and 1 request/s) and the place search in the stop list; the Census places index (`tools/fetch_census.sh`, `worst-route aux places`: the states and towns a route passes through), the comparison lines, and the share image (`GET /routes/{id}/share.png`, linked from the share section); the directions with deadpan jokes and the stats and toasts in the route documents (`GET /routes/{id}`, `GET /routes/{id}/directions`, route.json, `worst-route directions`); the frontend's directions panel (a step's edges highlighted on the map), toasts under the map, extra stat rows and vehicle setting; the departure setting with the arrival time, and the GeoJSON download |
| M5 — Scaling up | In progress | built: the compact build format (format 4, pipeline version 6: on Portland the arrays the engine maps 15.0 MB where format 3's were 39.1 MB, the whole build 17.2 MB where it was 39.9 MB, every acceptance route the same; the shapes simplified to 2 m apart, read only to draw a route; the validator's OSM-node proof from the nodes' members); Oregon (M5.1; `docs/reference/pipeline.md`, **Oregon**; `docs/reference/engine.md`, **On Oregon**): the whole state builds in 6–11 min with the OSMnx path (237,581 nodes, 593,555 edges, 174 MB; peak 2.6–3.9 GB, in cleanup), measured by `tools/bench_build.py`, and passes QA (connectivity judged by a 98% share and its largest dropped component, at most 200 nodes); the automatic tier is the all-roads graph below 800 km (engine version 3), giving Portland → Bend 10.47×, Astoria → Ontario 4.03×, Eugene → Portland 14.00× and Medford → Portland 8.17× in 30 s, and 8 of 10 random statewide pairs at a median of 8.88×; served, a job's worker peaks at 374–399 MB. built: the public-hosting preparation (M5.4; `docs/reference/hosting.md`): the image serving the frontend and the API from one process, the Protomaps basemap, per-client limits, JSON logs with request ids, `/ready`, the attribution, MapLibre GL JS 6. built: M5.2, the array pipeline (`docs/reference/pipeline.md`, **The arrays engine**): every step from `build` to `save` on arrays, the default engine (`--engine networkx` for the other), its stages directories of `.npy` files, the same build byte for byte (`tools/parity.py`; on Oregon cleanup 5 s against 91 s, enrich, tiers and save 6.5 s against 23 s). built: US at T1 (M5.3; `docs/reference/pipeline.md`, **Regions of several states**; `docs/reference/engine.md`, **On the national T1 build**): regions of several states, merged at `filter` (Oregon and Washington stitched across the Columbia: 66 of the 118 roads Oregon's extract cut off in Washington continue), a class ceiling, and the 48 contiguous states and DC at T1 from the 49 extracts: filter 12 min, build and cleanup on arrays 4 min (on networkx, cleanup did not finish in 2 h), the whole build in 22 min; 887,737 nodes, 1,324,013 edges, 178.1 MB of arrays and strings (201.2 MB with the geometry), QA passed; masks 12–138 ms (8–13 ms since the leash is NumPy's); cross-country trips, refused at engine version 4's automatic tier, plan at engine version 5's (T1 from 800 km, a fifth of the trip as leash, windows widened to junctions, seed legs by a tree over bans) at 6.19–10.26× in 60 s, and 9 of 10 random town pairs 800–4,500 km apart at a median of 11.77× (`docs/reference/engine.md`, **On the national T1 build**). built: several builds served at once, each trip on the smallest holding its stops (`serve --region` repeated, `POST /regions/pick`, the frontend's header and Region setting; `docs/reference/api.md`, **Picking a build**). measured once: the per-state all-roads builds, a measurement of the array pipeline at state scale, not served (`docs/reference/pipeline.md`, **The state builds**): the 48 contiguous states and DC by `tools/build_regions.py --states` in 47 min, 18.07 M nodes and 45.8 M edges in 4.8 GB, at most 8.51 GB of memory (Texas); 32 pass QA and 17 drop what a single extract leaves them. Decided (the owner, 2026-10-31): the national build at T3 (`us-t3`), which the server serves, with Portland and Oregon as the development regions; all-roads city builds deferred (§3.2). built: the national T3 build (`us-t3`; `docs/reference/pipeline.md`, **The national T3 build**): 2,154,537 nodes and 4,412,885 edges, 586.6 MB, in 21.5 min at a peak of 16.84 GB (cleanup), QA passed; 99.1% of the 31,540 towns have a node within 5 km (T1: 81.8%); New York → Los Angeles 5.98× and Portland → Chicago 9.46× at the automatic tier in 60 s. Remaining: the all-roads city builds, deferred |
| M6 — Stretch | In progress | built: the desktop app (2026-10-31; `docs/reference/desktop.md`): `worst-route-desktop`, the site's API and frontend on 127.0.0.1 in a pywebview window (macOS on Apple Silicon and Windows; the browser on Linux and wherever the web view lacks WebGL 2), frozen by PyInstaller with `us-t3` and its places index inside (Portland's build left the app on 2026-11-02) (a 1.0 GB app, a 313 MB disk image, after pyarrow's 319 MB), Numba's kernels compiled on a computer's first start into the user's directory, after the server listens (on the dev machine, 2026-11-01, the server listening about 1 s after a first start's launch and its kernels ready 2.7–3.4 s after it listens, a later start listening at 0.7 s, where they were 4–22 s and 1.7–5 s with the kernels made first; Portland → Bend 10.5–11.6× and New York → Los Angeles 3.9–4.7× in 30 s on `us-t3`, every route validated; a job's worker 512–1,072 MB resident on `us-t3`, its build's mapped pages counted; the day's route the servers' own), OpenFreeMap's vector tiles until its own basemap (a Protomaps extract of the contiguous US to zoom 11, 684 MB) has been fetched, and a release workflow building and smoke-testing the installers on a tag (not yet run: the data pack is not uploaded, and where the public downloads from is the owner's to decide, §3.2); the daily worst route, the game loop's first piece (`GET /daily`, one trip a day planned once on the main build from the date, the same on every server with the build; the frontend's "Today's route" and `?daily`; on `us-t3`, Sageville, IA → Acworth, GA at 6.58× in 14–32 s on 2026-10-31); beat the engine, the game loop's second piece (`POST /draw` and `POST /draw/finish`, a route drawn by hand under the engine's rules, checked from the path on every step and stored like any other once finished; the frontend's "Draw it" against the engine's par, a 10× target run; 8–11 ms a step at 2,000 edges on `us-t3`); the challenges, the game loop's third piece (`docs/reference/frontend.md`, **Challenges**: ten goals over the modes on Portland, Oregon and `us-t3`, set up from the panel or `?challenge=`, graded in the browser on every route shown and every drawing's verdict, the badges in its storage; the desktop app on a port of its own so they last). built: OSM turn restrictions (pipeline version 8; `docs/reference/pipeline.md`, **Turn restrictions**): the filter's restrictions table, applied at cleanup as turn tables at the junctions, exactly through clusters and super-junctions on both engines; on Portland 3,357 of 3,643 apply and the build holds 19,042 transition rows at 642 super-junctions and 1,334 restricted junctions, on Oregon 6,057 of 6,493, on `us-t3` 174,534 of 230,695 (its build 36.5 min, QA passed); Portland's acceptance pairs all plan and validate at a median of 23.93× (24.30× on the version 7 build in the same session). Built: the worst order (`plan --reorder`, the API's `reorder`, the frontend's "Worst order"; `docs/reference/engine.md`, **Seed and baseline**): every order of up to 7 stops tried (5 on a build of more than 100,000 nodes), each resolved in full, the longest seed planned, the ratio against that order's baseline; 120 orders 11.8–14 s on Portland's build, 6 orders at most 7.3 s on `us-t3`; a 5-stop Portland trip 655–700 km (15.5–16.6×) in its worst order against 527–580 km (14.6–16.1×) as given. Built: simulated-annealing acceptance (engine version 6; `docs/reference/engine.md`, **Annealing**): a converged max run reheats up to 3 times and takes shorter splices with a cooling probability, the greedy run its exact prefix; progress events carry the best route so far and the stat line shows it; on Portland's acceptance pairs a median of 29.96× against greedy's 26.05× (30 s, interleaved), every pair at least greedy's, the median run 30 s against 9 s. Built: portfolio runs (the API's `portfolio`, the frontend's Seeds; `docs/reference/api.md`, **Portfolios**): K seeds of one request at once, each a worker, the leader's route streamed, the best stored under its seed; on Portland four seeds at 60,000 attempts in 14.5 s, the best of their own routes (63.01×); on `us-t3`, the machine swapping, two searches at once each at a fifth of one alone. Built: the certified worst (2026-11-01; `docs/reference/engine.md`, **Certified worst**): `plan --certified`, the API's `certified`, the frontend's "Certified" box; the provably longest legal route for a trip's stops within an area of at most 300 nodes and 1,000 edges (the automatic leash narrowed to an area of at most 600 edges, recorded in the request), solved by OR-Tools' CP-SAT (4 threads with its `max_lp` subsolver) from the seed route, every better route streamed whole with the solver's bound, the route stored with `run.certified` (proven, or the longest found and how much longer the worst possible may be); on Portland's short trips in town at the automatic leash and the default budget, 10 of 14 proven in 0.1–17 s, the others within 0.75–4.7 %, each 1.04–1.32× the loop's converged route; OR-Tools imported only by a certified job's worker (0.3 s, about 55 MB). Planned: scoring, leaderboards |

### 2.3 Known limitations

The risks the plan anticipates are `docs/PLAN.md` §8.3. Found on the Portland build:

- **The transition-aware search's second look is a heuristic**: when the cheapest legal way
  passes a node twice, a detour's search is made again with the edge that comes back to the node
  banned, up to 8 times (engine version 4; `docs/reference/engine.md`, **Searches**), and can
  still miss a longer node-simple legal way, so a detour attempt can fail where one exists. A
  seed leg's is a tree over bans (engine version 5) that misses one only when its searches run
  out, so a leg can still fall back (a re-snapped stop). On the acceptance pairs (the version 6 build,
  2026-10-31), 40 of about a million detour searches met such a walk: 15 now give a path, 25 find
  no walk once the edge is banned (before, 42 met one, each a failed attempt). No seed leg met
  one, and every seed is what it was.
- **Most of the road a request allows stays unused**: the loop converges before its budget on
  every acceptance pair (quartiles 9× and 27×); the remaining levers are the search's
  (`docs/reference/engine.md`, **On Portland**), and a faster backend helps only runs that hit
  the budget.
- **A twin false positive at hairpins** (cleanup): two consecutive one-way edges of one road
  that bend back on themselves are twinned with each other when the bend is split between two
  OSM ways, which the shared-way exclusion does not catch (an OR 224 Sunrise Expressway hairpin,
  ways 842904309 and 842904310), so the hairpin can never be driven; the acceptance pair that
  failed on it plans now, around it.
- **Delays mostly undercount**: of Portland's 5,960 stop signs, 4,471 are mapped on the
  approach as shape points, as are 829 of 3,377 signals, and add no delay in M1.
- **Junction collectibles** count on the node's lowest-id out-edge only, and **bearings** at a
  cluster or super-junction included the stub to its centre: 15% of the edges leaving a stage A
  cluster had a `bearing_out` more than 30° off the road's own, 55% at interchanges, whose
  stubs run 66 m (median) to the centroid. Both are M3's to refine, before turn modes; the
  bearings are refined (pipeline version 2): they describe the road less the stub, and a turn
  at a merged node classes as the road's in all but 0.2–0.5% of cases, the coordinates'
  rounding, as at plain nodes (`docs/reference/pipeline.md`, enrich).
- **Strict turn constraints are rarely satisfiable** on a street grid together with the rule that no
  junction repeats: a net left turn without left turns is three rights round a block, which comes
  back through its junction. On Portland strict no-left plans 7 of the 20 acceptance pairs and
  strict no-right 3 (engine version 4; Powell's → Reed plans under strict no-left, its end moved
  214 m, and refuses under strict no-right). **Soft ones make routes less bad**, since nearly
  every detour adds such a turn: medians of 5.33× (no-left) and 6.47× (no-right) on the same pairs
  at a kilometre a turn (`TURN_PENALTY_M`), against 21.5× without (1.12× and 1.10× at 10⁶). Under
  `collect` and `brand` a turn costs the same kilometre, against an item's 10⁶, and still about
  halves the avoided side's turns a km (Powell's → Reed collecting roundabouts under no-right: 1.33
  right turns a km against 2.96 without, 43 of 44 rings kept; a Starbucks pilgrimage 1.12 against
  3.05, 18 Starbucks against 17), but leaves them about as common as the other side's (1.43 and 1.17
  left a km): a route that never repeats a node tends to turn left about as often as right, its
  turns adding up to a small net change of heading unless it spirals; charging a fraction of an item
  instead bought nothing (at the review: a tenth gave the same densities on shorter routes, more
  lost items). Under the slowest time a left turn costs 90 s (`TURN_PENALTY_S`), and soft no-left
  gives Powell's → Reed 28.48× the experienced time with 1.09 left turns a km, more than the
  distance mode's 0.62, since a detour's time gain counts its delays too; 180 s and 300 s keep them
  rarer but at 11.66× and 4.67×. **A soft route barely shrinks** toward a smaller target, since
  shrinking may not add a net avoided turn and a shorter way round mostly needs one: Powell's →
  Reed's 16.77× soft no-left route, sent toward 3×, converges at 11.04× (`stopped_because:
  "converged"`, outside the band). That is the price of the rule, accepted by the coordinator at
  M3's review; letting a shrinking splice add an avoided turn when the route's density of them does
  not rise is a later option (`docs/reference/engine.md`, **Constraints**, **Limitations**, **On
  Portland**).
- **Collecting is bounded by the area and by the no-repeat rule**: a collect run draws its
  waypoints from the item's places in the request's area, so a route collects nothing it cannot
  reach within the leash (Powell's → Reed's leash of 3.6 km holds 45 rings, 144 bridge roads and
  no toll booth); an item on a dead-end road or one behind a cut edge cannot be passed without a
  stop beyond it (Portland's two toll booths, on roads into Barton Park and Meldrum Bar Park,
  stay out of reach even with a 30 km leash, where waypoints drawn at them hold the run to
  129.96× against distance's 176.93× over 30,000 attempts). Where an item is common, a long
  distance route can pass about as many by chance (level crossings, seed 2: 45 collecting
  against 52).
- **Most alphabet routes stop at A or B**: a road of the next letter must be reached over free
  connectors (unnamed edges, ramps, numbered streets: 29% of Portland's edges, in scattered
  pieces) and roads of the letter just completed, which outside the grids of numbered avenues it
  seldom is. On the 20 acceptance pairs (seed 1, 30 s) the median is 1 letter (1–9); the named
  trips through the alphabet streets carry the joke (Powell's → PDX A to W, Powell's → Reed A to
  M). Loosening the connectors further (a short stretch of any road, say) is a later option
  (`docs/reference/engine.md`, **Alphabet**, **Limitations**).
- **A possible stage B over-restriction**: the I-84 / NE Halsey super-junction (node 65437 of
  the 2026-10-30 Portland build) forbids NE 42nd Ave → NE Sandy Blvd, a turn that looks
  drivable; to investigate in the next cleanup fold.

Found on the Oregon build (M5.1, 2026-10-31; the inputs to M5.2's decision):

- **Some junctions still forbid a movement drivable on the ground, and the search then gives
  up.** Pipeline version 5 fixed the defect found at Woodburn (`docs/reference/pipeline.md`,
  **cleanup**, step 4): stage B now joins two clusters a carriageway leaves and comes back to, and
  its pockets follow a road by its name as well as its refs, so northbound I-5 and the Newberg
  Highway both go straight through Woodburn's interchange; Medford → Portland and Eugene →
  Portland plan on T1, and the random pair refused at Valley Junction plans
  (`docs/reference/engine.md`, **On Oregon**). Roads that leave a super-junction and come back to
  it with the pass not in its table (`lost_through`'s count: every road class, every branch) fell
  from 20 at 19 super-junctions to 12 at 11 on Oregon, and from 9 to 7 on Portland. Named
  limitations left, each for a brief of its own:
  - **Pockets longer than 600 m**: I-5 and US 30 by the Rose Quarter (1,497 m), US 26 by Banks
    (1,302 m), Old Highway 30 at Meacham (1,378 m), US 97 by Sunriver (3,456 m).
  - **Roads without a ref that leave under one name and come back under another**, which no
    pocket follows: North Argyle Way / North Columbia Boulevard, North Greeley Avenue / North
    Going Street, Northwest Zion Church Road / Northwest Glencoe Road, South Barlow Road / South
    Arndt Road, Northeast Cascades Parkway by Northeast Mount Hood Avenue, Southeast 91st Avenue
    round the block by Southeast Tolman Street and Southeast 90th Avenue (Portland); Southwest
    Country Club Drive / Southwest 35th Street (Corvallis).
  - **Cross-street turns a super-junction forbids** though they look drivable: NE 42nd Avenue onto
    NE Sandy Boulevard at I-84 and NE Halsey (Portland), French Prairie Road onto McKay Road by
    I-5 near Donald, which refused one random statewide pair on T4 until the search looked again
    for a node-simple path (engine version 4: it plans, 5.19× on the version 6 build
    e78224b5cf9a; `docs/reference/engine.md`, **On Oregon**).
  - **The transition-aware search's second look can miss** a slightly longer node-simple path
    where the cheapest legal walk enters a node twice (the search's second look, above).
- **The leash is tight where roads wind**: two rural points 8.8 km apart and 32.4 km apart by road
  have no route inside their 5.3 km leash (0.6 × the trip); the factor is kept, since it shapes
  every Portland route.
- **All-roads runs are bound by their budget at state scale**: 200–1,400 attempts a second on
  100–150 km leashes (Portland: thousands), so every trip over about 60 km stops on its 30 s and
  goes further with more. Stage A (OSMnx) is a third of the pipeline's time (133–178 s of 6–11
  min) and cleanup's networkx graphs its peak memory (2.6–3.9 GB): fine for a state, the reason
  for M5.2 nationally. A request's masks are computed over the whole build, the leash's
  distances by NumPy (1 ms on Oregon's 237,581 nodes, 4–10 ms on the national T1 build's 887,737,
  where shapely's had taken 83–166 ms and 22–159 ms).
- **The tiers above a state's span are untested on all roads**: T4 below 800 km and T1 beyond
  (engine version 5, tuned on the national T1 build, which holds T1's roads only); T0, T2 and T3
  are taken only when asked for. A rural stop's 3 km bubble seldom meets the T1 or T2 network (a
  stop in the Ochoco National Forest is 7.1 km from it).

Found on the national T1 build (M5.3, 2026-10-31; `docs/reference/engine.md`, **On the national
T1 build**; the inputs to the decision gate, §3.2):

- **Cross-country trips were refused at the automatic tier** (engine version 4): on T0 with a
  150 km leash, no motorway or trunk route from Portland to Chicago or from Miami to Seattle stays
  within the leash, and from New York to Los Angeles, reachable within the masks, the seed's
  transition-aware search finds no legal path. On the whole T1 network Portland → Chicago and New
  York → Los Angeles plan, at 1.11× and 1.26×, converging in seconds; Miami → Seattle stays out of
  reach of a 150 km leash. Since engine version 5 they plan at the automatic tier (T1, a fifth of
  the trip as leash) at 6.19–10.26× in 60 s, and 9 of 10 random town pairs over 800 km at a
  median of 11.77×; a pair whose straight line crosses Canada (North Dakota to Maine) is refused
  for its leash, and an attempt at a radius of hundreds of kilometres can take seconds, so a run
  can make as few as 150 attempts in its minute.
- **A T1-only graph refuses one town in five as a stop**: 81.8% of the Census places have a node
  within 5 km a trip can start from (52–55% in the Dakotas and Nebraska).
- **The share image's cities are thin on a national map**: drawing every T1 road in the
  bounding box of a 4,168 km route took 3.9 s (the T0–T2 thinning kept them all in a T1
  build); the image now draws at most 100,000 road units, the longest of the 385,356 motorway
  and trunk units there, in 0.64 s (`docs/reference/presentation.md`, **Share image**).
- **Washington's ferry islands fail the 200-node connectivity rule**: San Juan Island (1,154
  nodes), Vashon–Maury, Lopez, Orcas, Anderson Island and the Point Roberts exclave; the rule is
  kept, the measurement builds run with `--allow-fragmented`, and the thresholds are revisited at
  the decision gate.
- **The no-turn rule's second round of connectivity** costs 162 s on Oregon and Washington's
  networkx graph against 17 s for the first; the array pipeline's connectivity removes it.

Found with the turn restrictions (M6, pipeline version 8, 2026-10-31;
`docs/reference/pipeline.md`, **Turn restrictions**):

- **Some restrictions are not applied**: those over a via way (255 on Portland's and Oregon's
  extract, 11% of the country's restriction relations), conditional ones and those for some
  vehicles only, left out at `filter`; and at cleanup those whose via node simplification
  contracted into a road (279 on Portland, 400 on Oregon, 54,437 on `us-t3`, where a T3 road's
  junction with a street the ceiling drops is contracted), nearly all a U-turn on one road or a
  straight-on that is the only way, but for a few that ban that way on or mandate a turn onto the
  dropped street (1 on Portland, 4 on Oregon, about 425 on `us-t3`), which a route may then make.
  Keeping such via nodes as junctions would be a simplification change. A `from` or `to` way
  passing through the via node is told apart by the turn the value names, and left out when it
  names none or no edge turns so, as OSRM does (3 on `us-t3`).
- **Restrictions slow the loop on sparse graphs**: around a banned turn the cheapest legal way is
  often on and back, a walk that passes the junction twice, which the searches look at again up
  to 8 times (`docs/reference/engine.md`, **Limitations**, Transitions). On `us-t3` New York →
  Los Angeles made 1,602 attempts in 60 s for 3.81× where the version 7 build made 6,630 for
  5.88× on the same machine, back to back; a 60 s run there swings with the load (37 to about
  2,000 attempts on the version 8 build in one session of the review).
- **A restriction can expose a lost through movement**: eastbound I-84 east of I-205
  (Portland), where a ramp chain from it back to it is one super-junction whose mainline between
  lies outside it, longer than a pocket, could pass it only by the ramps, from one onto the other,
  which relation 1706307 bans; no route drives eastbound I-84 through there since version 8. The
  remedy is stage B's (a pocket along a mainline longer than `STAGE_B_POCKET_M`), not the
  restriction's.
- **The baseline may take a banned turn**: where a leg's cheapest legal way passes a node twice
  (on and back to avoid a no-left, say) the baseline is the turn-blind shortest path, a lower
  bound (`docs/reference/engine.md`, **Seed and baseline**), so its length, the ratio's
  denominator, can be that of a turn the route may not make; the route never makes it.

In the frontend (`docs/reference/frontend.md`, **Limitations**):

- **The dev server's proxy does not end a stream whose upstream drops**: if the API dies
  mid-run, the page keeps showing the run as going; a stream dropped between the browser and
  the server reconnects. No watchdog is planned.

## 3. Decisions

### 3.1 Recorded

Product decisions the owner made on 2026-10-30 while the plan was written:

- **Portland, OR is the prototype city; Oregon the prototype state** (243 MB Geofabrik extract).
- **A route crossing itself on an overpass or underpass is not a violation.** The rule is about
  nodes and road units, not geometry; two mainlines crossing with no ramps share no node.
- **Hard mode matches highways by `ref` first, then `name`**, both normalised; identity is the
  highest-class ref when several apply.
- **Stops are visited in the given order.** Reordering is a possible later toggle (M6). Since M6
  (2026-10-31, the coordinator's brief, its plan approved) a request may ask for the worst order
  (`reorder`, off by default, so no route planned without it changes): every order of the stops
  between the start and the end is resolved in full (with the re-snap fallback, so a stop that
  must move does not leave only the given order) and the longest seed wins, the first among
  equals; the given order must itself be plannable; the baseline, and so the ratio, is the chosen
  order's (the ratio does not count what the order adds; a ratio over the given order's or the
  best order's baseline would need a baseline per order and break make it worse's continuity);
  at most 7 stops on a build of at most 100,000 nodes, 5 on a larger one, from the search's
  measured cost, never bounded by time; not under the alphabet route; a continuation keeps its
  base route's order.
- **Local only now, designed for public hosting later**: tile provider, geocoder, rate limits
  and a deployable shape are planned, not built early.
- **OSM turn restrictions are ignored for now**; they plug into the same transition table as
  super-junctions later (`docs/PLAN.md` §3.13). Since M6 (pipeline version 8, 2026-10-31, the
  coordinator's brief) they are honoured: each restriction with one via node bans its moves at
  the stage A node holding it, composed through a cluster's absorbed edges and, by stage B's walk,
  through a super-junction; via ways, conditional restrictions, those for some vehicles only and
  those that except cars are left out (`docs/reference/pipeline.md`, **Turn restrictions**).
- **Turn constraints are soft by default, and a banned turn costs a kilometre** (decided by
  the coordinator at M3 from the Portland numbers; the owner may revisit (strict planned 3/20
  and 1/20 pairs; at 10⁶ soft gave 1.1×)): never turn left makes each left turn (and U-turn) a
  pair term of minus a kilometre's worth of the objective's score (1,000 m, `TURN_PENALTY_M`;
  90 s under the slowest time, `TURN_PENALTY_S`), the searches avoiding them where they can,
  the seed unconstrained; `strict` keeps none at all, every search refusing them, the validator
  proving it, and an unreachable stop saying so. Never turn right is the same for right turns;
  both at once is refused. A ratio target under a constraint compares the objective's primary
  quantity, and under the slowest time that is the experienced time, driving and the delays at
  signals and stop signs, over the baseline's; a duration target measures the experienced time
  under every objective, and so do the time ratio and the arrival the API and the CLI show, the
  driving time and the delays being reported apart (decided by the coordinator at M3's review:
  what the driver sits through).
- **Frontend is vanilla TypeScript + Vite with MapLibre GL JS**, not Leaflet, for GPU rendering
  of long live-updating routes and a self-hostable vector basemap (`docs/PLAN.md` §5.1).
- **uv with Python pinned to 3.12.**

Technical decisions the plan makes (the coordinator, 2026-10-30; any can be reversed as a tier A
brief):

- **The graph is CSR arrays behind a `GraphBackend` protocol**: a pure-Python reference
  backend in M1, Numba kernels in production from M2, networkx only as the tests' oracle.
  igraph/rustworkx are not expected to be needed: the hot loop is masked, bounded Dijkstra,
  which they do not offer without copying.
- **OSMnx for stage A consolidation, not for loading or simplification**: its
  `simplify_graph` would lose the shape points, so simplification is our own; array-based
  replacements for simplification and `consolidate_intersections` at national scale (M5).
- **Stage A places each cluster from its own buffers** (M5.2, 2026-10-31, pipeline version 4):
  at the centroid of the union of its members' buffers, or of their points when OSMnx's edge
  test split their group, as OSMnx does. OSMnx's own position comes from one union of every
  buffer in the region, whose last bits depend on the whole graph, which an array pipeline
  cannot reproduce at scale; placing each cluster from its own buffers in both pipelines keeps
  their builds byte-identical. On Portland it moves 3,977 clusters by at most 1.5e-8 m, which
  changes about 3,200 node positions and six float32 lengths and times, and nothing else.
- **State extracts, never the full US file**; cross-border ways deduplicated by id. As built at
  M5.3 (2026-10-31, at its plan): a region names its Geofabrik states and an optional class
  ceiling (`T0`–`T3`); `filter` merges the extracts in process (a border way taken once, from the
  first extract in the region's order; each node once, its newest version) and warns when their
  data dates differ; `us-t1` is the 48 contiguous states and DC at T1 in EPSG:5070, and `us`
  stays the name of an all-roads national region.
- **On-disk format: one `.npy` per array + `strings.json` + `meta.json`, memory-mapped**; not
  GraphML or pickle, and not NPZ (whose members NumPy cannot map) or Parquet for the graph.
- **The compact build format (format 4)** (2026-10-31, the owner's, before any state or national
  build is kept): `arrays/` holds only what the engine and the validator read, without changing
  a route. The shapes leave for `geometry/`, which only the drawing of a route reads (never the
  engine, the validator or a job's worker), each simplified by Douglas–Peucker to 2 m and stored
  as int16 whole-metre steps from its tail, a step over 32 km split; a drawn line lies within
  2.71 m of the road, and the lengths and bearings stay measured on the whole shape. The
  validator proves a route passes no OSM node twice from the nodes' members alone, `save`
  checking on every build the premise that lets it ignore the nodes inside edges; the OSM node
  and way ids along the edges and `node_state` leave the build. Nodes keep x/y and derive
  lon/lat (deriving x/y instead moved the merged nodes' x/y by 2e-9 m, enough to change golden
  routes); `edge_time_s` stays stored (derived from the float32 length it differed in its last
  bit on 41% of Portland's edges); the speed is a uint8 index into the build's table of distinct
  speeds (they are whole mph, not whole km/h), at most 256; `geom_indptr` is int32; GeoJSON
  keeps 7 decimals. `meta.json` carries `format: 4` and the geometry's own digest,
  `geometry_version`, outside `graph_version`; a build of another format is rebuilt.
- **Consolidation in two stages**: OSMnx stage A with per-node tolerances (1 m on roundabout
  rings, which stage B owns; 15 m on arterial crossings; 8 m elsewhere; `max_length=60`, dead
  ends kept) and a custom stage B that turns interchanges and roundabouts into super-junctions
  with allowed-transition tables, computed from M1 because `Route.splice` enforces them.
  Stage B joins the ramp components of one interchange when their attachment roads share two
  identities within 500 m, a slip-lane cluster takes in the at-grade crossing it bypasses
  (reached along two different roads), and every cluster takes in its pockets, a stretch of one
  road of at most 600 m that leaves it and comes back (2026-10-30, at the cleanup plan and its
  review). Since pipeline version 5 (the coordinator, 2026-10-31, at the plan found on the Oregon
  build), two clusters are one when a carriageway (motorway, trunk or primary, followed OSM node
  to node with one highway and its ref set, or its normalised name when it has no ref) leaves
  one, passes the other and comes back to the first within 1.5 km, from attachment nodes within
  500 m of each other; and a pocket follows one road by its name as well as by its refs, so a
  road whose ref changes on the way is one road.
- **Divided carriageways are linked as twins** detected on the stage A graph's edge segments
  (same ref/name and, motorways aside, the same name less directional words when both have
  one, so couplets are not twins; opposite bearing; side by side within a lateral distance by
  class; never two pieces of one OSM way), carried through stage B to the super-junctions'
  boundary edges along the same carriageway and to the transitions that must drive a twinned
  stretch inside them, a passage that must drive both carriageways inside a junction banned, and
  banned conservatively, not merged.
- **Tiers T0–T4 as masks on one master graph**, auto-selected by trip length, with local
  all-roads bubbles around stops and a leash along the stop polyline. As tuned on the Oregon
  build (M5.1, 2026-10-31, engine version 3): T4 below 800 km, T0 beyond, T1–T3 by override;
  the leash (0.6 × the trip, 3–150 km) and the 3 km bubbles unchanged. Accepted by the
  coordinator for now, with the version bump; revisited at M5.3 with national data: as tuned on
  the national T1 build (2026-10-31, engine version 5, the coordinator at its plan), T1 from 800
  km and T0 by override only, the leash a fifth of the trip when that is more than 150 km (from
  750 km), a request's own leash still at most 150 km; since 2026-11-01 a request's own leash is
  at most 5,000 km (`LEASH_MAX_KM`, the owner: a typed leash could not pass the automatic one on a
  long trip), the automatic one's ceiling `LEASH_AUTO_MAX_KM` staying 150 km. Connectivity, at the same
  time, fails a build below a 98% share of nodes kept or with a dropped component of more than
  200 nodes (it was a 99% share).
- **Enrich, tiers, save and qa as built for M1** (2026-10-30, at their plan): bearings follow
  the format, `bearing_out` over the first 20 m leaving u and `bearing_in` over the last 20 m
  arriving at v; a toll booth or level crossing inside a two-way road counts on both directions
  (a route drives one), one at a junction on its lowest-id out-edge; a node's delay is the
  largest of its merged stop signs and signals, not their sum; an identity keeps a
  `Business`/`Spur`/`Alt` suffix, and state-prefixed refs are a state's postal code or `SR`, so
  county routes rank below; a written build is never overwritten without `--force`; a QA
  threshold with nothing to measure passes as n/a; `meta.params` holds the configuration the
  steps' sidecars recorded. The M1 acceptance (`tools/acceptance.py`) draws its 20 pairs uniformly
  in the bbox (a node within 250 m, 3–15 km apart, `PCG64(2026)`) and runs them with unlimited
  attempts, so the 30 s budget is the limit.
- **Three detour strategies** (waypoint, random-weight Dijkstra, local exhaustive) mixed by the
  objective; strict improvement for max modes; a stopping rule for target modes.
- **Targets as built for M2** (2026-10-30, at the targets plan): `Target(kind, value)` with
  `max`, `ratio` (length over the baseline's, more than 1; not for a round trip) and `duration`
  (`time_s`, at most 7 days; since M3's review the experienced time, delays included), reached
  within ±2 % (`TARGET_TOLERANCE`); a `Goal` replaces
  `Objective.better` and owns "better" and "done" by values, so the API's follower applies it
  too. Growing, a detour is taken when it gains and its value rises to at most the band's top;
  shrinking, when it loses and its value falls to at least the band's bottom, with the radius at
  its floor; for a duration both the gain's sign and the value's direction are required. The
  smallest overshooting detour since the last splice is taken at the end of a cycle that
  accepted nothing, as an attempt; the run then shrinks; "converged" is 3 empty cycles in every
  phase; the best route is the closest (the later on a tie), and the API stores it, with a
  snapshot before `done` when the client drew another. `metrics.target` only for ratio and
  duration; no engine-version bump; `Objective.target_value` kept; `plan --base` takes the
  stops, tier, leash and objective from its file. Unfrozen at the plan's approval and kept for
  the Portland 62× → 30× shrink (34.59× converged without them, 30.58× with): while shrinking,
  runs of 6 edges on average (at most 24) and the local-exhaustive paths that lose ranked by
  closeness to the band; max and growing runs are byte for byte unchanged. At the review (same
  day): the API compares the drawn and stored routes by edges (parallel edges), types `done`'s
  target, refuses a ratio target for a round trip first (`field: "target"`), and answers a body
  with `Infinity` or `NaN` with a 422.
- **The engine's searches and loop as built for M1** (2026-10-30, at the engine-fixes plan and
  its review): on a build with transition rows every path is searched over edge states
  (`dijkstra_edges`, the DFS with turn checks), on one without by the node-state search, so the
  backends agree byte for byte there; a cheapest legal walk that repeats a node is no path
  (until engine version 4, below). The
  baseline turns legally inside each leg, independently at vias, and falls back to the
  turn-blind path, so it never exceeds the seed. Seed legs and detour searches are searched
  again without a twin they drive (up to 8 rounds); the baseline is not, since a GPS may drive
  both carriageways. The loop runs in cycles and stops after 3 empty ones; windows start with
  probability 0.3 at the route edges with the most unused nodes near, runs are 1 + geometric
  (mean 3, at most 12), searches are bounded by 8 × radius, and half the waypoints are drawn
  uniformly in the ball, each kept for raising the acceptance median. `auto` took the
  reference on a build with transition tables until the CSR edge-state kernels landed (M2);
  `validate` refuses a route made by another engine version.
- **The search looks again for a node-simple path** (2026-10-31, engine version 4, at its plan;
  the coordinator): the transition-aware search answers the cheapest legal walk even when it
  enters a node twice, and the engine checks it and searches again with the edge that comes back
  to the node banned, up to 8 rounds. The baseline does not search again and keeps the
  turn-blind fallback when its legal walk loops, so it is never longer than the seed.
- **A seed leg searches by a tree over bans** (2026-10-31, engine version 5, at its plan; the
  coordinator): a walk entering a node twice is searched again without the edge that comes back
  to it, else without the one it first entered by; a twin pair without its later edge only; up to
  9 searches and one more per 100 km between the leg's ends. The detours keep the chain of
  rounds, and the seeds on Portland and Oregon stay the same bytes (plain, strict and hard).
- **A window widens to the junctions at its ends** (2026-10-31, engine version 5, at its plan;
  the coordinator, the long trips brief's decision D1): the worsen loop moves a drawn window's
  ends out along the route to the nearest nodes with at least three distinct neighbours over the
  request's usable edges, marked once a run, or to a stop, never past one, since a detour cannot
  start or end in the middle of a road (on the national T1 build nine in ten of a route's nodes
  lie there). New York → Los Angeles on T1 with a 783 km leash went from 1.58× to 6.19–11.21×
  (seeds 1–3) in 60 s; every route changes, Portland's acceptance median going from 21.22× to
  24.30× and Oregon's named trips from 5.30–14.95× to 7.00–17.81× in 30 s; the golden routes
  `grid20_noleft` (7 left turns kept, 17,600 m, now 5 and 14,800 m) and `grid20_brand` (all 14
  locations still visited) were rewritten, the other seven keeping their bytes.
- **Alphabet mode is a constructive search**, not a splice objective. As built (M3, the
  coordinator's ruling at its plan, after the first Portland measurements left 18 of 22 trips
  without a letter): the first leg, up to the route's first A road, is free like the last;
  consecutive roads of one letter count as one letter, so a connector may drive free edges and
  roads of the letter just completed; the route records `alpha_from` and `free_from`, which the
  validator recounts; a start and an end only, no target and no "make it worse"; scored letters
  first, then the length under the request's turn constraints. With it the API's `NOT_YET`
  went: every objective the plan names is built, and any future objective lands with its own
  code, refused as unknown until then.
- **SSE for progress, not WebSockets.**
- **The API as built for M2** (2026-10-30, at the API plan): the job request mirrors the
  engine's `RouteRequest` field for field, stops as `{lat, lon, label}`, with a default
  `max_iterations` of 1,000,000 so the seconds budget rules (the CLI keeps 5,000); `plan_route`
  gained `on_start` and `base` hooks; a job runs in a spawned process of its own (at most
  cores − 2, 8 queued), its stops resolved before it exists so their errors are a 422; the
  stream sends the route the loop starts from as a snapshot and every point list with each
  node's index, so a client applies splices by node position; a cancel, a hard kill at budget
  + 30 s (`"timeout"`) or 15 s without a subscriber (once one connected) stores the route so
  far, validated; route ids are 12 hex digits over `graph_version`, nodes, edges and stop
  positions; "make it worse" keeps the base route's stops, tier and leash and refuses another
  build or engine version; `serve` needs `--region` and takes `--store`; the frontend sends
  `DELETE` on `pagehide`. At the review (same day): a worker's messages come down a one-way
  pipe rather than a `multiprocessing.Queue`, so a worker that dies mid-message fails its job at
  once, and a worker whose API process has died exits; a stored route keeps the id of its
  content, so a request that produces it again gets the first record.
- **The frontend as built for M2** (2026-10-30, at its brief and plan): Vite 6, TypeScript 5.9
  (strict), MapLibre GL JS 5 its one runtime dependency, vitest for the tests of its pure modules
  (the gate runs them); the dev server proxies `/api` to `WORST_ROUTE_API`; OSM raster tiles
  locally, at hobby volume with their attribution, and one switch (`basemap.ts`) to a vector
  style for public hosting; first click the start, second the end, later ones vias before the
  end, at 6 decimals, removable; editing the stops forgets the route shown; Reroll lets the
  server pick the seed; Make it worse continues the shown route with its seed + 1 and leaves the
  stops, tier and leash to it; the objectives other than distance are shown disabled; the
  settings and stops serialise to the URL (`?seed=&budget=&tier=&leash=&stops=`), `?route=`
  opens a stored route; seeds are safe
  integers; the stream's replay is tested against a recording of the server by node, not point
  for point (snapshots are simplified). At the review (same day): prettier (print width 100)
  formats `frontend/`, its check run by `typecheck` and so by the gate; maplibre-gl stays 5.x
  until public hosting despite its advisory (§2.3); a stream the dev proxy leaves hanging stays
  a documented limitation, without a watchdog; every error ends a run with the controls
  unlocked (`CLIENT_ERROR` for the app's own, `BAD_RESPONSE` for a success that is not JSON);
  a `?route=` link leaves the URL alone until it opens and keeps its parameter when it fails;
  Make it worse keeps the route and its seed when nothing worse is found and tries the next
  seed after the last one tried; leaving during the POST cancels the job once it is answered.
  Once the targets landed: the panel's ratio (20× by default) and drive time (9 h, typed as
  `9h`, `90m`, `2h30m` or minutes) are sent as the request's target and shown with what was
  achieved, within ±2 %; the URL carries `target=max|ratio:<x>|duration:<seconds>`; the
  max-mode recording is kept and a smaller ratio-target recording added. Once the modes
  landed (at their brief): a Mode row of radios (Longest, Slowest, Collect with its item,
  bridges by default) and a Turns row (Any, Avoid left turns, Avoid right turns, a strict
  checkbox only with a side, and its hint) replace the objective's list; the request sends the
  `item` only under collect and the `constraints` only with a side; no ratio target under
  collect; the URL carries `mode=distance|time|collect:<item>`, `turns=left|right` and
  `strict=1`, and a `?route=` link restores them; the stat line shows the time ratio ("× slower")
  with the drive and the delays, the items collected live, and the turns ("no lefts" when none);
  `INFEASIBLE_TURN_CONSTRAINT` offers "Try without strict", which sends the refused Go, Reroll
  or Make it worse again without strict; an error's `field` highlights its row; a slowest-time
  and a collect recording are added. At the review (same day): "no lefts" needs no U-turn
  either; a ratio target beside `mode=collect` in a link reads as as bad as possible.
- **Collect as built for M3** (2026-10-30, at its brief): one `Collect` objective, `objective:
  "collect"` with `item` ∈ toll_booth, level_crossing, roundabout, bridge, scoring items × 10⁶ +
  length in its own terms (toll booths and crossings as edge terms, roundabouts as node terms from
  the new `node_roundabouts` array, bridges by runs as pair terms and a new `first_edge_term`
  hook); a ratio target is refused under it, a duration compares the experienced time; waypoints from the
  uncollected items' places in the request's area, any node once none is left; the first radius
  half the baseline within [2 km, r_max]; the metrics' counters `{item, items}` and every route
  file and route document's `metrics.items` (all four, whatever the mode); `plan
  --objective/--item` (taken from the file with `--base`) and the API's `item`. `node_roundabouts`
  is appended to the format, so every `graph_version` changes and a build made before it is made
  again (from `filter` at pipeline version 1, by `pipeline save --force` at version 2); the golden
  routes keep their routes, with the new version and the request's null `item`. At the review
  (same day): a waypoint ball holding none of an objective's pool draws from all its nodes (the
  toll-booth run under a 30 km leash had starved at 49.8×); a bridge run through a round trip's
  start counts once (`first_edge_term(e, closing)`); the validator recounts reported `items`,
  which a saved route's file carries; `PIPELINE_VERSION` 2 (moved to `config`) names both M3
  changes, the bearings without stubs and the new array, and a build without one of the format's
  arrays reads as one line naming its remedy (the rebuild from `filter` for another pipeline
  version's build, else `pipeline save --force`), which a plain `save` gives too; `plan` refuses
  an unknown objective before its item.
- **Brand pilgrimage as built for M3** (2026-10-30, at its brief and plan): every POI with
  `brand` or `brand:wikidata` that filter keeps is indexed, no curated list; each snaps to the
  nearest **pass-through** node within 100 m (a node only a route's ends can use would count a
  location no route can visit; such POIs are counted at dead ends), not to an edge within 50 m
  as §2.5 first planned; grouped by `brand:wikidata` when present, else by the casefolded name,
  a name-only POI staying apart from a wikidata group spelled the same; a brand on fewer than 3
  distinct nodes left out. The index is **build arrays** (`brand_indptr`, `brand_nodes`,
  `brand_locations`, with the names and wikidata ids in `strings.json`), not a Parquet sidecar,
  so the format's checks, `graph_version`, memory-mapping and the missing-array remedy cover it;
  every `graph_version` changed with it (the golden routes kept their routes) and
  `PIPELINE_VERSION` is 3. The objective counts each brand node a route passes once (visits ×
  10⁶ + length), no ratio target; the metrics carry `brand: {id, name, visits, of}`, `of` the
  brand's nodes in the request's area; `/brands`' `locations` are a brand's distinct nodes. A
  brand the build lacks or the area does not hold is `INVALID_REQUEST` on `field: "brand"`,
  not an `UNKNOWN_BRAND` code. The CLI takes a brand's id or its name in any case, refusing a
  name two brands share.
- **Hard mode as built** (2026-10-30, at its plan; the coordinator): the rule is over the
  positions of the whole route's edges, each identity's one run; an unnamed edge is free but not
  transparent (a road on both sides of one is two stretches; on Portland's acceptance seeds the
  two readings never differ); a stop splits nothing; a round trip is read from start to end, so
  coming home on the road it left by is a return. `hard` is its own flag, in a request's JSON
  only when set. Inside a road's stretch the searches are banned every other edge; the window
  check is exact; `find_detour` asks every candidate the objective's `feasible_window`; the
  objective interface gains `window(route, i, j)`, by which hard mode widens a window drawn
  inside a stretch to the stretch's ends (no randomness drawn). The seed re-solves the leg where
  a road comes back with it banned but for the stretch before, else the leg of that stretch with
  it banned but for the one it comes back on, up to 16 rounds, a kept stretch free to grow by an
  edge at either end, and once more with only the last return's ban when that fails (at the
  review: the seed is a heuristic, and on lattices with repeated names it had refused trips a
  legal route serves; the retry and the growth recover about a third of them, and its refusal
  says only what it knows). An unreachable leg is blamed with at most two more seed builds:
  under strict and hard, the strict constraint when hard mode alone builds, hard mode ("the
  seed found no way from stop 0 to stop 1 under hard mode"; "try without --hard", "try without
  hard" in the API) when the strict constraint alone does, else neither.
- **Share image rendered from our own data** (route, faint network, Census outlines), no
  basemap of any kind; as built (2026-10-30, at its brief and plan): 1200 × 630, matplotlib Agg
  (the one new dependency), DejaVu Sans as matplotlib ships it rather than a bundled font, the
  baseline dashed over the route, a slowest-time route showing its time ratio ("slower than the
  sensible route") where the others show their ratio of lengths, a round trip its length; cached
  per route under the drawing's version (`share/<id>-v<version>.png` beside the route store),
  rendered once for concurrent first requests; a link in the share section, opening in a new
  tab, not a modal. The network is at most 100,000 road units, thinned by tier and then to the
  longest units of T0 (version 2, 2026-10-31).
- **States and towns from Census cartographic boundary polygons**, not the Gazetteer: the 5m
  states, the 20m outlines for drawing, each state's 500k places; every LSAD, **CDPs included**
  (names people know, such as Aloha and Cedar Mill); "passed through" = the route's line
  intersects the polygon, listed in the order the route first meets each; a region's places are
  those of every state's file present that meet its bbox. The files are fetched by a person with
  `tools/fetch_census.sh` (2025 vintage, sha256-pinned), never by the gate or an agent.
- **Comparison lines** chosen deterministically by length: the longest of fifteen things the
  route exceeds, "N×" from twice its length, the thing's length in miles with kilometres.
- **Directions, jokes, stats and toasts as built** (2026-10-30, at their brief and plan): no
  change to the build format, so super-junctions are described from the edges entering and
  leaving them (a ramp "toward" the road it leads to, a roundabout's exit uncounted);
  same-identity runs merged straight on, every turn a step ("to stay on" a road named alike),
  a ramp step only where the route leaves a road for a link (an interchange otherwise a
  junction like another; at the review); "Slight" within 45° and "Sharp" beyond 120°;
  OSM's names unabbreviated; a step's time counts the delays of the nodes it arrives at, so the
  steps add up to the experienced time; `edge_range` half-open. Jokes are their own step,
  forty, about every eighth step, never twice in a route, from `PCG64(seed)` on the route's
  seed; the API always tells them (the `joke` kind lets a client hide them), the CLI's
  `directions` has `--no-jokes`; a joke's distance in miles with kilometres, as the comparisons
  write it. Toasts in a fixed order, the most striking first, the ratio first whenever a route has one (a
  round trip through a via too), at most ten, so none is dropped. The presentation is
  added by `presentation.document.present` from the CLI's `plan --out` and `GET /routes/{id}`
  only, never stored, so the stored document (`engine/io.py`'s `route_document`, the job
  manager's) is unchanged and every stored route gets it; worked out on every request, not
  cached: the places index's polygons are prepared instead, which brings a long Portland
  route's document from about a quarter of a second to under 0.1 s.
- **The frontend's directions, toasts and vehicle as built** (2026-10-30, at their brief): the panel
  lists the document's steps with no other request, the document's numbers kept (a joke shows none);
  a step's edges are placed on the map by distance along the document's geometry (the driving steps'
  distances add up to its length), each step from the point where the one before it ended, since
  neither the document nor `/geojson` gives offsets of its edges and a link has no stream to take
  them from: 99.9 % of the steps' ends on their nodes exactly, the rest a point away, at any length,
  no API change. Each request labels its stops along the way as their markers are numbered
  ("Stop 2"), so the directions say "Arrive at Stop 2; continue", and not the start and the end,
  which keep the API's "Arrive at your destination" and "Arrive back where you started". A stop
  placed on a city's label is labelled with the city's name whatever its role, so the directions
  name it ("Arrive at Portland; continue", "Arrive at Portland"); the start and the end of clicked
  stops still go without a label (2026-11-02). The toasts
  sit in a strip under the map, every 4 s, waiting lines and the run's own live ratio in four fixed
  slots during a run, then the document's toasts in order. The vehicle (24 mpg and $3.89 by default,
  5–100 mpg, $0.50–20) is sent with every request, so every route has its fuel; in the URL only when
  not the default; a link restores its request's, and keeps the panel's when it has none or one
  outside those limits. States show by their USPS codes. The departure is a `datetime-local` (empty:
  now), sent with every request as ISO 8601 with the browser's offset, in the URL only when set,
  restored from a link; the arrival shows as "arrives Sat 14:57" at the departure's offset. The
  share section downloads the route's `/geojson`.
- **The frontend's brand, alphabet and hard-mode controls as built** (2026-10-31, at their brief):
  the Mode row gains Brand, with a picker of the region's brands from `GET /brands` (asked once the
  region is known, in the API's order, "Starbucks (126)", the first picked unless the settings name
  one the region has), and Alphabet; the Turns row a Hard mode checkbox, independent of the side
  and of strict. The request sends the `brand` only under brand and `constraints.hard: true` only
  when on (the constraints sent with a side or hard mode). A brand run takes no ratio target, the
  alphabet route no target at all (its row disabled, its stops start and end only, and Make it
  worse off, since it continues no route): one rule for the panel and for links. The URL carries `mode=brand:<id>`, `mode=alphabet` and `hard=1`,
  and a `?route=` link restores them. The stat line's counter row shows a pilgrimage's visits ("17
  of 19 Starbucks") and an alphabet route's letters ("letters: 15 (A–O)", the roads in its
  tooltip), live; the alphabet route's better routes are drawn whole, each in place of the last.
  At the coordinator's answers (same day): under Brand, Go, Reroll and Make it worse wait for
  `/brands` (the picker reading "loading…"; a failure keeps them disabled, with the banner), so
  no request goes without a brand; a refusal hard mode is why offers "Try without hard mode", as
  strict's does, both a leg the seed cannot build under it and a base route that comes back to a
  road (`BASE_ROUTE_MISMATCH`); an error on `field: "stops"` highlights the stops along the way.
  A brand and an alphabet recording are added, their sizes kept down by `max_iterations`. At the
  review (same day): Make it worse is off under Alphabet, its tooltip saying to pick another mode;
  the letters' span is written as the CLI writes it, a lap at a time past Z ("(A–Z, A–C)").
- **Public hosting as built** (2026-10-31, at its brief and plan; M5.4): one container serving
  the built frontend at `/` and the API under `/api` from one process, the data never inside it;
  the basemap Protomaps' PMTiles, self-hosted from a file a person fetches
  (`tools/fetch_basemap.sh`, which downloads nothing in a lane), served by the API with byte
  ranges (nginx may serve it instead), drawn by `@protomaps/basemaps` in its grayscale flavor
  with labels only when their fonts and icons are there; `WORST_ROUTE_BASEMAP=raster|pmtiles`
  named to the frontend by `GET /config`, which also carries `public` and the limits (the
  frontend reads only the basemap for now); the public limits behind `serve --public`, off by
  default: per client one job running and three waiting, its next waiting even while workers are
  free, `TOO_MANY_JOBS` carrying the client's counts only when they are why, ten place searches
  a minute counting cached answers, budgets up to 60 s, and, as of M5.4, the leash the schema's
  150 km (the plan's 300 km is above any request); the client the peer, or with `--trust-proxy` the
  last `X-Forwarded-For` address, IPv6 by its /64, uvicorn's own proxy headers off; JSON logs with a
  request id, the client as a salted hash and a line at each job's end; `/ready` failing (503) on a
  missing places index or basemap; Protomaps credited in the map's attribution control,
  OpenStreetMap (ODbL) and the Census Bureau in a footer; MapLibre GL JS upgraded to 6
  (GHSA-jrc7-96c5-q579 fixed); `pmtiles` and `@protomaps/basemaps` (BSD-3-Clause) the new
  dependencies.
- **The public leash limit relaxed** (2026-10-31, engine version 5; `docs/PLAN.md` §4.5): a
  request's own leash at most 150 km, the automatic leash following the trip (a fifth of it beyond
  750 km), the budget bounding the job.
- **Several builds as built** (2026-10-31, at its brief and plan; the coordinator): `serve
  --region` repeats, the first region the default and `--build-id` its build; the pick is the
  server's, `POST /regions/pick` (`{stops}`, one or more, → `{region, build_id, reason}`), and
  `POST /jobs` still names its region, the one picked; among the regions whose bbox holds every
  stop, one clipped to a bbox first (the smallest; with a places index loaded, only when it names
  every stop's state, so Portland → Vancouver, WA, is not portland's), then one whose states hold
  every stop's state (the fewest; a stop's state from the places index's Census polygons, none at
  sea or abroad; the reason saying when a smaller build's bbox misses a stop in its state:
  "all stops in Oregon, one beyond Oregon's build"), else the one with the most states, else
  `STOP_OUTSIDE_REGION` naming the first stop outside the region holding the most; ties to the
  region served first; each build's bbox worked out once (`Loaded.bbox`); on a public server
  sixty picks a minute a client, apart from its place searches. Each region's places index is
  its own, else a served region's whose states cover its own (never one clipped to a bbox);
  `build_of` answers a served build rather than read it again, the loader keeping two others;
  the basemap file is named by `--basemap-name`, by default the first region. `/regions` gains a
  display `name` (`config.Region.display`: "Oregon", "United States"), additive. The frontend
  fits the union of the served bboxes, asks the pick 300 ms after the stops change, names the
  build over the stop list with its roads and the reason, waits with Go until it answers,
  reloads the brands when the region changes (keeping the brand by its wikidata id, else its
  name), searches places in the pinned region, else the served region with the largest bbox, and
  pins a region from the settings (`region=` in the URL only when pinned); a `?route=` link
  leaves the pin alone; a failed pick other than a refusal is asked again three times a second
  apart, then the banner says so. At the review (same day): a continuation keeps its base
  route's own objective, with its item or brand, from the route's recorded request, never the
  panel's (a brand's id is its build's, and the panel's follow the region trips run on), so Make
  it worse runs in the route's mode on its build, with the panel's turns, budget and target (as
  bad as possible where the route's objective refuses it), and an alphabet route is not made
  worse from the frontend (until then, M3's rule, the panel's mode applied and another mode could
  continue an alphabet route).
- **The desktop app as built** (2026-10-31, at its brief and plan; the coordinator's decisions):
  one language and one runtime, pywebview on the system's web view over the app's own FastAPI
  server on 127.0.0.1 (no Tauri, no Electron), frozen by PyInstaller in one folder per platform
  and built on each platform's runner; macOS on Apple Silicon only (Numba has no Intel macOS
  wheels since 0.63; Intel Macs use the hosted site); Linux opens the system's browser (its
  WebKitGTK cannot be carried in the frozen app, PyGObject has no wheels), and so does any web
  view without WebGL 2, the window saying so; the data inside the installer (`us-t3`, Portland's
  build, the places indexes), packed once by a person and pinned by `packaging/desktop/data.json`
  (agents never upload); the basemap OpenFreeMap's online until the app's own Protomaps extract
  (the contiguous US to zoom 11, the CONUS polygon, 684 MB) has been fetched on its first start,
  resumably and checked, and used from the next start; OSM's raster tiles never the app's; the
  geocoder the API's proxy, each installation one client, under the app's own User-Agent with the
  contact from the repository variable `WORST_ROUTE_CONTACT` written at build time (the
  environment's from a checkout, the repository URL alone otherwise); the server answering only
  a Host of 127.0.0.1 or localhost; the Windows installer Inno Setup's, per user; unsigned until
  the owner buys certificates, the workflow signing and notarizing when they exist; `pywebview`
  (BSD-3-Clause) and `pyinstaller` with its hooks (GPL with the bootloader's exception, Apache)
  the new dependencies, in a `desktop` group; `/config`'s basemap gains the kind `openfreemap`
  and a `style` field, `ApiSettings` a `basemap_dir` and a `user_agent`.
- **Compute budgets as the plan assumed them** (`docs/PLAN.md` §9 Q1; recorded by the
  coordinator at the M2 and M3 acceptance, 2026-10-31; the owner may revisit): 20 s by default
  (the CLI, the API and the frontend's settings), at most 120 s, at most 60 s on a public server
  (`DEFAULT_BUDGET_S`, `BUDGET_MAX_S` and `PUBLIC_BUDGET_MAX_S` in `config.py`;
  `docs/reference/api.md`, **Requests and errors**, **Public limits**).
- **Workflow conventions**: tiers A/B/C, lanes as clones, patch-series delivery, no commit
  trailers, the gate on every change (`CLAUDE.md`, **Agent workflow**).
- **The daily worst route as built** (2026-10-31, the coordinator's brief; `docs/reference/api.md`,
  **The daily route**): the UTC date is the day and, as YYYYMMDD, the seed; the served region with
  the most states plans it; its towns are two incorporated places (LSAD 25, 43, 47) of the region's
  places index inside the build's bbox, each with a node within 5 km, 15–45 % of the bbox diagonal
  apart (60 % at first, whose longest trips ran over the public budget), labelled "Town, ST", and
  the trip is the first such pair the request's pre-flight accepts (at the automatic tier 13 of the
  first 30 days' first pairs on `us-t3` are refused), at most ten tried, so the day's route is what
  a user planning that trip would get; the request is the longest, as bad as possible, at the
  automatic tier and leash, with 5,000 attempts (`DAILY_MAX_ITERATIONS`, the measured value: 20,000
  ran over the public budget), the server's largest budget as a safety net, and the frontend's
  default vehicle (24 mpg, $3.89) and departure (the moment its job is submitted), so its document
  has its fuel and its arrival; it is planned on the day's first call through the job manager with
  no client, under a lock per date and region, and recorded per date, region and `graph_version` in
  the route store (a build replaced under its id has its own row); its trip is drawn and its
  pre-flights run once a date and build, a full server answering 429 before any drawing; no client
  may cancel its job (409 `DAILY_JOB`); a failed job is submitted again on the next call. `?daily`
  opens it only while the user has started no run of their own since the page loaded. The frontend
  shows it at the top of the panel, collapses it when the user plans a route of their own, and opens
  it from `?daily`. Considered and left for later: planning it on all roads (T4), 17–31× rather than
  4–11× but 1.7–3.4 times as long an attempt (`docs/reference/api.md`, **The daily route**). Out of
  scope then: scoring, accounts, leaderboards, "beat the engine".
- **Beat the engine as built** (2026-10-31, the coordinator's brief and its answers to the plan;
  `docs/reference/api.md`, **Drawing a route**): stateless steps, `POST /draw` taking `{request,
  path}`, the request exactly `POST /jobs`' body (its region inside it) and the whole path each
  call; a prefix checked by `route.check_route` as a route whose last stop is the node reached (a
  stand-in that masks no rule: `check_route` reads stop positions for their shape only), beside the
  stops' order, the end and the request's area, strict turns and hard mode, the refusal the first
  illegal edge (`path[:index]` legal); the next moves a filter held to the check by a test on random
  walks; a drawing the longest route with no target (another objective, a target or a base route
  refused on its field); a strict turn constraint and hard mode rules of the drawing, a soft one
  nothing; a finished drawing stored with `run.backend: "human"` and `stopped_because: "drawn"`
  (a value added to the stop reasons), the API request as its `input` unchanged (no `drawn` flag),
  its seed as `POST /jobs` picks one; what each step needs kept for the last eight requests drawn on
  (`DRAW_CONTEXTS`), a miss working it out again (37–148 ms on `us-t3`, about 6.6 MB each); at most
  10,000 edges a path (`MAX_DRAW_EDGES`) and on a public server 240 steps a minute a client over
  both endpoints (`PUBLIC_DRAW_PER_MINUTE`); the par a normal job the frontend starts with the
  drawing, its ratio followed and nothing of it drawn; Finish taking the longest move to the end;
  the next moves drawn as whole edges, a click taking the nearest within 8 px (the plan's 6,
  widened at the review: the better target); on a public server at most ten finished drawings a
  minute a client (`PUBLIC_FINISH_PER_MINUTE`), since each finish stores a route, the review's
  addition; a drawing forgotten, its par cancelled, whenever the stops or the settings it was drawn
  on change, whatever changed them. Out of scope: scoring across players, accounts, leaderboards,
  hints, completing a drawing for the player.
- **Simulated-annealing acceptance, on by default** (M6, the coordinator, 2026-10-31, from the
  simulated-annealing brief's measurements; engine version 6; `docs/reference/engine.md`,
  **Annealing**): for a max goal, a run that would stop as converged reheats instead, up to 3
  times (`ANNEAL_REHEATS`; as many as the budget allows made a few % more but never converges),
  and takes a detour that loses, strictly (`delta < 0`: exp(0) = 1 would take every
  equal-length alternative, which on the fixture lattices kept a heat going for a hundred times
  greedy's attempts), with probability exp(delta / T), one rng draw for it after it is scored;
  T starts at 1 % of the objective's primary quantity of the baseline (the start route's for a
  round trip), cools ×0.99 per accepted splice and is none below a thousandth of its start; the
  gates are a refused candidate's own (no net penalised turn added, the window feasible); an
  annealed splice is an accepted splice in every way, and the best route moves only by the
  goal. Reheating at convergence, not cooling from the first attempt as the brief proposed:
  measured interleaved with greedy on Portland's acceptance pairs (build 0b72253e5e8a, seed 1,
  30 s, a load of 7–24), cooling per accepted splice from the start made 27.92× and per cycle
  28.21×, some pairs 9 % below greedy, and both lost 5–9 % on budget-bound Oregon and national
  trips, while reheating made 29.96× against greedy's 26.05× (6.10–54.05×), every pair at least
  greedy's (×1.00–×1.64), 20 of 20 validated; greedy's own seeds differ per pair by ×0.65–×1.77,
  so only a schedule that keeps the greedy run as its prefix can promise no pair worse. The
  decision rule applied: default on, since the median rose by 15 % (at least 10 % asked), no
  pair fell by more than 10 %, and the variety test still holds: the Jaccard of seeds 1 and 2
  rose from 0.160 to 0.189 on the edges (0.298 to 0.363 on road units), the longer routes
  filling more of the leash, under the test's 0.3. The cost is time: most Portland runs now use
  their whole budget (the median run 30 s against 9 s at 30 s; at the frontend's 20 s most end
  on it). Progress events gain `best_score` and `best_metrics` (an additive shape change), and
  the stat line shows the best beside the current while a heat dips below it, the route drawn
  live with its shrinks. ENGINE_VERSION is 6 (version-5 routes refused as bases). A run is
  version 5's byte for byte wherever greedy needs more than its cap to converge: the goldens
  (400 attempts, too few to end a cycle), the CLI's default 5,000 attempts on Portland's
  acceptance pairs (greedy converging after 16,406–173,807), and the daily route's 5,000 on
  `us-t3` (trips of 849–2,547 km, like the national ones measured, which did not converge within
  60 s) are such runs. On a small graph greedy can converge within 5,000 (the 20 × 20 lattice
  corner to corner at 4,245–4,874 attempts with seeds 1–3; seed 0 needs 5,207), and at version 6
  such a run reheats and ends on its cap instead: the same route bytes there, its `run.attempts`
  and `stopped_because` ("budget" for "converged") not.

- **Challenges as built** (2026-10-31; served since 2026-11-02, the coordinator's briefs and
  rulings; `docs/reference/api.md`, **The daily challenges**; `docs/reference/frontend.md`,
  **Challenges**): eight a day, served by `GET /challenges` (an API shape, tier A), the same on
  every server with the main build: seven drawn from the date between towns of the daily route's
  pool, each passing the pre-flight (the long way, 150–500 km; the slow way under the slowest
  mode, 80–250 km; strict no-left, 100–300 km; bridges, 30–200 km; a pilgrimage to one of twenty
  chains by wikidata id with at least six locations in the trip's area, 30–250 km; hard mode,
  100–300 km; four towns in the worst order, every two 25–200 km apart), then today's route made
  worse, today's only. Each bar is 80 % of the trip's own plan, rounded down: a job with no client
  in a worker, as the daily route's, with 2,500 attempts (hard mode 8,000), so every server sets
  the same bar; no-left's is 1.0× (strict no-left converges at 1.2–1.8×, where the soft constraint
  keeps hundreds of left turns), and hard mode is never under 1.5× (a draw below it passed over).
  A date is made on its first call (about a minute on `us-t3`) and kept in memory and in the route
  store; today and the 30 days before are answered, a public server making only today's. This
  replaced the fixed list of ten bundled in the frontend (on the Portland, Oregon and national
  builds; the alphabet, roundabouts and a drawing among them), not served at first because a
  server-side list was an API shape: the Portland build left the app, and the alphabet and
  roundabouts are thin on the national build. Graded in the browser on every route shown, the
  completions kept with the day in `localStorage` (`worst-route.challenges`), every access guarded
  so the page works without it; the desktop app on a fixed port (`DESKTOP_PORT`, 17429) so its
  storage lasts: on macOS and Linux bound with `SO_REUSEADDR` only once a probe connection to it is
  refused (a free port otherwise), on Windows bound plainly (a free port when that fails), the
  review's ruling. Out of scope: scores, leaderboards, accounts, new modes.
- **Portfolio runs as built** (2026-10-31, the coordinator's brief and its rulings on the plan;
  `docs/reference/api.md`, **Portfolios**): `portfolio` on `POST /jobs`, 1 by default (left out of
  the stored request then, so a job of one search sends and stores exactly what it did before), at
  most `MAX_PORTFOLIO` (4), `PUBLIC_MAX_PORTFOLIO` (2) on a public server and never more than the
  server's workers (`PORTFOLIO_TOO_LARGE {max}`, after the budget); the engine's request unchanged,
  the K seeds `seed` to `seed + K − 1`; refused under the alphabet route, with a base route and in
  a drawing (`INVALID_REQUEST` on `field: "portfolio"`); allowed with the worst order, which no seed
  changes, the manager reordering on the first `Started` and failing the job on a later one that
  differs. The leader the search whose best is strictly better by the goal (a tie keeps it), the
  lead passing at most once in `API_LEADER_HOLD_S` (2 s; at once when the leader fails), sent as a
  snapshot of its route then a `progress` with no splice, an old leader's unsent splices dropped;
  `progress` carrying `portfolio: {k, leader, best_ratios}`, `best_ratios` the ratio `progress`
  itself gives; a failed search dropping out, the job ending in the first one's error only when
  all fail; the job complete when every search has ended or one reaches its target (the others
  stopped), the winner the best finished route (the first of the best), sent as a snapshot before
  `done` whatever the hold when the clients have drawn another, stored with its own seed and
  `run.portfolio = {k, seeds, winner}` (present only for a portfolio), the stored request keeping
  the first seed; a cancel or the time limit storing the best route so far of the search ahead;
  the server's `max_jobs` counting workers, each search's freed as it ends, a job waiting for
  workers holding up those behind it, a public client's share counting jobs. The frontend's Seeds
  setting hidden until `GET /config` says the server runs more than one, the stat line naming the
  leader's seed, then the winner's, the panel keeping the first seed so Go plans the same searches,
  links carrying `seeds=K`. Which seed wins is deterministic for runs ended by their iterations or
  convergence; a target or budget race depends on timing, each winner reproducing from its seed.
  Out of scope: sharing work between the searches, any change to the engine's loop.
- **The par capped at a 10× target** (2026-11-01, the owner, on the coordinator's recommendation;
  `docs/reference/frontend.md`, **Drawing**): the engine's par for a drawing is the drawing's
  request with a ratio target of 10× (`DRAW_PAR_RATIO` in `draw/drawing.ts`), which the engine
  reaches within ±2 % in a second or two, so the bar is a fixed number the player sees before
  drawing much and can beat with patience: on the short in-town trips people draw by hand the full
  run reached 200–320× while a patient drawing lands around 10–15×, and nobody beat it. A round
  trip, on which the API refuses a ratio target (its baseline is 0 m when it has no stop between),
  keeps the full run as its par; where the engine stops short of the target, its ratio is the par
  as before. The verdict and the API are unchanged; the drawing challenge now grades only a drawing
  on its own 300 m leash (the coordinator, 2026-11-01), since on the automatic leash a 10× par is
  beaten easily and the challenge's point is the 300 m leash. Considered: leaving the par the full
  run, a shorter budget, and two jobs (a capped par beside the engine's full number). Out of scope:
  tiers (bronze, silver, gold), scoring across players.

- **The certified worst as built** (2026-11-01, the coordinator's brief and its answers to the
  plan; OR-Tools approved as a dependency by the owner, 2026-11-01): CP-SAT's `add_circuit` over
  the request's own area (the edges its masks allow on some walk from a stop to the next), not a
  drawn box; no DFS fallback, the dependency being unconditional, and OR-Tools imported only
  inside the driver. Two pairs of caps: every request's area at most 300 nodes and 1,000 edges
  (`AREA_TOO_LARGE_TO_CERTIFY` on the leash), and the automatic leash aimed at 300 and 600 so a
  default request is proven within the default budget, while an explicit leash and a 120 s
  budget may certify up to the hard caps (on Portland 300/600 proved 10 of 14 short trips within
  19 s, 300/800 only the downtown ones). Stops between the ends are taken (positions keep them in
  order; a round trip is not, yet), and so are no target, worst order, turn constraint, hard mode,
  portfolio, base route or drawing. 4 threads with `max_lp` added, everywhere (a public value
  is a follow-up); the length in decimetres; the area checked at `POST /jobs` and again in the
  worker, the leash recorded as a number in the engine's request while the API's input keeps what
  was asked; each better route streamed as a snapshot and a progress, the bound between them at
  most once a second; `run.certified` built by the worker and, for a cancelled job, by the
  manager. Make it worse is disabled on a certified route in the frontend (the API still
  continues one under the loop). The notices list OR-Tools, absl-py, protobuf, immutabledict and
  the libraries in OR-Tools' native library with where their sources are (COIN-OR's are EPL-2.0),
  which the desktop app's licence file carries too. Reproducibility: a proven route's length is
  fixed by the build and the request; the route among equally long ones and a best found under
  the time limit are not.

### 3.2 Open

The plan's assumed defaults, each open until the owner confirms (`docs/PLAN.md` §9):

- "Only turn left" means "never turn right"; straights allowed.
- Alphabet mode: unnamed roads, ramps and numbered streets are free connectors; wrap Z→A; the
  first and final legs are free; one letter's roads in a row count once (built so, §3.1).
- National end state: one US all-roads master graph if the array pipeline is fast enough,
  otherwise T3 national plus per-state all-roads builds. The decision gate's numbers (M5.3,
  2026-10-31; `docs/reference/pipeline.md`, **The national T1 build**):
  - (a) **One all-roads master graph.** The national filter keeps 14.3 M ways at every class, 70.6
    times Oregon's (26.6 times Oregon and Washington's), so about 17 M nodes and 42 M edges. Sizes
    in decimal units, scaled from Oregon's build ea3b18096e8b (55.3 MB of arrays and strings plus
    11.3 MB of geometry): about 3.9 GB of arrays and strings, which the engine maps, and 4.7 GB with
    the geometry, where format 3 (Oregon's 174 MB) would take about 12 GB. On networkx it is out of
    reach: the T1 build's 2.6 M raw edges did not finish cleanup in two hours. On arrays, scaled
    from the runs measured (about 49 M raw edges, 18.7 times the T1 build's): the filter 23 min as
    measured, build about 16 min (50 s for T1), cleanup 20–60 min (Oregon 15 s, the T1 build 198 s,
    their costs per raw edge bracketing it), and enrich, tiers, save and qa about 30–40 min (on
    networkx they took 345 s for the T1 build's 1.3 M edges, about 3 h scaled to 42 M, and tens of
    GB; the array pipeline's speed-up on Portland is about 8×): about 1.5–2.5 h in all, now that
    enrich, tiers and save run on arrays too and `filter` merges in a stream (the in-memory merge
    would have held about 190 M nodes). Its memory, measured (2026-10-31;
    `docs/reference/pipeline.md`, **The all-roads national build**), does not fit the machine's
    25.8 GB (24 GiB): the filter streams the country's 1.92 GB of roads in 23 min at 4.2 GB of
    resident set, but `build` was stopped by hand at a physical footprint of about 29 GB, swapping
    (the watchdog read the resident set, about 2.2–2.6 GB, which on macOS leaves out compressed and
    swapped pages; it reads the footprint since), and west-coast's footprints (California, Oregon
    and Washington), scaled 8–9 times, put the steps at about 39–45 GB for build, 79–91 GB for
    cleanup, 66–76 GB for enrich and 71–82 GB for save. So (a) needs leaner steps before it can be
    built on this machine: the changes the probes point to (chunked projection and geometry, arrays
    for cleanup's Python lists, one copy of the coordinates, bearings without GEOS lines) would save
    from about a third of build's to most of enrich's and save's, leaving cleanup at about 45–50 GB,
    which needs a way of working through the graph in parts. The engine at 17 M nodes has the
    leash's distances in NumPy (4–10 ms on the T1 build's 887,737 nodes, where shapely's 0.16–0.3 µs
    a node would have taken seconds for a coast-to-coast trip) and would need searches whose set-up
    does not grow with the build (the kernels allocate per-node or per-edge work arrays on each
    call; at 887,737 nodes runs still made 1,070–1,170 attempts a second).
  - (b) **T1 nationally plus all-roads builds per state.** The T1 build: 887,737 nodes, 1,324,013
    edges, 178.1 MB of arrays and strings (201.2 MB with the geometry), 22 minutes from the extracts
    with build and cleanup on arrays. The state builds, made once as a measurement and not served
    (2026-10-31; `docs/reference/pipeline.md`, **The state builds**): the 48 contiguous states and
    DC in 47 min on the dev machine, at most 8.51 GB of memory (Texas), 4,208 MB of arrays and
    strings and 586 MB of geometry in all, the largest Texas's 453 MB and California's 334 MB; 32
    pass QA, 14 fail only the connectivity rule (islands, land reached only through a neighbour:
    what a region loses without its neighbours' extracts) and DC, West Virginia and Washington fail
    their T0 share or twin coverage too. On the T1 build a cross-state trip plans at the automatic
    tier (T1, a fifth of the trip as leash, engine version 5) at 6.2–10.3× in 60 s, where it
    converged at 1.1–1.9× on T1 and was refused at T0 with a 150 km leash; one town in five (81.8%
    kept) has no primary road within 5 km to start from.
  - Either national graph is projected to CONUS Albers (EPSG:5070), whose scale is off by up to
    ±1.4% (lengths and ratios, near Key West and the northern border) and whose grid north is up
    to 17° from true north near the coasts (the first step's compass word); turns, measured as
    differences of bearing, are unaffected. Geodesic lengths and bearings corrected for it would be
    a pipeline version of their own.
  - The plan's rule (`docs/PLAN.md` §7 M5.3) recommends (a) if the array pipeline makes it a few
    hours, which its times point to; its memory, now measured, does not fit this machine until the
    steps are leaner (above). The automatic tier for trips beyond a state is retuned (engine
    version 5; `docs/reference/engine.md`, **On the national T1 build**): T1 from 800 km, a fifth
    of the trip as leash, 6.2–10.3× on four cross-country trips.
  - The outcome (the owner, 2026-10-31): national at T3, the server serving `us-t3` (with Portland
    and Oregon as the development regions; built 2026-10-31, `docs/reference/pipeline.md`, **The
    national T3 build**: 2.15 M nodes, 4.41 M edges, 586.6 MB, 99.1% of the towns within 5 km);
    all-roads city builds deferred (bbox regions like Portland, picked by the server when a trip
    stays inside one); the per-state builds a one-off measurement of the array pipeline at state
    scale, 4.8 GB, not served. The owner, 2026-10-31: all roads nationally was never the plan; its
    numbers above are a measurement.
- Alaska and Hawaii in national builds: left out of `us-t1`, `us-t3` and `us` for now (the
  coordinator, 2026-10-31, for the owner to revisit): no road reaches them and CONUS Albers does not
  cover them; they would be regions of their own.
- Round trips exposed in the UI.
- Vehicle stats: MPG and gas price only, or EV too.
- Public geocoder: self-hosted Photon or a paid provider (the trade-off in
  `docs/reference/hosting.md`, **The geocoder**).
- Where the public downloads the desktop app from (asked of the owner, 2026-10-31; decided
  2026-11-01): this repository, made public, so its release assets (the installers, the basemap
  file the app fetches) need no token and a tag's build costs no minutes. The workflow still makes
  a draft release the owner publishes; the data pack and the basemap file are not uploaded yet, and
  the app's basemap address stays a placeholder until the first release holds the file
  (`WORST_ROUTE_BASEMAP_URL` sets it).
