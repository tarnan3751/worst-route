# Worst Route

A joke web app on OpenStreetMap data: the user sets a start, an end and optional stops in the
US, and the app computes a hilariously inefficient route that **never repeats an intersection
and never drives the same road twice**, respecting one-way streets. Python everywhere except
the frontend (TypeScript + MapLibre GL JS). Solo developer + Claude.

This file is the **map**: what the project is, where each area lives, the commands, the
conventions and the workflow. The design is in `docs/PLAN.md` (architecture, pipeline, engine,
API, frontend, milestones, risks); the scope, status, roadmap and decisions are in
`docs/project_overview.md`; how each built area works is in its **reference file** under
`docs/reference/`, written by whoever builds the area.
Read this file, then `docs/PLAN.md`'s sections for the areas a task touches, then the reference
file of every built area it touches, then the module header comments of the modules involved.
A section named in bold is a heading of this file or of a doc.

## Status

**The bootstrap is built**: the uv project, the gate wired to ruff, mypy and pytest, and the
CLI. Every pipeline step is real (`docs/reference/pipeline.md`), from `download` to `save`, which
writes the build, and `qa`; Portland's build passes QA. The graph package is built too
(`docs/reference/graph.md`): a build's arrays and on-disk format, the backend protocol, its
pure-Python reference backend and the CSR + Numba production backend (the default, M2's first
step), snapping and masks. So is the engine (`docs/reference/engine.md`):
the route and its splice, the seed route, the detours, the worsen loop, the distance objective,
the validator, and `worst-route plan` / `validate` on a saved build. So is the API
(`docs/reference/api.md`, M2's second step): `worst-route serve`, jobs in worker processes, the
route streamed over Server-Sent Events, the route store and "make it worse". So are the
inefficiency targets (M2's fourth step): a ratio or a duration, reached within ±2 %, from
`plan --target` and the API. So is the frontend (`docs/reference/frontend.md`, M2's third
step): a MapLibre map on which stops are clicked, the route drawn growing live from the stream,
the run controls, the settings (the targets among them) and links. So is the geocoding proxy
(M4's first piece): a place search in the stop list, answered by `GET /geocode` from Nominatim
behind a cache and a one-request-a-second limit. The plan (`docs/PLAN.md`,
2026-10-30) is written and its open questions answered as far as `docs/project_overview.md` §3
records. **M1 is built**: in the acceptance run on the
Portland build (`uv run python tools/acceptance.py --region portland --pair-seed 2026 --pairs 20
--seed 1 --budget 30`) all 20 pairs plan, the validator passes all 20, and the median ratio is
21.5× (`docs/project_overview.md` §2.2). The default backend (`auto`) is the CSR backend on
every build, Portland's turn tables included; it plans the reference's routes byte for byte.
`docs/project_overview.md` holds the roadmap (§2.2) and the decisions (§3).

## Stack (pinned)

- **Python 3.12**, managed by **uv** (`.python-version`, `uv.lock`). Not 3.14: the geo stack's
  wheels are uneven there.
- **pyosmium 4** for reading `.osm.pbf`; **OSMnx 2.1** for `consolidate_intersections`
  (cleanup's stage A) at city/state scale (it never loads data; it cannot read PBF;
  simplification is our own, since its `simplify_graph` would lose the shape points);
  **NumPy** CSR arrays as the graph, searched by a pure-Python reference backend (M1) and
  **Numba** kernels in production (M2), with **networkx** as the tests' oracle;
  **shapely 2**, **scipy**, **pyarrow**; **FastAPI** + **uvicorn** with SSE for progress;
  **matplotlib** for the share image; **typer** for the CLI; **pytest** + **hypothesis**.
- **Frontend: Vite + TypeScript, no framework, MapLibre GL JS.** `npm` only under `frontend/`.
- **Data: Geofabrik state extracts**, never the 11 GB US file. Census boundaries and Gazetteer
  for states and towns.

Check before writing library calls. **OSMnx 1.x versus 2.x** API drift is the most common
mistake here (`consolidate_intersections` and `simplify_graph` changed parameter names in 2.0),
then Leaflet idioms written against MapLibre. The installed truth is the virtualenv:
`uv run --no-sync python -c "import inspect, osmnx; print(inspect.signature(osmnx.simplification.consolidate_intersections))"`,
or grep `.venv/lib/python3.12/site-packages/`.

## Commands

Every command below exists. One a later milestone adds is marked with that milestone, as
*(M3)*, until it lands.

| What | Command |
|---|---|
| The gate (sync, lint, types, tests, frontend typecheck/build/test, leftovers) | `tools/gates.sh` |
| Install / refresh the virtualenv | `uv sync` |
| Run all tests | `uv run pytest` |
| Run some tests | `uv run pytest -k splice` |
| Tests that need a built graph (skipped by the gate) | `uv run pytest -m data` |
| Lint and format check | `uv run ruff check . && uv run ruff format --check .` |
| Types | `uv run mypy src` |
| Pipeline, one step (all eight are real, run in this order; `filter` and `build` also take `--source PATH`, `cleanup` and `tiers` take `--allow-fragmented`) | `uv run worst-route pipeline <download\|filter\|build\|cleanup\|enrich\|tiers\|save\|qa> --region portland` |
| Plan a route from the CLI, validated (`--target ratio:20` or `duration:9h` aims for a target within ±2 %; `--base route.json` continues a saved route) | `uv run worst-route plan --region portland --from LAT,LON --to LAT,LON --seed 1 --budget 30 --out route.json` |
| Validate a saved route | `uv run worst-route validate route.json` |
| API server (the coordinator only; agents use `--port 0` or a port of their own, `--store` in their own tree, and `--geocoder none` or `--nominatim-url` pointed at a local stub: no agent calls Nominatim) | `uv run worst-route serve --region portland` |
| Frontend dev server / build / typecheck (with the prettier check) / tests / format (the dev server proxies `/api` to `WORST_ROUTE_API`, default `http://127.0.0.1:8000`; agents point it at their own server and add `-- --port <free>`) | `cd frontend && npm run dev` / `npm run build` / `npm run typecheck` / `npm test` / `npm run format` |
| Land a delivered series | `tools/land_series.sh <patch-dir> <base-commit> <tree-hash>` |

**Run the gate after every change.** The gate never downloads anything and never needs `data/`;
tests that need a built graph carry the `data` marker and run on demand.

`WORST_ROUTE_DATA` names the data directory (default `./data`). Built graphs are immutable unless
`pipeline save --force` replaces one, which changes its `graph_version`, so a lane may **read**
the main tree's `data/graphs/` through that variable; it **writes** only into its own.

## Where things live

Code paths are the plan's (`docs/PLAN.md` §1.2); a reference file is written when its area is
first built and listed here when it exists.

| Area | Code | Plan | Reference |
|---|---|---|---|
| Pipeline: download, filter, build, exclusions, simplify, consolidate (stages A and B), carriageway twins, connectivity, enrich, tiers, save (to the graph package's build format), qa | `src/worst_route/pipeline/` | §2 | `docs/reference/pipeline.md` |
| Graph: a build's arrays and on-disk format, the backend protocol, the reference and CSR backends, Numba kernels, spatial (snapping, leash, bubbles), test builders, loader | `src/worst_route/graph/` | §2.7, §3.5, §3.9, §3.10 | `docs/reference/graph.md` |
| Engine: `Route`, snapping, the seed route, detours, the worsen loop, the validator, exact mode | `src/worst_route/engine/` | §3.1–3.4, §3.11–3.13 | `docs/reference/engine.md` |
| Objectives and constraints: the interface, each mode, the alphabet search | `src/worst_route/engine/objectives/`, `alphabet_search.py` | §3.6–3.8 | `docs/reference/engine.md` (**Objectives**) |
| API: jobs, SSE, routes, geocode proxy, workers | `src/worst_route/api/`, `geocode/` | §4 | `docs/reference/api.md` |
| Presentation: directions, stats, comparisons, share image, jokes | `src/worst_route/presentation/` | §4.3, M4 | *(when built)* `docs/reference/presentation.md` |
| Frontend: map, stops, settings, run controls, toasts, directions, share | `frontend/src/` | §5 | `docs/reference/frontend.md` |
| Testing and tools: the gate, lanes, landing, fixtures, benchmarks | `tests/`, `tools/` | §8 | `docs/reference/testing-tools.md` |

## Architecture rules

These are what make the joke true (the route really never repeats) and reproducible (a seed
really gives the same route), so treat them as hard rules.

### Legality is enforced in one place and proven in another

- `Route.splice()` is the **only** mutation of a route, and it refuses anything that would
  reuse a node, an edge, a twin edge, a banned transition or an interior stop.
- `engine/validator.py` proves a finished route legal from the raw data (original OSM node
  ids, cluster membership, twins, transitions, stops, constraints, leash, recomputed metrics).
  **Every route the CLI prints or the API returns has passed the validator**; one that fails
  is an error, never a result.
- Consolidation (stage A signal clusters, stage B interchange and roundabout super-junctions)
  and carriageway twins are what the rule rests on; a change to them is tier A.

### Determinism

- Randomness comes from one `numpy.random.Generator(PCG64(seed))` passed down; never the
  global `numpy.random`, `random`, or set iteration order in the hot loop (arrays only).
- `(graph_version, seed, request)` → the same route, byte for byte. The golden tests under
  `tests/golden/` freeze that for the fixture builds; they change only under a tier A brief
  that says so.
- `graph_version` is the sha256 in a build's `meta.json`; a route records it.

### Masks, not copies

- The engine never copies or subgraphs the graph. Tier, leash, bubbles, forbidden nodes and
  banned edges are boolean masks passed to the kernels.
- Only `graph/` touches the CSR arrays; objectives and the engine go through `GraphBackend`.
  The reference (pure-Python) and Numba backends give identical paths on the fixtures (the
  tie rule in `docs/reference/graph.md`); networkx is the tests' oracle.

### Objectives are terms, the engine is one loop

- A mode is an `Objective` (node, edge, pair and global terms, feasibility hooks, search
  guidance); a constraint is an objective with only hooks. The worsen loop does not know
  which mode it runs. The one exception is the alphabet mode's constructive driver, which
  shares kernels and validator but not the loop (`docs/PLAN.md` §3.8).
- `objective.delta()` equals a full re-score on every accepted splice; tests assert it.

### Pipeline and data

- Steps are idempotent and content-hashed; `data/` is gitignored and never read by the gate.
- **Tests use the fixtures** under `tests/fixtures/osm/`, never `data/`, and never the network.
  A test that needs a built graph carries `@pytest.mark.data`.
- The on-disk format is `docs/PLAN.md` §2.7 (one `.npy` per array, `strings.json`,
  `meta.json`; `docs/reference/graph.md`); a change to it bumps the build id and is tier A.
- Speeds, tier definitions, class allowlists and defaults live in `src/worst_route/config.py`
  or data files, not scattered in code.

### API and frontend

- The API is the only contract between backend and frontend; the shapes are
  `docs/reference/api.md`. A shape change is tier A.
- Coordinates: `[lon, lat]` in GeoJSON, `{lat, lon}` in request objects. All metrics metric;
  the frontend converts.
- No framework in `frontend/`; MapLibre owns the DOM of the map.

### Outside services and their policies

- Nominatim: backend proxy only, ≤ 1 request/s, a search button (no type-ahead), cached,
  `User-Agent: worst-route/<version> (<contact>)`. OSM raster tiles: hobby volume locally,
  a vector basemap before anything public. **No test, gate, agent or fixture calls either.**
- Downloads happen only through `pipeline download`, run by a person, never by the gate.

## Folder layout

```
docs/            PLAN.md (the design); project_overview.md (scope, status, roadmap, decisions);
                 reference/ (one file per built area); adr/ later
data/            gitignored: raw/, filtered/, aux/, graphs/<region>/<build_id>/
src/worst_route/ config, cli; pipeline/, graph/, engine/ (objectives/), presentation/, geocode/, api/
frontend/        Vite + TS app: src/{main,state,api,map,stops,settings,run,toasts,directions,share}
tests/           fixtures/osm/*.osm, unit/, graph/, pipeline/, engine/, api/, property/, golden/, bench/
tools/           gates.sh, land_series.sh, acceptance.py (the M1 acceptance run); later: fetch_census.sh, bench
.claude/agents/  scout, researcher, developer, reviewer
.github/         workflows/gate.yml: the gate on every push and pull request
```

## Python and TypeScript gotchas for this project

- OSMnx 2.x: `simplify_graph(G, edge_attrs_differ=…)` (1.x called it `endpoint_attrs`);
  `consolidate_intersections(G, tolerance=…)` takes a per-node dict and `max_length` in 2.1 and
  **removes dead ends unless `dead_ends=True`**. Projected graphs only.
- OSMnx and networkx `MultiDiGraph` edges are keyed `(u, v, key)`; parallel edges are real.
- pyosmium objects are views: copy what you keep out of a handler (`way.id`, tag values), never
  the object itself.
- shapely 2 is vectorised: `STRtree.query(..., predicate=…)` over arrays, not Python loops.
- Numba kernels take plain arrays and scalars; no Python objects, no dicts; masks are `uint8`.
- `multiprocessing` on macOS uses `spawn`: workers import the package fresh and must mmap, never
  receive a graph by pickling.
- Build arrays are `.npy` files read with `np.load(mmap_mode="r", allow_pickle=False)`; NumPy
  cannot memory-map `.npz` members. Parquet via `pyarrow`.
- Bearings are degrees clockwise from north; turn deltas are wrapped to `(−180, 180]`.
- GeoJSON is `[lon, lat]`. MapLibre's `LngLat` is `[lng, lat]`. Requests are `{lat, lon}`.
- 4 spaces in Python, 2 in TypeScript (`.editorconfig`); ruff formats Python.

## Seeing the app

The coordinator runs the API and the frontend on the main tree
(`uv run worst-route serve --region portland`, `cd frontend && npm run dev`) and looks through
the browser (the `claude-in-chrome` skill, or the `run` skill); that is the coordinator's
instance on the default ports. Agents see the app in their own clones through the CLI
(`worst-route plan` and its validator), the API tests (`httpx` against the app in-process), the
frontend's tests (`npm test`), and the frontend itself: `serve` and `npm run dev` on free ports of
their own, driven by headless Chrome over the DevTools protocol with the tile server blocked,
screenshots to their scratch directory (`docs/reference/frontend.md`, **Seeing it**); a
Playwright script under `tools/` writing to `tools/out/` (gitignored) is a later item. "It
parses" is the floor; a route printed with its validator report, or a screenshot read, is
proof.

## Agent workflow

Work on this repo is split across a coordinator and four project subagents (`.claude/agents/`).
The split is by judgment required, and by how much context each seat can afford to hold.

- **Coordinator** (the top-level session) — talks to the owner, makes product and feature
  decisions or escalates them, writes briefs (each naming its **tier**, below), approves plans
  where a plan is required, routes work and review findings, reads the real diff of every tier A
  change and of every fold, and lands series. It does not read large files itself (the scout
  does), and does not write feature code in the main tree: trivial edits only, when spawning an
  agent would cost more than the edit.
- **`developer`** (Opus) — works in a clone of its own (**Lanes**), implements the brief, runs
  `tools/gates.sh` at every commit, verifies behaviour through the CLI, the tests and the API in
  its clone, updates the docs the change invalidates, and delivers a patch series on the current
  `main` with the tree it reproduces. Never writes to the main working tree. Plans first only
  when the brief asks (tier A) or when it meets an open decision. **Continue the same agent from
  plan to implementation** (`SendMessage`); respawning loses what it learned planning.
- **`reviewer`** (Opus) — independent review of the series, applied in a clone of its own: the
  full pass for tier A, the targeted pass for tier B (**Tiers**). Report-only; it is given the
  code and the brief, never the developer's conclusions.
- **`scout`** / **`researcher`** (Sonnet) — read-only codebase lookups (including library
  signatures from the virtualenv) and outside research (OSM tagging, OSMnx, pyosmium, MapLibre,
  FastAPI, Census files, usage policies). Neither runs the pipeline, the server or the tests. A
  load-bearing research fact (a library behaviour, a tag convention, a policy limit) is verified
  by Opus or the coordinator before a decision rests on it.

**Tiers.** Every brief names its tier, which sets the plan and the review:

- **A** — anything the legality or reproducibility of routes rests on: `Route.splice`, the
  validator, the kernels and their mask semantics, consolidation stages A and B, twin detection,
  the seed route, the on-disk format and `graph_version`, the `Objective` interface, the API
  request/response shapes, the rng plumbing, a new dependency, and any change to a golden
  route or an existing test's expected values. A plan before code, approved by the coordinator,
  who escalates product decisions to the owner; then the implementation; then the full review:
  the validator run on many seeds, determinism probed, backend parity, every fixture, the gate,
  the CLI run on the Portland build when one exists.
- **B** — a new objective or constraint on the existing interface, pipeline enrichment (speeds,
  collectibles, brands, places), presentation (directions, stats, share image), frontend work,
  tooling and QA reports, config values. No plan round trip: the brief carries the decisions
  and the developer stops only at an open decision. The targeted review, about thirty minutes:
  the diff read, the gate run, the changed behaviour driven once (a CLI run, a test, a
  screenshot).
- **C** — docs, comments, config. No reviewer: the gate and the coordinator's own read.

Small later-scope items are **batched** by area, up to about five a slice, one commit each, one
brief and one review.

**Briefs.** A brief is the whole of what an agent knows about the task, so it carries:

- **Title and tier.**
- **Goal** — what changes for the user or the tooling, in two or three sentences, and what is
  explicitly out of scope.
- **Decisions made** — every choice the coordinator has already settled (names, defaults,
  thresholds, which module, which file), so the developer never has to guess; for tier A, the
  plan is where remaining choices surface.
- **Read** — `CLAUDE.md`, the plan sections and reference files of the areas touched, and any
  module or doc the task depends on; the scout's findings when there are any, as `path:line`.
- **Verification** — the tests expected, and what the run must show (which CLI command or API
  call, on which build or fixture, what the validator report or screenshot must contain).
- **Lane** — the clone to use, which data directory it may read, whether it may run a heavy
  job (a state-scale pipeline step, a benchmark), and the base commit the series must be built on.
- **Delivery** — the plan format (tier A) or the report format, as the agent definition states.

**Lanes.** The main working tree is the coordinator's and stays at `HEAD`: no agent writes to
it, its index, its `.venv` or its `data/`. A developer or a reviewer works in a `git clone` of
its own under its scratch directory; `uv sync` there builds the virtualenv from the lockfile
(the uv cache makes it seconds) and `npm ci` under `frontend/` when the frontend exists. Three
developers may run at once on disjoint areas, under these rules: a lane reads built graphs
through `WORST_ROUTE_DATA` pointing at the main tree's `data/` (builds are immutable unless
`pipeline save --force` replaces one, which changes its `graph_version`) and writes pipeline
output only under its own `data/`; nothing in a lane downloads from
Geofabrik, Census, Nominatim or a tile server; a lane's server binds a free port, never the
default; heavy jobs (a state-scale build, a benchmark) run one at a time, which the brief
arranges; the second lane rebases its series onto `main` before delivery; a lane's docs changes
stay within its area's reference file, the map only where a status line changes.

**Review routing.** The reviewer's findings go back to the **same** developer (`SendMessage`),
which folds them into the series; the coordinator reads the fold's diff; then the reviewer
re-reviews. A FAIL loops until it passes; PASS WITH FIXES is folded and read, re-reviewed when
the fold touched tier A ground. Anything the developer did outside the brief (**Adjacent
changes**) is kept or vetoed by the coordinator, not the reviewer.

**Delivery and landing.** A series is `git format-patch` files on the current `main`, one
logical change per commit (refactors separate from features, an imperative sentence-case
subject, a body of plain paragraphs saying what and why, **no trailers**: no commit in this
repo carries one, the coordinator's own included), with the write-tree hash it reproduces and
the gate's tail per commit. Once the review's findings are folded and the fold's diff read, the
coordinator lands it with `tools/land_series.sh <patch-dir> <base-commit> <tree-hash>` (clean
tree and HEAD at the base required; `git am`; the tree checked, with a rollback on mismatch; the
gate run; the commits left in place). The coordinator pushes `main` to `origin` after each
landing, once an `origin` exists.

Constraints:

- **Decisions come from `docs/project_overview.md`** (§3) and the plan. An agent that finds an
  open decision stops and reports; it does not guess.
- **The architecture rules above are not negotiable**, and neither are: fixtures not `data/`
  in tests, no network in tests or the gate, masks not copies, the validator on every result,
  seeded rng only, golden routes frozen outside tier A, no new dependency outside tier A, no
  framework in the frontend, the usage policies.
- **Temporary instrumentation is tagged `[verify]`** and gone before a report:
  `grep -rn "\[verify\]" src tests tools frontend/src` returns nothing.
