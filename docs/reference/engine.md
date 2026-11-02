# Engine

How a route is planned, made worse and proven legal: the route model and its one mutation, the
request and its masks, snapping, the seed route and the baseline, the objective interface, the
distance, slowest-time, collect and brand modes, turns, the turn constraints and hard mode, the
detour strategies, the worsen loop and its targets, the alphabet route's search, the certified
worst's driver, the validator, route files and the `plan` / `validate` commands.
Code: `src/worst_route/engine/`. The design it implements is `docs/PLAN.md` §3.1–3.9, §3.11
and §8.1; the conventions are `CLAUDE.md`.
Everything runs on a build's `GraphData` through `GraphBackend` and `SpatialIndex`
(`docs/reference/graph.md`): `CsrBackend` by default, `PythonBackend` when asked, with the same
routes either way, on every build, transition tables included (**Searches**).

| Module | What it holds |
|---|---|
| `route.py` | `Route`, `Splice`, `Metrics`, the `IllegalSplice` refusals, `check_route`, `splice_arrays`, `twins_of` |
| `validator.py` | `validate_route`, `ValidationReport`, `Check`, `ValidationFailed`, `RouteLike` |
| `request.py` | `RouteRequest`, `ObjectiveKey`, `RequestMasks`, `request_masks`, `auto_tier`, `auto_leash_m`, `objective_problem`, `max_reorder_stops`, `reorder_problem` |
| `target.py` | `Target` (a request's target), `Goal` (what a run is after: better, done, its phases) |
| `snap.py` | `snap_stops`, `resnap`, `apart_from`, `role_of`, `is_round_trip`, `StopUnreachable`, `InfeasibleTurnConstraint`, `InfeasibleHardMode` |
| `search.py` | `shortest_path`, `leg_path` (a seed leg's), `simple_paths`: which backend search builds a path |
| `seed_route.py` | `resolve_stops`, `choose_order` (the worst order), `baseline_route` / `Baseline`, `seed_route` / `SeedRoute` |
| `objectives/` | `base.py` (`Objective`, `BaseObjective`, `route_delay_s`), `distance.py` (`Distance`), `time.py` (`SlowestTime`), `collect.py` (`Collect`, `count_items`, `ITEMS`, `CollectItem`), `brand.py` (`Brand`, `brand_counts`, `brand_problem`, `BrandRefused`), `composite.py` (`Composite`), `OBJECTIVES`, `OBJECTIVE_KEYS`, `make_objective` |
| `turns.py` | `turn_classes`, `count_turns` |
| `constraints.py` | `Constraints`, `TurnConstraint`, `NoLeft`, `NoRight`, `HardMode`, `stretches`, `returning`, `constrain`, `turn_penalty_for`, `NO_CONSTRAINTS` |
| `detour.py` | `find_detour`, `pick_strategy`, `waypoint_path`, `random_weight_path`, `local_exhaustive_paths` |
| `worsen.py` | `worsen`, `run_worsen`, `pick_segment`, `detour_ends`, `widened`, `UnusedNodes`, `temperature`, `Budget`, `ProgressEvent`, `WorsenResult` |
| `alphabet_search.py` | `alphabet_route`, `edge_letters`, `alphabet_counts`, `alphabet_scored` (**Alphabet**) |
| `exact.py` | `certified_route`, `certified_area`, `certified_request`, `Area`, `AreaTooLarge`, `too_large`, `Certificate`, `Incumbent` (**Certified worst**) |
| `plan.py` | `plan_route`, `PlanResult`, `check_base`, `BaseRouteMismatch`, `anneal_temperature` |
| `io.py` | `route_document`, `document`, `read_route` / `SavedRoute` |
| `draw.py` | `DrawRules`, `Drawing`, `check_drawing`, `next_moves`, `finish_drawing`, `DrawRefused`, `DrawingUnfinished`, `drawing_problem` (**Drawing**) |

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

### Drawing

A route drawn by hand (`draw.py`; the API's `POST /draw`, `docs/reference/api.md`, **Drawing a
route**) is a path of edges from the request's start, extended a move at a time and checked
whole, from the path alone, on every call. `DrawRules` is what it keeps to: the resolved stop
nodes (`resolve_stops`), the request's `active` nodes and `banned` edges, and the turn classes
its strict constraint refuses and its hard mode (a soft constraint changes nothing here: it only
scores). `check_drawing(data, rules, path)` returns a `Drawing` (its nodes and edges, the
positions of the stops reached in order, the start's 0 first, and whether it has reached the
end), or raises `DrawRefused(code, index)`, `index` the first edge of the path that is not a
legal move, so that `path[:index]` is legal:

| Code | When |
|---|---|
| `BROKEN_CHAIN` | an id outside the graph, an edge that does not leave the node the path is at (a one-way's only edge taken from its head), a self-loop |
| `OUTSIDE_AREA` | an edge into a node outside `active` |
| `NODE_REUSED`, `ROUND_TRIP_START_REUSED` | a node the path has passed; a round trip's start only as its end, once every stop between was reached |
| `EDGE_BANNED` | an edge `banned` holds, the twin of one the path drives, a two-edge round trip out and back along one road |
| `TRANSITION_FORBIDDEN` | a turn the node's transition rows do not list |
| `STOP_SKIPPED` | a stop reached before the one before it, the end included |
| `PAST_END` | an edge after the end |
| `TURN_REFUSED` | a turn of a class the strict turn constraint refuses |
| `ROAD_AGAIN` | under hard mode, a road the path left and comes back to |

The route rules are `check_route`'s, the one place they are written: the path is checked as a
whole route whose stops are those reached so far and, when its last node is not one of them,
that node, a stand-in for the stop it is heading to. `check_route` reads its stop positions for
their shape only (the first 0, the last the last node, rising), so the stand-in masks no rule;
the stops' order, the end and the request's own rules (the area, strict turns, hard mode, as
`plan.check_base` reads them) are checked beside it. Each check names the first position it
fails at, and `check_route` the first of the first rule it finds broken (a node passed is looked
for before a turn): legality is closed under prefixes, so the earliest refusal found is checked
again on the path before it until that path passes, which makes it the first illegal edge, and
the path up to and with that edge is checked once more so that the code is the same whichever
longer path showed it (on a tie, the stops' walk first, then `check_route`, the area, the turns,
hard mode). A node passed again is the edge into it, but a round trip's start, which comes back
as the end: the stops' walk judges it there, and a route that goes on from it goes wrong on the
edge leaving it (`PAST_END`).

`next_moves(data, rules, drawing)` is the out-edges of the drawing's last node that extend it
legally, ascending, filtered without a route check per candidate: not in `banned`, the head in
`active` and not the node itself, the head not on the path (but a round trip's start once every
stop is reached, and not by the road the path's one edge came along), the head no stop but the
one due next, not one of the path's edges or their twins, the turn from the last edge listed
when the node has transition rows, of no refused class, and under hard mode no road the path has
left. They are exactly the edges e for which `check_drawing` passes `path + [e]`, which
`tests/engine/test_draw.py` holds on random walks. A dead end has none: the player undoes.

`finish_drawing(data, request, rules, snaps, baseline, path, r_max=...)` makes a drawing that
has reached the end a route: `Route.new` on it (with the request's bans as `extra_banned`),
scored by the distance objective with the request's constraints as `plan_route` scores a seed,
then `validate_route` on the real stops, the masks and the constraints, and returns its
`route.json` document (`io.document`, **Route files**) with the run `{attempts, accepted}` its
moves, `stopped_because: "drawn"`, `elapsed_s: 0` and `backend: "human"`. A drawing short of the
end raises `DrawingUnfinished` (`reached`, the stops it has), a route the validator refuses
`ValidationFailed`. `drawing_problem(objective, target_kind, reorder)` is what a drawing's request
may not ask: a drawing is the longest route, as bad as the player makes it, so the objective
`distance` and the target `max` only, and no worst order (its stops are drawn in the order given:
there is no order search). Nothing is drawn from an rng: the same build, request and path give
the same answer.

On `us-t3` (build 6adae6917be1: 2,154,510 nodes, 4,412,843 edges), `check_route` on a path costs
2.7 ms at 2,000 edges and 8.0 ms at 10,000 (`Route.new`, which also allocates its node and edge
masks, 3.5 and 9.0 ms); the request's masks cost 22–24 ms and settling its stops and its baseline
5–123 ms (Powell's → Reed to Chicago → Denver), which the API keeps per request
(`docs/reference/api.md`, **Drawing a route**).

## Requests

`RouteRequest` (pydantic, frozen, JSON round trip): `stops` as (lat, lon) in order, at least
two (identical first and last points make a round trip); `objective` (`"distance"`, the
default, `"time"`, `"collect"`, `"brand"` or `"alphabet"`: `OBJECTIVE_KEYS`, the keys of
`OBJECTIVES` and the alphabet route, which is no `Objective` (**Alphabet**); another is "unknown
objective"); `item`, what `collect` collects (`toll_booth`, `level_crossing`, `roundabout` or
`bridge`; null for every other objective); `brand`, the id of the brand `brand` visits, in the
build's brand index (≥ 0; null for every other objective, and then left out of the request's
JSON, so a request reads as it did before brands existed); `constraints` (**Constraints**:
`{no_left, no_right, strict, hard}`, none by default, and then left out of the request's JSON,
so a request reads as it did before constraints existed, and `hard` in it only when set, so a
request without hard mode reads as it did before hard mode existed); `reorder`, whether the
stops may be visited in the worst order (**Seed and baseline**, **The worst order**; off by
default, and then left out of the JSON, so a request reads as it did before it existed);
`certified`, whether it asks for the certified worst (**Certified worst**; off by default, and then
left out of the JSON; on, `certified_problem` refuses another objective, a target, the worst
order, a turn constraint, hard mode and a round trip, field `certified`, which the CLI and the API
ask first); `tier` (`"auto"` or
`"T0"`..`"T4"`); `leash_km` (`"auto"` or in
(0, 150]); `budget_s` (in (0, 120], default 20) and `max_iterations` (default 5000); `seed`
(≥ 0); and `target` (**Targets**: `max`, the default, `ratio` or `duration`; a ratio target is
refused for a round trip, whose baseline is 0 m). Every number must be finite, so a request
(and `route.json`) never holds `Infinity` or `NaN`. `objective_problem(objective, item,
target_kind, brand, stops)` is what the request refuses, as (the field at fault, a sentence):
under `alphabet`, more than two stops ("the alphabet route takes a start and an end", field
`stops`) and any target but `max` ("the alphabet route takes no target: it counts letters");
`collect`
without an item, an item for another objective, and a ratio target under `collect`, which
counts items rather than metres; the same for `brand` and its brand ("the brand objective needs
a brand", "only the brand objective takes a brand", and no ratio target: it counts visits); and
`reorder` under `alphabet` ("the alphabet route takes no worst order: it has no stop to reorder",
field `reorder`); the CLI and the API ask it first, to refuse as early. The request itself
refuses `reorder` with more than `MAX_REORDER_STOPS` (7) stops ("the worst order takes at most 7
stops, not 8"); on a build of more than `REORDER_LARGE_NODES` (100,000) nodes the cap is
`MAX_REORDER_STOPS_LARGE` (5): `max_reorder_stops(data)` is a build's cap and
`reorder_problem(stops, data)` the sentence for a request past it (without `data`, past 7) ("the worst order takes at
most 5 stops on us-t3, not 6"), which planning raises as a `ValueError` and the CLI and the API
refuse first. The cap depends on the build and the number of stops only, never on their order,
so a request already in its worst order is never refused where it was accepted. Whether the build has the brand, and whether
the request's area holds any of its locations, is planning's to check (**Planning**).

`request_masks(spatial, data, request)` derives everything from the request alone, so a saved
route can be checked against the same masks later. The trip length is the straight-line length
of the polyline through the projected stops.

- **Tier auto**: below 800 km T4, else T1 (`TIER_AUTO_KM`, four thresholds, T4 below the first,
  T0 from the last; the first three coincide and the last is infinite, so T0, T2 and T3 are
  taken only when asked for). Engine version 3, tuned on the Oregon build (**On Oregon**), made
  it T4 below 800 km and T0 beyond; engine version 5, tuned on the national T1 build (**On the
  national T1 build**), T1 beyond, where T0's motorways and trunks gave 1.0–2.8× and T1 6.2–10.3×
  on four cross-country trips. Before version 3, below 15 km T4, below 60 km T3, below 250 km T2,
  below 800 km T1, else T0. The golden routes ask for `tier: auto` on lattices about 2 km across,
  T4 under every rule. No two nodes of the Oregon build lie 800 km apart (the farthest 727.6
  km), so only a trip with stops between its ends reaches T1 there.
- **Leash auto**: 0.6 × the trip length within [3, 150] km, or a fifth of it
  (`LEASH_LONG_FACTOR`) when that is more, from 750 km on (engine version 5: on the national T1
  build a 150 km leash, 3% of a 4,500 km trip, refused Miami → Seattle and left little room);
  `leash` is the nodes within it of the polyline; 150 km is `LEASH_AUTO_MAX_KM`, the automatic
  leash's ceiling before the fifth-of-the-trip rule. A request's own leash stays at most 5,000 km
  (`LEASH_MAX_KM`; 150 km until 2026-11-01, when no typed leash could pass the automatic one on a
  long trip), the whole national build from any trip.
- **Bubbles**: the nodes within 3 km of a stop (`BUBBLE_KM`).
- **`active`**, the node mask of every search: `leash & (tier_node_mask[t] | bubbles)`.
- **`banned`**, the request's edge bans for every search, every splice, the seed and the
  baseline: the edges outside `tier_edge_mask[t]`, unless both their ends lie in a bubble. A
  tier therefore keeps lower-class roads only near the stops, even a lower-class shortcut
  between two of the tier's own nodes.
- **`extent_m`**: the diagonal of the active nodes' bounding box. The loop's radius bound is
  `r_max = min(leash, extent)`.

The masks cost one pass over the build's nodes and edges per request, computed over the whole
build. The leash's distances are NumPy's, shapely deciding the nodes within a metre of the leash
(`docs/reference/graph.md`, **Spatial**): 1 ms on the Oregon build (237,581 nodes, 593,555
edges), where shapely over the stops' bounding box had taken 83–166 ms at load averages of 15–40
(Astoria → Ontario: 133 ms for the leash, which holds 161,341 nodes, under 3 ms for the bubbles,
`active` and `banned`), and 4–10 ms on the national T1 build (887,737 nodes) against 22–159 ms.

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
stop, the stop the API's `STOP_UNREACHABLE_WITHOUT_REPEAT` error names. Its message is one sentence
for people, the stops numbered from 1 as the app's markers are, naming both ends and the stops
that were moved: "No route from stop 2 to stop 3 without repeating a road, after moving stop 2
and stop 3 up to 10 times each."

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

The transition-aware search answers the cheapest legal walk, which may pass a node twice (a
table can make the cheapest legal way a loop round a block); such a walk is illegal for a route.
So `shortest_path` makes the search again with one more edge banned, the one by which the walk
first comes back to a node it has passed (the node stays passable, by its other ways in), the
bans adding up, up to `SIMPLE_RETRY_ROUNDS` (8) times; then there is no path, and the caller's
fallback applies as for no path at all. The bans depend only on the walks found, so this is
deterministic, and the same on both backends, which answer the same walks. It is a heuristic: a
node-simple path that drives a banned edge is missed. On every pair of nodes of two 4 × 5
lattices whose junctions each allow a random 60% of their turns (`test_search.py`), 107 cheapest
walks loop where a legal simple path exists: searching again finds one for 66 (48 longer than
the walk), and misses 41. The baseline does not search again (**Seed and baseline**).

**A seed leg's search** (`leg_path`, engine version 5) makes the same searches as a depth-first
tree over bans rather than a chain. A walk that enters a node X a second time has two children:
the walk banned the edge by which it comes back to X (the chain's step, tried first), and the
walk banned the edge by which it first entered X. A node-simple path enters X once, so it
survives in one of the two, and the tree, given searches enough, finds a node-simple legal path
wherever one exists. A walk that drives an edge and its twin has one child, the later edge
banned (**Twins**). The bans of a branch add up, re-entries and twins alike. The tree makes at
most 1 + `LEG_SEARCHES` (8) searches, and one more per `LEG_SEARCH_KM` (100 km) of the straight
line between the leg's ends, since a long leg passes many junctions whose cheapest legal walk
loops and the first branch fixes one a search: New York to Los Angeles on the national T1 build's
motorways and trunks needs 31 (**On the national T1 build**). Where the chain finds a path
without meeting a twin, the tree's first branch is the chain and gives the same path; the seeds
of Powell's → Reed and the 20 acceptance pairs on Portland, and of Oregon's five named trips and
20 random pairs, are the same bytes as engine version 4's, plain, under strict no-left and
no-right and under hard mode. On the small towns above, 9 searches find 103 of the 107 and 17
all of them. The bans depend only on the walks found: deterministic, the same on both
backends.

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
`TWIN_RESEARCH_ROUNDS` (8) times for a detour's search, up to the tree's searches for a seed
leg's; then there is no path. The bans depend only on the paths
found, so this is deterministic. The baseline is not: a GPS may drive both carriageways.

## Seed and baseline

**The baseline** (`baseline_route`) is what a sane GPS would drive: the shortest path on length
between consecutive stops, within the request's masks and nothing else, concatenated. Its legs
may share nodes. It is the denominator of the ratio. Each leg turns only where the tables allow,
independently of the leg before it (no entry edge at a via), so the baseline is never longer
than the seed; a leg whose cheapest legal path would pass a node twice is the turn-blind
shortest path instead, a lower bound, so the baseline exists whenever the seed does (such a
leg may take a turn a turn restriction bans, as the one at Alder St and Birch Ave in
`tests/fixtures/osm/restrictions.osm` does, whose cheapest legal way goes on and comes back;
the route never does). It does not
search again for a node-simple path (`shortest_path(..., simple_retry=False)`; **Searches**),
which could be longer than the seed's.

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
   constraint, none it refuses: **Constraints**; a soft one leaves the seed alone). Each leg
   is `leg_path`'s, the tree over bans (**Searches**): a walk entering a node twice is searched
   again without the edge coming back to it, else without the one it first entered by, and a
   leg driving an edge and its twin without the later one; and a round trip's leg
   whose other legs drive a single edge avoids that edge's road unit, so a trip to a neighbour
   and back is not one road out and back.
2. **Penalty re-solve.** When a leg fails, up to 4 rounds (`docs/PLAN.md` §3.3): the leg's
   unconstrained path (turning legally, from the same entry edge) names the nodes other legs hold; those legs are rebuilt with 8× the
   weight on every edge touching those nodes, keeping their old path when they have no other;
   then the leg is tried again.
3. **Hard mode** (**Constraints**). Once the legs are built, while a road the seed has left comes
   back (`constraints.returning`), up to 16 rounds (`HARD_SEED_ROUNDS`): the leg where it comes back
   is searched again with that road banned but for the stretch before, else the leg of that stretch
   with the road banned but for the one it comes back on (so a stop on the road keeps a way to it),
   a kept stretch free to grow by an edge at either end. The bans stay with their leg for the rest
   of the build: every later search for that leg sees them (the penalty re-solve of step 2 runs
   while the legs are first built, before any). When this fails, it is tried once more from the legs
   as they are, with every ban dropped but the one the last return asks for, since bans piled up
   over rounds can shut out a legal way. It is a heuristic (**Constraints**, hard mode): it may
   refuse a leg, or move a stop, where a legal route exists.
4. **Failed legs.** A leg that still fails, a leg that would not move (two stops on one node),
   a road neither leg can drop, or a seed `check_route` refuses names the leg.
5. **Fallback** (`resolve_stops`; `docs/PLAN.md` §3.2): the leg's earlier stop, if it is a
   via, is re-snapped to its next-nearest candidate (never another stop's node), up to 10
   times, and the whole seed is rebuilt each time.
   Then, with that stop back on its node, the later stop is re-snapped the same way (a via or
   the end). The start never moves. Then `StopUnreachable` names the leg. A via trapped
   in a one-entrance subdivision therefore walks out to the street the subdivision hangs off,
   and the stops after it stay (`tests/engine/test_seed_route.py`).
6. **Blame.** An unreachable leg is put down to the constraints that shape the seed only when the
   seed builds without them, with at most two more builds, on failure only: under a strict turn
   constraint, `InfeasibleTurnConstraint` when it builds without it; under hard mode,
   `InfeasibleHardMode` ("No route from stop 1 to stop 2 without repeating a road and with no road
   twice, after moving stop 2 up to 10 times.") when it builds without that; under both, the strict constraint's when
   hard mode alone builds, hard mode's when the strict constraint alone does; else the plain
   `StopUnreachable`.

**A round trip with no stop between its ends** has no legs. Its seed is a loop: the waypoint
strategy from the start back to itself, on length, with radius `min(1000 m, r_max)` and the
waypoint at least 500 m away, drawn up to 20 times from the request's rng. If none closes into
a legal loop (under hard mode, one that never comes back to a road it left),
`StopUnreachable` for the leg (0, 1), or its constraint's error (step 6). Its baseline is 0 m,
so its ratio is undefined.

**The worst order** (`choose_order(data, spatial, backend, request)`, for a request with
`reorder`; `docs/PLAN.md` §3.13). Every order of the stops between the start and the end is tried
(the start and the end stay; a round trip's start is its end), in lexicographic order of the
given positions, the given order first: up to 120 orders for 7 stops. Each is resolved in full,
as a request in that order would be: its own masks (the tier and the leash follow its polyline),
for a pilgrimage the brand within them (an order whose area holds none of its locations is
skipped; the given order's is planning's step 1), then the stops snapped and moved by the
fallback above until a seed fits, the seed built once (`resolve_stops`' work, `_settle`, hands it
on). The order whose seed is the longest wins (strictly longer, so the first among equals); an
order no seed fits is skipped, but the given order's error is raised as `resolve_stops` raises
it, so a request is planned in its worst order only when it can be planned in its own, and is
refused with the same error otherwise. The baseline is not compared: it exists whenever the seed
does. A seed with legs draws nothing from the rng, and the search is bounded by its count of
orders, never by time, so the order is fixed by the build and the request and does not depend on
the seed. The result is the request with its stops in that order (`reorder` still set) and the
order, each stop's position among those given (`[0, 3, 1, 2, 4]`). Planned again, that request
picks the same order: it comes first among its own orders and none is longer. Fewer than two
stops between the ends make one order, tried by nothing.

Planning goes on with the reordered request (**Planning**), so the baseline, the ratio and a
ratio target are the chosen order's: what a sane GPS drives through the stops in the order the
route visits them. The ratio therefore does not count the way the order itself lengthens the
trip; the route's length does.

The cost is a resolution an order: masks (3 ms on Portland's build, 58 ms on us-t3's 2.15 M
nodes), the snaps and a seed, and up to twenty more builds for an order whose stop must move.
Measured (2026-10-31, CSR, the dev machine at a load of 5-12; `config.MAX_REORDER_STOPS`): 7 stops
in town, 120 orders, 11.8 s on Portland's build (118 orders fit; 28 s when each order also built
its seed a second time and its baseline); on us-t3, 120 orders took 10-75 s with one build each
and no stop moved, and 102 s resolved in full in New York with a stop that had to move in nearly
every order (Queens: on us-t3's roads the nearest node to a stop is often one a seed cannot pass
through, and the fallback moves the failing leg's earlier stop ten times before its later one);
5 stops, 6 orders, took 0.3-7.3 s on us-t3 (7.3 s in Los Angeles, Long Beach moving in every
order) and 5.0 s on Oregon. Hence 7 stops on a build of at most 100,000 nodes and 5 on a larger
one. Resolving each order with its stops as snapped only, without the fallback, cost 4.7 s on
Portland but left only the given order in Los Angeles (1 of 6) and 3 of 6 in New York.

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
  (bans per window), `window(route, i, j)` (the window the loop tries for the one it drew: as
  drawn, unless a constraint widens it; hard mode does), `search_weights` and `waypoint_pool`
  (search guidance), and `initial_radius` and `search_budget(radius)` (default 8 × radius,
  `SEARCH_BUDGET_FACTOR`).
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

**`Brand`** (`objective: "brand"` with a `brand` id; `docs/PLAN.md` §3.7) visits as many of one
brand's locations as it can, then drives as far: its locations are the build's brand index
(`GraphData.nodes_of_brand`; `docs/reference/pipeline.md`, **enrich**: the nodes a route can pass
through nearest the brand's POIs, within 100 m), and its score is visits ×
`COLLECT_ITEM_WEIGHT` (1e6) + the length in metres: node terms of the weight at each of the
brand's nodes, edge terms the length, no pair or global term. A route never repeats a node, so
each location counts once (a round trip's start once). The detours search on length. A waypoint
is drawn from the brand's nodes in the request's active area that the route has not visited,
worked out once per route; once none is left, from any node (and a ball holding none of them
draws from all its nodes), as for `collect`; the first radius is collect's. Its primary quantity
is the length, its target value the visits; a ratio target is refused under it, a duration
target compares the experienced time. Its `Metrics.counters` are `{"brand": {id, name, visits,
of}}`, `of` the brand's nodes in the area, and `describe` is the same object;
`brand_counts(data, brand, nodes, area)` counts the same from a route's nodes and the build
alone, for the validator and the API. Planning refuses a brand the build does not have
("unknown brand 999") or none of whose nodes the request's area holds ("no Starbucks within
reach"): `brand_problem` says which, `plan_route` raises it as `BrandRefused`, a `ValueError`
whose `field` is `"brand"`, right after the masks (**Planning**).

**A new mode** is a subclass of `BaseObjective` that overrides the terms and hooks it needs,
registered in `OBJECTIVES` under its request key (and in `ObjectiveKey`; `make_objective`
gives `collect` the request's item and active nodes, `brand` its brand and active nodes).
`tests/engine/test_objectives.py` holds `delta` to a full re-score with a test objective that
has every kind of term, the first-edge term included; a new mode gets the same test
(`SlowestTime` has it, node terms included, `test_collect.py` for each item, and
`test_brand.py`). A constraint is an objective about turns (**Constraints**); `Composite(objective,
constraints)` (`objectives/composite.py`) is the one objective the engine runs when a request
has any: the sum of every part's score and `delta`, the objective's metrics, guidance, target
value and primary quantity, every part's feasibility (all must agree), the union of their
bans and of their turn classes, the drawn window as each part in turn widens it, and as its
`constraint_delta` the sum of the constraints' `delta`s.

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

`constraints.py`: a request's `Constraints(no_left, no_right, strict, hard)` adds turn
constraints and hard mode (below) to its objective; `no_left` and `no_right` together is
refused ("straight only is not a route"). Each turn constraint is a `TurnConstraint`, an
objective about two classes: `NoLeft` about left turns and U-turns, `NoRight` about right turns
and U-turns. `constrain(objective, constraints, data)` gives the objective the engine runs: the
objective alone without constraints, else a `Composite` of it and them. The baseline is never
constrained (a sane GPS turns left), and a ratio target under a constraint compares the
objective's primary quantity as before; shrinking toward a target, a splice must not add a net
avoided turn (**Targets**).

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
leash, and the cheapest walk the edge-state search finds then loops, which is no path unless
searching again finds a node-simple one (**Searches**).
On Portland (the version 6 build 3a4a3df6269f, engine version 4), strict no-left plans 7 of the
20 acceptance pairs (pairs 0, 6, 9, 10, 11, 13 and 17; 4 before the search looked again for a
node-simple path) and strict no-right 3 (the others fail at the seed, after the stops'
fallback); Powell's → Reed plans under strict no-left, its end moved 214 m by the fallback
(1.86×, no left turn), and refuses under strict no-right (**On Portland**). The same geometry
bounds a soft run: a detour that adds no left turn needs right turns at both ends of its window,
or two inside it, so where a left turn costs more than any detour gains the route barely grows.
Hence the penalty of a kilometre, not more: at 10⁶ (fewest left turns first) the soft modes gave
a median of 1.12× on the acceptance pairs; at 1,000 they give 5.33× (no-left) and 6.47×
(no-right), against 21.5× without (**On Portland**). And a soft route barely shrinks toward a
smaller target: a shorter way round mostly needs the left turn its three rights stood for, which
shrinking may not add (**Targets**).

**Hard mode** (`hard`; `HardMode`; `docs/PLAN.md` §3.7): each road in one stretch at most, so
once the route leaves a road it never comes back to it. A road is an identity (`edge_identity`:
the highest-class ref, else the normalised name; `docs/reference/pipeline.md`, enrich), so
"northeast walker road" and "northwest walker road" are two roads, and both carriageways of a
divided road are one. The rule is over the positions of the whole route's edges: those of each
identity form one run.

- An edge without an identity (−1: 2.7% of Portland's, mostly alleys and the few ramps
  cleanup leaves outside its super-junctions) is free, but not transparent: a road on both
  sides of an unnamed edge is two stretches.
- A stop splits nothing: a road driven through a via is one stretch.
- A round trip is read from its start to its end: coming home along the road it left by is a
  return.

It is its own flag (not "strict"), allowed alone or with a turn constraint, and has no terms;
its hooks keep the rule:

- **`banned_edges`**, per window: every edge whose identity the route drives outside the window,
  but the identities of the edges either side of it, which a detour may extend (and those only
  inside the window, freed by it). A window inside one stretch (the same identity either side)
  can only be replaced along that road, so every other edge is banned there, and its searches
  fail at once. The route's identity counts are kept per route and rebuilt when it changes (a
  bincount and one pass over the edges, once per accepted splice); a window's mask is a copy and
  a few identities' edges cleared.
- **`feasible_window`**, exact for a legal route: the splice is legal iff the detour with the
  window's two neighbours has one stretch per identity, and every identity of the detour that
  the route drives outside the window is a neighbour's. `find_detour` asks it of every
  candidate (**Detours**), and the loop again (**The loop**).
- **`window`**: a window drawn inside a stretch is widened out to the stretch's ends, never past
  the stops either side, so the loop tries a window a detour can leave the road from. Without it
  hard mode's routes were half as long on Powell's → Reed (a prototype: 6.4× against 12.7×).

The seed keeps the rule itself (**Seed and baseline**, step 3). It is a heuristic, not a search
for every legal route: a leg it finds no way to build under hard mode, but can without it, is
`InfeasibleHardMode` ("No route from stop 1 to stop 2 without repeating a road and with no road
twice."; the API's `STOP_UNREACHABLE_WITHOUT_REPEAT`, "… Try without it."), and a stop the
fallback moves notes "the seed found no way to nearer nodes under hard mode". On random 4–6 × 4–6
lattices whose rows and columns repeat two or three names (the review's exhaustive probe, seeds
11–14, 800 trips with two or three stops), the seed refused 90 trips, 56 of which have a legal
route (an exhaustive search finds one); with a kept stretch free to grow and the retry it refuses
70, 36 of which have one (each change alone recovers about 10; up to eight retries, 24). On
Portland a real street's names repeat less: of the 20 acceptance pairs, one is refused (**On
Portland**). `check_base` refuses a base route that comes back to a road, and the validator
proves the rule from the build's identities (**The validator**, `constraints`). `stretches` and
`returning` read the rule off a route's edges.

## Detours

`find_detour(backend, spatial, data, route, i, j, *, forbid, banned, node_mask, radius, objective,
rng, entry_edge, exit_edge, strategy=None, refused=None)` returns a path from a = `nodes[i]` to b =
`nodes[j]`, or None. It returns a path only if the path differs from the removed run, the
objective's `feasible_window` allows it (asked first, being the cheaper; so the local-exhaustive
ranking gives the best path a constraint such as hard mode allows, not one it refuses) and
`route.check_splice` accepts it; each candidate the splice refuses is tallied by its refusal class
in `refused`, a `Counter`, when one is passed. No `feasible_window` before hard mode refused
anything a search proposes, so the filter changed no route. `forbid` and `banned` are the loop's
search masks. `banned` includes the request's and the objective's bans, and is passed to the splice
check as `extra_banned`. `entry_edge` is `edges[i − 1]` and `exit_edge` is `edges[j]` (−1 at the
route's ends): every strategy searches through `search.py` from the one to the other, so on a build
with transition rows the detour's turns, the window's two boundary junctions included, are ones the
tables allow.

**Choosing a strategy.** It is drawn from `rng` with weights 0.6 waypoint, 0.3 random weight,
0.1 local exhaustive. Local exhaustive is allowed only when a and b lie less than 800 m apart;
otherwise the draw is 2/3 waypoint, 1/3 random weight. `strategy` forces one (tests, and the
round-trip seed).

- **Waypoint.** A waypoint w is drawn from the nodes the search may enter within 4 × radius of
  the midpoint of a and b (`SpatialIndex.nodes_within`, a scan of the request's nodes: a query
  of the whole build's KD-tree, the index before the grid, cost a millisecond per attempt once
  the ball covered the area). The pool is the objective's when it has one and the ball holds some of it, else every
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
  passes a node twice and searching again finds no node-simple path). The paths are ranked by
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
banned_edges=None, goal=None, check_delta=False, anneal_t0=None)` is a generator of
`ProgressEvent(iteration, elapsed_s, score, metrics, splice)`, one per accepted splice. The
`Splice(i, j, nodes, edges)` replays it on a copy of the route. The generator returns
`WorsenResult(best, events, attempts, accepted, stopped_because, elapsed_s,
refused_transitions, reheats)`, and `run_worsen(..., on_event=None)` drives it.
`refused_transitions` counts the candidates the splice refused with `TransitionForbidden`: the
searches see the tables, so a nonzero count means a search returned a turn it should not have.
`reheats` counts the times a max run reheated (**Annealing**). Each attempt:

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
   changes, ties to the earlier edge. Its ends then move out along the route (`widened`,
   engine version 5) to the nearest nodes a detour can leave or join, or to a stop, never past
   one: `detour_ends`, marked once a run, are the nodes with at least three distinct neighbours
   over the request's usable edges (both ends active, the edge not banned). A node in the
   middle of a road, kept where its tags change, has only the route's own two neighbours, so no
   detour can start or end there: on the national T1 build only 10.8% of New York → Los
   Angeles's seed nodes, and 12.5% of Portland → Chicago's, can be left (their median edges are
   259 m and 447 m long), and 1.8% and 2.6% of their one-edge windows have two such ends,
   against 61.2% on Powell's → Reed; there 11,017 of 11,046 attempts found no detour in 60 s
   and the run converged at 1.58× (the T1 tier, a 783 km leash). Widened, the same request
   makes 6.19×, 9.93× and 11.21× with seeds 1, 2 and 3 in 60 s (**On the national T1 build**);
   Portland's acceptance pairs (**On Portland**, 20 pairs, seed 1, 30 s, build a453e105582b)
   went from a median of 21.22× (3.40–49.45×) to 24.30× (6.10–53.93×), every run still
   converging, and Oregon's named trips (30 s, other runs beside them, so their attempts
   differ) from 11.07× to 11.07× (Portland → Bend), 14.95× to 17.81× (Eugene → Portland), 6.71×
   to 10.41× (Medford → Portland) and 5.30× to 7.00× (Astoria → Ontario). The objective's `window` may then widen the window further
   (hard mode, a window inside one road's stretch), drawing nothing.
2. **Masks.** `route.release(i, j)`, then OR in the request's `banned_edges` and the
   objective's bans for the window.
3. **Detour.** `find_detour` at the current radius.
4. **Accept** when the goal accepts the detour (`Goal.accepts`; for `max`, the default, when
   its `objective.delta` is strictly positive; **Targets**) and `feasible_window` agrees. The
   new score and metrics are a full `objective.evaluate` of the spliced arrays.
   With `check_delta` (tests), the loop asserts that this equals the current score plus
   `delta`. The splice is told the request's and objective's bans as `extra_banned`. `best` is
   tracked by `Goal.better`: for `max` the highest score. Once a max run has reheated
   (**Annealing**, below), a detour that loses is sometimes taken too.

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
empty ones. The same rule holds in every phase of a target run. A max run reheats instead, its
first 3 times (**Annealing**), counting the empty cycles again from each reheat: it ends 1,604
attempts after its last splice when that came after its last reheat, else 1,203 after the
reheat.

**Annealing** (engine version 6, 2026-10-31). A max run that would stop as converged reheats
instead, up to `ANNEAL_REHEATS` (3) times, and stops as converged after the last; the cycle
starts again as any new one does. While heated, a detour the goal refuses is taken anyway when
it loses (`delta` < 0), adds no net penalised turn (`constraint_delta` ≥ 0) and
`feasible_window` agrees, with probability exp(delta / T), one `rng.random()` drawn for it
after it is scored: T = T0 × `ANNEAL_COOLING` (0.99)^k after k splices accepted since the
reheat, and none once below T0 × `ANNEAL_FREEZE` (1/1000), so every heat ends greedy
(`temperature`). T0 (`plan.anneal_temperature`) is `ANNEAL_T0_FRACTION` (1 %) of the
objective's primary quantity of the baseline: metres for the distance, collecting and the
pilgrimage, seconds of experienced time for the slowest time; of the route the loop starts
from for a round trip, whose baseline is 0 m. An item or a visit weighs 10⁶ in the score, so a
detour that loses one has a delta of −10⁶ plus whatever length it adds, and its chance is at most
exp((−10⁶ + added) / T0): for a detour adding little length, 0 in floating point (exp underflows
below about −745) while T0 is under about 1,342 m, a baseline under about 134 km; about
1.4 × 10⁻¹¹ a candidate at a 4,000 km baseline (T0 40 km) and 9 × 10⁻⁹ at 5,400 km. Whatever
the current route gives back, the route returned is the highest-scoring one the run had
(`Goal.better`): the best never moves down. A target run never anneals (`plan_route` passes
`anneal_t0` for a max goal only, and the loop ignores it for any other), and the alphabet route
has its own search. Only a strict loss is taken: exp(0) = 1 would take every equal-length
alternative at any temperature, and on a lattice, where every
way round a block is as long, a heat then spliced in equal-length swaps until its freeze (688
of them) and the random towns of `test_worsen.py` ran about a hundred times greedy's attempts
(175,000 against 1,700); on Portland no candidate had a gain of exactly 0 (none of the 989–2,028
a run weighed, on four pairs) and the strict rule gives the same routes byte for byte. A splice
taken so is an accepted splice in every way: its event, the radius ×1.5, the failures reset, its
cycle not empty. `best` moves only by `Goal.better`, so it is never one, and the run returns
the highest-scoring route it had, which may come before its last (within 0.4 % of it in every
Portland run below; on the fixture lattices the last is often a little below).

Why it reheats at convergence rather than cooling from the first attempt, measured with greedy
interleaved per pair in one session on Portland's acceptance pairs (build 0b72253e5e8a, seed
1, 30 s, seed 2 on the first five for the Jaccard; a load of 7–24):

| Schedule | Median | Range | Converged | Per pair against greedy | Jaccard of seeds 1 and 2: edges, road units | Median run |
|---|---|---|---|---|---|---|
| greedy (version 5) | 26.05× | 6.10–54.05× | 16 of 20 | – | 0.13–0.18, 0.28–0.33 | 9.2 s |
| cooling per accepted splice from the start (×0.993, T0 3 %) | 27.92× | 7.15–51.31× | 18 | ×0.91–×1.71 | 0.14–0.20, 0.27–0.40 | 12.5 s |
| cooling per cycle from the start (×0.7, T0 3 %) | 28.21× | 6.16–52.16× | 13 | ×0.92–×1.69 | 0.15–0.21, 0.28–0.39 | 19.0 s |
| reheating on convergence (as built) | 29.96× | 7.11–55.17× | 7 | ×1.00–×1.64 | 0.17–0.23, 0.33–0.40 | 30.0 s |

Greedy's own seeds differ that much per pair (seeds 2 and 3 against seed 1 run ×0.69–×1.77 and
×0.65–×1.44, five and nine pairs more than 10 % lower), so a schedule that changes the run from
its first draw is a reseed as much as annealing. Reheating keeps the greedy run, draw for draw,
until it would have stopped, so its best is at least greedy's on every pair greedy converges on
(16 of 16 here; the others ran to the budget in both), and it uses the budget greedy leaves
idle. On Oregon's named trips (30 s) and the national ones on `us-t3` (Portland → Chicago and
New York → Los Angeles, 60 s) every run stops on its budget, so none reheats and each is
greedy's (their figures moved only with the attempts the load allowed: 14.72× and 15.25×,
19.15× and 18.61×, 9.82× and 9.11×, 6.70× and 7.20×, 8.33× and 8.13×, 3.81× and 3.81×), where
cooling from the start lost 5–9 % on four of the six. On eight of the pairs, reheating from 3 % of
the baseline made a little more than from 1 % (a median ×1.29 against ×1.22 of greedy per pair)
but converged within the budget in 2 of 8 runs against 5, and as many reheats as the budget
allows (×1.31) in none: 3 reheats from 1 % keep a run bounded. The acceptance run as built
(`tools/acceptance.py --region portland --pair-seed 2026 --pairs 20 --seed 1 --budget 30`, the
same build, 2026-10-31, a load of 7–12) plans all 20 pairs and the validator passes all 20, at a
median of 30.59× (7.11–54.56×), 10 of 20 runs converging within the 30 s, the median run 29.3 s
at 7,019 attempts/s, no candidate refused for a transition; seeds 1 and 2 share 0.17–0.23 of
their edges and 0.32–0.41 of their road units on the first five pairs.

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
best, and the max loop draws and ranks exactly as before (the golden route); since engine
version 6 a max run that converges goes on annealing (**Annealing**), which no target run does.

**Determinism.** Every random choice is drawn from one `numpy.random.Generator(PCG64(seed))`, in
a fixed order: the round-trip seed, then the loop's windows, strategies, waypoints and weights.
Candidate lists are sorted arrays, and no set is iterated. A seed therefore gives the same
sequence of splices on any machine. The clock only decides when to stop:

- a run that stops on iterations is reproduced byte for byte;
- a run that stops on time is a prefix of the same sequence, and is reproduced by planning
  again with `max_iterations` set to its `run.attempts` and a budget in seconds at least as
  large as the first run's (or the clock may cut it shorter).

Annealing (engine version 6, **The loop**) adds one draw, at a candidate a heated max run may
take, and none before a run's first reheat. A run that stops before it would have converged
therefore draws what version 5 drew and gives version 5's route, byte for byte: the goldens
(400 attempts cannot end even a cycle), and the CLI's default 5,000 attempts and the daily
route's 5,000 wherever greedy needs more to converge (16,406–173,807 attempts on Portland's
acceptance pairs; the two national trips above did not converge within their minute). Its
temperature is a count, so a run that stops on time is still a prefix of its run.

**Measured** on the dev machine, with the reference backend unless the row says otherwise:

| Graph | Result |
|---|---|
| 20 × 20 grid, corner to corner | converges in 0.2–0.4 s (3,800–6,800 attempts) at 10.3×, using 391–393 of 400 nodes (seeds 0–3); at engine version 6 (2026-11-01, `plan_route`, seeds 0–3) it converges after its 3 reheats in 0.35–0.38 s (8,345–8,898 attempts) at 10.26–10.32×, the same 391–393 nodes: every way round a block is as long, so a shorter alternative loses at least 200 m against a T0 of 38 m, and the heats took one shorter splice in the four runs; a heat that takes nothing adds its 1,203 attempts (3 empty cycles), so with no splice taken the route is greedy's and the run 3,609 attempts longer |
| 30 × 30 grid, start, centre via and end | converges in 0.5–0.6 s at 14.9–15.1×; at engine version 6 (2026-11-01, `plan_route`, the via at node 465, seeds 0–3) it converges after its 3 reheats in 0.70–1.02 s (14,001–19,809 attempts) at 15.07–15.17×, 875–881 of 900 nodes, the heats taking 2–6 shorter splices a run |
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

## Alphabet

The alphabet route (`objective: "alphabet"`; `docs/PLAN.md` §3.8; `alphabet_search.py`): the
named roads, in order, start with A, B, C, ..., wrapping Z → A. Splicing a detour into such a
route would break every letter after it, so it is not an `Objective` the worsen loop runs but a
constructive search of its own, on the same graph, searches, `Route.splice` and validator.

**Letters.** A road's letter is its name's, `pipeline.names.road_letter`: the name normalised
(`normalize_name`: casefolded, `NW` spelled `northwest`, `St` `street` but as the first word),
its leading directional words dropped, but one followed by a single street type (names.py's
suffix table) kept as the name itself, accents taken off, and its first letter or digit read: a
letter a–z is the road's letter; a digit (a numbered street), a letter of another script, or
none at all makes it a free connector. So "Southwest Broadway" is B, "NW Glisan St" G,
"Southwest Parkway Avenue" P and "North Terrace Drive" T, but "North Street" N and "E Avenue" E,
and "Northeast 82nd Avenue" is free. `edge_letters(data)` gives every edge's, 1–26, or 0 for a free
connector: an unnamed edge, a link (`EdgeFlag.LINK`, a ramp) or a name without a letter. On
Portland's build ffe2e7c881ca, 2,396 of its 16,892 names are free, and 29% of its edges; the
most common letters are C (1,402 names), S (1,349), B (1,182) and M (1,172), the rarest X (2),
Z (29), U (47), Q (57) and Y (71).

**The rule.** A route has three parts:

1. its **first leg**, from the start to its first edge of letter A (`alpha_from`), is free: any
   roads;
2. its **alphabet part**, from `alpha_from` to `free_from`: the letters of its lettered edges
   (the free ones skipped), equal letters in a row counted once, read A, B, C, ..., Z, A, ...
   So after a road of one letter the route may drive any road of that same letter (Alder, then
   Ash, is one A), but the next road of another letter must carry the next one;
3. its **last leg**, from `free_from` to the destination, is free again.

`alphabet_counts(data, edges, free_from)` reads it: `{letters, sequence, roads, alpha_from,
free_from}`, the letters in order, the first road of each, and where the part starts (its first
A edge, or `free_from` when it has none) and ends. An alphabet route's metrics carry it as their
`alphabet` counter, and `alphabet_scored(objective, ...)` scores a route: letters ×
`ALPHABET_LETTER_WEIGHT` (1e6) plus the score of the objective it is given, the distance under
the request's turn constraints (the length, less a soft constraint's penalties), so the most
letters, then the longer. The validator proves the rule (**The validator**, `alphabet`).

**The search** (`alphabet_route(backend, data, route, objective, budget, rng, node_mask, *,
r_max, banned_edges, on_event) -> WorsenResult`). It restarts from the seed route (`route`,
scored with no letter) within the budget. A restart grows its route a letter a step; each step
is one `Route.splice` replacing the route's free last leg, from the end of its alphabet part to
the destination, by a connector, a ride along a road of the next letter and a new free last leg,
so every route a run makes is a whole legal route:

1. **Candidates.** One tree search (`GraphBackend.dijkstra_edges_tree`, on length) from the end
   of the alphabet part, continuing its last edge, over the edges a connector may use: any edge
   before the first A road (the free first leg), after it the free edges and the roads of the
   letter just completed; the edges of the wanted letter are its final states. It looks within
   `ALPHABET_RADIUS_M` (1,500 m) of driving, then twice that while under `r_max`, then anywhere
   in the request's area. The candidates join each road of the wanted letter at the junction
   where the search first reaches it, in every direction the road leaves that junction (both
   ways along a two-way road: the first settled alone would always be the lower edge id, one
   way, which on a grid walled the route in), each by its tree path, dropped when that path
   enters a node twice.
2. **Rides.** For each candidate, a tree search from its first edge along that road's name only,
   avoiding the connector's nodes and the twins of its edges, lists the ways on; a ride is drawn
   uniformly from the candidate's first edge alone and every simple way on: at least one edge,
   at most the whole road.
3. **Choice.** A candidate's lookahead is the number of roads of the letter after it that leave
   an active node the route does not use within `ALPHABET_RADIUS_M` (straight line) of its
   ride's far end. The candidates are tried in the order of keys u^(1 / (1 + lookahead)), u
   uniform, the highest first (a weighted draw without replacement), at most
   `ALPHABET_CANDIDATE_TRIES` (8).
4. **Splice.** The new free last leg is the shortest path from the ride's end to the destination
   that avoids the route, the connector and the ride (`search.shortest_path`, twins avoided),
   tried for the drawn ride, then for the candidate's first edge alone. The connector, the ride
   and that leg replace the old last leg when the objective's `feasible_window` agrees and
   `Route.splice` accepts it (with the request's and the objective's bans for the window).

A step with no candidate, or none that splices, ends the restart; its route already ends with a
free leg to the destination, so no restart is lost and the destination is always reached (a
restart that adds nothing leaves the seed). A first step with no candidate (no A road in reach)
draws nothing from the rng, so every restart would be the same: the run ends at once,
`"converged"`, with the seed and no letter. The best route is the highest score, compared after
every step; each better one is a `ProgressEvent` whose splice replaces the whole previous best
(`Splice(0, len(best.edges), ...)`), so the events replay from the seed to the best, and its
`iteration` is the restart that found it. A run stops on its budget (`"budget"`: `max_iterations`
restarts, or the seconds, looked at between restarts only, so a restart is never cut and a run
that stops on time is reproduced with `max_iterations` set to its `attempts`) or after
`ALPHABET_PATIENCE` restarts in a row that found no better route (`"converged"`). Its
`WorsenResult` counts restarts as `attempts` and better routes as `accepted`, and
`refused_transitions` the candidates `Route.splice` refused for a forbidden turn (0: every
search sees the tables).

`ALPHABET_PATIENCE` is 1000, tuned on Portland (build ffe2e7c881ca, Powell's → Reed and →
PDX, seeds 1–3, 30 s): a better route still turns up after hundreds of restarts, so a patience
of 100 gave 9–20 letters, 300 gave 10–23 and 1000 gave 13–23 (20–23 in five runs of the six),
each run converging in 10–27 s or running to its budget (measured before the directional rule
was narrowed, which changes no route of the acceptance run: **On Portland**).

**Constraints.** Every search turns only where the transition tables allow, and refuses a strict
turn constraint's classes; a soft one's classes are refused by the candidate search first (made
again without them when it finds none) and by the rides, avoided by the free leg where it can,
and its penalties count in the score's length part. The objective's bans for
the window (`banned_edges`) and its `feasible_window` apply to every step, so a constraint on a
window composes: under hard mode (**Constraints**) no route along the way comes back to a road
it left. Hard mode bites, since the connectors' numbered avenues are roads too, each driven in
one stretch: on Portland (300 restarts, seed 1) Powell's → Reed reaches I under it and Powell's
→ PDX only A.

**Requests.** An alphabet request takes a start and an end only (a round trip too), no target
but `max`, and no base route (**Requests**, **Planning**). The seed is the plain one, as for the
distance. Every random draw is the request's rng, in a fixed order over candidate lists in settle
order, so a seed gives the same route on either backend.

## Certified worst

The certified worst (`certified`; `docs/PLAN.md` §3.12; `exact.py`): the provably longest legal
route for a request's stops within its area, found by OR-Tools' CP-SAT. It is the second driver
beside the alphabet route's that is not the worsen loop: it shares the request's masks, `Route`
and the validator with planning, never the loop, the kernels or the splice. OR-Tools is imported
inside `exact.py`'s functions only, so a worker that plans any other route never loads it: in a
worker that has planned a Portland route, `cp_model`'s import (pandas with it) costs 0.3 s and
about 55 MB of resident memory (193 MB to 248 MB at its peak).

**Requests.** `certified` (**Requests**) takes the distance objective only, no target but `max`,
no worst order, no turn constraint, no hard mode and no round trip (`certified_problem`; the
dummy arc below would be a self-loop: splitting the start in two is a later step); stops between
the ends are taken. A base route is a `ValueError` ("a certified route is planned from its stops:
no base route"), which the CLI and the API refuse first; the API refuses a portfolio and a
drawing too. `max_iterations` does not apply.

**The area** (`certified_area(data, masks, stop_nodes)`): the edges every search may use (both
ends in `active`, not in `banned`, no self-loop) that lie on some walk from a stop to the next
inside the masks, the other stops blocked, the walk never leaving its end nor coming back to its
start, legality otherwise ignored, and the nodes those walks pass; the edges into the start and
out of the end are left out. Its counts are what a certificate allows: at most
`CERTIFIED_MAX_NODES` (300) nodes and `CERTIFIED_MAX_EDGES` (1,000) edges, else `AreaTooLarge`
(a `ValueError`, `field` `"leash"`: "This area is too big to prove. It holds 1,132 junctions and
3,865 roads; a proof allows 300 and 1,000. Shorten the corridor or bring the stops closer."). The reach is two breadth-first searches a leg (scipy's), over the area's candidate
edges only.

**The leash** (`certified_request(data, spatial, request)`). A request's own leash is used as
given. With the automatic one (0.6 × the trip, at least 3 km, which on a city build holds
thousands of nodes) the leash is the widest, in `CERTIFIED_LEASH_STEP_M` (10 m) steps from
`CERTIFIED_LEASH_MIN_M` (50 m) up to the automatic leash, whose area holds at most 300 nodes and
`CERTIFIED_AUTO_MAX_EDGES` (600) edges, found by bisection over request copies with that leash
(their masks and their stops snapped, without the re-snap fallback, a leash that does not join
the stops counting as narrow enough); about ten mask builds, 20–80 ms on Portland's build. When that
leash does not join the stops, the narrowest that does is taken if its area is within the hard
caps, else `AreaTooLarge` ("the narrowest leash that joins the stops, 640 m, makes an area of
... : bring the stops closer"); when not even the automatic leash joins them, it is kept and
resolving the stops says why. The request comes back with the leash as a number
(`leash_km: 0.42`): planning goes on with it, so `PlanResult.request`, route.json's request and
the API's stored request record it, and `validate` rebuilds the same masks with no search. The
two caps differ because the automatic area should be proven within the default budget, while an
explicit leash and a longer budget may certify more: on Portland, at 300 nodes and 600 edges 10
of 14 short trips were proven within 19 s (the other four 0.75–4.7 % short of a proof), at 300
and 800 only the downtown ones (**Measured**).

**The model** (CP-SAT, `add_circuit`, not MTZ): a Boolean per area edge (parallel edges each
their own: the circuit takes several arcs between two nodes, one of them at most true), a
self-loop literal ("skipped") on every area node but the stops, which must be visited, and the
arc from the end back to the start, its literal fixed true, so the one circuit through the nodes
not skipped is a simple path from the start to the end through every stop. Legality as
constraints: each twin pair within the area at most one (`twin_indptr`/`twin_edges`: carriageway
pairs and super-junction edges; the two directions of a road are node-simplicity's), and at a
node with transition rows each pair of an area edge in and an area edge out that the rows do not
list at most one (a visited node has one edge in and one out, so no line graph is needed). With
stops between the ends, each area node has an integer position, the start's 0, each edge taken
giving its head its tail's plus one, and each stop's position below the next stop's: the
positions keep the stops in order (one circuit a leg over shared node-use literals was slower:
0.9 s against 33.6 s, and 2.0 s against a 1.2 % gap at 60 s). The objective is the length in
decimetres, each edge's rounded to the nearest, so "longest" is to the decimetre on each road.
The hint is the seed route, complete (every literal and position), so the solve starts from a
legal route and ends with one at least as long. These are the validator's rules: chain and area
by the arcs, a node once by the circuit (and with it an OSM junction once, each id belonging to
one node), an edge once and a road unit once by the circuit and node-simplicity, twins and turns
by their constraints, the stops by the circuit and the positions.

**The solve** (`certified_route`): `CERTIFIED_WORKERS` (4) threads with `CERTIFIED_SUBSOLVERS`
(`max_lp`) added to CP-SAT's own (its portfolio of four lacks `max_lp`, whose linear relaxation
with the circuit's cuts is what proves the residential grids: Sellwood at 400 m, 134 nodes and
435 edges, in 2.6–3.8 s with it and 18–35 s without; eight default threads, which include it,
proved fewer of the larger areas than four with it), the request's seed (mod 2³¹) as CP-SAT's,
and the time left of the request's budget less `CERTIFIED_MARGIN_S` (0.5 s), at least a tenth of
the budget: `plan_route` counts from its own start, so the leash search counts against it. Each
better solution, in a solver thread, is read, chained from the start, scored by the distance
objective and made a route by `Route.new` under the request's bans (`check_route` on the whole
route: a solution it refused would be a bug, which stops the search and is raised), and handed to
`on_progress` as an `Incumbent`, a `ProgressEvent` whose splice replaces the whole route before
it, with the stops' positions, the solver's bound and the gap (both null when the bound lies
below the solution's own value, the certificate's guard below); between better solutions the
bound goes to `plan_route`'s `on_bound(elapsed_s, bound_m, gap)` at most every
`CERTIFIED_BOUND_EVERY_S` (1 s). An exception from either hook (a cancelled job, a closed pipe)
stops the search and is raised once the solve returns. The run's `attempts` are the solutions
CP-SAT reported (the first is the hint itself), its `accepted` the better routes handed on, and
it stops `"optimal"` when proven, else `"budget"` (`StopReason` gained `"optimal"`, which the
loop never returns). `PlanResult.certified` is the `Certificate`: `status` (`optimal` or
`feasible`), `bound_m` (the longest any legal route in the area can be: the route's length when
proven, else its length plus the bound's excess over it, in the model's decimetres), `gap`
(`(bound − found) / found` in the model's decimetres, 0 when proven), `nodes`, `edges`,
`leash_m`, `solve_s` (the solver's wall time) and `workers`; route.json's `run.certified`
(**Route files**). The certificate follows CP-SAT's status: `OPTIMAL` is proven (and its value
must be the last route's, else a bug, raised); `FEASIBLE` takes the solver's bound only when it is
at least the last route's value, else `bound_m` and `gap` are null (never the route's own length
passed off as a bound); `UNKNOWN`, the time limit come before CP-SAT took in the hint (a budget
of 0.1 s on Ladd's Addition at 500 m, 200 nodes and 599 edges, ends so), keeps the seed with
`bound_m` and `gap` null, `feasible`, stopped on its `budget`; `INFEASIBLE` and `MODEL_INVALID`
cannot happen with a legal hint, so either is a bug, raised. The validator then proves the route
as any other.

**Reproducibility.** The loop's rule is unchanged, and the certified mode does not touch it. A
proven route's length is fixed by the build and the request; the route among equally long ones,
and the best found under a time limit, are not, as a budgeted loop run's is not: the solver's
threads race, and a seed does not pin them. Tests assert lengths and legality, never a route's
bytes, on models the solver proves at once.

**Measured** (Portland's build 0b72253e5e8a, the dev machine at load averages of 8–21 from other
lanes, so every time is long and noisy: one Sellwood solve took 18, 29 and 35 s three times
running; the spike's probes under the lane's `cert-probes/`). The first solution, the seed, comes
in 5–90 ms; building the model takes 5–8 ms and the area 9–18 ms.

| Trip (two stops) | Leash | Nodes / edges | 4 threads | 4 + `max_lp` | Certified | Loop, 30 s | × the loop |
|---|---|---|---|---|---|---|---|
| Powell's → Pioneer Sq | 300 m | 94 / 179 | 0.03 s | | 4.88 km | 4.31 km | 1.13 |
| Powell's → Pioneer Sq | 600 m | 276 / 578 | 0.48 s | | 18.22 km | 16.96 km | 1.07 |
| Pioneer Sq → PSU | 600 m | 289 / 554 | 0.13 s | | 17.53 km | 14.78 km | 1.19 |
| Union Station → Lan Su | 600 m | 176 / 395 | 0.06 s | | 12.22 km | 8.72 km | 1.40 |
| Providence Park → Powell's | 600 m | 331 / 763 | 10.1 s | 7.6 s | 21.31 km | 16.56 km | 1.29 |
| Ladd's → Division | 400 m | 133 / 396 | 5.9–10.5 s | | 10.77 km | 10.01 km | 1.08 |
| Ladd's → Division | 600 m | 260 / 811 | gap 12.1 % at 60 s | gap 1.07 % at 60 s | 22.11 km | 17.72 km | 1.25 |
| Sellwood | 600 m | 230 / 773 | gap 8.2 % | 22.8 s | 25.68 km | 21.16 km | 1.21 |
| Alberta | 600 m | 273 / 914 | gap 8.5 % | 6.9 s | 26.45 km | 20.47 km | 1.29 |
| Hawthorne → Laurelhurst | 600 m | 258 / 803 | gap 9.4 % | gap 0.37 % | 25.47 km | 22.23 km | 1.15 |
| Irvington | 600 m | 171 / 569 | gap 8.0 % | 30.3 s | 22.07 km | 18.63 km | 1.18 |

One-way downtown grids (about two edges a node) are proven in a second or less; the two-way
residential grids (about three) are the hard ones. With one stop between the ends (4 + `max_lp`,
positions): 133–290 nodes and 304–754 edges proven in 0.3–27 s (Sellwood 429 edges 1.9 s,
591 9.9 s; Ladd's 397 11 s, 552 27 s; Alberta 754 10.4 s; downtown 588 2.0 s).

With the automatic leash and the default budget (19 s of solve, 4 + `max_lp`), at the caps as
built (300 nodes, 600 edges): Powell's → Pioneer Sq 610 m (283 / 591) proven in 0.3 s, 18.85 km
against the seed's 0.58 km and the loop's 18.07 km; Pioneer Sq → PSU 600 m in 0.1 s; Union
Station → Lan Su 760 m in 6.9 s; Providence Park → Powell's 500 m in 0.6 s; Alberta 420 m (182 /
578) in 3.1 s, 16.43 km against the loop's 12.49 km; Hawthorne → Laurelhurst 500 m in 5.3 s;
Irvington 620 m in 17.3 s; Ladd's → Division 500 m (200 / 599) and Sellwood 500 m (176 / 595)
within 4.66 % and 1.74 % at 19 s; with a stop between the ends, Powell's → Pioneer Sq → PSU
500 m in 3.6 s, Union Station → Lan Su → Pioneer Sq 530 m in 1.0 s, the Sellwood via 510 m in
9.3 s, the Ladd's and Alberta vias (420 m) within 0.96 % and 0.75 %. Every certified route was
1.04–1.32× the loop's converged route on the same request and leash. At 800 edges the automatic
areas grow to 530–820 m, and only the downtown trips are proven within 19 s (the residential
ones within 0.16–7.9 %).

`plan --certified` on the landed code (seed 1, the default 20 s budget; the loop at `--budget
30` on the same leash, converged in 0.6–4.5 s):

| Trip | Leash | Nodes / edges | Status, solve | Certified | Loop |
|---|---|---|---|---|---|
| Powell's → Pioneer Sq | auto, 610 m | 283 / 591 | optimal, 0.5 s | 18.85 km, 32.40× | |
| Pioneer Sq → PSU | 400 m | 170 / 305 | optimal, 0.03 s | 11.22 km, 11.79× | 9.54 km, 10.02× |
| Union Station → Lan Su | 500 m | 135 / 297 | optimal, 0.1 s | 7.24 km, 11.72× | 6.07 km, 9.82× |
| Sellwood | 400 m | 134 / 435 | optimal, 2.3 s | 14.68 km, 10.55× | 13.01 km, 9.36× |
| Alberta | auto, 420 m | 182 / 578 | optimal, 2.8 s | 16.43 km, 21.52× | 12.49 km, 16.36× |
| Sellwood, via SE 15th | auto, 510 m | 177 / 599 | optimal, 4.0 s | 20.56 km, 12.49× | 17.43 km, 10.59× |
| Ladd's → Division | 500 m | 200 / 599 | feasible at 19.5 s, within 1.32 % (16.77 km) | 16.56 km, 32.41× | 12.63 km, 24.72× |

The same request at seeds 1 and 2 (Alberta, proven): 16.43 km at both, proven in 2.4 s and 1.3 s. Alberta at a 1.5 km leash is
refused: "cannot certify: the area within a 1.5 km leash holds 1,132 nodes and 3,865 edges, over
the 300 and 1,000 a certificate allows: tighten the leash or bring the stops closer", exit 1.
Every route above passed the validator, its file `validate` too.

## Planning

`plan_route(data, request, *, backend=None, spatial=None, base=None, on_start=None,
on_progress=None, on_bound=None, check_delta=False) -> PlanResult(request, masks, snaps,
baseline, route, run, report, backend, goal, order, certified)` runs a request end to end. It searches with `backend`, else
`make_backend(data)` (`config.DEFAULT_BACKEND`, `auto`: `CsrBackend` when numba imports);
`PlanResult.backend` is the name of the one that searched. The steps run in this order:

1. for a certified request, its leash (`certified_request`, **Certified worst**); then the
   masks, and for a pilgrimage its brand: one the build does not have, or none of whose
   nodes the active mask holds, raises `BrandRefused` (**Objectives**);
2. with `reorder`: more stops than the build's cap is a `ValueError` (`reorder_problem`), and
   so is a `base`; then the worst order (`choose_order`, **Seed and baseline**), whose request
   (its stops in the order the route visits them) is the one planned from here on, with its own
   masks; `order` is each stop's position among those given (the identity without `reorder`);
3. one `Generator(PCG64(request.seed))`;
4. `resolve_stops` (snapping and its fallback, which may move stops; `snaps` are the final
   ones), and for a certified request its area, `AreaTooLarge` over the caps;
5. `seed_route` on them, or, given `base`, that route instead;
6. `baseline_route` on those stops;
7. `make_objective` with the baseline length and `r_max` (for `collect`, the request's item and
   active nodes; for `brand`, its brand and active nodes; the distance for `alphabet`), with
   the request's turn constraints (`constrain`), and the request's `Goal` (**Targets**) with
   the baseline length;
8. `Route.new` of the seed (or the base; an alphabet route's seed scored with its letters,
   `alphabet_scored`, none yet), then `on_start(snaps, masks, baseline, route, order)`;
9. `run_worsen` toward the goal within the request's budget, calling
   `on_progress(event, baseline)` on every accepted splice, with `anneal_t0` the reheats'
   temperature (`anneal_temperature`, **Annealing**) for a max goal and none for a target; for
   `alphabet`, `alphabet_route` instead (**Alphabet**), calling it on every better route; for a
   certified request, `certified_route` instead (**Certified worst**), calling it on every better
   route and `on_bound(elapsed_s, bound_m, gap)` on the bound between them;
10. `validate_route` on the best route, against the request's masks and the resolved stops (and
    for `alphabet`, with its `alphabet` check).

`PlanResult.request` is the request as planned: with `reorder`, its stops in the order the route
visits them, and for a certified request its leash as a number, which is what the route file
records and `validate` re-derives the stops from. `PlanResult.certified` is a certified route's
`Certificate`, None for any other.

Only the loop depends on the seed: the masks, the worst order (`choose_order` draws nothing), the
resolved stops, the baseline and the seed route (but a round trip's without legs, whose waypoint
is drawn from the rng) are the same for every seed of a request. The API's portfolios rest on it
(`docs/reference/api.md`, **Portfolios**): the K seeds of one request, each planned by
`plan_route` as it is, share their order, stops and baseline, and differ only in their loops.

An alphabet route cannot be continued: `base` with `objective: "alphabet"` is a `ValueError`
("the alphabet route cannot be continued"), which the CLI and the API refuse first.

The two hooks serve the API (`docs/reference/api.md`). `on_start` reports the route the loop
starts from before the first splice: the seed is not the baseline once there are stops between
the ends, and a client applying splices needs it; and, with `reorder`, the order the search chose
and the stops resolved in it, which the API's `snapped` event reports. `base` ("make it worse") is a route planned
earlier on this build for the same stops, tier and leash, anything the validator reads
(`RouteLike`). Before the loop, `check_base(base, masks, stop_nodes)` refuses it with
`BaseRouteMismatch`, a `ValueError` whose `field` is `"stops"` (its stop nodes are not the ones
step 3 gives) or `"route"` (a node outside the request's active mask, or an edge the request
bans); the API's pre-flight runs the same check. `Route.new` then checks the rest as for any
route (repeats, twins, transitions; an `IllegalSplice` from it is raised as it is, not as
`StopUnreachable`). Nothing is drawn from the rng before the loop, so a continuation is fixed
by the base, the request and its seed, on either backend (`tests/engine/test_plan.py`). Under
hard mode `check_base` also refuses a base that comes back to a road ("the base route comes
back to Row 0 at edge 3, which hard mode refuses", `field` `"route"`). A base past the
request's target shrinks toward it, one below it grows, and one within it is returned as it is,
with no attempt (**Targets**). Without the hooks, planning is unchanged.

A route that fails validation raises `ValidationFailed` and is never returned. The errors to
expect are `SnapError` and `StopUnreachable`, and for a pilgrimage `BrandRefused`, which the CLI
and the API refuse as a misfit of the field `brand`. The seed checks every leg itself, so a
`ValueError` or `IllegalSplice` from seeding would be a bug; `plan_route` still reports it as
`StopUnreachable` for the leg at fault (the first leg when the position is unknown), with
"the seed route was refused: …", so the CLI prints one line rather than a traceback.

## The validator

`validate_route(data, route, *, stop_nodes, node_mask, banned_edges, banned_turn_classes=0,
alphabet=False) -> ValidationReport`
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
4. **`osm_ids`**, the proof from the raw data. The route's nodes are expanded to the original
   OSM node ids merged into each (`member_osm_ids`: a stage A cluster's or a super-junction's
   members, a plain node's own id), a round trip's closing node left out, and no id may appear
   twice: no OSM junction is passed twice, whatever the engine's bookkeeping says. The index is
   the edge into the node that passes it again ("original OSM node 100 passed twice"). That a
   junction is one build node, and not two that each hold it, is the build's guarantee: a
   written build holds each original id in one node only (`write_build`'s deep `validate`), and
   `pipeline save` refuses the graphs the proof below cannot cover (`ShapePointError`); the
   check restates the proof from the route's data, and would fail on a build that broke them.

   The OSM nodes inside edges, which the build does not keep, need no check. Simplification
   removes only a node with one through path per direction (`docs/reference/pipeline.md`,
   **cleanup**), so such a node lies once inside one edge, or inside the two directions of one
   road unit, and is no node's member: passing it twice means driving an edge or a unit twice,
   which `edge_simple` refuses. `pipeline save` checks that premise on every build, with each
   edge starting and ending at its nodes' members (`ShapePointError`, **save**), so the proof
   holds for every build the engine reads.
5. **`transitions`**: every turn at a node with transition rows is listed.
6. **`stops`**: `nodes[stop_idx[s]] == stop_nodes[s]`, with positions strictly increasing from
   the first node to the last. For a saved route, `stop_nodes` are re-derived from its request
   (`resolve_stops`), not read from the file, so a file cut short and edited to agree with
   itself still fails here, naming the stop.
7. **`constraints`**: every node in `node_mask`, no edge in `banned_edges` (the request's
   tier bans), under a strict turn constraint (`banned_turn_classes`, from the request's
   constraints) no turn of a class it refuses, each turn classed again from the edges'
   bearings with the configured thresholds rather than the engine's table, and under hard mode
   (`hard`, the request's) each road in one stretch, from the build's `edge_identity` rather
   than the engine's counts: the first edge that starts a second stretch of an identity fails,
   "road 'Row 0' appears in two stretches, from edges 0 and 4, which hard mode refuses".
8. **`metrics`**: the reported length and time are finite and equal the ones recomputed with
   `math.fsum` within a relative 1e-6 (the comparison is written so that NaN fails), reported
   `items` (in the metrics' counters: a collecting route's, and a saved route's, which
   `read_route` carries from its file) equal `count_items` on the route, and a reported `brand`
   (a pilgrimage's, from the route or its file) is one of the build's brands and equals
   `brand_counts` on the route, `of` within `node_mask`, and a reported `alphabet` (an alphabet
   route's) has a `free_from` within the route and equals the validator's own recount up to it
   (`letters`, `sequence`, `roads`, `alpha_from`, `free_from`; it shares only `road_letter`,
   what a name's letter is), so a route.json whose counts were edited fails.
9. **`alphabet`**, only when the request's objective is the alphabet route (`alphabet=True`;
   every other route's report has the eight checks above): the route reports its letters, and
   its alphabet part, from its first edge of letter A to `free_from`, reads A, B, C, ..., Z
   then A again, the letters of its lettered edges counted once for equal ones in a row. It
   fails at the first road out of order, naming it and the letter wanted. Its detail says what
   it proved and that the first leg (before the first A road) and the last leg (from
   `free_from`) are free: they may carry any roads.

## Route files

**`route.json`** (`route_document`; `worst-route plan --out`) holds:

- `graph_version`, `region`, `build_id`, `seed`, `engine_version`;
- `request`, the `RouteRequest` as JSON (its `constraints` and its `brand` only when it has
  some, the constraints' `hard` and `reorder` only when set; with `reorder`, its stops in the
  order the route visits them);
- `stops`: `[{lat, lon, node, moved_m, note}]`, the snapped node's position;
- `order`: each stop's position among those asked for, in the order visited (the identity
  without `reorder`; a file from before the worst order has none, read as the identity);
- `nodes`, `edges`, `stop_idx`;
- `metrics`: `{length_m, time_s, delay_s, baseline_length_m, baseline_time_s,
  baseline_delay_s, ratio, turns: {left, right, straight, u_turn}, items: {toll_booth,
  level_crossing, roundabout, bridge}}` (`ratio` is null for a round trip; `delay_s` and
  `baseline_delay_s` the waiting at signals and stop signs, which `time_s` and
  `baseline_time_s`, the driving, leave out; `turns` by **Turns**; `items` the route's
  collectibles by `count_items`, whatever the mode), plus the objective's counters (a
  collecting route's `item`; a pilgrimage's `brand`, `{id, name, visits, of}`; an alphabet
  route's `alphabet`, `{letters, sequence, roads, alpha_from, free_from}`), and for a ratio
  or duration target
  `target: {kind, value, achieved, within_tolerance}` (`Goal.describe`: the target, the route's
  ratio or its experienced time in seconds, and whether that is within ±2 %);
- `run`: `{attempts, accepted, stopped_because, elapsed_s, backend}` (`backend` is `"csr"` or
  `"python"`; every backend gives the same route), and for a certified route `certified:
  {status, bound_m, gap, nodes, edges, leash_m, solve_s, workers}`, its `Certificate`
  (**Certified worst**), `stopped_because` then `"optimal"` when proven.

`route_document(data, result)` builds it from a plan's result through `document(data, request,
snaps, route, baseline, *, run, target=None)`, which takes the run's numbers as they are and a
target's description, for a route that no plan made.

`worst-route plan --out` writes it with the presentation's fields
(`docs/reference/presentation.md`, **The document**): `directions` (the turn-by-turn steps,
jokes included) and `toasts` beside the keys above, and in `metrics` `extra_fuel_gal` and
`extra_cost_usd` (null: the CLI has no vehicle), `states`, `towns` and `towns_n` (null without a
places index under `WORST_ROUTE_DATA`) and `network_fraction`. `route_document` itself, which
the API's worker stores, leaves them out; nothing reads them back.

`read_route(path)` returns a `SavedRoute` (with the file's `order`), which the validator reads
like a `Route` (its
metrics' counters carry the file's `items`, `brand` and `alphabet`, which the validator
recounts; the keys
it does not read, the presentation's among them, are kept in its `document`). A file that is
not one raises `KeyError`, `TypeError`, `ValueError`, `OverflowError` (an id beyond
int32) or pydantic's `ValidationError`.

A route's GeoJSON (`plan --geojson`) is the presentation's (`docs/reference/presentation.md`,
**GeoJSON**).

## The CLI

```
worst-route plan --region R [--build-id ID] --from LAT,LON --to LAT,LON [--via LAT,LON]...
                 [--seed N] [--budget SECONDS] [--max-iterations N] [--leash-km X|auto]
                 [--tier auto|T0..T4] [--target max|ratio:X|duration:TIME]
                 [--objective distance|time|collect|brand|alphabet] [--item ITEM]
                 [--brand ID|NAME]
                 [--no-left | --no-right]
                 [--strict] [--hard] [--reorder] [--certified]
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
pipeline version wrote is rebuilt ("... was written by pipeline 2 (now 6): rebuild the region
with `worst-route pipeline filter --region R` and the steps after it"), one of this version is
saved again ("... lacks the array node_roundabouts: rebuild it with `worst-route pipeline save
--force --region R`"); `validate` says the same. With `--geojson`, or `--out` and a places
index, a build without its geometry is one line too, before the search ("... lacks its geometry
(geom_indptr): rebuild it with `worst-route pipeline save --force --region R`"). Without `--seed` it draws a random seed, which it
prints and records. While it searches it writes `iteration N: ratio Rx, L km`
to stderr on an accepted splice (`restart N: K letters, ratio Rx, L km` on an alphabet route's
better route; `solution N: ratio Rx, L km, the worst possible at most G % longer` on a certified
route's, and `T s: the worst possible at most G % longer (B km)` on its bound between them), at
most one line every 2 s. On stdout it prints:

- the build;
- the tier, leash (to two decimals under 1 km: `leash 0.42 km`), seed and budget (a certified
  run's in seconds only: `max_iterations` does not apply to it);
- the objective and the constraints, when not the default: `objective: time, few left turns`
  (`no left turns` with `--strict`; `, hard mode` with `--hard`);
- with `--reorder`, the order the stops are visited in, each stop's place among those given:
  `order: 0, 2, 1, 3, 4 (each stop's place among those given, in the order visited)`;
- each stop, in the order visited, with its node, position, how far it moved, and the note;
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
- for a pilgrimage, its visits, the area's locations, the brand's name and id:
  `visited: 17 of 19 Starbucks (brand 229)`;
- for an alphabet route, its letters and the first five roads: `letters: 9 (A–I): Southeast
  Ankeny Street, East Burnside Street, Northeast Couch Street, Northeast Davis Street, Northeast
  Everett Street, …` (`letters: 0` without one; past Z, each lap: `A–Z, A–D`);
- the node and edge counts;
- the attempts, accepted splices, elapsed time and why the search stopped;
- for a certified route, its certificate: `certified: optimal: no legal route in this area is
  longer (283 nodes, 591 edges, leash 610 m, 0.5 s, 4 threads)`, or `certified: feasible: the
  longest found; the worst possible is at most 4.66 % longer (16.79 km) (...)`, or `certified:
  feasible: the longest found; no bound yet (...)` when the solver ended without one;
- the backend it searched with (`backend: csr`);
- the validator's checks.

It writes the files it is asked for. A stop that cannot be snapped or reached is one line, `cannot
plan: ` and the error (for an unreachable stop, the leg and the stops it moved), and exit 1; under
`--strict`, "cannot plan: No route from stop 1 to stop 2 without repeating a road and with no left
turns, after moving stop 2 up to 10 times. Try without --strict.", and when the seed finds no way
under hard mode but does without it, "cannot plan: No route from stop 1 to stop 2 without repeating
a road and with no road twice, after moving stop 2 up to 10 times. Try without --hard.". A route that fails
the validator is reported as an engine bug, exit 1, and never printed. A bad argument is exit 2:
`--no-left` with `--no-right`, an unknown objective, and `--strict` without `--no-left` or
`--no-right` (one line, "--strict needs --no-left or --no-right").

**`--objective`** is `distance` (the default), `time`, `collect`, `brand` (**Objectives**) or
`alphabet` (**Alphabet**), which takes `--from` and `--to` only: `--via` is exit 2 ("invalid
--via: the alphabet route takes a start and an end"), and so is a target other than `max`
("invalid --target: the alphabet route takes no target: it counts letters") and a `--base` file
of an alphabet route ("invalid --base: the alphabet route cannot be continued");
**`--no-left`** and **`--no-right`** make each such turn cost a kilometre of route,
**`--strict`** allows none, and **`--hard`** keeps each road in one stretch (**Constraints**).

**`--reorder`** visits the stops between `--from` and `--to` in the worst order (**Seed and
baseline**, **The worst order**); route.json records the request with its stops in that order
and `order`, and `validate` re-derives the stops from that request. More stops than the build
takes is one line and exit 2 (`invalid --reorder: the worst order takes at most 7 stops, not
8`, or on a build of more than 100,000 nodes `... at most 5 stops on us-t3, not 6`), and so is
`--reorder` with `--objective alphabet` (`invalid --reorder: the alphabet route takes no worst
order: it has no stop to reorder`).

**`--certified`** plans the certified worst (**Certified worst**), the leash the widest a
certificate allows unless `--leash-km` gives one. What it does not take is one line and exit 2
(`invalid --certified: a certified route is the longest: the distance objective only`; a target,
a turn constraint, hard mode, `--reorder`, a round trip, `--base`), and an area over the caps one
line and exit 1 (`cannot certify: the area within a 1 km leash holds ...`). route.json records the
leash used and `run.certified`.

**`--target`** is `max` (the default), `ratio:<x>` (× the baseline, more than 1) or
`duration:<time>`, where a time is `9h`, `90m`, `2h30m`, `45s` or a number of seconds (at most
7 days). A target it cannot read or one out of those bounds is exit 2, and so is a ratio target
for a round trip, which prints `invalid --target: a round trip has no ratio target`.

**`--objective collect`** needs **`--item`**: `toll_booth`, `level_crossing`, `roundabout` or
`bridge` (**Objectives**). An objective it does
not know is exit 2 with one plain line, checked before the item, as the API checks it:
`invalid --objective: unknown objective 'bogus'; known: distance, time, collect, brand, alphabet`. So is a
misfit (`objective_problem`): `invalid --item: the collect objective needs an item: toll_booth,
level_crossing, roundabout, bridge`, `invalid --item: only the collect objective takes an
item`, `invalid --target: the collect objective takes no ratio target: it counts items, not
metres`. An item it does not know is exit 2 too.

**`--objective brand`** needs **`--brand`**, resolved against the build's brand index (`GET
/brands` lists it) once the build is loaded: `#N` is the brand whose id is N; `"NAME"`, in
double quotes (`--brand '"76"'` in a shell), the brand named NAME, in any case; anything else
the brand of that name, in any case, or else, when it is a number (decimal digits only), the
brand of that id. A number that is one brand's name and another's id is refused rather than
guessed: on Portland's build ffe2e7c881ca, `--brand 76` gives `invalid --brand: '76' names brand
2 (76) and is also the id of brand 76 (DQ Grill & Chill): give the name in quotes ('"76"') or
the id with # ('#76')`. Each refusal is one plain line and exit 2: the misfits (`invalid
--brand: the brand objective needs a brand`, `invalid --brand: only the brand objective takes a
brand`, `invalid --target: the brand objective takes no ratio target: it counts visits, not
metres`), a name no brand has (`invalid --brand: no brand named 'Starbuck' in this build`, and
so `--brand ²`, a digit but not a decimal one), a name several brands share (on that build,
`invalid --brand: 'shell' names 2 brands: 220 Shell (Q110716465), 221 Shell (Q154950); give its
id`), a `#` without a number (`invalid --brand: '#x' is not a brand id: # takes a number`), an
id the build does not have (`invalid --brand: unknown brand 999`) and a brand none of whose
locations the request's area holds (`invalid --brand: no Starbucks within reach`).

**`--base route.json`** continues a saved route, as the API's `base_route_id` does ("make it
worse", toward the new target, with the new seed and budget). The stops (in the order its
route visits them, so a route planned with `--reorder` is continued in its worst order, without
searching again), tier, leash, objective, item, brand and constraints come from the file;
`--from`, `--to`, `--via`, `--reorder`, `--tier` or `--leash-km` beside it is exit 2 ("--base
takes the stops, tier and leash from its route: drop …"), `--objective`, `--item`, `--brand`, `--no-left`, `--no-right`, `--strict` or `--hard`
too ("--base takes the objective, item, brand and constraints from its route: drop …"), and so
is a `--base` path that does not exist. The build is the file's (`--build-id` names another); a
file it cannot read, one made by another engine version, on another region or on another
`graph_version`, is one line and exit 1, and so is a base `check_base` refuses (`cannot plan:
the base route's stops are not the ones the request resolves to`) or whose route is not legal.
The route.json it writes records the new request; `validate` checks it like any other, and
replaying it needs the base file.

**`validate`** reads a `route.json` and loads the build it names (or `--region` /
`--build-id`). A file it cannot read (a missing field, an id beyond int32) is `cannot read …`
and exit 1, and so is a file made by another engine version (`… was made by engine version 1;
this is version 3: re-plan`): its stops are re-derived with this engine's search, which could
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

**Brands on Portland** (build ffe2e7c881ca at `graph_version` 8867636799a6, pipeline version 3,
engine version 2, `CsrBackend`, 2026-10-30, load average 18–22 while other lanes ran; Powell's →
Reed; every route validates, `worst-route validate` too). The request's area (leash 3.6 km, T4)
holds 19 of Starbucks' 126 locations and 104 of the Postal Service's 174, the brand with the
most.

| Request | Visits | Length |
|---|---|---|
| distance, 20,000 attempts, seeds 1, 2 | Starbucks 15, 11; Postal Service 71, 73 | 51.46×, 51.69× |
| `--objective brand --brand Starbucks`, 20,000 attempts, seeds 1, 2 | 17, 17 of 19 | 54.72×, 51.15× |
| `--objective brand --brand 261` (the Postal Service), 20,000 attempts, seeds 1, 2 | 84, 85 of 104 | 48.57×, 52.27× |
| `--brand Starbucks`, seed 1, `--budget 30` | 17 of 19 | 61.90× (47,551 attempts) |
| `--brand 261`, seed 1, `--budget 30` | 84 of 104 | 49.83× (24,135 attempts) |
| `--brand Starbucks --target duration:3h`, seed 1 | 7 of 19 | 2 h 58 min experienced (within ±2 %), 8.93×, after 44 attempts |

A pilgrimage gives up little or no length for its visits; the distance route already passes most
of them.

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

This table and the paragraph below predate engine version 4, under which strict no-left plans
Powell's → Reed and 7 of the 20 acceptance pairs (**Constraints**).

On the 20 acceptance pairs (pair seed 2026, seed 1, 30 s each, the same build): soft no-left
plans all 20 at a median of 5.33× (2.54–11.04×, 0.54 left turns and U-turns a km), soft
no-right all 20 at 6.47× (2.36–13.89×); strict no-left plans 4 (2.40×, 1.42×, 1.04×, 1.00×; 16
fail at the seed), strict no-right 3 (1.59×, 1.49×, 1.21×); every route validates. No strict
route turns the refused way by the roads' own geometry: the review's probe, which takes one
stub off each merged end, flags one turn (strict no-right, pair 12, at an interchange), where
the OSM nodes' positions show two stubs and a straight road (180° in, 206° out). On build
e4b3b4ad90db, whose bearings included the stubs, the soft medians were 6.88× and 5.65× and
strict planned 3 and 1, one of them turning left by the road at a stage A cluster.

**Hard mode on Portland** (build 506f747d7458, `graph_version` ae1ce1e8289b, and again on build
ffe2e7c881ca, `graph_version` 8867636799a6, at pipeline version 3, which gives the same routes;
`CsrBackend`, 2026-10-30, with other lanes running beside it (load average 10–22), so no time
here compares with those above; Powell's → Reed with `worst-route plan --hard --budget 30
--max-iterations 1000000`; every route validates, `worst-route validate` too):

| Request | Result |
|---|---|
| `--hard`, seeds 1–3 | 9.68×, 11.97×, 11.15× (74.70–92.36 km), 223, 292 and 273 roads each in one stretch (6–26 unnamed edges), converged after 26,710, 19,909 and 10,972 attempts (93–118 splices) in 3–10 s; with the CLI's default 5,000 attempts, seed 1 stops at 7.26× in 1.3 s |
| `--hard --no-left`, seed 1 | 6.96× (53.65 km), 36 left turns of 622 and no U-turn, converged after 12,065 attempts |
| `--hard --no-left --strict` | one line, exit 1, the strict constraint blamed (hard mode alone plans the trip): "cannot plan: no route from stop 0 to stop 1 without repeating a road, after moving stop 1 up to 10 times, under the strict no-left constraint; try without --strict" |

On the 20 acceptance pairs (pair seed 2026, seed 1, 30 s each, attempts unlimited), 19 plan
under hard mode at a median of 4.73× (1.23–8.26×), every one converged (in 1.2–15 s) and
valid. The 20th (11.0 km) is refused as `InfeasibleHardMode` after moving its end 10 times: the
seed found no way that does not leave NW Walker Road and come back to it (whether one exists, it
cannot tell). The plain seeds of 8 of the pairs come back to a road (one or two each); the seed
re-solve makes all but that one legal. On build
ffe2e7c881ca the same, but that at a load of 22 pair 3 ran out of its 30 s at 4.14× (17,326
attempts, a prefix of the run that converges at 4.23×), and Powell's → Reed's seed 1 at 9.33×
(24,121 attempts; with `--budget 120` it converges on the same 9.68× route). Before the
window was widened (**Constraints**), a prototype gave Powell's → Reed 6.4× at seed 1.

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

Under `collect` and `brand` a turn costs the same kilometre (`TURN_PENALTY_M`), against an
item's or a visit's 10⁶: a detour that gains an item takes its turns with it, one that gains only
length takes a turn only for more than a kilometre. It still bites, on the turns a kilometre of
route rather than on the side they turn to. Powell's → Reed under `--no-right`, seed 1, 20,000
attempts (`CsrBackend`, build ffe2e7c881ca, 2026-10-30):

| Request | No constraint | Soft no-right, a kilometre a turn |
|---|---|---|
| `--objective collect --item roundabout` | 53.11×, 44 rings; 2.96 right and 2.93 left turns a km | 16.08×, 43 rings; 1.33 right and 1.43 left turns a km |
| `--objective brand --brand Starbucks` | 54.72×, 17 of the 19 Starbucks in reach; 3.05 right, 3.06 left a km | 18.49×, 18 of 19; 1.12 right, 1.17 left a km |

The constraint about halves the density of the turns it avoids and keeps the items, but it does
not make them rarer than the other side's. A route that never repeats a node tends to turn left
about as often as right: its turns add up to its net change of heading, which stays small unless
the route spirals (a tendency, not a law). So no penalty makes one side's turns scarce while the
route stays long. Charging a turn a fraction of an item instead was tried at the review and
bought nothing: at a tenth of an item the densities were the same (1.26 and 1.30 right turns a
km) with the same items on shorter routes, and at a quarter or half an item the routes lost
items (42 or 28 of 44 rings, 13 or 9 of 17 Starbucks).

**The alphabet route on Portland** (build ffe2e7c881ca, `graph_version` 8867636799a6, engine
version 2, `CsrBackend`, 2026-10-30, seed 1, 30 s, attempts unlimited; load average about 20
while other lanes ran; every route validates with its `alphabet` check, `worst-route validate`
too):

| Trip | Result |
|---|---|
| Powell's → Reed (`worst-route plan --objective alphabet --budget 30 --max-iterations 1000000`) | 13 letters, A to M: SW Alder Street, W Burnside Street, NW Couch Street, NW Davis Street, …, NW Marshall Street, the free first leg 5 edges; 17.82 km, 2.31× (2.85× the experienced time); converged after 1,288 restarts, 13.3 s |
| Powell's → PDX (the same) | 23 letters, A to W, the NW Alphabet District from SW Alder Street to NW Wilson Street; 27.25 km, 1.94×; 864 restarts in its 30 s (1,220 in an earlier run, with the same letters) |
| the 20 acceptance pairs (pair seed 2026) | all plan and validate; letters 1 on 12 pairs, 2 on 4, then 4, 5, 7 and 9: a median of 1; ratios 1.00–3.20×; all converged, in 1.3–15.5 s |
| Powell's → Reed, 100 restarts (`test_portland_data.py`) | 9 letters, A to I, SE Ankeny Street to NE Irving Street, the same on both backends |

Before the rule had a free first leg and connectors over the roads of the letter just completed
(the first leg had to reach an A road over free connectors, and a connector could drive only
free edges and the current road; patience 100, 10 s), 18 of these 22 trips got no letter, and
Powell's → Reed none: Powell's node leaves on Burnside (B) and on NW 6th Avenue northbound,
whose free search reached 8 edges and no A road. With the free first leg alone, all 22 got at
least one, at a median of 1; with the letter just completed as a connector too (the rule as
built), Powell's → Reed reached 13 and Powell's → PDX 15 at that patience.

The table above was measured before `road_letter` kept a directional word only before a single
street type ("Southwest Parkway Avenue" had been S, now P; 10 of Portland's names changed
letter). Run again after it (2026-10-31, load average over 30): every one of the 20 pairs
converges on the same route, letters and restart count, and the two named trips, planned with
the restarts they ran (1,288 and 864), give the same route.json edges; so they do again on top
of hard mode's series (2026-10-31), which changes no alphabet route.

**On Oregon** (M5.1; build 5913ade7a2df at `graph_version` 73b137860fc2, pipeline version 3,
`docs/reference/pipeline.md`, **Oregon**; `CsrBackend`; `worst-route plan --seed 1 --budget 30
--max-iterations 1000000`; 2026-10-31, other lanes running, load average 15–40, so a rate compares
only within a table; every route validates, `worst-route validate` too). The named trips run from
Pioneer Courthouse Square (45.5189, −122.6793) to Bend (44.0582, −121.3153), Astoria (46.1879,
−123.8313) to Ontario (44.0266, −116.9629), Eugene (44.0521, −123.0868) to the square, and Medford
(42.3265, −122.8756) to the square. With engine version 2's automatic tiers (load 36–40):
Portland → Bend on T2, 2.03×; Astoria → Ontario on T1, 1.17×, converged; Eugene → Portland on
T2, 3.12×; Medford → Portland on T1, refused (below). Forced on each tier (`--tier`, load 15–33;
"budget" or "converged", how the run stopped):

| Trip, straight line | T1 | T2 | T3 | T4 |
|---|---|---|---|---|
| Portland → Bend, 195 km | 2.17×, budget | 5.31×, converged (5.16× and 5.18× at load 34–40) | 3.77×, budget (3.74× twice) | 9.26×, budget |
| Astoria → Ontario, 592 km | 1.17×, converged | 1.32×, converged | 3.35×, converged | 3.56×, budget |
| Eugene → Portland, 166 km | refused | 3.28×, budget | 6.73×, budget | 10.99×, budget |
| Medford → Portland, 355 km | refused | 4.26×, budget | 5.37×, budget | 7.59×, budget |

Ten random pairs 71–403 km apart (`tools/acceptance.py`'s draw, pair seed 2026, 60–650 km)
planned 4 of 10 on T2, 7 on T3 (median 5.16×) and 8 on T4 (6.10–10.85×). The sparser tiers'
networks hold few loops a detour can take without repeating a junction, so their runs converge
early, and a rural stop's 3 km bubble seldom meets their roads (a stop in the Ochoco National
Forest, 44.43, −120.35, lies on a tertiary road but 7.1 km from the nearest T1 or T2 node: on T2
the seed cannot leave its bubble). So the automatic tier is T4 below 800 km (engine version 3,
**Requests**). With it (load about 19):

| Trip | Baseline | Worst | Ratio | Attempts, rate | Route |
|---|---|---|---|---|---|
| Portland → Bend (T4, leash 116.9 km) | 256.87 km | 2,689.84 km | 10.47× | 10,386, 346/s | 11,618 nodes |
| Astoria → Ontario (leash 150 km) | 751.90 km | 3,027.09 km | 4.03× | 10,749, 358/s | 10,161 nodes |
| Eugene → Portland (leash 99.7 km) | 181.26 km | 2,537.91 km | 14.00× | 9,808, 327/s | 12,013 nodes |
| Medford → Portland (leash 150 km) | 443.99 km | 3,626.45 km | 8.17× | 5,981, 199/s | 11,610 nodes |

Every run stopped on its budget; the `plan` process peaked at 375–498 MB, and `worst-route
validate` of the Astoria → Ontario route takes 1.3 s. Astoria → Ontario stays the least bad: the
route must cross the Cascades in the Columbia Gorge, where no detour exists. `tools/acceptance.py
--region oregon --pairs 10` (3–15 km pairs, which T4 planned before too) plans 9 of 10, the
validator passing all 9, median 2.82× (1.00–8.86×), median 2,398 attempts/s, every run converged
in 0.3–13.8 s, Jaccard of seeds 1 and 2 0.11–0.39 on five pairs; the tenth, two points 8.8 km
apart but 32.4 km by road, does not fit its 5.3 km leash (it plans with `--leash-km 10`). With
`--min-km 60 --max-km 650 --jaccard 0` it plans 8 of 10, the validator passing all 8, median
8.88× (6.47–12.74×), median 557 attempts/s, every run on its budget. A stop with no node within
5 km is refused ("no node within 5000 m of (44.1, -121.77) can be an end stop", in the Three
Sisters Wilderness).

The refusals left are the transition-aware search's (**Limitations**, **Transitions**), each a
super-junction the cheapest legal walk enters twice: Medford → Portland and Eugene → Portland on
T1 at the Woodburn interchange (I-5 and OR 214/OR 219, node 236835), the random pairs from near
Melrose (west of Roseburg) to near Warren (by St. Helens) and from near Rose Lodge to near
Sisters on T4 at nodes 236640 (McKay Road
and French Prairie Road by I-5 near Donald) and 236164 (Valley Junction, OR 18 and OR 22). At
Woodburn the turn table is wrong: stage B made the interchange three super-junctions, the OR 214
side (236835) and the OR 219 side (236639), whose ramps share only I-5 (the Newberg Highway
changes its ref at the bridge), and an east one (236678). Northbound I-5, one OSM way
(1186652782) with no turn restriction, enters 236835 at its off-ramp's gore, passes 236639 at its
on-ramp's merge and comes back to 236835 at its own on-ramp's merge; the direct pass (edge 590380
to 590995) is not in 236835's table, since no internal path joins those two members, and the
stretch between is no pocket, running through 236639's member. So no route drives north
straight through Woodburn on I-5 (southbound passes 236639 twice too, but its direct pass is in
the table, through the off-ramp, the Newberg Highway and the on-ramp). A legal node-simple path
exists, 455.0 km by OR 99E against the walk's 454.7 km, which the search did not look for
before engine version 4 (**Searching again**, below).
Statewide, roads that leave a super-junction and come back to it with the pass not in its table
number 20 at 19 super-junctions on that build, Portland's 9 at 9, by `lost_through`'s count
(`tests/pipeline/invariants.py`: every road class, every branch, back within four edges; the
first count, of motorway, trunk and primary roads down one branch, had given 11 at 9 and 5 at
4). The I-5 junction near Salem (237073) the turn-blind path also crosses is right: straight
through is allowed both ways, and what the blind path did there, northbound I-5 onto the Salem
Parkway's on-ramp, cannot be driven.

**At pipeline version 5** stage B joins the clusters a carriageway leaves and comes back to, and
its pockets follow a road by its name as well as its refs (`docs/reference/pipeline.md`,
**cleanup**): Woodburn's halves are one interchange, through which northbound I-5 and the Newberg
Highway both ways each pass once. On the rebuilt build (ad1ae3f57d81, `graph_version`
089647c53fa2, from the 2026-10-29 extract that replaced the 2026-10-28 one; engine version 3,
2026-10-31, load average about 17; seed 1, `--budget 30`), every route validating:

| Trip | Before (build 5913ade7a2df, 2026-10-28) | At version 5 |
|---|---|---|
| Medford → Portland, `--tier T1` | refused | 2.01× (454.66 km baseline), budget |
| Eugene → Portland, `--tier T1` | refused | 4.74× (182.89 km), budget |
| Medford → Portland (T4) | 8.17× | 6.03× (443.40 km), budget, 4,111 attempts |
| Eugene → Portland (T4) | 14.00× | 12.02× (180.80 km), budget |
| near Rose Lodge → near Sisters (T4) | refused at Valley Junction | 2.70× (258.72 km), budget |
| near Melrose → near Warren (T4) | refused at McKay Road / French Prairie Road | refused |

Every run that plans stops on its budget, so its ratio depends on how many attempts its 30 s
allow; on the version 5 build made before the review's fold (97dc06067f0c, from the 2026-10-28
extract) the same three T4 trips made 9.20×, 14.68× and 12.77×.

Valley Junction's OR 18 and "OR 18;OR 22" are one road by name (Salmon River Highway), which the
pockets now follow. The pair at 236640 stayed refused: there the table forbids every turn from
French Prairie Road onto McKay Road inside one super-junction, a cross turn rather than a road
leaving and coming back, like NE 42nd Avenue onto NE Sandy Boulevard at I-84 and NE Halsey in
Portland (`docs/project_overview.md` §2.3), and the seed's cheapest legal walk came back
through it.

**Searching again** (engine version 4, **Searches**): on Oregon's version 6 build (e78224b5cf9a,
`graph_version` ec7360404031; 2026-10-31, `CsrBackend`, seed 1, `--budget 30 --max-iterations
1000000`, the near Melrose → near Warren pair from (43.23416, −123.48214) to (45.88307,
−122.93844); load average 17–22 as other lanes ran, so every run stopped on its budget and the
attempts compare only roughly), engine version 3 (main at a61bd77) against version 4, every route
validating:

| Trip | Engine version 3 | Engine version 4 |
|---|---|---|
| near Melrose → near Warren (T4) | refused | 5.19× (353.81 km baseline), 7,091 attempts |
| Medford → Portland, `--tier T1` | 1.92× (454.66 km), 20,496 attempts | 2.54×, 45,990 attempts |
| Medford → Portland (T4) | 6.24× (443.40 km), 5,306 attempts | 5.91×, 3,879 attempts |
| Eugene → Portland, `--tier T1` | 4.82× (182.89 km), 41,757 attempts | 4.89×, 44,966 attempts |
| Eugene → Portland (T4) | 11.93× (180.80 km), 7,052 attempts | 13.99×, 9,938 attempts |

Near Melrose → near Warren plans with its stops where the snap put them: the seed's leg search
meets a walk that enters a node twice and finds a node-simple leg one round later, as the seed
built again in `plan` does; the baseline, which does not search again, is the turn-blind path.
At a fixed 5,000 attempts (`--max-iterations 5000`, two runs each, alternated), Eugene → Portland
on T4 takes 18.1–18.3 s of CPU at version 3 and 14.2–15.1 s at version 4, on another route
(11.21× against 12.74×). In those 5,000 attempts, 14 of the 5,082 transition-aware detour
searches met a walk that loops (0.3%); searching again found a node-simple path for 13 (10 after
one round, 2 after two, 1 after three) and no walk for the last; with their retries they took
1.45 s, against 0.45 s for the 14 such searches of the version 3 run, which gave up at once. The
same seed gives the same route twice (near Melrose → near Warren, 2,000 attempts: 3.80×), and
both backends the same route (500 attempts). The same fixed-attempt runs on the version 5 build
(ad1ae3f57d81) gave the same routes (11.21×, 12.74×, 3.80×).
On Portland's version 6 build (3a4a3df6269f) the acceptance pairs plan as before, 20 of 20 at a
median of 21.22×, the validator passing all; the second look changed four of their routes
(**Limitations**, Transitions).

**On Oregon and Washington** (M5.3; build 6fdd57c305fe at `graph_version` bc958387324a,
pipeline version 5, engine version 3, `docs/reference/pipeline.md`, **Oregon and Washington**;
`CsrBackend`;
`worst-route plan --seed 1 --budget 30 --max-iterations 1000000`, each route validated by `plan`
and again by `worst-route validate`; load average about 8). Trips across the Columbia, which a
build of Oregon alone cuts off at the river:

| Trip | Baseline | Worst | Ratio | Time ratio | Attempts | Peak memory |
|---|---|---|---|---|---|---|
| Pioneer Courthouse Square → Seattle (47.6062, −122.3321), T4, leash 140.1 km | 284.30 km | 3,107.67 km | 10.93× | 13.41× | 3,337 (162 accepted), budget | 0.73 GB |
| Esther Short Park, Vancouver WA (45.6270, −122.6727) → Salem (44.9429, −123.0351), leash 48.7 km | 90.01 km | 1,187.42 km | 13.19× | 18.87× | 12,304 (515), budget | 0.56 GB |

A request's masks on its 646,338 nodes (median of five): Vancouver → Salem 54 ms, Portland →
Seattle 104 ms, Astoria → Spokane (514 km, T4, leash 150 km) 153 ms, Brookings → Spokane (825
km, T0) 151 ms, nearly all of it the leash, about 0.3 µs a node inside the leash's bounding box;
the spatial index builds in 0.14 s (+20 MB).

**On the national T1 build** (M5.3; build c4a85b3d36d0 at `graph_version` 8846d0d4ab7a,
pipeline version 7, engine version 4, `docs/reference/pipeline.md`, **The national T1 build**:
887,737 nodes, 1,324,013 edges, primary roads and above only; `CsrBackend`; seed 1, 60 s,
`--max-iterations 1000000`; load average 5–8). Its spatial index builds in 0.20 s (+54 MB), and a
request's masks take 12–138 ms (median of five), all of it the leash: Powell's → Reed 12 ms,
Portland → Chicago 37 ms, Chicago → Miami 79 ms, New York → Los Angeles 99 ms, Miami → Seattle
138 ms (its leash's bounding box holds the whole build). The three long trips at the automatic
tier (T0, the motorways and trunks, beyond 800 km, with a 150 km leash) are refused in about 4 s
("no route from stop 0 to stop 1 without repeating a road, after moving stop 1 up to 10 times"):
from Portland to Chicago and from Miami to Seattle no motorway or trunk route stays within 150 km
of the straight line (the end is unreachable in the request's masks; without the leash it is),
and from New York to Los Angeles it is reachable within the masks (95,623 nodes) but the seed's
transition-aware search finds no legal path. On the whole T1 network (`--tier T1`):

| Trip | Baseline | Worst | Ratio | Attempts | Stopped | Peak memory |
|---|---|---|---|---|---|---|
| Pioneer Courthouse Square → Chicago (41.8781, −87.6298) | 3,507.79 km | 3,892.95 km | 1.11× | 3,974 (5 accepted) | converged in 3.4 s | 0.68 GB |
| New York (40.7128, −74.0060) → Los Angeles (34.0522, −118.2437) | 4,513.45 km | 5,675.45 km | 1.26× | 8,356 (17) | converged in 7.8 s | 0.88 GB |
| Miami (25.7617, −80.1918) → Seattle | refused: the end is unreachable within the 150 km leash on T1 too | | | | | |

Of the places index's 31,540 towns (`docs/reference/presentation.md`, **Places**), 25,793
(81.8%; incorporated places 83.7%) have a node a trip can start from within 5 km of a point
inside them, the snap moving a median 782 m (p90 3.1 km); the rest are refused ("no node within
5000 m … can be a start stop"), most in the plains (North Dakota 52%, Nebraska 54%, South Dakota
55%), least in the Northeast (96–97%). Primary roads and above within 150 km of a straight line
give few node-disjoint detours, so the runs converge in seconds; both routes validate (`worst-route validate`, 0.8–1.1 s). Served
(`serve --port 0 --geocoder none --workers 1`), the API process holds 549 MB idle (the build
mapped, its spatial index, the national places index) and 1.2 GB after a route document and a
share image; a job's worker peaked at 694 MB. Chicago → Miami on T1, a 4,168 km route (1.87×,
converged): `GET /routes/{id}` 0.18 s (0.53 MB), `GET /routes/{id}/share.png` 3.87 s on its first
render (0.41 MB; it drew every T1 road in the route's bounding box, which the drawing now caps:
`docs/reference/presentation.md`, **Share image**), then cached.

**Long trips** (engine version 5, the long trips brief, 2026-10-31; the same build; `CsrBackend`;
seed 1, `--budget 60 --max-iterations 1000000`; other runs beside them, load average 3–12, so a
rate compares only within a table). Why engine version 4 refused at its automatic tier (T0, a
150 km leash), from its searches instrumented: Portland → Chicago (2,826 km straight) for the
leash, no motorway or trunk path lying within 150 km of the line (3,498 km without the leash);
New York → Los Angeles (3,913 km) for the seed's rounds, the end reachable in the masks (4,657 km
turn-blind) but the cheapest legal walk entering 13 super-junctions twice (Newark, I-78 near
Clinton NJ, the Pennsylvania Turnpike, I-70 and I-79 at Washington PA, Kansas City, Durango ...)
and driving 9 twin pairs, which the chain of rounds, dropping its bans at each twin ban, never
got through in any of the 21 builds the fallback makes (198 searches); Miami → Seattle (4,395
km) for the leash on T0 and T1, and on T0 at a tenth or a fifth of the trip for the rounds;
Chicago → Miami (1,920 km) planned, its end moved 75 m, the rounds' bans cutting off every walk
at the snapped one. Planned at T0 and T1 with a leash of 150 km, a tenth and a fifth of the trip
(the plan's runs, the seed by the tree, the windows widened by the plan's prototype, which
counted neighbours over the whole build), the trips made (ratio; "conv" converged, else the
budget stopped the run):

| Trip | T0, 150 km | T0, a tenth | T0, a fifth | T1, 150 km | T1, a tenth | T1, a fifth |
|---|---|---|---|---|---|---|
| Portland → Chicago | refused | 1.02× conv | 2.78× conv | 3.36× conv | 4.87× | 8.55× conv |
| New York → Los Angeles | 1.20× conv | 2.17× | 2.75× | 4.26× | 8.56× | 10.14× |
| Chicago → Miami | 1.44× conv | 1.36× conv | 1.99× conv | 6.31× | 7.01× conv | 11.06× |
| Miami → Seattle | refused | 1.07× conv | 2.43× conv | refused | 5.23× | 9.87× |

With engine version 4's windows every one of them converged in 1–47 s at 1.00–2.66×. So the
automatic tier is T1 from 800 km and the leash a fifth of the trip from 750 km (**Requests**).
At those settings, as built:

| Trip | Leash | Baseline | Worst | Ratio | Time ratio | Attempts (accepted) | Stopped | Peak memory |
|---|---|---|---|---|---|---|---|---|
| Portland → Chicago | 565.2 km | 3,384.45 km | 28,946.48 km | 8.55× | 8.90× | 18,950 (106) | converged in 41.3 s | 0.56 GB |
| New York → Los Angeles | 782.6 km | 4,462.99 km | 27,623.80 km | 6.19× | 7.07× | 150 (10) | budget | 0.68 GB |
| Chicago → Miami | 384.0 km | 2,228.90 km | 22,204.72 km | 9.96× | 10.41× | 31,353 (158) | budget | 0.58 GB |
| Miami → Seattle | 879.0 km | 5,363.66 km | 55,009.38 km | 10.26× | 11.47× | 17,116 (116) | budget | 0.62 GB |

Every route validates, and again with `worst-route validate` (1.1–3.5 s). New York → Los Angeles
with seeds 2 and 3 makes 9.93× (5,105 attempts) and 11.21× (5,727): seed 1's run met attempts
at a 783 km radius that made dozens of continental searches each (**Limitations**, Continental
radii). Chicago → Miami's end still moves 75 m, the twin rule's (**Limitations**, Twins within
a leg). Portland → Chicago with `--max-iterations 500` gives the same route document on both
backends but for `run.elapsed_s` and `run.backend` (5.89×; 2.7 s on `csr`, 11.1 s on `python`).
A request's masks take 8–13 ms (median of five; the leash 4–5 ms of it). The seed's tree takes
3 searches a build for New York → Los Angeles at these settings and 9 for Miami → Seattle; on
T0, where the chain refused them, 31–33 and 19.

The national acceptance run, ten random pairs of the places index's towns 800–4,500 km apart
(`tools/acceptance.py --region us-t1 --places --pairs 10 --min-km 800 --max-km 4500 --near-m
5000 --budget 60 --seed 1 --jaccard 2`, the places index written by `worst-route aux places
--region us-t1`), plans 9 of 10, the validator passing all 9, at a median of 11.77× (4.33–14.70×)
on T1, 288–1,017 attempts a second, six runs stopping on the budget and three converging in
24–34 s; no transition refused a candidate or a pair; the second seed's routes' edges have a
Jaccard of 0.13 and 0.07 with the first's. The tenth, a town east of Jamestown, North Dakota (46.92378, −97.67424),
to one by Waterville, Maine (44.79999, −69.89532), 2,164 km apart, is refused for its leash:
the straight line crosses Lake Superior and Canada, which the build does not hold, and the
shortest path on the whole build (2,979 km) goes round Lake Michigan at Gary, Indiana, 579 km
from the line, past the 433 km leash; no turn-blind path lies inside it.

| # | From → to | km | Baseline km | Worst km | Ratio | Attempts / time |
|---|---|---|---|---|---|---|
| 00 | 26.93249, −98.09218 → 33.46989, −82.50108 | 1,662.6 | 2,000.45 | 14,680.21 | 7.34× | 33,487 / 33.9 s, converged |
| 01 | 34.90171, −114.57391 → 46.26091, −97.45724 | 1,912.9 | 2,515.20 | 10,900.96 | 4.33× | 32,050 / 31.5 s, converged |
| 02 | 39.07600, −76.96443 → 40.05087, −94.52548 | 1,496.9 | 1,697.19 | 24,737.24 | 14.58× | 53,172 / 60.0 s |
| 03 | 46.92378, −97.67424 → 44.79999, −69.89532 | 2,164.4 | – | – | refused (the leash) | – |
| 04 | 35.20556, −88.19773 → 37.10737, −113.48459 | 2,257.7 | 2,706.32 | 32,467.94 | 12.00× | 53,332 / 60.0 s |
| 05 | 44.19065, −121.18327 → 33.53858, −83.08126 | 3,456.9 | 4,158.68 | 43,496.83 | 10.46× | 17,279 / 60.0 s |
| 06 | 28.69317, −99.17826 → 41.11775, −80.98248 | 2,148.9 | 2,523.16 | 33,182.47 | 13.15× | 37,805 / 60.0 s |
| 07 | 34.55711, −117.95450 → 31.04044, −84.87916 | 3,088.1 | 3,506.79 | 41,260.81 | 11.77× | 26,934 / 60.0 s |
| 08 | 40.06306, −80.03141 → 37.39849, −102.61799 | 1,963.1 | 2,133.97 | 31,379.03 | 14.70× | 46,591 / 60.0 s |
| 09 | 36.11879, −86.43286 → 26.22868, −81.72796 | 1,186.2 | 1,364.50 | 8,224.21 | 6.03× | 22,025 / 23.6 s, converged |

## Limitations

- **Transitions.** The tables hold the super-junctions' passages and, since pipeline version 8,
  OSM's turn restrictions at the junctions they apply at (`docs/reference/pipeline.md`, **Turn
  restrictions**), whose cheapest legal way is often round a block or on and back, a walk that
  passes a node twice. On the national T3 build, sparse without its residential streets, that is
  the rule: New York → Los Angeles (seed 1, 30 s, profiled) made 32 attempts on the version 8
  build, 4.9 transition-aware searches to a `shortest_path` at about 200 ms each, where the
  version 7 build made 633 attempts, 1.4 searches to a `shortest_path` at about 27 ms; in 60 s,
  back to back, 3.81× against 5.88× (`docs/reference/pipeline.md`, **The national T3 build**),
  though a 60 s run there swings with the machine's load (the review saw 37 to about 2,000
  attempts on the version 8 build in one session). On Portland's
  denser streets the acceptance run (20 pairs, seed 1, 30 s, 2026-10-31) plans all 20 on the
  version 8 build 0b72253e5e8a, the validator passing all 20, at a median of 23.93× (6.10–47.72×)
  and 5,777 attempts/s, where the version 7 build a453e105582b, run after it in the same session,
  gives 24.30× (6.10–53.27×) at 5,366 attempts/s; no candidate refused for a transition, no pair
  unplanned. The searches see the tables (**Searches**), and a part of a
  detour whose cheapest legal walk passes a node twice is searched again, up to 8 times, with
  the edge banned by which the walk comes back to the node. That is a heuristic: it misses a
  node-simple legal path that drives a banned edge or needs more rounds (41 of 107 on the small
  towns of `test_search.py`), and need not find the cheapest one. A seed leg's tree over bans
  (engine version 5) misses one only when its searches run out (4 of the 107 with its 9 there),
  and need not find the cheapest one either; the seed then falls back as for an unreachable
  leg. On Portland's acceptance pairs (the version 6 build 3a4a3df6269f,
  2026-10-31), 40 of about a million transition-aware detour searches met a walk that loops: 15
  found a node-simple path (6 after one round, 8 after two, 1 after three), 25 no walk once the
  edge was banned; no seed leg met one. On Oregon's build at pipeline version 3 the walks that
  loop refused 2 of ten random 60–650 km pairs on T4 and two of the four named trips on T1, at
  super-junctions whose table forbade a through movement, before the search looked again; since
  version 5's stage B, one pair (a cross turn at McKay Road and French Prairie Road), which
  engine version 4 plans (**On Oregon**). Under a strict turn constraint walks that loop are
  common (three rights round a block), so a leg with no node-simple path runs all 9 searches in
  every search that fails, through each re-snap of the stops' fallback, and a refusal costs
  several times its old CPU: Medford → Portland under strict no-right on Oregon's version 6
  build (e78224b5cf9a) 11.2–14.1 s against 1.3–1.7 s, Powell's → Reed under strict no-right on
  Portland's (3a4a3df6269f) 0.52–0.68 s against 0.09–0.10 s, the refusals among the 20
  acceptance pairs a median of 0.19–0.21 s against 0.06–0.10 s (load average about 23). The
  rounds stay at 8. With the tree (engine version 5), whose searches grow with the leg, a
  refusal costs a little more again: Medford → Portland under strict no-right on Oregon's build
  ea3b18096e8b 9.1 s against 7.0 s, Powell's → Reed on Portland's a453e105582b 0.72–0.77 s
  against 0.75–0.92 s (the whole `plan` process; a fixed 64 searches had made the first 52 s).
- **Twins within a leg.** A leg driving an edge and its twin is searched again without the
  later one, within the tree's searches, never without the earlier one; a leg that needs more
  searches, or whose only way drives the later edge of a twin pair, falls back as an unreachable
  one. Trying the earlier one too planned one of Oregon's 25 trips at its snapped end, but on a
  531 km seed instead of the re-snapped end's 455 km, so it is not done.
- **Speed.** `CsrBackend`'s searches are 8–13× the reference's on large searches, but most of a
  run's searches are small: a whole run is about 1.2× faster on Portland (**On Portland with
  `CsrBackend`**) and 1.5–1.9× on a build without tables (**The loop**, Measured).
- **Continental radii.** A detour's search is bounded by 8 × the radius, which on a
  cross-country trip grows to the leash (783 km from New York to Los Angeles), and its chains of
  rounds (up to 9 twin rounds of 9 searches for each of the waypoint's two legs) can make
  dozens of such searches in one attempt: in a run of that trip the slowest attempt made 102
  searches in 6.96 s, the median one in 7 ms, and seed 1 made 150 attempts in its 60 s (6.19×)
  where seeds 2 and 3 made 5,105 and 5,727 (9.93×, 11.21×; **On the national T1 build**). The
  detours keep the chain, which the long trips brief left as it was.
- **Set-up per call.** A search allocates its work arrays and combines its node masks, O(n) and
  O(m) a call (`docs/reference/graph.md`, **The CSR backend**), and an attempt of the loop copies
  the route's masks, the waypoint's and the random weights, O(n) and O(m) too. On the national
  T1 build (887,737 nodes, 1,324,013 edges) a search to a neighbour costs 48 µs (`dijkstra`
  120 µs), an attempt's copies about 0.7 ms, against searches of about 100 ms at a continental
  radius; at 17 M nodes and 42 M edges the same operations would cost about 1.4–3 ms a search
  and 5–20 ms an attempt, which would bound a city trip's attempts on an all-roads national
  build. Reusing work arrays per backend is left to the all-roads engine brief, with the loop's
  copies; they would have to be per thread, since the API process runs `resolve_stops` in a
  thread pool. Two set-ups of engine version 5 grow with the build too: the leash measures every
  node, each request (`docs/reference/graph.md`, **Spatial**), its NumPy temporaries about 56 B a
  node at once (about 1 GB at 17 M nodes; a bounding-box prefilter ahead of it is the later
  fix), and `detour_ends`, once a run, about 61 B an edge (0.2–0.7 s on the national T1 build,
  0.46 s with the whole country active; about 2.6 GB at 42 M edges).
- **Time budgets.** A run that stops on time is reproducible only through its recorded attempt
  count.
- **Annealing** (engine version 6, **The loop**). A max run that converged before its budget now
  goes on to its reheats, so most runs on Portland use their whole budget: the acceptance pairs'
  median run took 30.0 s against greedy's 9.2 s at 30 s (7 of 20 converging within it against
  16), and at the frontend's default 20 s most Portland runs end on the budget. It helps only a
  run that converges: one that stops on its budget first (every Oregon and national trip
  measured, at 30 s and 60 s) never reheats and is greedy's. While a heat lasts, the route drawn
  and the stat line's ratio can fall (the stat line then shows the best so far beside it), and
  the route stored is the best, which may come before the last. Annealed routes are longer and
  fill more of their leash, so two seeds share more of them: the Jaccard of seeds 1 and 2 on the
  first five acceptance pairs rose from 0.13–0.18 (mean 0.160) to 0.17–0.23 (mean 0.189) on the
  edges and from 0.28–0.33 (0.298) to 0.33–0.40 (0.363) on the road units; the variety test's
  0.3 on the edges still holds.
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
- **Turn constraints.** A strict one is rarely satisfiable on a street grid (7 and 3 of 20
  Portland pairs plan), and a soft one makes routes less bad than no constraint (medians 5.33×
  and 6.47× against 21.5×; **Constraints**). Under the slowest time a left turn costs 90 s,
  and the time a detour gains counts its delays, so more left turns stay than under the
  distance (1.09 a km on Powell's → Reed, against 0.62). A soft route shrinks toward a smaller
  target only as far as it can without adding a net avoided turn, the price of the rule that
  shrinking never adds one: Powell's → Reed's 16.77× soft no-left route, sent toward 3×,
  converges at 11.04× (`stopped_because: "converged"`, outside the band, the closest route
  returned). Letting a shrinking splice add an avoided turn when the route's density of them
  does not rise is a later option. The turn classes are coarse at super-junctions (**Turns**).
- **Hard mode.** A route under it stays far shorter than without (**On Portland**): every detour
  must leave its window on a road the route does not drive, or on a neighbour's, and never come back
  to one it drove, and a window inside a road's stretch can only be replaced along that road, which
  `window` answers by widening it to the stretch's ends. A road split by an unnamed edge, or by a
  change of ref or name along it, is two roads. The seed is a heuristic: it may find no way and
  refuse a trip (one of the 20 acceptance pairs, where it cannot tell whether a legal route exists)
  or move a stop, even where a legal route exists (**Constraints**). When hard mode refuses the best
  local-exhaustive paths, `find_detour` asks it of the others in turn, so a boxed-in route's
  attempts cost more than a plain one's.
- **Collecting.** The waypoints aim at the item's places while the ball holds some, so where an
  item is common a long distance route can pass about as many by chance (level crossings,
  seed 2: 45 collecting against distance's 52), and places that cannot be reached still draw
  waypoints (the toll booths under a 30 km leash: 129.96× against 176.93×). An item on a
  dead-end road, or behind a cut edge of the road graph, is passed only by a route with a stop
  beyond it. `candidates_in_area` counts a bridge once per road, so a bridge split by junctions
  counts several times there, once on a route.

- **The alphabet route under a strict turn constraint** gets 0 or 1 letters on a grid: a road
  is offered only at the junction where the search first reaches it, where strict leaves it one
  direction at most, and turning onto the next road or off the last mostly needs the refused
  turn (on the 10 × 10 and 20 × 20 named lattices, 200 restarts: 0 or 1 letters strict, 1–10
  soft).
- **The alphabet route.** Most trips get one or two letters: a road of the next letter must be
  reached over free connectors (unnamed edges, ramps and numbered streets, 29% of Portland's
  edges, in scattered pieces) and roads of the letter just completed, which outside the street
  grids with numbered avenues it seldom is (**On Portland**, the alphabet route: a median of 1
  letter on the acceptance pairs, against A to W from Powell's to PDX). Loosening the
  connectors further, such as letting a short stretch of any road connect, is a later option.
  The lookahead counts roads near a ride's end by straight-line distance, not by what the next
  search can reach.
- **The certified worst.** An area is small: 300 nodes and 1,000 edges at most, a few blocks
  around a short trip in town, and the automatic leash aims at 600 edges, which proves most of
  Portland's short trips within the default budget but not every residential grid (Ladd's
  Addition at 500 m ended within 1.32 % at 19.5 s). How long a proof takes depends on the streets
  more than on the counts (one-way grids prove at once, two-way grids are hard) and on the
  machine's load. "Longest" is to the decimetre on each road. A round trip is refused: its start
  would have to be split into a node out and a node in. The solver's 4 threads run in the job's
  one worker, which a server counts as one: a public server busy with certified jobs may run more
  threads than it has cores, and a public value of the threads (2 proved some areas as fast as 4,
  others not) is a follow-up. The route among equally long ones is the solver's choice, and a
  best found under the time limit varies with the run.

## Tests

`tests/engine/` builds its graphs with `worst_route.graph.testing` and the helpers in
`tests/engine/graphs.py` (`lattice`, with twins and transitions, its positions whole metres
for stable bytes; `at_node`, a node's lon/lat rounded to 1e-7° as a request's stop, and
`exactly_at_node`, unrounded, for a stop that must lie on the node itself; `board`, a 5 × 5
lattice with one-ways, twins, a straight-only junction, named roads and a road unit for each
two-way street, which the drawing's tests and the API's draw on).

| Test file | What it covers |
|---|---|
| `test_route.py` | every refusal, round trips, and the release and the ban counts against a brute force on random twins |
| `test_road_copies.py` | a road mapped several times over one run (three two-way copies, two one-way copies and a two-way copy, a one-way and a two-way copy; one segment or an inner junction), built from OSM through the pipeline's test helpers: every round trip out on one copy and back on another refused by `Route.new` and `Route.splice` and failing `edge_simple` alone |
| `test_validator.py` | each violation caught by the right check, including one OSM junction a cluster and its stub both hold, passed at each (failing at the edge into the second), a cluster's members met once, non-finite metrics and reported item counts that differ from the recount; a strict turn constraint proven from the bearings (a left turn failing strict no-left, a right turn strict no-right, nothing under a soft one); an alphabet route passing with any roads on its free first and last legs (the `alphabet` check only in an alphabet request's report, its detail saying so), one out of order failing `alphabet` at the road at fault, each edited field of its letters failing `metrics`, a `free_from` outside the route (or not an int) failing both, an alphabet request whose route reports no letters |
| `test_alphabet.py` | the letters (unnamed edges, links and numbered streets free), the counts (the free first leg up to the first A, equal letters in a row once, a road of the same letter after another, letters out of order counted as they come, the wrap from Z to A, an empty part); the search on named lattices: an ordered town driven A to J, every better route legal (`check_route`) and replaying from the seed, each better than the last and in order, the same bytes twice and on both backends (another seed another route), a connector driving a road of the letter just completed, the free first leg, a town without an A road keeping the seed and converging after its first restart, a run converging after its patience, the build's letters passed in, no restart, a round trip, a strict constraint on every route, a soft one, hard mode on every route (no road come back to), turn tables kept; `plan_route` the same on both backends with the `alphabet` check, its route.json read back and validated (an edited sequence failing `metrics`), no continuation |
| `test_request.py` | the request model and its bounds (its target's too, and no ratio target for a round trip; the item only and always with `collect`, which takes no ratio; the brand only and always with `brand`, which takes none either, and left out of the JSON when there is none; the alphabet route with two stops only, a round trip among them, and no target; `reorder` left out of the JSON when off, at most 7 stops, not under the alphabet route, and 5 on a large build; `certified` left out of the JSON when off, round-tripping when on with a stop between the ends, and refused with another objective, a target, the worst order, a turn constraint, hard mode or a round trip), tier and leash auto, the masks and the tier's edge bans; the objective's keys, the constraints left out of the JSON when there are none, both at once refused |
| `test_target.py` | the target's bounds; the goal's rules on values (the band's edges, phases, the closer route better and a tie to the later, growing and shrinking acceptance at the band, a duration moving one way, a candidate's value equal to the re-scored route's, a ratio and a duration under the slowest time and a duration under the distance on the experienced time, delays included); and on lattices with `check_delta`: ratio and duration targets reached while only growing, a base past a target shrinking into it, a route already within it stopping before an attempt, an unreachable target converging with the closest route, the overshoot after an empty cycle (the closest route returned, and a run cut at its attempt the prefix ending with it), the same bytes on both backends and twice; the slowest time reaching ratio and duration targets on its experienced time |
| `test_snap.py` | roles, cul-de-sacs, stops kept apart, round trips only for identical points, an end near the start moved on, re-snapping |
| `test_search.py` | the turn edges counting only on a build with rows, a path driving both carriageways searched again, and the rounds' limit; a cheapest legal walk entering a node twice searched again, to a bypass or to no path (and none at once for the baseline's search), the rounds' limit (as many loops as rounds, not one more), on both backends; on every pair of two small towns with tables, the answer a legal simple path by brute force (or None, always when none exists), costing at least the walk; the searches made again on a 30 × 30 town the same bytes on both backends; a seed leg's tree (`leg_path`): the walk's first entry banned where banning its way back leaves nothing, the searches growing with the distance between the ends (as many loops as searches, not one more, unless the ends lie 100 km farther apart), the later twin banned, on the small towns the walk where it is simple and else a legal simple path costing at least it, all 107 with 17 searches, the same bytes on both backends there and on the 30 × 30 town |
| `test_seed_route.py` | `resolve_stops` deterministic, node-disjoint legs, legs avoiding later stops, the penalty re-solve on a corridor, the baseline, the re-snap walk out of a one-entrance ring, the start never moving, a point-to-point trip never collapsing onto the start, a leg between two stops on one node, unreachable legs named by both ends, a leg going round a junction that allows only straight on, the turn at a via, the baseline's legal legs and its turn-blind fallback, a leg whose cheapest legal walk loops taking a simple path (the end staying, the baseline still turn-blind), a leg the chain of rounds refused entering the node by the way its walk came back (the end staying), a leg driving both carriageways going round, a round trip to a neighbour not one road out and back, round-trip loops (never back along the way out); the worst order against each order's seed found as planning finds it: the first of the longest (four orders tied, two on a round trip), the orders no seed fits skipped (8 of 24 round a round trip), the start and the end kept, a stop the fallback moves out of a one-entrance ring in every order and the order not given winning, the given order's refusal raised as `resolve_stops` raises it, the same order twice and on both backends, one order for one stop between the ends, a pilgrimage's order whose area lacks its brand skipped |
| `test_objectives.py` | `Distance`, the radius, the registry (`brand` with its brand), and `delta` against a full re-score for every kind of term, the first-edge term included; the delays in every mode's metrics; `SlowestTime`'s terms, weights, waypoint pool, `describe` and primary quantity (the experienced time), and its `delta` against a full re-score |
| `test_collect.py` | `count_items` and each item's score, items first then length (a bridge split by a junction once, a bridge at the start, a bridge run through a round trip's start once, a roundabout on a round trip's start once, a toll booth in either direction), `delta` against a full re-score for each item with windows at the route's start and at its end (a round trip's start inside a bridge run among them), the waypoint pool (the uncollected places, once per route, any node once none is left in the area), the radius, `describe`; for each item a loop run with `check_delta` that never gives an item back and validates; a request planning the same collecting route on both backends, and one reaching a duration target; annealing to convergence and its reheats never giving an item back |
| `test_brand.py` | `brand_counts` and the score, visits first then length (a round trip's start once), the counters and `describe`, an unknown brand refused, `delta` against a full re-score with windows at the route's start and at a round trip's end, the waypoint pool (the brand's unvisited nodes in the area, once per route, any node once none is left), the radius; a 300-attempt loop with `check_delta` that never gives a visit back, validates and is the same on both backends; the validator failing each tampered field of a reported brand and an `of` the area does not give; a request planning the same pilgrimage on both backends, its route.json read back with its brand and validated, an edited one failing; a brand the build lacks or the area does not hold refused as `BrandRefused`; a duration target reached |
| `test_turns.py` | the classes by the change of bearing (the bands' ends, across north), the table against the rule written out, a lattice route's counts, a U-turn and the stored bearings deciding |
| `test_constraints.py` | the request's constraints (`strict` alone refused); strict ones refusing turns and windows (at their boundaries), soft ones scoring minus the penalty a turn, a kilometre's worth in the objective's units (`turn_penalty_for`); the composite summing scores and combining hooks, its `constraint_delta` the penalties, its `delta` against a full re-score; the searches avoiding a class when they can and taking it when they must, refusing it when banned; the strict seed (vias, an unreachable leg as `InfeasibleTurnConstraint` with its note, one no route covers without the constraint either as the plain `StopUnreachable`, a round-trip loop), the strict loop never turning the refused way, the soft loop adding an avoided turn only for more than a kilometre of route (and far fewer than the plain loop), both on both backends, one-way towns with tables; a soft no-left base shrunk toward smaller ratios never adding a left turn or U-turn (each splice replayed) |
| `test_hard_mode.py` | the rule read off a route's edges (an unnamed edge between two stretches of a road, a round trip coming home on the road it left by); `feasible_window` refusing a splice that splits a stretch or comes back to a road and accepting fresh roads and the neighbours' extended, and agreeing with a full check of the spliced route over every short window of three routes; the bans exact per window (all but the road inside a stretch), a fresh array each time and following the route after a splice; no terms; `window` widening a window inside a stretch, never past a stop, through the composite; `find_detour`'s local-exhaustive path the best hard mode allows; the request's flag (in the JSON only when set, its note, with strict and soft turns); the seed re-solving a leg that comes back to its road, and the earlier leg when the later cannot; round-trip loops; what an unreachable leg blames (hard mode, the strict constraint, nothing); a stop moved for hard mode; `check_base` refusing a base that comes back to a road; the loop with `check_delta` on both backends, alone and with soft no-left, every route along the way one stretch a road; the validator's proof; `plan --hard`, `validate` on a file edited to claim it, `--base` refusing `--hard`, and a trip it cannot drive in one line ending "try without --hard" |
| `test_detour.py` | each strategy's legal detours and None when none exists, the waypoint's pool (and any node of the ball when it holds none of the pool), the strategy weights, random weight drawing only near the window, the local-exhaustive ranking and the refused-candidate skip, no turn refused on a lattice of straight-only junctions (and many when the searches are made turn-blind); shrinking toward a target, the local-exhaustive path closest to the band first, and never a longer one (a longer, faster path shadowed a shorter one for a duration) |
| `test_worsen.py` | over fixed seeds, `check_delta` on: the score only rises, every event replays to the best route, budgets in iterations and seconds, convergence, a splice after two empty cycles and the run stopping only after three, the segment bias's rate and its anchors (the edges with the most unused nodes near), the unused-node grid's cap, the same seed gives the same bytes, different seeds give edge sets with Jaccard < 0.7, random one-way towns with twins always validate, and so do those with turn tables (no turn refused), a tier's shortcut ban, a point-to-point request never becoming a round trip, identical points planning a loop, an unexpected seed error reported as `StopUnreachable`; the same-seed test on both backends, and both backends planning the same route, splice for splice, on the grid and on one-way towns with and without turn tables; shrinking toward a target, runs of 6 edges on average and up to 24; the nodes a detour can leave or join (three distinct neighbours over the usable edges, the mask and bans of both directions taking one away), a window widened to them on a lattice whose streets are split in two, never past a stop, and every splice of a run there starting and ending at one or a stop; annealing (on a lattice of uneven streets and a town with turn tables): the temperature's cooling and freeze, greedy an exact prefix of an annealed run (its first reheat where greedy stopped), shorter splices taken and the highest-scoring route returned and validated, a run capped below convergence and a target run unchanged, both backends and a second run alike, and `plan_route` reheating a max run 3 times |
| `test_draw.py` | a drawing on a 5 × 5 lattice with one-ways, twins, a straight-only junction and named roads: an empty path at the start with its legal ways out, the stops reached in order and the end; every refusal at its first illegal edge (a one-way taken from its head, an id outside the graph, a path not from the start among them); of two refusals the earlier, the path before it legal, where the route check finds the later first; a round trip closing at its start only once its stops are reached and not out and back along one road (a road of its own back allowed); on 150 random walks under random stops, round trips, areas, bans, strict turns and hard mode, the next moves exactly the out-edges the check takes, each other refused at the path's end, every code met; on 300 walks that break the rules anywhere, the refusal at an edge whose path before passes and whose path with it is refused alike; a finished drawing's route.json (its run drawn by a human, its ratio), a round trip's without a ratio, an unfinished one refused; the objective and target a drawing takes, and no worst order |
| `test_io.py` | the route files, the target in the request and, for a target run, in the metrics; the delays and the turns in the metrics, and a saved route's metrics carrying its file's item counts; the order and the reordered request in the file, a file without an order read as the identity |
| `test_exact.py` | the certified worst: its length the longest legal route networkx's simple paths give (each checked by `check_route`) on the board at eight pairs, two with a stop between the ends, the rules refusing some paths of each; on a lattice whose longest path a twin and a turn table forbid, the certificate shorter than the path without them and equal to the longest legal one; on the pipeline's fixtures, restrictions (where the rules forbid the longest path of most pairs), a divided road and three super-junctions (a diamond, a cloverleaf, a roundabout); every route the solver hands on legal (`Route.new`, the validator) and longer than the last; the area without a dead end, a node behind the end, one that reaches only the start or the edges back into the start; an explicit leash over a monkeypatched cap refused with its counts and message; the automatic leash the widest within the cap (10 m more is over it), recorded in the request and the certificate; the narrowest joining leash taken when no small one joins the stops, refused over the hard cap; the same length at four seeds; a short budget's route legal and at least the seed, its bound, when it has one, never below the longest legal route (3.5 km, 0 → 5 on a 6 × 6 lattice); a solve ended before any solution (`UNKNOWN`) keeping the seed with a null bound and gap, `feasible`, stopped on its budget; a model the solver finds infeasible raised as a bug; a better route whose bound CP-SAT puts below it (monkeypatched) handed on with a null bound and gap, the route still legal; route.json's request and `run.certified`, none for another route; no base route; a process that plans without `certified` never importing OR-Tools |
| `test_plan.py` | `plan_route`'s hooks: `on_start` once before the loop with the route it starts from (every splice replays from it to the result), the same route without the hooks, a continuation from `base` that never scores less, is fixed by its seed on both backends, and refuses a base for other stops, one leaving the request's area (before the loop) and, in `check_base`, a node outside the mask or an edge the request bans; a soft constraint starting from the plain seed and keeping left turns rarer than the plain run's, a strict one no route keeps as `InfeasibleTurnConstraint` with its note, one no route keeps without it either (two one-way rings) refused as without it, a stop the strict fallback moves noting why, a base refused for a turn a strict constraint refuses (taken by a soft one); a strict route validated against its constraint; the worst order planned and validated in the order it visits the stops (`on_start` given it), the identity and the same route without it, the same order and route twice, on both backends and with another seed's order, the reordered request planned again the same, refused with a base and past a large build's cap; the temperature a max run anneals from (a hundredth of the baseline's length, of its experienced time under the slowest time, of the start route's length for a round trip; none for a target) |
| `test_cli_plan.py` | `plan` and `validate` on builds written with `write_build`, including the error paths, a route that fails validation never printed, a truncated and re-edited file caught by its re-derived stops, an id beyond int32, `--backend` on both commands (the same route.json on both backends but the backend and the time), and the same on a build with turn tables, where `auto` is `csr` too, a file from another engine version refused; `--target` (its line, its parsing, its refusals) and `--base` (a continuation to a smaller target, the options it refuses, a file from another engine version, region or `graph_version`, a truncated or broken route, stops that do not resolve); a ratio target for a round trip, from the stops or the base, refused; an illegal route reported as the base file's fault only with `--base`; `--objective`, `--no-left`, `--no-right` and `--strict` (their lines, the time ratio with its drive and delays, the route.json, `validate`, their refusals, `--strict` alone in one line, a strict refusal in one line ending "try without --strict", `--base` taking them from its file); `validate` failing a soft route's file edited to claim a strict constraint; `--reorder` (its order line, the stops printed in the order visited, the order and the reordered request in route.json, `validate` passing it, `--base` continuing it in that order without `--reorder` and refusing it, no order line without it) and its refusals (`--objective alphabet`, eight stops, six on a large build), each one plain line; `--objective collect --item` (the collected line, the items and item in route.json, `--base` keeping them, a distance run's items) and its refusals, each one plain line (an unknown objective before its item); `--objective brand --brand` by name in any case and by id (the visited line, the brand in route.json, `--base` keeping it and refusing `--brand`, `validate` passing it and failing an edited count) and its refusals (the misfits, a name no brand has, one two brands share, an unknown id, a brand out of reach); a brand named "76" in a build whose brand 76 is another (the bare number refused, `"76"` the name, `#76` the id, `77` an id), `#` without a number, `²`; a build missing an array, one line for `plan` and `validate`; `validate` refusing a route.json whose item counts were edited; `--objective alphabet` (its objective and letters lines, the letters line for one letter and past Z, the `alphabet` check in the validator's line, the letters in route.json, `validate` passing it and failing a road edited in it) and its refusals (`--via`, a target, a `--base` file of an alphabet route), each one plain line; `--certified` (the request line with the leash to two decimals and no attempts, its certificate line after the search, route.json's request and `run.certified`, `validate` passing it; the certificate's line proven, with a bound, and with none) and its refusals (another objective, a turn constraint, a target and `--base`, exit 2; an area over a monkeypatched cap, exit 1) |
| `test_portland_data.py` | (`data`: Portland's latest build through `WORST_ROUTE_DATA`, skipped without one) Powell's → Reed, seed 1, 4,000 attempts: the same bytes on both backends, splice for splice, no turn refused; the slowest time reaching 9 h within ±2 %; soft no-left with fewer left turns a kilometre than its seed, at more than 5×; strict no-left planning Powell's → Reed with no left turn or U-turn, strict no-right refusing it in one line; hard mode over 4,000 attempts, the same on both backends, every road one stretch, at more than 3×; collecting each item over 4,000 attempts, more of it than the distance route passes (none of the toll booths, which lie outside the area), and a duration target under `collect` reached within ±2 %; a pilgrimage to the brand with the most locations visiting more of it than the distance route over the same 4,000 attempts, its counts the recount's; an alphabet route over 100 restarts, the same bytes and letters on both backends, no turn refused, its `alphabet` check passing, at least 5 letters (A to I on build ffe2e7c881ca); the certified worst from Powell's to Pioneer Courthouse Square at a 400 m leash, proven at two seeds with one length, longer than the loop's converged route |

**The acceptance run**, `tools/acceptance.py`, plans random stop pairs on a saved build, so it
needs `WORST_ROUTE_DATA` and is not part of the gate:

```
uv run python tools/acceptance.py --region portland [--build-id ID] [--pairs 20] [--seed 1]
    [--budget 30] [--backend auto|csr|python] [--min-km 3] [--max-km 15] [--jaccard 5]
    [--pair-seed 2026] [--near-m 250] [--tier auto|T0..T4] [--leash-km auto|X] [--places]
    [--out tools/out/acceptance.json]
```

Its `--backend` is `make_backend`'s, `auto` by default, which is `csr` when numba imports (M1's
numbers were measured on the reference); the report records which backend ran. A run bounded by
time goes further on a faster backend, so the backends give different routes only where a run
stops on time rather than converging.

The pairs are uniform points in the region's bbox with a node within `--near-m`, the end at a
straight-line distance within the bounds, drawn from `PCG64(--pair-seed)`; with `--places`, the
places of the region's places index instead (`worst-route aux places`, towns and CDPs alike, one
drawn uniformly by its row and put at its polygon's `point_on_surface`), with the same rejection
and bounds. Each is planned with `plan_route` and no attempt limit, so the budget bounds every
run, at `--tier` and `--leash-km` (`auto` both by default; a leash within (0, 150] km, as a
request's). The first `--jaccard` pairs
that plan are planned again with the next seed and compared (Jaccard of edges and of road
units). A pair that cannot be planned, or whose stop the re-snap fallback moved, is resolved
again on the build without its transition rows, to tell whether the tables stopped it. It prints
a table row per pair and a summary (planned, validator passes, median ratio and range, attempts
per second, the failures the transitions caused, the Jaccards), writes the JSON report, and
exits 1 only when a planned route fails the validator. On Portland it took under two minutes
before engine version 6; with annealing most runs use their whole budget, and it takes about 11
minutes (the 20 runs 505 s of them, 2026-10-31).

`tests/golden/` freezes nine routes (seed 7, three stops but the last's two) on a 20 × 20
lattice whose positions
are whole metres and whose stops are its nodes' lon/lat rounded to 1e-7°, so their bytes and
pinned `graph_version` do not depend on PROJ's last bits: a max run of 400 attempts (`grid20_distance.json`); runs to a 6× ratio and a 2,400 s
duration target, each stopping within it (`grid20_ratio.json`, `grid20_duration.json`); the
slowest time, 400 attempts, on the lattice with a primary road every fourth row and column, 30 s
signals where two cross and 10 s stop signs along them (`grid20_time.json`); soft no-left,
400 attempts, between inner nodes (`grid20_noleft.json`); 400 attempts collecting bridges
on the lattice with thirteen roads made bridges (`grid20_collect_bridge.json`, 7 of them);
400 attempts of a pilgrimage to the second of two brands placed on the lattice
(`grid20_brand.json`, all 14 of its locations); 400 attempts under hard mode on the lattice
with its rows and columns named "Row r" and "Col c" (`grid20_hard.json`, 3.26× the baseline);
and 20 restarts of an alphabet route corner to corner on the lattice with its rows named after
trees in a fixed shuffled order (A on row 2) and its columns numbered avenues but two named ones
(`grid20_alphabet.json`: A to I, its free first leg two edges). Both backends must reproduce
them. They change only under a tier A brief (the collect brief added the sixth, the brand brief
the seventh, the hard mode brief the eighth and the alphabet brief the ninth;
`node_roundabouts`, the request's `item` and the brand arrays changed the others'
`graph_version` and request, not their routes; the long trips brief's windows widened to the
nodes a detour can leave or join moved `grid20_noleft.json` and `grid20_brand.json`, the brand
route still visiting all 14: on a lattice only a corner has two neighbours, and both routes
pass one that is no stop);
`WORST_ROUTE_REGEN_GOLDEN=1 uv run pytest tests/golden` rewrites them.
