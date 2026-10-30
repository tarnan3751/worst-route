# Worst Route — project overview

Last updated: 2026-10-30. Snapshot taken after the pipeline's last steps landed: the plan is
written; the project scaffolding, every pipeline step, the graph package and the engine are
built; Portland has a saved build, and the M1 acceptance run on it passes (§2.2): M1 is built.

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
  CSR + Numba backend since M2's first step; target modes and the other modes are later
  milestones.
- **API and frontend** (Planned): FastAPI jobs with Server-Sent Events progress, a MapLibre map
  that shows the route getting worse live, settings, reroll, make it worse. `docs/PLAN.md` §4–5.
- **Modes** (Planned): longest distance, slowest time, never/only turn left, collect toll booths /
  rail crossings / roundabouts / bridges, brand pilgrimage, alphabet route, hard mode; targets
  by ratio or duration.
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
for the masked Dijkstra and the simple-path search, byte-identical to the reference and 8–13×
faster on large searches; whole runs on a build without transition tables gain 1.5–1.9×. The
engine and `worst-route plan` / `validate` use it by default (`--backend auto|csr|python`),
except on a build with transition tables, which it cannot search yet: there, Portland's among
them, the default (`auto`) takes the reference.
The engine is built (`docs/reference/engine.md`): the route model whose splice is its only
mutation and refuses any repeat, the request with its tier, leash and bubble masks (a tier also
bans lower-class roads away from the stops), snapping with the re-snap fallback, the baseline
and the seed route with node-disjoint legs (and loops for round trips), searches that see the
transition tables and avoid driving both carriageways of a road, the three detour strategies, the worsen loop with its ambition radius and budget, the objective interface with
the distance mode, the validator that proves a route from the original OSM ids, route files,
and `worst-route plan` / `validate`, which run on any saved build. A golden route freezes the
engine's output on a test lattice.
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
| M2 — API + map, live growth | In progress | CSR + Numba backend built (the default; byte-identical to the reference); remaining: its edge-state kernel (until then `auto` takes the reference on a build with transition tables, Portland's among them); FastAPI jobs + SSE; MapLibre frontend; targets by ratio and duration; reroll, make it worse |
| M3 — Modes and constraints | Planned | turn-aware kernel, never/only-left; slowest time; collectibles; brands; hard mode; the search honouring stage B transitions (built at M1); alphabet search |
| M4 — Presentation | Planned | directions, stats, toasts, comparisons, share image, geocoding proxy, share links |
| M5 — Scaling up | Planned | Oregon; array-based simplify/consolidate; US at T1; the national decision gate; public-hosting prep |
| M6 — Stretch | Planned | certified worst (CP-SAT), turn restrictions, stop reordering, portfolio runs |

### 2.3 Known limitations

The risks the plan anticipates are `docs/PLAN.md` §8.3. Found on the Portland build:

- **Only the reference backend searches transition tables.** The engine's searches see them
  (Portland has 11,378 rows at 657 super-junctions, 180 of which forbid some turns), through the
  reference backend's Python edge-state search; `CsrBackend` has no kernel for it yet, so
  `auto` takes the reference on Portland (the `backend:` line says why), and `plan --backend
  csr` there says so in one line.
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
  cluster or super-junction include the stub to its centre: 15% of the edges leaving a stage A
  cluster have a `bearing_out` more than 30° off the road's own, 55% at interchanges, whose
  stubs run 66 m (median) to the centroid. Both are M3's to refine, before turn modes.
- **A possible stage B over-restriction**: the I-84 / NE Halsey super-junction (node 65437 of
  the 2026-10-30 Portland build) forbids NE 42nd Ave → NE Sandy Blvd, a turn that looks
  drivable; to investigate in the next cleanup fold.

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
  uniformly in the ball, each kept for raising the acceptance median. `auto` takes the
  reference on a build with transition tables until the CSR edge-state kernel lands; `validate`
  refuses a route made by another engine version.
- **Alphabet mode is a constructive search**, not a splice objective.
- **SSE for progress, not WebSockets.**
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
