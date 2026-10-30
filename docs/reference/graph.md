# Graph

The routing graph of one build: its arrays (`GraphData`), the on-disk build format and its
`graph_version`, the `GraphBackend` protocol the engine searches through, the pure-Python
reference backend, the spatial helpers (projection, snapping, leash and bubble masks), and the
builders tests use to make small graphs by hand. Code: `src/worst_route/graph/`. The design it
implements is `docs/PLAN.md` §2.6–2.7, §3.2, §3.9–3.10; the conventions are `CLAUDE.md`.

| Module | What it holds |
|---|---|
| `data.py` | `GraphData`, `Strings`, `ARRAY_SPECS` (the format), `NodeKind`, `EdgeFlag`, `validate()` |
| `store.py` | `write_build`, `read_build`, `graph_version` |
| `backend.py` | the `GraphBackend` protocol and the `Path` type |
| `python_backend.py` | `PythonBackend`, the reference implementation |
| `spatial.py` | `SpatialIndex` (`project`, `snap`, `leash_mask`, `bubble_mask`), `Snap`, `SnapError`, `active_mask` |
| `testing.py` | `make_graph`, `grid_graph`, `Edge`, `edge_id`, `GRID_ORIGIN` |

`config.py` holds `HighwayClass` (the road classes, 0–13) and `TIERS` (the classes of T0–T4,
each tier containing the one before it, T4 every class).

## The arrays

`GraphData` is a frozen dataclass of NumPy arrays plus `strings` and `meta`. n is the node
count, m the edge count, T = 5 tiers, k the number of transition rows. Every array is
read-only: memory-mapped when read from disk, flagged read-only when built in memory, so a
stray write into the graph raises instead of corrupting every worker's view. `ARRAY_SPECS`
lists the arrays in a fixed order, the order of the fields, the files and the hash.

| Group | Array | dtype, shape | Meaning |
|---|---|---|---|
| nodes | `node_lon`, `node_lat` | f64[n] | position |
| | `node_x`, `node_y` | f64[n] | projected metres in `meta["crs_epsg"]` |
| | `node_kind` | u8[n] | `NodeKind`: 0 plain, 1 cluster (stage A), 2 interchange, 3 roundabout (stage B) |
| | `node_state` | i16[n] | state FIPS, −1 unknown |
| | `node_pass_through` | bool[n] | some u → v → w exists with u ≠ w (self-loops ignored), on the whole graph; snapping judges via stops within the request's mask instead |
| | `node_delay_s` | f32[n] | stop sign / signal delay |
| | `member_indptr`, `member_osm_ids` | i64[n+1], i64[] | original OSM node ids merged into each node (≥ 1 each); each id belongs to one node only, which the validator's proof from original ids relies on |
| edges | `edge_u`, `edge_v` | i32[m] | tail, head |
| | `edge_length_m`, `edge_speed_kmh`, `edge_time_s` | f32[m] | length and time finite and ≥ 0 |
| | `edge_highway` | u8[m] | `HighwayClass` |
| | `edge_tier` | u8[m] | the first tier whose classes contain the edge's class |
| | `edge_name`, `edge_ref`, `edge_identity` | i32[m] | index into `strings.names`, `.refs`, `.identities`; −1 none |
| | `edge_unit` | i32[m] | road unit (the two directions of a two-way road), in [0, m) |
| | `edge_flags` | u8[m] | `EdgeFlag` bits: 1 bridge, 2 tunnel, 4 toll, 8 roundabout, 16 link |
| | `edge_toll_booths`, `edge_level_crossings` | u8[m] | counts on the edge |
| | `edge_bearing_in`, `edge_bearing_out` | i16[m] | degrees clockwise from north, 0–359: arriving at v, leaving u |
| | `way_indptr`, `way_osm_ids` | i64[m+1], i64[] | original OSM way ids of each edge (≥ 1 each) |
| adjacency | `out_indptr`, `out_edges` | i64[n+1], i32[m] | each node's out-edges, ascending edge id |
| | `in_indptr`, `in_edges` | i64[n+1], i32[m] | each node's in-edges, ascending edge id |
| geometry | `coord_indptr`, `coords` | i64[m+1], f32[*, 2] | (lon, lat) along each edge, endpoints included (≥ 2 each) |
| | `osmnode_indptr`, `osmnode_ids` | i64[m+1], i64[] | original OSM node ids along each edge, endpoints included (≥ 2 each) |
| twins | `twin_indptr`, `twin_edges` | i64[m+1], i32[] | using an edge bans every edge in its row; symmetric, rows strictly ascending, never itself |
| transitions | `trans_node`, `trans_in`, `trans_out` | i32[k] each | allowed (in, out) pairs at a node, sorted by (node, in, out), no duplicates; a node with no rows allows every pair |
| tiers | `tier_node_mask`, `tier_edge_mask` | bool[T, n], bool[T, m] | tier membership (the pipeline's after per-tier SCC) |

**Edge ids are sorted by (u, v, original order).** Each node's out-edges are therefore one
contiguous run of ascending ids (`out_edges` is `0..m-1`), which the backends' tie rule relies
on.

`strings` is `Strings(names, refs, identities)`, three lists of str. `meta` is a dict with
`region`, `build_id`, `crs_epsg`, `created` (ISO time), `pipeline_version`, `graph_version`,
`counts` (at least `nodes` and `edges`), `params` (the build parameters: free-form, but
JSON-native with string keys, so no int keys, tuples, NaN or other objects) and `sources` (a
list).

`validate()` raises `ValueError` naming the array at fault. `validate(deep=False)` checks only
what costs nothing to read: every array present as a NumPy array of its dtype and shape, the
meta keys, `counts` against n and m, and `params` surviving a JSON round trip unchanged. The
default also checks contents: the indptr runs (from 0 to the values' length, each at least its
minimum above), id and enum ranges, `member_osm_ids` unique, edges sorted by (u, v), both
adjacency lists exactly as defined, twins and transitions as above, and a transition's edges
meeting at its node.

## The build format

One directory per build, `data/graphs/<region>/<build_id>/`, which the pipeline's earlier
steps already use for their `stages/` and sidecars (`docs/reference/pipeline.md`):

```
meta.json            the meta dict, graph_version filled in (sorted keys, indented)
strings.json         {"names": [...], "refs": [...], "identities": [...]} (UTF-8, compact)
arrays/<name>.npy    one file per array of ARRAY_SPECS, written by np.save without pickles
```

- `write_build(data, path) -> str` runs `validate()` and writes the build into `path`, which
  may already exist and hold other files. A `path` that has `meta.json` raises
  `FileExistsError`: a written build is never overwritten (builds are immutable per build id).
  It stages all three in a sibling `.<name>.tmp` directory, moves `strings.json` and `arrays/`
  into `path`, then moves `meta.json` in last as the commit marker: `read_build` needs
  `meta.json`, so a reader never sees half a build. What a crashed write leaves is replaced by
  the next one: a staging directory (a crash while staging leaves `path` as it was), or
  `arrays/` and `strings.json` without `meta.json` (a crash between the moves). It returns the
  `graph_version` and writes it into `meta.json`; it does not change the caller's `meta`.
- `read_build(path, mmap=True) -> GraphData` loads every array with
  `np.load(mmap_mode="r", allow_pickle=False)`: worker processes share one copy through the OS
  page cache, and loading costs nearly nothing. `mmap=False` loads read-only copies. It runs
  `validate(deep=False)` only: a deep validation or a re-hash would read every page of the
  build in every worker.
- `.npy` per array, not `.npz` or Parquet: NumPy memory-maps `.npy` files only (`.npz`
  members are read whole). Parquet exports for DuckDB or QGIS are a `qa` convenience for later.

### `graph_version`

`graph_version(data)` is the sha256, as hex, over, in `ARRAY_SPECS` order, each array's
`name|dtype.str|[shape]` line and its raw bytes; then the `strings.json` bytes; then the JSON
of `meta["params"]` with sorted keys and compact separators (which stands for the params
exactly because `validate()` requires them JSON-native). Nothing else in `meta` enters it
(not `created`, `region`, `build_id`, `pipeline_version`, `sources`, `counts`), so rewriting
the same build reproduces it. A test pins its value for a small build (`tests/graph/test_store.py`);
changing the array order, a dtype or the hash recipe changes every version and is a format
change (tier A, a new build id).

The version hashes bytes, so it is exact per platform: projected coordinates from PROJ can
differ in their last bits between machines, and a build made on macOS and on Linux from the
same input may carry different versions. Routes do not depend on those bits (the engine works
on x/y and lengths), but a golden test should record the version of the build it ran on.

## Backends

`GraphBackend` (`backend.py`) is what the engine programs against; M1's surface is three calls.
Every backend must give identical results on identical inputs; `PythonBackend` is the
reference, the CSR + Numba backend (M2) the production one, and networkx the oracle the tests
check the reference against.

**Masks.** A node may be entered iff `node_mask[v]` (default: every node) and not
`forbid_nodes[v]`. `src` and `dst` obey the same rule, so a caller that forbids the route's used
nodes clears the endpoints of the search from `forbid_nodes`. An edge may be used iff not
`banned_edges[e]` and both its endpoints may be entered. Masks are `bool` arrays of length n or
m; `weights` are float64, one per edge, non-negative and not NaN. The Python API takes bool
masks; uint8 masks are the M2 kernels' internal business. `PythonBackend` raises `ValueError`
for a node id outside [0, n) (no negative-index wraparound), a mask or weights of the wrong
dtype or length, and NaN or negative weights; the M2 kernels may leave the weight scan to their
callers.

- `dijkstra(src, dst, weights, *, node_mask, forbid_nodes, banned_edges, max_cost=inf) -> Path | None`:
  a cheapest path, or None when `dst` cannot be reached at a cost of at most `max_cost`. The
  search never visits a node whose tentative cost exceeds `max_cost`, `src` at cost 0 included,
  so a negative `max_cost` returns None even when `src == dst`.
- `simple_paths_dfs(src, dst, *, node_mask, forbid_nodes, banned_edges, max_depth, max_paths) -> list[Path]`:
  the simple paths of at most `max_depth` edges, in ascending edge-id order (lexicographic in
  their edge ids), stopping after `max_paths`.
- `largest_scc_mask(edge_mask=None) -> bool[n]`: the nodes of the largest strongly connected
  component of the graph restricted to `edge_mask` (scipy's `connected_components`,
  `connection="strong"`); on a tie, the component scipy labels first.

A `Path` is `(nodes, edges)`, both int32, `edges[k]` going from `nodes[k]` to `nodes[k + 1]`;
from a node to itself (when it may be entered) it is that node and no edge, for both searches.

**The tie rule**, which every backend reproduces: the heap orders entries by (cost, node), so
equal costs pop the lower node id first; a node's out-edges are relaxed in `out_edges` order
(ascending edge id); only a strictly smaller cost replaces a node's predecessor. Of two
equal-cost routes, the one that reaches the shared node first keeps it; of equal parallel
edges, the lower id.

**`PythonBackend(data)`** turns the adjacency into Python lists once (tens of MB at Portland
scale), combines the node masks into one array per search with a NumPy operation, and reads
masks and weights only for the edges a search touches, so a small bounded search does not pay
for the whole graph. Measured on the dev machine: a search across a 200 × 200 `grid_graph`
(40,000 nodes, 159,200 edges, every node settled) takes about 50 ms of CPU, roughly 0.3 µs per
relaxed edge; a search ten blocks from the centre of the same grid takes under 1 ms.

## Spatial

`SpatialIndex(data)` builds, once per build, a `cKDTree` on every node's (x, y) and a pyproj
transformer from EPSG:4326 to `meta["crs_epsg"]` (`always_xy=True`). Masks filter at query
time, so one tree serves every request.

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
  node's position, and `note` None when the nearest candidate was taken, else one of
  "moved N m to the nearest through street" (via), "… street you can leave by" (start),
  "… street you can arrive by" (end). Nothing qualifying raises `SnapError`.
  **Re-snapping** (a leg that cannot be built, `docs/PLAN.md` §3.2): snap again with the
  nodes already tried in `exclude`, keeping the same `node_mask`. The next candidate is then
  the nearest, so its `note` is None unless a role rejects it; the caller says why the stop
  moved.
- `leash_mask(stops_xy, leash_m) -> bool[n]`: the nodes within `leash_m` of the polyline
  through the stops (consecutive duplicate stops dropped; a single stop is a point). Nodes
  outside the stops' bounding box grown by `leash_m` are skipped before shapely measures the
  rest against one geometry.
- `bubble_mask(stops_xy, radius_m) -> bool[n]`: the nodes within `radius_m` of any stop.
- `active_mask(tier_mask, leash, bubbles) = leash & (tier_mask | bubbles)`: a request's node
  mask (`docs/PLAN.md` §2.6).

## Test builders

`worst_route.graph.testing` ships in the package so every area's tests can build graphs by
hand; nothing outside tests uses it.

- `make_graph(nodes, edges, *, twins=(), transitions=(), crs_epsg=32610, lonlat=None) -> GraphData`.
  `nodes` are (x, y) in metres in `crs_epsg`; lon/lat are their inverse projection unless
  `lonlat` is given. `edges` are `(u, v, length_m)` tuples or `Edge(u, v, length_m=None,
  highway=RESIDENTIAL, speed_kmh=40.0, name=None, ref=None, flags=0, toll_booths=0,
  level_crossings=0)`; a missing length is the straight-line distance. `twins` pairs edges and
  `transitions` lists allowed `((a, v), (v, b))` pairs, each edge named by its (u, v); naming
  no edge or several parallel ones raises `ValueError`. Edge ids are the positions after a
  stable sort by (u, v), as in a real build.
  Defaults for everything else: time = length / speed; tier from `TIERS`; the link flag set for
  `*_LINK` classes; identity = ref, else name (not normalised); unit = the edge id; bearings
  from the coordinates (in = out); one member per node with OSM id = node index + 1; one way
  per edge with id = input position + 1; geometry the two endpoints; pass-through from the
  topology; state −1, kind plain, no delay; tier masks: edges of tier ≤ t and their endpoints
  (no SCC); meta region and build id `"test"`, a fixed `created`, and `graph_version` computed.
  The result is validated and read-only.
- `grid_graph(rows, cols, spacing_m, *, oneway_rows="", oneway_cols="") -> GraphData`: a
  lattice of residential streets at `GRID_ORIGIN` (UTM 10N, near downtown Portland); node
  `r * cols + c` at `(x0 + c * spacing, y0 + r * spacing)`, row 0 the southmost. One character
  per row or column: `+` one-way east (rows) or north (columns), `-` the opposite, `.` two-way;
  `""` is two-way everywhere. A grid with extras (a cul-de-sac, a stub) is built with
  `make_graph`.
- `edge_id(data, u, v) -> int`: the one edge u → v, or `ValueError`.

## Tests

`tests/graph/`: `test_data.py` (every malformation `validate` rejects), `test_testing.py` (the
builders), `test_store.py` (round trips, the version and its pin, refusals),
`test_python_backend.py` (hypothesis parity with networkx for all three calls, and the exact
path against a heap-free implementation of the tie rule on graphs where equal costs are
common, 200 examples each; the tie rule, masks, `max_cost`, bad arguments, depth and count
limits on crafted graphs), `test_spatial.py` (snapping roles, masks, `exclude` and notes; leash
and bubble masks). Run them with
`uv run pytest tests/graph`.
