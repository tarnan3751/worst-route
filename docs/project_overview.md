# Worst Route — project overview

Last updated: 2026-10-30. Snapshot taken after the slowest time and the turn constraints
landed: the plan is written; the project scaffolding, every pipeline step, the graph package,
the engine, the API and the frontend are built; Portland has a saved build, and the M1
acceptance run on it passes (§2.2): M1 is built, and M3's first two modes are.

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
  the other modes are later milestones.
- **API and frontend** (In progress): FastAPI jobs with Server-Sent Events progress, a MapLibre map
  that shows the route getting worse live, settings, reroll, make it worse. `docs/PLAN.md` §4–5.
  The API and the frontend are built for the distance mode and its targets
  (`docs/reference/api.md`, `docs/reference/frontend.md`); M2's acceptance is next.
- **Modes** (In progress): longest distance, slowest time, never/only turn left, collect toll
  booths / rail crossings / roundabouts / bridges, brand pilgrimage, alphabet route, hard mode.
  Built: longest distance, slowest time, and never turn left / right as constraints on either
  (soft: each such turn costs a kilometre; strict: none), with targets by ratio or duration;
  the frontend offers the distance mode only so far.
- **Presentation** (Planned): directions, stat toasts, comparisons, states and towns, share image.
- **Scale** (Planned): Portland → Oregon → national at a tier to be decided at M5.

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
`graph_version`; `qa` writes the build's report and GeoJSON layers and checks its thresholds.
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
The API is built (`docs/reference/api.md`, M2 step 2): `worst-route serve` runs a FastAPI app
that plans each request as a job in a worker process of its own, streams the route as it grows
over Server-Sent Events (splices, periodic snapshots, resumable with `Last-Event-ID`), stores
finished routes in sqlite, continues a stored route ("make it worse"), and on a cancel, a time
limit or a closed stream stores the best route so far once the validator passes it. On
Portland, Powell's → Reed streams to 62.4× in 11.8 s through it, the engine's own result for
that seed; a 9 h target from the same 13-minute drive ends within ±2 % in 1.8 s.
The frontend is built (`docs/reference/frontend.md`, M2 step 3): a Vite + TypeScript app
without a framework on MapLibre GL JS, in which the user clicks a start, an end and stops along
the way on the region's map, picks a target (as bad as possible, a ratio or a drive time), runs
a job and watches the route grow from the stream (each splice applied and flashed, a colour
gradient from start to end, reconnecting with `Last-Event-ID`), cancels, rerolls, makes the
route worse, and opens a stored route by its `?route=` link; the settings and stops live in the
URL, every API error shows in a banner, and leaving the page cancels a running job. On
Portland, Powell's → Reed grows to 60.0× in 12 s on the map; its tests replay streams recorded
from the server, node for node.
The geocoding proxy is built (M4's first piece; `docs/reference/api.md`, **Geocoding**): a
place search in the stop list (a field and a button, no type-ahead) asks `GET /geocode`, which
answers up to five places in the region from Nominatim behind a 30-day sqlite cache and a limit
of one request a second, under the project's User-Agent; a pick places the next stop as a map
click does. No test or agent run calls Nominatim: the tests use a fake geocoder or a mocked
transport, and every test refuses httpx's real transports.
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
| M2 — API + map, live growth | In progress | CSR + Numba backend built, its transition-aware kernels included (the default on every build; byte-identical to the reference: on Portland the acceptance's 20 routes and Powell's → Reed reproduce exactly, at 7,200–8,200 attempts/s); the API built (jobs in worker processes, SSE progress, the route store, make it worse, `worst-route serve`); targets by ratio and duration built (engine, CLI `--target`/`--base`, API), a 13-minute Portland drive to 9 h within ±2 % in 1.8 s over the API; the MapLibre frontend built (stops by click, the route growing live, run controls, settings with the targets, links); remaining: the acceptance |
| M3 — Modes and constraints | In progress | built: the turn classes, never turn left / right (soft by default, `strict` for none; engine, CLI, API), the kernels refusing banned turn classes, slowest time (engine, CLI, API); on Portland strict no-left plans 4 of the 20 acceptance pairs and soft no-left 20 at a median of 5.33× (§2.3; on a build re-enriched at pipeline version 2, whose bearings describe the roads, not cleanup's stubs); the search honouring stage B transitions was built at M1. Remaining: collectibles; brands; hard mode; alphabet search; the frontend's controls for the new modes; the acceptance |
| M4 — Presentation | In progress | directions, stats, toasts, comparisons, share image, geocoding proxy, share links; built: the geocoding proxy (`GET /geocode`, Nominatim behind a cache and 1 request/s) and the place search in the stop list |
| M5 — Scaling up | Planned | Oregon; array-based simplify/consolidate; US at T1; the national decision gate; public-hosting prep |
| M6 — Stretch | Planned | certified worst (CP-SAT), turn restrictions, stop reordering, portfolio runs |

### 2.3 Known limitations

The risks the plan anticipates are `docs/PLAN.md` §8.3. Found on the Portland build:

- **The transition-aware search answers no path** when the cheapest legal way passes a node
  twice; it does not look for the cheapest node-simple legal way, so a leg can fall back (a
  re-snapped stop) where a longer legal one exists. No seed leg of the acceptance pairs did;
  in their detour searches the review counted 23 such answers in about 155,000 calls, each
  costing one failed attempt.
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
- **Strict turn constraints are rarely satisfiable** on a street grid together with the rule
  that no junction repeats: a net left turn without left turns is three rights round a block,
  which comes back through its junction. On Portland strict no-left plans 4 of the 20
  acceptance pairs and strict no-right 3 (Powell's → Reed refuses). **Soft ones make routes
  less bad**, since nearly every detour adds such a turn: medians of 5.33× (no-left) and 6.47×
  (no-right) on the same pairs at a kilometre a turn (`TURN_PENALTY_M`), against 21.5× without
  (1.12× and 1.10× at 10⁶). Under the slowest time a left turn costs 90 s (`TURN_PENALTY_S`),
  and soft no-left gives Powell's → Reed 28.48× the experienced time with 1.09 left turns a km,
  more than the distance mode's 0.62, since a detour's time gain counts its delays too; 180 s
  and 300 s keep them rarer but at 11.66× and 4.67×. **A soft route barely shrinks** toward a
  smaller target, since shrinking may not add a net avoided turn and a shorter way round mostly
  needs one: Powell's → Reed's 16.77× soft no-left route, sent toward 3×, converges at 11.04×
  (`stopped_because: "converged"`, outside the band). That is the price of the rule, accepted
  by the coordinator at M3's review; letting a shrinking splice add an avoided turn when the
  route's density of them does not rise is a later option (`docs/reference/engine.md`,
  **Constraints**, **Limitations**, **On Portland**).
- **A possible stage B over-restriction**: the I-84 / NE Halsey super-junction (node 65437 of
  the 2026-10-30 Portland build) forbids NE 42nd Ave → NE Sandy Blvd, a turn that looks
  drivable; to investigate in the next cleanup fold.

In the frontend (`docs/reference/frontend.md`, **Limitations**):

- **MapLibre GL JS 5 carries an advisory**: GHSA-jrc7-96c5-q579, an XSS bypass in its HTML
  sanitizer, which `npm audit` lists as critical for every version up to 6.4.0. In 5.x the
  sanitizer renders only the attribution control's strings, here the app's own, so 5.x is kept
  for local use (decided 2026-10-30); the upgrade to 6.x comes before public hosting, whose
  third-party vector style brings attribution of its own (M5).
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
- **Stops are visited in the given order.** Reordering is a possible later toggle (M6).
- **Local only now, designed for public hosting later**: tile provider, geocoder, rate limits
  and a deployable shape are planned, not built early.
- **OSM turn restrictions are ignored for now**; they plug into the same transition table as
  super-junctions later (`docs/PLAN.md` §3.13).
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
- **State extracts, never the full US file**; cross-border ways deduplicated by id.
- **On-disk format: one `.npy` per array + `strings.json` + `meta.json`, memory-mapped**; not
  GraphML or pickle, and not NPZ (whose members NumPy cannot map) or Parquet for the graph.
- **Consolidation in two stages**: OSMnx stage A with per-node tolerances (1 m on roundabout
  rings, which stage B owns; 15 m on arterial crossings; 8 m elsewhere; `max_length=60`, dead
  ends kept) and a custom stage B that turns interchanges and roundabouts into super-junctions
  with allowed-transition tables, computed from M1 because `Route.splice` enforces them.
  Stage B joins the ramp components of one interchange when their attachment roads share two
  identities within 500 m, a slip-lane cluster takes in the at-grade crossing it bypasses
  (reached along two different roads), and every cluster takes in its pockets, a stretch of one
  road of at most 600 m that leaves it and comes back (2026-10-30, at the cleanup plan and its
  review).
- **Divided carriageways are linked as twins** detected on the stage A graph's edge segments
  (same ref/name and, motorways aside, the same name less directional words when both have
  one, so couplets are not twins; opposite bearing; side by side within a lateral distance by
  class; never two pieces of one OSM way), carried through stage B to the super-junctions'
  boundary edges along the same carriageway and to the transitions that must drive a twinned
  stretch inside them, a passage that must drive both carriageways inside a junction banned, and
  banned conservatively, not merged.
- **Tiers T0–T4 as masks on one master graph**, auto-selected by trip length, with local
  all-roads bubbles around stops and a leash along the stop polyline.
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
  backends agree byte for byte there; a cheapest legal walk that repeats a node is no path. The
  baseline turns legally inside each leg, independently at vias, and falls back to the
  turn-blind path, so it never exceeds the seed. Seed legs and detour searches are searched
  again without a twin they drive (up to 8 rounds); the baseline is not, since a GPS may drive
  both carriageways. The loop runs in cycles and stops after 3 empty ones; windows start with
  probability 0.3 at the route edges with the most unused nodes near, runs are 1 + geometric
  (mean 3, at most 12), searches are bounded by 8 × radius, and half the waypoints are drawn
  uniformly in the ball, each kept for raising the acceptance median. `auto` took the
  reference on a build with transition tables until the CSR edge-state kernels landed (M2);
  `validate` refuses a route made by another engine version.
- **Alphabet mode is a constructive search**, not a splice objective.
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
  max-mode recording is kept and a smaller ratio-target recording added.
- **Share image rendered from our own data** (route, faint network, Census outlines), no
  third-party basemap.
- **Towns from the Census Gazetteer** with an area-equivalent radius; states from Census
  cartographic boundaries.
- **Workflow conventions**: tiers A/B/C, lanes as clones, patch-series delivery, no commit
  trailers, the gate on every change (`CLAUDE.md`, **Agent workflow**).

### 3.2 Open

The plan's assumed defaults, each open until the owner confirms (`docs/PLAN.md` §9):

- Compute budget defaults: 20 s default, 120 s max locally, 60 s max public.
- "Only turn left" means "never turn right"; straights allowed.
- Alphabet mode: unnamed roads and ramps are free connectors; wrap Z→A; the final leg is free.
- Brand set: index every `brand` / `brand:wikidata` object, not a curated list.
- Share image without a basemap.
- Towns: Gazetteer places of all LSAD types including CDPs, centre-plus-radius, not polygons.
- National end state: one US all-roads master graph if the array pipeline is fast enough,
  otherwise T3 national plus per-state all-roads builds.
- Alaska and Hawaii in national builds.
- Round trips exposed in the UI.
- Vehicle stats: MPG and gas price only, or EV too.
- Public geocoder: self-hosted Photon or a paid provider.
- Attribution text beyond a footer line.
