# Frontend

The single-page app under `frontend/`: a map of the served region on which the user clicks a
start, an end and stops along the way, starts a job, watches the worst route grow live, cancels,
rerolls, makes the route worse, reads its directions and its toasts, and opens a stored route
by link. Vite + TypeScript (strict), no
framework, MapLibre GL JS 6. The design it implements is `docs/PLAN.md` §5; the conventions are
`CLAUDE.md`. It talks to the backend through the API only (`docs/reference/api.md`), and changes
nothing there.

| Module | What it holds |
|---|---|
| `main.ts` | bootstrap and wiring: the store from the URL's state, the panel's parts, the region's bbox, the basemap the server names (`GET /config`), a stored route when the URL names one, clicks on the map, the cancel on `pagehide` |
| `state.ts` | `Store`, the app's state on an `EventTarget`; `Settings`, `Stop`, `RunView`, `targetOf`, `takesTarget` (the targets a mode takes), `modeOf` (the request's objective, item, brand and constraints), `vehicleOf`, `departureOf`; the API's limits (tiers, budget, leash, targets), the vehicle's, and the modes and items it plans |
| `api/types.ts` | the API's shapes, written by hand from `api.md` |
| `api/client.ts` | the fetch wrappers (`getConfig`, `getBrands` and `geocode` among them), `ApiError`, `errorBody`, `subscribe` (a job's events over `EventSource`) |
| `map/basemap.ts` | `basemapParts` (the basemap `/config` names, as sources and layers), `addBasemap`, `EMPTY_STYLE` (**Basemap**) |
| `map/map.ts` | the MapLibre map, its controls, MapLibre's worker and the `pmtiles://` protocol, `fitTo` |
| `map/routeLayer.ts` | `spliceLine` and `RouteLayer`: the baseline, the growing route, the flash (**Live growth**), and the highlight of a step of the directions |
| `map/stopsLayer.ts` | the stop markers and the snapped-position ghosts |
| `stops/stopList.ts` | how a click, a drag or a removal changes the stops (`placeStop`: a click's, and a place search's pick); `stopErrored`, the stops an error names; the list in the panel |
| `stops/search.ts` | `PlaceSearch` (the place search, no DOM, so the tests drive it) and `mountPlaceSearch`, its field, button and results |
| `settings/panel.ts` | the request's settings (the mode with its item or brand, the turns and hard mode, the vehicle and the departure among them; `loadBrands` and `brandOptions`, the brand picker's), the row an error's `field` names, and the units |
| `run/controls.ts` | `JobRunner`, a job's lifecycle (no DOM, so the tests drive it), and the retries without strict and without hard mode; `mountRunControls`, Go, Cancel, Reroll, Make it worse and the stat line; `statLines`, `countsTitle` and `routeLines`, its rows; `awaitingBrand`, which holds the buttons under Brand until the brands are known, and `worseRefused`, which turns Make it worse off under Alphabet |
| `directions/panel.ts` | `StepLocator` (where the steps lie along the route's geometry), `stepRows`, `DirectionsPanel` (the list's rows, a hover's highlight and a click's pan, no DOM) and `mountDirections` (**Directions**) |
| `toasts.ts` | `Toasts`, the rotation (no DOM), and `mountToasts`, the strip under the map (**Toasts**) |
| `share/share.ts` | the URL state, `UrlSync`, `openRoute`, `shareImageUrl`, `geojsonUrl`, the share section (**Links**) |
| `banner.ts` | the error banner, with "Try without strict" or "Try without hard mode" when the refusal says either is why (`retryOf`) |
| `format.ts` | distances (mi/km), durations, ratios, counts, items collected; fuel and its cost, percentages, states by their codes; times of day (`isoWithOffset`, `parseIsoTime`, `formatClock`) |
| `util/geo.ts`, `util/dom.ts` | rounding and bounds; building DOM with text, never HTML |

## Running it

```
cd frontend && npm ci
npm run dev           # the dev server (Vite), proxying /api to WORST_ROUTE_API
npm run build         # the static app in dist/
npm run preview       # dist/ served, with the same proxy
npm run typecheck     # tsc on the app (tsconfig.json) and on vite.config.ts and the tests (tsconfig.node.json), then format:check
npm test              # vitest
npm run format        # prettier --write (format:check: prettier --check, print width 100)
```

The API has no `/api` prefix; the dev server (and `preview`) proxies `/api/*` to
`WORST_ROUTE_API` with the prefix stripped, by default `http://127.0.0.1:8000`, the port
`worst-route serve` takes by default. The coordinator runs both on their defaults. A lane runs
its own pair on free ports:

```
WORST_ROUTE_DATA=<main tree>/data uv run worst-route serve --region portland --port <api> --store <lane>/data/routes.sqlite --geocoder none
cd frontend && WORST_ROUTE_API=http://127.0.0.1:<api> npm run dev -- --port <web> --strictPort
```

No agent calls Nominatim: a lane's `serve` runs with `--geocoder none` (the search then shows
`GEOCODER_DISABLED`), or with `--nominatim-url` pointed at a stub of its own that answers
`/search` with a jsonv2 body such as `tests/fixtures/geocode/nominatim-portland.json`.

Prettier formats everything under `frontend/` but the recordings (`.prettierignore`); the
gate runs its check as part of `typecheck`.

`dist/` expects `/api` to reach the API, as the dev server and `preview` arrange; public
hosting needs the same from its web server (M5). The bundle is about 1.2 MB minified (MapLibre
is most of it), 330 kB gzipped, and MapLibre's worker a module of its own, 0.5 MB, in
`dist/assets/`: MapLibre 6 looks for its worker beside its own module, which a bundle (or Vite's
pre-bundled dependencies in the dev server) does not keep, so `map.ts` imports it as an asset
(`maplibre-gl/dist/maplibre-gl-worker.mjs?url`) and names it with `setWorkerUrl`.

## Using it

- **Stops.** On load the map fits the region's bbox (`GET /regions`; the server serves one
  region). The first click sets the start, the second the end; later clicks add stops along the
  way, inserted before the end. A marker is numbered and coloured by its role (start green, via
  blue, end red) and dragged to move; the list shows each stop's coordinates and a remove button.
  A stop keeps its coordinates at 6 decimals, so the request and the link that reproduces it
  carry the same numbers. **Searching for a place** is the other way to place a stop: a field
  and a Search button under the list's heading. Nothing is asked while typing (Nominatim's
  policy forbids type-ahead); the button or Enter sends the text to `GET /geocode` (the server's
  proxy, cached and limited to a request a second, `docs/reference/api.md`, **Geocoding**), and
  up to five places in the region are listed under the field by their names ("No places found."
  when there are none; a place's OSM tag is its tooltip). Picking one places the next stop
  exactly as a click there would (`placeStop`: the start, the end, then stops along the way, at
  6 decimals), and clears the field; the place's name is not kept with the stop, as a click has
  none. The search is locked with the stops while a job runs, and until `/regions` has answered. Once a job has snapped them (its `snapped` event, or a stored route's
  stops), the list shows each stop's node, how far it moved and the API's note, and the map a
  dashed ghost dot where it went (the note as its tooltip). Editing the stops forgets the route
  shown and the snaps. Stops and settings are locked while a job runs. Under the alphabet route
  a second hint says "start and end only: the alphabet route takes no stops along the way" (the
  API refuses a stop along the way).
- **Settings.** The seed (empty: random, picked by the server), the time budget (1–120 s), the
  tier (`auto`, `T0`–`T4`) and the leash (empty: auto, else up to 150 km); once a job has said,
  what `auto` became ("auto was T4", "auto was 3.6 km"). The inefficiency target is "as bad as
  possible", "a ratio" to the sensible route (× it, more than 1; 20 by default) or "a drive
  time" (9 h by default), typed as `9h`, `90m`, `2h30m`, `45s` or a bare number of minutes, at
  most 7 days; typing a number picks its kind, and each kind keeps its number while another is
  picked. A ratio target for a round trip is the API's `INVALID_REQUEST` (`field: "target"`),
  in the banner. The units (miles, kilometres) change what the panel shows; the API's numbers
  are metric.
- **Vehicle.** Two numbers under the target: miles per gallon (24 by default, 5–100) and the gas
  price in dollars a gallon (3.89 by default, 0.50–20); a value outside them is put back. Every
  request carries them as its `vehicle` (`{mpg, gas_price_usd}`), so every stored route has its
  extra fuel and its cost. An `INVALID_REQUEST` naming `field: "vehicle"` highlights the row.
- **Departure.** A date and time (`datetime-local`, the browser's local time) under the
  vehicle, empty by default ("empty: now"). Every request carries it as its `departure`, an ISO
  8601 time with the browser's offset to the second (`2026-10-31T09:00:00-05:00`), or, when
  empty, the time Go (Reroll, Make it worse) is pressed; the document's `arrival` follows from
  it. A departure that came with a link or a stored route keeps its own offset until it is
  changed (the field shows its clock there). `field: "departure"` highlights the row.
- **Mode and Turns.** The Mode row, above the target, picks the objective: "Longest"
  (`distance`, the default), "Slowest" (`time`: the driving and the waits at signals and stop
  signs), "Collect" (`collect`), which shows a list of what to collect: toll booths, level
  crossings, roundabouts or bridges (`toll_booth`, `level_crossing`, `roundabout`, `bridge`;
  bridges by default; the pick is kept while another mode is chosen), "Brand" (`brand`, the
  pilgrimage: the most locations of a brand), which shows a list of the region's brands, or
  "Alphabet" (`alphabet`: named roads whose letters run A, B, C, ...). The brands come from
  `GET /brands?region=`, asked once the region is known and kept in the store, in the API's order
  (the most located first), each named with its locations ("Starbucks (126)"); the first is
  picked unless the settings already name a brand the region has (a link's, or a stored
  route's), and the pick is kept while another mode is chosen; a region without brands has the
  Brand mode disabled. Until they have answered, the picker is disabled and reads "loading…"
  ("brands unavailable" when the request fails, its error in the banner), and under Brand Go,
  Reroll and Make it worse stay disabled (`awaitingBrand`: until the brand set is one of the
  region's), so no request goes without a brand; the other modes run meanwhile. A
  collect or brand run takes no ratio target (the API refuses one: it counts items or visits):
  under Collect or Brand the ratio is disabled, with the hint "a collect run takes a drive-time
  target only" or "a brand run counts visits: a drive-time target only". The alphabet route
  takes no target at all: under Alphabet the whole target is disabled, with the hint "the
  alphabet route takes no target". Picking a mode that refuses the target chosen makes it "as
  bad as possible" (`takesTarget` says which targets a mode takes, for the panel and for links).
  The Turns row is "Any" (the default), "Avoid left
  turns" (`no_left`: each left turn, and U-turn, costs the route a kilometre's worth of its
  score, or 90 s under the slowest time) or "Avoid right turns" (`no_right`), and a "strict"
  checkbox (none at all), enabled only when a side is chosen, with the hint "strict rarely
  finds a route on a street grid: the route may not exist"; picking "Any" unticks it. Under them
  a "Hard mode" checkbox (`hard`: each road in one stretch, never driven again once left, which
  its tooltip says, and a note under it while ticked), independent of the side and of strict.
- **Run.** Go posts the stops, each stop along the way with the `label` its marker's number
  gives it ("Stop 2", "Stop 3", …, which the directions say: "Arrive at Stop 2; continue"; the
  start and the end go without one, so the API keeps its own "Arrive at your destination" and
  "Arrive back where you started"), and the settings (the target as
  `{kind: "ratio", value}` or `{kind: "duration", seconds}`; the `objective`; the `item` only
  under collect; the `brand`, a brand's id, only under brand; the `constraints` `{no_left,
  no_right, strict}` only when a side is avoided or hard mode is on, with `hard: true` in them
  only when it is on and `strict` only with a side, the key left out otherwise, as the API's own
  serializer does; the `vehicle` and the `departure`), and its 202's seed goes into the
  settings. Reroll posts the same without a
  seed, so the server picks one. Make it worse posts the shown route as `base_route_id`, with
  the budget, mode, turns, target, vehicle and departure from the panel (a route opened by link
  has put its own in the panel first), leaving the stops, tier and leash to the base route
  (the API's rule), and the seed after the last one tried on
  that route (its own seed at first). Under Alphabet, Make it worse is disabled, its tooltip "the
  alphabet route cannot be continued; pick another mode to make it worse" (`worseRefused`): the
  API continues no route under the alphabet route, while another mode may continue any route,
  an alphabet route too. The settings' seed follows the route shown: when the
  job's `done` names the base route (nothing worse was found), the route and its seed stay, and
  the next press tries the seed after; with a target, the base route may already meet it ("already
  within the target"), or no route nearer it was found. A target below the base route's value
  shrinks it (the API's rule). Cancel sends `DELETE`; the stream then ends with `done`
  (the route so far, stored as `cancelled`) or `error`. Leaving the page sends `DELETE` with
  `keepalive` for a running job, and for one whose POST is in flight as soon as the POST is
  answered (no stream is opened for it).
- **The stat line**: the ratio, large (under the slowest time, once the route is stored, the
  ratio of experienced times, "16.9× slower", from the document's `time_ratio`, as in `done`;
  while the run goes it is the lengths' ratio); the length and drive time; under the slowest
  time, the drive and the delays ("drive 4 h 8 min + delays 1 h 59 min"), live from each
  `progress` event's `delay_s` and then the stored route's; the objective's counter, live from
  each `snapshot` and `progress` event and then from the stored route's document: under collect,
  the items collected ("24 bridges", from `items`), under brand the locations visited of those
  within reach ("17 of 19 Starbucks", from `brand`), and under the alphabet route its letters,
  how many and the laps of the alphabet they run, as the CLI writes them ("letters: 15 (A–O)",
  "letters: 1 (A)", "letters: 0", and past Z "letters: 29 (A–Z, A–C)", never "A–A"; from
  `alphabet`), the first road of each letter one a line in the row's tooltip ("A: Southwest
  Alder Street", `countsTitle`); once stored, the turns ("277 left ·
  282 right", "no lefts" or "no rights" when the side avoided has none and the route no U-turn,
  which either constraint counts too); for a ratio or duration
  target, the target while the run goes ("target 9 h") and what it achieved once done ("target
  9 h, achieved 8 h 50 min, within ±2 %", from `done`'s `metrics.target`, or the document's for
  a route opened by link); the attempts (the attempt of the last splice while running, the
  route's attempts once stored; the alphabet route's count its restarts), the elapsed time, how
  the run is going or ended
  (`getting worse…`, `reconnecting…`, `budget`, `converged`, `target reached`, `cancelled`,
  `timeout`, `failed`), the seed, and, when Make it worse found nothing, "no worse route found
  with seed N; try again or Reroll". The rows follow the run's own mode (what it asked, even
  when Make it worse found nothing and shows the base route), or a route opened by link's
  request. Under them, once the route is stored, five rows from its document (`routeLines`): when it
  arrives, the day and the minute at the departure's offset ("arrives Sat 14:57", the whole time in
  the tooltip; `formatClock`); the towns it passes through, the first two and how many more ("towns:
  Beaverton, West Slope +14", every name in the row's tooltip); the states by their USPS codes
  ("states: OR, WA", the names in the tooltip); the fuel it burns beyond the sensible route's and
  its cost ("fuel: +6.8 gal · $28.83"); and its share of the roads within reach ("roads used: 29 %
  of those within reach", "under 1 %" as the toasts say). A row the document has nothing for (a
  server without the places index has no towns or states; one stored without a departure has no
  arrival) is left empty, and all five are while a job runs.
- **Directions** (`directions/panel.ts`). A collapsible section under the run's, empty (with
  the hint "The directions come once a route is stored.") until the stored route's document is
  fetched, on `done` or by a link; it lists the document's `directions` with no other request,
  in a list that scrolls (45 % of the window's height). A driving step reads "Turn left onto X
  · 0.3 mi · 2 min", its distance in the units set and its time as the stat line writes times;
  a stop ("Arrive at Stop 2; continue") and the arrival are bold, a joke is in italics, muted,
  without a number of its own: the numbers are the document's, so they skip where a joke is.
  Hovering a driving step, or focusing it, draws its edges over the route in blue (a
  `directions-highlight` line layer on top of the route layer's); a stop, the arrival and a
  joke drive nothing and draw nothing. Clicking a step, or Enter or Space on it, pans the map
  to where it starts. The list is built in a document fragment (2,026 steps in 41–69 ms) and
  its listeners are on the list, none on a step; one step is in the tab order at a time (the
  one last focused, at first the first), and the arrow keys move along the steps, jokes
  skipped.

  The document gives no offsets of its edges in its geometry, nor does `/geojson`, and a route
  opened by its link has no stream whose node indices could say where its nodes are, so
  `StepLocator` places the steps by distance: the driving steps cover the route's edges in
  order (`edge_range`, half-open) and their `distance_m` add up to its length. The geometry is
  measured segment by segment on the WGS84 ellipsoid at each segment's middle latitude and
  scaled to the steps' total (the build measures its edges in its projection, UTM on Portland,
  whose scale the scaling takes out), and each driving step ends at the geometry's point nearest
  its distance past the point where the step before it ended (at least one point on); a step's
  line is the geometry's points from where it starts to where it ends, so the driving steps'
  lines cover the geometry end to end. Each step starts again from a point, so the error does
  not grow along the route: on four Portland routes of 70 to 513 mi (the directions recording
  among them), 99.9 % of the steps' ends are their nodes exactly and the rest within 0.73 m, a
  point next to the node. Cutting the line at the sum of the distances from the route's start
  instead, as the first version did, drifted with length: 2.1 m at the median, 5.2 m at the
  95th percentile and 7.2 m at most on a 299 mi route (the review's), 15.2 m at most on a
  513 mi one.
- **Toasts** (`toasts.ts`). A strip under the map (not over it; it keeps its height, so the map
  does not resize) shows one line at a time, the next every 4 s. While a job starts and runs:
  "Finding the worst way. Please hold.", "Rejecting good ideas…", "Still not repeating a road."
  and the live ratio, "Currently 17.2× worse", four slots in a fixed cycle, round again. The
  ratio is the run's own, from its snapshots and `progress` (its number follows them while
  shown; a run starting shows none of the last one's), and its slot is skipped while the run has
  none (before its first snapshot, or a round trip), so the line shown never jumps when the
  ratio comes. Once the
  route is stored (or opened by a link), its document's `toasts`, from the first, in order,
  looping. The pointer over the strip holds the line shown; a click shows the next one at once
  and starts the 4 s again. Nothing is shown while no job runs and no route is shown.
- **Errors.** Every API error shows its `code` and `message` in a banner over the map, closed by
  its × or the next action (a place search's too: `RATE_LIMITED`, "the geocoder is busy, try
  again in a few seconds", when the server's slots are taken; a search closes the banner first); an error with a `stop_index` (`STOP_OUTSIDE_REGION`,
  `NO_NODE_NEAR`, `STOP_UNREACHABLE_WITHOUT_REPEAT`, `INFEASIBLE_TURN_CONSTRAINT`) highlights
  that stop in the list and on the map, one whose `field` is `stops` (the alphabet route's
  refusal of a stop along the way, "the alphabet route takes a start and an end") every stop
  along the way, and an error whose `field` is `target`, `item`, `brand`, `constraints` or
  `vehicle` (`INVALID_REQUEST`) highlights the How bad, Mode, Turns or Vehicle row. A strict turn
  constraint under which the trip cannot be driven (`INFEASIBLE_TURN_CONSTRAINT`, refused by
  the `POST`) shows the server's message ("... under the strict no-left constraint; try without
  strict") and a "Try without strict" button, which unticks strict and sends the refused
  request again without it: the same Go, Reroll or Make it worse, with its seed (none for a
  Reroll, so the server picks one) and its base route (so a refused Make it worse continues the
  route shown), hard mode kept. A refusal hard mode is why gets a "Try without hard mode" button
  the same way (`retryOf` picks the button): a leg the seed finds no way to build under it
  (`STOP_UNREACHABLE_WITHOUT_REPEAT`, its message saying "under hard mode", refused by the
  `POST`: "the seed found no way from stop 0 to stop 1 under hard mode, ...; try without hard"),
  or a Make it worse whose base route comes back to a road (`BASE_ROUTE_MISMATCH`, "the base
  route comes back to OR 99E at edge 40, which hard mode refuses"). It unticks hard mode and
  sends the refused request again without `hard`, its seed and base route kept, its turn
  constraint kept and its `constraints` left out when no side is avoided. The client adds codes of its own: `NETWORK` (the server cannot be reached),
  `HTTP_<status>` (a refusal without the API's body, as a proxy's 502), `BAD_RESPONSE` (a
  success that is not JSON, as a page where the API should be; the message is its first 80
  characters), `STREAM_LOST` (the event stream closed and the browser gave up reconnecting) and
  `CLIENT_ERROR` (an error of the app's own, with its message). Every error ends the run with
  the controls unlocked: a request refused before its job exists keeps what was shown, and an
  error while following a job closes its stream (and, for `CLIENT_ERROR`, cancels the job).

## State

`Store` holds `{region, stops, settings, run, route, error, brands}` (`brands`, the region's,
null until `GET /brands` has answered). A module renders from it with
`watch(keys, listener)` (called at once, then after every `update` that touches one of the
keys) and changes it with `update(patch)`, `updateRun` or `updateSettings`. `run` changes up to
ten times a second while a job runs, so the stop list, the markers and the settings compare what
they show before rebuilding. The route's geometry is not in the store: it lives in
`RouteLayer`.

## Live growth

`RouteLayer` has four GeoJSON sources: the baseline (grey, dashed), the route (with
`lineMetrics: true`, drawn by a `line-gradient` on `line-progress` from yellow at the start to
dark red at the end, over a white casing), the flash (cyan), and on top the directions'
highlight (blue, `highlight(line)`, **Directions**). `line-gradient` cannot be
combined with a dash pattern, hence the separate baseline layer.

The route is kept as the stream sends it (`api.md`, **Applying the stream**): its `[lon, lat]`
points and `index`, the position among them of each route node. `subscribe` calls, per event:

- `snapped`: each stop's snap, and the resolved tier and leash;
- `baseline`: `setBaseline(geometry)` (none for a round trip);
- `snapshot`: `setSnapshot(geometry, node_index)`, which replaces points and index, with no
  flash, then the numbers;
- `progress`: `applySplice(i, j, coords, node_index)` for each splice, in order, then the
  numbers; each splice is flashed for 400 ms;
- `done`: `GET /routes/{id}`, then `setFinal(geometry)`, the document's full geometry (the
  drawn line until then is partly simplified, as snapshots are), the final numbers, the
  directions and the toasts, the URL's `?route=`, and the camera fitted to the route;
- `error`: the banner, and the run marked failed.

`applySplice` is `spliceLine`, a pure function of the line:

```ts
const a = line.index[i];
const b = line.index[j];
const shift = coords.length - (b - a + 1);
points = line.points.slice(0, a).concat(coords, line.points.slice(b + 1));
index = line.index.slice(0, i).concat(nodeIndex.map((k) => a + k), line.index.slice(j + 1).map((k) => k + shift));
```

Changes are drawn at most once per animation frame: a `progress` event's splices cost one
`setData` of the route, and expired flashes are cleared by a timer.

**The alphabet route** grows differently (`api.md`, **Events**): its better routes are whole new
routes, not splices of the last, so each comes as a `snapshot`, which replaces the line (the
route drawn jumps to the new one, nothing flashed), then a `progress` event with its numbers and
no splice; the ratio, the letters and the toasts' live ratio follow them, down as well as up (a
route with more letters may be shorter). `done` draws the stored route as for any run.

**The stream.** `subscribe` opens an `EventSource` on `/api/jobs/{id}/events`. When the
connection drops, the browser reconnects by itself (about 3 s later in Chrome) with
`Last-Event-ID`, and the server resumes with a `snapshot` carrying the id of the latest event
that changed the route, which replaces the line; the stat line says `reconnecting…` meanwhile.
The server's `error` event and `EventSource`'s own connection error share a name: the first is a
`MessageEvent` with data, the second a plain `Event`. The stream is closed on `done` and on the
server's `error`, since an `EventSource` would otherwise reconnect when the server ends it; one
the browser closes for good (an answer that is not a stream, as a 404 for a job forgotten) is
`STREAM_LOST`.

## Basemap

The server names the basemap (`GET /config`, `docs/reference/api.md`, **Basemap**; its
`WORST_ROUTE_BASEMAP`), and `map/basemap.ts` draws it. The map starts with nothing drawn
(`EMPTY_STYLE`); once it has loaded, the route's layers are added, and once `/config` has
answered, `addBasemap` adds the basemap's sources and layers below them (before the first layer
the map has), its fonts (`setGlyphs`) and icons (`setSprite`) when it has labels. If `/config`
fails the banner shows the error and the map has no basemap: it never falls back to another,
and a `pmtiles` answer without its file draws none either (and logs an error on the console).

- **`raster`** (the server's default): OpenStreetMap's raster tiles
  (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`, desaturated so the route stands out) with
  the attribution "© OpenStreetMap contributors", at hobby volume, as the tile usage policy
  allows locally; the browser's own `User-Agent` and `Referer` are what the policy asks for.
  MapLibre GL JS 6 fetches only the tiles in view (it has no prefetch option) and nothing is
  cached offline. The policy rules these tiles out for a public site.
- **`pmtiles`**: Protomaps' vector tiles from one file the server holds, read through
  `pmtiles://` (the `pmtiles` package's `Protocol`, registered in `map.ts` with `addProtocol`)
  by HTTP byte ranges at `/api/basemap/<region>.pmtiles`; drawn by `@protomaps/basemaps` in its
  `grayscale` flavor, muted so the route stands out, with labels (`layers(…, {lang: "en"})`) when
  the server has their fonts and icons (`glyphs` and `sprite`) and without them otherwise, so a
  deployment without `fetch_basemap.sh --assets` still has a basemap. The labels ask for four
  fonts, the ones the script fetches (`basemap.test.ts` holds the style to them). The source's
  attribution is "Protomaps © OpenStreetMap", linked, the app's own string: never a tileset's
  metadata, whose HTML the attribution control would render.

The URLs are made absolute under the page's origin and `/api` (MapLibre needs absolute ones,
and the templates' braces must stay unencoded). Below the panel a footer credits the data: "Road
data © OpenStreetMap contributors (ODbL) · Towns and states: U.S. Census Bureau", linked; the
basemap's own credit is in the map's attribution control. Agents verifying the app block the
tile server (**Seeing it**), so their screenshots show the route on a blank background with
`raster`, or on a basemap of their own making with `pmtiles` (**Measured**).

## Links

The URL carries the request:
`?seed=&budget=&tier=&leash=&target=&mode=&turns=&strict=&hard=&vehicle=&departure=&stops=lat,lon;lat,lon;…`,
the target as `max`, `ratio:<x>` or `duration:<seconds>`, the mode as `distance`, `time`,
`collect:<item>` (`collect:bridge`), `brand:<id>` (`brand:229`, a brand's id, digits only) or
`alphabet`, the turns as `left` or `right` and `strict=1` (read only with a side), hard mode as
`hard=1`, the vehicle as `<mpg>,<gas price>` (`vehicle=30,4.25`, both within the panel's limits), the
departure as an ISO 8601 time with its offset, encoded (`departure=2026-10-31T09%3A00%3A00-05%3A00`;
a day the month lacks is not one); the mode, the turns and the vehicle are written only when not the
default (Longest, Any, 24 mpg at $3.89; a pilgrimage only once its brand is known), hard mode and
the departure only when set, so a distance link reads as before. A target the mode refuses reads
as "as bad as possible": a ratio beside `mode=collect:…` or `mode=brand:…`, any beside
`mode=alphabet`. A brand id the region's brands lack is replaced by the first of them once
`/brands` has answered. The URL is rewritten (`history.replaceState`, by
`UrlSync`) whenever the stops or settings change, so a link reproduces the request (Go with the
same seed plans the same route on the same build). A value that does not parse, or lies outside
the API's limits, is ignored and the default kept. Once a route is shown, the URL
also carries `route=<id>`; the share section offers `…/?route=<id>` with a copy button, and an
"Open share image" link to `/api/routes/<id>/share.png` (`shareImageUrl`), the server's PNG of the
route (`docs/reference/api.md`, **Share image**), opening in a new tab (`rel="noopener"`), and a
"Download GeoJSON" link to `/api/routes/<id>/geojson` (`geojsonUrl`), the route, its baseline
and its stops as a FeatureCollection, saved as `worst-route-<id>.geojson` (the `download`
attribute, the proxy keeping it on the page's origin).

`?route=<id>` opens a stored route without running anything (`openRoute`): `GET /routes/{id}`
and `GET /routes/{id}/geojson` (for the baseline), the stops and settings from the route's own
request (its seed, budget, tier, leash, target, objective, item, brand, turn constraints, hard
mode, vehicle and departure; the panel keeps its item and its brand under another objective, its
vehicle when the request has none or one outside the panel's limits, as the URL's is, and its
departure when the request has none;
for a "make it worse" route, the base route's stops),
the snaps from its stops, the route drawn whole, the camera fitted to it, its numbers in the
stat line, its directions and its toasts, from which Make it worse and Reroll work as after a
run (an alphabet route's link sets Alphabet, under which Make it worse is off: another mode
continues it). The URL is left as it
came until the route has opened; then it is written whole. A link that does not open (a route
not stored, a build gone) keeps its `route=` and shows the API's error in the banner; the URL
follows the app again from the next change. The units are not in the URL.

## Tests

`npm test` (vitest, in Node; the gate runs it after the build):

| Test file | What it covers |
|---|---|
| `map/routeLayer.test.ts` | `spliceLine` on hand-made lines (a longer and a shorter detour, either end, the input left alone); each recording replayed: it ends with the stored route's node count, every node where the job's final snapshot has it (whose stop positions are the route's), every drawn point a point of the stored geometry in order with the same ends; and at every snapshot after splices (the max-mode recording's after its 50th `progress`), the replayed nodes equal the snapshot's (the alphabet recording's snapshots follow none); the alphabet recording through a `RouteLayer` on a stand-in map: each snapshot drawn whole, no flash ever, the stored geometry drawn at the end |
| `api/types.test.ts` | each type's runtime spec, which the compiler holds to the type's keys; the specs against `api.md` (its JSON request example, and the field lists of the events, `done`'s metrics, `/regions`, the 202, the job status, the route document, `/routes/{id}/directions`, `/brands`, `/geocode` and `/config`) and against every payload of the seven recordings; the ratio recording's target reached in `done` and the document, its round-trip refusal; the slowest-time recording's time ratio, delays and turns in `done` and the document, its refusal of both turn constraints; the collect recording's bridges counted in every `progress`, in `done` and the document, its refusal of a ratio target; the directions recording's vehicle, fuel and cost, states and towns, its steps numbered from 1 with the driving steps' ranges covering the route's edges in order and adding up to its length, its first toast, its refusal of a vehicle of 0 mpg; the brand recording's visits never falling in its `progress` events (8, 13, 14 of 19), `done` and the document agreeing, its refusal of a ratio target; the alphabet recording's better routes, each a snapshot then a `progress` with no splice, the same metrics and a higher score, from "A" to "ABCDEFGHIJKLMNO", `done` and the document agreeing, its refusal of a stop along the way (`field: "stops"`); a payload that does not fit is caught |
| `map/basemap.test.ts` | `basemapParts`: OSM's raster tiles for `raster`; nothing (an error on the console, never OSM's tiles) for `pmtiles` without its file; Protomaps' tiles from the server for `pmtiles`, its `pmtiles://` URL absolute under `/api`, the attribution the app's own, no symbol layer without the server's fonts; with them the fonts' and icons' URLs absolute with the template's braces kept, and the labels asking for no font but the four `tools/fetch_basemap.sh` fetches; `addBasemap` on a stand-in map: the fonts, icons and sources set, then every layer before the first layer the map already had (the route's), or on top of an empty map |
| `run/controls.test.ts` | `JobRunner` with `fetch` and `EventSource` stubbed: a TypeError from the client and an event the app cannot handle end the run as `CLIENT_ERROR` with the controls unlocked (the job cancelled), the server's `error` closes the stream; Make it worse keeps the route and its seed when nothing worse is found and tries the next seed, follows a worse route's seed; Go and Reroll send the settings' seed, then none; leaving the page cancels a job whose POST is in flight once it is answered, a running one at once; a ratio and a drive-time target sent, shown while running and with what was achieved (`target reached`), and Make it worse whose base already meets its target; the objective, the item only under collect and the constraints only with a side sent (and not by default), the brand only under brand, the alphabet route with no item nor brand, Brand waiting for the region's brands with the brand set among them (none, a link's before they answer, one they lack, an empty list) and no other mode waiting, hard mode's `hard: true` alone or beside a side (the key left out when off, strict sent only with a side), Make it worse in the mode set (a pilgrimage under hard mode, its whole body); the slowest-time recording replayed (the lengths' ratio while running, then the time ratio, the drive and delays, the turns) and the collect recording's bridges counted live, then the stored route's; the run's mode shown when Make it worse in another finds nothing; "no lefts" and "no rights" (not with a U-turn); `INFEASIBLE_TURN_CONSTRAINT` as the error, and the refused request sent again without strict: a Go with its seed, a Make it worse on the same route with its seed, a Reroll with none, hard mode kept; the counter row's "17 of 19 Starbucks" and "letters: 23 (A–W)", "letters: 1 (A)", "letters: 0", the laps past Z for 26, 27 and 29 letters ("(A–Z)", "(A–Z, A)", "(A–Z, A–C)"), the roads one a line in its tooltip, nothing under another mode; Make it worse off under Alphabet with its reason, and in no other mode; the brand recording's visits counted live (1, 8, 13, 14 of 19), then the stored route's; the alphabet recording replayed: every snapshot drawn, no splice, the ratio and letters following each better route (1.1× and 1, 1.0× and 2, ...), the stored route drawn, its fifteen roads in the tooltip; a refusal under hard mode sent again without it: a Go with its seed and no constraints left, a Make it worse with its base and seed and its strict side kept, a Reroll with none, and a Make it worse whose base hard mode refuses (`BASE_ROUTE_MISMATCH`) on the same route with the same seed; each stop along the way labelled as its marker is numbered ("Stop 2", "Stop 3"), the start and the end without a label; the vehicle set sent by Make it worse, Go and Reroll (the default in Make it worse's whole body); the departure set sent, or, empty, the time Go is pressed at the browser's offset (on a fake clock); "arrives Sat 17:42" from a document's arrival at its own offset, nothing without one; the directions recording replayed: no route rows while it runs, then its towns, states, fuel and share of the roads; the first two towns and how many more with every name in the tooltip, two states by their codes, no fuel without a vehicle, "under 1 %", and nothing from a document without them |
| `directions/panel.test.ts` | on the directions recording: a row per step classed by its kind, numbered as the document numbers them (none for the 40 jokes, the numbers skipping them), a driving step's distance and time in miles and kilometres, the arrival's and a stop's rows with none; `StepLocator`: the driving steps' lines ending on their first and last nodes (more than 99 % exactly, all within a metre; the job's last snapshot gives where the nodes are) and covering the geometry end to end, a step given at least one segment however short its distance, no line for a step that drives nothing, every step starting where it is; `DirectionsPanel` with a stand-in map: the rows of the route shown and none without one, the units followed, a hover highlighting the right edges, from node to node (once for the same step), a joke and none highlighting nothing, a click panning to the step's start, the highlight forgotten when the route changes, a document without directions listing nothing |
| `settings/panel.test.ts` | the brand picker with `fetch` stubbed: one `GET /api/brands?region=`, the options in the API's order named with their locations ("Starbucks (126)", "(1,234)"), the first picked when the settings name none, the list put in the store, a brand the region has kept and one it lacks replaced, a region without brands leaving the settings alone |
| `banner.test.ts` | `retryOf`: "Try without strict" for `INFEASIBLE_TURN_CONSTRAINT`, "Try without hard mode" for `STOP_UNREACHABLE_WITHOUT_REPEAT` under hard mode and `BASE_ROUTE_MISMATCH` naming hard mode, nothing for the same codes without it (a base route a strict constraint refuses) nor for another refusal |
| `stops/stopList.test.ts` | `stopErrored`: the stop of an error's `stop_index`, every stop along the way for `field: "stops"`, none otherwise |
| `toasts.test.ts` | `Toasts` on fake timers: nothing and no timer while idle; the three waiting lines and the live ratio every 4 s while a job starts and runs (the ratio following `progress` while shown, round again), the ratio's slot skipped without one, and the line shown kept when the ratio comes; none of the last run's ratio while the next one's POST is in flight (and the route's back when it is refused); the stored route's toasts from the first, in order, every 4 s, looping (ten toasts, so ten periods come round); held under the pointer, a click moving on at once and starting the period again; nothing once the route is forgotten, nor for a document without toasts |
| `api/client.test.ts` | the API's error bodies, `BAD_RESPONSE` for an HTML success, `HTTP_<status>`, `NETWORK`, `CLIENT_ERROR` |
| `stops/search.test.ts` | `PlaceSearch` with `fetch` stubbed and a real store: nothing asked while typing, one `GET /api/geocode?q=&region=` on Search, nothing for a blank text or before the region; the candidates listed, an empty answer said; a pick placing the stop exactly as `placeStop` (a click) does, as the start and as a stop before the end, and clearing the field; nothing placed or asked while a job runs; a 429 in the banner, and a search closing it |
| `share/share.test.ts` | the URL state round trip (every field, `auto`, seeds 0 and 2⁵³ − 1, clicked coordinates exactly), values it ignores, the app's state to the URL and back; a `?route=` link at startup: the URL untouched until the route opens, then written from its request, and kept, with the banner, when it does not open; targets in the URL, values ignored, and a ratio-target route opened by link; every mode and turn setting round-tripped, values ignored (strict without a side among them), a ratio beside a collect mode read as max, the defaults left out, the settings given back; a brand (`brand:229`, `brand:0`), the alphabet route and hard mode round-tripped with every turn setting, malformed brands ignored (none, a name, negative, a fraction, an exponent, a space, beyond 2⁵³ − 1), `alphabet:1` and a `hard` other than 1 ignored, a ratio beside a brand and any target beside the alphabet route read as max, a pilgrimage whose brand is not known yet written without a mode, the brand kept under another mode; a slowest-time and a collecting route opened by link (mode, item and turns into the settings, the URL and the stat line, the panel's item kept), the recorded pilgrimage and alphabet route (`mode=brand:229` and "14 of 19 Starbucks", `mode=alphabet` and "letters: 15 (A–O)" with its roads); a pilgrimage, an alphabet route and a hard-mode route (alone and beside a side) opened by link, hard mode unticked by a route without it; the share image's URL; the vehicle round-tripped, at its limits, the default left out, values ignored; a route opened by link putting its request's vehicle into the settings and the URL (the default not written), and one without a vehicle, or with one outside the panel's limits, keeping the panel's; the departure round-tripped with its offset (encoded), what is not an ISO time with an offset ignored, a route's link putting its request's departure into the settings and the URL, and one without keeping the panel's; the GeoJSON's URL |
| `format.test.ts` | distances in both units and their thresholds, durations, ratios, counts, items one and many; drive times typed as a target, read and written back; fuel and its cost, percentages ("under 1 %"), states by their codes and a name not known; a time written at the browser's offset to the second and read back, ISO times with an offset read and others (a day the month lacks among them) refused, a time shown as a clock at its own offset |

The drawn points are not the stored geometry point for point: a snapshot is simplified
(`api.md`, **Applying the stream**) and a splice is not, so the replay has fewer points; it is
compared node by node, and as an in-order subsequence of the stored geometry once consecutive
duplicates are dropped (the document's geometry drops them, a stream's points keep them).

`src/__fixtures__/http.ts` holds the stand-ins: `stubFetch`, `FakeEventSource` and a route view
that records what it is asked to draw.

**The recordings** (`src/__fixtures__/stream.ts` loads them) are jobs on Portland as
`worst-route serve` streamed them, the first two on build e4b3b4ad90db, the next two on
506f747d7458, the last three on ffe2e7c881ca. `portland-stream.json` (about 1 MB), recorded
before the API had targets (so its `done` and document have no `target`, which the types allow),
is a max-mode run:
Powell's (45.5231, −122.6765) → a via in the Central Eastside (45.5195, −122.6625) → Lloyd
Center (45.5317, −122.654), seed 1, a 6 s budget, ended on the budget at 78.0× after 63 events,
with a snapshot after the 50th `progress`. It holds `/regions`, the request, the 202, every
event as `{id, event, data}`, `GET /jobs/{id}` after `done` (whose snapshot gives every node's
position), `GET /routes/{id}`, and a request refused for a stop in Seattle
(`STOP_OUTSIDE_REGION`, `stop_index` 1). `portland-ratio-stream.json` (0.12 MB) is a ratio
target: Powell's → Portland State (45.5118, −122.6833), seed 1, a 4 s budget, 25×, reached in
one `progress` event of 15 splices, `done` `"target"` at 25.01× within ±2 %; its refusal is a
ratio target for a round trip (`INVALID_REQUEST`, `field: "target"`). It has no snapshot just
before `done` (one comes only when the stored route is not the drawn one, after an overshoot
shrinking could not undo; 14 Portland runs tried had none), which the replay would handle as
any snapshot. `portland-time-stream.json` (0.33 MB) is the slowest time: Powell's → Reed
(45.4817, −122.6263), seed 1, a 4 s budget, 25 `progress` events and the first snapshot only,
ended on the budget at 17.2× in length and 16.9× in experienced time (drive 4 h 8 min, delays
1 h 59 min, 277 left and 282 right turns) after 552 attempts; its refusal is both turn
constraints at once (`INVALID_REQUEST`, `field: "constraints"`). `portland-collect-stream.json`
(0.34 MB) collects bridges on the same stops, seed 1, 4 s: 25 `progress` events counting 5
bridges at first and 18 at the end (14.9×, 897 attempts); its refusal is a ratio target under
collect (`field: "target"`). Both were recorded while the machine was busy with other work: on
a less loaded one the same requests made about 18,000 and 12,000 attempts in 4 s, and
recordings of those would be several times larger, so a re-recording keeps its size down with
a shorter budget. `portland-directions-stream.json` (0.41 MB) is Powell's → Reed again, seed 1,
as bad as possible, with a vehicle (24 mpg, $3.89) and a 0.3 s budget (on a quiet machine
0.5 s made 0.62 MB and 1 s 0.89 MB): 3 `progress` events and the first snapshot only, ended on
the budget at 14.5× (694 attempts); its document carries 586 steps (505 turns, 24 continues,
15 roundabouts, 40 jokes, the departure and the arrival), ten toasts, 2.7 gallons and $10.50
extra, Oregon and Portland, and 10 % of the roads within reach; its job status's snapshot gives
where every node of the stored route is, against which the directions' tests place the steps;
its refusal is a vehicle of 0 mpg (`INVALID_REQUEST`, `field: "vehicle"`).
`portland-brand-stream.json` (0.36 MB) is a pilgrimage to Starbucks (`brand: 229`), Powell's →
Reed, seed 1, a 4 s budget cut to 450 attempts by `max_iterations` (a route that visits more of
the 19 is a longer one, and with its directions a larger file: 4,200 attempts, 17 of 19, made
0.91 MB): 3 `progress` events counting 8, 13 and 14 of the 19 Starbucks within reach after the
seed's 1, ended on the budget at 13.2×; its refusal is a ratio target under brand (`field:
"target"`). `portland-alphabet-stream.json` (0.28 MB) is the alphabet route from Powell's to PDX
(45.5898, −122.5951), seed 1, 150 restarts (`max_iterations`, within a 20 s budget): 20 better
routes, each a snapshot then a `progress` without splices, from "A" to fifteen letters, A to O
(Southwest Alder Street to Southwest Oak Street), 1.87× (4.5 s); its refusal is a stop along the
way (`field: "stops"`, "the alphabet route takes a start and an end"). Both were recorded from a
lane's `serve` with the hard-mode and alphabet series applied before they landed; recorded again
on the landed code, every payload has the same shape and the same routes (the pilgrimage's
splices grouped into its `progress` events differently, as timing decides). To record one again
(after an API change): run `serve` on a build, `POST /jobs` with its request, read
`/jobs/{id}/events` to its end, then fetch the status, the route and the refusal, and write the same
keys, one top-level value (and one event) per line; a script of a few lines with `httpx` does it.
The max-mode run needs at least 50 `progress` events before
its end, so a mid-run snapshot exists, and stays near 1 MB.

## Seeing it

Agents run the app in their own clone (**Running it**) and drive it with headless Chrome over
the DevTools protocol, the tile server blocked (`Network.setBlockedURLs`) and the geocoder off
or pointed at a stub of their own, writing screenshots to their scratch directory; a
Playwright tool under `tools/` is a later item. MapLibre 6 needs WebGL2, which headless Chrome
gives with `--use-angle=swiftshader --enable-unsafe-swiftshader`. Killing the dev server
mid-run reloads the page (Vite's client does), so a dropped stream is tested through a relay in
front of `npm run preview`.

## Measured

On Portland (build e4b3b4ad90db, the CSR backend), the dev server proxying to a lane's
`serve`, headless Chrome on the dev machine:

- **Powell's → Reed, seed 1, 15 s** (clicked at 45.523475, −122.676653 and 45.481983,
  −122.626625): 23.0× at 0.7 s, 47.0× at 3.3 s, 56.3× at 7.3 s, then `done` at 12.0 s,
  converged at 60.0× (290 mi, 14 h 14 min, 64,996 attempts), and the same route again on a
  second pass; the stops snapped 120 ft and 512 ft, auto was T4 with a 3.6 km leash. Through
  the dev proxy events arrive every 0.1 s, as from the server.
- **Cancel** at 2 s (seed 2): `done`, `cancelled`, 44.7× (47.6× on a second pass: where a cancel
  or a budget stops a run depends on timing), stored. **Reroll**: the server's seed
  882069796, 62.6× converged in 14.3 s; **Make it worse** from it (seed 882069797): 63.1× in
  1.8 s. **Leaving the page** mid-run: the server logged the `DELETE`.
- **Three stops** (a via in Ladd's Addition), seed 3, 10 s: 61.9× and 63.0× on the budget; its
  link, opened in a fresh page, shows the same route, stops, snaps, settings and numbers.
- **A stop in Seattle** from a request link: `STOP_OUTSIDE_REGION` in the banner, the end stop
  highlighted in the list and on the map; dragging the start clears it and rewrites the URL.
- **Targets** (on the targets' API, Powell's → Reed as above): 25 typed in the ratio box picks
  the ratio and puts `target=ratio:25` in the URL; seed 1 ends in 3.1 s at 24.6× ("target
  25.0×, achieved 24.6×, within ±2 %", `target reached`), and its link reopens it with the ratio
  set. Make it worse from it with 15× shrinks it, to 15.4× when its 15 s ran out (outside
  ±2 %). `3h` in the drive-time box (a typo is put back) reaches 3 h 1 min in 0.2 s. A ratio
  target for a round trip shows `INVALID_REQUEST` in the banner.
- **A dropped stream** (cut 2.3 s in, at event 26): `reconnecting…`, then about 3 s later a
  snapshot with id 57 and live events, ids rising strictly; replaying everything the browser
  received gives the stored route's 4,734 nodes exactly.
- **Modes and turns** (2026-10-30, build 506f747d7458, stops clicked at 45.523659, −122.676763
  and 45.482436, −122.62666, seed 1, 8 s): Slowest put `mode=time` in the URL; 19.1× in length
  at 2 s, then "40.1× slower", 235 mi · 11 h 35 min, "drive 11 h 35 min + delays 3 h 42 min",
  "1,114 left · 1,115 right" (14,225 attempts, budget). Collect, picked while a 25× ratio was
  chosen, turned the target to as bad as possible, disabled the ratio with its hint and put
  `mode=collect:bridge` in the URL; the count rose live (19 bridges at 1.2 s, 23 at 2.8 s, 24
  at 7.1 s) and the run ended at 51.4×, 248 mi, 24 bridges. Avoid left turns (`turns=left`),
  the longest: converged at 10.4× in 2.7 s, "58 left · 80 right". Strict on top
  (`strict=1`): refused by the `POST` with `INFEASIBLE_TURN_CONSTRAINT` ("no route from stop 0
  to stop 1 without repeating a road, after moving stop 1 up to 10 times, under the strict
  no-left constraint; try without strict"), the end stop highlighted and the "Try without
  strict" button in the banner; the button unticked strict, closed the banner, dropped
  `strict=1` from the URL and planned the soft no-left route again (the same numbers). The
  slowest-time route's link, in a fresh page, showed Slowest checked and the same rows; Make it
  worse from it (seed 2) gave "47.5× slower", "drive 14 h 1 min + delays 4 h 5 min", still
  `mode=time`. With a route shown (21.7×, seed 1, 4 s), a strict no-left Make it worse was
  refused the same way with the route still shown; the button sent it again without strict:
  the stored route's request names the shown route as `base_route_id`, seed 2, `strict: false`
  (21.8×). A strict Reroll refused, the button rerolled without strict, the server picking the
  seed. A hand-made link with `mode=collect:bridge&target=ratio:20` opened with "as bad as
  possible" chosen; a ratio target for a round trip got `INVALID_REQUEST` with the How bad row
  highlighted.
- **The place search** (2026-10-30, `serve --nominatim-url` pointed at a local stub answering
  the committed jsonv2 Portland body; Nominatim itself never called): typing "Powell's" one
  key at a time sent nothing; Enter sent one request and listed three places; picking the first
  placed the start at 45.523117, −122.681255 and cleared the field, and a search by the button
  for the Hawthorne store placed the end, both in the URL's `stops`. Go (seed 1, 6 s) from
  them: the search locked while it ran, 50.4× on the budget (174 mi), the stops snapped 59 ft
  and 151 ft. With ten distinct queries sent at once by a script (on the serialised rate
  limit), six went upstream, each at least a second after the previous answer, and four were
  refused, and the page's own search 0.05 s later, with five queued ahead of it, showed
  `RATE_LIMITED` in the banner.
- **Directions, toasts, the stat rows and the vehicle** (2026-10-30, build ffe2e7c881ca with
  the Portland places index, `serve --geocoder none`, the machine shared with other lanes): a
  link with `vehicle=30,4.25` put 30 and 4.25 in the Vehicle row. Powell's → Reed (seed 1,
  15 s): "Finding the worst way. Please hold." at Go, "Rejecting good ideas…" at 4.2 s, "Still
  not repeating a road." at 8.2 s, "Currently 33.8× worse" at 12.1 s rising with each
  `progress` to 39.9×, the directions empty with their hint meanwhile; on `done` (20.8 s, the
  worker's start and the document included) "43.5× less efficient than the sensible route.",
  2,026 steps in the list (1,917 turns, 35 continues, 32 roundabouts, 40 jokes), and the rows
  "towns: Portland, Milwaukie", "states: OR", "fuel: +6.8 gal · $28.83", "roads used: 29 % of
  those within reach". Hovering "911. Turn left onto Southeast 28th Avenue · 0.8 mi · 2 min"
  drew that street in blue, from the turn onto it to the turn off it; a click panned the map to
  its start; from it two ArrowDowns focused step 913, the only step in the tab order. Its link,
  in a fresh browser, showed the same route, rows, 2,026 steps, the vehicle and the first toast.
  With a 6 s budget (32.5×), the pointer over the toasts held one for 5 s and a click moved on.
  Beaverton → Milwaukie (seed 1, 15 s): 27.4×, "towns: Beaverton, West Slope +14", the 16
  names in the tooltip, "fuel: +12.1 gal · $46.98". Three stops (a via in the Central Eastside, seed
  3, 4 s, 39.2×), the via labelled: "1609. Arrive at Stop 2; continue" in bold under the marker
  numbered 2, and the API's own "1767. Arrive at your destination"; a round trip through the same
  via (seed 1, 3 s) ends "Arrive back where you started", its stored request labelling the via
  "Stop 2" and the ends not at all. Switching the units rebuilt the 2,026 steps in 41–69 ms. The
  departure (seed 2, then 5, 3 s, a via): left empty, the request carried the time Go was pressed
  ("2026-10-30T23:26:25-05:00"); typed as 31 Oct 09:00, the URL took
  `departure=2026-10-31T09%3A00%3A00-05%3A00` and the route (seed 5, 17.8×, 4 h 14 min of driving
  and its delays) "arrives Sat 14:57"; its link, in a fresh browser, put 09:00 back in the field.
  "Download GeoJSON" pointed at `/api/routes/6bd8d2294df9/geojson` with the download name
  `worst-route-6bd8d2294df9.geojson`, which answered 200 with the route, the baseline and three
  stops.
- **Brand, alphabet and hard mode** (2026-10-31, build ffe2e7c881ca, a lane's `serve` with the
  hard-mode and alphabet series applied, `--geocoder none`, load average about 17–20): the Brand
  picker listed the 287 brands, "United States Postal Service (174)" first, then "Starbucks
  (126)"; picking Starbucks put `mode=brand:229` in the URL and disabled the ratio with its hint.
  Powell's → Reed (seed 1, 8 s): "7 of 19 Starbucks" at 2.0 s, 14 at 2.4 s, 15 at 3.5 s, 16 at
  4.7 s and 17 at 7.2 s, then `budget` at 39.0×, 187 mi, "17 of 19 Starbucks" (6,361 attempts).
  A link with `target=ratio:20&mode=alphabet` opened on Alphabet with as bad as possible, the
  whole target disabled with its hint, and the stops' second hint. Powell's → PDX (seed 1, 10 s):
  "letters: 4 (A–D)" at 1.4 s, 9 (A–I) at 2.2 s, 10 at 6.5 s and 15 (A–O) at 8.8 s, each better
  route drawn whole in place of the last with nothing flashed, the ratio following it down as
  well as up (1.7× with 9 letters, then 1.6× with 10); `done` at 1.9×, "letters: 15 (A–O)", the
  fifteen roads from "A: Southwest Alder Street" to "O: Southwest Oak Street" in the tooltip (the
  store already held this route from the recording, so its document, and the attempts shown,
  are that run's: 150 restarts, 4.5 s). Its `?route=` link in a fresh browser showed Alphabet,
  the target disabled, the same rows and tooltip. A stop along the way under Alphabet: refused
  by the `POST` (`INVALID_REQUEST`, "the alphabet route takes a start and an end"), the via
  highlighted in the list and on the map. Hard mode (`hard=1`, seed 1, 6 s): 8.2×, 39.5 mi, the
  stored request's constraints carrying `hard: true`, its `/validate` ok. Hard mode on the
  acceptance pair it refuses (45.52537, −122.88288 → 45.51051, −122.7433): refused in 0.4 s
  (`STOP_UNREACHABLE_WITHOUT_REPEAT`, "the seed found no way from stop 0 to stop 1 under hard
  mode, after moving stop 1 up to 10 times; try without hard"), the end stop highlighted and
  "Try without hard mode" in the banner; the button unticked hard mode, dropped `hard=1` from
  the URL and planned the same request without it (11.2× in 4 s, seed 1, its stored request
  without `hard`). With `GET /brands` held (the DevTools' `Fetch` domain), a link with
  `mode=brand:229` showed the picker disabled at "loading…" and Go and Reroll disabled, which
  Longest enabled again; once answered, "Starbucks (126)" of 287 and Go enabled. With `/brands`
  blocked: "brands unavailable", `NETWORK` in the banner, Go and Reroll disabled under Brand and
  enabled under Slowest. A plain route (24.0×, seed 1, 2 s) made worse with Hard mode ticked:
  refused (`BASE_ROUTE_MISMATCH`, "the base route comes back to OR 99E at edge 40, which hard
  mode refuses"), the route still shown and "Try without hard mode" in the banner, which made it
  worse without hard mode (seed 2, 34.4×, the stored request's `base_route_id` the shown route's
  and no `hard`). The alphabet route's link set Alphabet, under which Make it worse was off with
  its tooltip; under Longest it continued that route (seed 2, 48.3×, its base the alphabet
  route).

- **The pmtiles basemap** (2026-10-31, build 4b8998deee9f, `WORST_ROUTE_BASEMAP=pmtiles`, the dev
  server proxying to a lane's `serve`; the lane's `portland.pmtiles` a 191 kB file of hand-made
  vector tiles in Protomaps' schema, zooms 0–12 over the Portland bbox, standing for an extract,
  and no fonts): `/api/config` named the file without fonts or icons; the page read
  `/api/basemap/portland.pmtiles` by byte ranges, every answer a 206, and drew the earth, a band
  of water and the roads in the grayscale flavor under the route (Powell's → Reed, seed 1, 4 s,
  32.3×), "Protomaps © OpenStreetMap" in the attribution control, the credits under the panel,
  no request to the OSM tile server. With stand-in icons and an empty fonts directory the
  labelled style loaded too, its font requests 404 (`Unable to load glyph range`, at the
  `/api/basemap/fonts/Noto Sans Regular/0-255.pbf` the template gives) and the rest drawn alike.

## Limitations

- **A stream through the dev proxy whose upstream drops is not ended**: Vite's proxy keeps the
  browser's response open, so if the API dies mid-run the stat line stays at `getting worse…`.
  A connection dropped between the browser and the server is reconnected as above.
- **Seeds above 2⁵³ − 1** cannot be typed: the API allows up to 2⁶³ − 1, but a JavaScript number
  is exact only up to 2⁵³. A stored route with a larger seed (from the CLI or a script) shows it
  rounded.
- **Under the slowest time the large ratio changes kind when the run ends**: the stat line shows
  the experienced time's ratio ("× slower") only once the route is stored (from the document's
  `time_ratio`), and the lengths' ratio while the run goes; the drive and delays row below it is
  live from `progress`'s `delay_s` and settles on the stored route's.
- **Desktop layout only**. The share image is a link to the server's PNG, not shown in the page.
- **Most alphabet routes are short of letters**: on random trips the alphabet route gets a median
  of one letter (the 20 acceptance pairs: 1 on 12 of them, at most 9; `docs/reference/engine.md`,
  **Alphabet**), against fifteen to twenty-three from Powell's to PDX through the Northwest
  Alphabet District. That is the engine's search on Portland's names, not the frontend's: the
  row shows what the route has.
- **A step's highlight is placed by distance**, not by its nodes (**Directions**): a few of its
  ends (0.1 % on Portland) are a point next to the node, within a metre. The document carries
  no offsets of its edges in its geometry; exact ones would need the API to send them.
- **A route already stored keeps its first record's presentation**: the store names a route by
  its content (`docs/reference/api.md`, **The store**), so a run that finds a route already
  stored (the same request and seed again, or another seed landing on it) shows the first
  record's arrival, fuel and stop names, whatever departure, vehicle or labels it was sent with:
  changing the departure and pressing Go with the same seed shows the first arrival.
- **The fuel is in US gallons and dollars** whatever the units, as the vehicle is set in mpg and
  dollars a gallon.
