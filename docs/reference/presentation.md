# Presentation

What the app says and shows about a route once it exists: the states and towns it passes
through, how its length compares with things people know, and its share image. Code:
`src/worst_route/presentation/`, the share image's endpoint `api/share_api.py`
(`docs/reference/api.md`, **Share image**), and the Census files' fetch script
`tools/fetch_census.sh`. The design it implements is `docs/PLAN.md` §4.3 and §7 M4; the
conventions are `CLAUDE.md`. Nothing here plans or changes a route: it reads stored routes, their
builds and the places index. Directions, stats, toasts and jokes are later M4 pieces.

| Module | What it holds |
|---|---|
| `places.py` | `write_places` (the index's writer), `PlaceIndex` (`load`, `states_crossed`, `towns_passed`, `outlines`), `State`, `Town`, `PlacesWritten`, `route_line`, `census_dir`, `places_dir`, `state_files`, `place_files` |
| `comparisons.py` | `COMPARISONS`, `Comparison`, `SHORTER`, `comparison_line`, `METRES_PER_MILE` |
| `share_image.py` | `render_share_image`, `headline`, the image's sizes, thresholds and colours (its version, `SHARE_IMAGE_VERSION`, is the endpoint's, in `api/share_api.py`) |

`config.py` holds `CENSUS_VINTAGE` (2025) and `STATES`: each state's Geofabrik slug (a `Region`'s
`state`) to its USPS and FIPS codes, for the 50 states and DC, which match the Census state
file's NAME, STUSPS and STATEFP.

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
pinned sha256: the two state files and the places of Oregon (41) and Washington (53) are pinned;
a mismatch is deleted (exit 1); a file with no pinned sum is kept and its sum printed, to pin in
the script; a fetch that fails leaves nothing behind (exit 1). A code that is not two digits is
refused before anything is fetched (exit 2).

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
its state's polygon's bounds. Every places file present is read, so a region across a state line
has both states' places (STUSPS says which). It prints what it wrote, and on stderr a warning,
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

**Asking it.** `PlaceIndex.load(region, data_dir=None)` reads the three files (pyarrow and
shapely, no geopandas) into two shapely STRtrees, the states and the region's places, and keeps
the outlines to draw; it returns None when any file is missing, and callers then know no states
or towns and draw no outlines, never an error. For a route's line in lon/lat
(`route_line(data, edges)`, the points `engine.io.line_coordinates` gives):

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
parentheses (one decimal below 100 with ".0" dropped, whole numbers with separators from 100).
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

`render_share_image(data, route_nodes, route_edges, metrics, places, comparison, *,
baseline_edges=None, objective="distance") -> bytes` draws a route (its nodes and edges on its
build, its document's `metrics`, the objective it was planned for) as a 1200 × 630 PNG, the
Open Graph size, from our own data only. The API serves it at `GET /routes/{id}/share.png`,
rendered once and cached (`docs/reference/api.md`, **Share image**).

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
  From the bottom up: the build's roads as light hairlines (one edge per road unit, those whose
  bounds meet the extent; only T0–T2 when more than 50,000 would show), the state outlines from
  the places index's 20m file in a darker grey (none without an index), the route in red
  (`#e8452c`, the map's) on a white casing, the baseline dashed in grey over it (the sensible
  route through the mess; none for a round trip), a ring at the start and a smaller dot at the
  end on top (a round trip's shows inside its ring).
- **The footer**, bottom right: "worst-route · © OpenStreetMap contributors (ODbL) ·
  Boundaries: U.S. Census Bureau".

It draws on a `Figure` with a `FigureCanvasAgg` of its own, never pyplot nor a process-wide
backend, with DejaVu Sans as matplotlib ships it, given to each text by its file's path, and
saves without the PNG's Software stamp: the same inputs give the same bytes (in one environment:
another matplotlib or FreeType may draw a pixel differently). Importing matplotlib may still
build its font manager's cache, a scan of the system's fonts that took 11 s on a cold machine,
once per machine; the API therefore imports this module only when it first renders, never at
startup. matplotlib is not thread-safe, so renders take one lock in turn. `SHARE_IMAGE_VERSION`
(1, in `api/share_api.py`) names the drawing; the API's cache files carry it, so it goes up
whenever the drawing changes.

**Measured** (Portland, `serve --port 0`, the dev machine, 2026-10-30, a warm font cache):
Powell's → Reed (seed 1, `5ea844bffd40`, 62.4×, 299 mi): 489 kB in 2.0 s for the first image
after the server started (matplotlib's import included), 8 ms from the cache; Powell's → Hayden
Island (seed 3, 52.4×) next, 1.4 s, the OR/WA outline across the Columbia at the top right. In
one process, a render takes 0.34 s once matplotlib and the fonts are loaded.

## Tests

`tests/presentation/` (`support.py` writes the index from the census fixture into a temporary
data directory and holds a route over the API tests' 10 × 10 lattice):

| Test file | What it covers |
|---|---|
| `test_places.py` | the index's rows; states and towns in route order, the CDP included, and reversed; a route that comes back into a town listed where it first met it; a point repeated (a zero-length segment) met where it is; a route touching nothing; a touch counting; places outside the bbox left out; a state the bbox meets without its places file reported missing; the state's bounds when there is no bbox; the files' columns and CRS; no index when a file is missing; `aux places` on the fixture zipped as the Census ships it (shapefiles), with the latest build's bbox and the warning, and its missing state file; `STATES` (51, unique, every region's state) |
| `test_comparisons.py` | the four lines above; the things in order, each with both lines; a sweep from 0.1 to 500,000 mi reaching every thing, and every "N×" line but those the list makes unreachable (derived from it), deterministic; the boundaries (equal is not longer, 2× exactly, the shorter line); every distance's format |
| `test_share_image.py` | a 1200 × 630 PNG (its IHDR), the same bytes twice, with and without the index (which differ), no Software stamp; `headline` for each objective and a round trip; a slowest-time route drawing its own headline, a distance route ignoring its time ratio; a round trip; no baseline; the extent's margin and shape, and its minimum; the network one line per road unit, thinned when too many |
| `test_fetch_census.py` | the script against a `curl` stand-in on PATH: the URLs and User-Agent, a file without a pinned sum kept with its sum printed, a pinned file whose sum differs deleted (exit 1), a failed fetch leaving no `.part` file and fetching no further (exit 1), a bad FIPS code fetching nothing (exit 2) |
| `test_places_data.py` (`data`) | Portland: Powell's → Reed crosses `[OR]` and enters Portland city first; a straight line over the Columbia enters WA and ends in Vancouver. It needs the index `aux places` wrote under `WORST_ROUTE_DATA` beside the build, and is skipped without either |

`tests/api/test_share_api.py` covers the endpoint (`docs/reference/api.md`, **Tests**).

**The census fixture** (`tests/fixtures/census/`, hand-written GeoJSON with the Census's columns):
`states.geojson`, two fake states split at lon −122.674 through the lattice (Westland 91 WL,
Eastland 92 EL), standing for both state files; `places_91.geojson` (Westville, a city, at the
lattice's south-west; Faraway, a village, outside it) and `places_92.geojson` (Middlefield, a
CDP, just east of the border; Eastbrook, a town, at its north-east), one file per state as the
Census publishes them.

## Limitations

- **The cache knows the drawing, not the data**: an image is cached under its route id and
  `SHARE_IMAGE_VERSION`, so a places index written after a route's first render leaves that
  image without outlines until `share/` is cleared.
- **Places cover the files present**: a region's towns in a state whose places file was not
  fetched are unknown (`aux places` warns). Portland's build reaches little of Washington (the
  Oregon extract stops just past the Columbia), so a route seldom gets there anyway.
- **The 20m outlines are coarse** at city scale: the OR/WA line across the Columbia is a few
  straight segments. Cartographic files are for display; neither file is used for areas.
- **Every placement is by polygon**: a route that only runs along a town's edge passes through
  it.
- **The image is 1200 × 630 only**, in miles (the comparison gives kilometres too); the units
  toggle does not reach it.
