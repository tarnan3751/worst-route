# Engine

How a route is planned, made worse and proven legal: the route model and its one mutation, the
request and its masks, snapping, the seed route and the baseline, the objective interface, the
distance, slowest-time and collect modes, turns and the turn constraints, the detour strategies, the
worsen loop and its targets, the validator, route files and the `plan` / `validate` commands.
Code: `src/worst_route/engine/`. The design it implements is `docs/PLAN.md` §3.1–3.7, §3.9,
§3.11 and §8.1; the conventions are `CLAUDE.md`.
Everything runs on a build's `GraphData` through `GraphBackend` and `SpatialIndex`
(`docs/reference/graph.md`): `CsrBackend` by default, `PythonBackend` when asked, with the same
routes either way, on every build, transition tables included (**Searches**).

| Module | What it holds |
|---|---|
| `route.py` | `Route`, `Splice`, `Metrics`, the `IllegalSplice` refusals, `check_route`, `splice_arrays`, `twins_of` |
| `validator.py` | `validate_route`, `ValidationReport`, `Check`, `ValidationFailed`, `RouteLike` |
| `request.py` | `RouteRequest`, `ObjectiveKey`, `RequestMasks`, `request_masks`, `auto_tier`, `auto_leash_m`, `objective_problem` |
| `target.py` | `Target` (a request's target), `Goal` (what a run is after: better, done, its phases) |
| `snap.py` | `snap_stops`, `resnap`, `apart_from`, `role_of`, `is_round_trip`, `StopUnreachable` |
| `search.py` | `shortest_path`, `simple_paths`: which backend search builds a path |
| `seed_route.py` | `resolve_stops`, `baseline_route` / `Baseline`, `seed_route` / `SeedRoute` |
| `objectives/` | `base.py` (`Objective`, `BaseObjective`, `route_delay_s`), `distance.py` (`Distance`), `time.py` (`SlowestTime`), `collect.py` (`Collect`, `count_items`, `ITEMS`, `CollectItem`), `composite.py` (`Composite`), `OBJECTIVES`, `make_objective` |
| `turns.py` | `turn_classes`, `count_turns` |
| `constraints.py` | `Constraints`, `TurnConstraint`, `NoLeft`, `NoRight`, `constrain`, `NO_CONSTRAINTS` |
| `detour.py` | `find_detour`, `pick_strategy`, `waypoint_path`, `random_weight_path`, `local_exhaustive_paths` |
| `worsen.py` | `worsen`, `run_worsen`, `pick_segment`, `UnusedNodes`, `Budget`, `ProgressEvent`, `WorsenResult` |
| `plan.py` | `plan_route`, `PlanResult`, `check_base`, `BaseRouteMismatch` |
| `io.py` | `route_document`, `read_route` / `SavedRoute`, `route_geojson` |

`ENGINE_VERSION` (`engine/__init__.py`) is recorded with every route: bump it when the same
build, request and seed would give a different route. The engine's numbers (thresholds,
weights, radii) are in `config.py`.

## Routes

`Route` (frozen; arrays read-only) holds `nodes` and `edges` (`edges[k]` runs `nodes[k]` →
`nodes[k + 1]`), `stop_idx` (the stops' positions: strictly increasing, the first 0, the last
`len(nodes) - 1`), `used_nodes` (bool[n]), `banned_edges` (bool[m]: the route's edges and
their twins), `ban_count` (int32[m]: how many of the route's edges ban each edge, as itself or
as a twin), `score` and `metrics` (`Metrics(length_m, time_s, counters, delay_s=)`: the delays
at its signals and stop signs, every mode's), `seed` and the
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
two (identical first and last points make a round trip); `objective` (`"distance"`, the
default, `"time"` or `"collect"`: the keys of `OBJECTIVES`; another is "unknown objective");
`item`, what `collect` collects (`toll_booth`, `level_crossing`, `roundabout` or `bridge`; null
for every other objective); `constraints` (**Constraints**: `{no_left, no_right, strict}`, none
by default, and then left out of the request's JSON, so a request reads as it did before
constraints existed); `tier` (`"auto"` or `"T0"`..`"T4"`); `leash_km` (`"auto"` or in
(0, 150]); `budget_s` (in (0, 120], default 20) and `max_iterations` (default 5000); `seed`
(≥ 0); and `target` (**Targets**: `max`, the default, `ratio` or `duration`; a ratio target is
refused for a round trip, whose baseline is 0 m). Every number must be finite, so a request
(and `route.json`) never holds `Infinity` or `NaN`. `objective_problem(objective, item,
target_kind)` is what the request refuses, as (the field at fault, a sentence): `collect`
without an item, an item for another objective, and a ratio target under `collect`, which
counts items rather than metres; the CLI and the API ask it first, to refuse as early.

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
(the test lattices, the golden route) are what they were before the tables were searched.

The transition-aware search answers None when the cheapest legal path passes a node twice (a
table can make the cheapest legal way a loop round a block); such a path is illegal for a route
anyway, so the caller's fallback applies as for no path at all.

**Turn classes.** `shortest_path` and `simple_paths` take `banned_turn_classes` and
`avoided_turn_classes` (sets of class bits, **Turns**), which `find_detour` passes from the
objective (**Constraints**) and the seed from a strict constraint. Either set makes the search
the transition-aware one, on any build, refusing a turn of those classes at every junction,
the two ends included. A search with avoided classes is made again without them when it finds
nothing, so a soft constraint's turns are avoided where they can be and taken where they must;
the banned ones never are.

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
   earlier leg), so the turn at every via is one the tables allow (and, under a strict turn
   constraint, none it refuses: **Constraints**; a soft one leaves the seed alone). A leg driving an edge and
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

    score = Σ node_terms[v] + Σ edge_terms[e] + Σ pair_term(e_k, e_k+1)
            + first_edge_term(e_0, closing) + global_term(nodes, edges)

- **Terms.** Node terms count a round trip's start once. Pair terms, and the first-edge term
  (the route's first edge, which has no edge before it to pair with: a bridge the route starts
  on; `closing` is a round trip's last edge, which comes back to the start, else −1), are read
  only when `has_pair`, the global term only when `has_global`. `global_term` takes a route's
  arrays, so a candidate can be scored before it is spliced.
- **Hooks.** `feasible_pair` and `feasible_window` (constraints), `banned_edges(G, route, i, j)`
  (bans per window), `search_weights` and `waypoint_pool` (search guidance), and
  `initial_radius` and `search_budget(radius)` (default 8 × radius, `SEARCH_BUDGET_FACTOR`).
- **Turn classes.** `banned_turn_classes`, the classes its `feasible_pair` refuses, which no
  search for it proposes; `avoided_turn_classes`, the classes its pair terms penalise, which
  the searches try without first (**Searches**). Both 0 for a mode; a turn constraint's.
- **The rest.** `primary(metrics)`, the quantity a ratio target compares with the baseline's
  (the length for the base class and `Distance`, the experienced time, driving and delays, for
  `SlowestTime`), a static method, so the API's follower reads it from the class without
  making the objective; `constraint_delta(route, i, j, nodes, edges)`, the part of `delta` its
  constraints add (a soft one's penalties, 0 for a mode alone), which a shrinking target reads
  (**Targets**); `target_value` (the mode's own quantity) and `describe` (mode numbers for the
  presentation). Which route is better, and when a run is done, belongs to the run's goal
  (**Targets**), not to the objective. `Metrics.counters` are the mode's own JSON values,
  which ride along in the route files and the API's events.

`BaseObjective(data, *, baseline, r_max)` (the baseline's `Metrics`) gives every hook a neutral
default and caches its term vectors for the build. It implements, once for every mode:

- `evaluate(nodes, edges) -> (score, Metrics)`;
- `score(route)`;
- `delta(route, i, j, nodes, edges)`.

`delta` sums the terms a splice removes and adds: node and edge terms over the removed interior
and the detour; pair terms at both boundaries and inside, and the first-edge term when the
window starts the route or ends a round trip; the global term re-evaluated on the spliced
arrays. Everything is
summed with `math.fsum`, so `delta` equals
`score(after) − score(before)` to rounding, and a change of nothing scores exactly 0. Its
`initial_radius` is a quarter of the baseline length, at least 1 km and at most `r_max`. Its
metrics are the length and the driving time (`time_s`, the edges' `edge_time_s`), summed with
`math.fsum`, and the delays (`delay_s`, `route_delay_s`: the nodes' `node_delay_s`, start and
end included, a round trip's start once), whatever the mode.

**`Distance`** scores the length: its edge terms are `edge_length_m`, and it has no node,
pair or global terms. It searches on length, draws waypoints anywhere, and its target value,
primary quantity and `describe` are the length.

**`SlowestTime`** (`"time"`) scores the time: its edge terms are `edge_time_s`, its node terms
`node_delay_s` (the 30 s signals and 10 s stop signs the pipeline merges into each node), and
it has no pair or global terms. It searches on length × speed / 60 km/h (`SEARCH_SPEED_KMH`):
a road at 60 km/h weighs its length, a slower one less, a faster one more, so a search on its
way prefers slow roads. Its waypoints are drawn from the slow nodes, those at least half of
whose out-edges are of class unclassified or slower (residential, living street, alley),
worked out once per build. Its first radius is the base class's; its primary quantity and its
target value are the experienced time, `time_s + delay_s`, so a ratio target under it is the
route's experienced time over the baseline's, as a duration target measures it under any mode
(**Targets**). `describe` gives `time_s`,
`delay_s_total` and `slowest_edge_class_share` (the share of the length on those slow
classes). On Portland it converges where the distance does (**On Portland**): both fill the
area the leash allows, and either's experienced time is about 52× the baseline's (67× its
driving time).

**`Collect`** (`objective: "collect"` with an `item`; `docs/PLAN.md` §3.7) passes as many of one
item as it can, then drives as far as it can: its score is items × `COLLECT_ITEM_WEIGHT` (1e6) +
the length in metres, items first as long as a splice changes the length by less than 1,000 km.

| Item | Counted | Terms |
|---|---|---|
| `toll_booth` | the toll booths on the route's edges (`edge_toll_booths`) | edge terms: weight × count + length |
| `level_crossing` | the level crossings on its edges (`edge_level_crossings`) | the same |
| `roundabout` | the rings of the super-junctions it passes (`node_roundabouts`; a roundabout is one node, entered once, so its ring counts once; an interchange that holds a ring counts it) | node terms: weight × rings; edge terms: length |
| `bridge` | the runs of bridge edges (`EdgeFlag.BRIDGE`), so a bridge split by a junction counts once, and so does a run through a round trip's start, which the route enters at its end and leaves at its start (a round trip made only of bridge edges would count none) | pair term: weight when a bridge edge follows an edge that is not one; first-edge term: weight when the route starts on one, unless it is a round trip whose last edge is one too (`first_edge_term(e, closing)`); edge terms: length |

An item inside a two-way road is on both its edges (enrich, `docs/reference/pipeline.md`), of
which a route drives one, so it counts once; one at a junction is on one out-edge of the
junction. The detours search on length. A waypoint is drawn from the places of the items the
route has not collected (the ends of the edges holding one, the super-junctions holding a
ring) within the request's active nodes, less the route's own nodes, worked out once per
route; once none is left in the area, from any node, so a collecting run in an area without its
item searches as distance does. A detour whose waypoint ball holds none of the pool draws its
waypoint from any node of the ball (**Detours**), so the pool steers where it can and a run is
never starved by items far from the window or out of reach (a toll booth behind a cut edge).
The first radius is half the baseline (`COLLECT_RADIUS_FRACTION`), at least 2 km
(`COLLECT_RADIUS_MIN_M`), at most `r_max`: items are sparse.

`count_items(data, nodes, edges)` counts all four items on any route the same way (a round
trip's start, and a bridge run through it, once); the route files give it for every mode
(**Route files**), and the validator recounts the items a route reports (**The validator**).
A collecting route's `Metrics.counters` are `{item, items}`, `items` that count, so the API's progress
events carry them as the route grows. Its target value is its item's count, and `describe` is
`{item, count, candidates_in_area}`, the last the item's places in the request's area (an item
inside a two-way road once, a bridge once per road it carries, so a bridge split by junctions
several times). A ratio target is refused under `collect` (**Requests**); a duration target
compares the experienced time as for distance (**Targets**).

**A new mode** is a subclass of `BaseObjective` that overrides the terms and hooks it needs,
registered in `OBJECTIVES` under its request key (and in `ObjectiveKey`; `make_objective`
gives `collect` the request's item and active nodes). `tests/engine/test_objectives.py` holds
`delta` to a full re-score with a test objective that has every kind of term, the first-edge
term included; a new mode gets the same test (`SlowestTime` has it, node terms included, and
`test_collect.py` for each item). A constraint is an objective about turns (**Constraints**); `Composite(objective,
constraints)` (`objectives/composite.py`) is the one objective the engine runs when a request
has any: the sum of every part's score and `delta`, the objective's metrics, guidance, target
value and primary quantity, every part's feasibility (all must agree), the union of their
bans and of their turn classes, and as its `constraint_delta` the sum of the constraints'
`delta`s.

## Turns

`turns.py` classes a turn from edge e into edge f by its change of bearing, `delta =
wrap(bearing_out(f) − bearing_in(e))` in (−180, 180] degrees clockwise: **straight** when
|delta| ≤ 30 (`TURN_STRAIGHT_DEG`), **right** when 30 < delta ≤ 160 (`TURN_UTURN_DEG`), **left**
when −160 ≤ delta < −30, a **U-turn** beyond. The bearings are the build's, over the last and
the first 20 m (`BEARING_SAMPLE_M`) of the two roads, cleanup's stubs to a merged node's
position left out (`docs/reference/pipeline.md`, enrich), so a junction is classed by how its
roads arrive and leave; at a super-junction (an interchange or a roundabout, one node) that is
the
edge that enters it and the edge that leaves it, whatever the way through it looks like
(coarse: a cloverleaf's loop ramp from east to north counts as a left turn). The rule is one
table by delta modulo 360, `graph.backend.TURN_CLASS`, which the backends' searches read too;
a class's code is its bit in a set of classes: left 1, right 2, U-turn 4, and straight 0, which
no set holds. `turn_classes(data, e_in, e_out)` classes many turns at once and
`count_turns(data, edges)` counts a route's, `{left, right, straight, u_turn}`, for its metrics
(**Route files**). The validator reads them only to prove a strict constraint (**The
validator**, `constraints`).

## Constraints

`constraints.py`: a request's `Constraints(no_left, no_right, strict)` adds turn constraints
to its objective; `no_left` and `no_right` together is refused ("straight only is not a
route"). Each is a `TurnConstraint`, an objective about two classes: `NoLeft` about left turns
and U-turns, `NoRight` about right turns and U-turns. `constrain(objective, constraints, data)`
gives the objective the engine runs: the objective alone without constraints, else a
`Composite` of it and them. The baseline is never constrained (a sane GPS turns left), and a
ratio target under a constraint compares the objective's primary quantity as before; shrinking
toward a target, a splice must not add a net avoided turn (**Targets**).

- **Soft** (the default): "few such turns". Each such turn, the two boundaries of a window
  included, is a pair term of minus a kilometre's worth of the objective's score,
  `turn_penalty_for(objective)` (the one place that knows it, keyed on the objective's primary
  quantity): 1,000 m (`TURN_PENALTY_M`), or 90 s under the slowest time (`TURN_PENALTY_S`, a
  kilometre at 40 km/h). So the loop takes a detour that adds one only for more than that, and
  one that removes one even at a loss of up to that. Its classes
  are its `avoided_turn_classes`: every detour search tries without them first (**Searches**).
  The seed is the plain one, and the route's metrics count its turns.
- **Strict** (`strict`): none at all. Its classes are its `banned_turn_classes`, which every
  search for the route refuses: the seed's legs (and the penalty re-solve and the stops'
  fallback, so `resolve_stops` and `worst-route validate` re-derive the same stops), the
  round-trip loop and every detour strategy; `feasible_pair` and `feasible_window` (the turns
  into and out of the detour included) refuse them too, the safety net. A leg that cannot be
  built is `InfeasibleTurnConstraint` (a `StopUnreachable` naming its `constraint`, the API's
  `INFEASIBLE_TURN_CONSTRAINT`), its message ending "under the strict no-left constraint"
  (`Constraints.note`), when the seed's legs can be built without the constraint (one more
  build, on failure only); when they cannot either, the constraint is not the reason, and it
  is the plain `StopUnreachable`. A stop the fallback moves notes "nearer nodes cannot be reached
  under the strict no-left constraint"; `check_base` refuses a base route with such a turn (a
  soft constraint takes any base). The validator proves it from the raw data (**The
  validator**, `constraints`). `strict` without `no_left` or `no_right` is refused ("strict
  needs no_left or no_right").

**Why strict is rarely satisfiable.** On a street grid a net left turn without left turns is
three rights round a block, which comes back through the junction it left, and no junction may
repeat; a node-simple way must spiral outward round the trip's own start, mostly beyond the
leash, and the edge-state search answers None for the cheapest walk that loops (**Searches**).
On Portland, strict no-left plans 4 of the 20 acceptance pairs and strict no-right 3 (the
others fail at the seed, after the stops' fallback), and Powell's → Reed refuses (**On
Portland**). The same geometry bounds a soft run: a detour that adds no left turn needs right
turns at both ends of its window, or two inside it, so where a left turn costs more than any
detour gains the route barely grows. Hence the penalty of a kilometre, not more: at 10⁶ (fewest
left turns first) the soft modes gave a median of 1.12× on the acceptance pairs; at 1,000 they
give 5.33× (no-left) and 6.47× (no-right), against 21.5× without (**On Portland**). And a soft
route barely shrinks toward a smaller target: a shorter way round mostly needs the left turn
its three rights stood for, which shrinking may not add (**Targets**).

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
  area). The pool is the objective's when it has one and the ball holds some of it, else every
  node of the ball (so a pool steers, and never starves the search); a and b are never
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
  returned. A run shrinking toward a target (`shrink`, its goal) keeps only the paths it can
  take, those that lose on the objective's own part (`delta − constraint_delta` < 0) and add
  no net avoided turn (`constraint_delta` ≥ 0; **Targets**), so a longer path that is faster
  never shadows a shorter one for a duration, and ranks them by how close each would bring the
  route to the target's band (`Goal.shrink_rank`: 0 inside it, the distance above it, last
  below it).

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
   (`SEGMENT_RUN_MAX`), cut short at the next stop, so no stop is ever inside it (shrinking
   toward a target, 6 on average and at most 24: `SHRINK_RUN_EDGES_MEAN`,
   `SHRINK_SEGMENT_RUN_MAX`). With
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
4. **Accept** when the goal accepts the detour (`Goal.accepts`; for `max`, the default, when
   its `objective.delta` is strictly positive; **Targets**) and `feasible_window` agrees. The
   new score and metrics are a full `objective.evaluate` of the spliced arrays.
   With `check_delta` (tests), the loop asserts that this equals the current score plus
   `delta`. The splice is told the request's and objective's bans as `extra_banned`. `best` is
   tracked by `Goal.better`: for `max` the highest score.

**The ambition radius** starts at `objective.initial_radius`. It grows 1.5× on every accepted
splice, up to `r_max`, and halves after every 20 failures in a row, down to
`min(300 m, r_max)`. A failure is no detour, one the goal does not accept (for `max`, a gain of
zero or less), or an infeasible window. Shrinking toward a target, it stays at that floor.

**Cycles.** The run goes in cycles, so it keeps searching while searching still pays. A cycle
ends after more than 400 failures in a row (`CONVERGED_FAILS`) at the smallest radius; the
radius goes back to `objective.initial_radius` and a new cycle starts, giving every radius its
20 tries again around the longer route.

**Stopping**: `"target"` when the route is within its target (**Targets**), before the first
attempt too; `"budget"` when `max_iterations` attempts are made or `budget.seconds` have
passed; `"converged"` when 3 cycles in a row (`CONVERGED_EMPTY_CYCLES`) accept nothing, so a
run ends 1,604 attempts after its last splice: the rest of that splice's cycle, then three
empty ones. The same rule holds in every phase of a target run.

## Targets

A request's `target` (`target.py`) says how much worse: `max` (as bad as the budget allows,
the default), `ratio` (the route's length as `value` × the baseline's, more than 1) or
`duration` (`value` seconds, more than 0 and at most 7 days, `TARGET_MAX_DURATION_S`). A target
is reached within ±2 % (`TARGET_TOLERANCE`): the band [T × 0.98, T × 1.02], ends included. A
ratio is the objective's primary quantity over the baseline's (`Objective.primary`: the length
for distance, the experienced time for the slowest time); a duration is always the experienced
time, `time_s + delay_s`: the driving and the waiting at the route's signals and stop signs.
Under `collect` a ratio target is refused (it counts items, not metres) and a duration target
compares the experienced time the same way: growing, a detour is taken when it raises the score
(an item, or length with no item lost) and the experienced time. Shrinking (a `--base` route
past a smaller duration target) takes replacements that lower the score and the time, so it
gives items back: a shorter path past fewer items is a loss like any other.

`Goal(target, data, *, primary, baseline)` (the objective's `primary` and the baseline's
`Metrics`) is what the loop asks, by a route's **value** (the score for `max`; the ratio of the
primary quantities; the experienced time): `better`, `reached`, `phase`, `candidate` (a
detour's value from its window: `math.fsum` of its edges' lengths and times less the removed
ones', and the delays at the nodes it passes less those at the nodes it removes),
`accepts`, `overshoots`, `shrink_rank` and `describe`. Its rules take values, not routes, so the
API's follower applies them to the splices it is sent (`docs/reference/api.md`, **Jobs**).
`plan_route` makes it once the baseline is known; `PlanResult.goal` holds it.

**Phases.** A target run is in one by its value:

- **grow**, below the band: a detour is taken when it gains (`delta > 0`) and its value rises
  without passing the band's top. A detour that would pass it is a failure, so the radius
  halves after 20 of them as after any 20 failures, and later detours are smaller.
- **shrink**, above the band (a base route past a smaller target, or after an overshoot): a
  replacement is taken when its objective's part of the score loses (`delta −
  constraint_delta < 0`), its constraints' part does not (`constraint_delta ≥ 0`: no net
  avoided turn added, which a soft constraint's penalty would otherwise pay for), and its value
  falls without passing the band's bottom. The radius stays at its floor (300 m), windows are longer, and the
  local-exhaustive paths that lose are ranked by closeness to the band (**Detours**, **The
  loop**).
- **done**, inside the band: the run stops with `"target"`, also before its first attempt.

For a ratio on the distance objective a gain and a rising value are the same thing; for a
duration the second condition also refuses a longer but faster detour while growing (and a
shorter but slower one while shrinking), so the value moves one way only: growing it never
falls, shrinking it never rises.

**The overshoot.** Growing, the loop keeps the smallest detour that would pass the band's top
(ties to the earlier one), from the last accepted splice on. When a whole cycle accepts
nothing, that detour is spliced in at the cycle's end, as one more attempt (so a run cut at that
attempt is the prefix that ends with it), and the run goes on shrinking. It never undershoots
from above, so there is at most one overshoot.

**The best route** is the one closest to the target, the later on a tie. Growing and shrinking
only approach it, so the best is the last route except after an overshoot that shrinking cannot
bring back: on a 5 × 5 lattice, where every corner-to-corner path is 800 m plus a multiple of
200 m, a 2.1× target (1,646–1,714 m) grows to 1,600 m, overshoots to 1,800 m, converges, and
returns the 1,600 m route. For `max`, nothing changes: a gain is taken, the highest score is
best, and the max loop draws and ranks exactly as before (the golden route).

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

`plan_route(data, request, *, backend=None, spatial=None, base=None, on_start=None,
on_progress=None, check_delta=False) -> PlanResult(request, masks, snaps, baseline, route, run,
report, backend, goal)` runs a request end to end. It searches with `backend`, else
`make_backend(data)` (`config.DEFAULT_BACKEND`, `auto`: `CsrBackend` when numba imports);
`PlanResult.backend` is the name of the one that searched. The steps run in this order:

1. the masks;
2. one `Generator(PCG64(request.seed))`;
3. `resolve_stops` (snapping and its fallback, which may move stops; `snaps` are the final
   ones);
4. `seed_route` on them, or, given `base`, that route instead;
5. `baseline_route` on those stops;
6. `make_objective` with the baseline length and `r_max` (for `collect`, the request's item and
   active nodes), and the request's `Goal` (**Targets**) with the baseline length;
7. `Route.new` of the seed (or the base), then `on_start(snaps, masks, baseline, route)`;
8. `run_worsen` toward the goal within the request's budget, calling
   `on_progress(event, baseline)` on every accepted splice;
9. `validate_route` on the best route, against the request's masks and the resolved stops.

The two hooks serve the API (`docs/reference/api.md`). `on_start` reports the route the loop
starts from before the first splice: the seed is not the baseline once there are stops between
the ends, and a client applying splices needs it. `base` ("make it worse") is a route planned
earlier on this build for the same stops, tier and leash, anything the validator reads
(`RouteLike`). Before the loop, `check_base(base, masks, stop_nodes)` refuses it with
`BaseRouteMismatch`, a `ValueError` whose `field` is `"stops"` (its stop nodes are not the ones
step 3 gives) or `"route"` (a node outside the request's active mask, or an edge the request
bans); the API's pre-flight runs the same check. `Route.new` then checks the rest as for any
route (repeats, twins, transitions; an `IllegalSplice` from it is raised as it is, not as
`StopUnreachable`). Nothing is drawn from the rng before the loop, so a continuation is fixed
by the base, the request and its seed, on either backend (`tests/engine/test_plan.py`). A base
past the request's target shrinks toward it, one below it grows, and one within it is returned
as it is, with no attempt (**Targets**). Without the hooks, planning is unchanged.

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
7. **`constraints`**: every node in `node_mask`, no edge in `banned_edges` (the request's
   tier bans), and, under a strict turn constraint (`banned_turn_classes`, from the request's
   constraints), no turn of a class it refuses, each turn classed again from the edges'
   bearings with the configured thresholds rather than the engine's table.
8. **`metrics`**: the reported length and time are finite and equal the ones recomputed with
   `math.fsum` within a relative 1e-6 (the comparison is written so that NaN fails), and
   reported `items` (in the metrics' counters: a collecting route's, and a saved route's, which
   `read_route` carries from its file) equal `count_items` on the route, so a route.json whose
   counts were edited fails.

## Route files

**`route.json`** (`route_document`; `worst-route plan --out`) holds:

- `graph_version`, `region`, `build_id`, `seed`, `engine_version`;
- `request`, the `RouteRequest` as JSON (its `constraints` only when it has some);
- `stops`: `[{lat, lon, node, moved_m, note}]`, the snapped node's position;
- `nodes`, `edges`, `stop_idx`;
- `metrics`: `{length_m, time_s, delay_s, baseline_length_m, baseline_time_s,
  baseline_delay_s, ratio, turns: {left, right, straight, u_turn}, items: {toll_booth,
  level_crossing, roundabout, bridge}}` (`ratio` is null for a round trip; `delay_s` and
  `baseline_delay_s` the waiting at signals and stop signs, which `time_s` and
  `baseline_time_s`, the driving, leave out; `turns` by **Turns**; `items` the route's
  collectibles by `count_items`, whatever the mode), plus the objective's counters (a
  collecting route's `item`), and for a ratio or duration target
  `target: {kind, value, achieved, within_tolerance}` (`Goal.describe`: the target, the route's
  ratio or its experienced time in seconds, and whether that is within ±2 %);
- `run`: `{attempts, accepted, stopped_because, elapsed_s, backend}` (`backend` is `"csr"` or
  `"python"`; every backend gives the same route).

`worst-route plan --out` writes it with the presentation's fields
(`docs/reference/presentation.md`, **The document**): `directions` (the turn-by-turn steps,
jokes included) and `toasts` beside the keys above, and in `metrics` `extra_fuel_gal` and
`extra_cost_usd` (null: the CLI has no vehicle), `states`, `towns` and `towns_n` (null without a
places index under `WORST_ROUTE_DATA`) and `network_fraction`. `route_document` itself, which
the API's worker stores, leaves them out; nothing reads them back.

`read_route(path)` returns a `SavedRoute`, which the validator reads like a `Route` (the keys it
does not read, the presentation's among them, are kept in its `document`). A file that is not
one raises `KeyError`, `TypeError`, `ValueError`, `OverflowError` (an id beyond
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
                 [--tier auto|T0..T4] [--target max|ratio:X|duration:TIME]
                 [--objective distance|time|collect] [--item ITEM] [--no-left | --no-right]
                 [--strict]
                 [--out route.json] [--geojson route.geojson] [--backend auto|csr|python]
worst-route plan --region R --base route.json [--build-id ID] [--seed N] [--budget SECONDS]
                 [--max-iterations N] [--target ...] [--out ...] [--geojson ...] [--backend ...]
worst-route validate route.json [--region R] [--build-id ID] [--backend auto|csr|python]
worst-route directions route.json [--region R] [--build-id ID] [--jokes/--no-jokes]
```

**`directions`** prints a saved route's turn-by-turn directions, one numbered line a step
(`docs/reference/presentation.md`, **Directions**).

`--backend` picks the graph backend (`docs/reference/graph.md`, **Choosing a backend**). The
default is `auto`, which is `csr` when numba imports. The two give the same routes, so the
choice changes the speed and nothing else. An unknown name is exit 2. Asking for `csr` when
numba does not import prints one line on stderr ("the csr backend needs numba, which did not
import: …") and exits 1.

**`plan`** reads `<WORST_ROUTE_DATA>/graphs/<region>/<build_id>/`, by default the build named in
`graphs/<region>/latest`. A missing `latest` or `meta.json` is one line on stderr ("... run
`worst-route pipeline save --region R` first") and exit 1, and so is a build without an array
of the format, naming its remedy (`docs/reference/graph.md`, **The build format**): one another
pipeline version wrote is rebuilt ("... was written by pipeline 1 (now 2): rebuild the region
with `worst-route pipeline filter --region R` and the steps after it"), one of this version is
saved again ("... lacks the array node_roundabouts: rebuild it with `worst-route pipeline save
--force --region R`"); `validate` says the same. Without `--seed` it draws a random seed, which it
prints and records. While it searches it writes `iteration N: ratio Rx, L km`
to stderr on an accepted splice, at most one line every 2 s. On stdout it prints:

- the build;
- the tier, leash, seed and budget;
- the objective and the constraints, when not the default: `objective: time, few left turns`
  (`no left turns` with `--strict`);
- each stop with its node, position, how far it moved, and the note;
- the baseline, the base route with `--base` (`base: FILE: 481.59 km, 14 h 42 min, 62.43x`)
  and the worst route (length and time);
- the ratio, and the time ratio with its breakdown: `time ratio: 52.18x (drive 14 h 42 min +
  delays 4 h 11 min = 18 h 53 min)`. Time, wherever a ratio or an arrival is shown, is the
  experienced time, the driving and the delays at signals and stop signs, against the
  baseline's, as the API's `time_ratio` and `arrival`; the driving time and the delays are
  reported apart (`worst:`, `turns:`);
- the turns and the delays: `turns: 80 left, 144 right, 1067 straight, 0 u-turns; delays 2 h
  0 min`;
- for a ratio or duration target, the target and what was achieved:
  `target: ratio 20x → achieved 19.60x (within ±2 %)`,
  `target: duration 9 h 0 min → achieved 8 h 50 min (within ±2 %)` (or `outside ±2 %`);
- for a collecting route, its item's count and every item's:
  `collected: 30 bridge (toll_booth 0, level_crossing 44, roundabout 41, bridge 30)`;
- the node and edge counts;
- the attempts, accepted splices, elapsed time and why the search stopped;
- the backend it searched with (`backend: csr`);
- the validator's checks.

It writes the files it is asked for. A stop that cannot be snapped or reached is one line,
`cannot plan: ` and the error (for an unreachable stop, the leg and the stops it moved), and
exit 1; under `--strict`, "cannot plan: no route from stop 0 to stop 1 without repeating a
road, after moving stop 1 up to 10 times, under the strict no-left constraint; try without
--strict". A route that fails the validator is reported as an engine bug, exit 1, and never
printed. A bad argument is exit 2: `--no-left` with `--no-right`, an objective other than
`distance` or `time`, and `--strict` without `--no-left` or `--no-right` (one line, "--strict
needs --no-left or --no-right").

**`--objective`** is `distance` (the default) or `time` (**Objectives**); **`--no-left`** and
**`--no-right`** make each such turn cost a kilometre of route, **`--strict`** allows none
(**Constraints**).

**`--target`** is `max` (the default), `ratio:<x>` (× the baseline, more than 1) or
`duration:<time>`, where a time is `9h`, `90m`, `2h30m`, `45s` or a number of seconds (at most
7 days). A target it cannot read or one out of those bounds is exit 2, and so is a ratio target
for a round trip, which prints `invalid --target: a round trip has no ratio target`.

**`--objective`** is `distance` (the default) or `collect`, which needs **`--item`**:
`toll_booth`, `level_crossing`, `roundabout` or `bridge` (**Objectives**). An objective it does
not know is exit 2 with one plain line, checked before the item, as the API checks it:
`invalid --objective: unknown objective 'bogus'; known: distance, collect`. So is a misfit
(`objective_problem`): `invalid --item: the collect objective needs an item: toll_booth,
level_crossing, roundabout, bridge`, `invalid --item: only the collect objective takes an
item`, `invalid --target: the collect objective takes no ratio target: it counts items, not
metres`. An item it does not know is exit 2 too.

**`--base route.json`** continues a saved route, as the API's `base_route_id` does ("make it
worse", toward the new target, with the new seed and budget). The stops, tier, leash,
objective, item and constraints come from the file; `--from`, `--to`, `--via`, `--tier` or
`--leash-km` beside it is exit 2 ("--base takes the stops, tier and leash from its route: drop
…"), `--objective`, `--item`, `--no-left`, `--no-right` or `--strict` too ("--base takes the
objective, item and constraints from its route: drop …"), and so is a `--base` path that does
not exist. The build is the file's (`--build-id` names another); a file it cannot read, one made by
another engine version, on another region or on another `graph_version`, is one line and exit
1, and so is a base `check_base` refuses (`cannot plan: the base route's stops are not the ones
the request resolves to`) or whose route is not legal. The route.json it writes records the new
request; `validate` checks it like any other, and replaying it needs the base file.

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

**Targets on Portland** (the same build, engine version 2, `CsrBackend`, 2026-10-30; Powell's →
Reed, `--max-iterations 1000000`; every route validates, `worst-route validate` too):

| Request | Result |
|---|---|
| `--target ratio:20`, seeds 1–3 | 19.60×, 20.17×, 19.61× (151–156 km), `target` after 1,017–1,201 attempts, 0.3–0.4 s |
| `--target duration:3h`, seeds 1–3 | 2 h 58 min, 2 h 58 min, 3 h 1 min of experienced time (64.94–66.08 km, 8.42–8.57×; 1 h 5 min–1 h 13 min of it delays), `target` after 43–137 attempts, 0.1–0.2 s (driving time alone, before M3: 3 h 3 min, 2 h 57 min, 2 h 57 min, 102–106 km) |
| `--target duration:9h`, seed 1 | 8 h 51 min (6 h 19 min of driving, 2 h 31 min of delays; 212.49 km, 27.55×), `target` after 2,440 attempts, 1.1 s (driving alone: 8 h 50 min, 289.75 km) |
| max, seed 1 (`--budget 120`) | 62.43×, the same route.json as before targets but the request's `target` key |
| `--base` of that route, `--target ratio:30`, seed 1 | 30.58× (235.89 km), `target` after 30,158 attempts and 728 shrinking splices, 5.0 s |
| `--base` of that route, `--target duration:7h`, seed 1 | 7 h 8 min of experienced time (180.74 km), `target` after 44,486 attempts and 876 shrinking splices, 14.6 s on a busy machine (driving alone: 7 h 8 min, 241.38 km, 30,247 attempts) |

A target run gives the same bytes on both backends and twice. Before the shrinking windows and
ranking (**Targets**), the 62.43× → 30× shrink converged at 34.59× after 228,329 attempts
(24.4 s); with the longer windows alone it reached 30.59× after 36,628, with the ranking alone
it converged at 31.67×.

**Collecting on Portland** (`CsrBackend`, engine version 2, 2026-10-30; Powell's → Reed,
`--budget 120 --max-iterations 1000000`, every run converged and validates, `worst-route
validate` too; load average 5–16). Measured on the build with `node_roundabouts` and the
bearings of pipeline version 1; on build 506f747d7458 at `graph_version` ae1ce1e8289b (pipeline
version 2: `node_roundabouts` and the bearings without cleanup's stubs), distance seed 1 and the
collect runs of bridge seed 1, level crossings seed 2, roundabouts seed 3 and toll booths seed 1
were run again and give the same routes, attempts and counts (no search reads a bearing without
a turn constraint); the duration row is of that build, a duration being the experienced time. The
request's area (leash 3.6 km, T4) holds 45 roundabout rings, 144 bridge roads and 212 level
crossings on 114 edges (both directions of a road counted), and no toll booth.

| Request, seeds 1–3 | Passes | Length |
|---|---|---|
| distance (max) | 17, 16, 16 bridges; 33, 52, 31 level crossings; 39, 38, 40 rings | 62.43×, 62.20×, 57.94× |
| `--objective collect --item bridge` | 30, 24, 26 bridges | 65.39×, 60.54×, 56.69×, 15–26 s |
| `--item level_crossing` | 46, 45, 53 level crossings | 62.18×, 59.07×, 60.52× |
| `--item roundabout` | 44, 44, 44 rings, of the area's 45 | 60.66×, 57.95×, 56.35× |
| `--item toll_booth`, seed 1 | none | 62.39× (as distance would: no item in the area) |
| `--item bridge --target duration:3h`, seed 1 | 13 bridges | 2 h 56 min experienced (1 h 36 min of driving, 1 h 20 min of delays; within ±2 %), 57.39 km, after 194 attempts, 0.1 s |

The bridge run of seed 1 with `--budget 30 --max-iterations 1000000` converges within the 30 s
(22.5 s, 101,490 attempts) on the same 30 bridges. Portland's two toll booths are on Barton Park
Road and Meldrum Bar Park Road, roads into parks that only those roads reach (cut edges of the
road graph), so a route that never repeats a node passes one only with a stop beyond it. With
`--leash-km 30` the area holds them and they stay out of reach: over 30,000 attempts (seed 1)
the toll-booth run reaches 129.96× against distance's 176.93×, its waypoints drawn at the four
unreachable places whenever the ball holds one (49.8× before a ball without any drew from all
its nodes). Over the same 4,000 attempts (seed 1; `test_portland_data.py`), the distance route
passes 10 bridges, 12 level crossings and 26 rings, and collecting each passes 24, 30 and 44.

**On Portland with `CsrBackend`** (the same build and engine version 2, 2026-10-30, once its
transition-aware kernels landed; load average 3–5, about 11 during part of the CSR acceptance
run, so a rate compares only with the run made just before or after it):
- **Powell's → Reed, seed 1, `--budget 120 --max-iterations 1000000`.** Both backends write the
  same route.json but the backend and the time: 62.43× (481.59 km, 4,913 nodes), converged
  after 82,841 attempts and 800 accepted splices, in 11.7 s on the reference (7,090 attempts/s)
  and 10.1 s on `CsrBackend` (8,230/s).
- **The acceptance run on both backends**, back to back (`--backend python`, then the default):
  the same 20 routes pair for pair (lengths, attempts and accepted splices; every run converged;
  no turn refused), so the summary above (median 21.5×, quartiles 9.0× and 27.0×, 3.3–45.2×);
  a median of 7,180 attempts/s on `CsrBackend` against 6,830 on the reference, 157 s of runs
  against 169 s. Four of the five seed-2 re-plans match; pair 01's ran out of its 30 s on
  `CsrBackend` while the machine was busiest and stopped at 36.54×, after about 133,300 attempts:
  the prefix of the 37.05× route both backends reach when it converges (142,254 attempts, with
  the same bytes).
- **Where the time goes** (Powell's → Reed, each search call timed). The searches take 33% of
  the reference's run and 24% of `CsrBackend`'s. `dijkstra_edges` is called 77,573 times, 64 µs
  a call on the reference and 37 µs on `CsrBackend`, about 40% of which is the weight check
  scanning each new random-weight array (`docs/reference/graph.md`, **The CSR backend**); the
  turn DFS 8,110 times, 71 µs against 20 µs. Most searches are small, so a kernel 8–10× faster on
  large searches makes a whole run about 1.2× faster; the rest of a run is the loop's own work.

**The slowest time and the turn constraints on Portland** (build 506f747d7458, `graph_version`
f7c6ebdd303d: the same extract re-enriched at pipeline version 2, so its turn classes are the
roads', not the stubs'; engine version 2, 2026-10-30, `CsrBackend`, Powell's → Reed with
`worst-route plan --seed 1 --budget 120 --max-iterations 1000000`; every route validates;
other lanes ran beside it, so no time here compares with those above). The baseline is 7.71 km,
13 min 4 s of driving and 8 min 40 s of delays, 21 min 44 s experienced; the plain seed turns
left 10 times, 1.30 a km, and right 11. On the same build saved with `node_roundabouts`
(`graph_version` ae1ce1e8289b), the four `--objective time` rows below, run again, give the same
routes and numbers.

| Request | Result |
|---|---|
| `--objective time` | 479.83 km, 14 h 42 min of driving and 4 h 11 min of delays: 52.18× the experienced time (67.55× the driving time, 62.20× the length); converged after 66,578 attempts (the distance route: the same bytes as on build e4b3b4ad90db, 62.43×, 52.29× the experienced time) |
| `--objective time --target duration:9h` | 8 h 55 min (6 h 16 min of driving, 2 h 39 min of delays; 205.63 km, 24.64×), `target` after 1,575 attempts |
| `--objective time --target ratio:20` | 20.14× the experienced time (165.80 km), `target` after 668 attempts |
| `--no-left` | 129.40 km, 16.77×, with 80 left turns of 1,291 (0.62 a km, the seed's 1.30) and no U-turn; converged after 48,152 attempts |
| `--no-right` | 49.05 km, 6.36×, with 31 right turns of 511 (0.63 a km, the seed's 1.43) and no U-turn |
| `--base` of the `--no-left` route, `--target ratio:3` (or `ratio:8`), seed 1 | converged at 11.04× (85.18 km, outside the band) after 36,754 attempts and 132 shrinking splices, its left turns 80 → 45: below that every shorter way round needs a left turn the route's rights stood for, which shrinking may not add (shrinking on the score alone, as it did before M3's review, such a run gained left turns) |
| `--objective time --no-left` | 228.78 km, 28.48× the experienced time (29.66× the length), 250 left turns of 2,359 (1.09 a km) and 2 U-turns, converged after 46,597 attempts: a left turn costs 90 s here, cheaper against the time a detour gains (its delays included) than a kilometre is against its length, so more stay than under the distance (**the penalty** below) |
| `--no-left --strict` (or `--no-right --strict`) | one line, exit 1: "cannot plan: no route from stop 0 to stop 1 without repeating a road, after moving stop 1 up to 10 times, under the strict no-left constraint; try without --strict" |

On the 20 acceptance pairs (pair seed 2026, seed 1, 30 s each, the same build): soft no-left
plans all 20 at a median of 5.33× (2.54–11.04×, 0.54 left turns and U-turns a km), soft
no-right all 20 at 6.47× (2.36–13.89×); strict no-left plans 4 (2.40×, 1.42×, 1.04×, 1.00×; 16
fail at the seed), strict no-right 3 (1.59×, 1.49×, 1.21×); every route validates. No strict
route turns the refused way by the roads' own geometry: the review's probe, which takes one
stub off each merged end, flags one turn (strict no-right, pair 12, at an interchange), where
the OSM nodes' positions show two stubs and a straight road (180° in, 206° out). On build
e4b3b4ad90db, whose bearings included the stubs, the soft medians were 6.88× and 5.65× and
strict planned 3 and 1, one of them turning left by the road at a stage A cluster.

**The penalty.** How much the soft modes trade length for turns is the penalty; with the
distance (`TURN_PENALTY_M`), on Powell's → Reed under no-left:

| `TURN_PENALTY_M` | Result |
|---|---|
| 10⁶ (fewest left turns first) | 2.58×, 8 left turns of 208; acceptance pairs: medians 1.12× (no-left), 1.10× (no-right) (build e4b3b4ad90db) |
| 1,000 (a left turn worth a kilometre; the value) | 16.77×, 80 of 1,291; acceptance pairs: medians 5.33×, 6.47× (on build e4b3b4ad90db: 11.63×, 57 of 918; 6.88×, 5.65×) |
| 300 | 52.91×, 676 of 4,138 (build e4b3b4ad90db) |

Under the slowest time (`TURN_PENALTY_S`), Powell's → Reed under no-left, tuned to keep left
turns about as rare as under the distance (under 0.7 a km) at well above 10×; no value tried
does both, so it stays 90 s:

| `TURN_PENALTY_S` | Result |
|---|---|
| 90 (the value) | 28.48× the experienced time (228.78 km), 250 left turns, 1.09 a km |
| 180 | 11.66× (70.44 km), 60 left turns, 0.85 a km |
| 300 | 4.67× (29.43 km), 16 left turns, 0.54 a km |

## Limitations

- **Transitions.** The searches see the transition tables (**Searches**), but a leg or a part
  of a detour whose cheapest legal path passes a node twice is no path: the search does not
  look for the cheapest node-simple legal path, which a turn table can make longer than the
  cheapest walk. The seed then falls back as for an unreachable leg.
- **Twins within a leg.** A leg driving an edge and its twin is searched again without the
  later one, up to 8 times; a leg that needs more rounds, or whose only way drives a twin
  pair, falls back as an unreachable one.
- **Speed.** `CsrBackend`'s searches are 8–13× the reference's on large searches, but most of a
  run's searches are small: a whole run is about 1.2× faster on Portland (**On Portland with
  `CsrBackend`**) and 1.5–1.9× on a build without tables (**The loop**, Measured).
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
- **Targets.** A shrinking run only replaces windows (at most 24 edges) by shorter paths, so a
  base far past a small target can converge above the band, returning the closest route
  (`"converged"`); a target beyond what the area holds converges below it.
- **Turn constraints.** A strict one is rarely satisfiable on a street grid (4 and 3 of 20
  Portland pairs plan), and a soft one makes routes less bad than no constraint (medians 5.33×
  and 6.47× against 21.5×; **Constraints**). Under the slowest time a left turn costs 90 s,
  and the time a detour gains counts its delays, so more left turns stay than under the
  distance (1.09 a km on Powell's → Reed, against 0.62). A soft route shrinks toward a smaller
  target only as far as it can without adding a net avoided turn, the price of the rule that
  shrinking never adds one: Powell's → Reed's 16.77× soft no-left route, sent toward 3×,
  converges at 11.04× (`stopped_because: "converged"`, outside the band, the closest route
  returned). Letting a shrinking splice add an avoided turn when the route's density of them
  does not rise is a later option. The turn classes are coarse at super-junctions (**Turns**).
- **Collecting.** The waypoints aim at the item's places while the ball holds some, so where an
  item is common a long distance route can pass about as many by chance (level crossings,
  seed 2: 45 collecting against distance's 52), and places that cannot be reached still draw
  waypoints (the toll booths under a 30 km leash: 129.96× against 176.93×). An item on a
  dead-end road, or behind a cut edge of the road graph, is passed only by a route with a stop
  beyond it. `candidates_in_area` counts a bridge once per road, so a bridge split by junctions
  counts several times there, once on a route.

## Tests

`tests/engine/` builds its graphs with `worst_route.graph.testing` and the helpers in
`tests/engine/graphs.py` (`lattice`, with twins, transitions and lon/lat rounded for stable
bytes).

| Test file | What it covers |
|---|---|
| `test_route.py` | every refusal, round trips, and the release and the ban counts against a brute force on random twins |
| `test_validator.py` | each violation caught by the right check, including a cluster entered twice through its members, a shared shape point, non-finite metrics and reported item counts that differ from the recount; a strict turn constraint proven from the bearings (a left turn failing strict no-left, a right turn strict no-right, nothing under a soft one) |
| `test_request.py` | the request model and its bounds (its target's too, and no ratio target for a round trip; the item only and always with `collect`, which takes no ratio), tier and leash auto, the masks and the tier's edge bans; the objective's keys, the constraints left out of the JSON when there are none, both at once refused |
| `test_target.py` | the target's bounds; the goal's rules on values (the band's edges, phases, the closer route better and a tie to the later, growing and shrinking acceptance at the band, a duration moving one way, a candidate's value equal to the re-scored route's, a ratio and a duration under the slowest time and a duration under the distance on the experienced time, delays included); and on lattices with `check_delta`: ratio and duration targets reached while only growing, a base past a target shrinking into it, a route already within it stopping before an attempt, an unreachable target converging with the closest route, the overshoot after an empty cycle (the closest route returned, and a run cut at its attempt the prefix ending with it), the same bytes on both backends and twice; the slowest time reaching ratio and duration targets on its experienced time |
| `test_snap.py` | roles, cul-de-sacs, stops kept apart, round trips only for identical points, an end near the start moved on, re-snapping |
| `test_search.py` | the turn edges counting only on a build with rows, a path driving both carriageways searched again, and the rounds' limit |
| `test_seed_route.py` | `resolve_stops` deterministic, node-disjoint legs, legs avoiding later stops, the penalty re-solve on a corridor, the baseline, the re-snap walk out of a one-entrance ring, the start never moving, a point-to-point trip never collapsing onto the start, a leg between two stops on one node, unreachable legs named by both ends, a leg going round a junction that allows only straight on, the turn at a via, the baseline's legal legs and its turn-blind fallback, a leg driving both carriageways going round, a round trip to a neighbour not one road out and back, round-trip loops (never back along the way out) |
| `test_objectives.py` | `Distance`, the radius, the registry, and `delta` against a full re-score for every kind of term, the first-edge term included; the delays in every mode's metrics; `SlowestTime`'s terms, weights, waypoint pool, `describe` and primary quantity (the experienced time), and its `delta` against a full re-score |
| `test_collect.py` | `count_items` and each item's score, items first then length (a bridge split by a junction once, a bridge at the start, a bridge run through a round trip's start once, a roundabout on a round trip's start once, a toll booth in either direction), `delta` against a full re-score for each item with windows at the route's start and at its end (a round trip's start inside a bridge run among them), the waypoint pool (the uncollected places, once per route, any node once none is left in the area), the radius, `describe`; for each item a loop run with `check_delta` that never gives an item back and validates; a request planning the same collecting route on both backends, and one reaching a duration target |
| `test_turns.py` | the classes by the change of bearing (the bands' ends, across north), the table against the rule written out, a lattice route's counts, a U-turn and the stored bearings deciding |
| `test_constraints.py` | the request's constraints (`strict` alone refused); strict ones refusing turns and windows (at their boundaries), soft ones scoring minus the penalty a turn, a kilometre's worth in the objective's units (`turn_penalty_for`); the composite summing scores and combining hooks, its `constraint_delta` the penalties, its `delta` against a full re-score; the searches avoiding a class when they can and taking it when they must, refusing it when banned; the strict seed (vias, an unreachable leg as `InfeasibleTurnConstraint` with its note, one no route covers without the constraint either as the plain `StopUnreachable`, a round-trip loop), the strict loop never turning the refused way, the soft loop adding an avoided turn only for more than a kilometre of route (and far fewer than the plain loop), both on both backends, one-way towns with tables; a soft no-left base shrunk toward smaller ratios never adding a left turn or U-turn (each splice replayed) |
| `test_detour.py` | each strategy's legal detours and None when none exists, the waypoint's pool (and any node of the ball when it holds none of the pool), the strategy weights, random weight drawing only near the window, the local-exhaustive ranking and the refused-candidate skip, no turn refused on a lattice of straight-only junctions (and many when the searches are made turn-blind); shrinking toward a target, the local-exhaustive path closest to the band first, and never a longer one (a longer, faster path shadowed a shorter one for a duration) |
| `test_worsen.py` | over fixed seeds, `check_delta` on: the score only rises, every event replays to the best route, budgets in iterations and seconds, convergence, a splice after two empty cycles and the run stopping only after three, the segment bias's rate and its anchors (the edges with the most unused nodes near), the unused-node grid's cap, the same seed gives the same bytes, different seeds give edge sets with Jaccard < 0.7, random one-way towns with twins always validate, and so do those with turn tables (no turn refused), a tier's shortcut ban, a point-to-point request never becoming a round trip, identical points planning a loop, an unexpected seed error reported as `StopUnreachable`; the same-seed test on both backends, and both backends planning the same route, splice for splice, on the grid and on one-way towns with and without turn tables; shrinking toward a target, runs of 6 edges on average and up to 24 |
| `test_io.py` | the route files, the target in the request and, for a target run, in the metrics; the delays and the turns in the metrics, and a saved route's metrics carrying its file's item counts |
| `test_plan.py` | `plan_route`'s hooks: `on_start` once before the loop with the route it starts from (every splice replays from it to the result), the same route without the hooks, a continuation from `base` that never scores less, is fixed by its seed on both backends, and refuses a base for other stops, one leaving the request's area (before the loop) and, in `check_base`, a node outside the mask or an edge the request bans; a soft constraint starting from the plain seed and keeping left turns rarer than the plain run's, a strict one no route keeps as `InfeasibleTurnConstraint` with its note, one no route keeps without it either (two one-way rings) refused as without it, a stop the strict fallback moves noting why, a base refused for a turn a strict constraint refuses (taken by a soft one); a strict route validated against its constraint |
| `test_cli_plan.py` | `plan` and `validate` on builds written with `write_build`, including the error paths, a route that fails validation never printed, a truncated and re-edited file caught by its re-derived stops, an id beyond int32, `--backend` on both commands (the same route.json on both backends but the backend and the time), and the same on a build with turn tables, where `auto` is `csr` too, a file from another engine version refused; `--target` (its line, its parsing, its refusals) and `--base` (a continuation to a smaller target, the options it refuses, a file from another engine version, region or `graph_version`, a truncated or broken route, stops that do not resolve); a ratio target for a round trip, from the stops or the base, refused; an illegal route reported as the base file's fault only with `--base`; `--objective`, `--no-left`, `--no-right` and `--strict` (their lines, the time ratio with its drive and delays, the route.json, `validate`, their refusals, `--strict` alone in one line, a strict refusal in one line ending "try without --strict", `--base` taking them from its file); `validate` failing a soft route's file edited to claim a strict constraint; `--objective collect --item` (the collected line, the items and item in route.json, `--base` keeping them, a distance run's items) and its refusals, each one plain line (an unknown objective before its item); a build missing an array, one line for `plan` and `validate`; `validate` refusing a route.json whose item counts were edited |
| `test_portland_data.py` | (`data`: Portland's latest build through `WORST_ROUTE_DATA`, skipped without one) Powell's → Reed, seed 1, 4,000 attempts: the same bytes on both backends, splice for splice, no turn refused; the slowest time reaching 9 h within ±2 %; soft no-left with fewer left turns a kilometre than its seed, at more than 5×; strict no-left refusing in one line; collecting each item over 4,000 attempts, more of it than the distance route passes (none of the toll booths, which lie outside the area), and a duration target under `collect` reached within ±2 % |

**The acceptance run**, `tools/acceptance.py`, plans random stop pairs on a saved build, so it
needs `WORST_ROUTE_DATA` and is not part of the gate:

```
uv run python tools/acceptance.py --region portland [--build-id ID] [--pairs 20] [--seed 1]
    [--budget 30] [--backend auto|csr|python] [--min-km 3] [--max-km 15] [--jaccard 5]
    [--pair-seed 2026] [--near-m 250] [--out tools/out/acceptance.json]
```

Its `--backend` is `make_backend`'s, `auto` by default, which is `csr` when numba imports (M1's
numbers were measured on the reference); the report records which backend ran. A run bounded by
time goes further on a faster backend, so the backends give different routes only where a run
stops on time rather than converging.

The pairs are uniform points in the region's bbox with a node within `--near-m`, the end at a
straight-line distance within the bounds, drawn from `PCG64(--pair-seed)`; each is planned with
`plan_route` and no attempt limit, so the budget bounds every run. The first `--jaccard` pairs
that plan are planned again with the next seed and compared (Jaccard of edges and of road
units). A pair that cannot be planned, or whose stop the re-snap fallback moved, is resolved
again on the build without its transition rows, to tell whether the tables stopped it. It prints
a table row per pair and a summary (planned, validator passes, median ratio and range, attempts
per second, the failures the transitions caused, the Jaccards), writes the JSON report, and
exits 1 only when a planned route fails the validator. On Portland it takes under two minutes.

`tests/golden/` freezes six routes (seed 7, three stops) on a 20 × 20 lattice whose lon/lat
are rounded to 1e-7°, so their bytes and pinned `graph_version` do not depend on PROJ's last
bits: a max run of 400 attempts (`grid20_distance.json`); runs to a 6× ratio and a 2,400 s
duration target, each stopping within it (`grid20_ratio.json`, `grid20_duration.json`); the
slowest time, 400 attempts, on the lattice with a primary road every fourth row and column, 30 s
signals where two cross and 10 s stop signs along them (`grid20_time.json`); soft no-left,
400 attempts, between inner nodes (`grid20_noleft.json`); and 400 attempts collecting bridges
on the lattice with thirteen roads made bridges (`grid20_collect_bridge.json`, 7 of them). Both
backends must reproduce them. They change only under a tier A brief (the collect brief added
the sixth, and `node_roundabouts` and the request's `item` changed the others' `graph_version`
and request, not their routes);
`WORST_ROUTE_REGEN_GOLDEN=1 uv run pytest tests/golden` rewrites them.
