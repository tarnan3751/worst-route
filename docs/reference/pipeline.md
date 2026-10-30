# Pipeline

How the offline pipeline works: the steps built so far, the data layout, build ids and
sidecars, and the rules each step applies. The design is `docs/PLAN.md` §2; the code is
`src/worst_route/pipeline/`, one module per step, and each module's header comment states its
contract.

Built: `download`, `filter`, `build`. Still stubs that exit with code 2: `cleanup`, `enrich`,
`tiers`, `save`, `qa`.

## Commands

```
uv run worst-route pipeline download --region portland [--force]
uv run worst-route pipeline filter   --region portland [--source PATH] [--force]
uv run worst-route pipeline build    --region portland [--source PATH] [--build-id ID] [--force]
```

`--region` is a key of `config.REGIONS` (a Geofabrik state slug, an optional bbox, a projected
CRS). `--source` makes `filter` or `build` read that file instead of its usual input (a fixture,
in tests). Every step accepts `--build-id` and `--force`; `download` and `filter` ignore the
build id. Each step prints what it wrote and its counts, or that its output is up to date and
was skipped. A missing input exits with code 1 and names the step to run first; a failed
download (an md5 mismatch, an HTTP or connection error) prints one line on stderr and exits 1.

## Data layout

Everything is under `settings().data_dir` (`WORST_ROUTE_DATA`, default `./data`); `layout.py`
is the one place that names these paths.

| Path | Written by |
|---|---|
| `raw/<state>-latest.osm.pbf`, `raw/<state>-latest.osm.pbf.md5` | `download` |
| `filtered/<region>.roads.osm.pbf`, `filtered/<region>.pois.parquet` | `filter` |
| `graphs/<region>/<build_id>/stages/build.pkl`, `stages/build.json` | `build` |
| `graphs/<region>/latest` (the region's current build id, one line) | `build` |
| `<output>.inputs.json` beside a step's output (its sidecar) | `filter`, `build` |

`stages/` holds intermediate state between the steps of one build. `stages/*.pkl` is the only
place the pipeline pickles; the final on-disk format (`docs/PLAN.md` §2.7) never does.

## Build ids, sidecars, skipping

- **Build id:** `--build-id` if given, else the first 12 hex digits of a sha256 over the JSON
  list `[md5 of the filtered roads file, region name, region bbox, region CRS (EPSG code),
  PIPELINE_VERSION]` (`layout.build_id_for`). `PIPELINE_VERSION` is in `pipeline/__init__.py`;
  bump it when a step's output changes for the same input.
- **Sidecar:** after `filter` or `build` writes its output it records
  `{"step", "inputs": [{"file", "md5"}], "params"}` in `<output>.inputs.json` (filter: the
  bbox and the pipeline version; build: the region, bbox, CRS and pipeline version). Inputs are
  recorded by file name and md5, so moving the data directory does not invalidate them. The
  filter's sidecar also records `source_timestamp`, the extract's data date from its header
  (`osmosis_replication_timestamp`, read by `filter.source_timestamp`; null when the header has
  none, as in the fixtures), for the `save` step to carry into the build's metadata.
- **Skipping:** a step is skipped, with a message, while its outputs exist and its sidecar
  equals what it would record now; `--force` runs it anyway. The sidecar is deleted before a
  step runs, so an interrupted run is never mistaken for a finished one. `build` writes
  `latest` whether it ran or was skipped.
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
has to carry `geometry` and `osm_nodes` through simplification itself.

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

The tests are `tests/pipeline/`: `test_exclusions.py`, `test_download.py`, `test_filter.py`,
`test_build.py`, `test_layout.py`, and `test_cli_steps.py` (the three steps through the CLI
under a temporary `WORST_ROUTE_DATA`, including the sidecar skips).
