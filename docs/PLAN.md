# Worst Route — Project Plan

The least efficient way to get anywhere in the US. The user sets a start, an end, and optional stops; the app computes a hilariously inefficient route that **never repeats an intersection and never drives the same road twice**, respecting one-way streets.

Plan written 2026-10-30, before any code existed.

## 0. Decisions already made

| Question | Decision |
|---|---|
| Prototype city / state | Portland, OR / Oregon (Geofabrik `oregon-latest.osm.pbf`, 243 MB) |
| Route crosses itself on an overpass (no shared node) | **Not a violation.** The rule is about nodes and road units. |
| Hard mode highway identity | **`ref` first, `name` fallback**, both normalized |
| Stop order | **Fixed**, as given. Reordering is a possible later toggle. |
| Hosting | **Local now, designed for public later** |
| OSM turn restrictions (`restriction=*` relations) | **Ignored for now.** Designed-in hook, later milestone. |
| Frontend | **Vanilla TypeScript + Vite**, map library **MapLibre GL JS** (justified in §5) |
| Python tooling | **uv**, Python pinned to **3.12** |
| Dev machine | Apple M5, 10 cores, 24 GB RAM, ~138 GB free disk |

Stack push-backs (details in §1.3):

1. **networkx is the tests' oracle, not a backend.** The engine's hot loop is "bounded Dijkstra with forbidden node/edge masks". Neither igraph nor rustworkx offers masked search without copying the graph, so the graph is **CSR arrays**, searched behind a `GraphBackend` protocol from day one: a pure-Python reference backend over the arrays in M1, **Numba kernels** in production from M2. igraph/rustworkx stay possible implementations of the protocol but are not expected to be needed.
2. **OSMnx is used for its cleanup algorithms, not for loading.** It cannot read `.osm.pbf`. We parse with pyosmium, build a `MultiDiGraph` in OSMnx's attribute conventions, then call `consolidate_intersections` (simplification is our own: OSMnx's `simplify_graph` would lose the shape points). At national scale networkx memory is prohibitive, so simplification and consolidation get array-based reimplementations in the scaling milestone.
3. **Never download `us-latest.osm.pbf` (11.4 GB).** Download the 50 state extracts instead. Same bytes in total, but each state is filtered independently, cached, resumable, and parallelizable. Ways that cross state lines appear in both extracts and are deduplicated by OSM id when merged.

---

## 1. Architecture overview and repo layout

### 1.1 Runtime shape

```
 browser (MapLibre, TS)  ──HTTP/JSON + SSE──▶  FastAPI (uvicorn, asyncio)
                                                   │
                                                   ├── geocode proxy (Nominatim now, Photon later) + sqlite cache
                                                   ├── job manager: queue, cancel, progress fan-out
                                                   │        │
                                                   │        ▼
                                                   │   a worker process per job (spawn), at most cores − 2 at once
                                                   │     each mmaps data/graphs/<region>/<build>/*.npy  (shared page cache)
                                                   │     runs engine.plan_route with progress hooks → a queue of events
                                                   │
                                                   ├── route store (sqlite): route_id → request, seed, node ids, graph build id
                                                   └── presentation: directions, stats, comparisons, share.png (matplotlib)

 offline:  pipeline CLI  download → filter → build → cleanup → enrich → tiers → save → qa
```

Key properties:

- **One master graph per region, tiers and leash are masks.** The engine never copies the graph. A request builds a node mask (leash ∧ (tier ∨ local bubbles around stops)) and the Dijkstra kernels take masks as arguments.
- **Graph files are memory-mapped NumPy arrays.** Worker processes share them through the OS page cache, so N workers cost ~1× graph memory, not N×.
- **The engine is a generator.** Every accepted detour yields a `ProgressEvent`. The CLI prints them, the API streams them as Server-Sent Events, tests consume them as a list.
- **Routes are data, not sessions.** A route is `(graph_build_id, seed, request, node_ids)`. "Make it worse", share links and the validator all take that tuple.

### 1.2 Repo layout

```
worst-route/
├── pyproject.toml              # uv project, src layout, [project.scripts] worst-route = "worst_route.cli:app"
├── uv.lock
├── .python-version             # 3.12
├── README.md
├── docs/
│   ├── PLAN.md                 # this file
│   └── adr/                    # short decision records as decisions get revisited
├── data/                       # gitignored
│   ├── raw/<state>-latest.osm.pbf
│   ├── filtered/<region>.roads.osm.pbf, <region>.pois.parquet
│   ├── aux/                    # Census state boundaries, Gazetteer places, maxspeed defaults
│   └── graphs/<region>/<build_id>/   # see §2.7
├── src/worst_route/
│   ├── config.py               # pydantic-settings: data dir, regions, budgets, geocoder, UA string
│   ├── cli.py                  # typer app: `pipeline ...`, `plan ...`, `validate ...`, `serve`, `bench`
│   ├── pipeline/
│   │   ├── download.py         # Geofabrik fetch + md5 + cache
│   │   ├── filter.py           # pyosmium tag filters → roads pbf + POI parquet
│   │   ├── build.py            # pbf → nodes/ways tables → raw MultiDiGraph (OSMnx conventions)
│   │   ├── exclusions.py       # highway allowlist, access/service rules, implied oneway
│   │   ├── simplify.py         # degree-2 contraction (ours on networkx now, arrays later)
│   │   ├── consolidate.py      # stage A: OSMnx consolidate; stage B: interchange + roundabout super-junctions
│   │   ├── carriageways.py     # divided-highway twin detection
│   │   ├── connectivity.py     # SCC per tier, fragmentation report
│   │   ├── enrich.py           # maxspeed, identities, collectibles, brands, states, places
│   │   ├── tiers.py            # tier masks
│   │   ├── store.py            # the save step: the cleaned graph as GraphData, written by graph/store.py (§2.7)
│   │   └── qa.py               # GeoJSON/HTML QA report per build
│   ├── graph/
│   │   ├── data.py             # GraphData: one build's arrays, the format table, validation
│   │   ├── store.py            # write and mmap-read a build directory, graph_version (§2.7)
│   │   ├── backend.py          # GraphBackend protocol + Route-independent primitives
│   │   ├── python_backend.py   # pure-Python reference implementation over the arrays (M1; networkx is the tests' oracle)
│   │   ├── csr_backend.py      # NumPy CSR + Numba kernels (M2+)
│   │   ├── backends.py         # make_backend: csr, python, or auto (csr when numba imports)
│   │   ├── kernels.py          # numba: masked bounded dijkstra (node-based), edge-state dijkstra (turn-aware), bounded DFS
│   │   ├── spatial.py          # cKDTree snapping, leash mask, bubble mask
│   │   ├── testing.py          # hand-built graphs for tests (make_graph, grid_graph)
│   │   └── loader.py           # LRU of loaded builds over store.read_build
│   ├── engine/
│   │   ├── route.py            # Route dataclass, splice(), used-sets, metrics cache
│   │   ├── snap.py             # stop snapping incl. pass-through capability + outward fallback
│   │   ├── seed_route.py       # initial shortest route through stops with node-disjoint legs
│   │   ├── detour.py           # find_detour strategies: waypoint, random-weight, local-exhaustive
│   │   ├── worsen.py           # the splice loop (generator), budget, acceptance, ambition radius
│   │   ├── objectives/
│   │   │   ├── base.py         # Objective protocol, Terms (node/edge/pair/global), Composite
│   │   │   ├── distance.py  time.py  turns.py  collect.py  brand.py  hardmode.py  alphabet.py
│   │   ├── alphabet_search.py  # constructive search for alphabet mode (not splice-based)
│   │   ├── exact.py            # stretch: CP-SAT certified worst for tiny subgraphs
│   │   └── validator.py        # the proof-of-legality checker
│   ├── presentation/
│   │   ├── directions.py  stats.py  comparisons.py  share_image.py  jokes.py
│   ├── geocode/
│   │   ├── base.py  nominatim.py  cache.py  limiter.py  service.py  fake.py   # as built (M4); photon.py later
│   └── api/
│       ├── app.py  routes_api.py  schemas.py  jobs.py  worker.py  sse.py  store.py
├── frontend/                   # Vite + TS, see §5
├── tests/
│   ├── fixtures/osm/*.osm      # hand-written OSM XML: grid, divided highway, cloverleaf, roundabout, cul-de-sac, private roads
│   ├── unit/  graph/  pipeline/  engine/  api/  property/
│   └── bench/
└── scripts/                    # one-off helpers (fetch Census files)
```

### 1.3 Python dependencies

Core: `pyosmium` (4.x), `osmnx` (2.1, brings `networkx`, `geopandas`, `shapely` 2, `pandas`), `numpy`, `scipy` (cKDTree, sparse csgraph for SCC), `numba`, `pyarrow` (Parquet), `pydantic` 2, `fastapi`, `uvicorn`, `sse-starlette`, `httpx`, `typer`, `matplotlib` + `pillow` (share image), `pyproj`.
Dev: `pytest`, `hypothesis`, `ruff`, `mypy`, `pytest-benchmark`.
Optional/stretch: `ortools` (certified-worst mode), `python-igraph` or `rustworkx` only if a second backend is ever wanted.

Why Python 3.12: numba, pyosmium, osmnx and shapely all ship wheels for it; 3.14 wheels for the geo stack are still uneven.

---

## 2. Data pipeline

Every step is a `worst-route pipeline <step>` subcommand with `--region`, `--build-id`, and `--force`. Steps are idempotent and cache on content hashes, so re-running a later step reuses earlier outputs. A `justfile` chains them. Regions are named configs: `portland` (bbox clipped from Oregon), `oregon`, `us-t1`, `us`.

### 2.1 `download`

- Fetch `https://download.geofabrik.de/north-america/us/<state>-latest.osm.pbf` + `.md5` into `data/raw/`. Verify md5, skip if present.
- Record the Geofabrik timestamp (`osmium.io.Reader` header) in the build metadata.
- National: loop over the 50 states + DC with 4 parallel downloads. Alaska/Hawaii optional (flag), they only matter if a user puts a stop there.

### 2.2 `filter`

pyosmium 4 `FileProcessor` with C++-side filters, output via `BackReferenceWriter` so node references are completed:

```python
fp = osmium.FileProcessor(src, osmium.osm.WAY).with_filter(osmium.filter.KeyFilter("highway"))
with osmium.BackReferenceWriter(dst, ref_src=src) as w:
    for way in fp:
        if way.tags["highway"] in KEEP_CLASSES and not excluded(way.tags):
            w.add(way)
```

- **Kept classes:** `motorway, motorway_link, trunk, trunk_link, primary, primary_link, secondary, secondary_link, tertiary, tertiary_link, unclassified, residential, living_street`. Also `service` **only** when `service=alley` (named alleys are real streets in Portland); every other `service` way is dropped.
- **Dropped at filter time** (cheap, before anything touches memory): `footway, path, track, cycleway, steps, pedestrian, bridleway, corridor, busway, bus_guideway, raceway, escape, construction, proposed, abandoned, platform, rest_area, services, road`; `area=yes`; access for cars `private` or `no`, by OSM's hierarchy (`motorcar` if present, else `motor_vehicle`, else `access`); `service in {driveway, parking_aisle, drive-through, emergency_access}`. Ferries are `route=ferry`, never `highway=*`, so they never enter.
- **Second pass for POIs and collectible nodes** (same `FileProcessor`, `NODE|WAY`, `KeyFilter`): `barrier=toll_booth`, `railway=level_crossing`, `highway in {stop, traffic_signals}` (for the slowest-time mode), and any object with `brand` or `brand:wikidata`. Written to `data/filtered/<region>.pois.parquet` with lon/lat (ways: the mean of their node locations).
- **Portland:** clip the Oregon roads pbf to the metro bbox (`45.25..45.72 N, -123.05..-122.35 W`) with a pyosmium `GeoInterfaceFilter`/bbox check on node coordinates before `BackReferenceWriter`. Keep complete ways that touch the bbox so the clip edge has no dangling half-ways.
- Cross-state ways appear in two state files; `build` dedupes by way id.
- Expected sizes: Oregon roads pbf ~40–60 MB; all states combined ~2.5–3 GB (all drivable), ~0.7 GB at tertiary and up.

### 2.3 `build` — pbf → raw directed graph

1. Stream nodes into arrays (`id → lon, lat`) and ways into (`way_id, node_ids[], tags`) with pyosmium. Use `osmium.index` location store only if needed; for roads-only files an in-memory dict/array is fine up to state scale, a sorted `int64` array + `searchsorted` beyond.
2. **Junction detection:** a node is a junction if it is referenced by ≥2 kept ways or is an endpoint of a way. Everything else is a shape point.
3. **Split ways at junctions** into segments; each segment is one raw edge (plus its reverse if two-way). Store original way id, tags, and the ordered OSM node ids of the segment (geometry).
4. **One-way rules:** `oneway in {yes, true, 1}` → forward only; `oneway=-1` or `reverse` → backward only; `junction in {roundabout, circular}` → forward only; `highway in {motorway, motorway_link}` with no `oneway` tag → forward only (OSM convention, same as OSRM's car profile); `oneway=no` → both; `oneway=reversible/alternating` → both (joke app, accept).
5. Output a networkx `MultiDiGraph` with OSMnx's attribute conventions (`x`, `y` on nodes; `osmid`, `length`, `oneway`, `highway`, `name`, `ref`, `geometry` on edges; `G.graph["crs"]`), so OSMnx functions apply unchanged. Project to a local CRS (UTM 10N for Oregon; EPSG:5070 CONUS Albers nationally) before any metric step.

### 2.4 `cleanup`

Order matters: simplify → consolidate A → carriageway twins → consolidate B (which carries the twins across) → connectivity. Built; how each step works, its constants and the stage it writes are `docs/reference/pipeline.md`, **cleanup**.

**a. Exclusions** (`exclusions.py`): applied way by way in `filter` and again in `build` (§2.2); cleanup has no exclusions pass of its own. Anything the filter cannot decide at way level (e.g. edges inside `access=private` areas) would join here later if needed.

**b. Simplify** (`simplify.py`, ours): OSMnx's `simplify_graph` rebuilds a merged edge's geometry from its nodes' coordinates and would lose the shape points the raw graph keeps inside each edge, so the contraction is our own. Self-loops are dropped first (no simple route can use one). A degree-2 node is contracted only when the two edges of each through pair carry the same class, `oneway`, `reversed` and every stored tag, and never when the merge would repeat an OSM node id (a chain closing on itself).
Keeping endpoints wherever the tags change keeps every edge **homogeneous**: one name, one ref set, one class, one speed limit. The alphabet mode, hard mode, directions and the twin detector all rely on that. OSM ways are often split end to end, so this still removes many nodes (15% of Portland's), but fewer than a purely topological contraction would.
Each simplified edge keeps: summed `length`, the full coordinate list, the tuple of original way ids (`osmids`), and the original OSM node ids (`osm_nodes`, needed by the validator, §8).

**c. Consolidate, stage A — signal clusters and divided-arterial crossings** (`consolidate.py`):
`osmnx.simplification.consolidate_intersections(G, tolerance=tol_by_node, rebuild_graph=True, max_length=60, dead_ends=True, reconnect_edges=True)`.
- `tolerance` is a per-node dict (OSMnx 2.1 supports this), the first rule that matches: 1 m for nodes on a roundabout edge (stage B owns roundabouts; at 8–15 m stage A would swallow most small rings), 15 m for nodes touching `primary`/`secondary`/`trunk` at-grade (medians 20–30 m wide create 4-node crossings), 8 m for everything else. Buffers overlap at the sum of two tolerances, so Portland's 61 m downtown blocks never merge.
- `max_length=60` means nodes only merge when they are also connected by edges ≤ 60 m. This is what prevents a cloverleaf's 300 m loop ramps from fusing endpoints. It also prevents merging the two ends of a short overpass (no connecting edge).
- `dead_ends=True`: OSMnx's default *removes* dead-end nodes; we need them (stops at cul-de-sacs).
- Original node ids of merged nodes land in `osmid_original`; OSMnx drops an edge whose two ends merged without record, so our post-pass adds its interior OSM node ids too, and keeps the lot as the node's `members`.

**d. Consolidate, stage B — interchanges and roundabouts become super-junctions** (`consolidate.py`):
This is the piece the no-repeat rule actually depends on, and OSMnx does not do it.
1. Build the undirected graph of **ramp edges** (`*_link`) plus **roundabout edges** (`junction=roundabout|circular`).
2. Each connected component of that subgraph, together with the mainline/cross-road nodes where its ramps attach, is a candidate cluster. The ramps of one interchange rarely share a node (a diamond's two sides, a cloverleaf's eight ramps; Portland's I-84/181st diamond is six components), so two components are **joined** when the roads at their attachment nodes share at least two identities (normalised ref set, else normalised name) and some of their attachment nodes lie within 500 m of each other: an interchange joins two roads, while consecutive interchanges share only the freeway. A cluster whose links are all minor (`primary_link`, `secondary_link`, `tertiary_link`: slip lanes) also takes in the at-grade node it bypasses, any node that two of its attachment nodes reach through roads of two different identities of at most 150 m; a slip lane and the crossing it bypasses are one junction, while a node reached along one road only is no crossing. Each cluster then takes in its **pockets**: the interior nodes of a path of at most 600 m along one road from one of its attachment nodes to another, outside it (a bridge between two ramp terminals); otherwise the road would leave the super-junction and come back to it, and no route could go straight through. Each super-junction records how many roundabout rings it holds, for the roundabout collectible.
3. Guard: if a cluster's bounding-box diagonal exceeds 1.5 km (collector-distributor lanes can chain interchanges), split it by single linkage (`eps=300 m`) of the attachment nodes, the other nodes joining the nearest group. Roundabout clusters are never split.
4. Replace the cluster's nodes with **one super-node** at the centroid. Edges entirely inside the cluster (ramps, the mainline through the interchange, the roundabout ring) leave the routing graph but are kept with the super-node (`internal`), for drawing and directions.
5. **Allowed transitions:** for each (in-edge, out-edge) pair of the super-node, an internal directed path from the in-edge's end to the out-edge's start makes the pair allowed. Computed from M1, since `Route.splice` enforces them even while the search ignores them; it is the same table that OSM turn restrictions will use later.
Result: passing under an interchange on the mainline and later looping through its ramps both touch the super-node, so the second is impossible. Two mainlines crossing on a plain overpass with no ramps share no node and stay independent, matching the overpass decision.
A roundabout is one junction: the route can enter it once. This also makes "most roundabouts" a per-node collectible.

**e. Divided-highway twins** (`carriageways.py`):
OSM maps each direction of a divided road as a separate one-way way. Going north on I-5 and later south on the same stretch would otherwise be legal.
Detection, per build, on the stage A graph (before stage B makes stretches of road internal to its super-junctions), on the **segments of the edges' geometry** (between consecutive shape points, so the original way segments, less the stub stage A adds to reach a merged node) because the two carriageways' junctions never line up 1:1:
1. Candidates: one-way, non-`_link` edges with class ≥ `tertiary`, roundabout rings aside.
2. Signature: normalized ref set (see §3.6 for normalization) if any ref, else normalized name. Edges with neither are candidates only if class ∈ {motorway, trunk}.
3. Spatial index: `shapely.STRtree` on candidate segments. For each segment `s`, query within `D` (motorway 100 m, trunk 80 m, primary 50 m, others 35 m).
4. Pair `s` with `t` when: same signature, and the same name less directional words when both have one and neither is a motorway (a one-way couplet a block apart shares a ref, not a name; freeway carriageways carry alias names); bearings differ by 180° ± 30°; the midpoint of each is within `D` of the other's line, and the foot of at least one perpendicular falls on the other segment (they run side by side, not end to end); `s` and `t` are on different edges that share no OSM way (a way curling back beside itself is one road).
5. `twins(E) = { F : some segment of E pairs with some segment of F }`, symmetric; stored per edge with the graph (a CSR adjacency in the build format).
6. Stage B carries the table across: an edge that became internal to a super-junction maps to its boundary edges on the same road along its own carriageway, so going through the interchange on one carriageway bans the other carriageway's stretch beside it, inside or outside the interchange; and a transition that must drive such an edge bans its twin on the transition's in-edge and out-edge (a per-transition ban table is the exact form, planned with M3's transition-aware search). A passage through a super-junction that must drive both edges of a twin pair inside it (a U-turn at a median's end within the junction) is banned.
Semantics: **using edge E bans every edge in twins(E).** This is conservative (a 1 km edge twinned with a 600 m and a 400 m edge bans both entirely), which is the right direction for a "never" rule. QA emits a GeoJSON of pairs for eyeballing; the unit test uses a synthetic divided highway with mismatched junctions.
`dual_carriageway=yes` is rare and unreliable; it is a hint, not the detector.

**f. Connectivity** (`connectivity.py`):
- Strongly connected components (networkx, at cleanup and again per tier in `tiers`, §2.6). Keep the largest. Report dropped node count, total dropped length, and the 20 largest dropped components (size, length, centroid) — a large dropped component means a filtering bug (e.g. a town whose only access got excluded), not noise.
- Thresholds that fail the build: all-roads graph must retain ≥ 99% of nodes (`pipeline cleanup` fails unless `--allow-fragmented`); each tier ≥ 95% of its own nodes.
- Fragmentation test for tiers: a fixture list of city pairs per region (Portland–Salem, Portland–Bend, …) must be mutually reachable at every tier that is supposed to cover that trip length.

### 2.5 `enrich`

Built for M1 without brands, states and places (M3, M4); how it works and Portland's numbers are `docs/reference/pipeline.md`, **enrich**.

- **Speed:** parse `maxspeed` ("45 mph", "35" in mph by US convention, "50 km/h", "signals", "none" → class default; multi-values take the first). Defaults by class (mph): motorway 65, trunk 55, primary 45, secondary 35, tertiary 30, unclassified 25, residential 25, living_street 15, alley 10, `_link` 35. `time_s = length / speed`. Node delay for `highway=stop` (10 s) and `traffic_signals` (30 s) feeds the slowest-time mode: the largest among a node's merged junctions (a signal cluster delays once); a stop sign or signal mapped as a shape point before the junction adds none in M1.
- **Identity (hard mode):** `identity_id` per edge: normalized ref set if present (`"I 5"`, `"I-5"`, `"Interstate 5"` → `I 5`; `"US 26;OR 8"` → both refs, identity = the first after sorting by class I > US > state (a state's postal code or `SR`) > other, alphabetically within a class; a `Business`/`Spur`/`Alt` suffix stays on its ref, so such a route never takes its mainline's identity), else normalized name (case-folded, abbreviations expanded: `St→Street`, `NE→Northeast`, directional suffixes kept). Dictionary of identities saved with the build.
- **Twin units:** `unit_id` groups the forward/backward edges of a two-way road (the edges whose original node lists run opposite ways; the unit's id is the smaller edge id; implied by node-simplicity, except in a two-edge round trip out along a road and back, which `Route.splice` and the validator refuse by unit); the twins table from §2.4e stays a table of its own.
- **Bearings:** `bearing_out` over the first 20 m of the geometry (leaving u) and `bearing_in` over the last 20 m (arriving at v), as §3.6's turn rule reads them.
- **Collectibles:** toll booths and level crossings are nodes on ways; map each to the simplified edges whose original node list contains it (both directions of a two-way road; a route uses one) → `toll_booth_n`, `level_crossing_n` per edge; one at a junction counts on the node's lowest-id out-edge until node collectibles (M3). `bridge=yes` per edge. Roundabout → super-node flag. Stop signs/signals → node delay.
- **Brands:** POIs with `brand` or `brand:wikidata`; snap each to the nearest kept edge within 50 m (STRtree nearest on edge geometries). Table `pois(brand_key, wikidata, name, lon, lat, edge_id, offset_m)`; `brand_key` = `brand:wikidata` when present else normalized brand name. Index by brand for the `/brands` autocomplete.
- **States:** Census cartographic boundary file `cb_2024_us_state_500k` → `state_fips` per node (STRtree `contains` on node points).
- **Places:** Census Gazetteer `2024_Gaz_place_national.txt` (GEOID, NAME, LSAD, ALAND, INTPTLAT/INTPTLONG). Store as a table with `radius_m = clamp(sqrt(ALAND/π), 400 m, 12 km)`. "Passed through" is evaluated at request time (§3.9).
- **Pass-through capability** per node: exists `u→v→w` with `u ≠ w` (needed for intermediate stops, §3.2).

### 2.6 `tiers`

| Tier | Classes | Auto-selected for straight-line trip length |
|---|---|---|
| T0 | motorway, trunk (+links) | > 800 km |
| T1 | + primary (+links) | 250–800 km |
| T2 | + secondary (+links) | 60–250 km |
| T3 | + tertiary (+links) | 15–60 km |
| T4 | + residential, unclassified, living_street, alleys | < 15 km |

Each tier is an edge mask on the master graph plus a node mask after re-running SCC within the tier: the tier's nodes are the largest strongly connected component of its own roads, and its edges those roads between kept nodes; `pipeline tiers` fails when a tier keeps less than 95% of the nodes its roads touch (`docs/reference/pipeline.md`, **tiers**). Node ids never change across tiers, so a manual override at runtime is just a different mask.
**Local bubbles:** at request time, the active mask is `leash ∧ (tier_mask ∨ within(R, any stop))` with `R` = 3 km of all-roads graph around each stop, so a cross-state trip still starts from the user's actual street instead of snapping 10 km to the nearest primary road. The request also bans every edge outside the tier's edge mask unless both its ends lie in a bubble, so a lower-class road between two of the tier's own nodes stays out away from the stops (`docs/reference/engine.md`, **Requests**).

### 2.7 `save` — on-disk format

**One `.npy` file per array + JSON**, one directory per build; `docs/reference/graph.md` is the reference (every array with its dtype, shape and invariants). Not GraphML (XML, slow, Portland alone would be hundreds of MB), not networkx pickle (Python-object-heavy, version-fragile, ~1.5 KB per edge, cannot be memory-mapped), and neither NPZ nor Parquet for the arrays the engine reads: NumPy memory-maps `.npy` files only (`.npz` members are read whole), and Parquet is not mappable.

```
data/graphs/<region>/<build_id>/
  meta.json          region, build_id, crs_epsg, created, pipeline_version, counts, params (build parameters),
                     sources (files + md5 + OSM timestamp), sha256 "graph_version"
  strings.json       names, refs, identities: the tables edge_name / edge_ref / edge_identity index into
  arrays/<name>.npy  one file per array:
    nodes            node_lon/lat/x/y, node_kind (plain|cluster|interchange|roundabout), node_state (FIPS),
                     node_pass_through, node_delay_s; member_indptr + member_osm_ids (original OSM node ids per node)
    edges            edge_u/v (int32), length_m, speed_kmh, time_s (float32), highway (enum), tier, name, ref,
                     identity, unit, flags (bridge, tunnel, toll, roundabout, link), toll_booths, level_crossings,
                     bearing_in/out (int16 degrees); way_indptr + way_osm_ids
    adjacency        out_indptr + out_edges, in_indptr + in_edges (each node's edges in ascending edge id)
    geometry         coord_indptr + coords (float32 [N,2] lon/lat), osmnode_indptr + osmnode_ids (int64)
    twins            twin_indptr + twin_edges: CSR adjacency edge → twin edges, symmetric
    transitions      trans_node, trans_in, trans_out: allowed (in_edge, out_edge) pairs at super-nodes, sorted
    tiers            tier_node_mask [T0..T4, n], tier_edge_mask [T0..T4, m], after SCC
  stages/            the pipeline's intermediate state between its steps (docs/reference/pipeline.md)
  qa/                qa.json; GeoJSON: twin pairs, super-junctions, dropped components, nodes the tiers dropped
  later, with their steps:
  internal_paths     coordinate runs for drawing through super-junctions (stage B)
  pois.parquet       brand POIs (§2.5)
  places.parquet     Gazetteer places with radius_m (national file, shared)
  qa/                snapping oddities; qa.html summary
```

Built: `pipeline save` writes it from the enriched graph and the tiers (`docs/reference/pipeline.md`, **save**), with `params` holding the configuration each step ran with, and refuses a super-junction whose transition table allows no turn, which the format cannot say. Edge ids are sorted by (u, v, original order), so each node's out-edges are one contiguous run of ascending ids. `graph_version` is the sha256 over each array's name, dtype, shape and bytes in a fixed order, then `strings.json`, then the build `params`; nothing else in `meta` enters it, so rewriting the same build reproduces it. `graph/store.py` writes a build into its build directory, beside the pipeline's `stages/`: it stages the files in a temporary sibling directory, moves `strings.json` and `arrays/` in, and moves `meta.json` in last as the commit marker, so a reader never sees half a build. A directory that already has `meta.json` is refused, so a build is never overwritten; `arrays/` or `strings.json` without `meta.json` are a crashed write's leftovers and are replaced.

Why: `np.load(mmap_mode="r")` makes load time ~0 and lets worker processes share memory; `.npy` is language-neutral if a Rust kernel ever replaces Numba. Parquet exports of the node and edge tables, for DuckDB and QGIS, become a `qa` convenience later.

### 2.8 `qa`

Per build: counts by class and tier, SCC drop report, twin-pair statistics (pairs per class, unpaired one-way motorway length — should be near zero), super-junction count and size histogram, 50 random snap tests, and a map HTML with the GeoJSON layers. The build is "accepted" only when the QA thresholds in §2.4f pass. Built for M1 (`docs/reference/pipeline.md`, **qa**): `qa.json` with the counts, the reports, the twin coverage, the histogram and the thresholds (twin coverage ≥ 0.95, SCC retained ≥ 0.99, every tier ≥ 0.95), and the GeoJSON layers; the snap tests and the map page are later. This is the fastest way to catch the classic failures (a ramp class missing, all of I-84 unpaired, a bridge tagged `access=private`).

---

## 3. Engine design

### 3.1 Data model

```python
@dataclass
class Route:
    nodes: list[int]            # vertex-simple across the whole trip
    edges: list[int]            # len(nodes) - 1; edges[k] goes nodes[k] → nodes[k+1]
    stop_idx: list[int]         # positions of the stops in `nodes`; stop_idx[0] == 0, stop_idx[-1] == len(nodes)-1
    used_nodes: BitMask         # numpy bool array sized n_nodes (fast membership for kernels)
    banned_edges: BitMask       # edges used ∪ twins(used); the request's and the objective's bans are per window (§3.4)
    ban_count: ndarray          # as built: per edge, how many route edges ban it (itself or a twin), so a release costs the window
    score: float
    metrics: Metrics            # length_m, time_s, per-objective counters; recomputed lazily
    seed: int
    graph_version: str

@dataclass
class Splice:                   # the only mutation
    i: int; j: int              # replace nodes[i..j] inclusive run's interior, i < j, by detour
    detour_nodes: list[int]     # starts with nodes[i], ends with nodes[j]
    detour_edges: list[int]
```

Rules enforced by `Route.splice()` and re-checked by the validator:

- interior nodes of the detour ∉ `used_nodes` after removing the interior of `nodes[i..j]`;
- no detour edge ∈ `banned_edges` (minus the bans contributed by the removed run) or in the request's and objective's bans for the window, no two twins inside the detour, and no two-edge round trip out and back along one road unit;
- stops are never interior to a removed run (`i` and `j` may equal stop positions; stop positions are shifted after the splice);
- super-node transitions (`in_edge, out_edge`) allowed at every junction, including the two boundary junctions of the splice;
- round trips (start == end) are allowed: the end node is exempt from the used-check only as the final node.

### 3.2 Snapping stops

- KD-tree on every node's projected coordinates (cKDTree built at load, ~2 s for 5M nodes; cached per build), filtered by the active mask at query time.
- Start needs `out_degree ≥ 1`, end needs `in_degree ≥ 1`, intermediate stops need `pass_through` (exists `u→v→w`, `u ≠ w`). A stop at the end of a cul-de-sac cannot be passed through without reusing the mouth node, so it snaps to the nearest pass-through node and the response says so: `"Stop 2 moved 140 m to the nearest through street"`.
- Dead-end *neighbourhoods* (a subdivision with one entrance) cannot be detected cheaply per node, so the seed-route step handles them: if a leg cannot be built, re-snap its earlier stop if it is a via, then its later stop (a via or the end; the start never moves), each to its next-nearest candidates (k = 1..10, expanding radius), retrying after each. After those candidates, fail with `STOP_UNREACHABLE_WITHOUT_REPEAT` and name the stop. A via trapped in such a subdivision walks out, and the stops after it stay.
- Stops stay on distinct nodes: a via avoids the other stops' nodes, and so does the end, except in a round trip: a request whose start and end points are identical, where the end may take the start's node. A point-to-point request never becomes a round trip.
- Snap distance and the chosen node are returned in the `snapped` event so the UI can show the marker jump.

### 3.3 Seed route: shortest path through all stops with node-disjoint legs

Finding a simple path a→b→c in a directed graph is NP-hard in general (two vertex-disjoint paths), so this is heuristic too, but it almost always works on road networks:

```
def seed_route(G, stops, mask):
    used = {}                                   # nodes used by committed legs
    legs = []
    for (s, t) in consecutive(stops):
        p = dijkstra(G, s, t, weight=length, forbid_nodes=used - {s}, mask=mask)
        if p is None:
            # penalty re-solve: push earlier legs away from what this leg needs
            for round in 1..4:
                q = dijkstra(G, s, t, weight=length, mask=mask)             # unconstrained
                conflict = set(q) ∩ used
                re-solve the earlier legs that own `conflict` with penalty ×8 on conflict nodes
                recompute used; p = constrained dijkstra again
                if p: break
        if p is None: raise LegInfeasible(stop_index)   # caller re-snaps per §3.2
        legs.append(p); used |= set(p)
    return concat(legs)
```

As built (`docs/reference/engine.md`, **Seed and baseline**): each leg also avoids every other stop's node and bans the twins of the other legs' edges, and on a build with transition rows is searched with the edge-state search, continuing the leg before it and handing over to the leg after it, so every turn, at the vias too, is allowed; a leg that drives an edge and its twin is searched again without the later one (up to 8 times); a leg that still fails, or a seed `check_route` refuses, re-snaps the leg's earlier stop up to 10 times if it is a via, then its later stop (a via or the end; never the start), then names the leg (`stop_index` stays its later stop, for the API's error). A round trip with no stop between its ends has no legs: its seed is a loop through a waypoint at least 500 m from the start.

The baseline for the inefficiency ratio is the **unconstrained** shortest route through the stops in order (sum of plain Dijkstra legs, within the request's masks), which is what a sane GPS would do. As built, each leg turns only where the transition tables allow (independently of the leg before it, so the baseline is never longer than the seed), and falls back to the turn-blind path when the cheapest legal one would pass a node twice.

### 3.4 The worsen loop (splice heuristic)

```
def worsen(G, route, objective, budget, rng, mask) -> Iterator[ProgressEvent]:
    best = route
    radius = objective.initial_radius(route)        # "ambition": how far a detour may wander, metres
    fail_streak = 0
    while not budget.exhausted(route):
        # 1. choose a segment to replace: a single edge (j = i+1) most of the time,
        #    a short run of up to K=4 edges sometimes (frees interior nodes, lets the route shrink/reshape)
        i, j = pick_segment(route, rng, K)           # never spans a stop as an interior node
        freed = interior nodes of route.nodes[i..j]
        forbid = route.used_nodes - freed - {nodes[i], nodes[j]}
        banned = route.banned_edges - bans_from(route.edges[i..j-1]) | objective.banned_edges(route, i, j)

        # 2. find a candidate detour
        detour = find_detour(G, nodes[i], nodes[j], forbid, banned, mask, radius, objective, rng,
                             entry_edge=route.edges[i-1], exit_edge=route.edges[j])
        if detour is None: fail_streak += 1; radius = max(radius/2, r_min) if fail_streak % 20 == 0 else radius; continue

        # 3. score it
        delta = objective.delta(G, route, i, j, detour)   # exact for node/edge/pair terms; global terms re-evaluated
        cand_ok = objective.feasible_window(G, route, i, j, detour)   # hard mode stretch check, turn feasibility at boundaries
        if not cand_ok or not accept(delta, route, objective, budget): fail_streak += 1; continue

        # 4. commit
        route = route.splice(i, j, detour); fail_streak = 0; radius = min(radius * 1.5, r_max)
        yield ProgressEvent(iter, route.score, route.metrics, splice=(i, j, detour))
        if objective.better(route, best): best = route
        if budget.target_reached(route): break
    return best
```

`accept()`:

- **maximise** modes: strict improvement `delta > 0`.
- **target** modes (ratio or duration): accept if the new value does not overshoot `target × 1.02`; when it would, shrink `radius` and retry; when within `[0.98, 1.02] × target`, stop. If the route is above target (after "make it worse" with a new, smaller target) run-replacement with a *shorter* detour (`delta < 0`) is accepted until under target. As built (M2 step 4; `docs/reference/engine.md`, **Targets**): a `Goal` owns "better" and "done" in place of `objective.better`, by the route's value (the ratio, or `time_s`); growing, a detour is taken when it gains and its value rises to at most the band's top (an overshoot is a failure, so the radius halves as after any 20 failures); shrinking, when it loses and its value falls to at least the band's bottom, with the radius at its floor, runs of 6 edges on average (at most 24) and the local-exhaustive paths that lose ranked by closeness to the band; the smallest overshooting detour is taken when a whole cycle accepts nothing, then the run shrinks; the run stops with `"target"` within the band, and the best route is the one closest to the target.
- Optional later: simulated-annealing acceptance for run replacements to escape local optima. Not in M1.

`budget`: wall-clock seconds (default 20, max 120), max iterations, and a no-improvement cutoff; the run reports `stopped_because` = `budget` (seconds or iterations), `converged`, or `target` (a target run within its band). As built, the run goes in cycles: `fail_streak > 400 and radius == r_min` ends a cycle, which resets the radius to `objective.initial_radius`, and the run is `converged` only when 3 cycles in a row accept nothing. Tuned on the M1 acceptance (`docs/reference/engine.md`, **The loop**): a run of edges is 1 + geometric (mean 3, at most 12), not up to K=4, and with probability 0.3 a window starts at one of the 5 route edges with the most unused active nodes near. Always returns the best route so far. `ProgressEvent` carries the splice so the client can apply it to its own copy; every 50th event carries a full snapshot for resync.

**Why routes do not converge to "the same giant snake":** the leash and tier bound the arena, the ambition radius bounds each detour, and both the segment pick and the waypoint sample are seeded. Two seeds on the same request should give edge sets with Jaccard similarity < 0.3; this is a test (§8).

### 3.5 `find_detour`: three strategies, mixed by the objective

All three run on the active mask with `forbid`/`banned` masks passed to the kernels (no graph copies).

1. **Waypoint** (default weight 60%). Sample a waypoint `w` from allowed, unused nodes with probability ∝ `exp(-dist(w, midpoint(a,b)) / radius)`; `w` is drawn from the objective's preferred pool when it has one (uncollected toll booths, brand POIs, slow residential nodes). Then `p1 = dijkstra(a → w)` and `p2 = dijkstra(w → b, forbid ∪ p1)`, both bounded by `max_cost = 4 × radius` so a hopeless search stops early (as built: 8 × radius, and half the waypoints drawn uniformly over the 4 × radius ball, tuned on the M1 acceptance). Detour = `p1 + p2`. Expected gain scales with `radius`, which is what makes "target duration" controllable.
2. **Random-weight Dijkstra** (30%). `dijkstra(a → b)` with per-edge weights `length × U(0.2, 5.0)` drawn from the seeded rng for the edges leaving the nodes within `4 × radius` of the midpoint (the others keep their length, so the draws do not grow with the graph), bounded by the same `max_cost` in scaled cost. Produces plausible wiggles and usually succeeds when waypoint search fails; on a one- or two-edge window the scaled direct edge usually wins, which is no detour.
3. **Local exhaustive** (10%, only when `dist(a,b) < 800 m`; for farther ends the draw is waypoint 2/3, random weight 1/3). Bounded DFS enumerating simple paths `a → b` within a ball of radius `3 × dist(a,b)` capped at 400 m, depth ≤ 25 edges, cap 5,000 paths, after a Dijkstra probe that skips the enumeration when `b` is unreachable; pick the best by `objective.delta`. This is the exact local optimiser and the same code path the certified mode generalises. The cap exists because the reference backend's DFS grows exponentially with the ball (a 4-edge window: 7–13 s uncapped, 0.3 s capped, `docs/reference/engine.md`); the M2 kernels may relax it.

A candidate equal to the removed run is not a detour, and a candidate `Route.splice` would refuse (two twins inside it; a turn a transition table forbids, which the transition-aware search no longer proposes) is a failed attempt.

Search weights come from `objective.search_weights(G)` so e.g. the slowest-time mode runs Dijkstra on `length × speed` (prefers slow roads on the way to the waypoint).

When the objective has pair constraints (turn modes) or a transition table matters, strategies 1–2 use the **edge-state Dijkstra kernel** (state = edge, relax only to successor edges with an allowed pair), seeded from `entry_edge` and accepted only on an edge whose pair with `exit_edge` is allowed. Otherwise the cheaper node-state kernel is used. As built in M1 (`docs/reference/engine.md`, **Searches**): on a build with transition rows every path the engine builds is searched transition-aware (`GraphBackend.dijkstra_edges` for the seed, the baseline and strategies 1–2, the DFS with its turn checks for strategy 3), on either backend (the reference's Python search in M1, the CSR kernels from M2); two searches stay turn-blind by design, the local-exhaustive reachability probe (a legal path exists only where a turn-blind one does) and the baseline's fallback when the cheapest legal path would pass a node twice. On a build without rows the node-state search. Turn modes' `feasible_pair` joins in M3.

### 3.6 Objective interface

```python
class Objective(Protocol):
    key: str

    # additive terms, precomputed per build as vectors (None = zero)
    def node_terms(self, G) -> np.ndarray | None: ...      # e.g. roundabout = 1, signal delay = 30 s
    def edge_terms(self, G) -> np.ndarray | None: ...      # e.g. length, time, toll_booth_n, brand POI count
    # consecutive-edge term and feasibility (turn direction, bridge-run counting)
    def pair_term(self, G, e_in: int, e_out: int) -> float: ...
    def feasible_pair(self, G, e_in: int, e_out: int) -> bool: ...
    # anything non-additive; evaluated on the whole route
    def global_term(self, G, nodes, edges) -> float: ...           # the route's arrays, so a candidate is scored before it is spliced
    def feasible_window(self, G, route, i, j, detour) -> bool: ...
    def banned_edges(self, G, route, i, j) -> BitMask | None: ...   # extra bans beyond twins
    # search guidance
    def search_weights(self, G) -> np.ndarray: ...
    def waypoint_pool(self, G, route) -> np.ndarray | None: ...     # candidate node ids, or None = any
    def initial_radius(self, route) -> float: ...
    # targets and reporting
    def target_value(self, G, route) -> float: ...                  # the quantity the target modes compare
    def describe(self, G, route) -> dict: ...                       # mode-specific numbers for toasts

score(route) = Σ node_terms[v] + Σ edge_terms[e] + Σ pair_term(e_k, e_k+1) + global_term(nodes, edges)
```

`Objective.delta(route, i, j, detour)` is implemented once in the base class: it sums node/edge terms over the removed run and the detour, re-evaluates the pair terms at the two boundaries plus inside the detour, and calls `global_term` on the candidate only if the objective declares `has_global = True`. `Composite(primary, secondary)` implements lexicographic objectives as `primary × 10⁹ + secondary` (lengths are < 10⁷ m, so no overflow in float64).

Turn classification (used by `pair_term`/`feasible_pair` and by directions): `delta = wrap(bearing_out(e_out) − bearing_in(e_in))` in `(−180, 180]`; straight `|delta| ≤ 30`, right `30 < delta ≤ 160`, left `−160 ≤ delta < −30`, U-turn beyond. Bearings are the first/last 20 m of each edge's geometry, stored per edge.

Normalisation of refs for identities: strip spaces/hyphens, uppercase, map `INTERSTATE→I`, `US HIGHWAY|US HWY→US`, `STATE ROUTE|SR|OR|WA…→<state>`; multi-value refs (`;`) keep all; the identity is the highest-class ref.

### 3.7 How each mode plugs in

| Mode | Terms | Search guidance | Fits splice? |
|---|---|---|---|
| **Longest distance** (default) | `edge_terms = length_m` | weights = length; waypoints anywhere | Yes, perfectly |
| **Slowest time** | `edge_terms = time_s`, `node_terms = signal/stop delay` | weights = `length × speed` (prefers slow roads); waypoint pool biased to residential/living_street nodes | Yes |
| **Never turn left** | constraint: `feasible_pair` rejects left and U-turn-left; objective = distance (or any) | edge-state Dijkstra; boundary pairs checked in `feasible_window` | Yes, with the edge-state kernel. Can make a stop unreachable (report `INFEASIBLE_TURN_CONSTRAINT`, suggest relaxing). |
| **Only turn left** | interpreted as **never turn right**; straight is allowed (otherwise four lefts on a grid return to the start node and the mode is dead on arrival) | same | Yes, same caveat |
| **Collect: toll booths, rail crossings** | `edge_terms = count on edge`, Composite with length | waypoint pool = edges with uncollected items; `initial_radius` larger | Yes. Items on an edge are counted once because an edge is used once. |
| **Collect: bridges** | `pair_term = 1` when `e_out.bridge and not e_in.bridge` (counts bridge *runs*, so a bridge split by a junction counts once) | waypoint pool = bridge edges | Yes |
| **Collect: roundabouts** | `node_terms = 1` on roundabout super-nodes | waypoint pool = roundabout nodes | Yes, **only because** roundabouts are super-nodes (§2.4d). Without that, the route could use two different arcs of the same ring. |
| **Brand pilgrimage** | `edge_terms = POI count for brand_key on edge`, Composite with length | waypoint pool = POI edges | Yes. "Pass" = drive the edge the POI is snapped to (≤ 50 m). |
| **Hard mode** (constraint, any objective) | `banned_edges` = every edge whose `identity_id` is already in the route, except identities of `edges[i−1]` and `edges[j]` (so a stretch can be extended at the splice boundary); `feasible_window` recounts stretches in the changed window and rejects if any identity has > 1 maximal run | — | Yes, with the window check. Base rule (twins) is a special case of this with identity = carriageway pair. |
| **Alphabet route** | letters completed, then length | — | **No.** See §3.8. |
| **Target ratio / duration** | not an objective: a stopping rule on `target_value` (length or time) plus the acceptance rule in §3.4 | radius shrinks as the target nears | Yes |

Objectives and constraints compose: `objective = Composite(Collect("toll_booth"), Distance)`, `constraints = [NoLeft(), HardMode()]`. Constraints are objectives with zero terms and only feasibility/ban hooks; the engine chains their `feasible_*` and `banned_edges`.

### 3.8 Alphabet route: constructive search instead of splicing

Splicing a detour into the middle of an alphabet route breaks the letter sequence of everything after it, so this mode runs a different driver on the same graph, kernels and validator.

Definition: along the route, the sequence of **named** roads (consecutive edges with the same name collapse to one) must start with letters A, B, C, … in order, wrapping Z→A. Unnamed roads and `_link` ramps are free connectors. Score = letters completed, then length. The final leg from the last alphabet road to the destination is a free leg (otherwise the mode almost never arrives).

```
def alphabet_route(G, start, end, mask, rng, budget):
    best = None
    for restart in budget:                                   # restarts with different rng draws
        pos = start; used = {start}; k = 0; route = []
        while True:
            # candidate next roads: edges reachable from `pos` via current-name edges or free connectors,
            # whose name starts with letter(k) — found with a masked multi-target Dijkstra limited to those edge classes
            cands = reachable_named_edges(G, pos, letter(k), allowed=current_name ∪ free, forbid=used, radius=R)
            if not cands: break
            # Warnsdorff-style choice: prefer candidates with many letter(k+1) roads nearby, with rng noise
            e = weighted_choice(cands, key=lookahead_count(letter(k+1)), rng)
            path = connect(pos, e)                           # the Dijkstra path, node-simple
            route += path; used |= path; pos = head(e); k += 1
            # ride the chosen road as far as useful (random stop point), then continue
        tail = dijkstra(pos → end, forbid=used, mask)         # free leg; if None, try end from an earlier prefix
        if tail: candidate = route + tail; best = max(best, candidate, key=(letters, length))
    return best
```

Letters like Q, X and Z are rare, so the realistic outcome is "got to W, then drove to the destination". The UI reports the letters achieved. `budget` and progress events work the same way (each completed letter yields an event). "Make it worse" on an alphabet route = more restarts, keep best.

### 3.9 Request-time masks and stats

- **Leash mask:** node allowed iff distance to the polyline through the stops ≤ `L`. `L` auto = `clamp(0.6 × straight-line trip length, 3 km, 150 km)`, user-adjustable. Computed vectorised with shapely 2 (`distance` of all active nodes to one LineString; ~0.5 s for 5M nodes).
- **Tier + bubbles:** §2.6.
- **Towns passed through:** `places` whose centre lies within `radius_m` of the route geometry (STRtree on place circles vs route LineString). Count distinct, list the first 10 by order encountered.
- **States visited:** distinct `state_fips` over route nodes, in order of first appearance.
- **Network fraction:** route length ÷ total edge length of the active mask.

### 3.10 Graph backend protocol

```python
class GraphBackend(Protocol):
    n_nodes: int; n_edges: int
    def out_edges(self, v) -> ndarray[int]        # edge ids
    def in_edges(self, v) -> ndarray[int]
    def head(self, e) -> int; def tail(self, e) -> int
    def dijkstra(self, src, dst, weights, forbid_nodes, banned_edges, mask, max_cost) -> Path | None
    def dijkstra_edges(self, src, dst, weights, entry_edge, exit_edge, forbid_nodes, banned_edges, mask, max_cost) -> Path | None   # transition-aware; M3 adds pair_ok
    def multi_target(self, src, targets_mask, weights, forbid_nodes, banned_edges, mask, max_cost) -> list[Path]
    def simple_paths_dfs(self, src, dst, forbid_nodes, banned_edges, mask, max_depth, max_paths) -> list[Path]
    def scc_mask(self, edge_mask) -> node_mask
```

- `PythonBackend` (M1): pure Python + `heapq` over the build's CSR arrays; the reference implementation, which networkx checks in the tests. M1's surface is `dijkstra`, `dijkstra_edges` (the transition-aware search, `docs/reference/graph.md`), `simple_paths_dfs` (with entry and exit edges and turn checks) and `largest_scc_mask` (this block's `scc_mask`); the other calls arrive with their users. ~0.3 µs per relaxed edge measured on the dev machine (`docs/reference/graph.md`).
- `CsrBackend` (M2): NumPy arrays + Numba kernels (`dijkstra_masked`, `simple_paths_dfs`, and the transition-aware `dijkstra_edges_masked` and `simple_paths_dfs_turns`), byte-identical to the reference for arguments of the declared types; 0.01–0.025 µs per relaxed edge measured, 8–13× the reference on large searches (`docs/reference/graph.md`, **The CSR backend**). Kernels take the masks as zero-copy `uint8` views, use a binary heap written as arrays, support `max_cost` early exit, and return the path arrays (the DFS's paths packed with offsets); the transition-aware ones read the transition table as two arrays precomputed once per build (each node's rows, and their `in·m + out` keys) and answer a turn by a binary search. It is the default on every build (`make_backend`, `auto`); igraph/rustworkx would be strictly worse for masked search.

### 3.11 Determinism and reproducibility

`numpy.random.Generator(PCG64(seed))` everywhere; no iteration over Python sets in the hot loop (arrays only); the route result records `seed`, `graph_version` (sha256 in `meta.json`), engine version and request. Same tuple → same route, enforced by a golden test (`tests/golden/`, on a test lattice until the Portland build exists). The clock decides only when a run stops: a run that stops on iterations is reproduced byte for byte, and one that stops on time is a prefix of the same sequence of splices, reproduced with `max_iterations` set to its recorded attempts.

### 3.12 Stretch: certified worst (tiny areas)

For a user-drawn box of ≤ ~300 nodes / ~1,000 directed edges:

- **OR-Tools CP-SAT with `add_circuit`:** one literal per arc `(u, v)`, a self-loop literal `(v, v)` per node meaning "skipped", a dummy arc `end → start` forced true; CP-SAT's circuit constraint then enforces a single Hamiltonian cycle over the non-skipped nodes, i.e. a simple `start → end` path. Objective: maximise Σ `length × lit` (+ item terms). Twin bans: `lit_e + lit_f ≤ 1`. Turn constraints need the line-graph formulation (one node per edge), fine at this size. Time limit 60 s; report `optimal` or `best bound / gap`.
- Fallback without the dependency: pruned DFS with the bound "current length + total length of edges still reachable from here without used nodes", good to ~60 nodes.

### 3.13 Hooks for later: turn restrictions and stop reordering

- OSM `restriction=*` relations become entries in the same `transitions` table as super-junction transitions (banned `(in_edge, out_edge)` pairs); the edge-state kernel already checks that table, so enabling restrictions is a pipeline change only. Consolidation must map `via` nodes to their cluster, which is why this waits until stage A/B are stable.
- Reordering stops: brute-force permutations for ≤ 7 stops, run the seed route for each, keep the worst baseline-feasible order. Engine unchanged.

---

## 4. API

FastAPI, JSON bodies validated by pydantic. All coordinates are `[lon, lat]` in GeoJSON and `{lat, lon}` in request objects. Long computations are **jobs**; progress is streamed with **Server-Sent Events** (one-directional, works through plain `fetch`/`EventSource`, no WebSocket upgrade or proxy headaches; cancellation is a separate `DELETE`). As built for M2, `docs/reference/api.md` is the reference for every shape below; the rows marked M3 and M4 arrive with those milestones.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + loaded builds |
| GET | `/regions` | regions, tiers, bbox, `graph_version`, counts |
| GET | `/geocode?q=&region=` | address → candidates (proxied, cached, rate-limited; §4.4; built) |
| POST | `/snap` | `{region, lat, lon, role: start\|via\|end, tier?}` → snapped node |
| GET | `/brands?q=` | brand autocomplete for the pilgrimage mode (local index, not Nominatim) *(M3)* |
| POST | `/jobs` | create a route job → `{job_id, events_url, seed}` |
| GET | `/jobs/{id}/events` | SSE stream |
| GET | `/jobs/{id}` | status + latest snapshot (polling fallback) |
| DELETE | `/jobs/{id}` | cancel; the best-so-far route is still stored |
| GET | `/routes/{route_id}` | full route document |
| GET | `/routes/{route_id}/directions` | turn-by-turn only *(M4)* |
| GET | `/routes/{route_id}/geojson` | geometry only (for "open in …") |
| GET | `/routes/{route_id}/share.png?w=1200&h=630` | server-rendered share image *(M4)* |
| POST | `/routes/{route_id}/validate` | runs the validator, returns the report (debug/CI) |

Every error answers `{code, message, ...}` (the error's own fields beside them), 422 for a request that cannot be planned, 429 when the server is full, 404 for an unknown job or route, 409 for a stored route whose build is gone.

### 4.1 `POST /jobs` request

The engine's `RouteRequest` field for field, under the engine's names (the stops as `{lat, lon}` objects), plus the region, the target, what the presentation keeps, and the route to continue:

```json
{
  "region": "portland",
  "stops": [
    {"lat": 45.5231, "lon": -122.6765, "label": "Powell's"},
    {"lat": 45.4817, "lon": -122.6263, "label": "Reed College"}
  ],
  "objective": "distance",
  "tier": "auto",
  "leash_km": "auto",
  "budget_s": 20,
  "max_iterations": 1000000,
  "seed": 12345,
  "target": {"kind": "max"},
  "vehicle": {"mpg": 24, "gas_price_usd": 3.89},
  "departure": "2026-10-31T09:00:00-07:00",
  "base_route_id": null
}
```

- `objective` ∈ `distance | time | collect | brand | alphabet`; only `distance` is built (the others are `NOT_YET`). M3 adds the collect item, the brand key and the turn constraints.
- `target.kind` ∈ `max | ratio | duration` with `"value": 50` (×) or `"seconds": 32400`, reached within ±2 %; built in M2 step 4 (`docs/reference/api.md`).
- `tier` ∈ `auto | T0..T4`; `leash_km` ∈ `auto | number` (at most 150); `budget_s` at most 120; `max_iterations` defaults to 1,000,000 here, so the seconds budget ends a run (the CLI keeps 5,000).
- `base_route_id` set → "Make it worse": the job starts from that route (its stops, tier and leash, which the request may leave out; the objective, target, budget and seed may differ). `seed` omitted → random, returned in the response.

Response `202`: `{"job_id": "…", "events_url": "/jobs/…/events", "seed": 12345}`.
Errors (`422`): `INVALID_REQUEST`, `UNKNOWN_REGION`, `STOP_OUTSIDE_REGION {stop_index}`, `NO_NODE_NEAR {stop_index}`, `STOP_UNREACHABLE_WITHOUT_REPEAT {leg, stop_index}`, `BUDGET_TOO_LARGE`, `NOT_YET {field}`, `UNKNOWN_ROUTE`, `BUILD_MISMATCH`, `BASE_ROUTE_MISMATCH {field}`; later `INFEASIBLE_TURN_CONSTRAINT`, `UNKNOWN_BRAND`. `429`: `TOO_MANY_JOBS`. The stops are snapped and the seed's legs tried before the job exists (the worker repeats it, deterministically), so these come back at once.

### 4.2 SSE events

```
event: snapped                        # at creation, from the request's pre-flight
data: {"stops":[{"input":{"lat":..,"lon":..},"label":"Powell's","node":1234,"lat":..,"lon":..,"moved_m":12.4,"note":null}, ...],
       "tier":"T4","leash_km":6.2,"active_nodes":118230}

event: baseline
data: {"length_m":7420,"time_s":1130,"geometry":{"type":"LineString","coordinates":[...]}}

event: snapshot                       # right after baseline (iteration 0: the route the loop starts from), every 50th progress event, and first on any (re)connect
data: {"iteration":450,"elapsed_s":3.1,"node_count":812,"stop_idx":[0,811],
       "geometry":{"type":"LineString","coordinates":[...]},"node_index":[0,3,...],"metrics":{...}}

event: progress                       # coalesced to ≤ 10/s; `splices` lists every accepted splice since the last event
data: {"iteration":412,"elapsed_s":2.8,"score":38210,"metrics":{"length_m":38210,"time_s":5020,"ratio":5.15},
       "splices":[{"i":17,"j":18,"node_count":9,"coords":[[lon,lat],...],"node_index":[0,4,...]}, ...]}

event: done
data: {"route_id":"…","stopped_because":"target|budget|converged|cancelled|timeout","metrics":{...}}

event: error
data: {"code":"STOP_UNREACHABLE_WITHOUT_REPEAT","leg":[0,1],"stop_index":1,"message":"..."}
```

The client keeps the route's points and `node_index` (each route node's index among them; consecutive edges share their junction point): a `snapshot` replaces both, and each splice replaces route nodes `i..j` (positions before it) by its points. The snapshot after `baseline` is needed because the seed is not the baseline once there are stops between the ends. Snapshots are simplified edge by edge to about 1 m, keeping every node a vertex. Ids count up per job; `Last-Event-ID` on reconnect returns the stored `snapped`/`baseline` events after it, then a `snapshot`, then live events.

### 4.3 `GET /routes/{route_id}`

As built for M2:

```json
{
  "route_id": "…", "created": "…", "graph_version": "…", "engine_version": "2",
  "region": "portland", "build_id": "…", "seed": 12345, "request": {…},
  "stops": [{"label":"Powell's","node":1234,"lat":..,"lon":..,"moved_m":5.6,"note":null,"route_index":0}, …],
  "geometry": {"type":"LineString","coordinates":[…]},
  "legs": [{"from_stop":0,"to_stop":1,"node_range":[0,812]}],
  "metrics": {
    "length_m": 318402, "baseline_length_m": 7420, "ratio": 42.9,
    "time_s": 41880, "baseline_time_s": 1130, "time_ratio": 37.1,
    "departure": "…", "arrival": "…"
  },
  "run": {"attempts": 82841, "accepted": 800, "stopped_because": "converged", "elapsed_s": 11.4, "backend": "python"},
  "validation": {"ok": true, "checked_at": "…"}
}
```

M4 adds to `metrics` `extra_fuel_gal`, `extra_cost_usd`, `states`, `towns`, `towns_n`, `network_fraction`, `turns`, `items`, `alphabet` and `letters`, and to the document `directions` (`[{"step":1,"text":"Head northeast on W Burnside St","distance_m":412,"time_s":55,"edge_range":[0,3],"kind":"depart"}, …]`), `toasts` (`["42.9× less efficient than the sensible route.", …]`) and `comparisons` (`["Longer than driving the entire length of I-5 once.", …]`).

### 4.4 Geocoding proxy

Nominatim's public policy (verified 2026-10-30): max **1 request/s per application across all users**, **no client-side autocomplete**, identifying `User-Agent` required, results must be cached, repeated identical queries get blocked. Plan:

- Backend proxy only; the browser never talks to Nominatim. Token bucket at 1 rps; a search **button**, not type-ahead; sqlite cache keyed by `(normalised query, bbox)` with a 30-day TTL; `User-Agent: worst-route/<version> (<contact email>)`; `countrycodes=us`, `viewbox` = region bbox, `bounded=1`.
- Map click / marker drag is the primary way to set stops, so geocoding is a convenience, not a dependency.
- Public hosting: either self-hosted **Photon** (Java + OpenSearch; the US-only index is tens of GB, workable on a small VPS) or a paid geocoder with a free tier. Interface `Geocoder.search(q, bbox) -> list[Candidate]` so this is a config switch.

As built (M4, `docs/reference/api.md`, **Geocoding**): `GET /geocode?q=&region=` (the region names the bbox) answers at most 5 candidates `{label, lat, lon, category}` from Nominatim's jsonv2 search, with `countrycodes=us`, the region's bbox as `viewbox` and `bounded=1`. The cache is `geocode.sqlite` beside the route store, keyed by the region, its bbox and the normalised query (casefolded, whitespace collapsed), 30 days, empty answers included. Requests go upstream one at a time per process, at least a second after the previous answer came back (so a slow connection never brings two closer); a search that finds five already queued is `RATE_LIMITED` (429), identical searches queued behind one in flight are answered from the cache, and after Nominatim refuses (429, 403) the proxy asks nothing for 60 s (502 `GEOCODER_ERROR` meanwhile). The User-Agent is `worst-route/<version> (<repo url>)`, with `; <contact>` from `WORST_ROUTE_CONTACT`. `worst-route serve --geocoder none` turns it off; `--nominatim-url` points it elsewhere. The search box is a field and a Search button at the top of the stop list (`docs/reference/frontend.md`, **Using it**); a pick places the next stop exactly as a map click does.

### 4.5 Jobs and workers

- One `multiprocessing` process per job, `spawn` start method (macOS default), at most cores − 2 running and 8 queued (`config.API_*`); each worker memory-maps the build (`graph/loader.py`, an LRU of two builds) and runs `plan_route` with its progress hooks.
- Engine events go worker → a one-way `multiprocessing` pipe (the worker holds its only write end, so its death ends a read at once; it exits itself if the API process dies) → the job's follower task (reading in a thread) → per-subscriber `asyncio.Queue`s for SSE. The follower keeps the job's own copy of the route, splice by splice, so a cancel (or a hard kill at budget + 30 s) stores the best so far, validated first. Coalesce at 100 ms.
- Job records live in memory with a 1 h TTL; finished routes persist in sqlite (`routes` table: id, created, region, build id, `graph_version`, engine version, seed, the engine's and the API's request JSON, the snapped stops, node and edge ids and the baseline's edges as blobs, stop positions, metrics, run). A route id is 12 hex digits of a sha256 over its `graph_version`, nodes, edges and stop positions. Share links are `?route=…`.
- A job whose SSE subscribers have all gone for 15 s is cancelled (dropped if queued); the frontend also cancels on `pagehide`.
- Limits (public, later): 1 running job per client IP, 3 queued, budget ≤ 60 s, leash ≤ 300 km. The CLI bypasses these.

---

## 5. Frontend

### 5.1 Map library: MapLibre GL JS (recommended over Leaflet)

- **Long, live-updating routes.** A 9-hour route is thousands of edges and tens of thousands of vertices, redrawn ~10×/s while it grows. MapLibre renders that on the GPU; `source.setData()` on a GeoJSON source is cheap. Leaflet draws polylines as SVG/Canvas on the main thread and gets choppy past ~20k vertices with frequent updates.
- **`line-gradient` with `lineMetrics: true`** colours the route by progress along it, which makes "watch it get worse" legible, and fits the share image's visual identity.
- **Vector basemap, self-hostable.** OSM's raster tile policy forbids heavy use and demands correct `Referer`/`User-Agent` handling; it is fine for local hobby volume but not for a public joke that might go viral. With MapLibre the basemap is a vector style; locally use any free style, publicly serve **Protomaps PMTiles** (one static file for the US, served from object storage, no tile server) or MapTiler/Stadia. Switching is a style URL in config. A muted basemap style also makes the route pop. As built for M2, the local basemap is OSM's raster tiles, desaturated, at hobby volume, and the switch is `VECTOR_STYLE_URL` in `frontend/src/map/basemap.ts` (`docs/reference/frontend.md`, **Basemap**).
- **Camera tricks** (fit bounds with padding, slow fly-along for the share "flyover") come free.
- Leaflet's advantages (smaller API, raster simplicity, plugin ecosystem) do not matter here, and the raster-tile policy would push us to a provider anyway.

### 5.2 Structure (Vite + TypeScript, no framework)

As built for M2 (`docs/reference/frontend.md`); what is marked M4 arrives with the presentation.

```
frontend/
├── index.html
├── vite.config.ts            # dev proxy /api → WORST_ROUTE_API (default 127.0.0.1:8000), prefix stripped
├── src/
│   ├── main.ts               # bootstrap, URL state (?route=, ?seed=, ?stops=, ...), wiring
│   ├── state.ts              # tiny typed store on EventTarget: region, stops, settings, run, route, error
│   ├── api/types.ts          # the API's shapes, by hand; a test holds them to api.md and a recorded stream
│   ├── api/client.ts         # fetch wrappers + SSE subscription (EventSource reconnects with Last-Event-ID)
│   ├── map/basemap.ts        # the basemap and its switch to a vector style
│   ├── map/map.ts            # MapLibre init, style, attribution, layer order
│   ├── map/routeLayer.ts     # baseline (grey dashed) + worst route (gradient) sources; applySplice(), setSnapshot()
│   ├── map/stopsLayer.ts     # draggable markers, numbered, snapped-position ghost + note tooltip
│   ├── stops/stopList.ts     # ordered list: add by click, remove
│   ├── stops/search.ts       # (M4, built) the place search: a field and a button, not type-ahead; a pick places a stop as a click
│   ├── settings/panel.ts     # seed, budget, inefficiency target, scale (tier auto/override, leash), objective mode, units; constraints (M3), vehicle, departure (M4)
│   ├── run/controls.ts       # Go / Cancel / Reroll (new seed) / Make it worse; the live stat line with the ratio ticking up
│   ├── share/share.ts        # URL state, ?route= links, copy link; open share.png, download GeoJSON (M4)
│   ├── banner.ts             # API errors, non-modal
│   ├── format.ts             # units (mi/km toggle), durations, numbers; money, dates (M4)
│   ├── util/                 # geo (rounding, bounds), dom
│   ├── toasts/toasts.ts      # (M4) rotating stat toasts during and after the run; jokes interleaved
│   └── directions/panel.ts   # (M4) scrollable turn list; hovering a step highlights the edge range on the map
└── public/                   # (later) favicon, fonts
```

Behaviour notes:

- **Live growth:** on each `progress` event apply splices in order; flash the new segment for 400 ms; update the ratio counter. On `snapshot` replace. On `done` fetch `/routes/{id}` and draw its full geometry (and, with M4, its directions, toasts and comparisons).
- **Stop picker:** first click = start, second = end, further clicks = vias appended before the end; drag to move; the list shows snap notes.
- **Settings** and the stops are serialised to the URL so a link reproduces the request (plus `seed`), and `?route=` loads a stored route directly.
- **Reroll** = same request, new seed. **Make it worse** = `base_route_id` = current route, same seed+1 so it is also reproducible.
- **Share image** is requested from the backend and shown in a modal with copy/download; no client-side rendering.
- Units default to miles for US users with a km toggle; all numbers from the API are metric.

---

## 6. Performance estimates and bottlenecks

Rough graph sizes after simplification and consolidation (±50%):

| Graph | Junction nodes | Directed edges | Source |
|---|---|---|---|
| Portland metro, all drivable | ~120k | ~330k | Oregon extract, bbox clip |
| Oregon, all drivable | ~700k | ~1.8M | 243 MB extract |
| US, T1 (primary and up) | ~1.5M | ~3.5M | 1.9M ways |
| US, T3 (tertiary and up) | ~3.5M | ~8M | 4.2M ways |
| US, all drivable | ~20M | ~55M | ~16M ways |

Memory per directed edge: CSR + attributes ~40 B, geometry ~50 B (avg 6 coords, float32) → ~100 B/edge. networkx: ~1.5 KB/edge.

| | CSR (mmap) | networkx |
|---|---|---|
| Portland | ~35 MB | ~0.5 GB |
| Oregon | ~0.2 GB | ~2.7 GB |
| US T3 | ~0.8 GB | ~12 GB (not viable with workers) |
| US all | ~5.5 GB | impossible on this machine |

Pipeline wall-clock on the M5 (single process unless noted):

| Step | Portland | Oregon | US T3 | US all |
|---|---|---|---|---|
| download | — | 1 min | ~20 min (parallel) | same |
| filter (pyosmium, C++ filters) | 1 min | 1–2 min | ~30 min total across states | ~40 min |
| build + simplify (networkx) | 10 s (measured) | 20–40 min | not viable | not viable |
| consolidate A (OSMnx) | 30–80 s (measured, by machine load) | 20–60 min | not viable | not viable |
| consolidate B + twins + SCC + enrich (ours) | ~10 s (measured, without enrich) | 5–10 min | 30–60 min | 2–4 h |
| array-based simplify/consolidate (M5 rewrite) | — | ~5 min | ~30 min | 1–2 h |

Engine throughput:

- One masked Dijkstra across a 40k-node grid (every node settled), measured: **1.7 ms** of CPU with the Numba kernels (~0.01 µs per relaxed edge), 21–50 ms with the pure-Python reference by the machine's load; ~1–2 s estimated with networkx. A small search costs 10–14 µs on Portland, once its weights array has been checked (`docs/reference/graph.md`, **The CSR backend**).
- Whole runs, measured on a 10k-node grid corner to corner: **1100–1160 attempts/s** with CSR against 1000–1070 with the reference, the same routes. The searches are now ~3% of a run; the loop's own NumPy work (`Route.release`, `bubble_mask`) is the rest (`docs/reference/engine.md`, **The loop**). On Portland, Powell's → Reed converges at **~1,700 attempts/s** with CSR against ~1,560 with the reference, the same route, the searches ~7% of the run once each weights array is checked only once. Then the route kept its ban counts and the detours' balls scanned the request's nodes (`docs/reference/engine.md`, **Routes**, **Detours**): on the same grid 3,240–3,700 attempts/s on the reference and 5,120–5,400 on CSR, the same routes. With the transition tables searched (engine version 2) and CSR's edge-state kernels, Powell's → Reed converges after 82,841 attempts at **~8,200 attempts/s** on CSR against ~7,100 on the reference, the same route; the searches are a quarter of the CSR run and a third of the reference's, mostly small ones (`docs/reference/engine.md`, **On Portland with `CsrBackend`**).
- 20 s budget → CSR: 100–200 accepted splices, ratios of 20–100× for a cross-town trip. networkx: ~5–10 splices, enough to prove the approach, not enough for a satisfying demo, which is why the CSR backend is the first task of M2.
- At state/national leashes the search ball grows (100 km on T2 ≈ 300k nodes ≈ 0.3 s/search). The ambition radius bounds searches, not the leash, so attempts stay ~50–100 ms; the number of splices needed for a "9 hour" route is only a few hundred.
- Leash mask: ~0.5 s for 5M nodes (shapely 2 vectorised). Snapping: µs after the KD-tree exists; tree build ~2 s for 5M nodes, cached.

Expected bottlenecks, in order of likelihood:

1. **OSMnx/networkx memory and time at state and national scale** — the reason the array pipeline rewrite is scheduled, not optional, for national.
2. **Consolidation correctness.** Too little: interchanges leak repeats. Too much: downtown blocks fuse and routes become impossible. Per-node tolerances, `max_length`, and the fixture tests exist for this.
3. **Twin detection recall on arterials** (odd geometry, missing refs). Measured by "unpaired one-way motorway/trunk length" in QA; motorways should be ~100% paired.
4. **Dijkstra attempts/s.** Numba kernels; later an A*-style heuristic (straight-line bound) and reusing the `a → *` search tree across several waypoint draws.
5. **Progress bandwidth** on public hosting: coalescing + deltas keep it to a few KB/s per job.
6. **Share image** (~0.3–0.5 s each, matplotlib Agg): cache by route id.
7. **Worker memory on macOS** (`spawn` + mmap is fine; accidental non-mmap loads would multiply memory by worker count).

---

## 7. Milestones

Each milestone ends with something runnable and a checklist. Estimates assume part-time work and are relative, not promises.

### M1 — Portland, distance mode, CLI (the proof)

Deliverable: `worst-route plan --region portland --from 45.5231,-122.6765 --to 45.4817,-122.6263 --seed 1 --budget 30` prints baseline length, worst length, ratio, node/edge counts, and writes `route.geojson` (open in geojson.io or QGIS). As built it needs no `--backend`: M1 planned it on the Python reference, and since M2 the default `auto` is the CSR backend, which plans the same route.

1. Repo bootstrap: `uv init`, src layout, ruff/mypy/pytest, `justfile`, `.gitignore` for `data/`, CI running tests on fixtures only (no downloads).
2. Pipeline `download → filter → build` for `portland` (bbox clip from Oregon).
3. Cleanup: simplify, consolidate stage A, stage B with its transition tables (enforced by `Route.splice`, and searched by the engine's transition-aware search on the reference backend from M1), twin detection, SCC + report.
4. Enrich: length, speed defaults (needed for time stats later anyway), identities, `pass_through`; also road units, flags, bearings, toll booths, level crossings and node delays.
5. Save/load in the §2.7 format; `qa` report with GeoJSON layers. Built: Portland's pipeline runs from the Oregon extract in under a minute and a half and passes QA (`docs/reference/pipeline.md`).
6. `GraphBackend` protocol + `PythonBackend` (pure Python over the arrays; networkx as the tests' oracle).
7. Engine: snapping with fallback, seed route with disjoint legs, `Route.splice`, `worsen` loop with the three detour strategies, distance objective, budget, progress events to stdout.
8. **Validator** (§8.1) and the fixture suite (§8.2). The CLI runs the validator on every result and refuses to print a route that fails.
9. Acceptance: on 20 random Portland stop pairs, 30 s budget: validator passes 20/20; median ratio ≥ 10× with the reference backend; two seeds give Jaccard < 0.3; a cul-de-sac intermediate stop produces a snap note instead of a failure. First run (2026-10-30, `uv run python tools/acceptance.py --region portland --pair-seed 2026 --pairs 20 --seed 1 --budget 30`, `docs/project_overview.md` §2.2): the validator passes every route returned, but only 14 of 20 pairs plan (the seed does not see transition tables or twins within a leg) and the median ratio is 6.4×; Jaccard (0.12–0.23) and the cul-de-sac note hold. Met (2026-10-30, engine version 2: transition-aware searches, twin re-search, the loop in cycles and tuned, `docs/reference/engine.md`, **On Portland**): all 20 plan and the validator passes all 20, the median ratio is 21.5× (3.3–45.2×), Jaccard 0.10–0.16, the cul-de-sac note holds.

### M2 — API + map, live growth

1. `CSRBackend` + Numba kernels (node-state Dijkstra, bounded DFS, and the transition-aware edge-state Dijkstra and turn-checking DFS); parity tests against the reference backend on fixtures; benchmark ≥ 10 attempts/s at Portland scale. Built: byte-identical to the reference and the default on every build; on Portland it plans the reference's routes at 7,200–8,200 attempts/s (`docs/reference/engine.md`, **On Portland with `CsrBackend`**).
2. FastAPI app: `/regions`, `/snap`, `/jobs` + SSE, `/routes/{id}`, `/routes/{id}/geojson`, sqlite route store, worker pool. Built (`docs/reference/api.md`): a process per job, "make it worse" from a stored route, cancel storing the route so far, `worst-route serve`.
3. Frontend: map, stop picker, run controls, route layer with splice application, ratio counter; settings panel with inefficiency target (max / ratio / duration), tier override, leash; Reroll; Make it worse. Built (`docs/reference/frontend.md`): the route drawn growing from the stream and reconnecting with `Last-Event-ID`, the ratio and duration targets with what a run achieved, the objectives other than distance shown disabled until they land, settings and stops in the URL, `?route=` links, cancel on `pagehide`, OSM raster tiles locally with the switch to a vector style.
4. Target modes (ratio and duration) in the engine, including run replacement for trimming. Built (`docs/reference/engine.md`, **Targets**): `plan --target ratio:<x>|duration:<time>`, `plan --base`, and the API's `target`; on Portland, Powell's → Reed reaches 20× and 3 h within ±2 % in under half a second, 9 h in 1.1 s, and a 62.43× route shrinks to 30.58× in 5 s.
5. Acceptance: a 15-minute Portland drive becomes a 9-hour route within ±2% of target in < 20 s; the map shows it growing; closing the tab cancels the job.

### M3 — Objective modes and constraints

1. Edge-state Dijkstra kernel (turn-aware; the reference backend's Python search and the engine's use of it moved to M1, the Numba kernel to M2 step 1); `feasible_pair`; never-left / never-right constraints; infeasibility reporting.
2. Slowest-time objective with node delays.
3. Collectibles pipeline (toll booths, crossings, bridges, roundabout super-nodes) + collect objectives with waypoint pools.
4. Brand index + pilgrimage objective + `/brands`.
5. Hard mode (identity bans + window stretch check). (The search honouring the stage B transition tables moved to M1.)
6. Alphabet mode via constructive search.
7. Acceptance: each mode has a fixture test proving its invariant (no left turns in the result, each identity in ≤ 1 stretch, letters strictly increasing, every counted item lies on a route edge).

### M4 — Presentation

1. Directions generator (merge same-identity runs, turn words, ramps via super-junction internal paths, roundabout exits), deadpan jokes injected deterministically from the seed.
2. Stats: ratio, arrival time, extra fuel/cost, states (Census boundaries), towns (Gazetteer), network fraction, turn counts; rotating toasts in the UI during and after the run.
3. Curated comparison list (NYC–LA 2,790 mi; Route 66 2,448 mi; I-90 end to end 3,020 mi; Earth's circumference 24,901 mi; Earth–Moon 238,900 mi; …) with the funniest-fitting line chosen by ratio.
4. Share image: matplotlib Agg, our own data only (route over faint active-network lines and Census outlines, big ratio, one comparison line), bundled OFL font, cached per route.
5. Geocoding proxy with cache and rate limit; search box in the UI.
6. Units toggle, URL state, share link.

### M5 — Scaling up

1. **Oregon** build with the OSMnx path; measure; fix tier fragmentation; local bubbles; tier auto-select tuned on real trips (Portland–Bend, Astoria–Ontario).
2. **Array-based simplify and consolidate** (replace OSMnx for big builds): degree-2 chain contraction in NumPy, stage A as cKDTree clustering with the `max_length` connectivity check, same outputs, parity test against OSMnx on Portland.
3. **US at T1** (primary and up, ~3.5M edges): validates the national pipeline cheaply. Local bubbles need an all-roads graph with the same node ids, which a T1-only national build does not have. Decision gate: either (a) build **US all-roads** as the single master graph (~5.5 GB mmap, 2–4 h pipeline, bubbles and tiers as masks — the clean end state), or (b) keep T1 national + per-state all-roads builds and accept that cross-state trips start from the nearest primary road. Recommendation: (a) if M5.2's throughput makes it a few hours; otherwise (b).
4. Public-hosting prep: Dockerfile, Protomaps PMTiles basemap, Photon or paid geocoder, per-IP limits, attribution text, `User-Agent` strings, structured logs. Deploy target can be a single 8–16 GB VPS for T3 national or a 32 GB box for all-roads.

### M6 — Stretch

Certified-worst mode (CP-SAT); OSM turn restrictions via the transitions table; stop reordering; portfolio runs (K seeds in parallel, stream the best); simulated-annealing acceptance.

---

## 8. Risks and testing strategy

### 8.1 The validator (`engine/validator.py`)

Input: a route (`nodes`, `edges`, `stop_idx`), the build, the request (constraints, leash, tier). Output: a report with every check and the first offending index. It is pure and fast (O(route length)) so it runs on every CLI result, every API result (stored in the route document), and in every engine test.

Checks:

1. **Edge chain:** `tail(edges[k]) == nodes[k]` and `head(edges[k]) == nodes[k+1]` for all k; every edge exists in the active mask; one-way direction is therefore enforced by construction.
2. **Vertex-simple:** `len(set(nodes)) == len(nodes)` (round trips: allow `nodes[0] == nodes[-1]` only).
3. **Edge-simple and no twin reuse:** for every pair of route edges `(e, f)`, `f ∉ twins(e)` and `e ≠ f`, and no road unit twice. Implemented as: mark `e ∪ twins(e)` while walking; any edge already marked fails.
4. **Consolidated junctions (the proof at the raw-data level):** expand the route to original OSM node ids via each edge's `osmnode_ids` plus the super-junction internal paths; assert no original OSM node id appears twice, and assert every `members` group appears in at most one contiguous run. This proves that neither a merged carriageway crossing nor a consolidated interchange/roundabout is entered twice, independently of the engine's own bookkeeping.
5. **Transitions:** every `(edges[k], edges[k+1])` pair at a super-node is in its allowed set.
6. **Stops:** `nodes[stop_idx[s]]` equals the snapped stop node, in order; no stop is interior to a removed run (checked by construction in `splice`, re-checked here). For a saved route the stop nodes are re-derived from its request, never read from the file.
7. **Constraints:** turn classes against `no_left`/`no_right`; hard-mode stretches (`identity → number of maximal runs ≤ 1`); leash (every node within `L` of the stop polyline); tier/bubble membership of every node, and no edge the tier bans outside the bubbles.
8. **Metrics:** the reported length/time/items are finite and equal the recomputed ones within 1e-6.

`worst-route validate route.json` runs it from the CLI; `POST /routes/{id}/validate` from the API.

### 8.2 Test layers

- **Fixtures** (`tests/fixtures/osm/*.osm`, hand-written OSM XML that pyosmium reads directly): 10×10 grid with some one-ways; divided highway with mismatched side junctions; cloverleaf; diamond interchange; roundabout with 4 arms; cul-de-sac neighbourhood with one entrance; private road + driveway + parking aisle; overpass with no ramps; a road whose `ref` changes mid-way. Each fixture has expected counts after each pipeline step.
- **Pipeline unit tests:** one-way rules, exclusions, implied oneway on motorways, maxspeed parsing, ref/name normalisation, twin detection on the divided-highway fixture (exact expected pairs), stage B cluster membership on the cloverleaf fixture, SCC report.
- **Engine unit tests:** splice bookkeeping, snapping fallbacks, seed route disjointness and the penalty re-solve, each detour strategy returns only legal paths, objective `delta` equals full re-score (checked on every accepted splice in test mode).
- **Property tests** (hypothesis): random strongly-connected digraphs with random twins → `worsen()` output always passes the validator; score never decreases in max modes; target modes land within tolerance or report why.
- **Backend parity:** the pure-Python reference backend and the CSR kernels give identical paths on fixtures (the same tie rule, `docs/reference/graph.md`); networkx checks the reference backend's costs and path sets.
- **Determinism/golden:** fixed seed on a fixture build (a 20 × 20 lattice now, the Portland build when it exists) → identical node list; recorded in `tests/golden/` with the `graph_version` (regenerated deliberately when the build changes).
- **Variety:** two seeds → Jaccard(edge sets) < 0.3 on the Portland acceptance pairs.
- **API tests:** httpx `AsyncClient` against the app with a fixture build; SSE event order; cancel; reconnect with `Last-Event-ID`.
- **Benchmarks** (`tests/bench`, not in CI): attempts/s per backend, pipeline step timings, written to `docs/bench.md`.
- **QA gates** per build (§2.8) run in `pipeline qa` and fail the build on threshold breaches.

### 8.3 Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Heuristic routes look samey ("the snake") | Joke falls flat | Leash, ambition radius, seeded waypoints, variety test, run replacement, later annealing |
| Consolidation wrong in either direction | False violations or impossible routes | Per-node tolerance, `max_length`, fixtures, QA map; validator check 4 catches leaks |
| Twin detection misses pairs | Same highway both ways | Motorway pairing coverage metric in QA; conservative bans; hard mode as a user-facing fallback |
| OSM data quality (missing `oneway` on ramps, `access` on bridges) | Odd routes, dropped components | SCC report with map; implied-oneway rules; per-region override file for known bad ways |
| The pure-Python reference backend too slow for a good demo | M1 underwhelms | Expected; CSR backend is M2 step 1 |
| OSMnx cannot scale to national | Blocks M5 | Array rewrite scheduled with parity tests |
| macOS multiprocessing (`spawn`) + large graphs | Memory blow-up | mmap-only loads, loader test asserting RSS per worker |
| Nominatim policy violations | Blocked geocoding | Proxy, 1 rps bucket, cache, UA string, button not type-ahead, Photon when public |
| OSM raster tile policy when public | Blocked tiles, bad citizenship | MapLibre vector basemap (PMTiles) before any public launch |
| Turn-constrained modes infeasible near stops | Confusing errors | Explicit error codes with the offending stop; suggest relaxing or moving the stop |
| Target-duration overshoot | "exactly 9 hours" is 9h20 | Radius shrink near target + run replacement trimming; ±2% tolerance shown honestly |
| Alphabet mode rarely finishes the alphabet | Disappointment | Report letters reached; free connectors and free final leg; wrap-around |
| Python 3.12 pin vs future wheels | Upgrade friction | Lockfile; revisit at M5 |
| Census file formats change year to year | Pipeline breaks | Pin the 2024 vintage URLs in `scripts/fetch_census.sh`; parse by column name |

---

## 9. Remaining open questions

Assumptions made in this plan; each can be flipped without restructuring.

1. **Compute budget defaults:** 20 s default, 120 s max locally, 60 s max public. OK?
2. **"Only turn left" semantics:** interpreted as "never turn right", straights allowed. Confirm.
3. **Alphabet mode:** unnamed roads and ramps are free connectors; wrap Z→A; final leg to the destination is free. Confirm.
4. **Brand set:** index every object with `brand` / `brand:wikidata` (thousands of brands) rather than a curated list. Confirm, or name a shortlist.
5. **Share image style:** route over our own faint network + Census outlines, no third-party basemap (avoids tile policy and looks deliberate). Or do you want a basemap behind it?
6. **Towns definition:** Gazetteer places of all LSAD types including CDPs, "passed through" = within an area-equivalent radius of the place centre. Polygons (`cb_2024_us_place_500k`) would be more accurate at the cost of a bigger aux file. Which?
7. **National end state:** M5.3 recommends one US all-roads master graph if the array pipeline makes it a few hours. Is a ~6 GB data directory and a 32 GB box for public hosting acceptable, or should national stop at T3?
8. **Alaska/Hawaii:** include in national builds?
9. **Round trips** (start == end): supported as described; worth exposing in the UI as "take me nowhere, slowly"?
10. **Vehicle stats:** MPG and gas price only, or also EV (kWh/mi, $/kWh)?
11. **Public geocoder:** self-hosted Photon vs a paid provider when the time comes.
12. **Licensing/attribution text** for OSM (ODbL) and Census data in the UI and share image: a footer line is planned; any other requirements?

---

## Appendix A — Sources checked while writing this plan

- Geofabrik North America/US extracts: Oregon 243 MB, Texas 692 MB, Pennsylvania 333 MB, Minnesota 276 MB, California 1.2 GB, `us-latest.osm.pbf` 11.4 GB — https://download.geofabrik.de/north-america/us.html
- OSMnx 2.1.1 `simplify_graph` (`edge_attrs_differ`, `track_merged`, `edge_attr_aggs`) and `consolidate_intersections` (per-node `tolerance` dict, `max_length`, `dead_ends`, `reconnect_edges`, `node_attr_aggs`) — https://osmnx.readthedocs.io/en/stable/user-reference.html
- pyosmium 4: `FileProcessor.with_filter`, `KeyFilter`/`TagFilter`, `IdTracker`, `BackReferenceWriter(ref_src=…)`; the author reports ~13 min for a tag filter over the full planet — https://docs.osmcode.org/pyosmium/latest/ and https://docs.osmcode.org/pyosmium/latest/cookbooks/Filtering-Data-By-Tags/
- Nominatim usage policy (1 rps, no autocomplete, UA, caching) — https://operations.osmfoundation.org/policies/nominatim/
- OSM tile usage policy (no bulk/prefetch, UA/Referer, alternatives incl. vector tiles) — https://operations.osmfoundation.org/policies/tiles/
- OR-Tools CP-SAT circuit constraint with self-loop literals for optional nodes: the official Python reference page returned 404 when fetched; semantics taken from the C++ doc comments quoted at https://activimetrics.com/blog/ortools/cp_sat/two_circuit_constraints/ — re-verify against the installed `ortools` before M6
