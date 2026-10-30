# Engine

How a route is planned, made worse and proven legal: the route model and its one mutation, the
request and its masks, snapping, the seed route and the baseline, the objective interface and
the distance mode, the detour strategies, the worsen loop, the validator, route files and the
`plan` / `validate` commands. Code: `src/worst_route/engine/`. The design it implements is
`docs/PLAN.md` §3.1–3.6, §3.9, §3.11 and §8.1; the conventions are `CLAUDE.md`. Everything
runs on a build's `GraphData` through `GraphBackend` and `SpatialIndex`
(`docs/reference/graph.md`); only `PythonBackend` exists so far.

| Module | What it holds |
|---|---|
| `route.py` | `Route`, `Splice`, `Metrics`, the `IllegalSplice` refusals, `check_route`, `splice_arrays`, `twins_of` |
| `validator.py` | `validate_route`, `ValidationReport`, `Check`, `ValidationFailed`, `RouteLike` |
| `request.py` | `RouteRequest`, `RequestMasks`, `request_masks`, `auto_tier`, `auto_leash_m` |
| `snap.py` | `snap_stops`, `resnap`, `apart_from`, `role_of`, `is_round_trip`, `StopUnreachable` |
| `seed_route.py` | `resolve_stops`, `baseline_route` / `Baseline`, `seed_route` / `SeedRoute` |
| `objectives/` | `base.py` (`Objective`, `BaseObjective`), `distance.py` (`Distance`), `OBJECTIVES`, `make_objective` |
| `detour.py` | `find_detour`, `pick_strategy`, `waypoint_path`, `random_weight_path`, `local_exhaustive_paths` |
| `worsen.py` | `worsen`, `run_worsen`, `pick_segment`, `Budget`, `ProgressEvent`, `WorsenResult` |
| `plan.py` | `plan_route`, `PlanResult` |
| `io.py` | `route_document`, `read_route` / `SavedRoute`, `route_geojson` |

`ENGINE_VERSION` (`engine/__init__.py`) is recorded with every route: bump it when the same
build, request and seed would give a different route. The engine's numbers (thresholds,
weights, radii) are in `config.py`.

## Routes

`Route` (frozen; arrays read-only) holds `nodes` and `edges` (`edges[k]` runs `nodes[k]` →
`nodes[k + 1]`), `stop_idx` (the stops' positions: strictly increasing, the first 0, the last
`len(nodes) - 1`), `used_nodes` (bool[n]), `banned_edges` (bool[m]: the route's edges and
their twins), `score` and `metrics` (`Metrics(length_m, time_s, counters)`), `seed` and the
build's `graph_version`. A round trip has `nodes[0] == nodes[-1]`; nothing else repeats.

**`Route.splice(data, i, j, nodes, edges, *, score, metrics, extra_banned=None)` is the only
mutation.** It replaces the interior of `nodes[i..j]` (i < j) by a detour from `nodes[i]` to
`nodes[j]` and returns a new route; the caller supplies the score and metrics (the route does
not know its objective). `Route.new(...)` makes a route and `check_route(...)` checks one, with
the same rules applied to the whole route. Each refusal is a subclass of `IllegalSplice` with
the position at fault in the resulting route:

| Refusal | When |
|---|---|
| `EndpointMismatch` | the detour does not start at `nodes[i]` and end at `nodes[j]`, or has one node |
| `BrokenChain` | an edge does not run from its node to the next, or is a self-loop |
| `StopInterior` | a stop lies strictly inside the removed run (`i` and `j` may be stops) |
| `NodeReused` | a node the route still uses once the removed interior is released, or one node twice |
| `RoundTripStartReused` | the same, for a round trip's start (a subclass of `NodeReused`) |
| `EdgeBanned` | an edge still banned once the removed edges' bans are released, an edge of `extra_banned`, the twin of another edge of the detour, or a two-edge round trip driving one road (`edge_unit`) out and back |
| `TransitionForbidden` | at any junction of the window, its two boundaries included, a node with transition rows does not list (in_edge, out_edge); nodes without rows allow every turn |

Releasing a run is exact: an edge the removed edges banned (themselves and their twins) stays
banned iff it is a kept edge or a twin of one (twins are symmetric). `extra_banned` is what
the caller bans for this window: the request's banned edges and the objective's
`banned_edges(G, route, i, j)`. They are not part of `route.banned_edges`, because an
objective's bans can depend on the window (hard mode). `release(data, i, j)` gives the
(forbid_nodes, banned_edges) masks a search for a detour of the window needs: the used nodes
without the removed interior and without its two ends (a search must be able to start and
end there), and the bans that remain.

A two-edge round trip `[a, x, a]` is the only way a vertex-simple route can drive both
directions of one two-way road; the `edge_unit` check covers it. Every other road reuse is
already a repeated node.

## Requests

`RouteRequest` (pydantic, frozen, JSON round trip): `stops` as (lat, lon) in order, at least
two (identical first and last points make a round trip); `objective` (a key of `OBJECTIVES`,
default `"distance"`); `tier` (`"auto"` or `"T0"`..`"T4"`); `leash_km` (`"auto"` or in
(0, 150]); `budget_s` (in (0, 120], default 20) and `max_iterations` (default 5000); `seed`
(≥ 0). Every number must be finite, so a request (and `route.json`) never holds `Infinity` or
`NaN`.

`request_masks(spatial, data, request)` derives everything from the request alone, so a saved
route can be checked against the same masks later. The trip length is the straight-line length
of the polyline through the projected stops.

- **Tier auto**: below 15 km T4, below 60 km T3, below 250 km T2, below 800 km T1, else T0
  (`TIER_AUTO_KM`).
- **Leash auto**: 0.6 × the trip length within [3, 150] km; `leash` is the nodes within it of
  the polyline.
- **Bubbles**: the nodes within 3 km of a stop (`BUBBLE_KM`).
- **`active`**, the node mask of every search: `leash & (tier_node_mask[t] | bubbles)`.
- **`banned`**, the request's edge bans for every search, every splice, the seed and the
  baseline: the edges outside `tier_edge_mask[t]`, unless both their ends lie in a bubble. A
  tier therefore keeps lower-class roads only near the stops, even a lower-class shortcut
  between two of the tier's own nodes.
- **`extent_m`**: the diagonal of the active nodes' bounding box. The loop's radius bound is
  `r_max = min(leash, extent)`.

## Stops

`snap_stops(spatial, data, stops, active)` snaps each stop with `SpatialIndex.snap` in the
active mask, by role: the start, then the vias, then the end. A cul-de-sac via moves to the
nearest through street, with `snap`'s note. Stops stay on distinct nodes: a via avoids the
nodes of every other stop, and so does the end, except in a round trip. **A round trip is a
request whose start and end points are identical** (`is_round_trip`); only then may the end
take the start's node. An end clicked a few metres from the start is a point-to-point trip:
it moves to its next candidate, and a trip never silently collapses into a loop. A stop moved
off another stop's node gets the note "moved N m: another stop is at the nearest node". `resnap` is the fallback below; its note is
"moved N m: nearer nodes cannot be reached without repeating one". `StopUnreachable` names a
leg no route covers without repeating a road: `leg = (k, k + 1)`, and `stop_index` is its later
stop, the stop the API's `STOP_UNREACHABLE_WITHOUT_REPEAT` error names. Its message names both
ends and the stops that were moved: "no route from stop 1 to stop 2 without repeating a road,
after moving stop 1 and stop 2 up to 10 times each".

## Seed and baseline

**The baseline** (`baseline_route`) is what a sane GPS would drive: the shortest path on length
between consecutive stops, within the request's masks and nothing else, concatenated. Its legs
may share nodes. It is the denominator of the ratio.

**Settling the stops** (`resolve_stops(data, spatial, backend, request, masks)`) comes first:
the stops are snapped, and when the seed cannot be built they fall back (step 4 below). It is
deterministic for a request and a build, so `worst-route validate` re-derives the stops of a
saved route with it; the cost is a few seed builds.

**The seed** (`seed_route`, on the resolved stops) is the route the loop starts from:

1. **Legs.** The legs are built in order on length. Each forbids the nodes of the other legs
   built so far and every other stop's node, and bans their edges' twins.
2. **Penalty re-solve.** When a leg fails, up to 4 rounds (`docs/PLAN.md` §3.3): the leg's
   unconstrained path names the nodes other legs hold; those legs are rebuilt with 8× the
   weight on every edge touching those nodes, keeping their old path when they have no other;
   then the leg is tried again.
3. **Failed legs.** A leg that still fails, a leg that would not move (two stops on one node),
   or a seed `check_route` refuses names the leg. `check_route` refuses, for example, a turn a
   transition table forbids, which the plain Dijkstra cannot see.
4. **Fallback** (`resolve_stops`; `docs/PLAN.md` §3.2): the leg's earlier stop, if it is a
   via, is re-snapped to its next-nearest candidate (never another stop's node), up to 10
   times, and the whole seed is rebuilt each time.
   Then, with that stop back on its node, the later stop is re-snapped the same way (a via or
   the end). The start never moves. Then `StopUnreachable` names the leg. A via trapped
   in a one-entrance subdivision therefore walks out to the street the subdivision hangs off,
   and the stops after it stay (`tests/engine/test_seed_route.py`).

**A round trip with no stop between its ends** has no legs. Its seed is a loop: the waypoint
strategy from the start back to itself, on length, with radius `min(1000 m, r_max)` and the
waypoint at least 500 m away, drawn up to 20 times from the request's rng. If none closes into
a legal loop, `StopUnreachable` for the leg (0, 1). Its baseline is 0 m, so its ratio is
undefined.

## Objectives

A mode is an `Objective` (`objectives/base.py`, `docs/PLAN.md` §3.6). The worsen loop sees
only this interface:

    score = Σ node_terms[v] + Σ edge_terms[e] + Σ pair_term(e_k, e_k+1) + global_term(nodes, edges)

- **Terms.** Node terms count a round trip's start once. Pair terms are read only when
  `has_pair`, the global term only when `has_global`. `global_term` takes a route's arrays, so
  a candidate can be scored before it is spliced.
- **Hooks.** `feasible_pair` and `feasible_window` (constraints), `banned_edges(G, route, i, j)`
  (bans per window), `search_weights` and `waypoint_pool` (search guidance), and
  `initial_radius` and `search_budget(radius)` (default 4 × radius).
- **The rest.** `better(a, b)` (default: a higher score), `target_value` (what target modes
  compare), and `describe` (mode numbers for the presentation).

`BaseObjective(data, *, baseline_length_m, r_max)` gives every hook a neutral default and
caches its term vectors for the build. It implements, once for every mode:

- `evaluate(nodes, edges) -> (score, Metrics)`;
- `score(route)`;
- `delta(route, i, j, nodes, edges)`.

`delta` sums the terms a splice removes and adds: node and edge terms over the removed interior
and the detour; pair terms at both boundaries and inside; the global term re-evaluated on the
spliced arrays. Everything is summed with `math.fsum`, so `delta` equals
`score(after) − score(before)` to rounding, and a change of nothing scores exactly 0. Its
`initial_radius` is a quarter of the baseline length, at least 1 km and at most `r_max`.

**`Distance`** scores the length: its edge terms are `edge_length_m`, and it has no node,
pair or global terms. It searches on length, draws waypoints anywhere, and its target value and
`describe` are the length.

**A new mode** is a subclass of `BaseObjective` that overrides the terms and hooks it needs,
registered in `OBJECTIVES` under its request key. `tests/engine/test_objectives.py` holds
`delta` to a full re-score with a test objective that has every kind of term; a new mode gets
the same test. A constraint is an objective with hooks and no terms. Composing objectives and
constraints (`docs/PLAN.md` §3.7) arrives with M3.

## Detours

`find_detour(backend, spatial, data, route, i, j, *, forbid, banned, node_mask, radius,
objective, rng, entry_edge, exit_edge, strategy=None)` returns a path from a = `nodes[i]` to
b = `nodes[j]`, or None. It returns a path only if the path differs from the removed run and
`route.check_splice` accepts it. `forbid` and `banned` are the loop's search masks. `banned`
includes the request's and the objective's bans, and is passed to the splice check as
`extra_banned`.

**Choosing a strategy.** It is drawn from `rng` with weights 0.6 waypoint, 0.3 random weight,
0.1 local exhaustive. Local exhaustive is allowed only when a and b lie less than 800 m apart;
otherwise the draw is 2/3 waypoint, 1/3 random weight. `strategy` forces one (tests, and the
round-trip seed).

- **Waypoint.** A waypoint w is drawn from the nodes the search may enter within 4 × radius of
  the midpoint of a and b. The pool is the objective's when it has one; a and b are never
  drawn. The probability is ∝ exp(−distance / radius). Then a → w, never through b, and
  w → b, avoiding the first leg's nodes and its edges' twins. Each search is on
  `search_weights` and bounded by `search_budget(radius)`.
- **Random weight.** a → b on a copy of `search_weights` in which every edge leaving a node
  within 4 × radius of the midpoint is multiplied by U(0.2, 5), drawn from `rng` in ascending
  edge id; the edges farther out keep their weight, so the draws do not grow with the graph.
  Bounded by the same budget in scaled cost.
- **Local exhaustive.** The simple paths a → b of at most 25 edges, up to 5000 of them. They
  are searched in a ball around the midpoint of radius 3 × dist(a, b), capped at 400 m. A
  Dijkstra probe skips the enumeration when b is unreachable. The paths are ranked by
  `objective.delta` (stable, so ties keep the DFS order), and the first the splice accepts is
  returned.

**Why the local-exhaustive cap.** With the reference backend, the DFS grows exponentially with
the ball. Measured on a 100 m grid with depth 25 and 5000 paths:

| Window | Uncapped 3 × d ball | Ball capped at 400 m |
|---|---|---|
| 1 edge (100 m) | 16–180 ms | — |
| 4 edges | 7–13 s | 0.27 s, the same best path |
| 800 m apart | 34 s | — |

The cap is `LOCAL_EXHAUSTIVE_BALL_MAX_M`, for the M2 kernels to revisit.

**What the searches cannot see.** A refused candidate is a failed attempt. The searches cannot
see:

- two twins inside one detour;
- a turn a transition table forbids (until the turn-aware kernel, M3);
- a two-edge round trip on one road.

`entry_edge` and `exit_edge` are for that kernel and unused until then. On a one- or two-edge
window, the randomly weighted direct edge usually wins, so random weight seldom finds a new
path there; the waypoint is the workhorse.

## The loop

`worsen(backend, spatial, data, route, objective, budget, rng, node_mask, *, r_max,
banned_edges=None, check_delta=False)` is a generator of `ProgressEvent(iteration, elapsed_s,
score, metrics, splice)`, one per accepted splice. The `Splice(i, j, nodes, edges)` replays it
on a copy of the route. The generator returns `WorsenResult(best, events, attempts, accepted,
stopped_because, elapsed_s)`, and `run_worsen(..., on_event=None)` drives it. Each attempt:

1. **Window** (`pick_segment`). One edge (j = i + 1) with probability 0.8, else a run of 2 to 4
   edges starting at a uniform edge, cut short at the next stop, so no stop is ever inside it.
2. **Masks.** `route.release(i, j)`, then OR in the request's `banned_edges` and the
   objective's bans for the window.
3. **Detour.** `find_detour` at the current radius.
4. **Accept** when the detour's `objective.delta` is strictly positive and `feasible_window`
   agrees. The new score and metrics are a full `objective.evaluate` of the spliced arrays.
   With `check_delta` (tests), the loop asserts that this equals the current score plus
   `delta`. The splice is told the request's and objective's bans as `extra_banned`. `best` is
   tracked by `objective.better`.

**The ambition radius** starts at `objective.initial_radius`. It grows 1.5× on every accepted
splice, up to `r_max`, and halves after every 20 failures in a row, down to
`min(300 m, r_max)`. A failure is no detour, a gain of zero or less, or an infeasible window.

**Stopping**: `"budget"` when `max_iterations` attempts are made or `budget.seconds` have
passed; `"converged"` after more than 400 failures in a row at the smallest radius. Target
modes (ratio, duration) are M2; their hooks (`target_value`, `better`) are in the interface.

**Determinism.** Every random choice is drawn from one `numpy.random.Generator(PCG64(seed))`, in
a fixed order: the round-trip seed, then the loop's windows, strategies, waypoints and weights.
Candidate lists are sorted arrays, and no set is iterated. A seed therefore gives the same
sequence of splices on any machine. The clock only decides when to stop:

- a run that stops on iterations is reproduced byte for byte;
- a run that stops on time is a prefix of the same sequence, and is reproduced by planning
  again with `max_iterations` set to its `run.attempts` and a budget in seconds at least as
  large as the first run's (or the clock may cut it shorter).

**Measured** with the reference backend on the dev machine:

| Graph | Result |
|---|---|
| 20 × 20 grid, corner to corner | converges in about 1 s at a ratio of about 10, using 383–391 of 400 nodes |
| 30 × 30 grid, start, centre via and end | converges in 1–3 s at about 15× |
| `grid_graph(100, 100, 100)` corner to corner, 20 s budget | about 490 attempts/s (the review's run), so the default `max_iterations` of 5000 stops it at about 10 s, at 33×; after random weight was limited to the window's ball, about 680 attempts/s, the 5000 attempts in about 7.4 s, at 33–34× |

On that grid a 20 s budget is never reached: `max_iterations` (default 5000) caps the run
first, so a longer search needs `--max-iterations` raised as well as `--budget`.

## Planning

`plan_route(data, request, *, backend=None, spatial=None, on_progress=None, check_delta=False)
-> PlanResult(request, masks, snaps, baseline, route, run, report)` runs a request end to end,
in this order:

1. the masks;
2. one `Generator(PCG64(request.seed))`;
3. `resolve_stops` (snapping and its fallback, which may move stops; `snaps` are the final
   ones);
4. `seed_route` on them;
5. `baseline_route` on those stops;
6. `make_objective` with the baseline length and `r_max`;
7. `Route.new` of the seed;
8. `run_worsen` within the request's budget, calling `on_progress(event, baseline)` on every
   accepted splice;
9. `validate_route` on the best route, against the request's masks and the resolved stops.

A route that fails validation raises `ValidationFailed` and is never returned. The errors to
expect are `SnapError` and `StopUnreachable`. The seed checks every leg itself, so a
`ValueError` or `IllegalSplice` from seeding would be a bug; `plan_route` still reports it as
`StopUnreachable` for the leg at fault (the first leg when the position is unknown), with
"the seed route was refused: …", so the CLI prints one line rather than a traceback.

## The validator

`validate_route(data, route, *, stop_nodes, node_mask, banned_edges) -> ValidationReport`
(`docs/PLAN.md` §8.1) reads any `RouteLike`: a `Route`, or a `SavedRoute` read from a file. It
is written without `route.py`, so a bookkeeping bug in the engine cannot hide in both. Each
check reports `ok`, the first offending index and a detail. `report.ok` is their conjunction,
`report.lines()` prints them, and `report.as_dict()` is JSON.

1. **`chain`**: `edges[k]` runs `nodes[k]` → `nodes[k + 1]` (so one-ways hold), and both ends
   of every edge are in `node_mask`. Ids outside the graph fail here; the other checks then
   report themselves skipped.
2. **`vertex_simple`**: no node twice, except a round trip's start at both ends. A route is a
   round trip when its first and last stop nodes are equal.
3. **`edge_simple`**: no edge twice, no edge together with its twin, no road unit twice.
4. **`osm_ids`**, the proof from the raw data. The route is expanded to original OSM node ids
   along its edges' `osmnode_ids`; consecutive edges' shared junction id is kept once, and
   different members of one cluster are kept both.
   - No id appears twice.
   - Each edge's first and last ids are members (`member_osm_ids`) of its tail and head.
   - A node's members appear in one contiguous run (a round trip's start in its first and its
     last run).

   So no consolidated junction is entered twice, and no shape point is driven twice, whatever
   the engine's bookkeeping says.
5. **`transitions`**: every turn at a node with transition rows is listed.
6. **`stops`**: `nodes[stop_idx[s]] == stop_nodes[s]`, with positions strictly increasing from
   the first node to the last. For a saved route, `stop_nodes` are re-derived from its request
   (`resolve_stops`), not read from the file, so a file cut short and edited to agree with
   itself still fails here, naming the stop.
7. **`constraints`**: every node in `node_mask`, and no edge in `banned_edges` (the request's
   tier bans).
8. **`metrics`**: the reported length and time are finite and equal the ones recomputed with
   `math.fsum` within a relative 1e-6 (the comparison is written so that NaN fails).

## Route files

**`route.json`** (`route_document`; `worst-route plan --out`) holds:

- `graph_version`, `region`, `build_id`, `seed`, `engine_version`;
- `request`, the `RouteRequest` as JSON;
- `stops`: `[{lat, lon, node, moved_m, note}]`, the snapped node's position;
- `nodes`, `edges`, `stop_idx`;
- `metrics`: `{length_m, time_s, baseline_length_m, baseline_time_s, ratio}` (`ratio` is null
  for a round trip), plus the objective's counters;
- `run`: `{attempts, accepted, stopped_because, elapsed_s}`.

`read_route(path)` returns a `SavedRoute`, which the validator reads like a `Route`. A file
that is not one raises `KeyError`, `TypeError`, `ValueError`, `OverflowError` (an id beyond
int32) or pydantic's `ValidationError`.

**GeoJSON** (`route_geojson`; `plan --geojson`) is a FeatureCollection:

- the route and the baseline as LineStrings along their edges' `coords` (`[lon, lat]`, 7
  decimals; a point repeated where two edges meet is kept once);
- one Point per stop, at its snapped node.

Each feature has a `kind` property (`route`, `baseline`, `stop`). The lines carry `length_m`
and `time_s`; the stops carry `index`, `node`, `moved_m` and `note`. A round trip's baseline
has a null geometry.

## The CLI

```
worst-route plan --region R [--build-id ID] --from LAT,LON --to LAT,LON [--via LAT,LON]...
                 [--seed N] [--budget SECONDS] [--max-iterations N] [--leash-km X|auto]
                 [--tier auto|T0..T4] [--out route.json] [--geojson route.geojson]
worst-route validate route.json [--region R] [--build-id ID]
```

**`plan`** reads `<WORST_ROUTE_DATA>/graphs/<region>/<build_id>/`, by default the build named in
`graphs/<region>/latest`. A missing `latest` or `meta.json` is one line on stderr ("... run
`worst-route pipeline save --region R` first") and exit 1. Without `--seed` it draws a random
seed, which it prints and records. While it searches it writes `iteration N: ratio Rx, L km`
to stderr on an accepted splice, at most one line every 2 s. On stdout it prints:

- the build;
- the tier, leash, seed and budget;
- each stop with its node, position, how far it moved, and the note;
- the baseline and the worst route (length and time);
- the ratio;
- the node and edge counts;
- the attempts, accepted splices, elapsed time and why the search stopped;
- the validator's checks.

It writes the files it is asked for. A stop that cannot be snapped or reached is one line,
`cannot plan: ` and the error (for an unreachable stop, the leg and the stops it moved), and
exit 1. A route that fails the validator is reported as an engine bug, exit
1, and never printed. A bad argument is exit 2.

**`validate`** reads a `route.json` and loads the build it names (or `--region` /
`--build-id`). A file it cannot read (a missing field, an id beyond int32) is `cannot read …`
and exit 1. It refuses a build whose `graph_version` differs, recomputes the request's masks
from the file's request, and re-derives the stops with `resolve_stops`. It prints one line per
check, then `valid`, or exits 1.

## Limitations

- **Transitions.** The searches do not know transition tables, so on a build with rows the
  seed and the detours can fail where a turn-aware search would succeed. No M1 build has rows;
  stage B allows every turn in M1.
- **Speed.** The reference backend is pure Python; the CSR + Numba backend is M2.
- **Time budgets.** A run that stops on time is reproducible only through its recorded attempt
  count.
- **The strategy mix.** Random weight found a path other than the removed run in 3 of 64
  one- and two-edge windows on a straight route (16 seeds, an 8 × 8 lattice; `test_detour.py`),
  against 32 of 64 for the waypoint. In whole runs on `grid_graph(100, 100, 100)` corner to
  corner (seeds 1 and 2, 5000 attempts each), the strategies were drawn and found a detour
  that lengthened the route as follows:

  | Strategy | Drawn | Lengthening detours |
  |---|---|---|
  | Waypoint | about 3000 | 92–100 |
  | Random weight | about 1480 | 29–42 (69–78 detours found) |
  | Local exhaustive | about 515 | 186–221 |

  Local exhaustive, at 10% of the draws, made most of the gains. The weights stand for M1;
  M2's tuning has the numbers.

## Tests

`tests/engine/` builds its graphs with `worst_route.graph.testing` and the helpers in
`tests/engine/graphs.py` (`lattice`, with twins, transitions and lon/lat rounded for stable
bytes).

| Test file | What it covers |
|---|---|
| `test_route.py` | every refusal, round trips, and the release against a brute force on random twins |
| `test_validator.py` | each violation caught by the right check, including a cluster entered twice through its members, a shared shape point and non-finite metrics |
| `test_request.py` | the request model and its bounds, tier and leash auto, the masks and the tier's edge bans |
| `test_snap.py` | roles, cul-de-sacs, stops kept apart, round trips only for identical points, an end near the start moved on, re-snapping |
| `test_seed_route.py` | `resolve_stops` deterministic, node-disjoint legs, legs avoiding later stops, the penalty re-solve on a corridor, the baseline, the re-snap walk out of a one-entrance ring, the start never moving, a point-to-point trip never collapsing onto the start, a leg between two stops on one node, unreachable legs named by both ends, round-trip loops |
| `test_objectives.py` | `Distance`, the radius, and `delta` against a full re-score for every kind of term |
| `test_detour.py` | each strategy's legal detours and None when none exists, the strategy weights, random weight drawing only near the window, the local-exhaustive ranking and the refused-candidate skip |
| `test_worsen.py` | over fixed seeds, `check_delta` on: the score only rises, every event replays to the best route, budgets in iterations and seconds, convergence, the same seed gives the same bytes, different seeds give edge sets with Jaccard < 0.7, random one-way towns with twins always validate, a tier's shortcut ban, a point-to-point request never becoming a round trip, identical points planning a loop, an unexpected seed error reported as `StopUnreachable` |
| `test_io.py` | the route files |
| `test_cli_plan.py` | `plan` and `validate` on builds written with `write_build`, including the error paths, a route that fails validation never printed, a truncated and re-edited file caught by its re-derived stops, an id beyond int32 |

`tests/golden/` freezes one route (seed 7, three stops) on a 20 × 20 lattice whose lon/lat are
rounded to 1e-7°, so its bytes and pinned `graph_version` do not depend on PROJ's last bits.
It changes only under a tier A brief; `WORST_ROUTE_REGEN_GOLDEN=1 uv run pytest tests/golden`
rewrites it.
