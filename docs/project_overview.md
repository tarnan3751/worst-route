# Worst Route — project overview

Last updated: 2026-10-30. Snapshot taken after the frontend's directions panel, toasts and
departure landed: the plan is written; the project scaffolding, every pipeline step, the graph
package, the engine, the API and the frontend are built, and so is the presentation (M4): the
geocoding proxy, the places index, the comparison lines, the share image, the directions with
their jokes, and the stats and toasts, in the route documents and in the frontend; Portland has a
saved build, and the M1 acceptance run on it passes (§2.2): M1 is built, and M3's first four
modes are.

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
  the collect mode and hard mode since M3; the other modes are later milestones.
- **API and frontend** (In progress): FastAPI jobs with Server-Sent Events progress, a MapLibre map
  that shows the route getting worse live, settings, reroll, make it worse. `docs/PLAN.md` §4–5.
  The API and the frontend are built for the distance, slowest-time, collect and brand modes,
  the alphabet route, the turn constraints, hard mode and the targets (`docs/reference/api.md`,
  `docs/reference/frontend.md`); M2's acceptance is next.
- **Modes** (In progress): longest distance, slowest time, never/only turn left, collect toll
  booths / rail crossings / roundabouts / bridges, brand pilgrimage, alphabet route, hard mode.
  Built: longest distance, slowest time, and never turn left / right as constraints on either
  (soft: each such turn costs a kilometre; strict: none), with targets by ratio or duration, and
  collecting toll booths, level crossings, roundabouts or bridges (targets by duration); the
  frontend offers each of them (its Mode and Turns rows). Hard mode (each road in one stretch) is
  built in the engine, the CLI and the API, with any of them, and the frontend offers it (a
  checkbox in its Turns row), as it does the brand pilgrimage (a Brand mode with a brand picker
  from `GET /brands`) and the alphabet route (an Alphabet mode).
- **Presentation** (Built): directions, stat toasts, comparisons, states and towns, share
  image. Built: the states and towns a route passes through (the Census places index), the
  comparison lines, the share image, and the directions with deadpan jokes, the stats and the
  toasts in the route documents (`docs/reference/presentation.md`), which the frontend shows: a
  directions panel whose steps highlight on the map, rotating toasts under the map, the extra
  stats with the arrival time, a vehicle and a departure setting, and the route's GeoJSON to
  download (`docs/reference/frontend.md`).
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
its share of the roads within reach, and up to twelve toasts, the most striking first ("34.2× less
efficient than the sensible route."). `GET /routes/{id}` carries them, worked out again on every
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
| M3 — Modes and constraints | In progress | built: the turn classes, never turn left / right (soft by default, `strict` for none; engine, CLI, API), the kernels refusing banned turn classes, slowest time (engine, CLI, API), collectibles (the collect objective for toll booths, level crossings, roundabouts and bridges in the engine, `plan --objective collect --item` and the API; `node_roundabouts` in the build; on Portland 24–30 bridges where the distance route passes 16–17); the frontend's controls for them (the Mode and Turns rows, the request, the URL, the stat line's time ratio, delays, items and turns, the strict refusal's retry); the brand pilgrimage (the brand index at `enrich`, saved as build arrays at pipeline version 3; `plan --objective brand --brand`, the API's `brand` and `GET /brands`; on Portland 17 of the 19 Starbucks in reach); the frontend's controls for the brand pilgrimage, the alphabet route and hard mode (a Brand mode with its picker, an Alphabet mode, a Hard mode checkbox, the request, the URL, the visits and letters live in the stat line, the alphabet route's better routes drawn in place, the hard-mode refusal's retry); on Portland strict no-left plans 4 of the 20 acceptance pairs and soft no-left 20 at a median of 5.33× (§2.3; on a build re-enriched at pipeline version 2, whose bearings describe the roads, not cleanup's stubs); hard mode (engine, CLI, API; on Portland 19 of the 20 acceptance pairs plan under it, at a median of 4.73×); the alphabet route (its own search, a new tree search on both backends, `plan --objective alphabet` and the API; on Portland Powell's → Reed A to M, Powell's → PDX A to W, the acceptance pairs a median of 1 letter); the search honouring stage B transitions was built at M1. Remaining: the acceptance |
| M4 — Presentation | Built | directions, stats, toasts, comparisons, share image, geocoding proxy, share links; built: the geocoding proxy (`GET /geocode`, Nominatim behind a cache and 1 request/s) and the place search in the stop list; the Census places index (`tools/fetch_census.sh`, `worst-route aux places`: the states and towns a route passes through), the comparison lines, and the share image (`GET /routes/{id}/share.png`, linked from the share section); the directions with deadpan jokes and the stats and toasts in the route documents (`GET /routes/{id}`, `GET /routes/{id}/directions`, route.json, `worst-route directions`); the frontend's directions panel (a step's edges highlighted on the map), toasts under the map, extra stat rows and vehicle setting; the departure setting with the arrival time, and the GeoJSON download |
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
- **Strict turn constraints are rarely satisfiable** on a street grid together with the rule that no
  junction repeats: a net left turn without left turns is three rights round a block, which comes
  back through its junction. On Portland strict no-left plans 4 of the 20 acceptance pairs and
  strict no-right 3 (Powell's → Reed refuses). **Soft ones make routes less bad**, since nearly
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
- **Stage A places each cluster from its own buffers** (M5.2, 2026-10-31, pipeline version 4):
  at the centroid of the union of its members' buffers, or of their points when OSMnx's edge
  test split their group, as OSMnx does. OSMnx's own position comes from one union of every
  buffer in the region, whose last bits depend on the whole graph, which an array pipeline
  cannot reproduce at scale; placing each cluster from its own buffers in both pipelines keeps
  their builds byte-identical. On Portland it moves 3,977 clusters by at most 1.5e-8 m, which
  changes about 3,200 node positions and six float32 lengths and times, and nothing else.
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
  per route under the drawing's version (`share/<id>-v1.png` beside the route store), rendered
  once for concurrent first requests; a link in the share section, opening in a new tab, not a
  modal.
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
  round trip through a via too), at most twelve, so none is dropped. The presentation is
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
  which keep the API's "Arrive at your destination" and "Arrive back where you started". The toasts
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
- **Workflow conventions**: tiers A/B/C, lanes as clones, patch-series delivery, no commit
  trailers, the gate on every change (`CLAUDE.md`, **Agent workflow**).

### 3.2 Open

The plan's assumed defaults, each open until the owner confirms (`docs/PLAN.md` §9):

- Compute budget defaults: 20 s default, 120 s max locally, 60 s max public.
- "Only turn left" means "never turn right"; straights allowed.
- Alphabet mode: unnamed roads, ramps and numbered streets are free connectors; wrap Z→A; the
  first and final legs are free; one letter's roads in a row count once (built so, §3.1).
- National end state: one US all-roads master graph if the array pipeline is fast enough,
  otherwise T3 national plus per-state all-roads builds.
- Alaska and Hawaii in national builds.
- Round trips exposed in the UI.
- Vehicle stats: MPG and gas price only, or EV too.
- Public geocoder: self-hosted Photon or a paid provider.
- Attribution text beyond a footer line.
