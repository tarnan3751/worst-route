# Pipeline

How the offline pipeline works: the steps built so far, the data layout, build ids and
sidecars, and the rules each step applies. The design is `docs/PLAN.md` §2; the code is
`src/worst_route/pipeline/`, one module per step, and each module's header comment states its
contract.

Built: every step, `download`, `filter`, `build`, `cleanup`, `enrich`, `tiers`, `save` and `qa`.
`save` writes the build in the format `docs/reference/graph.md` describes, which `worst-route
plan` reads.

## Commands

```
uv run worst-route pipeline download --region portland [--force]
uv run worst-route pipeline filter   --region portland [--source PATH] [--force]
uv run worst-route pipeline build    --region portland [--source PATH] [--build-id ID] [--force]
uv run worst-route pipeline cleanup  --region portland [--build-id ID] [--force] [--allow-fragmented]
uv run worst-route pipeline enrich   --region portland [--build-id ID] [--force]
uv run worst-route pipeline tiers    --region portland [--build-id ID] [--force] [--allow-fragmented]
uv run worst-route pipeline save     --region portland [--build-id ID] [--force]
uv run worst-route pipeline qa       --region portland [--build-id ID] [--force]
```

`--region` is a key of `config.REGIONS` (a Geofabrik state slug, an optional bbox, a projected
CRS). `--source` makes `filter` or `build` read that file instead of its usual input (a fixture,
in tests). Every step accepts `--build-id` and `--force`; `download` and `filter` ignore the
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
| `graphs/<region>/<build_id>/stages/build.pkl`, `stages/build.json` | `build` |
| `graphs/<region>/latest` (the region's current build id, one line) | `build` |
| `graphs/<region>/<build_id>/stages/cleanup.pkl`, `stages/cleanup.json` | `cleanup` |
| `graphs/<region>/<build_id>/stages/enrich.pkl`, `stages/enrich.json` | `enrich` |
| `graphs/<region>/<build_id>/stages/tiers.json` | `tiers` |
| `graphs/<region>/<build_id>/meta.json`, `strings.json`, `arrays/`; `stages/save.json` | `save` (the build, through `graph.store.write_build`) |
| `graphs/<region>/<build_id>/qa/qa.json`, `qa/*.geojson` | `qa` |
| `<output>.inputs.json` beside a step's output (its sidecar) | every step but `download` |

`stages/` holds intermediate state between the steps of one build. `stages/*.pkl` is the only
place the pipeline pickles; the final on-disk format (`docs/PLAN.md` §2.7) never does. The build
itself, what `worst-route plan` reads, sits beside `stages/` in the same directory, and the QA
report beside it in `qa/`.

## Build ids, sidecars, skipping

- **Build id:** `--build-id` if given, else the first 12 hex digits of a sha256 over the JSON
  list `[md5 of the filtered roads file, region name, region bbox, region CRS (EPSG code),
  PIPELINE_VERSION]` (`layout.build_id_for`). `PIPELINE_VERSION` is in `config.py` (the
  `pipeline` package re-exports it, and `graph.store` reads it to name the remedy for another
  version's build); bump it when a step's output changes for the same input. It is 2 since
  M3, for two changes: `enrich`'s bearings leave out cleanup's stubs, and `save` writes
  `node_roundabouts`. Every step's sidecar records it, so a region built at version 1 is
  rebuilt from `filter` (which reads the raw extract again, no download) under a new build id,
  and `latest` moves to it; a version 2 build saved before `node_roundabouts` existed is saved
  again (`pipeline save --force`). Portland's build at version 2 is 506f747d7458, at the
  `graph_version` ae1ce1e8289b; the main tree's e4b3b4ad90db (version 1) is rebuilt so after
  this lands.
- **Sidecar:** after `filter`, `build` or `cleanup` writes its output it records
  `{"step", "inputs": [{"file", "md5"}], "params"}` in `<output>.inputs.json` (filter: the
  bbox and the pipeline version; build: the region, bbox, CRS and pipeline version; cleanup:
  `allow_fragmented`, the config values it depends on, `cleanup.parameters()`, and the pipeline
  version, its input being `build.pkl`; enrich: its config and the pipeline version, its inputs
  `cleanup.pkl` and the POI table; tiers: like cleanup, its input `enrich.pkl`; save: the
  pipeline version and the `params` it copies into the build's meta, so a changed parameter that
  leaves every stage byte-identical is still another input, its inputs `enrich.pkl` and
  `tiers.json`, its output `stages/save.json`;
  qa: its thresholds, its inputs `meta.json` and the stage reports, its output `qa/qa.json`).
  Inputs are recorded by file name and md5, so moving the data directory does not invalidate
  them. The
  filter's sidecar also records `source_timestamp`, the extract's data date from its header
  (`osmosis_replication_timestamp`, read by `filter.source_timestamp`; null when the header has
  none, as in the fixtures), for the `save` step to carry into the build's metadata.
- **Skipping:** a step is skipped, with a message, while its outputs exist and its sidecar
  equals what it would record now; `--force` runs it anyway. The sidecar is deleted before a
  step runs, so an interrupted run is never mistaken for a finished one (`cleanup` also deletes
  its old outputs, so a failed run leaves none, and so do `enrich` and `tiers`). `build` writes
  `latest` whether it ran or was skipped. `save` is the exception, because a written build is
  never overwritten: with its sidecar matching it skips; with a build already written from other
  inputs it refuses without `--force`; `--force` deletes the build (`meta.json` first) and writes
  it again. `qa` prints its stored thresholds when it skips, and fails again when they fail.
- **Stale stages:** a step reads earlier stages only when they are fresh. `layout.stale_stages`
  walks the chain `STAGE_CHAIN` (cleanup from `build.pkl`, enrich from `cleanup.pkl`, tiers
  from `enrich.pkl`, save from `enrich.pkl` and `tiers.json`) and names each stage whose sidecar
  records an input md5 that the file there no longer has (or a file now gone); a stage not yet
  made is not stale. `enrich`, `tiers`, `save` and `qa` refuse with one line, "stage X is stale:
  rerun `worst-route pipeline X --region R`", when a stage before them is (`qa` checks the whole
  chain, `save` included): rerunning cleanup and enrich and then `save`, without `tiers`, is refused
  rather than saving tiers computed on the old graph.
- **Download** is skipped while the extract on disk matches its local `.md5` file (no sidecar;
  the check reads only local files).

## `download`

`download.download_state(state, force=False, fetch=fetch) -> Download(path, skipped)` fetches
`https://download.geofabrik.de/north-america/us/<state>-latest.osm.pbf` and its `.md5`
(`<hex>  <filename>`), each to a `.part` file. Only when the extract's md5 matches the
published one are both renamed into place; a mismatch raises `ValueError` and leaves the
previous pair as it was. `skipped` is True when the extract on disk was already current and
nothing was fetched, so the CLI computes the md5 once. `fetch(url, dest)` is the only function
that touches the network (httpx streaming, `settings().user_agent`); tests pass a fake, and an
autouse fixture in `tests/pipeline/conftest.py` fails any test that would reach the network.

## `filter`

`filter.filter_roads(sources, roads_out, pois_out, bbox) -> FilterStats` reads each source with
pyosmium 4 in two passes.

**Roads.** With a bbox, `FileProcessor(src, NODE | WAY)` with node locations, `EntityFilter(WAY)`
and `KeyFilter("highway")` (pyosmium refuses `with_locations()` unless nodes are read, so nodes
are read and filtered out); without one (`oregon`), the ways alone, with no locations. A way is
kept when `exclusions.is_kept` accepts
its tags and, with a bbox, at least one of its node locations lies inside it (boundary
inclusive): a way that touches the box is kept whole, and a way that crosses the box with no
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

**Several sources** (multi-state builds) are read in order: a way or POI already taken from an
earlier source is skipped (`ways_duplicate`), the per-source road files are merged with
`MergeInputReader` (sorted, duplicate nodes dropped). Outputs are written to temporary files
and renamed into place. The CLI passes one source: the region's state extract, or `--source`.

`FilterStats`: `ways_read` (highway ways), `ways_excluded`, `ways_outside` (no node in the
bbox), `ways_duplicate`, `ways_kept`, and `pois` (rows per kind).

## `build`

`build.build_raw_graph(roads, crs_epsg) -> (nx.MultiDiGraph, BuildStats)`, then
`build.save_stage(graph, stats, build_dir)`.

1. One pass with `FileProcessor(roads, NODE | WAY)`: node id → (lon, lat) into arrays; each
   way's node refs and the tags in `EDGE_TAGS`. `exclusions.kept_class` is applied again (so an
   unfiltered file builds the same graph as its filtered version), a way id seen twice is read
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
   two in and two out pairing into the two directions of one road) and the two edges of each
   pair have the same `highway`, `oneway`, `reversed` and every `EDGE_TAGS` value. In the raw
   graph such a node always ends a way, so a node stays wherever a way's tags change (ref, name,
   speed limit, lanes, bridge, ...) and every edge stays homogeneous. A merge that would repeat
   an OSM node id (a chain closing on itself) is not made. The merged edge concatenates
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
   become one `cluster` node at the centroid of their buffers; nodes near each other with no
   short edge between them (an overpass, the far ends of a cloverleaf loop) do not. OSMnx drops
   an edge whose two ends merged without keeping it anywhere, so the post-pass adds its interior
   `osm_nodes` to the cluster's members; it also replaces OSMnx's node attributes
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
   ways. The relation is symmetric. Using an edge bans its twins. A one-way couplet a block
   apart shares a ref but not a name, so it is not a twin pair: OR 99E's Grand Avenue and MLK
   Boulevard, OR 8's Baseline and Oak Streets.
4. **Stage B** (`consolidate.stage_b`, ours): interchanges and roundabouts become one
   super-junction each.
   - The ramp (`*_link`) and roundabout edges, undirected, form components. A component's
     attachment nodes are those that also touch another road.
   - **Join:** two components whose attachment roads share at least two identities
     (`names.road_signature`: the normalised ref set, else the normalised name) and have
     attachment nodes within `STAGE_B_JOIN_M` (500 m) of each other are one cluster. The ramps of
     one interchange rarely share a node (a diamond's two sides, a cloverleaf's eight ramps), and
     consecutive interchanges share only the freeway, so this joins the first and not the second.
   - **Slip lanes:** a cluster whose links are all minor (`STAGE_B_MINOR_LINKS`:
     `primary_link`, `secondary_link`, `tertiary_link`) also takes in every node that two of its
     attachment nodes reach through other roads of at most `STAGE_B_SLIP_CENTER_M` (150 m), of
     two different identities: the at-grade crossing the slip lane bypasses, which is the same
     junction. A node reached along one road only is no crossing (two branches of a road
     merging, or two carriageways meeting). Clusters that claim the same node, or a node of
     another cluster, become one.
   - **Pockets:** each cluster then takes in the interior nodes of every path of at most
     `STAGE_B_POCKET_M` (600 m) along one road (one signature, unnamed roads aside) from one of
     its attachment nodes to another, outside the cluster and through no other cluster, and
     repeats until it takes nothing more. Without that, a road that leaves the super-junction
     and comes back to it (a bridge between two ramp terminals, a frontage-road crossing) could
     not be driven straight through: the route would pass the super-node twice. A path that
     changes road (one carriageway, a cross street, the other carriageway) is not a pocket, and
     a road that leaves a node and comes back to the same node is a turnaround, not a pocket.
     On Portland 8 same-road through movements at 5 super-junctions stay lost: their pocket
     path runs through another cluster's nodes or is longer than 600 m.
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
   less than `SCC_MIN_RETAINED` (0.99) of the nodes, `run_cleanup` raises
   `FragmentedGraphError` and the CLI exits 1 without writing the stage, unless
   `--allow-fragmented`. The kept nodes are numbered `0..n-1` again, in the same order.

**The stage file.** `stages/cleanup.pkl` is the cleaned `MultiDiGraph`:

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
  pairs; present, possibly empty, for every super-node: an empty table here means no pair is
  allowed). `GraphData` reads a node without transition rows as allowing every pair and cannot
  express an empty table, so `save` refuses a build that has a super-node whose table is empty
  (Portland's has none).
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
`twins`, found on the stage A graph (`candidates`, `paired`, `pairs`, `motorway_trunk_m`,
`coverage`: the share of one-way motorway and trunk length with a twin, the QA metric);
`stage_b` (`superjunctions` by kind, `sizes`: nodes replaced → super-nodes, `joined`,
`slip_centres`, `pocket_nodes`, `split`, `internal_edges`, `twin_pairs` carried across,
`twin_transitions`: transitions that must drive an internal edge with a twin outside,
`twin_passages`: passages banned for driving both edges of a twin pair inside, `nodes`,
`edges`); `connectivity` (nodes and edges before and after, dropped nodes, edges and length, a
two-way road counted once, the number of dropped components and the 20 largest with their size,
length and centroid, `retained`); `seconds` per step.

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
The cleaned graph has 65,810 nodes and 168,167 edges.

## `enrich`

`enrich.run_enrich(graph, pois) -> EnrichReport` adds to the cleaned graph, in place, what the
engine and the modes read, then `enrich.save_stage` pickles it to `stages/enrich.pkl` and writes
the report to `stages/enrich.json`. Edges are visited in key order; nothing is random. The
constants are in `config.py`.

- **Speed and time.** `parse_maxspeed` reads the `maxspeed` tag: `"45 mph"`, `"45mph"`; a bare
  number is mph, the US convention; `"50 km/h"`, `kmh`, `kph`; a `;` list takes its first value.
  Anything else (`signals`, `none`, `US:urban`, `0`, knots) falls back to the class default,
  `SPEED_DEFAULT_MPH` (motorway 65, trunk 55, primary 45, secondary 35, tertiary 30,
  unclassified and residential 25, living street 15, alley 10, every `_link` 35). `speed_kmh`
  is stored, `speed_parsed` says which, and `time_s = length / (speed_kmh / 3.6)`.
- **Identity** (hard mode, directions): `names.identity_of`, the highest-class ref (Interstate,
  then US, then a state's postal code or `SR`, then any other, such as a county's `CR`;
  alphabetical within a class), each ref keeping its `BUSINESS`, `SPUR` or `ALT` suffix so such a
  route never takes its mainline's identity; else the normalised name; else none.
- **Road unit.** The two directions of a two-way road are the edges whose `osm_nodes` run
  opposite ways; both get the smaller key of the two as `unit`, which `save` turns into the
  smaller edge id. A one-way edge is its own unit. Parallel roads between the same junctions
  differ in their shape points and stay separate units.
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
  from the road's own, 63% at interchanges, 19% at roundabouts; now 0.2–0.5%, as at plain nodes
  (0.07%): the build's float32 coordinates' rounding, a degree or two, flipping turns at a
  class's threshold. `uv run python tools/turn_bearings.py --region portland` reports it for a
  saved build, counting each edge's stubs from the build alone (its coordinates less its OSM
  nodes).
- **Collectibles**, from the POI table's node rows (a way row is not counted): a toll booth or
  level crossing whose OSM id lies inside edges counts on every edge that holds it, both
  directions of a two-way road, of which a legal route uses one; one that is a node's member (at
  a junction, or a shape point a cluster absorbed) counts on that node's lowest-key out-edge, an
  M1 approximation until node collectibles (M3); any other is off the graph.
- **Node delay**: the largest `NODE_DELAY_S` (stop 10 s, signals 30 s) among the stop signs and
  signals in the node's members, so a signal cluster delays once. One that is a shape point adds
  none: most stop signs are mapped a few metres before the junction, on the approach.
- **Pass-through**: some in-neighbour u and out-neighbour w with u ≠ w, self-loops ignored.

`stages/enrich.json` is `EnrichReport`: edges whose speed was parsed and defaulted, by class;
distinct identities and the edges with one; units and two-way pairs; edges per flag; per
collectible kind the POI nodes found, placed inside edges, at junctions and off the graph, and
the edges with a count; per delay kind the same, shape points counted apart; nodes with a delay;
pass-through nodes; seconds.

**Portland** (the 2026-10-30 extract, 4–6 s): 89,902 of 168,167 edges have a parsed speed;
16,864 identities on 163,645 edges; 87,793 units, 80,374 of them two-way pairs; 1,088 bridge, 23
tunnel, 4 toll and 54 link edges; 2 toll booths (on 4 edges), 1,442 level crossings (550 inside
edges, 146 at junctions, 746 on roads the filter dropped); of 5,960 stop signs 730 are junction
members and 4,471 shape points, of 3,377 signals 2,469 and 829; 2,438 nodes have a delay; 51,406
of 65,810 nodes are pass-through.

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
T3 20,202 of 20,213 (99.95%; 7 components), T4 all 65,810.

## `save`

`save.graph_data(graph, tier_nodes, meta) -> GraphData`, then `save.save_build(data,
build_dir)`. The arrays are `docs/reference/graph.md`'s; how they come from the enriched graph:

- **Edge ids** are the positions of the sorted edge keys `(u, v, key)`, the format's (u, v,
  original order); `out_edges` and `in_edges` list each node's edges in ascending id. A unit's
  id is the id of its key.
- **Nodes**: `x, y, lon, lat`; `node_kind` from `kind` (`NodeKind`); `node_state` −1 (states
  arrive with places, M4); `pass_through`; `delay_s`; `members`.
- **Edges**: `length` and the enriched values; `edge_highway` from the class string
  (`HighwayClass`); `edge_tier` the first tier holding the class; `edge_name` and `edge_ref`
  index the raw `name` and `ref` tags, `edge_identity` the identities, each table sorted;
  `way_osm_ids` from `osmids`, `osmnode_ids` from `osm_nodes`; `coords` the projected geometry
  turned back into (lon, lat), float32, so each edge runs from its tail's position to its head's.
- **Twins** and **transitions**: the cleaned graph's tables, keys turned into ids, rows sorted.
- **Tiers**: `tier_node_mask[t]` what `tiers` kept, `tier_edge_mask[t]` the tier's classes with
  both ends kept.
- **Collectibles**: `node_roundabouts`, the `roundabouts` count of each super-junction's record
  (the rings among its internal edges, so an interchange that holds a ring counts it), 0 at
  every other node.

A super-node whose transition table is empty allows no turn through it, which the format cannot
say (a node without rows allows every turn): `graph_data` raises `EmptyTransitionsError`, naming
the node and its position, and the CLI exits 1. Portland has none.

`save.build_meta` assembles `meta`: `region`, `build_id`, `crs_epsg`, `created` (UTC),
`pipeline_version`, `sources` and `params`. `save.build_sources` gives the extract `filter`
read (file, md5 and `source_timestamp`) after checking that the filtered roads are still the
ones `build` read (their md5 against `build.pkl`'s sidecar); otherwise it raises
`SourcesChangedError` and the CLI says "the filtered extract changed since this build was
built; rebuild". `save.build_params` gives the `region`, its `bbox` and `crs_epsg`, and the
`config` and `allow_fragmented` the cleanup, enrich and tiers sidecars recorded, so
`graph_version` covers what the build was made with; `pipeline_version` stays out of
`params`. `graph_data` fills `counts`: nodes, edges, twin
pairs, transition rows, super-junctions. `save_build` writes the build with `write_build`
(which validates it), reads it back with `read_build`, raises `RuntimeError` if any array, the
strings or the meta differ, and writes `stages/save.json` (`graph_version`, the counts, the
size, seconds). `save.remove_build` deletes a written build, `meta.json` first, for `--force`.

**Portland** (2–4 s): 65,810 nodes, 168,167 edges, 3,353 twin pairs, 11,378 transition rows at
657 super-junctions, 39.9 MB; 170 roundabout rings, at 165 roundabouts (one holds two) and 4
interchanges.

## `qa`

`qa.run_qa(build_dir) -> QaReport` reads the saved build, the stage reports and the
super-junction records in `stages/enrich.pkl`, and writes under the build's `qa/` (a build
without an array of the format is one line naming its remedy, the rebuild from `filter` for one
another pipeline version wrote, and exit 1; a plain `save` refuses such a build with the same
line):

- `twins.geojson`: one feature per twin pair, a MultiLineString of the two edges, with their
  ids, classes, identities and lengths;
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
cleanup kept at least `SCC_MIN_RETAINED` (0.99) of the nodes; every tier kept at least
`TIER_MIN_RETAINED` (0.95) of its own. `pipeline qa` prints each with its value and exits 1 when
one fails. No map page and no random snap tests yet (§2.8 has them for later).

**Portland** (1–1.5 s): twin coverage 0.9588, SCC retained 0.9960, tiers 1.0, 1.0, 1.0, 0.9995,
1.0: passed. The whole pipeline from the Oregon extract takes 47–75 s on the dev machine,
depending on its load; on the quieter run: filter 13 s, build 3 s, cleanup 23 s, enrich 4 s,
tiers 2 s, save 2 s, qa 1 s.

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

The tests are `tests/pipeline/`: `test_exclusions.py`, `test_download.py`, `test_filter.py`,
`test_build.py`, `test_layout.py`, `test_names.py`, `test_simplify.py`, `test_consolidate.py`
(stages A and B, with in-memory cases: a half-diamond, a slip lane near and far from its
crossing, two branches of one road merging, two interchanges on one freeway, a cluster too wide
and one touching no other road, a diamond whose cross road bridges between its terminals, a
crossover between carriageways, a roundabout bypassed by a slip lane),
`test_carriageways.py` (with in-memory cases: ramps on one side only, offset gores),
`test_connectivity.py`, `test_cleanup.py` (the whole step on every fixture, determinism, the
fragmented failure), `test_enrich.py` (maxspeed parsing, bearings, at a cluster the road's and not the stub's,
identities, units, flags,
collectibles inside edges and at junctions, delays, pass-through, determinism), `test_tiers.py`
(each fixture's tiers, the tie rule, the fragmented failure), `test_save.py` (every fixture saved,
read back and checked with `check_build` and `check_saved`, the empty-transitions refusal, a
stable `graph_version`), `test_qa.py` (a passing and a failing build, the layers),
`test_cli_steps.py` (the steps through the CLI under a temporary `WORST_ROUTE_DATA`, including
the sidecar skips, save's refusal, and `worst-route plan` and `validate` on a build the
pipeline saved), and, marked `data`, `test_cleanup_data.py` (the invariants and the report on
Portland's latest build) and `test_build_data.py` (Portland's saved build: valid, its
`graph_version`, `check_build`, `check_saved` against `stages/enrich.pkl`, the tier masks, QA
passed). `invariants.py` holds the invariant checks the tests share (`check_build` and
`check_saved` on a saved build's arrays), `chain.py` runs the steps in memory on a fixture, and
`osm.py` writes the in-memory cases' OSM files from positions in metres.
