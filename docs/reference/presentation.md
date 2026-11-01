# Presentation

What the app says and shows about a route once it exists: its turn-by-turn directions with
deadpan jokes among them, its extra numbers (fuel, states and towns, the share of the roads) and
its toasts, how its length compares with things people know, and its share image. Code:
`src/worst_route/presentation/`, the endpoints `api/share_api.py` (`docs/reference/api.md`,
**Share image**) and `api/directions_api.py`, the route documents' fields (`GET /routes/{id}`,
`worst-route plan --out`), `worst-route directions`, and the Census files' fetch script
`tools/fetch_census.sh`. The design it implements is `docs/PLAN.md` §4.3 and §7 M4; the
conventions are `CLAUDE.md`. Nothing here plans or changes a route: it reads routes, their
builds and the places index.

| Module | What it holds |
|---|---|
| `places.py` | `write_places` (the index's writer), `PlaceIndex` (`load`, `states_at`, `town_points`, `states_crossed`, `towns_passed`, `outlines`), `State`, `Town`, `TownPoint`, `PlacesWritten`, `route_line`, `census_dir`, `places_dir`, `state_files`, `place_files` |
| `comparisons.py` | `COMPARISONS`, `Comparison`, `SHORTER`, `comparison_line`, `distance_text`, `METRES_PER_MILE` |
| `share_image.py` | `render_share_image`, `headline`, the image's sizes and thresholds (its version, `SHARE_IMAGE_VERSION`, is the endpoint's, in `api/share_api.py`) |
| `share_palettes.py` | `PALETTES` (the image's colours in each of the frontend's themes), `SharePalette`, `CLASSIC` |
| `directions.py` | `directions`, `Step`, `StepKind`, `road_text`, `compass`, `turn_word`, `numbered` |
| `jokes.py` | `JOKES`, `inject` |
| `stats.py` | `stats`, `Stats`, `toasts`, `Vehicle` |
| `document.py` | `present`, `with_presentation`, `route_directions` |
| `geojson.py` | `route_geojson`, `feature_collection`, `line_coordinates` |

`config.py` holds `CENSUS_VINTAGE` (2025) and `STATES`: each state's Geofabrik slug (a `Region`'s
`state`) to its USPS and FIPS codes, for the 50 states and DC, which match the Census state
file's NAME, STUSPS and STATEFP; and the directions' and toasts' numbers:
`DIRECTIONS_SLIGHT_DEG` (45) and `DIRECTIONS_SHARP_DEG` (120), `JOKE_EVERY` (8) and
`JOKE_JITTER` (0.25), `TOASTS_MAX` (10).

## Directions

`directions(data, nodes, edges, stop_idx, *, labels) -> list[Step]` turns a route into GPS-style
steps, each `Step(step, text, distance_m, time_s, edge_range, kind)`:

- `step` numbers them from 1;
- `edge_range` is **half-open**, `[a, b)` over the route's edge positions (edge k runs from
  node k to node k + 1); the driving steps (`depart`, `continue`, `turn`, `ramp`, `roundabout`)
  tile `[0, len(edges))` in order, and a `stop`, the `arrive` and a `joke` drive nothing, an
  empty range `[k, k)` where they come;
- `distance_m` is the `edge_length_m` of its edges; `time_s` their `edge_time_s` and the
  `node_delay_s` of the nodes it arrives at (the heads of its edges), the start's in the first
  step (a round trip's once), so the steps add up to the route's length and to its driving time
  and delays (`math.fsum` throughout).

**The grammar.** A step names its road (X below) by its first edge's `edge_name` as OSM writes it
("West Burnside Street", not abbreviated), else its `edge_ref` (the first of a `;` list: "I 5"
from "I 5;US 30"), else its class in words: "a ramp" (a link), "an alley", "an unnamed road".
The route is cut into steps at its junctions; at node v between edges e and f, the first rule
that applies:

| Rule | Step | Text |
|---|---|---|
| v is a stop between the ends | `stop` (then a new step from v by the rules below; where they would go on, `continue` "Continue on X") | "Arrive at <label or 'stop N'>; continue" |
| v is a roundabout (`NodeKind.ROUNDABOUT`), even on the same road | `roundabout` | "At the roundabout, take the exit onto X" |
| f is a link (`EdgeFlag.LINK`) and e is not | `ramp` | "Take the ramp toward X", X the first step after it that does not start on a link; "Take the ramp" when the route ends on an unnamed ramp |
| e and f are both links | the same step (a ramp's curves and forks are the ramp's) | |
| e is a link and f is not, straight on | `continue`, always (a ramp that carries the road's ref, as US 30's do, still ends) | "Continue onto X" |
| a left or right turn, or a U-turn (`engine.turns.turn_classes`), on the same road or not, off a ramp too | `turn` | "Turn left onto X"; "Slight left" when the bearing changes by at most 45°, "Sharp left" beyond 120°; "Make a U-turn onto X"; "to stay on X" instead of "onto X" when f's road is named (a name or a ref) as e's is: "Turn left to stay on Southwest Arthur Street" |
| straight on, e and f sharing an identity (`edge_identity`), neither with one, or their roads named alike (an identity change the driver cannot see) | the same step | |
| straight on, otherwise | `continue` | "Continue onto X" |

The first step is `depart`, "Head <compass point> on X", the first edge's `bearing_out` to the
nearest of eight points (a half-way bearing to the next one clockwise: 22.5° is northeast); the
last is `arrive`, "Arrive at <label or 'your destination'>", or
"Arrive back where you started" for a round trip without a label. The change of bearing is the
turn classes' (`docs/reference/engine.md`, **Turns**), wrapped to (−180, 180].

**Super-junctions.** The build keeps no path through an interchange or a roundabout (only
cleanup's stage does), so they are described by the edges that enter and leave them: a
roundabout's exit has no number, and a ramp is named by the road it leads to. A ramp step opens
only where the route leaves a road for a link edge; an interchange is otherwise a junction like
another, so a route through one turns or goes on by the edges around it ("Turn left onto SE
Main St" off I 5 at the diamond fixture's), and a motorway through it on one identity, straight
on, stays one step. On Portland's build every roundabout is a super-junction (no edge has the
roundabout flag) and the 54 link edges stage B left all touch interchanges.

**On Portland** (build 506f747d7458, `graph_version` ae1ce1e8289b; Powell's → Reed, seed 1,
`worst-route plan --budget 30`, 5,000 attempts, 34.21×, 2,819 edges): 1,532 steps, of them
1,430 turns (11 of them "to stay on"; no U-turn), 32 continues, 28 roundabouts and 40 jokes, and
no ramp (the route never takes a link edge), in about 10 ms; the first is "Head east on West
Burnside Street", the last "Arrive at your destination" (or the stop's label through the API),
and the steps add up to the route's 263.92 km and 10 h 54 min to the metre and second. The
route turns so often because it is the worst route: half its junctions are turns. Powell's →
PDX (seed 1) takes one ramp, "Take the ramp toward I 5", then "Continue onto I 5".

## Jokes

`JOKES` holds forty lines of a GPS that has given up ("In 400 feet, reconsider your choices.",
"You have passed this intersection's neighbour. Not this one. Rules are rules.", "Recalculating.
No. This is the route."). Nine fill placeholders from the route: `{ratio}` ("34.2×"), `{turns}`
(how many `turn` steps came before) and `{distance}` (the step just driven, "2.3 mi (3.7 km)" as
the comparisons write distances, "330 ft (100 m)" under a tenth of a mile).

`inject(steps, rng, *, ratio)` puts them among a route's steps, from a
`Generator(PCG64(seed))` on the route's seed, so a route and its seed always tell the same jokes
(the global generators are never used). It draws an order of all forty, then the gaps: a joke
after about every eighth driving step, the mean gap `JOKE_EVERY` (8), or the driving steps over
one more than the jokes when a route has more of them, each gap uniform within ±`JOKE_JITTER`
(25 %) of it. A gap's joke is the next in the order that it can fill: none with `{ratio}` for a
round trip (null ratio), none with `{turns}` before the first turn. No joke is told twice in a
route; once the forty are told there are no more, and a long route has them spread to its end
(Powell's → Reed: all 40 over 1,492 steps, about every 36th). A joke step drives nothing:
distance and time 0, an empty range where the step before it ends. The steps are numbered again.

`route_directions(data, nodes, edges, stop_idx, *, labels, seed, ratio, jokes=True)`
(`document.py`) is the two together, as every document, `GET /routes/{id}/directions` and
`worst-route directions` give them.

**`worst-route directions route.json [--region R] [--build-id ID] [--jokes/--no-jokes]`**
prints a saved route's directions, one numbered line a step, a driving step with its distance
and time, from the build the file names (or `--region` / `--build-id`), as `validate` loads it;
a file it cannot read, or a build at another graph_version, is one line and exit 1. Its labels
are none (the CLI has none), its jokes the file's seed's:

```
   1. Head east on West Burnside Street  (0.09 km, 0 min 11 s)
   2. Turn right onto Southwest 5th Avenue  (0.28 km, 2 min 32 s)
  ...
  16. Turn right onto North Multnomah Street  (0.18 km, 0 min 20 s)
  17. Slight right to stay on North Multnomah Street  (0.03 km, 0 min 34 s)
  ...
  33. Keep calm. Keep driving. Keep not arriving.
```

## Stats and toasts

`stats(data, edges, *, length_m, baseline_length_m, vehicle, places, geometry, active, banned)
-> Stats` (`geometry`, the build's, which only the places need: None without them):

- `extra_fuel_gal`: the length beyond the baseline's, in miles, over the vehicle's `mpg`;
  `extra_cost_usd`: that at its `gas_price_usd`. Both null without a vehicle (the API request's
  `vehicle`; the CLI has none). A round trip's baseline is 0 m, so its extra is its whole length.
- `states` and `towns`: the Census `NAME`s ("Portland", not "Portland city") of the states and
  places the route's line passes through, in the order it first enters them
  (`PlaceIndex.states_crossed`, `towns_passed`, **Places**); `towns_n`, how many. All three null
  without a places index.
- `network_fraction`: the road units (`edge_unit`) the route drives over those of every edge its
  request lets it drive, both ends in the request's `active` mask and not in its `banned` one
  (its leash, tier and bubbles: `docs/reference/engine.md`, **Requests**).

`toasts(stats, metrics, *, nodes, edges) -> list[str]` gives one-line facts in a fixed order,
the most striking first, a fact the route does not have left out; no randomness:

| | Toast | When |
|---|---|---|
| 1 | "34.2× less efficient than the sensible route." | a ratio (every route but a round trip without a via, whose baseline is 0 m) |
| 2 | "Ends exactly where it began, as all things do." | a round trip |
| 3 | "Passes through 11 towns and communities. Portland twice, in spirit." / "Never leaves Portland. Sees all of it instead." | towns known, at least one |
| 4 | "Never turns left. 847 right turns." (or right) | no turn one way, some the other |
| 5 | "An extra 6.6 gallons, or $25.80, for nothing." | a vehicle |
| 6 | "10 h 54 min on the road instead of 21 min." / "... on the road to get nowhere." | always (the second when the baseline takes no time); the experienced times, driving and delays, in whole minutes as `plan` counts them |
| 7 | "2 h 57 min waiting at signals and stop signs." | delays |
| 8 | "2,820 intersections, none of them twice." | always (a round trip's start once) |
| 9 | "2,819 stretches of road, each driven exactly once." | always |
| 10 | "Visits 2 states: Oregon and Washington." | more than one state |
| 11 | "725 left turns and 724 right turns." | turns both ways (never with 4) |
| 12 | "Uses 23 % of the roads within reach." ("under 1 %") | always |
| 13 | "Passes 12 level crossings, 28 roundabouts and 10 bridges on the way." | the metrics' `items` (every route's, `docs/reference/engine.md`), some above 0 |

A route has at most twelve at once (4 and 11 exclude each other), and `TOASTS_MAX` is 12, so
none is ever dropped; one without a places index, a vehicle or delays still has six. Powell's →
Reed through the API with a vehicle (24 mpg, $3.89) has ten: the ratio,
"Never leaves Portland. Sees all of it instead.", "An extra 6.6 gallons, or $25.80, for
nothing.", the time, the delays, the intersections and stretches, the turns, "Uses 23 % of the
roads within reach." and "Passes 12 level crossings, 28 roundabouts and 10 bridges on the
way."

## The document

`present(data, nodes, edges, stop_idx, metrics, *, active, banned, seed, labels, vehicle,
places, geometry) -> {"directions": [...], "toasts": [...], "metrics_extra": {...}}` is what both
route documents add to a route, from its arrays, its document's metrics (the ratio, the times
and delays, the turns), its request's masks, its seed, its stops' labels, its request's
vehicle, the region's places index and the build's geometry (only the places need it: the CLI
reads it only when it has a places index); `metrics_extra` holds `extra_fuel_gal`, `extra_cost_usd`, `states`,
`towns`, `towns_n` and `network_fraction`. Its two callers:

- **`worst-route plan --out`** writes `with_presentation(route_document(...), present(...))`:
  `directions` and `toasts` beside route.json's other keys, `metrics_extra` added to its
  `metrics` (no labels or vehicle; the places index under `WORST_ROUTE_DATA` when `aux places`
  wrote one). `read_route`, `validate` and `plan --base` read the file as before
  (`docs/reference/engine.md`, **Route files**).
- **`GET /routes/{id}`** works it out again on every request (`routes_api.presentation_of`),
  with the request's labels and vehicle and its region's places index (its own, else a served
  region's that covers its states: `docs/reference/api.md`, **Running it**), and
  **`GET /routes/{id}/directions`** gives the same directions alone (`docs/reference/api.md`).

Nothing of it is stored: the worker's and the job manager's stored document is route.json's
without it (`engine/io.py`'s `route_document`), so every route ever stored gets it, and a later
places index or a new joke reaches old routes too. **Measured** (Powell's → Reed, 2,819 edges,
build 506f747d7458, `serve --port 0`, the dev machine with other lanes' tests running, load
average 13–19): `GET /routes/{id}` answers in 28–70 ms once warm (0.11 s for the first, which
builds the prepared polygons' indexes), `/directions` in 13–45 ms; in one process `present`
takes 17–47 ms, the directions about 10 ms, the places lookups 5–7 ms and the request's masks
4 ms. Before the places lookups were prepared (**Places**), the same document took 0.26–0.38 s
on a quiet machine and 1.8–2.6 s at load average 30, nine tenths of it the lookups.

## GeoJSON

`route_geojson(data, geometry, result)` (`plan --geojson`) is a FeatureCollection:

- the route and the baseline as LineStrings along their edges' shapes in the build's geometry
  (`docs/reference/graph.md`, **Geometry**: within 2.71 m of the road; `[lon, lat]`, 7
  decimals; a point repeated where two edges meet is kept once);
- one Point per stop, at its snapped node.

Each feature has a `kind` property (`route`, `baseline`, `stop`). The lines carry `length_m`
and `time_s`; the stops carry `index`, `node`, `moved_m` and `note`. A round trip's baseline
has a null geometry. `feature_collection(data, geometry, *, edges, length_m, time_s,
baseline_edges, baseline_length_m, baseline_time_s, snaps)` makes the same from a stored route's
parts (the API's `GET /routes/{id}/geojson`), and `line_coordinates(data, geometry, edges)` is
one line's points. The engine and the job workers never import the module. Over the twenty
acceptance routes on Portland (2,000 attempts each) the GeoJSON files take 1.00 MB where format
3's unsimplified float32 shapes took 2.72 MB, with 32,131 points where those had 87,551.

## Places

**The Census files.** Cartographic boundary files of the 2025 vintage, free to use with
acknowledgement to the U.S. Census Bureau (the share image's footer gives it), and meant for
display and hit-testing, not for areas: `cb_2025_us_state_5m.zip` (states, DC and territories,
1:5,000,000), `cb_2025_us_state_20m.zip` (the same at 1:20,000,000, for drawing) and
`cb_2025_<FIPS>_place_500k.zip` per state (its incorporated places and census-designated places,
1:500,000). A person fetches them; nothing else downloads them:

```
tools/fetch_census.sh 41 53      # Oregon's and Washington's places, then the two state files
```

into `$WORST_ROUTE_DATA/aux/census/` (default `./data/aux/census/`), each with `curl` under the
project's User-Agent (`config.Settings.user_agent`) to a temporary name, then checked against its
pinned sha256: the two state files and the places of the 48 contiguous states and DC (`us-t1`'s
states) are pinned; a mismatch is deleted (exit 1); a file with no pinned sum is kept and its
sum printed, to pin in the script; a fetch that fails leaves nothing behind (exit 1). A code
that is not two digits is refused before anything is fetched (exit 2).

**The index.** `worst-route aux places --region R [--census-dir PATH]` (`--census-dir` default
`<WORST_ROUTE_DATA>/aux/census/`) writes three GeoParquet files under
`<WORST_ROUTE_DATA>/aux/places/`, geometry as WKB, CRS EPSG:4326 (the Census files are NAD83,
EPSG:4269, taken as equal):

```
states.parquet           STATEFP, STUSPS, NAME, geometry   the 5m file: 56 rows (50 states, DC,
                                                           5 territories), by STATEFP
states_outline.parquet   the same columns                  the 20m file: 52 rows, for drawing
<region>.parquet         GEOID, NAME, NAMELSAD, LSAD,      the places of every
                         STUSPS, geometry                  cb_2025_<FIPS>_place_500k.zip present
                                                           whose polygon meets the region's bbox,
                                                           every LSAD (25 city, 43 town, 47
                                                           village, 21 borough, 57 CDP, ...),
                                                           by GEOID
```

The region's bbox is its latest build's (`params.bbox`), else its own in `config.REGIONS`, else
the bounds of its states' polygons (the union of them, for a region of several states). Every
places file present is read, so a region across a state line has both states' places (STUSPS
says which). It prints what it wrote, and on stderr a warning,
not an error, for each state whose polygon meets the bbox but whose places file is absent; a
missing state file is one line and exit 1. It rewrites its files each time (about a second), the
same files from the same inputs:

```
wrote data/aux/places/states.parquet (56 states)
wrote data/aux/places/states_outline.parquet (52 outlines)
wrote data/aux/places/portland.parquet (68 places: OR 53, WA 15)
```

Without Washington's file it writes Oregon's 53 places and warns
`missing places for WA: run tools/fetch_census.sh 53`.

For `us-t1`, the 48 contiguous states and DC, it reads the 49 places files and writes 31,540
places (a 30.5 MB file) in 4.4 s, peaking at 0.33 GB. Loading that index takes 0.75 s and 279 MB
of the API process, and a 50,000-point line from coast to coast meets its 12–15 states and up to
235 towns in 0.13–0.14 s.

**Asking it.** `PlaceIndex.load(region, data_dir=None)` reads the three files (pyarrow and
shapely, no geopandas) into two shapely STRtrees, the states and the region's places, and keeps
the outlines to draw; it returns None when any file is missing, and callers then know no states
or towns and draw no outlines, never an error. For a route's line in lon/lat
(`route_line(data, geometry, edges)`, the points `geojson.line_coordinates` gives, along the
build's geometry):

- `states_crossed(line) -> list[State(fips, usps, name)]` and `towns_passed(line) ->
  list[Town(name, lsad, usps, geoid)]` list every polygon the line **intersects** (a touch
  counts: the route passed through it), CDPs included.
- **In route order, by where the route first meets each**: the first of the route's segments
  (consecutive points, in order) that intersects the polygon, then, along that segment, the point
  of their intersection nearest its start (its start itself when the segment has no length, a
  point repeated). Polygons first met at the same point keep the files' order (STATEFP, GEOID).
  Working segment by segment makes the order exact and never confused by a route that crosses
  itself on an overpass: a polygon is placed where it was first met, and a route that comes back
  into it later does not move it.
- **Fast**: the polygons are prepared (`shapely.prepare`) when the index loads, and each segment
  is tested only against the polygons whose bounds it meets, on the prepared polygons. On
  Portland a 9,642-point route takes 5–7 ms against the states and the region's places, a
  15,369-point one 49 ms; unprepared, the same lookups took 0.7–1.1 s on the same busy machine
  (load average about 27) and about 0.23 s on a quiet one. Prepared polygons are not safe under
  concurrent use with shapely 2.2 and GEOS 3.14, used before or not (sixteen threads asking at
  once crash the process), so the index answers one lookup at a time.

`states_at(points) -> list[State | None]` gives the state each `(lon, lat)` point lies in (a touch
counts; a point on a border the first state by STATEFP), None for a point in none, at sea or
abroad: the API's pick of the build a trip runs on reads its stops' states so
(`docs/reference/api.md`, **Picking a build**). Every index holds the same states, whichever
region's it is.

`town_points() -> tuple[TownPoint(name, usps, lsad, lon, lat), ...]` puts each town at a point
inside its polygon, shapely's `point_on_surface` (GEOS's interior point: on the polygon, never
outside it, as a centroid can be), in the towns' order (by GEOID), worked out on first use and
kept (0.08 s for the national index's 31,540 towns): the daily route draws its two towns from
them (`docs/reference/api.md`, **The daily route**), as `tools/acceptance.py --places` puts its
points.

On Portland, Powell's → Reed (seed 1) crosses `[OR]` and passes through Portland first (GEOID
4159000, "Portland city"); a straight line from Aloha (45.4943, −122.8671) to Powell's
(45.5231, −122.6765), then to Esther Short Park in Vancouver (45.6270, −122.6727), gives OR then
WA, and Aloha (CDP), Beaverton (city), Cedar Hills (CDP), West Haven-Sylvan (CDP), Portland,
Vancouver (WA).

## Comparisons

`comparison_line(length_m) -> str` compares a route's length with the longest of
`COMPARISONS` it **strictly exceeds**: that thing's "N×" line when the route is at least twice
as long (N to one decimal), else its "Longer than" line; a route no longer than the shortest
thing gets `SHORTER`. Every line names the thing's length in miles with kilometres in
parentheses (`distance_text`: one decimal below 100 with ".0" dropped, whole numbers with
separators from 100).
It is a pure function of the length: no randomness, no state.

| Miles | Thing | "Longer than" line |
|---|---|---|
| 1.7 | the Golden Gate Bridge | Longer than the Golden Gate Bridge, which is 1.7 mi (2.7 km). |
| 2.5 | a lap of the Indianapolis Motor Speedway | Longer than a lap of the Indianapolis Motor Speedway, which is 2.5 mi (4 km). |
| 13.4 | Manhattan | Longer than Manhattan from end to end, which is 13.4 mi (21.6 km). |
| 26.2 | the Boston Marathon | Longer than the Boston Marathon, which is 26.2 mi (42.2 km). |
| 72 | Lake Tahoe's shoreline | Longer than driving around Lake Tahoe, which is 72 mi (116 km) of shoreline. |
| 174 | Portland to Seattle | Longer than driving from Portland to Seattle, which is 174 mi (280 km). |
| 363 | the Oregon coast on US 101 | Longer than the whole Oregon coast on US 101, which is 363 mi (584 km). |
| 1,381 | I-5 | Longer than I-5 from Mexico to Canada, which is 1,381 mi (2,223 km). |
| 2,448 | Route 66 | Longer than driving Route 66 end to end, which is 2,448 mi (3,940 km). |
| 2,790 | New York to Los Angeles | Longer than driving from New York to Los Angeles, which is 2,790 mi (4,490 km). |
| 3,020 | I-90 | Longer than I-90 from Seattle to Boston, which is 3,020 mi (4,860 km). |
| 5,500 | the Great Wall of China | Longer than the Great Wall of China, which is 5,500 mi (8,851 km). |
| 19,000 | the Pan-American Highway | Longer than the Pan-American Highway from Alaska to Argentina, which is 19,000 mi (30,578 km). |
| 24,901 | Earth's circumference | Longer than driving around the Earth, which is 24,901 mi (40,074 km). |
| 238,900 | the distance to the Moon | Longer than the distance to the Moon, which is 238,900 mi (384,472 km). |

The "N×" lines read "2.1× the Boston Marathon, which is 26.2 mi (42.2 km).". A thing whose next one
is less than twice as long never gets its "N×" line (a route that long has passed the next thing):
the Golden Gate Bridge, Manhattan, I-5, Route 66, New York to Los Angeles, I-90 and the Pan-American
Highway (the test derives them from the list). Below 1.7 mi: "Shorter than the Golden Gate Bridge,
which is 1.7 mi (2.7 km). It still counts."

```
    5 km: Longer than a lap of the Indianapolis Motor Speedway, which is 2.5 mi (4 km).
   90 km: 2.1× the Boston Marathon, which is 26.2 mi (42.2 km).
  480 km: Longer than driving from Portland to Seattle, which is 174 mi (280 km).
 5000 km: Longer than I-90 from Seattle to Boston, which is 3,020 mi (4,860 km).
```

## Share image

`render_share_image(data, geometry, route_nodes, route_edges, metrics, places, comparison, *,
baseline_edges=None, objective="distance", palette=CLASSIC) -> bytes` draws a route (its nodes and
edges on its build, along the build's geometry, its document's `metrics`, the objective it was
planned for) as a 1200 × 630 PNG, the Open Graph size, in a theme's colours (**Palettes** below),
from our own data only. The API serves it at `GET /routes/{id}/share.png`, `?theme=<id>` in that
theme's colours, rendered once and cached (`docs/reference/api.md`, **Share image**).

- **The text column** (the left 38 %): the headline in bold, as large as fits the column (at
  most 110 pt), its caption under it, and the comparison line at the bottom, wrapped at 30
  characters (never between a number and its unit) and shrunk if still too wide. Ink colours,
  never the route's. `headline(metrics, objective)` picks the headline and caption:
  - the **ratio of lengths** ("62.4×", "12,345.7×"), "less efficient than the sensible route",
    for every objective but the slowest time;
  - for a route planned for the **slowest time** (its request's `objective` is `"time"`), its
    **time ratio**, the experienced time (driving and delays) over the baseline's, as its
    document's `time_ratio` has it: "slower than the sensible route";
  - for a **round trip**, which has neither ratio (its baseline is 0 m), its length ("299 mi"),
    "to end up where it started", whatever its objective.
- **The map** fills the rest, edge to edge, with **no basemap of any kind**. Its extent is the
  route's bbox with 15 % of its width and height on each side (at least 1 km across), widened to
  the map's shape with x scaled by cos(mean latitude), so distances look the same both ways.
  From the bottom up: the build's roads as light hairlines along its geometry (one edge per road
  unit, those whose bounds meet the extent, first in the build's CRS against the extent's
  outline projected and grown by `NETWORK_MARGIN_M`, 50 m, then in lon/lat; at most
  `MAX_NETWORK_EDGES`, 100,000, as **The network** below says), the state outlines from
  the places index's 20m file in a darker grey (none without an index), the route in red
  (`#e8452c`, the map's) on a white casing, the baseline dashed in grey over it (the sensible
  route through the mess; none for a round trip), a ring at the start and a smaller dot at the
  end on top (a round trip's shows inside its ring). These are classic's colours; a theme's
  are its own.
- **The footer**, bottom right: "worst-route · © OpenStreetMap contributors (ODbL) ·
  Boundaries: U.S. Census Bureau".

**Palettes** (`share_palettes.PALETTES`): one per theme of the frontend, ten, by the theme's
id, classic first, each seven hex colours: the surface (the background, the route's casing, the
baseline's and the footer's halos), the ink (the headline, the comparison, the start's ring and
the end's dot), the secondary (the caption, the footer), the network, the outlines, the baseline
and the route. Classic's (`CLASSIC`, the default) are the image's from before the themes, as
above, so an image without a theme is the same bytes as before. Every other theme's are its
preset's (the frontend's `theme/presets/`): the surface its panel, the ink its ink, the secondary
and the outlines its muted, the network its line (but transit's, `#d4d4d4`, and ledger's,
`#cfcfcb`, a light grey: their line is black, which would bury the map), the baseline its
baseline, the route its route's last colour. The fonts stay DejaVu Sans, whatever the theme. The
module imports nothing heavy, so the API checks a request's theme without matplotlib.

**The network** is found without decoding most of it. A road unit with an end inside the box
(the outline's in the build's CRS, then the extent in lon/lat) meets it: its ends are points of
its line exactly, and `edge_points` gives them as `node_lon`, `node_lat` bit for bit. A unit
whose ends both lie farther than its length from the box on some axis misses it, since every
point of its line lies within its length of either end (`edge_length_m` is the length of the
road's whole shape, which the drawn line simplifies; a metre more covers the decoding's
rounding, half a metre on each axis, and the float32 length). Only the units between are
decoded to tell, then the units drawn, once: the roads a full decode finds, by construction,
which the tests check on a lattice of bent roads and on Portland. When more than 100,000 would
show, it keeps those of T0–T2, then T0–T1, then T0, the first few enough, and if T0 alone is
too many, its 100,000 longest units (the earlier of two the same length), drawn in unit order:
the motorway and trunk stretches between interchanges, so a national map shows the interstates
with the cities as clusters (from Chicago to Miami on the national T1 build, 100,000 of T0's
385,356). It never draws more than the cap, and the same inputs give the same lines.

It draws on a `Figure` with a `FigureCanvasAgg` of its own, never pyplot nor a process-wide
backend, with DejaVu Sans as matplotlib ships it, given to each text by its file's path, and
saves without the PNG's Software stamp: the same inputs give the same bytes (in one environment:
another matplotlib or FreeType may draw a pixel differently). Importing matplotlib may still
build its font manager's cache, a scan of the system's fonts that took 11 s on a cold machine,
once per machine; the API therefore imports this module only when it first renders, never at
startup. matplotlib is not thread-safe, so renders take one lock in turn. `SHARE_IMAGE_VERSION`
(2 since the network's cap, in `api/share_api.py`) names the drawing; the API's cache files
carry it, so it goes up whenever the drawing changes.

**Measured** (Portland, `serve --port 0`, the dev machine, 2026-10-30, a warm font cache):
Powell's → Reed (seed 1, `5ea844bffd40`, 62.4×, 299 mi): 489 kB in 2.0 s for the first image
after the server started (matplotlib's import included), 8 ms from the cache; Powell's → Hayden
Island (seed 3, 52.4×) next, 1.4 s, the OR/WA outline across the Columbia at the top right. In
one process, a render takes 0.34 s once matplotlib and the fonts are loaded.

**With the network's cap** (2026-10-31, the dev machine, load average 6–8): one process with
matplotlib and the fonts loaded draws each route with the drawing before the network's cap
(version 1) and after it (version 2), the median of five renders after a first, with the
places index's state outlines. Portland's build is a453e105582b, Oregon's ea3b18096e8b and the
national us-t1 c4a85b3d36d0; the routes are `worst-route plan --seed 1`, with `--budget 30`
(Powell's → Reed, which then stops at its 5,000 attempts; Medford → Pioneer Courthouse Square,
`--max-iterations 1000000`) or `--tier T1 --budget 60 --max-iterations 1000000` (both national
trips converge). "Units" counts the road units whose bounds meet the map; "network" is the
time `_network` takes to find the lines.

| Route | Units | Lines drawn, v1 → v2 | Render, v1 → v2 | Network, v1 → v2 |
|---|---|---|---|---|
| Powell's → Reed, Portland (248 km, 32.2×) | 25,567 | 25,567, the same bytes | 0.147 → 0.136 s | 25 → 14 ms |
| Medford → Pioneer Courthouse Square, Oregon (4,737 km, 10.7×) | 288,556 | 41,294 (T0–T2), the same bytes | 0.456 → 0.260 s | 210 → 31 ms |
| Chicago → Miami, us-t1 (4,168 km, 1.87×) | 740,676 | 740,676 → 100,000 (the longest of T0's 385,356) | 3.38 → 0.64 s | 704 → 170 ms |
| New York → Los Angeles, us-t1 (5,675 km, 1.26×) | 1,033,412 | 1,033,412 → 100,000 (the longest of T0's 512,708) | 4.80 → 0.64 s | 766 → 169 ms |

The national images shrink from 410 and 295 kB to 310 and 240 kB. The longest units carry
more points than the others (956,653 and 1,159,646 for the two national maps' 100,000), so a
national render costs more than the lines' count suggests: with a cap of 50,000 both took
0.42 s, a sparser network, and drawing the whole of T0 takes 1.9 s from Chicago to Miami and
2.5 s from New York to Los Angeles. A later item: the collection's data limits, which the map's
fixed extent never uses, take about 0.06 s of a national render (`autolim=False` would skip
them).

## Tests

`tests/presentation/` (`support.py` writes the index from the census fixture into a temporary
data directory and holds a route over the API tests' 10 × 10 lattice):

| Test file | What it covers |
|---|---|
| `test_places.py` | the index's rows; each town's point inside its polygon, in the towns' order, worked out once; states and towns in route order, the CDP included, and reversed; a route that comes back into a town listed where it first met it; a point repeated (a zero-length segment) met where it is; a route touching nothing; a touch counting; the state each point lies in (one in each, one on the border, a corner, one in neither); places outside the bbox left out; a state the bbox meets without its places file reported missing; the state's bounds when there is no bbox; the files' columns and CRS; no index when a file is missing; `aux places` on the fixture zipped as the Census ships it (shapefiles), with the latest build's bbox and the warning, and its missing state file; `STATES` (51, unique, every region's state) |
| `test_comparisons.py` | the four lines above; the things in order, each with both lines; a sweep from 0.1 to 500,000 mi reaching every thing, and every "N×" line but those the list makes unreachable (derived from it), deterministic; the boundaries (equal is not longer, 2× exactly, the shorter line); every distance's format |
| `test_share_image.py` | a 1200 × 630 PNG (its IHDR), the same bytes twice, with and without the index (which differ), no Software stamp; each theme's palette: classic's the default, the same bytes twice, the map's corner its surface, its ink and its route among the pixels, ten different images; `headline` for each objective and a round trip; a slowest-time route drawing its own headline, a distance route ignoring its time ratio; a round trip; no baseline; the extent's margin and shape, and its minimum; the network one line per road unit, drawn along a bent geometry, only the units near the map; the roads a full decode finds, on the lattice with every road bent over 243 maps in and around it and on a hairpin met only by its tip, and none decoded to tell for a map holding every road or none; under a cap of 10, thinned to T0–T2, T0–T1, T0, then the longest units of T0 (359 of one length: the first ten; among unequal lengths the ten longest, ties to the lower unit id, a long unit outside T0 left out), the same lines twice |
| `test_share_palettes.py` | the ten themes, classic first, every colour a lowercase hex string, no two palettes alike; classic's the image's colours from before the themes; night's its preset's; transit's and ledger's networks light grey |
| `test_share_image_data.py` (`data`) | Portland: on the map of Powell's → Reed and two smaller ones inside it, the network is the roads a full decode finds |
| `test_fetch_census.py` | the script against a `curl` stand-in on PATH: the URLs and User-Agent, a file without a pinned sum kept with its sum printed, a pinned file whose sum differs deleted (exit 1), a failed fetch leaving no `.part` file and fetching no further (exit 1), a bad FIPS code fetching nothing (exit 2) |
| `test_directions.py` | each step's words on small named graphs (`streets.py`): the town's turns, an interior stop with and without a label (the road going on as "Continue on"), a round trip's arrival, the turn words at the slight and sharp bands' ends, straight on continuing only on a new road, a turn on the same road its own step ("to stay on"), never "to stay on" an unnamed road, US 26 through downtown (a ref joining a name straight on unannounced, then each turn a step), unnamed roads and an alley in words, a ramp of two links toward the motorway's first ref and one the route ends on, a ramp carrying the ref of the road it joins (US 30) still ending, toward that road, an interchange on the ramp or where it meets the motorway adding no second ramp, an interchange without a link a junction like another and a roundabout node (the same road through it cut too), the diamond and roundabout fixtures through the pipeline; planned lattice routes with signals, a round trip included: the ranges tiling the edges, the distances adding up to the length and the times to the driving and delays; names, refs and the compass, half-way bearings clockwise |
| `test_jokes.py` | forty distinct jokes and their placeholders, one turn counted as "1 turn"; the same seed the same jokes, another seed others; over 20 seeds no joke twice, every placeholder filled, the real steps kept in order and numbered again, each joke empty after a driving step, the gaps within their band; a long route spreading nearly all forty to its end; the ratio, the turns before and the step's distance filled in; a round trip without ratio jokes and a route without turns without turn counts; a short route without a joke; the distances' format; `route_directions` with and without jokes |
| `test_stats.py` | fuel and cost from a vehicle (the API's `Vehicle`), null without; states and towns from the census fixture in route order, null without an index; the network fraction over the units within reach, less what the request bans or leaves outside its area, a unit of two edges counted once; the toasts in order with everything known, six without places, a vehicle or delays, never turning one way, one town, one state left out, under 1 %, the items, a round trip with and without a via (its ratio first, then its ending where it began), twelve at most and the items never dropped |
| `test_geojson.py` | the route, the baseline and the stops as features, one point per node along a lattice route, a round trip's null baseline, and the collection from a stored route's parts equal to the planned route's |
| `test_document.py` | `present`'s keys, `with_presentation`'s layout, and the same presentation from a planned route and from its route.json |
| `test_cli_directions.py` | `plan --out` writing the directions (the ones `route_directions` gives), the toasts and the metrics (no fuel, the fixture's states and towns, null without an index) into route.json, which `validate` and `plan --base` still read; `worst-route directions` printing the file's steps with and without jokes, and refusing another graph_version or a file it cannot read |
| `test_directions_data.py` (`data`) | Portland: Powell's → Reed's first step heads along a road at Powell's (West Burnside Street, NW Couch Street, NW or SW 10th or 11th Avenue), every step has words, the driving steps tile the route, there are jokes, and the toasts open with the ratio, six to twelve of them |
| `test_places_data.py` (`data`) | Portland: Powell's → Reed crosses `[OR]` and enters Portland city first; a straight line over the Columbia enters WA and ends in Vancouver. It needs the index `aux places` wrote under `WORST_ROUTE_DATA` beside the build, and is skipped without either |

`tests/api/test_share_api.py` and `tests/api/test_directions_api.py` cover the endpoints
(`docs/reference/api.md`, **Tests**).

**The census fixture** (`tests/fixtures/census/`, hand-written GeoJSON with the Census's columns):
`states.geojson`, two fake states split at lon −122.674 through the lattice (Westland 91 WL,
Eastland 92 EL), standing for both state files; `places_91.geojson` (Westville, a city, at the
lattice's south-west; Faraway, a village, outside it) and `places_92.geojson` (Middlefield, a
CDP, just east of the border; Eastbrook, a town, at its north-east), one file per state as the
Census publishes them.

## Limitations

- **The cache knows the drawing, not the data**: an image is cached under its route id, its
  theme and `SHARE_IMAGE_VERSION`, so a places index written after a route's first render leaves
  that image without outlines until `share/` is cleared. The files cached before the themes
  (`share/<id>-v2.png`, an older server's or a desktop home's) are left behind, never served.
- **Places cover the files present**: a region's towns in a state whose places file was not
  fetched are unknown (`aux places` warns). Portland's build reaches little of Washington (the
  Oregon extract stops just past the Columbia), so a route seldom gets there anyway.
- **The 20m outlines are coarse** at city scale: the OR/WA line across the Columbia is a few
  straight segments. Cartographic files are for display; neither file is used for areas.
- **Every placement is by polygon**: a route that only runs along a town's edge passes through
  it.
- **Directions see a super-junction from outside**: a ramp is named by where it leads, a
  roundabout's exit is not counted, and a turn through an interchange is classed by the roads
  that enter and leave it (`docs/reference/engine.md`, **Turns**), so a cloverleaf's loop ramp
  is a left turn; a motorway exit that the build leaves as link edges reads "Take the ramp
  toward" the road at its end. Names are OSM's, unabbreviated ("Southeast Hawthorne
  Boulevard").
- **One identity straight on is one step**: an identity carried by two names (US 26 as
  "Southeast Powell Boulevard", then "Mount Hood Highway") keeps the first name until the route
  turns, and an identity change the names do not show is not announced. Every turn is a step,
  on the same road too.
- **Prose is in miles**: a joke's distance is in miles and feet with km and m, and the fuel in
  gallons and dollars; the numeric fields are metric.
- **The image is 1200 × 630 only**, in miles (the comparison gives kilometres too); the units
  toggle does not reach it.
- **A national map's cities are thin**: where T0 alone holds more than 100,000 road units the
  image draws its 100,000 longest, the motorways and trunks between interchanges; the primary
  roads are gone and a city's short motorway units and ramps thin to a cluster (from Chicago
  to Miami on the national T1 build, 100,000 of T0's 385,356).
