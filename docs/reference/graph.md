# Graph

The routing graph of one build: its arrays (`GraphData`), the on-disk build format and its
`graph_version`, the build's geometry (the edges' shapes, apart from the arrays), the
`GraphBackend` protocol the engine searches through, the pure-Python
reference backend and the CSR + Numba production backend, the spatial helpers (projection,
snapping, leash and bubble masks), loading saved builds for the API, and the builders tests use
to make small graphs by hand.
Code: `src/worst_route/graph/`. The design it implements is `docs/PLAN.md` §2.6–2.7, §3.2,
§3.9–3.10; the conventions are `CLAUDE.md`.

| Module | What it holds |
|---|---|
| `data.py` | `GraphData`, `Strings`, `ARRAY_SPECS` (the format), `FORMAT_VERSION`, `MAX_SPEEDS`, `NodeKind`, `EdgeFlag`, `validate()` |
| `store.py` | `write_build`, `read_build`, `graph_version`, `MissingArray`, `outdated` |
| `geometry.py` | `Geometry`, `GEOMETRY_SPECS`, `encode`, `straight`, `edge_xy`, `edge_points`, `lonlat_to_xy`, `xy_to_lonlat`, `geometry_version`, `read_geometry` (**Geometry**) |
| `backend.py` | the `GraphBackend` protocol, the `Path` and `EdgeTree` types, and what every backend shares: `check_ends`, `WeightCheck`, `as_cost`, `as_limits`, `enterable`, `banned_mask`, `final_mask`, `largest_scc_mask`, the turn classes (`TURN_CLASS`, `as_turn_classes`, `check_turn_edges`) |
| `python_backend.py` | `PythonBackend`, the reference implementation |
| `csr_backend.py` | `CsrBackend`, the production implementation, over `kernels.py` |
| `kernels.py` | the Numba kernels `dijkstra_masked`, `simple_paths_dfs`, `dijkstra_edges_masked`, `dijkstra_edges_tree`, `simple_paths_dfs_turns` and their turn checks `turn_allowed` and `turn_class_allowed` |
| `backends.py` | `make_backend` |
| `loader.py` | `load_build`, `Loaded`, `BuildNotFound` |
| `spatial.py` | `SpatialIndex` (`project`, `snap`, `leash_mask`, `bubble_mask`), `Snap`, `SnapError`, `active_mask` |
| `testing.py` | `make_graph`, `grid_graph`, `Edge`, `edge_id`, `GRID_ORIGIN` |

`config.py` holds `HighwayClass` (the road classes, 0–13), `TIERS` (the classes of T0–T4,
each tier containing the one before it, T4 every class), and `BackendName` and
`DEFAULT_BACKEND` (**Choosing a backend**).

## The arrays

`GraphData` is a frozen dataclass of NumPy arrays plus `strings` and `meta`. n is the node
count, m the edge count, T = 5 tiers, k the number of transition rows, b the number of brands
(the length of `strings.brands`), r the number of brand rows (the length of `brand_nodes`) and
s the number of distinct speeds (at most `MAX_SPEEDS`, 256). Every array is read-only:
memory-mapped when read from disk, flagged read-only when built in memory, so a stray write into
the graph raises instead of corrupting every worker's view. `ARRAY_SPECS` lists the arrays in a
fixed order, the order of the fields, the files and the hash (format 4, `FORMAT_VERSION`, with
`PIPELINE_VERSION` 6). They are what the engine and the validator read, with a few the
presentation reads (`node_kind`, also qa's; `edge_tier`, the share image's thinning) and two
nothing reads at run time (`node_pass_through`, which snapping judges within the request's mask
instead, and `brand_locations`): nothing that only draws a route, which is the build's geometry
(**Geometry**), nor what only the pipeline needs, the OSM nodes along each edge and its way ids,
which stay in the stages.

| Group | Array | dtype, shape | Meaning |
|---|---|---|---|
| nodes | `node_x`, `node_y` | f64[n] | position, projected metres in `meta["crs_epsg"]` |
| | `node_kind` | u8[n] | `NodeKind`: 0 plain, 1 cluster (stage A), 2 interchange, 3 roundabout (stage B) |
| | `node_pass_through` | bool[n] | some u → v → w exists with u ≠ w (self-loops ignored), on the whole graph; snapping judges via stops within the request's mask instead |
| | `node_delay_s` | f32[n] | stop sign / signal delay |
| | `member_indptr`, `member_osm_ids` | i64[n+1], i64[] | original OSM node ids merged into each node (≥ 1 each); each id belongs to one node only, which the validator's proof from original ids rests on (`docs/reference/engine.md`, `osm_ids`) |
| edges | `edge_u`, `edge_v` | i32[m] | tail, head |
| | `edge_length_m`, `edge_time_s` | f32[m] | length and time, finite and ≥ 0 |
| | `edge_speed`, `speed_kmh` | u8[m], f32[s] | each edge's index into `speed_kmh`, the build's distinct speeds in km/h, finite, positive and strictly ascending (a build's speeds are few, and not whole km/h: Portland's thirteen are whole mph) |
| | `edge_highway` | u8[m] | `HighwayClass` |
| | `edge_tier` | u8[m] | the first tier whose classes contain the edge's class |
| | `edge_name`, `edge_ref`, `edge_identity` | i32[m] | index into `strings.names`, `.refs`, `.identities`; −1 none |
| | `edge_unit` | i32[m] | road unit (one OSM node run in both directions, however many ways map it), in [0, m) |
| | `edge_flags` | u8[m] | `EdgeFlag` bits: 1 bridge, 2 tunnel, 4 toll, 8 roundabout, 16 link |
| | `edge_toll_booths`, `edge_level_crossings` | u8[m] | counts on the edge |
| | `edge_bearing_in`, `edge_bearing_out` | i16[m] | degrees clockwise from north, 0–359: arriving at v, leaving u, over the road's last and first 20 m (the road, not the stub cleanup added to reach a merged node: `docs/reference/pipeline.md`, enrich) |
| adjacency | `out_indptr`, `out_edges` | i64[n+1], i32[m] | each node's out-edges, ascending edge id |
| | `in_indptr`, `in_edges` | i64[n+1], i32[m] | each node's in-edges, ascending edge id |
| twins | `twin_indptr`, `twin_edges` | i64[m+1], i32[] | using an edge bans every edge in its row; symmetric, rows strictly ascending, never itself |
| transitions | `trans_node`, `trans_in`, `trans_out` | i32[k] each | allowed (in, out) pairs at a node, sorted by (node, in, out), no duplicates; a node with no rows allows every pair. Rows sit at super-junctions (their passages) and, since `PIPELINE_VERSION` 8, at the plain and cluster nodes an OSM turn restriction gave a table (restricted junctions: `docs/reference/pipeline.md`, **Turn restrictions**); the format, the backends and the validator read both alike |
| tiers | `tier_node_mask`, `tier_edge_mask` | bool[T, n], bool[T, m] | tier membership (the pipeline's after per-tier SCC) |
| collectibles | `node_roundabouts` | u8[n] | the roundabout rings a super-junction holds (a roundabout's one, sometimes two; an interchange that holds a ring counts it), 0 at every other node; the collect mode's roundabouts (`docs/reference/engine.md`, **Objectives**). Added after the first builds (with `PIPELINE_VERSION` 2): a build saved before it lacks the file and is made again (**The build format**) |
| brands | `brand_indptr`, `brand_nodes` | i64[b+1], i32[r] | the brand index: brand k's nodes are `brand_nodes[brand_indptr[k]:brand_indptr[k + 1]]` (`nodes_of_brand(k)`), at least one each, strictly ascending; the nodes a route can pass through nearest the brand's POIs (`docs/reference/pipeline.md`, **enrich**); the brand mode's locations (`docs/reference/engine.md`, **Objectives**). A brand's id is its run's index; its name and wikidata id are in `strings` |
| | `brand_locations` | i32[r] | the POIs snapped to each of those nodes (≥ 1). The three arrays were added with `PIPELINE_VERSION` 3 |

Three arrays are derived, not stored: `node_lon` and `node_lat` (f64[n]), the inverse
projection of `node_x` and `node_y` from `meta["crs_epsg"]` (pyproj, `always_xy`), and
`edge_speed_kmh` (f32[m]), `speed_kmh[edge_speed]`. Each is a `cached_property`, worked out once
per `GraphData` on first use (once per process for a loaded build) and read-only. The engine
reads x/y, which are stored exactly as the pipeline computed them; lon/lat are the
presentation's (a snapped stop's position, the drawn lines' ends, the region's extent) and
differ from the pipeline's by at most 2e-14 degrees on Portland, the same at 7 decimals on
every node. Stored the other way round, x/y derived from lon/lat would move the merged nodes'
x/y by up to 2e-9 m, enough to change three of the golden routes on the lattice.

A node with no transition rows allows every pair, so an empty table cannot be written: cleanup
drops a super-junction or restricted junction left with edges in and out and no pair allowed
after connectivity, and connectivity drops one without, and `save` refuses a build that has one,
as a guard (`docs/reference/pipeline.md`, **cleanup**).

**Edge ids are sorted by (u, v, original order).** Each node's out-edges are therefore one
contiguous run of ascending ids (`out_edges` is `0..m-1`), which the backends' tie rule relies
on.

`strings` is `Strings(names, refs, identities, brands, brand_wikidata)`: the three lists of str
the edges index into, then each brand's display name and its wikidata id (str, or None when its
POIs carry none), by brand id (the last two empty by default). `meta` is a dict with
`region`, `build_id`, `crs_epsg`, `created` (ISO time), `pipeline_version`, `format` (4),
`graph_version`, `counts` (at least `nodes` and `edges`), `params` (the build parameters: free-form, but
JSON-native with string keys, so no int keys, tuples, NaN or other objects; `pipeline save`
puts the `region`, its `bbox` and `crs_epsg` there with each step's configuration, so they
enter `graph_version`, `docs/reference/pipeline.md`) and `sources` (a list).

`validate()` raises `ValueError` naming the array at fault. `validate(deep=False)` checks only
what costs nothing to read: every array present as a NumPy array of its dtype and shape, the
meta keys, `format` 4, `counts` against n and m, at most 256 speeds, and `params` surviving a
JSON round trip unchanged. The default also checks contents: the indptr runs (from 0 to the
values' length, each at least its minimum above), id and enum ranges (`edge_speed` within the
speeds), the speeds finite, positive and ascending, `member_osm_ids` unique, edges sorted by
(u, v), both adjacency lists exactly as defined, twins and transitions as above, a transition's edges
meeting at its node, roundabout rings counted at super-junctions (interchanges and
roundabouts) only, and each brand's nodes in range and strictly ascending, each with at least
one location. The cheap check holds `brand_indptr` to one more than the brand names and
`brand_wikidata` to one entry per name.

## The build format

One directory per build, `data/graphs/<region>/<build_id>/`, which the pipeline's earlier
steps already use for their `stages/` and sidecars (`docs/reference/pipeline.md`):

```
meta.json              the meta dict, graph_version and geometry_version filled in (sorted keys,
                       indented)
strings.json           {"names": [...], "refs": [...], "identities": [...], "brands": [...],
                        "brand_wikidata": [...]} (UTF-8, compact)
arrays/<name>.npy      one file per array of ARRAY_SPECS, written by np.save without pickles:
                       what the engine and the validator read
geometry/<name>.npy    geom_indptr and geom_dxy, the edges' shapes (**Geometry**): what only the
                       drawing of a route reads
```

- `write_build(data, path, geometry=None) -> str` runs `validate()` (and the geometry's) and
  writes the build into `path`, which may already exist and hold other files; `geometry` None
  writes straight edges (`geometry.straight`), as the test builders make them. A `path` that
  has `meta.json` raises `FileExistsError`: a written build is never overwritten. Builds are
  immutable unless `pipeline save --force` deletes one and writes it again, which changes its
  `graph_version` when its contents or params differ.
  It stages everything in a sibling `.<name>.tmp` directory, moves `strings.json`, `arrays/`
  and `geometry/` into `path`, then moves `meta.json` in last as the commit marker:
  `read_build` needs `meta.json`, so a reader never sees half a build. What a crashed write
  leaves is replaced by the next one: a staging directory (a crash while staging leaves `path`
  as it was), or `arrays/`, `geometry/` and `strings.json` without `meta.json` (a crash between
  the moves). It returns the `graph_version` and writes it and the `geometry_version` into
  `meta.json`; it does not change the caller's `meta`. It imports `graph/geometry.py` only
  inside the call, so the engine and the job workers, which read builds through this module,
  never import it.
- `read_build(path, mmap=True) -> GraphData` loads every array with
  `np.load(mmap_mode="r", allow_pickle=False)`: worker processes share one copy through the OS
  page cache, and loading costs nearly nothing. `mmap=False` loads read-only copies. It runs
  `validate(deep=False)` only: a deep validation or a re-hash would read every page of the
  build in every worker; it reads no geometry. A build it cannot take raises `MissingArray` (a
  `FileNotFoundError`), one line naming the remedy for the build's state (`store.outdated`):
  for a build another pipeline version wrote (its `meta.pipeline_version` is not
  `PIPELINE_VERSION`), whatever arrays it holds, the rebuild ("the build at PATH was written by
  pipeline 3 (now 6): rebuild the region with `worst-route pipeline filter --region R` and the
  steps after it"), which gives the build a new id, so this code never reads a build another
  version made; for this version's build of another format (its `meta.format` is not
  `FORMAT_VERSION`), the format and the save that writes it again, before its arrays are read;
  for this version's build lacking an array, the array and the save that writes
  it again ("the build at PATH lacks the array node_roundabouts: rebuild it with `worst-route
  pipeline save --force --region R`"). `plan`, `validate`, `serve`, `pipeline qa` and the
  acceptance tool print it and exit 1, and a plain `pipeline save` refuses another version's
  build with the same rebuild line. `PIPELINE_VERSION` 2 (M3) covered the bearings without
  cleanup's stubs and `node_roundabouts`, 3 (M3) the brand arrays and strings, 4 (M5) stage A's
  cluster positions, 6 (M5) the compact format (format 4: x/y without lon/lat, the speed table,
  no way ids, OSM node runs, states or shapes among the arrays, the geometry apart), so a build
  made at an earlier version is rebuilt from `filter` (`docs/reference/pipeline.md`, **Build
  ids, sidecars, skipping**).
- `.npy` per array, not `.npz` or Parquet: NumPy memory-maps `.npy` files only (`.npz`
  members are read whole). Parquet exports for DuckDB or QGIS are a `qa` convenience for later.

### `graph_version`

`graph_version(data)` is the sha256, as hex, over, in `ARRAY_SPECS` order, each array's
`name|dtype.str|[shape]` line and its raw bytes; then the `strings.json` bytes; then the JSON
of `meta["params"]` with sorted keys and compact separators (which stands for the params
exactly because `validate()` requires them JSON-native). Nothing else in `meta` enters it
(not `created`, `build_id`, `pipeline_version`, `sources`, `counts`, nor the top-level
`region` and `crs_epsg`, though a pipeline build's `params` repeat those two, so they enter
through them), so rewriting the same build reproduces it. The geometry is not in it: a route
depends on the arrays alone, and the geometry's own digest is `meta["geometry_version"]`
(**Geometry**). A test pins its value for a small build (`tests/graph/test_store.py`);
changing the array order, a dtype or the hash recipe changes every version and is a format
change (tier A, a new build id).

The version hashes bytes, so it is exact per platform: projected coordinates from PROJ can
differ in their last bits between machines, and a build made on macOS and on Linux from the
same input may carry different versions. Routes do not depend on those bits (the engine works
on x/y and lengths), but a golden test should record the version of the build it ran on. A
test build's positions are given in metres (`make_graph`), so its version is the same on every
machine.

## Geometry

The edges' shapes, which only the drawing of a route reads, live apart from the arrays:
`geometry/` in the build directory, `graph/geometry.py`'s `Geometry` (`GEOMETRY_SPECS`):

| Array | dtype, shape | Meaning |
|---|---|---|
| `geom_indptr` | i32[m+1] | edge e's interior points are `geom_dxy[geom_indptr[e]:geom_indptr[e + 1]]` (0 or more each; at most 2³¹ − 1 in all, which `encode` holds to) |
| `geom_dxy` | i16[*, 2] | the steps (dx, dy) in whole metres in `meta["crs_epsg"]` |

An edge's line runs from its tail's position to its head's (`node_x`, `node_y`): those ends are
not stored. Its interior points are steps, the first from the tail, each next from the point
before it, every step the difference of two points' offsets from the tail rounded to the metre
(`np.rint`): adding them up gives each point's rounded offset without drift, so a point decodes
to within half a metre of where it was on each axis (0.71 m). A step longer than `MAX_STEP_M`
(32,000 m) on either axis is split by points evenly placed on its segment, so every step fits
an int16. `pipeline save` simplifies each shape first, Douglas–Peucker to
`config.GEOMETRY_SIMPLIFY_M` (2 m) with its ends kept, after `enrich` measured the lengths and
bearings on the whole shape (`docs/reference/pipeline.md`, **save**): a drawn line lies within
2.71 m of the road. On Portland the 729,489 interior points become 174,003, and the geometry
is 1.4 MB.

- `encode(data, points, indptr) -> Geometry`: each edge's line, `points[indptr[e]:indptr[e +
  1]]` in projected metres; a line that does not run from its tail's position to its head's
  exactly is a `ValueError`. `straight(m)`: no interior points.
- `edge_xy(data, geometry, edges) -> (points, counts)`: the points along each of `edges` in
  projected metres, its ends included, edge k's `counts[k]` of them in a row; `edge_points`
  the same in (lon, lat), projected back with one transformer per CRS, so an edge's ends are
  `node_lon`, `node_lat` bit for bit and consecutive edges share their junction point exactly.
  `lonlat_to_xy` projects lon/lat into the build's CRS (the share image's extent), and
  `xy_to_lonlat` back with the same transformer, a node's point to its `node_lon`, `node_lat`
  bit for bit (a route document's marks, `docs/reference/presentation.md`, **Marks**).
- `geometry_version(geometry)`: the sha256 over each array's `name|dtype.str|[shape]` line
  and raw bytes, as `graph_version`'s recipe, pinned by a test (`tests/graph/test_geometry.py`).
- `read_geometry(path, m, mmap=True)`: memory-maps the files read-only and checks their dtypes
  and shapes; a missing file raises `MissingArray` ("the build at PATH lacks its geometry
  (geom_dxy): rebuild it with `worst-route pipeline save --force --region R`").

Who reads it: the API process (a stored route's document, GeoJSON and share image, a job's
events, `Loaded.geometry`), the CLI's `plan --geojson` and, when a places index exists, `plan
--out`'s states and towns, `pipeline qa`'s twin layer. The engine, the validator and a job's
worker never import the module: a test plans in a fresh interpreter, as a worker does, on a
build whose `geometry/` is gone, and checks its `sys.modules`; the CLI plans and validates on
such a build too. Where drawing will need it, a build without its geometry fails at once:
`serve` reads it before it listens, and `plan` before its search when it will write a GeoJSON,
or route.json with a places index.

## Backends

`GraphBackend` (`backend.py`) is what the engine programs against; M1's surface is four calls
and a `name` (`"python"`, `"csr"`), which a planned route records, and the alphabet route (M3)
added a fifth, the tree search (**The tree search**). Every backend must give
identical results on identical inputs; `PythonBackend` is the reference, `CsrBackend` the
production one (**The CSR backend**), and networkx the oracle the tests check both against.

**Masks.** A node may be entered iff `node_mask[v]` (default: every node) and not
`forbid_nodes[v]`. `src` and `dst` obey the same rule, so a caller that forbids the route's used
nodes clears the endpoints of the search from `forbid_nodes`. An edge may be used iff not
`banned_edges[e]` and both its endpoints may be entered. Masks are `bool` arrays of length n or
m; `weights` are float64, one per edge, non-negative and not NaN. The API takes bool masks;
the kernels' uint8 views of them are `CsrBackend`'s internal business. Every backend raises
`ValueError` for a node id outside [0, n) (no negative-index wraparound), a mask or weights of
the wrong dtype or length, and NaN or negative weights, through the checks `backend.py` shares
(the backend's `WeightCheck`, `check_ends`, `enterable`, `banned_mask`, in that order), so the
same bad argument raises the same error on either backend. Each backend's `WeightCheck` checks
the dtype and shape on every call but scans the values, O(m), only for a weights array object
other than the last one that passed (held by a weak reference). A caller that changes a checked
array in place is not checked again; the engine never does, and the random-weight strategy makes
a new array. The scalars are converted the same way on every backend too: `as_cost` makes
`max_cost` a Python float before it is compared, so a NumPy float32 budget compares in float64;
`as_limits` passes `max_depth` and `max_paths` through `operator.index` (a float, `inf`
included, raises `TypeError`) and clamps them to the int64 range (a huge limit is no limit).

- `dijkstra(src, dst, weights, *, node_mask, forbid_nodes, banned_edges, max_cost=inf) -> Path | None`:
  a cheapest path, or None when `dst` cannot be reached at a cost of at most `max_cost`. The
  search never visits a node whose tentative cost exceeds `max_cost`, `src` at cost 0 included,
  so a negative `max_cost` returns None even when `src == dst`.
- `dijkstra_edges(src, dst, weights, *, entry_edge=-1, exit_edge=-1, node_mask, forbid_nodes, banned_edges, max_cost=inf) -> Path | None`:
  the transition-aware search, below.
- `dijkstra_edges_tree(src, weights, *, entry_edge=-1, node_mask, forbid_nodes, banned_edges, final_edges=None, max_cost=inf, banned_turn_classes=0) -> EdgeTree`:
  the same search without a destination, below (**The tree search**).
- `simple_paths_dfs(src, dst, *, entry_edge=-1, exit_edge=-1, node_mask, forbid_nodes, banned_edges, max_depth, max_paths) -> list[Path]`:
  the simple paths of at most `max_depth` edges whose every turn the transition table allows
  (from `entry_edge` into the first edge and from the last edge into `exit_edge` included, when
  given), in ascending edge-id order (lexicographic in their edge ids), stopping after
  `max_paths`.
- `largest_scc_mask(edge_mask=None) -> bool[n]`: the nodes of the largest strongly connected
  component of the graph restricted to `edge_mask`; on a tie, the component completed first,
  the one scipy's `connected_components` (`connection="strong"`) labels first. `_scc_labels`
  labels the components as scipy did for this call until 2026-11-01, label for label: its
  iterative Pearce search ported step for step, in pure Python, over scipy's form of the graph
  (each node's heads once, ascending), roots in ascending order and a node's unreached heads
  pushed in descending order, so the lowest is visited first (pushed ascending, the labels
  differ from scipy's on a third of random graphs). Nothing that serves a route calls it: 43 ms
  on Portland, 0.18 s on Oregon, 2.4 s on `us-t3` (scipy's 3 ms, 17 ms, 0.18 s).

A `Path` is `(nodes, edges)`, both int32, `edges[k]` going from `nodes[k]` to `nodes[k + 1]`;
from a node to itself (when it may be entered) it is that node and no edge, for both searches.

**The tie rule**, which every backend reproduces: the heap orders entries by (cost, node), so
equal costs pop the lower node id first; a node's out-edges are relaxed in `out_edges` order
(ascending edge id); only a strictly smaller cost replaces a node's predecessor. Of two
equal-cost routes, the one that reaches the shared node first keeps it; of equal parallel
edges, the lower id.

**The transition-aware search** (`dijkstra_edges`; `docs/PLAN.md` §3.5) searches edge states,
so it sees the transition table: a turn from e into f is allowed iff head(e) has no rows or
lists (e, f), and its class is not among `banned_turn_classes` (below). `entry_edge` is the edge the path continues (it must end at `src`), `exit_edge`
the edge it hands over to (it must start at `dst`); both are optional (−1), and neither is
driven, banned or costed. A bad one is a `ValueError`.

- **Start**: the out-edges of `src` the turn from `entry_edge` allows (all of them without one).
- **Moves**: from e, each f of `out_edges(head(e))` in ascending id, iff the turn (e, f) is
  allowed, f is not banned, head(f) may be entered and is neither `src` nor head(e). No state
  returns to the start, and none is a self-loop.
- **End**: a state into `dst` whose turn into `exit_edge` is allowed (any, without one). A state
  into `dst` that cannot hand over is a dead end, never expanded: going on would pass `dst`
  twice.
- **Ties**: the heap orders entries by (cost, edge id); only a strictly smaller cost replaces a
  state's predecessor; `max_cost` is inclusive and bounds every state kept.
- **The answer** is the path of the first end state popped, or None. **It is a walk, and may
  enter a node twice**: node-simplicity cannot be said in an edge-state search, and a turn table
  can make the cheapest legal way a loop round a block. Such a walk is illegal for a route, so a
  caller that needs a simple path checks, as for the tree search (`engine/search.py` does, and
  searches again: `docs/reference/engine.md`, **Searches**). It never enters `src` again (no
  state returns to the start) and passes `dst` only at its end (a dead end is never expanded).
- From `src` to itself: the one-node path, unless both turn edges are given and their turn is
  not allowed at `src` (None).

On a build with no transition rows it finds a path whenever `dijkstra` does, at the same cost
(Dijkstra's tree of edge states never repeats a node when every turn is allowed), and the same
path whenever the two tie rules pick the same one (always when the cheapest path is unique).
The engine calls it on builds that have rows, and on any build when a turn constraint refuses
or avoids a class of turns (`docs/reference/engine.md`, **Searches**), so on every other build,
without constraints, the backends' node-state results stay what they were.

**The tree search** (`dijkstra_edges_tree`; the alphabet route's, `docs/reference/engine.md`,
**Alphabet**) is the transition-aware search from `src` with no destination: the same start
states (from `entry_edge` when given), moves, turn checks, masks and turn classes, run until no
state is left. It answers an `EdgeTree`, `(edges, costs, parents)`: every state it settles, in
the order it settles them (int32), each one's cost (float64), and each one's parent, the
position in these arrays of the state before it on its path (int32, −1 for a start state). A
state is settled after its parent, so a path is read back by following the parents.

- **Ties**, as `dijkstra_edges`'s: the heap orders entries by (cost, edge id), out-edges are
  relaxed in ascending edge id, and only a strictly smaller cost replaces a state's parent. So
  the settle order is ascending (cost, edge id), and of two equal-cost ways into a state the
  one whose last state settled first keeps it.
- **`max_cost`** is inclusive: a state costing exactly `max_cost` is settled, one costing more
  is never kept; a negative `max_cost` settles nothing. A start state is kept at its own
  weight within `max_cost`, an infinite one included, as `dijkstra_edges` keeps it.
- **`final_edges`** (a bool mask over edges, default none): a final state is settled, with its
  cost and parent, but never expanded. The search may end on it, and never goes on from it.
- No state enters `src`, and none is a self-loop. A path may still enter another node twice (a
  state is an edge; a cheapest way into an edge can come back through its head), so a caller
  that needs a simple path checks.
- An empty tree when `src` may not be entered. The same `ValueError`s as `dijkstra_edges`
  (`entry_edge` must end at `src`), and one for a `final_edges` of the wrong dtype or length
  (`final_mask`, shared).

**Turn classes.** `dijkstra_edges` and `simple_paths_dfs` take `banned_turn_classes`, a set of
class bits (default 0, which changes nothing): a turn from e into f whose class is in the set is
refused like one the table does not list, the turn from `entry_edge` and into `exit_edge`
included, and `src == dst` too. The class is `TURN_CLASS[(edge_bearing_out[f] −
edge_bearing_in[e]) mod 360]` (`backend.py`; the bearings are whole degrees), a 360-entry table
of the rule `docs/reference/engine.md` describes (**Turns**): straight 0 (never banned), left
1, right 2, U-turn 4. A set outside [0, 7] is a `ValueError` (`as_turn_classes`, shared).

**`PythonBackend(data)`** turns the adjacency into Python lists once (tens of MB at Portland
scale), and the transition table into a list of which nodes have rows and a set of allowed
`in_edge * m + out_edge` keys (a row's node is its in-edge's head; the set is only asked for
membership, the answer a binary search on the sorted rows gives); it combines the node masks
into one array per search with a NumPy operation, and reads masks and weights only for the
edges a search touches, so a small bounded search does not pay
for the whole graph. Measured on the dev machine: a search across a 200 × 200 `grid_graph`
(40,000 nodes, 159,200 edges, every node settled) takes 20–50 ms of CPU by the machine's load,
0.13–0.3 µs per relaxed edge; a search ten blocks from the centre of the same grid takes under
1 ms (the side-by-side measurement is under **The CSR backend**).

## The CSR backend

**`CsrBackend(data)`** (`csr_backend.py`) runs the Numba kernels of `kernels.py` over the
build's CSR arrays (`out_indptr`, `out_edges`, `edge_u`, `edge_v`); it copies nothing, and
precomputes the transition table once into the two arrays the turn kernels read:
`trans_indptr` (int64[n+1], from `searchsorted` on the sorted `trans_node`: node v's rows are
`trans_indptr[v]:trans_indptr[v + 1]`) and `trans_key` (int64[k], each row's
`in_edge * m + out_edge`, ascending within a node's rows), both read-only, 0.6 MB on Portland.
Each call runs the shared checks in the reference's order, answers the trivial cases as the
reference does (a negative `max_cost`, an end that may not be entered, `max_paths <= 0`,
`src == dst`, where both turn edges given must make an allowed turn), then hands the kernel the
arrays, the weights, and the masks as zero-copy uint8 views (`mask.view(np.uint8)`).
`dijkstra_edges` is always the edge-state kernel, and `dijkstra_edges_tree` its kernel without a
destination; `simple_paths_dfs` is the turn kernel on a
build with transition rows or when a turn edge is given, and the plain kernel otherwise, where
every turn is allowed and the two give the same paths. `largest_scc_mask` is the reference's,
shared.

- `dijkstra_masked(out_indptr, out_edges, edge_u, edge_v, weights, enter_u8, banned_u8, src,
  dst, max_cost) -> (found, nodes, edges)`: the tie rule exactly, with a binary heap of
  (cost, node) entries written as two arrays and their sift loops, stale entries skipped when
  popped. Each edge is relaxed at most once, so the heap holds at most m + 1 entries.
- `simple_paths_dfs(out_indptr, out_edges, edge_v, enter_u8, banned_u8, src, dst, max_depth,
  max_paths) -> (offsets, nodes, edges)`: the reference's enumeration on an explicit stack (the
  path's nodes, its edges, and per depth the next out-edge to try), sized by
  min(max_depth, n). The paths come back packed: path p is `edges[offsets[p]:offsets[p + 1]]`
  and `nodes[offsets[p] + p : offsets[p + 1] + p + 1]`, in buffers that double when full.
  `CsrBackend` unpacks them into the list of `Path`s.
- `dijkstra_edges_masked(out_indptr, out_edges, edge_u, edge_v, weights, enter_u8, banned_u8,
  trans_indptr, trans_key, bearing_in, bearing_out, turn_class, banned_turns, src, dst,
  entry_edge, exit_edge, max_cost) -> (found, nodes, edges)`:
  the transition-aware search exactly (**The transition-aware search**), with a binary heap of
  (cost, edge id) entries in two arrays. With non-negative weights each state is pushed at most
  once (its cost is its predecessor's plus its own weight, and states pop in non-decreasing cost,
  so its first push is its last): at most m entries, and at most 164,473 pushes in a search on
  Portland (m = 168,167). The heap still starts at 64 entries and doubles when full
  (`_heap_push`), so a small search allocates little and memory stays safe if a weights array
  changed in place after its cached check (**Backends**) holds a negative weight, which breaks
  the bound. Each edge state has one uint8 (not reached, reached, settled); its cost and
  predecessor are left uninitialised until it is reached, so a search does not fill O(m) floats:
  on Portland that fill would cost 18 µs a call, the state array costs 1.4 µs. A start state is
  kept at its own weight within `max_cost`, an infinite one included, as the reference keeps it;
  a relaxed state only on a strictly smaller cost. The first end state popped is the answer, its
  walk as it is, a node entered twice included.
- `dijkstra_edges_tree(out_indptr, out_edges, edge_v, weights, enter_u8, banned_u8, final_u8,
  trans_indptr, trans_key, bearing_in, bearing_out, turn_class, banned_turns, src, entry_edge,
  max_cost) -> (edges, costs, parents)`: the tree search exactly (**The tree search**), with the
  same heap and state array as `dijkstra_edges_masked`. Its answer and a slot per edge (each
  settled state's position, read only for settled states, so never filled) are O(m) buffers
  left uninitialised; the answer is copied out at its length.
- `simple_paths_dfs_turns(out_indptr, out_edges, edge_v, enter_u8, banned_u8, trans_indptr,
  trans_key, bearing_in, bearing_out, turn_class, banned_turns, src, dst, entry_edge, exit_edge,
  max_depth, max_paths) -> (offsets, nodes, edges)`: `simple_paths_dfs` with every turn
  checked, from `entry_edge` (when given) or the path's last edge into each edge it tries, and
  from an edge into `dst` into `exit_edge` (when given). `CsrBackend` uses it on a build with
  rows, with a turn edge, or with turn classes banned.
- `turn_allowed(trans_indptr, trans_key, edge_v, e, f) -> bool`: the turn from e into f at
  head(e): allowed when the node has no rows, else a binary search for `e * m + f` among its rows
  (at most 304, at a Portland super-junction). Both turn kernels call it, and so does
  `CsrBackend` for `src == dst`.
- `turn_class_allowed(bearing_in, bearing_out, turn_class, banned_turns, e, f) -> bool`: the
  turn's class is not in `banned_turns`; `True` at once when the set is empty. The turn kernels
  call it beside `turn_allowed`, with the build's bearings and `TURN_CLASS` as they are
  (zero-copy, read-only int16 and uint8).

**The parity contract.** For arguments of the declared types (int node ids and limits, a real
`max_cost`, float64 weights, bool masks), `CsrBackend` returns `PythonBackend`'s results byte
for byte: the same paths in the same int32 arrays, the same ties, the same Nones, the same
paths in the same order, the same errors. The semantics in `backend.py` are the specification;
a case where the two legitimately differ is a bug in one of them, never a choice. Floats are added one at a time
in the same order as the reference (no `fastmath`, no `parallel`), so equal inputs give equal
costs. The tests (**Tests**) hold this, and so do the golden route and the engine's same-seed
tests, which run on both backends.

**Compilation.** Each kernel is compiled once, eagerly, when `kernels.py` is imported, for one
signature whose arrays are read-only and of any layout. Memory-mapped builds, read-only cached
weights and fresh writeable masks therefore share one specialization. `cache=True` keeps it in
`src/worst_route/graph/__pycache__/` (gitignored). The first import after a change to
`kernels.py`, or in a fresh clone or CI run, compiles its eight functions (the five kernels,
the two turn checks and the heap's push): about 3 s on the dev machine before the tree kernel
was added (1.4 s for the two node-state kernels alone). Every later process loads them from the cache in about 0.3 s.
`NUMBA_DISABLE_JIT=1` runs the kernels as plain Python for debugging: slow on a real build, but
the whole test suite passes that way. `NUMBA_BOUNDSCHECK=1` turns an out-of-range index into an
`IndexError`, but only with a `NUMBA_CACHE_DIR` of its own: the cache index does not key on
the flag, so with the default cache the unchecked kernels are loaded and the flag does
nothing. Without it, the kernels trust the build: `write_build` validates it deeply, and
the backend checks every id and mask it is given.

**Per-call cost.** Each search allocates its own work arrays (distances, predecessors, settled
flags, heap; the DFS's on-path flags and stack), O(n) per call, as the mask combination
already is: the kernel's allocations and dispatch take 5–8 µs. With the weights array already
checked, a one-edge search costs 10 µs on the reference and 14 µs on `CsrBackend` on Portland
(5.5 and 10 µs on `grid_graph(200, 200, 100)`). Before the weight check was cached, its O(m) scan
took 30–32 µs of every call on both backends. The edge-state kernel's O(m) work is one uint8
array (above), and a transition-aware search to a neighbour costs 6 µs on the reference and
7 µs on `CsrBackend`. The engine's searches are mostly small: in Powell's → Reed on Portland
(`docs/reference/engine.md`, **On Portland with `CsrBackend`**) a `dijkstra_edges` call averages
64 µs on the reference and 37 µs on `CsrBackend`, about 40% of which is the weight check scanning
each new random-weight array, which both backends pay.

**Dependency.** `numba>=0.68`, locked at 0.68.1 with llvmlite 0.50.0. Both have CPython 3.12
wheels for macOS arm64, manylinux x86_64 and aarch64, and Windows, but none for macOS x86_64:
on an Intel Mac, `uv sync` would have to build them from source. numba 0.68 requires
numpy < 2.6, so a numpy upgrade past 2.5 waits for numba.

**Measured** on the dev machine (CPU time, median of 7):

| On `grid_graph(200, 200, 100)` | `PythonBackend` | `CsrBackend` | |
|---|---|---|---|
| `dijkstra` corner to corner (every node settled) | 21 ms (0.13 µs per edge) | 1.7 ms (0.010 µs per edge) | 12.6× |
| `dijkstra` ten blocks from the centre, `max_cost` 1 km | 0.14 ms | 0.04 ms | 3.4× |
| `dijkstra` to a neighbour (fixed cost; the weights already checked) | 5.5 µs | 10 µs | — |
| `simple_paths_dfs`, a 4-edge window in a 400 m ball, depth 25, 5000 paths | 33 ms | 2.7 ms | 12.1× |
| **On Portland** (build e4b3b4ad90db: 65,810 nodes, 168,167 edges) | | | |
| `dijkstra` south-west to north-east corner (every node settled) | 34 ms (0.20 µs per edge) | 4.1 ms (0.025 µs per edge) | 8.3× |
| `dijkstra` to a neighbour (fixed cost; the weights already checked) | 10 µs | 14 µs | — |
| **Transition-aware, on Portland** (the same build: 11,378 rows at 657 nodes; load average 4–5) | | | |
| `dijkstra_edges` south-west to north-east corner | 129 ms | 17 ms | 7.6× |
| `dijkstra_edges` 800 m across downtown, `max_cost` 1.5 km | 0.36 ms | 0.035 ms | 10.3× |
| `dijkstra_edges` to a neighbour (fixed cost; the weights already checked) | 6 µs | 7 µs | — |
| `simple_paths_dfs` with an entry edge, a 4-edge window through a 228-row super-junction in a 400 m ball, depth 25, 5000 paths | 70 ms | 7.3 ms | 9.5× |
| the same, a 4-edge window downtown (no rows in its ball; 745 paths) | 6.4 ms | 0.61 ms | 10.4× |

## Choosing a backend

`make_backend(data, name=DEFAULT_BACKEND)` (`backends.py`) makes the backend `name` asks for:
`"csr"`, `"python"`, or `"auto"`, which is `CsrBackend` when numba imports and `PythonBackend`
otherwise (`csr_backend` is imported only when asked for); `backend_name(name)` says which
one it makes without making it (auto: csr when numba imports; it imports numba, not the kernels,
so a broken numba counts as none and nothing compiles), as `serve` and the desktop app ask before
the backend exists. `config.DEFAULT_BACKEND` is
`"auto"`; `config.BackendName` lists the names. The backends give the same results, so the
choice changes only the speed. `plan_route` uses
`make_backend`'s default when it is given no backend, and `worst-route plan` and `validate`
take `--backend` (`docs/reference/engine.md`).

## Loading builds

`load_build(data_dir, region, build_id="latest", *, backend=DEFAULT_BACKEND) -> Loaded(data,
path, backend_name)` (`loader.py`) is how the API process and each job's worker process
load a build: `read_build` of `<data_dir>/graphs/<region>/<build_id>/`, every array
memory-mapped (the workers share the OS page cache), and the build's directory. Its
`SpatialIndex` (`Loaded.spatial`) and `make_backend(data, backend)` (`Loaded.backend`) are made
on first use and kept: the CSR backend's first imports the kernels, which compile then when their
cache does not hold them (**Compilation**). The API process makes both in a thread once it starts
(`api/app.py`, `warm_builds`: the spatial indexes first), so it listens without waiting for them; a
request that needs one before then waits for the kernels' import (Python's import lock) or makes
its own index, and either serves. `Loaded.geometry` reads the build's geometry on first use
(`read_geometry`, memory-mapped) and keeps it: the API process draws with it, a worker never
asks.
`"latest"` is the id in `graphs/<region>/latest`, read on every call, so the cache never pins
an old build under that name. The two builds used last stay loaded (`lru_cache(maxsize=2)` keyed
by data directory, region, build id and backend: the region the API serves, and one other a
stored route was planned on). A missing `latest` or `meta.json` raises `BuildNotFound` (a
`FileNotFoundError`) naming the step to run, and so does a build missing an array, with
`MissingArray`'s line; `make_backend`'s `ImportError` for `csr` without
numba passes through, from `Loaded.backend`. On Portland, in a fresh process, a load takes about
0.1 s with its imports (0.35 s when it made the index and the backend too), and the index and the
backend about 0.25 s more on first use, the kernels loaded from their cache.
The CLI's `plan` and `validate` keep their own loading, with its one-line errors.

## Spatial

`SpatialIndex(data)` builds, once per build, a grid of every node's (x, y) and a pyproj
transformer from EPSG:4326 to `meta["crs_epsg"]` (`always_xy=True`). Masks filter at query
time, so one grid serves every request.

**The grid**, which replaced scipy's `cKDTree` (2026-11-01; the app no longer carries scipy,
`docs/reference/desktop.md`, **Building it**): square cells of side sqrt(area / n) over the
nodes' bounding box, at least max(width, height) / n and 1 m, so about n cells and never more
than 3n + 1, the nodes sorted by cell row by row (`order`, int32) with each cell's offset
(`start`, int32): 8 bytes a node, 17 MB on `us-t3`. A ball, the nodes within r of a point, is
read from the cells the square around it covers and a cell more on each side, so rounding at
its edges never drops a node, each row's run of cells one slice of the sorted nodes; a node is
within when dx * dx + dy * dy <= r * r in float64, unfused, its coordinates less the point's.
Only balls were ever asked of the tree (`snap`'s nearest k are its own sort of a ball, by
distance then node id), and this is the tree's leaf test, so the balls are the tree's, node for
node: 10,000 random ones on every fixture build, on Portland, Oregon and `us-t3` (the tests
below). The one difference is a node within a rounding of r: scipy on Apple Silicon fuses the
test's last multiply-add, and of 7,500 radii set at a node's own distance on Portland it
answered 438 otherwise than the unfused test, every one by the fusion; no radius the engine
or the API uses is one. An empty build's grid is one empty cell. Measured against the tree
(2026-11-01, the dev machine at a load average of 3–4; Portland, Oregon, `us-t3`): the index
built in 4.9, 18.5 and 219 ms (the tree's 7.8, 30 and 347); a snap 0.36, 0.08 and 0.04 ms (0.42,
0.15, 0.19); a 3 km bubble 23, 14 and 22 µs (66, 26, 19); a 400 m ball, the local exhaustive
detour's, 8.3, 9.5 and 21 µs (5.8, 5.9, 13), the one place it is slower; a request's masks the
same, 0.7, 2.0 and 17 ms.

- `project(lat, lon) -> (x, y)` in the build's CRS.
- `snap(lat, lon, role, node_mask=None, k=10, max_radius_m=5000, exclude=None) -> Snap`,
  `role` one of `"start"`, `"via"`, `"end"`. The candidates are the nodes in `node_mask` and
  not in `exclude` within `max_radius_m`, nearest first, ties by node id, at most `k` of them;
  the first that can play the role wins. Roles are judged within the mask, self-loops ignored:
  a start needs an out-neighbour in the mask; an end an in-neighbour; a via stop an
  in-neighbour u and an out-neighbour w with u ≠ w, so the route can pass through without
  turning back (the build-wide `node_pass_through` is not used: a mask can leave a through
  node with a single neighbour). Excluded nodes are never chosen but still count as
  neighbours. Both masks are bool arrays of length n, else `ValueError`.
  `Snap(node, moved_m, lat, lon, note)`: the node, the distance from the query point, the
  node's position (its derived lon/lat rounded to 7 decimals, as OSM writes positions), and
  `note` None when the nearest candidate was taken, else one of
  "moved N m to the nearest through street" (via), "… street you can leave by" (start),
  "… street you can arrive by" (end). Nothing qualifying raises `SnapError`.
  **Re-snapping** (a leg that cannot be built, `docs/PLAN.md` §3.2): snap again with the
  nodes already tried in `exclude`, keeping the same `node_mask`. The next candidate is then
  the nearest, so its `note` is None unless a role rejects it; the caller says why the stop
  moved.
- `leash_mask(stops_xy, leash_m) -> bool[n]`: the nodes within `leash_m` of the polyline
  through the stops (consecutive duplicate stops dropped; a single stop is a point), as
  shapely measures them against one geometry, to the last bit. NumPy measures every node's
  distance to each segment (to its nearest point, one pass over the nodes a segment), and the
  nodes whose distance lies within `LEASH_BAND_M` (1 m) of the leash are measured again by
  shapely, which decides them; the two formulas differ by nanometres. On the national T1 build
  (887,737 nodes) a two-stop leash takes 4–5 ms where shapely over the stops' bounding box grown
  by the leash took 22–159 ms, a three-stop one 8–10 ms; on Oregon 1 ms against 24–38 ms, the
  same masks on every request measured.
- `bubble_mask(stops_xy, radius_m) -> bool[n]`: the nodes within `radius_m` of any stop, a
  ball of the grid's around each.
- `nodes_within(center, radius_m, node_mask) -> intp[]` and `edges_from_within(center,
  radius_m, node_mask) -> intp[]`: the detours' balls, the nodes of the mask within `radius_m`
  of a point and the edges leaving them, ascending. A node is within when dx² + dy² ≤ r², the
  grid's own expression, so the nodes are exactly `bubble_mask`'s within the mask (equal on
  every radius the tests try, lattice nodes on the boundary included). They scan the mask's
  nodes and out-edges, kept for the last mask seen (compared by content, after the mask's dtype
  and length are checked), instead of querying the whole build: on Portland a request holds
  about 7,600 of 65,810 nodes, and the scan costs 0.04 ms where a KD-tree query covering the
  area cost 1.4 ms (it returned its hits as a Python list). A mask of the wrong dtype or length
  is a `ValueError`.
- `active_mask(tier_mask, leash, bubbles) = leash & (tier_mask | bubbles)`: a request's node
  mask (`docs/PLAN.md` §2.6).

## Test builders

`worst_route.graph.testing` ships in the package so every area's tests can build graphs by
hand; nothing outside tests uses it.

- `make_graph(nodes, edges, *, twins=(), transitions=(), crs_epsg=32610, brands=None) ->
  GraphData`.
  `nodes` are (x, y) in metres in `crs_epsg`; lon/lat are derived from them, as in every build.
  `edges` are `(u, v, length_m)` tuples or `Edge(u, v, length_m=None,
  highway=RESIDENTIAL, speed_kmh=40.0, name=None, ref=None, flags=0, toll_booths=0,
  level_crossings=0)`; a missing length is the straight-line distance. `twins` pairs edges and
  `transitions` lists allowed `((a, v), (v, b))` pairs, each edge named by its (u, v); naming
  no edge or several parallel ones raises `ValueError`. Edge ids are the positions after a
  stable sort by (u, v), as in a real build.
  Defaults for everything else: time = length / speed; the speed table the distinct speeds;
  tier from `TIERS`; the link flag set for `*_LINK` classes; identity = ref, else name (not
  normalised); unit = the edge id; bearings from the coordinates (in = out); one member per
  node with OSM id = node index + 1; pass-through from the topology; kind plain, no delay, no
  roundabout rings; tier masks: edges of tier ≤ t
  and their endpoints (no SCC); no brands unless `brands` maps names to nodes (ids in name
  order, no wikidata id, one location at each node, the nodes sorted and each once); meta region
  and build id `"test"`, format 4, a fixed `created`, and `graph_version` computed.
  The result is validated and read-only. Its edges are straight: `write_build` writes it with
  `straight(m)` unless given a geometry, which `encode` makes from lines through the nodes.
- `grid_graph(rows, cols, spacing_m, *, oneway_rows="", oneway_cols="") -> GraphData`: a
  lattice of residential streets at `GRID_ORIGIN` (UTM 10N, near downtown Portland); node
  `r * cols + c` at `(x0 + c * spacing, y0 + r * spacing)`, row 0 the southmost. One character
  per row or column: `+` one-way east (rows) or north (columns), `-` the opposite, `.` two-way;
  `""` is two-way everywhere. A grid with extras (a cul-de-sac, a stub) is built with
  `make_graph`.
- `edge_id(data, u, v) -> int`: the one edge u → v, or `ValueError`.

## Tests

`tests/graph/`: `test_data.py` (every malformation `validate` rejects, rings at a plain node,
malformed brand runs, the speed table and another format among them), `test_testing.py` (the
builders, brands included; lon/lat the inverse projection, worked out once, read-only),
`test_store.py` (round trips, a build with brands among them, its files, geometry/ included;
the version, its pin, and its change with a brand's nodes, locations, name or wikidata id;
refusals, an earlier format's rebuild line among them), `test_geometry.py` (hypothesis: steps
round trip to the metre, ends exact; long steps split on their segment; the ends' lon/lat
the nodes'; straight edges; refused lines and malformed geometries; the files beside the
arrays and outside `graph_version`; the digest's pin; a build read and loaded without its
geometry, which is asked for only on use), `test_backends.py`, `test_edge_search.py`,
`test_spatial.py` (snapping roles, masks, `exclude` and notes; leash and bubble masks, the leash
shapely's at its boundary, nodes 1 µm either side of it included; the
masked balls against `bubble_mask`, radii on lattice nodes included; an empty build's index;
and, `slow`, the grid against scipy's KD-tree (`scipy_parity.py`): 10,000 random balls node for
node, then 1,000 snaps (random roles, masks, exclusions and k) and bubble masks against an
index whose balls are the tree's, on a lattice of whole metres, a cloud with a dense cluster
and duplicate nodes, a line with no height, and every OSM fixture's build), `test_parity_data.py`
(`data`: the same on Portland's, Oregon's and `us-t3`'s builds, balls up to 40 km), and `test_loader.py`
(`latest`, the two builds kept, memory-mapped arrays, missing builds, a build missing an array,
the geometry read on first use and kept).

`test_backends.py` runs the reference's tests on both backends through the `backend_type`
fixture:
- hypothesis parity with networkx for the node-state calls and the SCC mask, 200 examples each;
- the SCC labels against scipy's (`scipy_parity.py`), label for label, and the mask against the
  component scipy labels first among the largest, on 200 hypothesis graphs (self-loops,
  parallel edges, ties), 300 random graphs of up to 400 nodes and every OSM fixture's build,
  over every edge and a random part of them (`test_parity_data.py` again on Portland, Oregon
  and `us-t3`, over every edge and 70% of them);
- the exact path against a heap-free implementation of the tie rule, on graphs where equal
  costs are common, also 200 examples;
- the tie rule, masks, `max_cost`, bad arguments, and depth and count limits on crafted graphs.

Then its parity tests compare the two backends byte for byte:
- 300 hypothesis graphs with weights in {0, 0.5, 1, 2, inf}, masks, budgets, and depth and
  count limits, both searches compared;
- 300 random searches on a 30 × 30 grid with one-way streets and tied weights;
- the 8512 simple paths across a 5 × 5 grid, with and without masks and limits;
- a float32 `max_cost` against a 0.1000000015 edge, limits of 2**63, 2.5 and `inf` (the same
  results or the same `TypeError`), and the weights scanned once per array.

It also tests `make_backend`'s choices, `auto` taking CSR on a build with turn tables among
them.

`test_edge_search.py` tests `dijkstra_edges` and the DFS's turns on both backends, through
the same fixture: on random graphs with random transition tables, the exact path against a
heap-free implementation of the edge-state tie rule and the cost against networkx over the edge
states; without rows, the cost of `dijkstra` and its path when the cheapest is unique; the DFS
against networkx's simple paths filtered by turns; crafted entry and exit edges, a cheapest
legal walk entering a node twice (the answer as it is), no return to the start, the dead end at
`dst`, bad arguments. Then its
parity tests compare the two backends byte for byte:
- 300 hypothesis graphs with and without transition tables, turn edges, weights in
  {0, 0.5, 1, 2, inf}, masks, budgets (`src == dst` included), and depth and count limits, both
  searches compared;
- 300 transition-aware searches on a 30 × 30 grid with one-way streets, tables at a third of
  its junctions and tied weights, with turn edges, masks and budgets (heaps that grow), the
  walks among the answers that enter a node twice (21 of 157) each checked against networkx as
  the cheapest legal walk;
- the simple paths across a 5 × 5 grid whose nodes 6, 12 and 18 allow only straight on, corner
  to corner, between two of those nodes through turn edges, and under a mask and limits;
- with banned turn classes: the same semantics tests (the tie rule, the cheapest legal walk,
  every simple path) on hypothesis graphs with random bearings (the class bands' edges often)
  and random sets, classed by the rule written out in the test; 300 hypothesis cases of both
  searches compared byte for byte, a set of 0 against the call without it; 300 searches on the
  one-way town with tables and a set drawn per search, the DFS compared too; a crafted junction
  driven round by three rights, a U-turn refused, bad sets refused;
- the tree search: on hypothesis graphs with transition tables, random bearings and classes,
  final edges and budgets, the exact answer (states, costs, parents) against a heap-free
  implementation of its tie rule, each state a legal move from its parent, and every state
  settled at its least cost within the budget, against networkx over the edge states; 300
  hypothesis cases compared byte for byte, with and without tables; 100 trees on the one-way
  town with tables, final edges, turn edges, masks, budgets and classes (trees of a few states
  to over 500); a final state settled and not expanded (and a path through the ring entering a
  node twice), a tree turning only where the table allows, empty trees, `max_cost` inclusive,
  bad arguments.

Run them with `uv run pytest tests/graph`.
