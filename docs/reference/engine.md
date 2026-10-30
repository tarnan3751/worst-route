# Engine

How a route is planned, made worse and proven legal: the route model and its one mutation, the
request and its masks, snapping, the seed route and the baseline, the objective interface and
the distance mode, the detour strategies, the worsen loop, the validator, route files and the
`plan` / `validate` commands. Code: `src/worst_route/engine/`. The design it implements is
`docs/PLAN.md` §3.1–3.6, §3.9, §3.11 and §8.1; the conventions are `CLAUDE.md`. Everything
runs on a build's `GraphData` through `GraphBackend` and `SpatialIndex`
(`docs/reference/graph.md`): `CsrBackend` by default, `PythonBackend` when asked, with the same
routes either way; on a build with transition tables only `PythonBackend` can search yet
(**Searches**), and the default takes it there.

| Module | What it holds |
|---|---|
| `route.py` | `Route`, `Splice`, `Metrics`, the `IllegalSplice` refusals, `check_route`, `splice_arrays`, `twins_of` |
| `validator.py` | `validate_route`, `ValidationReport`, `Check`, `ValidationFailed`, `RouteLike` |
| `request.py` | `RouteRequest`, `RequestMasks`, `request_masks`, `auto_tier`, `auto_leash_m` |
| `snap.py` | `snap_stops`, `resnap`, `apart_from`, `role_of`, `is_round_trip`, `StopUnreachable` |
| `search.py` | `shortest_path`, `simple_paths`: which backend search builds a path |
| `seed_route.py` | `resolve_stops`, `baseline_route` / `Baseline`, `seed_route` / `SeedRoute` |
| `objectives/` | `base.py` (`Objective`, `BaseObjective`), `distance.py` (`Distance`), `OBJECTIVES`, `make_objective` |
| `detour.py` | `find_detour`, `pick_strategy`, `waypoint_path`, `random_weight_path`, `local_exhaustive_paths` |
| `worsen.py` | `worsen`, `run_worsen`, `pick_segment`, `UnusedNodes`, `Budget`, `ProgressEvent`, `WorsenResult` |
| `plan.py` | `plan_route`, `PlanResult` |
| `io.py` | `route_document`, `read_route` / `SavedRoute`, `route_geojson` |

`ENGINE_VERSION` (`engine/__init__.py`) is recorded with every route: bump it when the same
build, request and seed would give a different route. The engine's numbers (thresholds,
weights, radii) are in `config.py`.

## Routes

`Route` (frozen; arrays read-only) holds `nodes` and `edges` (`edges[k]` runs `nodes[k]` →
`nodes[k + 1]`), `stop_idx` (the stops' positions: strictly increasing, the first 0, the last
`len(nodes) - 1`), `used_nodes` (bool[n]), `banned_edges` (bool[m]: the route's edges and
their twins), `ban_count` (int32[m]: how many of the route's edges ban each edge, as itself or
as a twin), `score` and `metrics` (`Metrics(length_m, time_s, counters)`), `seed` and the
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
banned iff it is a kept edge or a twin of one (twins are symmetric), that is iff its
`ban_count` less the removed edges' bans is positive. So a release, and the splice check's
test of the bans a detour meets, cost the window, not the route. `extra_banned` is what
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

## Searches

Every path the engine builds (a baseline leg, a seed leg, a detour) comes from `search.py`.
On a build with transition rows, `shortest_path` is the transition-aware
`GraphBackend.dijkstra_edges` and `simple_paths` the DFS with its turn checks
(`docs/reference/graph.md`, **The transition-aware search**): a path turns only where the
tables allow, at the junctions inside it and, given the edge it continues (`entry_edge`) and
the edge it hands over to (`exit_edge`), at its two ends. On a build without rows no turn can
be forbidden, so they are the node-state `dijkstra` and the plain DFS, and the turn edges are
ignored: every backend gives the same paths there, byte for byte, and the routes on such builds
(the test lattices, the golden route) are what they were before the tables were searched. A
backend that cannot search a table yet raises `NotImplementedError` on a build with rows.

The transition-aware search answers None when the cheapest legal path passes a node twice (a
table can make the cheapest legal way a loop round a block); such a path is illegal for a route
anyway, so the caller's fallback applies as for no path at all.

**Twins.** A path a route is made of (a seed leg, the waypoint strategy's two searches, the
random-weight search) is searched with `avoid_twins`: when it drives an edge and one of its
twins (both carriageways of one road, as in a U-turn across a median), the later edge of the
pair that closes first along the path is banned and the search made again, up to
`TWIN_RESEARCH_ROUNDS` (8) times; then there is no path. The bans depend only on the paths
found, so this is deterministic. The baseline is not: a GPS may drive both carriageways.

## Seed and baseline

**The baseline** (`baseline_route`) is what a sane GPS would drive: the shortest path on length
between consecutive stops, within the request's masks and nothing else, concatenated. Its legs
may share nodes. It is the denominator of the ratio. Each leg turns only where the tables allow,
independently of the leg before it (no entry edge at a via), so the baseline is never longer
than the seed; a leg whose cheapest legal path would pass a node twice is the turn-blind
shortest path instead, a lower bound, so the baseline exists whenever the seed does.

**Settling the stops** (`resolve_stops(data, spatial, backend, request, masks)`) comes first:
the stops are snapped, and when the seed cannot be built they fall back (step 4 below). It is
deterministic for a request and a build, so `worst-route validate` re-derives the stops of a
saved route with it; the cost is a few seed builds.

**The seed** (`seed_route`, on the resolved stops) is the route the loop starts from:

1. **Legs.** The legs are built in order on length. Each forbids the nodes of the other legs
   built so far and every other stop's node, and bans their edges' twins. Each continues the
   leg before it (`entry_edge`, its last edge) and hands over to the leg after it when that one
   is built (`exit_edge`, its first edge, which matters when the penalty re-solve rebuilds an
   earlier leg), so the turn at every via is one the tables allow. A leg driving an edge and
   its twin is searched again without the later one (**Searches**), and a round trip's leg
   whose other legs drive a single edge avoids that edge's road unit, so a trip to a neighbour
   and back is not one road out and back.
2. **Penalty re-solve.** When a leg fails, up to 4 rounds (`docs/PLAN.md` §3.3): the leg's
   unconstrained path (turning legally, from the same entry edge) names the nodes other legs hold; those legs are rebuilt with 8× the
   weight on every edge touching those nodes, keeping their old path when they have no other;
   then the leg is tried again.
3. **Failed legs.** A leg that still fails, a leg that would not move (two stops on one node),
   or a seed `check_route` refuses names the leg.
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
  `initial_radius` and `search_budget(radius)` (default 8 × radius, `SEARCH_BUDGET_FACTOR`).
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
objective, rng, entry_edge, exit_edge, strategy=None, refused=None)` returns a path from a =
`nodes[i]` to b = `nodes[j]`, or None. It returns a path only if the path differs from the
removed run and `route.check_splice` accepts it; each candidate the splice refuses is tallied
by its refusal class in `refused`, a `Counter`, when one is passed. `forbid` and `banned` are the
loop's search masks. `banned` includes the request's and the objective's bans, and is passed to
the splice check as `extra_banned`. `entry_edge` is `edges[i − 1]` and `exit_edge` is
`edges[j]` (−1 at the route's ends): every strategy searches through `search.py` from the one
to the other, so on a build with transition rows the detour's turns, the window's two boundary
junctions included, are ones the tables allow.

**Choosing a strategy.** It is drawn from `rng` with weights 0.6 waypoint, 0.3 random weight,
0.1 local exhaustive. Local exhaustive is allowed only when a and b lie less than 800 m apart;
otherwise the draw is 2/3 waypoint, 1/3 random weight. `strategy` forces one (tests, and the
round-trip seed).

- **Waypoint.** A waypoint w is drawn from the nodes the search may enter within 4 × radius of
  the midpoint of a and b (`SpatialIndex.nodes_within`, a scan of the request's nodes: a
  KD-tree query over the whole build cost a millisecond per attempt once the ball covered the
  area). The pool is the objective's when it has one; a and b are never
  drawn. Half the draws (`WAYPOINT_UNIFORM_PROBABILITY`) are uniform over those nodes, the
  others ∝ exp(−distance / radius), so a waypoint often lands far out in the ball. Then a → w,
  never through b, and
  w → b, avoiding the first leg's nodes and its edges' twins. Each search is on
  `search_weights` and bounded by `search_budget(radius)`. The first search continues
  `entry_edge`; the second continues the first's last edge (the turn at the waypoint) and hands
  over to `exit_edge`. Both are searched again without a twin they drive; when a = b (a loop)
  the second also avoids the roads (`edge_unit`) of the first, so a loop is never one road out
  and back.
- **Random weight.** a → b on a copy of `search_weights` in which every edge leaving a node
  of the request's mask within 4 × radius of the midpoint (`SpatialIndex.edges_from_within`)
  is multiplied by U(0.2, 5), drawn from `rng` in ascending edge id; the edges farther out, or
  leaving a node outside the request's mask, keep their weight, so the draws do not grow with
  the graph. Edges leaving the route's own nodes inside the mask are drawn for too, though the
  search, which forbids those nodes, never uses them.
  Bounded by the same budget in scaled cost, from `entry_edge` to `exit_edge`, and searched
  again without a twin it drives.
- **Local exhaustive.** The simple paths a → b of at most 25 edges, up to 5000 of them. They
  are searched in a ball around the midpoint of radius 3 × dist(a, b), capped at 400 m, from
  `entry_edge` to `exit_edge`. A Dijkstra probe skips the enumeration when b is unreachable;
  it ignores turns, since a legal path exists only where a turn-blind one does, and the
  transition-aware search could answer None where a legal path exists (when its cheapest walk
  passes a node twice). The paths are ranked by
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

**What the searches cannot avoid.** A refused candidate is a failed attempt. The local
exhaustive paths are not searched again for twins: a path driving an edge and its twin is
refused, and the next one ranked is tried.

On a one- or two-edge window, the randomly weighted direct edge usually wins, so random weight
seldom finds a new path there; the waypoint is the workhorse.

## The loop

`worsen(backend, spatial, data, route, objective, budget, rng, node_mask, *, r_max,
banned_edges=None, check_delta=False)` is a generator of `ProgressEvent(iteration, elapsed_s,
score, metrics, splice)`, one per accepted splice. The `Splice(i, j, nodes, edges)` replays it
on a copy of the route. The generator returns `WorsenResult(best, events, attempts, accepted,
stopped_because, elapsed_s, refused_transitions)`, and `run_worsen(..., on_event=None)` drives
it. `refused_transitions` counts the candidates the splice refused with `TransitionForbidden`:
the searches see the tables, so a nonzero count means a search returned a turn it should not
have. Each attempt:

1. **Window** (`pick_segment`). One edge (j = i + 1) with probability 0.8, else a run of
   1 + a geometric number of edges, 3 on average (`RUN_EDGES_MEAN`) and at most 12
   (`SEGMENT_RUN_MAX`), cut short at the next stop, so no stop is ever inside it. With
   probability 0.3 (`SEGMENT_BIAS_PROBABILITY`) the window starts at one of the 5 route edges
   (`SEGMENT_BIAS_TOP`) with the most unused nodes of the request's mask near their midpoint,
   else at a uniform edge. The counts (`UnusedNodes`) are kept on a 100 m grid
   (`SEGMENT_BIAS_CELL_M`), coarser where the active area is so wide that the grid would hold
   more than 2,000,000 cells (`UNUSED_GRID_MAX_CELLS`; a 300 km square gets cells of about
   214 m), and its summed-area table is rebuilt on every accepted splice; an
   edge's count is read in the square of half-side 4 × radius around its midpoint (a square
   standing for the waypoint's ball), and the 5 are recomputed when the route or the radius
   changes, ties to the earlier edge.
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

**Cycles.** The run goes in cycles, so it keeps searching while searching still pays. A cycle
ends after more than 400 failures in a row (`CONVERGED_FAILS`) at the smallest radius; the
radius goes back to `objective.initial_radius` and a new cycle starts, giving every radius its
20 tries again around the longer route.

**Stopping**: `"budget"` when `max_iterations` attempts are made or `budget.seconds` have
passed; `"converged"` when 3 cycles in a row (`CONVERGED_EMPTY_CYCLES`) accept nothing, so a
run ends 1,604 attempts after its last splice: the rest of that splice's cycle, then three
empty ones. Target modes
(ratio, duration) are M2; their hooks (`target_value`, `better`) are in the interface.

**Determinism.** Every random choice is drawn from one `numpy.random.Generator(PCG64(seed))`, in
a fixed order: the round-trip seed, then the loop's windows, strategies, waypoints and weights.
Candidate lists are sorted arrays, and no set is iterated. A seed therefore gives the same
sequence of splices on any machine. The clock only decides when to stop:

- a run that stops on iterations is reproduced byte for byte;
- a run that stops on time is a prefix of the same sequence, and is reproduced by planning
  again with `max_iterations` set to its `run.attempts` and a budget in seconds at least as
  large as the first run's (or the clock may cut it shorter).

**Measured** on the dev machine, with the reference backend unless the row says otherwise:

| Graph | Result |
|---|---|
| 20 × 20 grid, corner to corner | converges in 0.2–0.4 s (3,800–6,800 attempts) at 10.3×, using 391–393 of 400 nodes (seeds 0–3) |
| 30 × 30 grid, start, centre via and end | converges in 0.5–0.6 s at 14.9–15.1× |
| `grid_graph(100, 100, 100)` corner to corner, 20 s budget | about 490 attempts/s (the review's run), so the default `max_iterations` of 5000 stops it at about 10 s, at 33×; after random weight was limited to the window's ball, about 680 attempts/s, the 5000 attempts in about 7.4 s, at 33–34× |
| the same, 5000 attempts, both backends in one session (seeds 1 and 2) | reference 1007–1072 attempts/s, `CsrBackend` 1101–1164 attempts/s; identical routes, at 34.00× and 33.44×; with the release by ban counts and the balls scanning the request's nodes, reference 3,240–3,700 attempts/s, `CsrBackend` 5,120–5,400 attempts/s, the same routes; with the loop tuned for the M1 acceptance (8 × radius searches, longer windows, the segment bias, waypoints drawn half uniformly), reference 1,775–2,134 attempts/s, `CsrBackend` 3,363–3,715 attempts/s, identical routes at 33.14× and 31.63× |

On that grid the backend was not what a run waited on. In a profile of a `CsrBackend` run, the
searches took about 3% of its time; most of the rest was the loop's own NumPy work, now gone:
- `np.isin` in `Route.release` (the bans its window keeps), about 45%, before the route kept
  its ban counts (**Routes**);
- `SpatialIndex.bubble_mask`, about 30%, before the balls scanned the request's nodes
  (**Detours**).

On that grid a 20 s budget is never reached: `max_iterations` (default 5000) caps the run
first, so a longer search needs `--max-iterations` raised as well as `--budget`.

## Planning

`plan_route(data, request, *, backend=None, spatial=None, on_progress=None, check_delta=False)
-> PlanResult(request, masks, snaps, baseline, route, run, report, backend)` runs a request end
to end. It searches with `backend`, else `make_backend(data)` (`config.DEFAULT_BACKEND`, `auto`:
`CsrBackend` when numba imports); `PlanResult.backend` is the name of the one that searched. The
steps run in this order:

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
- `run`: `{attempts, accepted, stopped_because, elapsed_s, backend}` (`backend` is `"csr"` or
  `"python"`; every backend gives the same route).

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
                 [--backend auto|csr|python]
worst-route validate route.json [--region R] [--build-id ID] [--backend auto|csr|python]
```

`--backend` picks the graph backend (`docs/reference/graph.md`, **Choosing a backend**). The
default is `auto`, which is `csr` when numba imports and the build has no transition tables,
which `csr` cannot search yet (**Searches**); on a build with tables, Portland's among them,
`auto` is `python` and the `backend:` line says why (`backend: python (csr cannot search turn
tables yet)`). Where both can search, the choice changes the speed and nothing else. An unknown
name is exit 2. Asking for `csr` when numba does not import prints one line on stderr ("the csr
backend needs numba, which did not import: …") and exits 1; asking for it on a build with
tables, `plan` prints `cannot plan: the csr backend cannot search turn tables yet; use --backend
python`, `validate` the same after `cannot validate:`, one line and exit 1.

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
- the backend it searched with (`backend: csr`);
- the validator's checks.

It writes the files it is asked for. A stop that cannot be snapped or reached is one line,
`cannot plan: ` and the error (for an unreachable stop, the leg and the stops it moved), and
exit 1. A route that fails the validator is reported as an engine bug, exit
1, and never printed. A bad argument is exit 2.

**`validate`** reads a `route.json` and loads the build it names (or `--region` /
`--build-id`). A file it cannot read (a missing field, an id beyond int32) is `cannot read …`
and exit 1, and so is a file made by another engine version (`… was made by engine version 1;
this is version 2: re-plan`): its stops are re-derived with this engine's search, which could
put them elsewhere. It refuses a build whose `graph_version` differs, recomputes the request's masks
from the file's request, and re-derives the stops with `resolve_stops` on the backend
`--backend` names. It prints the file's line, the backend's (`backend: csr`), one line per
check, then `valid`, or exits 1.

**On Portland** (the M1 acceptance, `docs/PLAN.md` §7 M1 step 9: `uv run python tools/acceptance.py
--region portland --pair-seed 2026 --pairs 20 --seed 1 --budget 30`, on build e4b3b4ad90db,
`graph_version` 45f650e868fa; the reference backend, attempts unlimited; engine version 2; the
named trips with `worst-route plan --budget 30 --max-iterations 1000000 --backend python`):

| | Result |
|---|---|
| 20 random pairs 3–15 km apart, seed 1 | all 20 plan and the validator passes all 20; median ratio 21.5× (quartiles 9.0× and 27.0×, range 3.3–45.2×); every run converged, in 0.8–19.3 s; median 5,400 attempts/s; no stop moved; `refused_transitions` 0 |
| the first 5 pairs again with seed 2 | Jaccard of the two routes' edges 0.10–0.16, of their road units 0.20–0.28 |
| Powell's Books → Reed College, 7.71 km by the baseline, seeds 1–3 | 62.4×, 62.2×, 57.9× (447–482 km, 4,673–4,913 nodes), converged in 9.9–12.5 s |
| PDX → Beaverton Transit Center, 26.4 km | 7.3× (192.1 km), converged in 4.5 s; before the searches saw the transition tables it could not be planned |
| SE Caruthers cul-de-sac via | the via moves 86 m to the nearest through street with its note; 71.6× (389.8 km) |
| Round trip from Pioneer Courthouse Square | a 164.4 km loop |

Along the way (same pairs, seed 1): with the searches blind to the tables, 14 pairs planned at a
median of 6.4×; seeing them, 19 at 6.6× (`refused_transitions` 0 from then on); with the twin
re-search, 20 at 7.0× (4 re-searches in seed legs, 2 legs, which had made one pair fail and moved
another's stop 1,024 m; 219 in detours); with the cycles, the ban counts and the masked balls,
8.2×; then the loop's tuning (**The loop**), each step measured on the 20 pairs: the segment bias
8.3× (and 7.1× → 8.8× on seed 2), longer runs 8.3×, an 8 × radius search budget 13.3×, half the
waypoints drawn uniformly 18.0×, three empty cycles to stop 20.2×. Runs before the tuning used
2–23% of the road their request allows; the ratio was the search running out of moves, not the
leash.

**On Portland with `CsrBackend`** (the same build, engine version 1, before the searches saw the
transition tables; `CsrBackend` cannot search them yet, so Portland now plans on the reference).
The machine was busy with other work (load average about 20 during the acceptance runs), so a
rate compares only with the run made just before or after it.
- **Powell's → Reed, seed 1, `--budget 30 --max-iterations 1000000`.** The reference's 49.74×
  with the same route bytes, converging after 18,708 attempts in 11.0 s (1,700 attempts/s); the
  reference took 12.0 s (1,560/s).
- **The same trip at the default 5,000 attempts.** 32.63× on both backends, 4.4 s against 5.3 s.
- **The acceptance run with `--backend csr`.** It gives every result above (14 of 20 planned,
  the validator passing all 14, a median of 6.4× from 2.4× to 31.1×, the same 6 failures),
  every run converging. Run back to back with the reference, the two agree pair for pair, except
  where the busy machine let the reference's 30 s run out before it converged: pair 05, and the
  seed-2 re-plans of pairs 05 and 06. Those are the prefix property at work: `CsrBackend` with `max_iterations` set to the reference's
  17,517 attempts gives pair 05's 432.0 km route exactly.
- **A profile of the CSR Powell's → Reed run.** `Route.release` takes 44% of the time and
  `bubble_mask` 18%. The searches take about 7%, now that the weight check scans each weights
  array once (`docs/reference/graph.md`, **Backends**); before that, the searches took 9%.
  After that change, on a busier machine (load average about 12), the same 18,708 attempts took
  19.8–24.0 s on `CsrBackend` against 25.2–28.0 s on the reference.

## Limitations

- **Transitions.** The searches see the transition tables (**Searches**), but a leg or a part
  of a detour whose cheapest legal path passes a node twice is no path: the search does not
  look for the cheapest node-simple legal path, which a turn table can make longer than the
  cheapest walk. The seed then falls back as for an unreachable leg.
- **Twins within a leg.** A leg driving an edge and its twin is searched again without the
  later one, up to 8 times; a leg that needs more rounds, or whose only way drives a twin
  pair, falls back as an unreachable one.
- **Speed.** `CsrBackend` has no transition-aware kernel yet, so a build with transition tables,
  Portland's among them, plans on the reference, which `auto` takes there. On a build without
  tables its searches are 8–13× the reference's, and a whole run 1.5–1.9× (**The loop**,
  Measured).
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
| `test_route.py` | every refusal, round trips, and the release and the ban counts against a brute force on random twins |
| `test_validator.py` | each violation caught by the right check, including a cluster entered twice through its members, a shared shape point and non-finite metrics |
| `test_request.py` | the request model and its bounds, tier and leash auto, the masks and the tier's edge bans |
| `test_snap.py` | roles, cul-de-sacs, stops kept apart, round trips only for identical points, an end near the start moved on, re-snapping |
| `test_search.py` | the turn edges counting only on a build with rows, a path driving both carriageways searched again, and the rounds' limit |
| `test_seed_route.py` | `resolve_stops` deterministic, node-disjoint legs, legs avoiding later stops, the penalty re-solve on a corridor, the baseline, the re-snap walk out of a one-entrance ring, the start never moving, a point-to-point trip never collapsing onto the start, a leg between two stops on one node, unreachable legs named by both ends, a leg going round a junction that allows only straight on, the turn at a via, the baseline's legal legs and its turn-blind fallback, a leg driving both carriageways going round, a round trip to a neighbour not one road out and back, round-trip loops (never back along the way out) |
| `test_objectives.py` | `Distance`, the radius, and `delta` against a full re-score for every kind of term |
| `test_detour.py` | each strategy's legal detours and None when none exists, the strategy weights, random weight drawing only near the window, the local-exhaustive ranking and the refused-candidate skip, no turn refused on a lattice of straight-only junctions (and many when the searches are made turn-blind) |
| `test_worsen.py` | over fixed seeds, `check_delta` on: the score only rises, every event replays to the best route, budgets in iterations and seconds, convergence, a splice after two empty cycles and the run stopping only after three, the segment bias's rate and its anchors (the edges with the most unused nodes near), the unused-node grid's cap, the same seed gives the same bytes, different seeds give edge sets with Jaccard < 0.7, random one-way towns with twins always validate, and so do those with turn tables (no turn refused), a tier's shortcut ban, a point-to-point request never becoming a round trip, identical points planning a loop, an unexpected seed error reported as `StopUnreachable`; the same-seed test on both backends, and both backends planning the same route, splice for splice, on the grid and on one-way towns |
| `test_io.py` | the route files |
| `test_cli_plan.py` | `plan` and `validate` on builds written with `write_build`, including the error paths, a route that fails validation never printed, a truncated and re-edited file caught by its re-derived stops, an id beyond int32, `--backend` on both commands (the same route.json on both backends but the backend and the time), `auto` taking `python` on a build with turn tables and saying so, `csr` there one line on both commands, a file from another engine version refused |

**The acceptance run**, `tools/acceptance.py`, plans random stop pairs on a saved build, so it
needs `WORST_ROUTE_DATA` and is not part of the gate:

```
uv run python tools/acceptance.py --region portland [--build-id ID] [--pairs 20] [--seed 1]
    [--budget 30] [--backend auto|csr|python] [--min-km 3] [--max-km 15] [--jaccard 5]
    [--pair-seed 2026] [--near-m 250] [--out tools/out/acceptance.json]
```

Its `--backend` is `make_backend`'s, `auto` by default: the reference on a build with
transition tables such as Portland's (M1's numbers were measured on it), `csr` on one without;
the report records which backend ran. A run bounded by time goes further on a faster backend,
so `csr` can give different routes where the reference runs out of time. On a build with
transition tables it first checks that the backend can run the transition-aware search; `csr`
asked for there cannot yet, and the tool prints `cannot plan: the csr backend cannot search
turn tables yet; use --backend python`, one line on stderr, and exits 1.

The pairs are uniform points in the region's bbox with a node within `--near-m`, the end at a
straight-line distance within the bounds, drawn from `PCG64(--pair-seed)`; each is planned with
`plan_route` and no attempt limit, so the budget bounds every run. The first `--jaccard` pairs
that plan are planned again with the next seed and compared (Jaccard of edges and of road
units). A pair that cannot be planned, or whose stop the re-snap fallback moved, is resolved
again on the build without its transition rows, to tell whether the tables stopped it. It prints
a table row per pair and a summary (planned, validator passes, median ratio and range, attempts
per second, the failures the transitions caused, the Jaccards), writes the JSON report, and
exits 1 only when a planned route fails the validator. On Portland it takes under two minutes.

`tests/golden/` freezes one route (seed 7, three stops) on a 20 × 20 lattice whose lon/lat are
rounded to 1e-7°, so its bytes and pinned `graph_version` do not depend on PROJ's last bits;
both backends must reproduce it. It changes only under a tier A brief;
`WORST_ROUTE_REGEN_GOLDEN=1 uv run pytest tests/golden` rewrites it.
