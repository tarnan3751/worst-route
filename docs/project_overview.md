# Worst Route — project overview

Last updated: 2026-10-30. Snapshot taken after M1's bootstrap landed: the plan is written and
the project scaffolding is built; no part of the app exists yet.

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
- **Engine** (Planned): a shortest route through the stops, then a seeded detour-splice
  heuristic that makes it worse under a pluggable objective, with a validator that proves the
  result legal. `docs/PLAN.md` §3.
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

The pipeline's first three steps are built (`docs/reference/pipeline.md`): `pipeline download`
fetches and md5-verifies a Geofabrik state extract, `filter` keeps the drivable roads, clipped
to the region's bbox, and a POI table, and `build` makes the raw directed graph with the one-way
rules applied, saved as an intermediate stage.
The workflow scaffolding exists: `CLAUDE.md`, the four agents,
`tools/gates.sh`, `tools/land_series.sh`, this overview, `docs/PLAN.md`. The M1 bootstrap is
built: the uv project (Python 3.12, `src` layout, the M1 dependencies), the gate running ruff,
mypy and pytest for real, locally and in GitHub Actions, and the `worst-route` CLI (`version`,
and `pipeline <step>`: `download`, `filter` and `build` real, the other steps stubs that exit as
not implemented yet).

### 2.2 Roadmap

Milestones as `docs/PLAN.md` §7 lays them out; each ends with something runnable.

| Milestone | Status | Summary |
|---|---|---|
| M1 — Portland, distance mode, CLI | In progress | bootstrap and the pipeline's first three steps (download, filter, build) built; remaining: the rest of the Portland pipeline (cleanup to qa), the graph package (networkx backend), the engine (snapping, seed route, splice loop, distance objective), the validator, `worst-route plan` printing a validated route and its ratio |
| M2 — API + map, live growth | Planned | CSR + Numba backend; FastAPI jobs + SSE; MapLibre frontend; targets by ratio and duration; reroll, make it worse |
| M3 — Modes and constraints | Planned | turn-aware kernel, never/only-left; slowest time; collectibles; brands; hard mode; stage B transition tables; alphabet search |
| M4 — Presentation | Planned | directions, stats, toasts, comparisons, share image, geocoding proxy, share links |
| M5 — Scaling up | Planned | Oregon; array-based simplify/consolidate; US at T1; the national decision gate; public-hosting prep |
| M6 — Stretch | Planned | certified worst (CP-SAT), turn restrictions, stop reordering, portfolio runs |

### 2.3 Known limitations

None recorded yet; the risks the plan anticipates are `docs/PLAN.md` §8.3.

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

- **networkx is the reference backend; CSR arrays + Numba are the production backend**, behind
  a `GraphBackend` protocol from M1. igraph/rustworkx are not expected to be needed: the hot
  loop is masked, bounded Dijkstra, which they do not offer without copying.
- **OSMnx for its cleanup algorithms, not for loading**; array-based replacements for
  `simplify_graph` and `consolidate_intersections` at national scale (M5).
- **State extracts, never the full US file**; cross-border ways deduplicated by id.
- **On-disk format: Parquet + NPZ + `meta.json`, memory-mapped**; not GraphML or pickle.
- **Consolidation in two stages**: OSMnx stage A with per-node tolerances (15 m on arterial
  crossings, 8 m elsewhere, `max_length=60`, dead ends kept) and a custom stage B that turns
  interchanges and roundabouts into super-junctions with allowed-transition tables.
- **Divided carriageways are linked as twins** detected on raw way segments (same ref/name,
  opposite bearing, lateral distance by class) and banned conservatively, not merged.
- **Tiers T0–T4 as masks on one master graph**, auto-selected by trip length, with local
  all-roads bubbles around stops and a leash along the stop polyline.
- **Three detour strategies** (waypoint, random-weight Dijkstra, local exhaustive) mixed by the
  objective; strict improvement for max modes; a stopping rule for target modes.
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
