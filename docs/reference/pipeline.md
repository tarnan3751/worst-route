# Pipeline

How the offline pipeline works: the steps built so far, the data layout, build ids and
sidecars, and the rules each step applies. The design is `docs/PLAN.md` §2; the code is
`src/worst_route/pipeline/`, one module per step, and each module's header comment states its
contract.

Built: every step, `download`, `filter`, `build`, `cleanup`, `enrich`, `tiers`, `save` and `qa`.
`save` writes the build in the format `docs/reference/graph.md` describes, which `worst-route
plan` reads. Portland and the whole of Oregon (**Oregon**) build and pass QA. Every step from
`build` to `save` runs on arrays, the default engine (**The arrays engine**), to the networkx
engine's build (`--engine networkx`). A region may name several states, whose extracts
`filter` merges (**Regions of several states**): Oregon and Washington, and the 48 contiguous
states and DC at T1, build and pass QA too.

## Commands

```
uv run worst-route pipeline download --region portland [--force]
uv run worst-route pipeline filter   --region portland [--source PATH] [--force]
uv run worst-route pipeline build    --region portland [--source PATH] [--build-id ID] [--force] [--engine E]
uv run worst-route pipeline cleanup  --region portland [--build-id ID] [--force] [--allow-fragmented] [--engine E]
uv run worst-route pipeline enrich   --region portland [--build-id ID] [--force]
uv run worst-route pipeline tiers    --region portland [--build-id ID] [--force] [--allow-fragmented]
uv run worst-route pipeline save     --region portland [--build-id ID] [--force]
uv run worst-route pipeline qa       --region portland [--build-id ID] [--force]
uv run worst-route pipeline prune    --region portland [--build-id ID] [--force]
```

`--region` is a key of `config.REGIONS` (one or more Geofabrik state slugs, an optional bbox, a
projected CRS). `--source` makes `filter` or `build` read that file instead of its usual input
(a fixture, in tests; for `filter`, instead of every extract of the region). `--engine` (`arrays`, the default `config.DEFAULT_PIPELINE_ENGINE`, or `networkx`)
chooses the engine of `build` and `cleanup`; both make the same build, byte for byte (**The
arrays engine**), and `cleanup` reads the build stage of its own engine. Every step accepts `--build-id` and `--force`; `download` and `filter` ignore the
build id; the steps from `cleanup` on read the build `--build-id` names, else the region's
`latest`. Each step prints what it wrote and its counts, or that its output is up to date and
was skipped. A missing input (a stage file, a sidecar) exits with code 1 and names the step to
run first. One line on stderr and exit 1 also for: a stage before the step that is stale (made
from inputs that have changed since; below), naming the step to rerun; a failed download (an
md5 mismatch, an HTTP or connection error); a cleanup whose largest strongly connected
component keeps too few nodes, or a tiers step with a tier that keeps too few of its own,
unless `--allow-fragmented`; a save that would overwrite a written build, whose filtered
extract has changed since the build was built, or that cannot express the build; a qa whose
thresholds fail (after it printed them and wrote its report).

## Data layout

Everything is under `settings().data_dir` (`WORST_ROUTE_DATA`, default `./data`); `layout.py`
is the one place that names these paths.

| Path | Written by |
|---|---|
| `raw/<state>-latest.osm.pbf`, `raw/<state>-latest.osm.pbf.md5` | `download` |
| `filtered/<region>.roads.osm.pbf`, `filtered/<region>.pois.parquet` | `filter` |
| `graphs/<region>/<build_id>/stages/build/`, `stages/build.json` | `build` (`stages/build.pkl` instead of `build/` with `--engine networkx`) |
| `graphs/<region>/latest` (the region's current build id, one line) | `build` |
| `graphs/<region>/<build_id>/stages/cleanup/`, `stages/cleanup.json` | `cleanup` (`stages/cleanup.pkl` with `--engine networkx`) |
| `graphs/<region>/<build_id>/stages/enrich/`, `stages/enrich.json` | `enrich` (`stages/enrich.pkl` after a networkx cleanup) |
| `graphs/<region>/<build_id>/stages/tiers.json` | `tiers` |
| `graphs/<region>/<build_id>/meta.json`, `strings.json`, `arrays/`, `geometry/`; `stages/save.json` | `save` (the build, through `graph.store.write_build`) |
| `graphs/<region>/<build_id>/qa/qa.json`, `qa/*.geojson` | `qa` |
| `<output>.inputs.json` beside a step's output (its sidecar) | every step but `download` |

`stages/` holds intermediate state between the steps of one build. `stages/*.pkl` is the only
place the pipeline pickles; the final on-disk format (`docs/PLAN.md` §2.7) never does. The
arrays engine's stages are directories of `.npy` files instead (**The arrays engine**), whose
`manifest.json` stands for them in a sidecar. The build
itself, what `worst-route plan` reads, sits beside `stages/` in the same directory, and the QA
report beside it in `qa/`.

## Build ids, sidecars, skipping

- **Build id:** `--build-id` if given, else the first 12 hex digits of a sha256 over the JSON
  list `[md5 of the filtered roads file, region name, region bbox, region CRS (EPSG code),
  PIPELINE_VERSION]` (`layout.build_id_for`). `PIPELINE_VERSION` is in `config.py` (the
  `pipeline` package re-exports it, and `graph.store` reads it to name the remedy for another
  version's build); bump it when a step's output changes for the same input. Every step's
  sidecar records it, so a region built at an earlier version is rebuilt from `filter` (which
  reads the raw extract again, no download) under a new build id, and `latest` moves to it.
  What each version changed:

  | Version | Change | Portland's build (`graph_version` on the dev machine) |
  |---|---|---|
  | 1 | M1's pipeline | |
  | 2 | M3: `enrich`'s bearings leave out cleanup's stubs; `save` writes `node_roundabouts` | 506f747d7458 (ae1ce1e8289b) |
  | 3 | M3: `enrich` indexes the brands, `save` writes them as arrays | ffe2e7c881ca (8867636799a6) |
  | 4 | M5: stage A places each cluster from its own buffers, not from OSMnx's union of every buffer (**cleanup**, step 2) | 4b8998deee9f (57e5deb43289) |
  | 5 | M5: stage B joins two clusters a carriageway leaves and comes back to, and its pockets follow a road by its name as well as its refs (**cleanup**, step 4). With them came the drop of a super-junction left with edges in and out and no allowed turn after connectivity (step 5), which needed no version of its own: no build that saved had one (`save` refuses them), so it changes no array of Portland's or Oregon's (Portland's `cleanup.pkl` is byte-identical with and without it) | c60bc0c9cb4a (0d4338234cae), under the connectivity rule of M5.1 |
  | 6 | M5: `save` writes the compact format (format 4: the speed table; no lon/lat, way ids, OSM node runs, states or shapes among the arrays; the shapes simplified into `geometry/`; `docs/reference/graph.md`) | 3a4a3df6269f (04d2e06854dd) |
  | 7 | M5: enrich makes one road unit of every OSM node run, however many ways map it, and simplification keeps the node two one-way ways overlapping in part share (**enrich**; **cleanup**, step 1). The rule changes no cleanup stage: Portland's and Oregon's `cleanup.pkl` are byte-identical with and without it, from either engine. The units change Oregon's build in four rows of `edge_unit`, Ponderosa Lane's, mapped twice as two two-way ways over the same nodes and now one unit a segment (304,337 units to 304,335; Oregon's `graph_version` ec7360404031 to dd86410e515c, build e78224b5cf9a to ea3b18096e8b); Portland's only in its build id | a453e105582b (04d2e06854dd) |

  Rows 1–4 were measured on Geofabrik's Oregon extract of 2026-10-28, rows from 5 on on that of
  2026-10-29 (md5 3c9bc0db9a4830d004044865fbecd965, data of 2026-10-29T21:21:26Z), which
  replaced it.

  Version 4 moves 3,977 of Portland's 4,236 clusters by at most 1.5e-8 m: against version 3,
  the build's `node_x`, `node_y`, `node_lat` and `node_lon` differ in 3,216, 3,206, 3,020 and
  510 rows, `edge_length_m` in one and `edge_time_s` in five (each by one float32 step), and
  every other array is identical. Version 6 changes no value the engine reads: from the same
  extract, every route planned on it is the one the version before plans.
- **Sidecar:** after `filter`, `build` or `cleanup` writes its output it records
  `{"step", "inputs": [{"file", "md5"}], "params"}` in `<output>.inputs.json` (filter: the
  bbox and the pipeline version; build: the region, bbox, CRS and pipeline version; both also
  the class ceiling, `classes`, for a region that has one; cleanup:
  `allow_fragmented`, the config values it depends on, `cleanup.parameters()`, and the pipeline
  version, its input being `build/manifest.json` or, from the networkx engine, `build.pkl`; build
  and cleanup also record their `engine`, beside `params` rather than in them, so that changing
  the engine reruns the step while the build's params, and its `graph_version`, do not depend on
  it (a sidecar written before the engine was recorded is the networkx engine's, and an arrays
  stage counts as written once its manifest is); enrich: its config and the pipeline version, its inputs
  the cleanup stage (`cleanup/manifest.json`, or `cleanup.pkl` from the networkx engine) and the
  POI table; tiers: like cleanup, its inputs the stages the enriched graph is read from
  (`layout.enriched_stages`: `enrich.pkl` from the networkx engine; from the arrays engine
  `enrich/manifest.json` and `cleanup/manifest.json`, the cleanup stage the enrich stage lies
  on); save: the pipeline version and the `params` it copies into the build's meta, so
  a changed parameter that leaves every stage byte-identical is still another input, its inputs
  the same stages and `tiers.json`, its output `stages/save.json`;
  qa: its thresholds, its inputs `meta.json` and the stage reports, its output `qa/qa.json`).
  Inputs are recorded by file name and md5, so moving the data directory does not invalidate
  them. The
  filter's sidecar records one input per extract, in the region's order, each with its
  `source_timestamp` too, the extract's data date from its header
  (`osmosis_replication_timestamp`, read by `filter.source_timestamp`; null when the header has
  none, as in the fixtures), for the `save` step to carry into the build's metadata. Before
  M5.3 the sidecar held one `source_timestamp` beside the inputs; `filter` reruns once on such a
  record (writing the same roads file, so the build id stays and `build` skips), and `save`
  refuses one until it has, naming the rerun.
- **Skipping:** a step is skipped, with a message, while its outputs exist and its sidecar
  equals what it would record now; `--force` runs it anyway. The sidecar is deleted before a
  step runs, so an interrupted run is never mistaken for a finished one (`cleanup` also deletes
  its old outputs, so a failed run leaves none, and so do `enrich` and `tiers`). `build` writes
  `latest` whether it ran or was skipped. `save` is the exception, because a written build is
  never overwritten: with its sidecar matching it skips; with a build already written from other
  inputs it refuses without `--force`; `--force` deletes the build (`meta.json` first) and writes
  it again. `qa` prints its stored thresholds when it skips, and fails again when they fail.
- **Stale stages:** a step reads earlier stages only when they are fresh. `layout.stale_stages`
  walks the chain `STAGE_CHAIN` (cleanup from `build/manifest.json` or `build.pkl`, enrich from
  the cleanup stage of the same engine, tiers from the stages the enriched graph is read from,
  the cleanup stage among them on the arrays engine, save from the same and `tiers.json`) and
  names each
  stage whose sidecar records an input md5 that the file there no longer has (or a file now
  gone), or records none of the chain's inputs; a stage not yet made is not stale. `save` checks
  the filtered extract against the sidecar of the build stage cleanup read
  (`layout.build_stage`). `enrich`, `tiers`, `save` and `qa` refuse with one line, "stage X is stale:
  rerun `worst-route pipeline X --region R`", when a stage before them is (`qa` checks the whole
  chain, `save` included): rerunning cleanup and enrich and then `save`, without `tiers`, is refused
  rather than saving tiers computed on the old graph.
- **Download** is skipped while the extract on disk matches its local `.md5` file (no sidecar;
  the check reads only local files).

## `download`

`pipeline download` fetches each of the region's states in order, one line each, and stops at the
first that fails (a rerun skips the extracts already current).
`download.download_state(state, force=False, fetch=fetch) -> Download(path, skipped)` fetches
`https://download.geofabrik.de/north-america/us/<state>-latest.osm.pbf` and its `.md5`
(`<hex>  <filename>`), each to a `.part` file. Only when the extract's md5 matches the
published one are both renamed into place; a mismatch raises `ValueError` and leaves the
previous pair as it was. `skipped` is True when the extract on disk was already current and
nothing was fetched, so the CLI computes the md5 once. `fetch(url, dest)` is the only function
that touches the network (httpx streaming, `settings().user_agent`); tests pass a fake, and an
autouse fixture in `tests/pipeline/conftest.py` fails any test that would reach the network.

## `filter`

`filter.filter_roads(sources, roads_out, pois_out, bbox, classes=None) -> FilterStats` reads
each source with pyosmium 4 in two passes.

**Roads.** With a bbox, `FileProcessor(src, NODE | WAY)` with node locations, `EntityFilter(WAY)`
and `KeyFilter("highway")` (pyosmium refuses `with_locations()` unless nodes are read, so nodes
are read and filtered out); without one (`oregon`), the ways alone, with no locations. A way is
kept when `exclusions.kept_class` accepts
its tags, its class is among `classes` when the region has a ceiling (`config.Region.classes`,
`T1` keeping motorway, trunk, primary and their links, `config.TIERS`), and, with a bbox, at
least one of its node locations lies inside it (boundary inclusive): a way that touches the box is kept whole, and a way that crosses the box with no
node inside is dropped. Kept ways go through `BackReferenceWriter`, which adds the nodes they
reference with their tags stripped.

**Exclusion rules** (`exclusions.py`, constants in `config.py`). A way is kept as class
`highway` when that value is in `KEPT_HIGHWAY` (`motorway`, `trunk`, `primary`, `secondary`,
`tertiary` and their `_link`s, `unclassified`, `residential`, `living_street`), or as class
`alley` when it is `highway=service` with `service=alley` (`ALLEY_SERVICE`); every other
`highway` value is dropped. A kept-class way is still dropped by `area=yes`, or when its access
for cars is in `BLOCKING_ACCESS` (`private`, `no`). Access follows OSM's hierarchy, the most
specific key present deciding (`exclusions.ACCESS_KEYS`): `motorcar`, else `motor_vehicle`, else
`access`. So `motor_vehicle=no` + `motorcar=yes` and `access=private` + `motor_vehicle=yes` are
kept, and `access=yes` + `motor_vehicle=no` and a lone `motor_vehicle=private` are dropped.

**POIs.** A second pass over nodes and ways, with locations, keeps objects tagged
`barrier=toll_booth`, `railway=level_crossing`, `highway=stop` or `highway=traffic_signals`
(`POI_TAGS`), or with `brand` or `brand:wikidata`, whose position lies inside the bbox, and
nodes on a kept way wherever they lie (a level crossing on a road that straddles the box; the
roads pass collects the kept ways' node ids when it clips). One row
per object and kind, columns (`POI_SCHEMA`): `osm_type` (`n`/`w`), `osm_id`, `lon`, `lat`,
`kind` (`toll_booth`, `level_crossing`, `stop`, `traffic_signals`, `brand`), `brand`,
`brand_wikidata`, `name` (null when the tag is absent). A node's position is its location; a
way's is the mean of its node locations, a closed way's repeated first node counted once.
Relations are not read.

**Several sources** (a region of several states, **Regions of several states**) are read in
order: a way or POI already taken from an earlier source is skipped (`ways_duplicate`), though
the way's nodes are still taken from each source that holds it, and the per-source road files are
merged (`filter.merge_parts`: sorted, each node once at its newest version, of those at the
newest timestamp, of those the earliest source's): so when the extracts differ only in their shared nodes, the merge writes the
bytes one extract of their union would (a way or POI that differs keeps the first extract's
version). The merge streams: it reads the sorted parts in step, a heap holding each part's next
object, and writes the top one through `SimpleWriter` as it is, passing over the other parts'
copies, so it holds one object and one reader per source whatever their size; it writes the
bytes `osmium.MergeInputReader`'s simplifying merge writes from the same parts, which held them
all in memory, for any extract: a negative id never reaches the merge, since the `IdSet` of
taken ways and `BackReferenceWriter` both refuse one, and a part holds each object once (a
further copy in one part would be passed over) (`test_filter.py`; Oregon and Washington's roads, md5
cfaf12c6d7e5fc61603df82cc8712e82 both ways, and the national T1 roads merged from 49 parts,
0db8bb1f0be941cd5cbedda4b91306c3 both ways, with the same POI tables). The ways already taken are kept in an
`osmium.index.IdSet`, a bit set by way id (at most about 0.2 GB for every way id there is,
where a Python set of the country's 14 M ways would take about 1 GB). Outputs are written to
temporary files and renamed into place. The CLI passes the region's state extracts in its order,
or `--source` alone, and warns on stderr when the extracts' data dates differ ("warning: the
extracts hold data of different dates: …").

`FilterStats`: `ways_read` (highway ways), `ways_excluded`, `ways_above_ceiling` (kept by the
exclusions and inside the bbox, but of a class above the ceiling, so `ways_kept +
ways_above_ceiling` is what the region would keep without one, but for a border way above the
ceiling, counted in each extract that holds it), `ways_outside` (no node in the bbox),
`ways_duplicate`, `ways_kept`, and `pois` (rows per kind). The CLI adds "above T1 N" to its ways line for a region
with a ceiling. The POIs are the region's whatever its ceiling.

## `build`

`build.build_raw_graph(roads, crs_epsg, classes=None) -> (nx.MultiDiGraph, BuildStats)`, then
`build.save_stage(graph, stats, build_dir)`.

1. One pass with `FileProcessor(roads, NODE | WAY)`: node id → (lon, lat) into arrays; each
   way's node refs and the tags in `EDGE_TAGS`. `exclusions.kept_class` and the region's class
   ceiling are applied again (so an unfiltered file builds the same graph as its filtered
   version), a way id seen twice is read
   once, and a node repeated back to back in a way is read once (as OSMnx does), so it makes no
   junction and no zero-length self-loop. A way that references a node absent from the file
   raises `ValueError`, also when the file has no nodes at all.
2. **Junctions:** a node referenced at least twice by kept ways (by two ways, or by one way
   passing through it twice) or the first or last node of a way. Every other node is a shape
   point.
3. Each way is split at its junctions into **segments**.
4. Each segment becomes one edge per direction its **one-way rule** allows (`build.direction`;
   the first rule that matches wins, the rule's name is what `BuildStats.edges_by_rule` counts):

   | Rule | Tags | Directions |
   |---|---|---|
   | `oneway` | `oneway` in `yes`, `true`, `1` | forward (node order) |
   | `oneway_reverse` | `oneway` in `-1`, `reverse` | backward |
   | `roundabout` | `junction` in `roundabout`, `circular` | forward |
   | `motorway_implied` | `highway` in `motorway`, `motorway_link`, no `oneway` tag | forward |
   | `oneway_no` | `oneway=no` | both |
   | `reversible` | `oneway` in `reversible`, `alternating` | both |
   | `two_way` | anything else | both |

   So a roundabout tagged `oneway=no` stays one way, as in OSMnx; a motorway tagged
   `oneway=no` is two-way.
5. The graph, in OSMnx's conventions so its functions apply unchanged:
   - `G.graph = {"crs": "EPSG:<crs_epsg>", "pipeline_version": PIPELINE_VERSION}`.
   - Nodes are the junctions that end a segment, keyed by OSM node id, with `x`, `y` (projected
     with `pyproj`, `always_xy`) and `lon`, `lat`.
   - Edges, keyed `(u, v, key)` with parallel edges kept: `osmid` (way id, int), `highway` (the
     kept class; `alley` for alleys), the `EDGE_TAGS` the way has (`name`, `ref`, `maxspeed`,
     `lanes`, `bridge`, `tunnel`, `toll`, `junction`, `service`, `access`, `layer`; a missing
     tag is absent, never `None`), `oneway` (bool: drivable one way only), `reversed` (True on
     the backward edge of a two-way segment; a `oneway_reverse` edge is `reversed=False`, as in
     OSMnx), `length` (metres, from the projected geometry), `geometry` (projected `LineString`)
     and `osm_nodes` (tuple of the segment's OSM node ids, endpoints included), both in
     traversal direction.

`BuildStats`: `ways_read` (way objects in the file, duplicates and unkept ones included),
`ways_kept`, `segments`, `nodes`, `edges`, `edges_by_rule`. `save_stage` writes the graph to
`stages/build.pkl` and the stats to `stages/build.json`.

**Invariants.** Every graph node is a junction and every junction that ends a segment is a
node. A shape point lies inside exactly one segment. An edge's `osm_nodes` repeat no node,
except that a self-loop (a way returning to its own junction, e.g. a loop at the end of a
cul-de-sac) starts and ends at its node. `length` equals `geometry.length`; the two edges of a
two-way segment have equal lengths and reversed geometries.

**What the raw graph contains.** Besides the streets themselves, the raw graph holds what the
cleanup steps must deal with (counts from the Portland build of the 2026-10-30 extract, for
scale):

- **Self-loops** (`u == v`, 267 edges): a way that returns to its own junction, almost all loops
  at the end of a residential cul-de-sac, or a closed ring that touches the network at one node.
  No simple route can use one.
- **Parallel edges** (790 node pairs, self-loops aside): two ways joining the same two
  junctions, or one closed way with exactly two junctions (160 of the pairs).
- **Nodes of total degree 1** (63), one one-way edge in or out and nothing else: 10 where the
  bbox clip cuts a one-way road (8 of them outside the box, on ways kept whole), and most of the
  rest (47 of 53) where a one-way road continues onto a way the exclusions drop, mostly service
  roads and private residential ways. They cannot be strongly connected; connectivity drops
  them.
- A two-way dead end (a cul-de-sac) is a node with one neighbour and two edges, in and out.

**For the next step.** Shape points are not graph nodes, so an edge's shape lives only in its
`geometry` and `osm_nodes`. OSMnx 2.1's `simplify_graph` rebuilds a merged edge's geometry from
its nodes' `x, y` and collapses differing attribute values into unordered lists, so `cleanup`
carries `geometry` and `osm_nodes` through simplification itself.

## `cleanup`

`cleanup.run_cleanup(raw, allow_fragmented=False) -> (nx.MultiDiGraph, CleanupReport)`, then
`cleanup.save_stage(graph, report, build_dir)`. It reads the raw graph `build` wrote and runs
five steps in order, each a module with its contract in its header comment; the constants are
in `config.py`. The result is the graph the no-repeat rule rests on: a route that never repeats
one of its nodes or uses an edge and its twin never passes one junction, or one stretch of road,
twice.

1. **Simplify** (`simplify.py`, ours: OSMnx's `simplify_graph` would rebuild a merged edge's
   geometry from its nodes and lose the shape points). Every self-loop is dropped first: no
   vertex-simple route can use one. A node is contracted when its edges form exactly one
   through path per allowed direction (one in and one out from two different neighbours, or
   two in and two out that are the two directions of one road: for each neighbour, the edge
   back to it runs over the OSM nodes of the edge from it, reversed) and the two edges of each
   pair have the same `highway`, `oneway`, `reversed` and every `EDGE_TAGS` value. In the raw
   graph such a node always ends a way, so a node stays wherever a way's tags change (ref, name,
   speed limit, lanes, bridge, ...) and every edge stays homogeneous. A merge that would repeat
   an OSM node id (a chain closing on itself) is not made. Two one-way ways over some of the
   same nodes in opposite directions, one with a node of its own between two they share (1, 2,
   3 and 3, 2, 6, 1), are not one road: the node they share stays a junction, since contracting
   it would put it inside two roads, which `save` refuses. So a contracted node lies inside one
   edge, or inside the two directions of one road. The merged edge concatenates
   `geometry` and `osm_nodes`, sums `length`, and lists its ways in `osmids` (every edge's
   `osmid` becomes the tuple `osmids`). Mappers split streets end to end often, so this removes
   many nodes: 15% of Portland's.
2. **Stage A** (`consolidate.stage_a`): OSMnx 2.1's `consolidate_intersections(G,
   tolerance=<per node>, rebuild_graph=True, max_length=STAGE_A_MAX_LENGTH_M (60),
   dead_ends=True, reconnect_edges=True)`. Each node is buffered by its tolerance, the first rule
   its edges match: `STAGE_A_TOLERANCE_ROUNDABOUT_M` (1 m) on a roundabout edge, so stage B
   sees every ring; `STAGE_A_TOLERANCE_ARTERIAL_M` (15 m) on a `STAGE_A_ARTERIAL_CLASSES` edge
   (trunk, primary, secondary: divided arterials' medians are 20–30 m); else
   `STAGE_A_TOLERANCE_M` (8 m). Nodes whose buffers overlap and that edges of at most 60 m join
   become one `cluster` node; nodes near each other with no short edge between them (an
   overpass, the far ends of a cloverleaf loop) do not. A buffer is the 64-sided polygon
   geopandas makes (`consolidate.BUFFER_QUAD_SEGS`, 16 segments a quarter circle), so a pair
   whose distance is near the sum of their tolerances merges as the polygons' corners decide
   (23 such pairs on Portland). The post-pass places each cluster itself
   (`consolidate.cluster_centre`): at the centroid of the union of its members' buffers when they
   are a whole group of overlapping buffers (`consolidate.buffer_groups`), at the centroid of
   its members' points when the edge test split the group (as OSMnx does). OSMnx takes the first
   from one union of every buffer in the region, whose last bits depend on all the others (3,977
   of Portland's 4,053 such clusters differ from their own buffers' centroid, by at most
   1.5e-8 m); a cluster's own union does not depend on the rest of the graph or on its members'
   order, which is what lets an array pipeline place it the same way (pipeline version 4).
   OSMnx drops an edge whose two ends merged without keeping it anywhere, so the post-pass adds
   its interior `osm_nodes` to the cluster's members; it also replaces OSMnx's node attributes
   (`osmid_original`, `street_count`, list-valued `lon`/`lat`) and edge attributes
   (`u_original`, `v_original`) with ours. Edges at a cluster have their geometry extended to the
   cluster's position and their `length` recomputed from it.
3. **Twins** (`carriageways.find_twins`), on the stage A graph, so that a carriageway stretch
   stage B makes internal to an interchange still has its twins. Candidates are one-way edges of
   `TWIN_CLASSES` (motorway, trunk, primary, secondary, tertiary; no links, no roundabout rings)
   with a signature (ref set, else name; `TWIN_GEOMETRY_ONLY_CLASSES`, motorway and trunk,
   pair without one too). Detection works on the segments of each candidate's geometry, less the
   segment stage A added at a cluster end: a `shapely.STRtree` finds the segments within the
   lateral distance of the edge's class (`TWIN_LATERAL_M`: motorway 100 m, trunk 80, primary 50,
   others 35), and two segments pair when their edges differ, their signatures are equal, their
   names, less directional words ("Pacific Highway East" and "SW Pacific Hwy E" agree), are equal
   if both have one and neither is a motorway (freeway carriageways carry alias names, such as
   Banfield Freeway and Columbia River Highway), their bearings differ by 180° ±
   `TWIN_BEARING_TOL` (30°), each midpoint is within that distance of the other's line, and the
   foot of at least one of those perpendiculars falls on the other segment (they run side by
   side, not end to end).
   Edges with a pair of segments are twins, unless they share an OSM way: a way curling back
   beside itself (a hairpin ramp, a loop) is one road, and real carriageways are always separate
   ways; or unless their `osm_nodes` are the same run reversed: two ways over the same nodes in
   opposite directions are one road mapped twice (a duplicate, an import artefact), not two
   carriageways. Such a pair is not twinned but counted (`same_road_pairs`; the CLI's twins line
   adds ", N pairs of one road mapped twice" when there are any), and enrich's units make its two
   edges one road unit, as they do the two directions of a two-way road, so the engine never
   drives both. A known limitation: a reversed copy of one carriageway of a divided road runs the
   same way as the opposite carriageway, so it is not twinned with it. The relation is symmetric.
   Using an edge bans its twins. A one-way couplet a block apart shares a ref but not a name, so
   it is not a twin pair: OR 99E's Grand Avenue and MLK Boulevard, OR 8's Baseline and Oak
   Streets.
4. **Stage B** (`consolidate.stage_b`, ours): interchanges and roundabouts become one
   super-junction each.
   - The ramp (`*_link`) and roundabout edges, undirected, form components. A component's
     attachment nodes are those that also touch another road.
   - **Join:** two components whose attachment roads share at least two identities
     (`names.road_signature`: the normalised ref set, else the normalised name) and have
     attachment nodes within `STAGE_B_JOIN_M` (500 m) of each other are one cluster. The ramps of
     one interchange rarely share a node (a diamond's two sides, a cloverleaf's eight ramps), and
     consecutive interchanges share only the freeway, so this joins the first and not the second.
   - **Join along a carriageway** (`_carriageway_rejoins`, pipeline version 5): then two clusters
     are one when a carriageway runs through them interleaved. An edge's carriageway key is its
     highway, of `STAGE_B_CARRIAGEWAY_CLASSES` (motorway, trunk, primary), and its ref set, else its
     normalised name (`_carriageway_key`; an edge with neither has none and continues no
     carriageway). The candidates are two attachment nodes of different clusters within
     `STAGE_B_JOIN_M` of each other with a key in common among their edges. Their clusters are one
     when a chain of edges of one of the keys the two nodes share, starting from any node of one
     cluster that has such an edge leaving it, each edge continuing the one before it OSM node to
     node and never back the way it came, leaves that cluster, passes a node of the other and comes
     back to the first within `STAGE_B_MAX_DIAMETER_M` (1,500 m) (`_comes_back`, which follows every
     branch, shortest first, so the answer does not depend on the order of the graph's edges). Kept
     apart, the carriageway would pass the first super-node twice, and no route could drive it
     straight through. A chain never turns onto another road at a corner: the key holds the name as
     well as the class. Woodburn's interchange (I-5 and the Newberg Highway, OR 219 west of I-5 and
     OR 214 east) is the case: its halves share only I-5, so the identity join leaves them apart,
     and northbound I-5 leaves the east half at its off-ramp (4,449 m along OSM way 1186652782),
     passes the west half's on-ramp (4,999 m) and comes back to the east half's on-ramp (5,427 m).
     The test runs on the clusters the identity join made, so a carriageway that comes back to
     another part of its cluster counts (OR 99E on Martin Luther King Boulevard in Portland), and
     repeats until nothing more joins; a cluster that grows still passes every test it passed, so
     the result does not depend on the order. The joined clusters go through the slip lanes, the
     pockets and the diameter guard like any other.
   - **Slip lanes:** a cluster whose links are all minor (`STAGE_B_MINOR_LINKS`:
     `primary_link`, `secondary_link`, `tertiary_link`) also takes in every node that two of its
     attachment nodes reach through other roads of at most `STAGE_B_SLIP_CENTER_M` (150 m), of
     two different identities: the at-grade crossing the slip lane bypasses, which is the same
     junction. A node reached along one road only is no crossing (two branches of a road
     merging, or two carriageways meeting). Clusters that claim the same node, or a node of
     another cluster, become one.
   - **Pockets:** each cluster then takes in the interior nodes of every path of at most
     `STAGE_B_POCKET_M` (600 m) along one road from one of its attachment nodes to another,
     outside the cluster and through no other cluster, and repeats until it takes nothing more.
     Without that, a road that leaves the super-junction and comes back to it (a bridge between
     two ramp terminals, a frontage-road crossing) could not be driven straight through: the
     route would pass the super-node twice. One road is one ref signature, or, since pipeline
     version 5, one normalised name (`_road_keys`): at Woodburn the Newberg Highway becomes OR 214
     on the bridge, so once the halves are joined, only its name makes the bridge a pocket, and
     the highway can still be driven straight across. Roads with neither ref nor name make no
     pockets. A path that changes road (one carriageway, a cross street, the other carriageway)
     is not a pocket, and a road that leaves a node and comes back to the same node is a
     turnaround, not a pocket.
     A road that leaves a super-junction and comes back to it with a pass from its in-edge to
     where it goes on not in the table is a lost through movement (`tests/pipeline/invariants.py`,
     `lost_through`: chains of edges of one highway and ref, OSM node to node, every branch, back
     within four edges). On Portland 9 at 9 super-junctions before pipeline version 5 and 7 since,
     on either extract; on Oregon 20 at 19 before and 12 at 11 since. Each left is a road without
     a ref that leaves under one name and comes back under another (North Argyle Way and North
     Columbia Boulevard, North Greeley Avenue and North Going Street over 756 m, Northwest Zion
     Church Road and Northwest Glencoe Road, South Barlow Road and South Arndt Road, Northeast
     Cascades Parkway by Northeast Mount Hood Avenue, Southeast 91st Avenue round the block by
     Southeast Tolman Street and Southeast 90th Avenue; Southwest Country Club Drive and Southwest
     35th Street in Corvallis), or a pocket longer than 600 m (I-5 and US 30 by the Rose Quarter,
     1,497 m; US 26 by Banks, 1,302 m; Old Highway 30 at Meacham, 1,378 m; US 97 by Sunriver,
     3,456 m).
   - **Diameter guard:** a cluster whose bbox diagonal exceeds `STAGE_B_MAX_DIAMETER_M`
     (1500 m), other than a roundabout, is split by single linkage of its attachment nodes at
     `STAGE_B_SPLIT_EPS_M` (300 m, `scipy.spatial.cKDTree.query_pairs`); every other node joins
     the group of its nearest attachment node along the cluster's own edges, and a group of one
     node stays as it is. A cluster whose attachment nodes stay linked is kept whole however wide:
     the I-405/US 26 complex, 1,655 m across, is accepted as one junction.
   - Each cluster becomes one node at the mean position of the nodes it replaces: `kind`
     `roundabout` when all its ramp and roundabout edges are roundabout edges, else
     `interchange`. Internal edges (both ends inside) leave the graph and are kept in its record;
     boundary edges are re-pointed to it, their geometry extended to its position.
   - **Transitions:** the allowed `(in_edge, out_edge)` pairs at the super-node are those for
     which a directed path through the internal edges leads from the in-edge's original end to
     the out-edge's original start (the same node counts). A full cloverleaf or diamond allows
     every pair, U-turns included (two loops, or off, across and back on); a half-diamond does
     not. Route splicing enforces them from M1.
   - **Twins carried across** (`_carry_twins`): an edge that stays maps to itself. An edge
     internal to a super-node maps to the super-node's boundary edges on the same road (equal
     signature) along its own carriageway: the in-edges from which that road leads to it through
     the super-node and the out-edges it leads to, kept only where they head the internal edge's
     way (within 90°); failing those, every boundary edge on that road heading its way, then
     every boundary edge heading its way, then the first of those sets unfiltered. A pair whose
     two edges are internal to one super-node is not carried (a route passes it once), but a
     passage through the super-node that must drive both (out on one carriageway, a U-turn at
     the median's end inside the junction, back on the other, as from NW Joy Ave to NW Joy Ave
     at NW Cornell Rd) is banned: its in-edge and out-edge become twins. Going through an
     interchange along a carriageway thus bans the opposite carriageway's stretch beside it,
     inside or outside, and never the same carriageway: mapping to every boundary edge on the
     road, both directions, would ban driving straight through on the other carriageway (135
     such bans on Portland, none this way). Besides, a transition every internal path of which
     drives an internal edge with a twin outside the super-node (a cross street that jogs along
     a carriageway inside it, as S Corbett Ave does along Macadam) bans that twin on its in-edge
     and its out-edge, whatever road they are on; when the twin is itself a boundary edge of the
     super-node, which a route uses only in a passage through it, only the transition that
     drives the stretch and then the twin is banned. This over-bans when a route uses that
     in-edge or out-edge for another transition; the exact form is a per-transition ban table,
     planned with M3's transition-aware search. The table stays symmetric.
   - Then nodes are numbered `0..n-1` (`consolidate.relabel`): plain and cluster nodes in
     ascending order of their smallest member, then the super-nodes by theirs; edge keys are
     kept and the tables below remapped.
5. **Connectivity** (`connectivity.largest_component`): the largest strongly connected
   component is kept (networkx; ties to the component with the smallest node id). When it holds
   less than `SCC_MIN_RETAINED` (0.98) of the nodes, or a component it would drop holds more
   than `SCC_MAX_DROPPED_NODES` (200) nodes, `run_cleanup` raises `FragmentedGraphError`, whose
   message says which, and the CLI exits 1 without writing the stage, unless
   `--allow-fragmented`. The share alone cannot tell a town whose only way in a filtering rule
   removed (one component, a fraction of a percent of a state's nodes) from the many small
   networks a state holds behind tracks and private roads, so the cap on the largest dropped
   component is the check for the first, and the share (0.99 until the Oregon build, **Oregon**)
   for a filtering rule that fragments everything. The kept nodes are numbered `0..n-1` again, in
   the same order.
   - **No turn allowed:** connectivity does not read the turn tables, and dropping an edge
     drops the transitions that name it, so a super-junction can be left with edges in and out
     and no allowed pair between them. No route can pass through it, and a build cannot say so
     (a node without transition rows allows every turn), so `run_cleanup` drops it with its
     edges, runs connectivity again on what is left, and repeats until none remains. It happens
     where a region's edge cuts an interchange whose interior lies beyond the extracts: I-90 at
     the Washington–Idaho line (State Line, 47.6968, −117.0417) in the build of Oregon and
     Washington allowed the straight-through pairs at stage B, each through a stub beyond the
     line; once connectivity dropped the stubs, eastbound arriving from the west and westbound
     leaving to it had no pair left between them. A super-junction without edges in or out (a
     ramp chain touching no other road) allows no pair either, but connectivity drops it as it
     is. The report adds the rounds up: nodes, edges, length and components dropped, the largest
     dropped components, `retained` against the stage B graph, and `dropped_no_turn`, the
     super-junctions dropped this way; the thresholds apply to the sums. The CLI's connectivity
     line adds ", N super-junctions dropped with no allowed turn" when there are any. Portland
     and Oregon have none, so their builds are unchanged.

**The stage file.** The networkx engine's `stages/cleanup.pkl` is the cleaned `MultiDiGraph`
(the arrays engine's `stages/cleanup/` holds the same graph as arrays, **The arrays engine**):

- `G.graph`: `crs`, `pipeline_version`, `superjunctions`, `twins`.
- Nodes `0..n-1`: `x, y` (projected), `lon, lat`, `kind` (`plain`, `cluster`, `interchange`,
  `roundabout`) and `members`, the sorted original OSM node ids the node stands for (a plain
  node's own id; a cluster's merged nodes plus the shape points of the edges it absorbed; a
  super-node's parts plus the shape points of its internal edges).
- Edges keyed `(u, v, key)`, parallel edges kept: `osmids`, `highway`, the `EDGE_TAGS` present,
  `oneway`, `reversed`, `length`, `geometry`, `osm_nodes` (original OSM ids, in traversal
  direction).
- `G.graph["superjunctions"][node]` for every interchange and roundabout node: `kind`,
  `members`, `parts` (the members of each node it replaced, sorted), `internal` (each internal
  edge's attributes plus `u`, `v`: the smallest OSM id of the part at each end), `roundabouts`
  (the number of roundabout rings among its internal edges, so an interchange that holds a
  dumbbell's ring still counts it) and `transitions` (sorted `((u, v, key), (u, v, key))`
  pairs; present for every super-node, never empty after cleanup: connectivity drops one
  without edges in or out, and step 5 one left with both and no pair). `GraphData` reads a node
  without transition rows as allowing every pair and cannot express an empty table, so `save`
  still refuses a build that has a super-node whose table is empty, as a guard.
- `G.graph["twins"]`: edge key → sorted tuple of its twins' keys, for every edge with one.

**Lengths through junctions.** An edge at a cluster or super-node has its geometry extended to
the node's position and its `length` follows, so for M1 these stubs stand in for the path
through the junction. The exact internal length of each transition is M3's business, a pair term
of the transition-aware search; `enrich` and `save` keep `length` as cleanup leaves it.

**Invariants** (`tests/pipeline/invariants.py` checks them after every step, on every fixture
and on the Portland build): (1) every OSM id of the raw graph (nodes and shape points, those of
self-loops aside) is in exactly one node's `members` or inside exactly one road segment (an edge
and its reverse count once), until connectivity drops whole components; (2) an edge's first and
last `osm_nodes` are members of its tail and head; (3) no edge's `osm_nodes` repeat an id;
(4) twins are symmetric, between existing edges, never an edge and itself; (5) a super-node's
transitions name its own in- and out-edges; (6) they equal the reachability rebuilt from its
`parts`, `internal` edges and the boundary edges' OSM ends, and its `roundabouts` count the rings
among its internal edges. These are what the validator's proof from original ids
(`docs/PLAN.md` §8.1, checks 3–5) rests on.

**The report.** `stages/cleanup.json` is `CleanupReport`: `input` (raw nodes and edges);
`simplify` (`self_loops_dropped`, `nodes_removed`, `nodes`, `edges`); `stage_a` (`clusters`,
`nodes_merged`, `edges_absorbed`, `cluster_sizes`: nodes merged → clusters, `nodes`, `edges`);
`twins`, found on the stage A graph (`candidates`, `paired`, `pairs`, `same_road_pairs`: pairs
left out as one road mapped twice, `motorway_trunk_m`, `coverage`: the share of one-way motorway
and trunk length with a twin, the QA metric);
`stage_b` (`superjunctions` by kind, `sizes`: nodes replaced → super-nodes, `joined`,
`rejoined`: joins made along a carriageway, `slip_centres`, `pocket_nodes`, `split`,
`internal_edges`, `twin_pairs` carried across,
`twin_transitions`: transitions that must drive an internal edge with a twin outside,
`twin_passages`: passages banned for driving both edges of a twin pair inside, `nodes`,
`edges`); `connectivity` (nodes and edges before and after, dropped nodes, edges and length, a
two-way road counted once, the number of dropped components and the 20 largest with their size,
length and centroid, `retained`, `dropped_no_turn`: the super-junctions dropped for allowing no
turn, the rounds after them added in); `seconds` per step (with `handover` where
`cleanup.run_cleanup` hands the arrays engine's graph to networkx, as the tests do).

**Portland, for scale** (the 2026-10-30 extract; about 30–110 s depending on the machine's load,
most of it stage A): simplify drops 267 self-loops and removes 12,833 nodes; stage A merges 9,771
nodes into 4,236 clusters; twins pair 5,707 of 7,261 candidates (3,732 pairs), covering 95.9% of
one-way motorway and trunk length, the unpaired rest mostly couplets; stage B makes 662
super-junctions (496 interchanges, of which 122 joined from several ramp components, 147
slip-lane clusters with their crossing, 4 holding a roundabout ring, 11 split by the guard; 166
roundabouts), takes in 661 pocket nodes and carries 3,388 twin pairs (298 transitions must drive
a twinned internal stretch, 250 passages at 39 super-junctions must drive both carriageways
inside); connectivity keeps 65,810
of 66,072 nodes (99.6%), dropping 262 nodes and 62.9 km in 157 components, the largest 11 nodes.
The cleaned graph has 65,810 nodes and 168,167 edges. At pipeline version 5 (build c60bc0c9cb4a,
the 2026-10-29 extract, about 50 s), stage B joins 25 pairs of clusters along a carriageway and
makes 647 super-junctions (481 interchanges, of which 123 joined, 144 slip-lane clusters, 14
split by the guard; 166 roundabouts), takes in 678 pocket nodes and carries 3,433 twin pairs (293
transitions, 304 passages); connectivity keeps 65,779 of 66,041 nodes, dropping 262 nodes and
62.8 km in 157 components, and the cleaned graph has 65,779 nodes and 168,120 edges.

## `enrich`

`enrich.run_enrich(graph, pois) -> EnrichReport` adds to the cleaned graph, in place, what the
engine and the modes read, then `enrich.save_stage` pickles it to `stages/enrich.pkl` and writes
the report to `stages/enrich.json` (the arrays engine computes the same values as arrays into
`stages/enrich/`, **The arrays engine**). Edges are visited in key order; nothing is random. The
constants are in `config.py`.

- **Speed and time.** `parse_maxspeed` reads the `maxspeed` tag: `"45 mph"`, `"45mph"`; a bare
  number is mph, the US convention; `"50 km/h"`, `kmh`, `kph`; a `;` list takes its first value.
  Anything else (`signals`, `none`, `US:urban`, `0`, knots) falls back to the class default,
  `SPEED_DEFAULT_MPH` (motorway 65, trunk 55, primary 45, secondary 35, tertiary 30,
  unclassified and residential 25, living street 15, alley 10, every `_link` 35). `speed_kmh`
  is stored, `speed_parsed` says which, and `time_s = length / (speed_kmh / 3.6)`. Both are
  float64 here; `save` stores the time as float32 and the speed as an index into the build's
  table of distinct speeds, exactly.
- **Identity** (hard mode, directions): `names.identity_of`, the highest-class ref (Interstate,
  then US, then a state's postal code or `SR`, then any other, such as a county's `CR`;
  alphabetical within a class), each ref keeping its `BUSINESS`, `SPUR` or `ALT` suffix so such a
  route never takes its mainline's identity; else the normalised name; else none.
- **Road unit.** One OSM node run in both directions, however many ways map it: the edges
  whose `osm_nodes` are one run or that run reversed (the two directions of a two-way road, and
  every copy of a road mapped more than once: two one-way ways over the same nodes in opposite
  directions, two two-way ways, a one-way and a two-way way, three of them) all get the smallest
  key among them as `unit`, which `save` turns into the smallest edge id. A one-way edge mapped
  once is its own unit. Parallel roads between the same junctions differ in their shape points
  and stay separate units.
- **Flags** (`EdgeFlag`): bridge and tunnel when the tag is present and not `no` (`viaduct`,
  `building_passage` count), toll for `toll=yes`, roundabout for `junction` `roundabout` or
  `circular`, link for a `*_link` class. Almost every ring and ramp edge is inside a
  super-junction after cleanup, so few edges carry the last two.
- **Bearings**, whole degrees clockwise from the projected CRS's grid north, 0–359 (in UTM zone
  10 grid north is within 0.2° of true north around Portland, but about 4° off in eastern
  Oregon): `bearing_out`, leaving u, over the first `BEARING_SAMPLE_M` (20 m) of the road;
  `bearing_in`, arriving at v, over the last 20 m; over the whole road when it is shorter. The
  road is the edge's geometry less the stubs cleanup added to reach a merged node's position
  (**Lengths through junctions**; `enrich.road_geometry`): one coordinate at an end on a stage A
  cluster or a super-node, two where the super-node took in a stage A cluster at that end (the
  part of the super-node holding the end's OSM node has more than one member). Every other
  coordinate is one of the edge's OSM nodes, so the bearings describe the road as it arrives and
  leaves, not the stub; an edge that is nothing but stub keeps its whole geometry. This is the
  format's definition (`docs/reference/graph.md`), which the turn rule of `docs/PLAN.md` §3.6,
  `wrap(bearing_out(e_out) − bearing_in(e_in))`, needs. Before pipeline version 2 the 20 m
  included the stub (7 m median at a stage A cluster, 66 m to an interchange's centroid), and
  on Portland 21.5% of the turns between two roads at a stage A cluster classed differently
  from the road's own, 63% at interchanges, 19% at roundabouts; now none. `uv run python
  tools/turn_bearings.py --region portland` reports it for a saved build, from the whole shapes
  its enrich stage holds (the build's own geometry is simplified), counting each edge's stubs
  (its coordinates less its OSM nodes): at version 6 the stored bearings are the road's at every
  turn of every kind of node (measured on the build's float32 coordinates, as the tool did before
  version 6, 0.2–0.5% of the turns differed, their rounding flipping turns at a class's
  threshold).
- **Collectibles**, from the POI table's node rows (a way row is not counted): a toll booth or
  level crossing whose OSM id lies inside edges counts on every edge that holds it, both
  directions of a two-way road, of which a legal route uses one; one that is a node's member (at
  a junction, or a shape point a cluster absorbed) counts on that node's lowest-key out-edge, an
  M1 approximation until node collectibles (M3); any other is off the graph, and so is one at a
  junction without out-edges (a lone node, which only `--allow-fragmented` keeps), since no edge
  a route drives holds it.
- **Node delay**: the largest `NODE_DELAY_S` (stop 10 s, signals 30 s) among the stop signs and
  signals in the node's members, so a signal cluster delays once. One that is a shape point adds
  none: most stop signs are mapped a few metres before the junction, on the approach.
- **Pass-through**: some in-neighbour u and out-neighbour w with u ≠ w, self-loops ignored.
- **Brands** (`index_brands`, the brand pilgrimage's index), from the POI table's `brand` rows
  (a node, or a way at the mean of its nodes): each is projected and snapped to the nearest
  node a route can pass through (pass-through, above) within `BRAND_SNAP_M` (100 m), ties to
  the lower node. A node that is not pass-through can only start or end a route, so it is never
  a brand's: a POI with only such nodes within 100 m is counted at a dead end, one with none
  too far. POIs are grouped by `brand:wikidata` when they carry it, else by the `brand` string
  casefolded with its whitespace collapsed, so a POI without a wikidata id stays apart from a
  wikidata group spelled the same (no disambiguation beyond that). A brand's display name is
  its group's most common `brand` spelling, whitespace collapsed (ties to the smaller string;
  else its most common `name`, else its wikidata id; a `;` list stays one string). A brand on
  fewer than `BRAND_MIN_LOCATIONS` (3) distinct nodes is left out. The kept brands, in (name,
  wikidata) order, which is their id, each with its nodes ascending and the POIs snapped to
  each, are `graph.graph["brands"]`, which `save` writes as arrays.

`stages/enrich.json` is `EnrichReport`: edges whose speed was parsed and defaulted, by class;
distinct identities and the edges with one; units and the two-way pairs among them (units
holding edges in both directions); edges per flag; per
collectible kind the POI nodes found, placed inside edges, at junctions and off the graph, and
the edges with a count; per delay kind the same, shape points counted apart; nodes with a delay;
pass-through nodes; per brand POI the found, snapped, skipped at a dead end and too far, the
brands found and kept, and the kept brands' brand-node rows and the distinct nodes they lie on;
seconds.

**Portland** (the 2026-10-30 extract, 4–6 s): 89,902 of 168,167 edges have a parsed speed;
16,864 identities on 163,645 edges; 87,793 units, 80,374 of them two-way pairs; 1,088 bridge, 23
tunnel, 4 toll and 54 link edges; 2 toll booths (on 4 edges), 1,442 level crossings (550 inside
edges, 146 at junctions, 746 on roads the filter dropped); of 5,960 stop signs 730 are junction
members and 4,471 shape points, of 3,377 signals 2,469 and 829; 2,438 nodes have a delay; 51,406
of 65,810 nodes are pass-through. Of the 5,933 brand POIs (2,520 nodes, 3,413 ways), 4,505 are
snapped, 121 skipped at dead ends and 1,307 have no node within 100 m; 287 of the
1,041 brands are kept, 3,661 brand-node rows on 2,411 nodes (3,858 POIs): the
United States Postal Service (post boxes included) on 174 nodes, Starbucks 126, Plaid Pantry 86,
Biketown 84, Chevron 76, 7-Eleven 75. Four display names belong to two kept brands each:
Amazon Hub, Columbia Bank and Shell (each under two wikidata ids) and Nectar (one group without
a wikidata id beside one with it); 6 kept brands have no wikidata id.

## `tiers`

`tiers.run_tiers(graph, allow_fragmented=False) -> TiersReport`, then `tiers.save_stage`. Tier
`t`'s roads are the edges whose class is in `config.TIERS[t]`, its own nodes the nodes they
touch. Each tier keeps the largest strongly connected component of its roads (networkx; ties to
the component holding the smallest node id), so a search restricted to the tier reaches every
node it keeps and can leave it again. T4 holds every class and keeps the whole cleaned graph.
When a tier keeps less than `TIER_MIN_RETAINED` (0.95) of its own nodes, `run_tiers` raises
`FragmentedTierError`, naming each such tier, and the CLI exits 1 without writing the stage,
unless `--allow-fragmented`.

`stages/tiers.json` lists, per tier: `name`, `classes`, `edges` and `nodes` of its roads,
`kept_nodes`, `kept_edges` (its roads with both ends kept), `components`, `retained`, and
`node_ids`, the kept nodes ascending, which `save` turns into the masks.

The small fixtures cut through networks, so their upper tiers are often fragments: a motorway
whose ramps are inside its interchange is one-way stubs around the super-node at T0 (diamond,
cloverleaf), and the overpass's two tertiary roads meet only through a residential connector.
**Portland** (2–3 s): T0 keeps 1,345 of 1,345 nodes, T1 5,202 of 5,202, T2 11,693 of 11,693,
T3 20,202 of 20,213 (99.95%; 7 components), T4 all 65,810 (pipeline version 3; at version 5,
build c60bc0c9cb4a: 1,331, 5,169, 11,663, 20,173 of 20,184 and 65,779).

## `save`

`save.graph_data(graph, tier_nodes, meta) -> GraphData` and `save.graph_geometry(graph, data)
-> Geometry`, then `save.save_build(data, build_dir, geometry)`. The arrays and the geometry are
`docs/reference/graph.md`'s; how they come from the enriched graph:

- **Edge ids** are the positions of the sorted edge keys `(u, v, key)`, the format's (u, v,
  original order); `out_edges` and `in_edges` list each node's edges in ascending id. A unit's
  id is the id of its key.
- **Nodes**: `x, y` (the build derives lon/lat from them); `node_kind` from `kind`
  (`NodeKind`); `pass_through`; `delay_s`; `members`. A route's states come from the Census
  polygons at presentation time (`docs/reference/presentation.md`), so no node carries one.
- **Edges**: `length`, `time_s` and the enriched values; `speed_kmh` the distinct speeds
  (float32, ascending) and `edge_speed` each edge's index among them, more than 256 refused
  with `TooManySpeedsError` (Portland has 13); `edge_highway` from the class string
  (`HighwayClass`); `edge_tier` the first tier holding the class; `edge_name` and `edge_ref`
  index the raw `name` and `ref` tags, `edge_identity` the identities, each table sorted. The
  way ids (`osmids`) and the OSM nodes along each edge (`osm_nodes`) stay in the stages.
- **Geometry** (`graph_geometry`): each edge's projected shape, which runs from its tail's
  position to its head's, simplified by Douglas–Peucker (shapely's `simplify`,
  `preserve_topology=False`) to `GEOMETRY_SIMPLIFY_M` (2 m) with its ends kept, then encoded as
  whole-metre steps (`graph.geometry.encode`). The lengths and bearings were measured at
  `enrich` on the whole shape, so nothing the engine reads depends on the simplification.
- **Twins** and **transitions**: the cleaned graph's tables, keys turned into ids, rows sorted.
- **Tiers**: `tier_node_mask[t]` what `tiers` kept, `tier_edge_mask[t]` the tier's classes with
  both ends kept.
- **Collectibles**: `node_roundabouts`, the `roundabouts` count of each super-junction's record
  (the rings among its internal edges, so an interchange that holds a ring counts it), 0 at
  every other node.
- **Brands**: enrich's index as `brand_indptr`, `brand_nodes` and `brand_locations`, in its
  order (the brand ids), each brand's name and wikidata id in `strings.brands` and
  `strings.brand_wikidata`.

A super-node whose transition table is empty allows no turn through it, which the format cannot
say (a node without rows allows every turn): `graph_data` raises `EmptyTransitionsError`, naming
the node and its position, and the CLI exits 1. It is a guard: cleanup drops such super-nodes
(**cleanup**, step 5).

The validator proves a route passes no original OSM node twice from the nodes' members alone
(`docs/reference/engine.md`, `osm_ids`), which rests on what lies along the edges, kept in the
stages only. `graph_data` checks it on every build and raises `ShapePointError` (the CLI exits
1): every edge starts and ends at OSM nodes merged into its tail and head, and every OSM node
inside an edge lies inside it once, is no node's member and lies inside one road unit only (the
edge, or the two directions of its road). Simplification removes only a node with one through
path per direction, and at a node with two edges in and two out only the two directions of one
road, so it holds by construction; on Portland its 366,196 inner OSM nodes do.

`save.build_meta` assembles `meta`: `region`, `build_id`, `crs_epsg`, `created` (UTC),
`pipeline_version`, `format` (4), `sources` and `params`. `save.build_sources` gives the
extracts `filter` read, in order (file, md5 and `source_timestamp` of each), after checking that
the filtered roads are still the ones `build` read (their md5 against the sidecar of the build
stage cleanup read, `layout.build_stage`: `build.pkl`'s or the arrays engine's `build/`'s);
otherwise it raises `SourcesChangedError` and the CLI says "the filtered extract changed since
this build was built; rebuild". A filter record of the shape before M5.3 (no data date per
extract) is refused the same way, naming the `filter` rerun. `save.build_params` gives the
`region`, its `bbox` and `crs_epsg`, its class ceiling (`classes`, only for a region that has one,
so every other build's params are as they were), the `config` and `allow_fragmented` the
cleanup, enrich and
tiers sidecars recorded, and save's own `config` (`save.parameters()`: `GEOMETRY_SIMPLIFY_M`, the
geometry's tolerance), so
`graph_version` covers what the build was made with; `pipeline_version` stays out of
`params`. `graph_data` fills `counts`: nodes, edges, twin
pairs, transition rows, super-junctions. `save_build` writes the build and its geometry with
`write_build` (which validates them), reads them back with `read_build` and `read_geometry`,
raises `RuntimeError` if any array, the strings or the meta differ, and writes
`stages/save.json` (`graph_version`, the counts, `megabytes` of the arrays and strings, which
the engine reads, `geometry_megabytes`, seconds); the CLI prints both sizes. `save.remove_build` deletes a written build, `meta.json` first, for `--force`.

**Portland** (2–4 s): 65,810 nodes, 168,167 edges, 3,353 twin pairs, 11,378 transition rows at
657 super-junctions, 39.9 MB; 170 roundabout rings, at 165 roundabouts (one holds two) and 4
interchanges (pipeline version 3). At version 5 (build c60bc0c9cb4a, the 2026-10-29 extract):
65,779 nodes, 168,120 edges, 3,397 twin pairs, 11,455 transition rows at 642 super-junctions (477
interchanges, 165 roundabouts), the same 170 rings. At version 6 (build 3a4a3df6269f, the same
extract; about 4 s, the checks and the geometry included), the same nodes, edges and tables in
15.8 MB of arrays and strings (39.9 MB at format 3) and 1.4 MB of geometry.

## `qa`

`qa.run_qa(build_dir) -> QaReport` reads the saved build, the stage reports and the
super-junction records (`qa.superjunctions`: in `stages/enrich.pkl`, or the arrays engine's
`stages/cleanup/`), and writes under the build's `qa/` (a build
without an array of the format is one line naming its remedy, the rebuild from `filter` for one
another pipeline version wrote, and exit 1; a plain `save` refuses such a build with the same
line):

- `twins.geojson`: one feature per twin pair, a MultiLineString of the two edges (along the
  build's geometry), with their ids, classes, identities and lengths;
- `superjunctions.geojson`: a point per super-junction with its kind, parts (nodes replaced),
  members, transition rows, in- and out-edges and whether every turn is allowed;
- `dropped_components.geojson`: a point per component cleanup dropped and listed (the 20
  largest), at its centroid, with its nodes and length;
- `tiers.geojson`: a point per node a tier dropped (its roads touch it, its largest component
  does not hold it);
- `qa.json`: counts by class (edges, and km with a two-way road counted once) and by tier, node
  kinds, the twins (cleanup's statistics, among them the pairs found on the stage A graph and
  the coverage, and `pairs_saved`, the pairs the build holds), the super-junction histogram (parts → super-junctions, by
  kind), the cleanup, enrich, tiers (without their node lists) and save reports, the
  thresholds, and `passed`.

**Thresholds** (`docs/PLAN.md` §2.4f, §2.8): twins cover at least `QA_TWIN_COVERAGE_MIN` (0.95)
of one-way motorway and trunk length (cleanup's figure; with none to measure it passes as n/a);
cleanup kept at least `SCC_MIN_RETAINED` (0.98) of the nodes ("SCC retained"); the largest
component it dropped holds at most `SCC_MAX_DROPPED_NODES` (200) nodes ("SCC largest dropped", a
threshold with a `maximum` instead of a `minimum`, printed "at most"); every tier kept at least
`TIER_MIN_RETAINED` (0.95) of its own. `pipeline qa` prints each with its value and exits 1 when
one fails. No map page and no random snap tests yet (§2.8 has them for later).

**Portland** (1–1.5 s): twin coverage 0.9588, SCC retained 0.9960, SCC largest dropped 11, tiers
1.0, 1.0, 1.0, 0.9995, 1.0: passed (build ffe2e7c881ca, under the rules since the Oregon build as
before them). The connectivity constants are cleanup's parameters, which every build's `params`
record and so its `graph_version` covers: a build made under the new rules has the same arrays
as one made before them but another `graph_version` (Portland at pipeline version 3:
455bc53299d2… against ffe2e7c881ca's 8867636799a6), and the routes stored on the older build
are refused ("re-plan"). So Portland's version 4 build, made under them, has another
`graph_version` than the table under **Build ids** gives for it, which was measured before them.
The whole pipeline from the Oregon extract takes 47–75 s on the dev machine, depending on its
load; on the quieter run: filter 13 s, build 3 s, cleanup 23 s, enrich 4 s, tiers 2 s, save 2 s,
qa 1 s.

## `prune`

`pipeline prune --region R` deletes a written build's stage data, the arrays engine's stage
directories (`stages/build/`, `stages/cleanup/`, `stages/enrich/`) and networkx's pickles, once
its QA has passed (`qa/qa.json`'s `passed`), printing what it removed and how much. It keeps
what the engine, the server and a reader need: `meta.json`, `arrays/`, `geometry/`,
`strings.json`, `qa/`, and the steps' reports and sidecars under `stages/` (`build.json`,
`cleanup.json`, `tiers.json` and the rest). A build its `--build-id` names, else the region's
latest. It refuses, in one line and exit 1, a build not yet saved, a build without a QA report,
and one whose QA failed, unless `--force`, which prunes whatever QA said or whether it ran; a
build with nothing left to prune is skipped. One failure it takes as a pass: a build cleaned with
`--allow-fragmented` (its `params.cleanup.allow_fragmented`) whose QA fails only the connectivity
rule's thresholds, "SCC retained", "SCC largest dropped" or both (`qa.CONNECTIVITY_THRESHOLDS`),
since keeping the largest component whatever it leaves out is what the flag asks for; any other
failure beside them (twin coverage, a tier's retained share) is refused.

Pruning trades the steps' skipping for disk: the stages are what each step checks itself against,
so once they are gone the steps from `cleanup` on cannot run or skip until `build` has run again
(from the filtered roads, which stay; the build id is the same), and `qa` cannot rerun on the
pruned build. The build itself is untouched and loads and plans as before. The state builds are
pruned so (**The state builds**): a state's stages are about six times its build.

## Oregon

The whole state (M5.1, `docs/PLAN.md` §7), region `oregon` in `config.REGIONS`: the Geofabrik
extract `raw/oregon-latest.osm.pbf` (243 MB, md5 1de27cc62802f3db82645ec52a41026f, data of
2026-10-28T21:21:02Z), no bbox, EPSG:32610; its places from the Census files of Oregon (FIPS 41)
and Washington (53). Build 5913ade7a2df, pipeline version 3, `graph_version` 73b137860fc2; the
numbers below are that build's (version 4 then moved stage A's clusters by a few nanometres).

**Time and memory**, measured with `tools/bench_build.py --region oregon --source
<raw extract> --force` (`docs/reference/testing-tools.md`) on the dev machine (Apple M5, 10 cores,
24 GB) while other lanes ran, load average about 20:

| Step | Wall | CPU (user + sys) | Peak RSS | Writes |
|---|---|---|---|---|
| filter | 44 s | 41 s | 0.93 GB | roads 31.4 MB, POIs 1.1 MB |
| build | 31 s | 21 s | 1.47 GB | `build.pkl` 241.5 MB |
| cleanup | 209 s | 165 s | 2.58 GB | `cleanup.pkl` 215 MB; simplify 14 s, stage A 133 s, twins 2 s, stage B 30 s, connectivity 16 s |
| enrich | 51 s | 34 s | 1.94 GB | `enrich.pkl` 284 MB |
| tiers | 12 s | 11 s | 1.60 GB | `tiers.json` 4.4 MB |
| save | 24 s | 16 s | 1.62 GB | the build, 174.2 MB (`arrays/` 164 MB, `strings.json` 2.4 MB) |
| qa | 7 s | 7 s | 1.62 GB | `qa/` 5.2 MB |
| all | 378 s | | 2.58 GB | |

At a load average of 20–60 the same steps took 10.5 minutes (cleanup 255 s, stage A 178 s), and
cleanup's peak ranged from 2.58 to 3.91 GB over three runs; nothing else came near. Stage A,
OSMnx's `consolidate_intersections`, is two thirds of cleanup and a third of the pipeline; the
networkx stages hold the graph at about 1.5–2.5 GB in memory and 0.2–0.3 GB pickled. `docs/PLAN.md`
§6 estimated 0.7 million nodes and 1.8 million edges, 20–40 minutes for build and simplify and
20–60 for stage A: the graph is a third of that and the whole pipeline takes 6–11 minutes.

**What it holds.** The filter keeps 202,426 of 861,974 highway ways and 36,947 POI rows (12,875
brand, 4,809 level crossings, 14,018 stop signs, 35 toll booths, 5,210 signals). The raw graph
has 284,718 nodes and 680,203 edges (355,611 segments). Simplify drops 2,267 self-loops and
removes 21,438 nodes; stage A merges 24,430 nodes into 11,092 clusters; twins pair 11,385 of
15,363 candidates (7,292 pairs), covering 97.3% of one-way motorway and trunk length; stage B
makes 2,047 super-junctions (1,701 interchanges, of which 371 joined, 484 slip-lane clusters and
22 split; 346 roundabouts), takes in 1,641 pocket nodes and carries 6,426 twin pairs;
connectivity keeps 237,581 of 241,212 nodes. The build has 237,581 nodes and 593,555 edges,
118,576 km of road (a two-way road once: residential 38,725, unclassified 29,702, tertiary 27,054,
secondary 9,489, primary 5,566, trunk 3,926, motorway 2,840), 6,390 twin pairs and 30,311
transition rows at 2,043 super-junctions. Enrich (28 s of its 51): 162,210 edges with a parsed
speed; 59,220 identities; 304,513 units (289,042 two-way pairs); 8,575 bridge, 120 tunnel and 20
toll edges; 22 toll booths (7 inside edges, 4 at junctions, 11 off the graph), 4,809 level
crossings (2,244, 319, 2,246); of 14,018 stop signs 1,584 are junction members and 10,606 shape
points, of 5,210 signals 4,114 and 997; 4,504 nodes have a delay; 178,933 are pass-through; 357
roundabout rings at 354 super-junctions (345 roundabouts, 9 interchanges); of 12,875 brand POIs
9,337 snapped, 343 at dead ends and 3,195 too far, 457 of 1,476 brands kept on 5,305 nodes (the
Postal Service 249, Starbucks 209, Chevron 196, Subway 149, McDonald's 136, 76 135). Tiers: T0
keeps 8,732 of 8,891 nodes (98.21%, 7 components), T1 20,386 of 20,472 (99.58%, 9), T2 39,151 of
39,289 (99.65%, 7), T3 75,773 of 75,848 (99.90%, 24), T4 all. QA: twin coverage 0.9727, SCC
retained 0.9849, SCC largest dropped 88, tiers 0.9821, 0.9958, 0.9965, 0.9990, 1.0: passed.
`worst-route aux places --region oregon` (no bbox, so the state polygon's bounds) writes 506
places (OR 426, WA 80) in under 5 s, warning that California's, Idaho's and Nevada's places files
are absent.

**What changed, and why.** Cleanup's connectivity failed at 98.49% under the 0.99 share it had
then. What it drops is 1,255 components, 3,631 nodes and 2,225 km of road: residential and forest
road networks whose only ways in are tracks, service roads or `access=private` ways (the largest,
88 nodes and 132 km south of Cottage Grove, is reached through 65 tracks, 24 service ways and 10
private residential or unclassified ways), and pieces of Washington, California and Idaho that
the extract's edge cuts off (of the 20 largest: 77 nodes by Stella and 19 on Puget Island,
Washington; 38 by Dorris, California; two of 17 in Idaho). So connectivity now fails on a share
below 0.98 or a dropped component of more than 200 nodes (**cleanup**, step 5), which Oregon
passes and Portland passes as before (99.60%, 11). Those constants enter every build's `params`,
and so its `graph_version`: a build saved before them keeps its version until it is rebuilt,
and rebuilding it gives another version with the same arrays, which refuses the routes stored
on the old one (**qa**, Portland). The tiers' rule needed no change. Nothing else in the
pipeline changed for the state; `engine.md`, **On Oregon**, has the trips and what the engine's
tiers became.

**At pipeline version 5** (build ad1ae3f57d81, `graph_version` 089647c53fa2, from the 2026-10-29
extract, md5 3c9bc0db9a4830d004044865fbecd965, which replaced the one above;
`tools/bench_build.py --force` at load average about 10–17): the whole pipeline takes 223 s,
cleanup 123 s (stage A 92 s, stage B 11 s) and 4.11 GB at its peak. Stage B joins 56 pairs of
clusters along a carriageway and makes 2,006 super-junctions (1,662 interchanges, of which 383
joined, 482 slip-lane clusters and 27 split; 344 roundabouts), taking in 1,708 pocket nodes;
connectivity keeps 237,469 of 241,100 nodes (98.49%, 3,631 dropped as before). The build has
237,469 nodes, 593,325 edges, 6,412 twin pairs and 30,502 transition rows at 2,002
super-junctions; the tiers keep 98.21%, 99.58%, 99.65% and 99.90% of their nodes, and QA passes
as before. Lost through movements (**cleanup**, step 4, Pockets) fall from 20 at 19
super-junctions on the version 3 build to 12 at 11, among them northbound I-5 at Woodburn, whose
two halves are now one interchange through which the Newberg Highway, OR 219 on one side and OR
214 on the other, also goes straight across.

## The arrays engine

`--engine arrays`, the default, runs every step from `build` to `save` on NumPy arrays, scipy,
shapely and Numba instead of networkx and OSMnx (`--engine networkx`;
`src/worst_route/pipeline/arrays/`), so that a state and later the whole country fit in memory
and take hours rather than days (`docs/PLAN.md` §7 M5.2). It makes exactly the networkx engine's
build: the same arrays byte for byte, the same geometry, strings and `graph_version`, and the
same cleanup, enrich and tiers reports; the parity tests and `tools/parity.py` (below) hold it
to that. `build` and `cleanup` take `--engine`; `enrich`, `tiers` and `save` follow the engine
the build's cleanup ran (`layout.cleanup_engine`: a build holds one engine's cleanup stage,
since `cleanup` removes the other's, and `enrich` likewise the other enrich stage); `tiers` and
`save` record the cleanup stage among their inputs beside the enrich stage
(`layout.enriched_stages`), and the enrich stage records the cleanup stage's md5, so a cleanup
that changes only what enrich never reads (twins, transitions, records) still reruns them. Its
stages are directories, no pickle among them: `stages/build/`, `stages/cleanup/`,
`stages/enrich/`. Switching a build's engine in place reruns the stages after cleanup, each
engine's own, and the build is the same. On a build the networkx engine made, the default needs
`build` run again before `cleanup`; the steps after it then run again on the arrays engine's
stages, `save` with `--force` (a build is never overwritten), to the same build. A build the
arrays engine made before its stages after cleanup were its own holds `stages/build/` and a
`cleanup.pkl` it handed over (its sidecar's engine `arrays`), which `layout.cleanup_engine`
reads as the networkx engine's: the default `cleanup` then runs again into `stages/cleanup/`,
`enrich` and `tiers` run again, and `save` needs `--force`, to the same build.

**Stages** (`arrays/stage.py`). A stage is a directory under `stages/`, one `.npy` per array
(no pickles) and `tables.json` for the rest (the tag sets, the CRS), with `manifest.json`
written last, naming every file with its md5: the stage exists once the manifest does, and the
manifest's md5 is the stage's in the next step's sidecar. Arrays are read memory-mapped.

**`build`** (`arrays/build.py`, `build_raw_arrays`). One pass over the roads file, as
`build.build_raw_graph` reads it, into typed arrays: node ids and locations as osmium's
fixed-point integers (whose quotient by 10^7 is exactly the `lon`/`lat` osmium gives), the kept
ways' refs, tag sets, one-way rules. Then in NumPy: a way id seen twice read once, a node repeated
back to back read once, junctions (referenced twice, or ending a way), the segments between
consecutive junctions in way order, the projection (pyproj, whose results do not depend on the
batch), each segment's length by GEOS (`shapely.length`, a million segments at a time, since its
sum is not reimplemented), and the nodes in networkx's order (first appearance as a segment's
end). `RawGraph` holds it: per node its OSM id, x, y, lon, lat; per segment its two end nodes,
way, tag set, directions and length; per ref (each segment's OSM nodes) the id and x, y. The
stage `stages/build/` and `stages/build.json` (the same `BuildStats` as networkx's).

**Simplification** (`arrays/simplify.py`). networkx's algorithm itself, in Numba: nodes visited
in ascending OSM id, each contracted when its edges form one through path per direction (two
edges in and two out the two directions of one road) with equal class, tags, `oneway` and
`reversed` and far ends that differ (which is networkx's "would repeat an OSM node" test:
segments share only junctions and a contracted node lies inside one edge or the two directions
of one road). Two raw edges are the two directions of one road when their refs are one sequence
reversed. A merged edge holds the junction its contraction removed, which no raw edge does and
only one other edge can, the one made beside it, its reverse when that contraction was of the
two directions of a road (`partner`); the two stay reverses as they grow, since a contraction at
either of their shared ends takes both. A merge adds an edge to a pool, its length the sum of
its two in networkx's order (so the float sums agree), and the survivors are expanded from
their merge trees into OSM nodes, coordinates and ways at the end. The result's order is networkx's, which every later step's
tie-breaks and edge keys depend on (Portland has 955 groups of parallel edges after stage B):

- the nodes in the raw graph's order;
- each node's edges by neighbour, the neighbours in the order their first edge was inserted,
  each neighbour's edges in insertion order: networkx's adjacency is a dict of dicts;
- an edge's insertion is its raw one (segment k's forward edge, then its backward one) when no
  merge touched it, else the visit of the node whose contraction made it, after every raw edge
  (an edge between two survivors is never removed, so the earliest edge of a neighbour is the
  one that created its entry).

This model predicts networkx's iteration order for all 183,654 of Portland's simplified edges;
the tests compare the arrays' own order with networkx's (a case where two roads to one neighbour
were drawn before and after a road to another pins the by-neighbour grouping).
`arrays/order.py` holds the order (`iteration_order`), which stage A sorts by too.

**Handing over** (`arrays/to_nx.py`). The tests hand the arrays engine's graphs to networkx
(`cleanup.run_cleanup` still hands its cleaned graph over, through `to_nx.cleanup_graph`) to
compare them with the networkx engine's as networkx itself holds them, and the cleaned graph
handed over pickles to the networkx engine's bytes (a pickle of a graph handed over, taken in
memory, can differ: networkx caches in a graph the views asked of it, and the two engines ask
for different ones). What makes it so: the edges added in the order networkx added them
(simplification copies the raw graph, adding its edges in the raw graph's iteration order, which
sets each node's predecessors in the order of their places, then merges), so successors,
predecessors and keys come out as networkx's (0, 1, ... per pair of nodes: an edge between two
nodes that both stay is never removed); each edge's attributes in networkx's order (a raw edge's
build order with `osmids` last, a merged edge's as `simplify._merged` writes it); and the
strings shared as they are once `cleanup` has read its raw graph back from `build.pkl`, since a
pickle writes a string several dicts share once: each way's tag values objects of its own,
shared by the edges along the way, as osmium hands them out, and the keys the raw graph holds
one object each, apart from the same names the later steps write. (The networkx engine run on
its raw graph in memory, without the read back, pickles a few bytes differently, though the
graph is the same; the tests compare against it as the CLI runs it.)

**Stage A** (`arrays/stage_a.py`). The graph `consolidate.stage_a` makes, by the rules OSMnx
applies there and no OSMnx: each node's radius by `consolidate`'s tolerances; the groups of
overlapping buffers (`consolidate.buffer_groups`, GEOS's tests on the same 64-sided polygons, two
that meet at a point only left apart as OSMnx's union leaves them: on Portland the groups are
exactly the union's parts); each group split into the nodes that edges of at most 60 m join
(scipy's connected components, as OSMnx's weakly connected components of the group's subgraph
without its long edges); each cluster of more than one node at `consolidate.cluster_centre`, its members its nodes and the inner OSM nodes of the
edges it absorbs; every other edge re-pointed, a merged cluster's position added at each such
end and its length then GEOS's. The order is the graph OSMnx rebuilds: clusters numbered by the
first place of their nodes (pandas' `factorize` over the simplified graph's nodes), edges added
in the simplified graph's order and so iterated by tail, then by the head's entry, then that
order; the keys of parallel edges follow. `to_nx.stage_a_graph` adds the edges in the order
OSMnx adds them, so its graph is networkx's up to the integer type of the labels (OSMnx keys
some by NumPy integers, of the same value). The tests compare the arrays' own edge order with
networkx's (a cluster whose roads to one neighbour come before and after a road to another pins
it) and include pairs of junctions a few centimetres inside the sum of their radii, where the
polygons' corners decide (some merge and some do not), two buffers meeting at one corner, and a
split group.

**Twins** (`arrays/twins.py`). The pairs `carriageways.find_twins` finds on the same stage A
graph, by its rules and its arithmetic (`carriageways._perpendicular` itself): the same
candidates, taken in the order of their networkx keys `(u, v, key)` so that the STRtree is built
from the same segments in the same order; each candidate's segments its coordinates less a
merged cluster's position at either end (which `stage_a.StageAGraph` marks by the node's kind);
the tree queried `QUERY_CHUNK` segments at a time, which finds the same pairs; edges that share
an OSM way left out, then a road mapped twice (two edges over the same OSM nodes in opposite
directions) left out and counted in `same_road_pairs`, as `find_twins` does (step 3). The table
comes out as arrays, each edge's twins ascending, and `to_nx.stage_a_graph` writes it as
`G.graph["twins"]`, keyed as networkx keys the edges (OSMnx labels some nodes as NumPy integers
there; the values are the same). Python's `sum`, which compensates since 3.12, makes the
report's lengths independent of the order in practice; the order is kept anyway. The tests
include a stub that alone would run beside the other carriageway, a way that runs back beside
itself, a road mapped twice beside a divided road, and the random road graphs of the review that
map a road twice (`tests/pipeline/random_roads.py`, seeds 13, 26 and 48).

**Stage B** (`arrays/stage_b.py`). The graph `consolidate.stage_b` makes from the same stage A
graph and twins, by its rules at pipeline version 5 (**cleanup**, step 4): the ramp and
roundabout components and their joins, those along a carriageway that leaves a cluster and comes
back to it, the slip-lane centres, the pockets along a road by its ref set or its name, the
diameter guard's split, the super-nodes with their records and transitions, the twins carried
across, then the numbering. Wherever the order can decide it is networkx's: the stage A graph's
out-edges in its iteration order, its nodes by their labels, the pocket search's ties to the
order of discovery, the split's multi-source search over a small networkx graph built as
`consolidate._split` builds it, a super-node's position as the mean of its nodes in label order.
The local work runs in Python over the arrays, cluster by cluster: clusters hold a percent or
two of the nodes. The tests run the networkx engine's own stage B cases through both engines,
the cleanup tests' interchanges a region cuts, and one of their own, a twin that is a boundary
edge of its super-junction.

**Connectivity** (`arrays/connectivity.py`). The component `connectivity.largest_component`
keeps, found by scipy's strongly connected components (the partition is unique; the largest
kept, ties to the one holding the smallest node, as there), and its report to the bit: each
dropped component's length summed over its nodes ascending and each node's out-edges by their
keys `(u, v, key)`, its centroid NumPy's mean in that order, the length lost in edge order.
`keep_only` then numbers the kept nodes as `consolidate.relabel` does, which is stage B's
numbering compacted (relabel sorts by keys that keep their order on any subset), leaving out the
edges, twins, super-junctions and transitions that lose an end. `without_no_turn` then drops the
super-junctions left with edges in and out and no allowed pair, round by round with connectivity
after each, as cleanup does (step 5), its sums added into the report in the same order; a round
reruns connectivity over the whole graph, which on arrays costs what the first did (on Portland
0.03 s a round, where networkx's relabel and search take seconds).
`cleanup.run_arrays_cleanup` returns that graph; `to_nx.cleanup_graph` hands it over as networkx
holds it, for the tests: each edge named in its tables by a tuple of its own, as relabel writes
them, and the view of the in-degrees that cleanup's last look for a super-junction without a
turn caches in the graph when it finds one (a single super-node without edges), since a pickle
holds it. The tests include components of one size, a twin whose pair is dropped, the
interchanges a region cuts, and a detached super-junction.

**The cleanup stage** (`arrays/cleaned.py`). `stages/cleanup/` holds the cleaned graph's arrays
and those of the stage A graph its edges and super-junctions name (`b_` and `a_` fields), the
tag sets and the CRS in its tables, and `cleanup.json` beside it as the networkx engine writes
it; the steps after cleanup read it memory-mapped. It is about 1.5 times the pickle (Portland:
72 MB against 47.9 MB) because it carries the whole stage A graph, internal and dropped edges
included; a leaner stage is a later item.

**Enrich** (`arrays/enrich.py`). What `enrich.run_enrich` sets, as arrays on the cleaned graph,
each value as networkx's (**enrich**): the speed, its source and the time per tag set and then
the edge's length, in the same float operations; the identity and flags per tag set; the road
units, the edges grouped by their OSM node run, a run paired with its reverse in key order; the
bearings, through `enrich.bearings`, of the road less the stubs at merged ends (the part holding
an end's OSM node found among the super-junctions' parts); the toll booths, level crossings and
delays placed as `_place_pois` places them (an OSM node's edges by a sorted search, a junction's
lowest-key out-edge the first of its out-edges, one without any off the graph); `pass_through`
from the distinct neighbour pairs; the brand index through `enrich.brand_index`, which both
engines call. `stages/enrich/` holds the values, the identities, the brand index and the md5 of
the cleanup stage's manifest, and `enrich.json` the report.

**Tiers and save** (`arrays/tiers.py`, `save.graph_data_of`, `save.graph_geometry_of`). Each
tier's largest strongly connected part of its roads by scipy's strongly connected components
over the nodes they touch (ties to the smallest node), and the same `tiers.json`; then the build
array for array as `graph_data` makes it, and its geometry as `graph_geometry` does, with the
same refusals (the shape-point rules checked on arrays, and on a breach the edges walked in key
order as `graph_data` walks them, so the first breach raises the same error). `qa` and
`tools/turn_bearings.py` read the super-junction records and the stages' geometry from either
engine's stages.

**Portland** (2026-10-30 extract, on a loaded machine): `build` 2–5 s against 3–7 s, the raw
stage 22 MB against `build.pkl`'s 58 MB (45 bytes per OSM way segment: 493,206 consecutive
node pairs, 604,539 refs); simplification 0.2 s against 3.5 s; stage A 0.6 s against 31–37 s
(OSMnx's union of every buffer alone is about 10 s); twins 0.5–0.9 s against 0.8–1.6 s, the
STRtree's work on both sides; stage B about 0.5–0.8 s against 3–8 s; connectivity 0.03 s
against 2.7 s; the whole of cleanup about 4–8 s against 45–67 s; enrich, tiers and the build
together 2.6 s against 21 s (`tools/parity.py`).

**Checking it.** `tests/pipeline/test_arrays.py` compares the engines on every fixture and on
cases of its own (rings and loops closing on themselves, parallel chains, a way drawn against
its chain, duplicate ways, two one-way ways overlapping in part, two carriageways meeting at one
node (US 322's, which the national build's save refused), merged edges at a node with two in and
two out, a road mapped several times over one run, an adjacency entry's place, small inputs, a
node without a location; pairs near the sum of their radii, buffers meeting at a corner, a split
group, a cluster's edges by neighbour; a stub beside a carriageway, a way beside itself, a road
mapped twice, the review's random roads; the networkx engine's stage B cases (a carriageway that
would come back by turning back among them), the interchanges a region cuts and a twin that is a
boundary edge; components of one size, a twin left without its pair): the raw graph, the
simplified graph and the stage A graph in iteration order, the twins, the stage B graph, the
cleanup graph and report and their pickles, and the saved build (`tests/pipeline/parity.py`'s
`assert_same_graph`, which compares keys, predecessors and attribute order too, and
`assert_same_build`). `test_arrays_enrich.py` compares enrich's values, brand index and report
(and pins the brand snap's tie to the lower node), and `test_arrays_build.py` the tiers, the
build, its geometry and the refusals, on every fixture, the stage B cases and the review's
random roads, each with POIs of every kind on every node of its file. `test_arrays_property.py`
does the cleanup comparison on 40 random street lattices (`slow`), and `test_arrays_data.py`
(`data`) everything on Portland's filtered roads, to the build and, when this pipeline version
saved Portland, its `graph_version`.

```
uv run python tools/parity.py --region portland [--engines networkx,arrays] [--allow-fragmented]
    [--work DIR] [--out tools/out/parity-portland.json]
```

runs each engine's `build`, `cleanup` and the build (enrich, tiers, then the arrays and the
geometry, written by `save`'s own functions as `pipeline save` writes them) on the region's
filtered roads, each step in its own process on its own engine's stages, into a temporary
directory (nothing under `WORST_ROUTE_DATA` is written), prints each step's seconds and peak
memory, compares the cleanup, enrich and tiers reports and the builds array by array, the
geometry's too, and exits 1 when they differ. On Portland (pipeline version 7): the same
graph_version, the same reports, no array differing. On Oregon (the 2026-10-29 snapshot at
pipeline version 7, every step on arrays, on a loaded machine): the same graph_version, reports,
strings and arrays, the geometry's included, Ponderosa Lane's copies one unit on both; `build` 7
s against 12 s, cleanup 5 s against 91 s (stage A 0.8 s of it against 64 s, connectivity 0.05 s
against 4 s), enrich, tiers and the build 6.5 s against 23 s. No super-junction is left without
a turn there; a round dropping one (tables emptied by hand) takes 0.08 s on arrays, as long as a
connectivity pass.

## Regions of several states

M5.3 (`docs/PLAN.md` §7). A region's `states` (`config.Region`) are Geofabrik slugs; `download`
fetches each, and `filter` reads them in that order into one roads file and one POI table, the
rest of the pipeline unchanged. Geofabrik cuts each extract along a polygon that reaches a little
past the state line and keeps every way that touches it complete, with all its nodes
(https://download.geofabrik.de/technical.html), so a road crossing the line is in both
extracts, whole and under the same ids; a road that continues it beyond the line is in the other
extract only, and a single extract ends there, a dead end. The merge reads that way once (from
the first extract, in the region's order) and every node once (its newest version), so the
continuation meets it at the shared node and the road is one road with one node sequence;
`tools/border_ends.py` counts a region's dead ends beyond its states and says which a merge of
more states joins up (`docs/reference/testing-tools.md`; **Oregon and Washington** has the
numbers).

The merge streams (`filter.merge_parts`), so its memory grows with the number of extracts, not
with their nodes: 49 readers held open at once take 0.5–0.7 GB. `osmium.MergeInputReader`, which
merged them before, held every part in memory (the national T1 filter, merging 49 files into a
196 MB roads file, peaked at 2.32 GB with it). The all-roads country's roads file, 1,917.6 MB,
25.6 times Oregon and Washington's 74.9 MB, was merged from its 49 parts in a stream, the whole
filter peaking at 4.19 GB of resident set (**The all-roads national build**).

Extracts of one day are cut from one snapshot. Extracts of different days still merge into a
reference-complete file, since each per-extract file is complete, but a border way edited between
the two snapshots can come out as the earlier extract's whole way beside the later one's
pieces; so `filter` warns when the dates differ, and a national region is downloaded in one
sitting (`download --force`).

| Region | States | Classes | CRS |
|---|---|---|---|
| `oregon-washington` | oregon, washington | all | EPSG:32610 |
| `oregon-washington-t1` | oregon, washington | T1 | EPSG:32610 |
| `us-t1` | the 48 contiguous states and DC, in `config.STATES`' order | T1 | EPSG:5070 |
| `us-t3` | the same | T3 | EPSG:5070 |
| `us` | the same | all | EPSG:5070 |
| `west-coast` | california, oregon, washington | all | EPSG:5070 |
| each of the 48 contiguous states and DC, by its slug (`california`, `district-of-columbia`, …) | that state | all | EPSG:5070 (`oregon`: EPSG:32610) |

**The class ceiling.** A region's `classes` (`T0`–`T3`) keeps only that tier's road classes from
`filter` on, so the national T1 build never reads a residential street into memory; the steps after
`build` are unchanged, and on such a build the tiers above the ceiling hold the same roads as it.
`us-t3` (secondary and tertiary roads too) is the national build the server serves (the owner,
2026-10-31, with Portland and Oregon as the development regions); `us-t1` stays the measured
reference. `us` is the all-roads national region, the master graph M5.3's decision weighed, defined
but not built here (**The all-roads national build**), and `west-coast` (about eight times Oregon)
the scale between a state and the country at which its memory is measured. The regions of one state
each, generated from `STATES` (Oregon's keeping its own UTM zone and its build), are regions one may
build; built once, they measure the array pipeline at state scale (**The state builds**).
Alaska and Hawaii are left out of `us-t1`: no road reaches them, so connectivity would drop them,
and CONUS Albers does not cover them; they would be regions of their own.

**Oregon and Washington.** The two-state builds, M5.3's proof, from Geofabrik's extracts of
2026-10-29 (both data of 2026-10-29T21:21:26Z: Oregon md5 3c9bc0db9a4830d004044865fbecd965,
Washington 37c7424efddc9f4cbe0c77f28422929c), at pipeline version 5, measured with
`tools/bench_build.py --allow-fragmented` on the dev machine at load averages of 7–20.

- **Stitching.** Oregon's filtered roads alone have 61,248 dead ends, 394 of them beyond Oregon's
  polygon: Idaho 125, Washington 118, California 95, Nevada 17, and 39 outside every state (the
  water, Canada). Merged with Washington's, 66 of the 118 in Washington continue into its roads,
  and the other 52 still end: they are dead ends of Washington's own network that Oregon's
  extract held because its polygon reaches past the line; none is absent. The two-state roads
  have 1,058 dead ends beyond the two states (677 outside every state, Idaho 269, California 95,
  Nevada 17) and none at the line between them. The filter read 2,350,247 highway ways and kept
  537,773, of which 626 were in both extracts and read once.
- **All roads** (`oregon-washington`, build 6fdd57c305fe, `graph_version` bc958387324a):

  | Step | Wall | Peak RSS | Writes |
  |---|---|---|---|
  | filter | 101 s | 1.78 GB | roads 74.9 MB, POIs 2.9 MB |
  | build | 64 s | 2.95 GB | `build.pkl` 618 MB: 772,694 nodes, 1,834,596 edges |
  | cleanup | 811 s (674 s on a quieter first pass) | 4.49–5.00 GB | simplify 41 s, stage A 413 s (425 s), twins 32 s, stage B 120 s, connectivity 162 s (17 s) |
  | enrich | 62–86 s | 3.94–5.29 GB | |
  | tiers | 27–30 s | 4.25 GB | |
  | save | 28 s | 4.33 GB | the build, 441.8 MB (`arrays/` 416 MB) |
  | qa | 17 s | 3.72 GB | |

  The build has 646,338 nodes and 1,604,503 edges, 2.7 times Oregon's, and 269,706 km of road;
  17,446 twin pairs (coverage 0.979) and 101,328 transitions at 6,262 super-junctions.
  Connectivity keeps 98.13% of the nodes, dropping 2,854 components, and one super-junction with
  no allowed turn: I-90 at State Line (47.6968, −117.0417), whose interior lies in Idaho; without
  that rule (**cleanup**, step 5) `save` refused the build. Six dropped components hold more than
  200 nodes, ferry-only islands and an exclave: San Juan Island 1,154, Vashon–Maury 830, Lopez 560,
  Point Roberts 448 (reached only through Canada), Orcas 415, Anderson Island 406. So QA fails
  "SCC largest dropped" alone (twin coverage 0.9794, SCC retained 0.9813, tiers 0.9656–1.0); the
  200-node rule stays, measurement builds of several states run with `--allow-fragmented` and
  list what they drop, and the thresholds are revisited at M5.3's decision gate. The rule's
  second round of connectivity (a relabel and a component search of the whole networkx graph)
  costs 162 s against the first round's 17 s, which the array pipeline's connectivity is to
  remove. Brands: 797 of 2,202 kept, on 13,771 nodes.
- **T1** (`oregon-washington-t1`, build 5bd8bf37c280), in 101 s at a peak of 1.63 GB: filter 35
  s (1.63 GB), build 4.5 s, cleanup 50 s (stage A 24 s, 0.87 GB), enrich 6 s, tiers 2.5 s, save 2
  s, qa 1.6 s. The filter keeps 59,252 ways and drops 478,860 above T1 (their sum counts a border
  way above the ceiling once per extract, 339 more than run 1's kept ways here); the raw graph has
  56,551 nodes and 87,170 edges, the build 24,158 nodes and 40,778 edges (3.7% of the all-roads
  build's nodes), 25,132 km and 17.0 MB, about 417 B an edge: T1 roads are long, with many shape
  points. Connectivity drops three super-junctions with no allowed turn, interchanges whose cross
  road is above the ceiling, and QA passes every threshold (largest dropped component 33 nodes).
  Brands: 432 of 2,202 kept, on 3,952 nodes.

**The national T1 build** (`us-t1`, build c4a85b3d36d0 at `graph_version` 8846d0d4ab7a,
pipeline version 7, format 4), from the 49 extracts of 2026-10-29 (11.87 GB, or 11.05 GiB, one
snapshot), on the dev machine at load averages of 20–26. The filtered roads went through `build`
and `cleanup` on both engines; networkx's cleanup did not finish (at pipeline version 6, build
c67d3da6cb6a, the same roads), arrays' took three minutes, and the build was written from the
arrays run, with enrich, tiers and save still on networkx. It was made with `--allow-fragmented`,
as every measurement build of several states is (its `meta.params` record it for cleanup and
tiers, so it enters the `graph_version`), though its connectivity and tiers pass the thresholds
anyway:

```
uv run python tools/bench_build.py --region us-t1 --allow-fragmented
uv run python tools/bench_build.py --region us-t1 --steps build,cleanup,enrich,tiers,save,qa \
    --force --allow-fragmented
```

The second command reran it from `build` on the first's filtered roads, once the arrays engine
was the default. The build predates the class ceiling's entry in a build's params (`save`,
`params.classes`), so its `meta.params` do not say T1 and a rebuild gets another
`graph_version`.

| Step | networkx | arrays |
|---|---|---|
| filter | 743 s (1,039 s CPU), 2.32 GB: roads 196.0 MB, POIs 61.6 MB | (the same step) |
| build | 290 s (209 s CPU), 3.85 GB, `build.pkl` 998 MB | 50 s (50 s CPU), 2.03 GB, `stages/build/` 555 MB |
| cleanup | did not finish: stopped after 7,372 s (2.05 h; 4,600 s CPU), 3.67 GB, in pure-Python code | 198 s (178 s CPU), 3.69 GB: simplify 1.7 s, stage A 11.9 s, twins 56 s, stage B 51 s, connectivity 1.2 s, the handover to networkx for the steps after it 46 s |
| enrich | | 173 s, 4.73 GB (networkx) |
| tiers | | 61 s, 5.22 GB (networkx) |
| save | | 46 s, 4.95 GB: the build, 178.1 MB of arrays and strings plus 23.1 MB of geometry, 201.2 MB in all |
| qa | | 65 s, 4.41 GB: every threshold passes |

- **The filter** reads 51,971,904 highway ways, keeps 1,864,378 at T1, excludes 37,674,756 and
  drops 12,425,597 above T1; 7,173 were in two extracts. Its POI table has 2,593,833 rows (712,781
  brand, 1,059,481 stop signs, 546,043 signals, 271,763 level crossings, 3,765 toll booths). The
  ways kept and above T1, 14,289,975, are what an all-roads national region would keep: 70.6
  times Oregon's 202,420 and 26.6 times Oregon and Washington's 537,773.
- **The graph.** The raw graph has 1,845,797 nodes and 2,613,372 edges (2,082,086 segments).
  Simplify removes 354,005 nodes; stage A makes 124,355 clusters of 295,275 nodes; twins pair
  577,534 couples (motorway and trunk coverage 0.9847; no copy of a road paired with itself,
  `same_road_pairs` 0); stage B makes 140,010 super-junctions (138,407 interchanges, 1,603
  roundabouts); connectivity keeps 887,737 of 893,286 nodes (99.38%), dropping 4,634 components,
  the largest of 69 nodes, and 33 super-junctions with no allowed turn. The build has 887,737 nodes
  and 1,324,013 edges, 822,866 km of road (motorway 247,194, trunk 210,731, primary 352,628, and
  12,313 of links: motorway_link 11,894, trunk_link 313, primary_link 106),
  1,033,413 road units (290,547 two-way pairs), 534,690 twin pairs and 514,360 transitions at
  139,166 super-junctions, about 134 B an edge of arrays and strings. Tiers: T0 keeps 99.18% of its nodes (2,607
  components), T1 to T4 all. Brands: 185,087 of the 712,781 brand POIs snap, and 2,431 of 12,036
  brands are kept, on 107,054 nodes.
- **Dead ends.** The national roads have 95,453; 429 lie beyond the 49 states, every one outside
  every state (Canada, Mexico, the sea), and none in a state outside the region.
- **After cleanup**, with build and cleanup on arrays, the handover of the cleaned graph to
  networkx (46–53 s) and enrich and tiers on networkx (234–389 s over two runs) were most of the
  time after the filter; they have run on arrays since (**The arrays engine**).
- **A road mapped twice** stopped the version 6 run at `save`: OSM node 106661013 (40.9309,
  −78.2796), on US 322 in Clearfield County, Pennsylvania, where two pairs of one-way trunk ways
  (425447408 and 425447409, 1534168462 and 1534168463) each close a loop through it, lay inside
  one edge and inside another road (`ShapePointError`). Pipeline version 7 keeps such a node, and
  the build saves.

**The all-roads national build** (`us`, M5.3's (a)), from the same 49 extracts at pipeline
version 7, on the dev machine (25.8 GB of memory, 24 GiB, and 10 cores) at load averages of 5–9,
under the watchdog and the disk check (the watchdog's flag, `--max-rss-gb` then, is `--max-mem-gb`
since). Sizes here are decimal, as the bench tool prints them; `footprint`(1) and `sysctl
vm.swapusage` print binary units, so `footprint`'s "27 GB" is 27 GiB, 29.0 GB:

```
uv run python tools/bench_build.py --region us --allow-fragmented --max-rss-gb 18 --min-free-gb 15
```

It did not get past `build`:

| Step | Wall | Memory | Writes |
|---|---|---|---|
| filter | 1,372 s (1,926 s CPU) | 4.19 GB of resident set | roads 1,917.6 MB, POIs 61.6 MB |
| build | stopped by hand after 1,005 s (410 s user, 485 s system) | physical footprint about 29 GB (21.5 GB of large allocations and 4.4–4.8 GB of small, read from `footprint` by hand) | nothing: the stage is written at the step's end |

- **The filter** keeps 14,243,093 of the 51,971,904 highway ways and reads 54,055 border ways
  once; the T1 run counted 14,289,975 kept or above its ceiling, the 46,882 more border copies
  read once now making the difference. Its 2,593,833 POIs are the T1 build's.
- **The build's stop.** The watchdog read the step's resident set from `ps`: about 2.2–2.6 GB in its
  last minute (2.43 GB at the stop, read by hand), its `ru_maxrss` 4.68 GB. On macOS the resident
  set leaves out the pages the system has compressed or swapped out, and `footprint` read about
  29 GB from 15 minutes in to the stop at 16.75 minutes, while the machine's swap grew from 8.9 GB
  at the step's start to 18.1 GB at its peak, of 19.3 GB (read by hand), and the step spent more
  time in the kernel than in its own code. So the 18 GB line never fired; the step was stopped by
  hand, with SIGTERM, and left nothing behind. The watchdog reads the footprint since
  (`docs/reference/testing-tools.md`).
- **West-coast** (California, Oregon and Washington, build ea89e860206f at `graph_version`
  c6ea6d2e721c, run twice to the same build), the scale between a state and the country at which
  the memory is measured: 1,752,551 ways kept, 1,930,839 nodes and 4,797,434 edges, 444.9 MB of
  arrays and strings and 63.2 MB of geometry; QA fails "SCC largest dropped" alone (San Juan
  Island's 1,154 nodes, **Oregon and Washington**). The peak memory is the physical footprint the
  bench tool reads, beside its resident set and the heap tracemalloc traces (a probe run of
  each step, four to twenty times slower under it):

  | Step | Wall | Peak memory | Peak RSS | Heap | Writes |
  |---|---|---|---|---|---|
  | filter | 162 s | 6.39 GB | 3.79 GB | | roads 205.6 MB, POIs 12.3 MB |
  | build | 47 s | 4.86 GB | 4.32 GB | 3.42 GB | `stages/build/` 733.2 MB |
  | cleanup | 56 s | 9.76 GB | 4.93 GB | 8.99 GB | `stages/cleanup/` 2,300.6 MB |
  | enrich | 27 s | 8.19 GB | 6.25 GB | 3.59 GB | `stages/enrich/` 322.0 MB |
  | tiers | 5.5 s | 1.07 GB | 1.30 GB | 0.72 GB | `tiers.json` 37.5 MB |
  | save | 27 s | 8.82 GB | 5.60 GB | 4.35 GB | the build, 508.1 MB |
  | qa | 13 s | 1.17 GB | 1.44 GB | | |

  The resident set reads above the footprint where a step maps its input stage (clean file pages
  the system may drop, which the footprint leaves out) and below it where pages were compressed.
  The heap leaves out what GEOS allocates for shapely's geometries: the 4,797,434 lines of the
  cleaned graph's 36.4 M coordinates take 2.89 GB of footprint, of which tracemalloc sees 0.31
  GB, and `enrich` builds two such sets, `save` one and its simplification.
- **What holds the peaks**, at west-coast (tracemalloc's largest holders at each step's peak; refs
  20.4 M, the roads file's nodes 17.7 M, segments 3.04 M, stage A edges 5.01 M and coordinates
  37.9 M, stage B coordinates 36.4 M):
  - `build`: reading the file, `array`s of the file's node ids (int64), their fixed-point lon and
    lat (int32) and the ways' refs (int64), 0.44 GB, and the ways' tag tuples and tagsets, 0.15
    GB; then whole-file NumPy over the refs: `flat` and `owner` after `[keep]` (int64, 0.31 GB),
    `np.unique` of the refs with an inverse and counts (0.29 GB of sorting), and `at`,
    `ref_id`, `position`, `lon` and `lat` (int64 and float64, 0.16 GB each) beside pyproj's copies
    of `lon` and `lat` (0.33 GB) and the argsort of the node ids (0.14 GB).
  - `cleanup`, at stage B's end with stage A's arrays alive: Python lists made by `.tolist()` in
    stage B's context (eight of 5.0 M items, 1.44 GB), `np.split` of the members into 4.1 M arrays
    (0.62 GB) and the members' lists (0.20 GB); the coordinates three times over, stage A's
    (float64, 0.61 GB), stage B's (0.59 GB) and its gathered copy for the stage (0.59 GB), with
    their `take` and `dst` indices (int64, 0.30 GB each) and stage A's OSM nodes (0.30 GB); and
    1.0 GB of gathers from the memory-mapped build stage.
  - `enrich`: the bearings, from two full sets of lines, every edge's and every edge's road
    without its junction stubs (9.6 M geometries, about 4.5 GB of GEOS memory by estimate, the
    step's footprint less its heap, beside their 0.61 GB of Python objects, and their stacked
    coordinates, 0.57 GB); 0.87 GB of gathers from the memory-mapped stage.
  - `save`: the geometry, every edge's line and its simplification (GEOS) and their points
    (0.49 GB), then `geometry.encode`'s per-step arrays (1.55 GB).
- **The projection.** The country is 8.1 times west-coast by the ways kept (9.3 times by the roads
  file's bytes), and every holder above grows with the graph: the footprints scale to about 39–45 GB
  at `build`, 79–91 GB at `cleanup`, 66–76 GB at `enrich`, 71–82 GB at `save` and 9–11 GB at `tiers`
  and `qa`, against the machine's 25.8 GB. The national build's 29 GB was likely short of its peak:
  its heap alone scales to 28–32 GB there. A slope fitted to the resident sets of Oregon, Oregon and
  Washington and west-coast had put `build` at 16–21 GB, and undercounted for the reason above.
- **Leaner steps.** What each change would save at west-coast (about 8–9 times as much
  nationally): at `build`, projecting and measuring the refs in chunks into `ref_x` and `ref_y`
  (dropping `position`, `lon`, `lat` and pyproj's copies) 0.65–0.8 GB, letting go of `refs`,
  `owner` and the first `flat` once used 0.3–0.5 GB, int32 indices 0.3 GB, junctions from one
  argsort instead of `np.unique` with an inverse and counts 0.3 GB; at `cleanup`, arrays instead
  of stage B's lists and split 2.2 GB, one copy of stage B's coordinates 1.2 GB, stage A's
  coordinates out of memory before stage B 0.6–0.9 GB; at `enrich`, the bearings from each road's
  end points as arrays, with no lines, about 6 GB; at `save`, the geometry encoded in chunks of
  edges, most of the 6 GB or so it holds. Even so `cleanup` would hold about 5.5 GB at
  west-coast, 45–50 GB nationally: the all-roads country needs leaner steps before it can be
  built on this machine, and `cleanup` a way of working through the graph in parts.

**The national T3 build** (`us-t3`, build 281a455e3d15 at `graph_version` ace6eb86b9c7, pipeline
version 7, format 4), the national build the server serves (the owner's design;
`docs/project_overview.md` §3.2), from the same 49 extracts, on the dev machine on 2026-10-31 from
14:56:05 to 15:17:34 CDT at load averages of 3.7–4.3 (the gate ran beside the filter for about five
minutes):

```
uv run python tools/bench_build.py --region us-t3 --allow-fragmented --max-mem-gb 18 \
    --min-free-gb 15
```

| Step | Wall | CPU (user + sys) | Peak memory | Peak RSS | Writes |
|---|---|---|---|---|---|
| filter | 695 s | 1,029 s | 4.68 GB | 2.67 GB | roads 570.9 MB, POIs 61.6 MB |
| build | 127 s | 128 s | 9.68 GB | 3.83 GB | `stages/build/` 1,590.6 MB |
| cleanup | 251 s | 220 s | 16.84 GB | 4.35 GB | `stages/cleanup/` 4,146.5 MB |
| enrich | 76 s | 59 s | 11.72 GB | 5.88 GB | `stages/enrich/` 299.9 MB |
| tiers | 9 s | 9 s | 1.89 GB | 2.13 GB | `tiers.json` 89.5 MB |
| save | 67 s | 60 s | 12.08 GB | 6.47 GB | the build, 485.2 MB of arrays and strings and 101.4 MB of geometry (586.6 MB) |
| qa | 64 s | 63 s | 4.02 GB | 4.30 GB | `qa/` 431.0 MB; every threshold passes |
| all | 1,288 s (21.5 min) | | 16.84 GB | 6.47 GB | |

- **The filter** keeps 4,194,946 of the 51,971,904 highway ways at T3 (motorway to tertiary, with
  their links), drops 10,085,919 above it and excludes 37,674,756; 16,283 were in two extracts. Its
  POIs are the other national builds', 2,593,833.
- **The graph.** The raw graph has 4,054,628 nodes and 7,378,939 edges (4,988,487 segments).
  Simplify removes 728,305 nodes; stage A makes 333,516 clusters of 814,819 nodes; twins pair
  1,389,109 of 1,530,624 candidate edges (motorway and trunk coverage 0.984); stage B makes 159,474
  super-junctions (149,144 interchanges, 10,330 roundabouts); connectivity keeps 2,154,537 of
  2,165,386 nodes (99.50%), dropping 6,880 components, the largest of 99 nodes, and 12
  super-junctions with no allowed turn. The build has 2,154,537 nodes and 4,412,885 edges, 823,485
  twin pairs and 1,539,304 transitions at 159,063 super-junctions, about 110 B an edge of arrays and
  strings: 2.4 times the T1 build's nodes and 3.3 times its edges. Brands: 328,589 of the 712,781
  brand POIs snap, and 3,194 of 12,036 brands are kept, on 192,172 nodes.
- **QA** passes every threshold: twin coverage 0.9845, SCC retained 0.9950, largest dropped
  component 99 nodes; the tiers keep T0 99.37% of their nodes (1,855 components), T1 99.81% (1,144),
  T2 99.91% (912) and T3 and T4 all.
- **Memory.** Its cleanup came within 1.2 GB of the watchdog's 18 GB, the build's other steps at
  9.7–12.1 GB: a national build with more classes than T3 needs the leaner steps (**The all-roads
  national build**).
- **Towns.** Of the places index's 31,540 towns (`docs/reference/presentation.md`, **Places**),
  31,259 (99.1%; incorporated places 99.8%) have a node a trip can start from within 5 km of a point
  inside them, the snap moving a median 363 m (p90 1,246 m), where the T1 build reaches 81.8% (a
  median 782 m, p90 3,089 m); the lowest states are Montana (87%), Nevada (96%), New Mexico and
  Arizona (97%), and Massachusetts, Oklahoma, South Dakota and Idaho (98%).
- **Trips** at the automatic tier (T1 beyond 800 km, a fifth of the trip as leash; seed 1,
  `--budget 60 --max-iterations 1000000`, `CsrBackend`), both passing the validator and `worst-route
  validate` (1.2–2.1 s):

  | Trip | Leash | Baseline | Worst | Ratio | Attempts | Peak RSS |
  |---|---|---|---|---|---|---|
  | New York (40.7128, −74.0060) → Los Angeles (34.0522, −118.2437) | 782.6 km | 4,430.75 km | 26,515.72 km | 5.98× | 7,327 (90 accepted), 60 s | 1.71 GB |
  | Pioneer Courthouse Square (45.5189, −122.6793) → Chicago (41.8781, −87.6298) | 565.2 km | 3,362.50 km | 31,807.84 km | 9.46× | 24,893 (159), 60 s | 1.44 GB |

**The state builds** (2026-10-31), a one-off measurement of the array pipeline at state scale
and an inventory of the regions one may build, not served (the owner's design is the national
build at T3, `us-t3`; `docs/project_overview.md` §3.2): each of the 48 contiguous states and DC as
a region of its own (above), from its own extract of 2026-10-29 at pipeline version 7, all roads,
built, checked, pruned and given its places index by `tools/build_regions.py --states`
(`docs/reference/testing-tools.md`) on the dev machine at load averages of 3–9, smallest extract
first, from 13:20:47 to 14:07:31 CDT: 46 min 44 s for all 49, of which the steps took 2,751 s.

```
uv run python tools/build_regions.py --states
```

No step came near the watchdog's 18 GB: the largest footprints were Texas's 8.51 GB and California's
7.65 GB, then Florida's 5.18 GB, and every `filter` takes about 2.1 GB, even DC's. Together the
builds hold 18.07 M nodes, 45.8 M edges and 7.95 M km of road in 4,208 MB of arrays and strings and
586 MB of geometry (4.79 GB), about the all-roads national build's estimated size, a road crossing a
state line held by both states. Oregon's is its build ea3b18096e8b (`graph_version` dd86410e515c):
the batch found it written from a measurement run with `--allow-fragmented`, and saved again without
the flag it has the main tree's build's `graph_version`; its row's time and peak are those of that
rerun, whose other steps were up to date (**Oregon** has its own).

| Region | Build | Nodes | Edges | Road km | Size | Time | Peak memory | QA |
|---|---|---|---|---|---|---|---|---|
| district-of-columbia | 73851ece91f8 | 16,707 | 47,729 | 2,651 | 4.7 MB | 11 s | 2.12 GB | failed: twin coverage 0.9268, T0 retained 0.935673 (fragmented) |
| delaware | 9b72a6b216cd | 48,771 | 120,173 | 14,472 | 12.9 MB | 11 s | 2.12 GB | passed |
| vermont | b1281748d02d | 53,743 | 123,505 | 28,007 | 14.8 MB | 14 s | 2.17 GB | passed |
| south-dakota | 6dac884480b3 | 119,349 | 330,522 | 130,837 | 32.8 MB | 17 s | 2.15 GB | passed |
| rhode-island | aca5c21ae0ff | 45,463 | 116,630 | 10,632 | 12.1 MB | 10 s | 2.15 GB | passed |
| new-hampshire | ae95d1d4efd8 | 79,873 | 189,418 | 31,094 | 21.3 MB | 18 s | 2.16 GB | passed |
| maine | ef0914ed661e | 116,341 | 266,333 | 57,954 | 30.3 MB | 22 s | 2.21 GB | fragmented: retained 0.964, largest 351 nodes, pruned |
| mississippi | 9b22eb948fc5 | 269,463 | 654,216 | 149,665 | 71.6 MB | 34 s | 2.21 GB | passed |
| wyoming | 6fe981ad059c | 77,481 | 199,857 | 70,274 | 22.6 MB | 18 s | 2.17 GB | fragmented: retained 0.966, pruned |
| west-virginia | a20d4ff35324 | 165,681 | 386,830 | 80,100 | 45.8 MB | 32 s | 2.23 GB | failed: SCC retained 0.97282, T0 retained 0.908609 (fragmented) |
| arkansas | 101db712188c | 298,843 | 744,655 | 162,768 | 80.4 MB | 39 s | 2.21 GB | passed |
| montana | eca6b87425c3 | 152,451 | 392,091 | 143,207 | 44.3 MB | 29 s | 2.20 GB | fragmented: retained 0.977, pruned |
| nebraska | e39eb036442a | 199,026 | 579,321 | 166,788 | 55.5 MB | 30 s | 2.19 GB | passed |
| kansas | c8fcff2c93f6 | 335,853 | 941,952 | 243,813 | 90.2 MB | 37 s | 2.19 GB | passed |
| nevada | d37ed76e9101 | 128,658 | 315,086 | 62,448 | 34.8 MB | 31 s | 2.21 GB | fragmented: retained 0.978, pruned |
| north-dakota | 22a6418e46cd | 88,030 | 253,013 | 115,892 | 24.5 MB | 19 s | 2.23 GB | passed |
| idaho | 8b892f2776ac | 177,541 | 452,213 | 112,225 | 52.6 MB | 37 s | 2.23 GB | fragmented: retained 0.979, pruned |
| iowa | 43ad0da862b0 | 297,339 | 836,231 | 198,318 | 80.1 MB | 40 s | 2.28 GB | fragmented: largest 216 nodes, pruned |
| new-mexico | feb7ef8baa43 | 226,583 | 583,309 | 166,338 | 64.0 MB | 38 s | 2.23 GB | fragmented: retained 0.967, largest 269 nodes, pruned |
| louisiana | 082d6a13f940 | 322,629 | 796,825 | 140,083 | 83.2 MB | 37 s | 2.28 GB | passed |
| alabama | 75e7cba98161 | 433,968 | 1,058,267 | 183,976 | 114.3 MB | 55 s | 2.83 GB | passed |
| kentucky | 73a7cd003232 | 322,631 | 759,598 | 147,176 | 87.1 MB | 58 s | 2.68 GB | passed |
| new-jersey | 51c8f806281d | 291,009 | 749,423 | 72,990 | 76.2 MB | 44 s | 2.49 GB | passed |
| south-carolina | 16f47ad29f14 | 360,432 | 888,924 | 140,950 | 94.7 MB | 52 s | 2.26 GB | passed |
| oklahoma | a56c47a39f88 | 421,722 | 1,087,132 | 221,228 | 108.6 MB | 45 s | 2.26 GB | passed |
| utah | 9f7156d58174 | 168,027 | 421,590 | 76,560 | 45.4 MB | 27 s | 2.23 GB | passed |
| tennessee | ebd6e403c532 | 423,312 | 1,027,089 | 181,062 | 113.1 MB | 54 s | 2.35 GB | passed |
| missouri | 2a4adea0aed3 | 536,985 | 1,359,034 | 255,024 | 143.6 MB | 68 s | 3.15 GB | passed |
| indiana | b8494f8699e3 | 463,364 | 1,245,563 | 182,611 | 122.7 MB | 54 s | 2.66 GB | passed |
| maryland | 4f1d71aef9eb | 247,607 | 596,358 | 67,616 | 64.1 MB | 40 s | 2.22 GB | passed |
| connecticut | 47fcaf17991f | 126,221 | 310,592 | 39,478 | 33.4 MB | 25 s | 2.21 GB | passed |
| oregon | ea3b18096e8b | 237,469 | 593,325 | 118,557 | 66.6 MB | 9 s | 1.61 GB | passed |
| minnesota | 20588e2c58f2 | 373,552 | 999,917 | 237,517 | 101.1 MB | 53 s | 2.50 GB | passed |
| wisconsin | 5a5bc447f1f3 | 396,445 | 1,026,744 | 204,912 | 106.3 MB | 56 s | 2.43 GB | passed |
| arizona | 2bc5b0c3fa73 | 421,036 | 1,045,757 | 143,829 | 108.5 MB | 64 s | 2.54 GB | fragmented: retained 0.980, largest 577 nodes, pruned |
| massachusetts | 72d7a2c74db3 | 260,272 | 639,782 | 65,097 | 67.2 MB | 57 s | 2.45 GB | fragmented: retained 0.973, largest 3,587 nodes, pruned |
| michigan | a6aa0863ba5b | 508,907 | 1,340,763 | 224,684 | 134.3 MB | 81 s | 2.95 GB | fragmented: largest 338 nodes, pruned |
| ohio | 73624416c9aa | 574,119 | 1,505,672 | 221,178 | 152.6 MB | 85 s | 3.28 GB | passed |
| pennsylvania | 672c14071c2b | 652,792 | 1,693,131 | 237,831 | 174.9 MB | 92 s | 3.82 GB | passed |
| georgia | 4aa26981a165 | 583,656 | 1,397,883 | 238,543 | 153.0 MB | 79 s | 3.41 GB | passed |
| illinois | 4bc42b30b07a | 690,754 | 1,858,899 | 264,375 | 180.6 MB | 79 s | 3.57 GB | passed |
| washington | 23c411d51103 | 409,027 | 1,011,499 | 151,345 | 108.5 MB | 70 s | 2.55 GB | failed: SCC retained 0.977942, SCC largest dropped 1154, T0 retained 0.946596 (fragmented) |
| colorado | f96dd61bf196 | 355,376 | 910,181 | 172,858 | 96.8 MB | 57 s | 2.68 GB | passed |
| virginia | 1b3d950d8f18 | 446,988 | 1,039,460 | 166,628 | 118.3 MB | 85 s | 3.48 GB | passed |
| north-carolina | 41e7dd574157 | 669,779 | 1,570,548 | 231,956 | 173.2 MB | 97 s | 3.89 GB | passed |
| new-york | 262fe8765147 | 527,491 | 1,340,399 | 211,148 | 140.7 MB | 93 s | 3.18 GB | fragmented: largest 402 nodes, pruned |
| florida | e1d010b6d5ef | 940,444 | 2,362,505 | 266,767 | 242.3 MB | 147 s | 5.18 GB | fragmented: largest 333 nodes, pruned |
| texas | 4c57ba80f564 | 1,723,758 | 4,460,083 | 748,441 | 453.1 MB | 268 s | 8.51 GB | fragmented: largest 353 nodes, pruned |
| california | f2a5e6a17dbe | 1,284,788 | 3,193,540 | 386,224 | 334.3 MB | 235 s | 7.65 GB | fragmented: largest 612 nodes, pruned |

- **QA.** 32 builds pass. 17 were too fragmented for `cleanup` or `tiers` and were run again with
  `--allow-fragmented`. 14 of them fail only the connectivity rule's thresholds (the share kept
  below 98%, a dropped component above 200 nodes) and are pruned like those that pass. Three fail
  other rules too and keep their stages (1.13 GB): DC (twin coverage 0.927, T0 retained 0.936),
  West Virginia (T0 retained 0.909) and Washington (T0 retained 0.947).
- **Disk.** 88.7 GB were free before the batch (the bench tool's `free_gb`) and 68.7 GB after it;
  the pruning pass (the batch run again, 40 s, every region done before and offered to `prune`)
  gave 12.9 GB back, leaving 81.6 GB (the last three read with `df -g` in GiB, 64, 12 and 76, and
  given here in GB).
- **Places.** Every state's index is written (`aux places`: the towns within the state's bounding
  box, its neighbours' included), with no state missing its Census file.
- **Plans** on three of the builds (seed 1, 30 s, `CsrBackend`), each passing the validator and
  `worst-route validate`:

  | Build | Trip | Baseline | Worst | Ratio | Search |
  |---|---|---|---|---|---|
  | `rhode-island` aca5c21ae0ff | Providence (41.8240, −71.4128) → Newport (41.4901, −71.3128) | 48.13 km | 598.06 km | 12.42× | 5,000 attempts in 2.9 s |
  | `washington` 23c411d51103, an island state | Seattle (47.6062, −122.3321) → Olympia (47.0379, −122.9007) | 98.07 km | 1,341.73 km | 13.68× | 5,000 attempts in 13.4 s |
  | `california` f2a5e6a17dbe | Los Angeles (34.0522, −118.2437) → San Diego (32.7157, −117.1611) | 199.90 km | 3,011.39 km | 15.06× | 1,328 attempts in 30 s, 0.91 GB |

- **The single-state cut.** A region built from one extract holds that extract alone, so a road
  network reached only through a neighbouring state falls off as a component of its own and is
  dropped, and a divided road continuing across the line loses its twin there. Of the 17 states run
  with `--allow-fragmented`, the largest dropped components are islands reached by ferry (Martha's
  Vineyard in Massachusetts, 3,587 nodes; San Juan Island in Washington, 1,154; Shelter Island in
  New York, 402; Vinalhaven in Maine, 351; Drummond Island in Michigan, 338) or land reached only
  across a state line (the Arizona Strip, 577 nodes at 36.865, −113.952, reached through Utah and
  Nevada; Carter Lake, Iowa's exclave in Omaha, 216; Alta, Wyoming, reached through Idaho, 122; the
  Montana end of the Beartooth Highway near Cooke City, reached through Wyoming, 111), and the
  shares kept fall to 0.964–0.980 in Maine, Wyoming, New Mexico, West Virginia, Massachusetts,
  Montana, Nevada, Washington, Idaho and Arizona. DC's twin coverage (0.927) and the T0 shares of
  DC, West Virginia and Washington are most likely the motorways and divided roads the line cuts
  (not examined one by one).
- **Later item: regions with their borders.** A region near a state line, a city's bbox region
  included, names the neighbouring states' extracts too and is clipped to its bbox grown by a
  margin (`filter` already merges several extracts and `Region.bbox` already clips), so that a
  network reached through a neighbour and a divided road across the line come whole, which would
  also give the build the bubbles at its edge; the figures above are its evidence.

## Fixtures

Hand-written OSM XML under `tests/fixtures/osm/`, read directly by pyosmium (no conversion),
placed around lon −122.6, lat 45.5 so EPSG:32610 applies; node and way ids are unique within a
file, not across files. Each file's header comment states what the pipeline must make of it.

| Fixture | What it exercises |
|---|---|
| `grid.osm` | a 6×6 grid at ~100 m: named streets, `ref` on two, `oneway=yes` and `oneway=-1` streets, shape points, a toll booth and a level crossing on streets (with a railway way, dropped), traffic signals and a stop sign at junctions, a Waffle House node and a Fred Meyer building way. 36 junctions, 60 segments, 110 edges, 6 POI rows |
| `exclusions.osm` | ways dropped (`service=driveway`, `parking_aisle`, `footway`, `path`, `track`, `construction`, `proposed`, `cycleway`, `area=yes`, `access=private`, `motor_vehicle=no`) and kept (`service=alley`, `access=private` + `motor_vehicle=yes`, `living_street`, `unclassified`), each joined to Main St |
| `oneway.osm` | a motorway with no `oneway` tag and its link, a roundabout ring with no `oneway` tag, `oneway=-1`, `oneway=no`, `oneway=reversible`, and a motorway tagged `oneway=no` |
| `culdesac.osm` | a dead-end branch ending at a cul-de-sac node, and a junction made only by a way ending mid-street |
| `bbox.osm` | ways inside, outside and straddling the bbox `(-122.605, 45.495, -122.595, 45.505)`, one crossing it with no node inside, and POIs inside and outside |
| `refchange.osm` | simplification: a ref change at a way end (kept), identical ways end to end (merged, two-way and one-way), a ring whose last merge would close it on itself (kept) |
| `overpass.osm` | two roads crossing with no shared node, one on a bridge (`layer=1`), their nearest nodes 14 m apart: no merge, no twins |
| `roundabout.osm` | a four-arm `junction=roundabout` ring 30 m across with two-way arms: one roundabout super-node, every (in, out) pair allowed |
| `divided.osm` | a dual-carriageway primary (`ref=OR 99W`, written `OR-99W` on one side) 25 m apart with mismatched side junctions and a right-in/right-out on one side, crossed by a divided minor street: the 4-node crossing becomes one cluster, five exact twin pairs |
| `diamond.osm` | a motorway over a two-way secondary with four diamond ramps: the two sides' ramps join into one interchange, all 16 (in, out) pairs allowed, two twin pairs |
| `cloverleaf.osm` | two motorways crossing on an overpass with four loops and four outer ramps (gores 120–240 m apart): one interchange of 16 gores, all 16 pairs allowed (U-turns through two loops included), four twin pairs |
| `border_south.osm`, `border_north.osm` | two states' extracts as Geofabrik cuts them, the line at lat 45.5: both hold a highway crossing it (900) complete with its nodes, a signal on it and a brand node by the line; each holds a street continuing the highway on its side. Alone, each ends the highway in a dead end beyond the line; merged, 3 ways, 6 nodes each once, 2 POI rows, a raw graph of 4 junctions, 3 segments, 6 edges |

The tests are `tests/pipeline/`: `test_exclusions.py`, `test_download.py`, `test_filter.py`,
`test_build.py`, `test_layout.py`, `test_names.py`, `test_simplify.py`, `test_consolidate.py`
(stages A and B, with in-memory cases: a half-diamond, a slip lane near and far from its
crossing, two branches of one road merging, two interchanges on one freeway, a cluster too wide
and one touching no other road, a diamond whose cross road bridges between its terminals, a
crossover between carriageways, a roundabout bypassed by a slip lane), `test_carriageways.py`
(with in-memory cases: ramps on one side only, offset gores), `test_connectivity.py`,
`test_cleanup.py` (the whole step on every fixture, determinism, the fragmented failure),
`test_enrich.py` (maxspeed parsing, bearings, at a cluster the road's and not the stub's,
identities, units (a road mapped several times one unit per run), flags, collectibles inside
edges and at junctions, delays, pass-through, determinism; the brand index: grouping by wikidata
id or name, the display name, the cut on distinct nodes, the 100 m limit, a POI with only a dead
end near), `test_tiers.py` (each fixture's tiers, the tie rule, the fragmented failure),
`test_save.py` (every fixture saved, read back and checked with `check_build` and `check_saved`,
the brand index saved and read back, the speed table, the empty-transitions, too-many-speeds and
shape-point refusals, two one-way ways overlapping in part saved, a stable `graph_version`, and
hypothesis: a shape drawn within the tolerance and the rounding, its ends exact), `test_qa.py`
(a passing and a failing build, the layers), `test_cli_steps.py` (the steps through the CLI
under a temporary `WORST_ROUTE_DATA`, including the sidecar skips, save's refusal, `worst-route
plan` and `validate` on a build the pipeline saved, both engines through `save` to one
`graph_version`, a build switched between them in place, and a cleanup changing only what enrich
never reads), `test_arrays.py`, `test_arrays_enrich.py`, `test_arrays_build.py` and
`test_arrays_property.py` (the arrays engine against networkx's, **The arrays engine**), and,
marked `data`, `test_arrays_data.py` (both engines on Portland), `test_cleanup_data.py` (the
invariants and the report on Portland's latest build, either engine's stages) and
`test_build_data.py` (Portland's saved build: valid, its `graph_version`, `check_build`,
`check_saved` against the enriched graph, `stages/enrich.pkl` or the arrays engine's cleaned
graph enriched by networkx, the tier masks, the brands (each on at least 3 pass-through nodes,
ids in name order), QA passed). `invariants.py` holds the invariant checks the tests share
(`check_build` on a saved build's arrays; `check_saved` of a build and its geometry against the
enriched graph, the OSM nodes along the edges that the validator's proof assumes included),
`chain.py` runs the steps in memory on a fixture, `parity.py` compares the two engines' graphs
and builds exactly, `osm.py` writes the in-memory cases' OSM files from positions in metres, and
`copies.py` the cases of a road mapped more than once (copies over one run, two one-way ways
overlapping in part, US 322's two carriageways meeting at one node), which
`tests/engine/test_road_copies.py` builds too.
