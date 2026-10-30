# Graph

The routing graph of one build: its arrays (`GraphData`), the on-disk build format and its
`graph_version`, the `GraphBackend` protocol the engine searches through, the pure-Python
reference backend and the CSR + Numba production backend, the spatial helpers (projection,
snapping, leash and bubble masks), loading saved builds for the API, and the builders tests use
to make small graphs by hand.
Code: `src/worst_route/graph/`. The design it implements is `docs/PLAN.md` §2.6–2.7, §3.2,
§3.9–3.10; the conventions are `CLAUDE.md`.

| Module | What it holds |
|---|---|
| `data.py` | `GraphData`, `Strings`, `ARRAY_SPECS` (the format), `NodeKind`, `EdgeFlag`, `validate()` |
| `store.py` | `write_build`, `read_build`, `graph_version` |
| `backend.py` | the `GraphBackend` protocol, the `Path` type, and what every backend shares: `check_ends`, `WeightCheck`, `as_cost`, `as_limits`, `enterable`, `banned_mask`, `largest_scc_mask` |
| `python_backend.py` | `PythonBackend`, the reference implementation |
| `csr_backend.py` | `CsrBackend`, the production implementation, over `kernels.py` |
| `kernels.py` | the Numba kernels `dijkstra_masked` and `simple_paths_dfs` |
| `backends.py` | `make_backend` |
| `loader.py` | `load_build`, `Loaded`, `BuildNotFound` |
| `spatial.py` | `SpatialIndex` (`project`, `snap`, `leash_mask`, `bubble_mask`), `Snap`, `SnapError`, `active_mask` |
| `testing.py` | `make_graph`, `grid_graph`, `Edge`, `edge_id`, `GRID_ORIGIN` |

`config.py` holds `HighwayClass` (the road classes, 0–13), `TIERS` (the classes of T0–T4,
each tier containing the one before it, T4 every class), and `BackendName` and
`DEFAULT_BACKEND` (**Choosing a backend**).

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

A node with no transition rows allows every pair, so an empty table cannot be written: cleanup
keeps a super-junction whose table is empty (no pair allowed), and `save` refuses a build that has
one (`docs/reference/pipeline.md`, **cleanup**).

**Edge ids are sorted by (u, v, original order).** Each node's out-edges are therefore one
contiguous run of ascending ids (`out_edges` is `0..m-1`), which the backends' tie rule relies
on.

`strings` is `Strings(names, refs, identities)`, three lists of str. `meta` is a dict with
`region`, `build_id`, `crs_epsg`, `created` (ISO time), `pipeline_version`, `graph_version`,
`counts` (at least `nodes` and `edges`), `params` (the build parameters: free-form, but
JSON-native with string keys, so no int keys, tuples, NaN or other objects; `pipeline save`
puts the `region`, its `bbox` and `crs_epsg` there with each step's configuration, so they
enter `graph_version`, `docs/reference/pipeline.md`) and `sources` (a list).

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
  `FileExistsError`: a written build is never overwritten. Builds are immutable unless
  `pipeline save --force` deletes one and writes it again, which changes its `graph_version`
  when its contents or params differ.
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
(not `created`, `build_id`, `pipeline_version`, `sources`, `counts`, nor the top-level
`region` and `crs_epsg`, though a pipeline build's `params` repeat those two, so they enter
through them), so rewriting the same build reproduces it. A test pins its value for a small build (`tests/graph/test_store.py`);
changing the array order, a dtype or the hash recipe changes every version and is a format
change (tier A, a new build id).

The version hashes bytes, so it is exact per platform: projected coordinates from PROJ can
differ in their last bits between machines, and a build made on macOS and on Linux from the
same input may carry different versions. Routes do not depend on those bits (the engine works
on x/y and lengths), but a golden test should record the version of the build it ran on.

## Backends

`GraphBackend` (`backend.py`) is what the engine programs against; M1's surface is four calls
and a `name` (`"python"`, `"csr"`), which a planned route records. Every backend must give
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
- `simple_paths_dfs(src, dst, *, entry_edge=-1, exit_edge=-1, node_mask, forbid_nodes, banned_edges, max_depth, max_paths) -> list[Path]`:
  the simple paths of at most `max_depth` edges whose every turn the transition table allows
  (from `entry_edge` into the first edge and from the last edge into `exit_edge` included, when
  given), in ascending edge-id order (lexicographic in their edge ids), stopping after
  `max_paths`.
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

**The transition-aware search** (`dijkstra_edges`; `docs/PLAN.md` §3.5) searches edge states,
so it sees the transition table: a turn from e into f is allowed iff head(e) has no rows or
lists (e, f). `entry_edge` is the edge the path continues (it must end at `src`), `exit_edge`
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
- **The answer** is the path of the first end state popped, or None. **A path that enters a
  node twice is None**: node-simplicity cannot be said in an edge-state search, and such a path
  is illegal anyway (a turn table can make the cheapest legal way a loop round a block).
- From `src` to itself: the one-node path, unless both turn edges are given and their turn is
  not allowed at `src` (None).

On a build with no transition rows it finds a path whenever `dijkstra` does, at the same cost
(Dijkstra's tree of edge states never repeats a node when every turn is allowed), and the same
path whenever the two tie rules pick the same one (always when the cheapest path is unique).
The engine calls it only on builds that have rows (`docs/reference/engine.md`, **Searches**),
so on every other build the backends' node-state results stay what they were.

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
build's CSR arrays (`out_indptr`, `out_edges`, `edge_u`, `edge_v`); it copies and precomputes
nothing. Each call runs the shared checks in the reference's order, answers the trivial cases as
the reference does (a negative `max_cost`, an end that may not be entered, `max_paths <= 0`,
`src == dst` for the DFS), then hands the kernel the arrays, the weights, and the masks as
zero-copy uint8 views (`mask.view(np.uint8)`). `largest_scc_mask` is the shared scipy call.

**The transition-aware search has no kernel yet.** `dijkstra_edges` raises
`NotImplementedError` ("the csr backend cannot search turn tables yet; use --backend python"),
and so does `simple_paths_dfs` on a build with transition rows; on a build without rows every
turn is allowed, so the DFS's turn edges (checked as the reference checks them) change nothing
and the plain kernel answers, byte for byte. The engine calls the transition-aware search only
on builds with rows (`docs/reference/engine.md`, **Searches**), so on those builds, Portland's
among them, `make_backend("auto")` takes the reference until the edge-state kernel lands, and
`csr` asked for by name fails at the first search; on builds without rows both backends plan
the same routes.

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

**The parity contract.** For arguments of the declared types (int node ids and limits, a real
`max_cost`, float64 weights, bool masks), `CsrBackend` returns `PythonBackend`'s results byte
for byte: the same paths in the same int32 arrays, the same ties, the same Nones, the same
paths in the same order, the same errors (the transition-aware search aside, above). The semantics in `backend.py` are the specification; a case where the
two legitimately differ is a bug in one of them, never a choice. Floats are added one at a time
in the same order as the reference (no `fastmath`, no `parallel`), so equal inputs give equal
costs. The tests (**Tests**) hold this, and so do the golden route and the engine's same-seed
tests, which run on both backends.

**Compilation.** Each kernel is compiled once, eagerly, when `kernels.py` is imported, for one
signature whose arrays are read-only and of any layout. Memory-mapped builds, read-only cached
weights and fresh writeable masks therefore share one specialization. `cache=True` keeps it in
`src/worst_route/graph/__pycache__/` (gitignored). The first import after a change to
`kernels.py`, or in a fresh clone or CI run, compiles both kernels: about 1.2 s on the dev
machine. Every later process loads them from the cache in about 0.15 s.
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
took 30–32 µs of every call on both backends.

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

## Choosing a backend

`make_backend(data, name=DEFAULT_BACKEND)` (`backends.py`) makes the backend `name` asks for:
`"csr"`, `"python"`, or `"auto"`, which is `CsrBackend` when numba imports and `PythonBackend`
otherwise (`csr_backend` is imported only when asked for), and `PythonBackend` on a build with
transition rows, which `CsrBackend` cannot search yet (**The CSR backend**); `auto_note(data,
name)` gives the reason, " (csr cannot search turn tables yet)", which the CLI appends to its
`backend:` line. `config.DEFAULT_BACKEND` is `"auto"`; `config.BackendName` lists the names.
Where both backends can search, the choice changes only the speed. `plan_route` uses
`make_backend`'s default when it is given no backend, and `worst-route plan` and `validate`
take `--backend` (`docs/reference/engine.md`).

## Loading builds

`load_build(data_dir, region, build_id="latest", *, backend=DEFAULT_BACKEND) -> Loaded(data,
spatial, backend)` (`loader.py`) is how the API process and each job's worker process load a
build: `read_build` of `<data_dir>/graphs/<region>/<build_id>/`, every array memory-mapped (the
workers share the OS page cache), its `SpatialIndex`, and `make_backend(data, backend)`.
`"latest"` is the id in `graphs/<region>/latest`, read on every call, so the cache never pins
an old build under that name. The two builds used last stay loaded (`lru_cache(maxsize=2)` keyed
by data directory, region, build id and backend: the region the API serves, and one other a
stored route was planned on). A missing `latest` or `meta.json` raises `BuildNotFound` (a
`FileNotFoundError`) naming the step to run; `make_backend`'s `ImportError` for `csr` without
numba passes through. On Portland a load takes about 0.35 s in a fresh process, mostly imports.
The CLI's `plan` and `validate` keep their own loading, with its one-line errors.

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
- `nodes_within(center, radius_m, node_mask) -> intp[]` and `edges_from_within(center,
  radius_m, node_mask) -> intp[]`: the detours' balls, the nodes of the mask within `radius_m`
  of a point and the edges leaving them, ascending. A node is within when dx² + dy² ≤ r², the
  same rule as `bubble_mask` up to floating point at the exact boundary, so the nodes are
  `bubble_mask`'s within the mask (equal on every radius the tests try, lattice nodes on the
  boundary included). They scan the mask's nodes and out-edges, kept for the last mask seen
  (compared by content, after the mask's dtype and length are checked), instead of querying the
  whole build: on Portland a request holds about 7,600 of 65,810 nodes, and the scan costs
  0.04 ms where a KD-tree query covering the area cost 1.4 ms (it returns its hits as a Python
  list). A mask of the wrong dtype or length is a `ValueError`.
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
builders), `test_store.py` (round trips, the version and its pin, refusals), `test_backends.py`,
`test_edge_search.py`, `test_spatial.py` (snapping roles, masks, `exclude` and notes; leash and
bubble masks; the masked balls against `bubble_mask`, radii on lattice nodes included), and
`test_loader.py` (`latest`, the two builds kept, memory-mapped arrays, missing builds).

`test_backends.py` runs the reference's tests on both backends through the `backend_type`
fixture:
- hypothesis parity with networkx for the node-state calls and the SCC mask, 200 examples each;
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

It also tests `make_backend`'s choices, `auto` taking the reference on a build with turn
tables among them.

`test_edge_search.py` tests `dijkstra_edges` and the DFS's turns on the reference: on random
graphs with random transition tables, the exact path against a heap-free implementation of the
edge-state tie rule and the cost against networkx over the edge states; without rows, the cost
of `dijkstra` and its path when the cheapest is unique; the DFS against networkx's simple paths
filtered by turns; crafted entry and exit edges, a loop that would repeat a node, no return to
the start, the dead end at `dst`, bad arguments. It also checks that `CsrBackend` raises
`NotImplementedError` for the transition-aware search on a build with rows, and answers the DFS
with turn edges like the reference on a build without. Run them with `uv run pytest tests/graph`.
