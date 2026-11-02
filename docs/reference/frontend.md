# Frontend

The single-page app under `frontend/`: a map of the served regions on which the user clicks a
start, an end and stops along the way, starts a job, watches the worst route grow live, cancels,
rerolls, makes the route worse, reads its directions and its toasts, opens a stored route
by link, sees today's route, the server's one trip of the day, draws a route by hand against
the engine's, and takes on the day's challenges, the server's, whose badges the browser keeps.
Vite + TypeScript (strict), no
framework, MapLibre GL JS 6. The design it implements is `docs/PLAN.md` §5; the conventions are
`CLAUDE.md`. It talks to the backend through the API only (`docs/reference/api.md`), and changes
nothing there.

| Module | What it holds |
|---|---|
| `main.ts` | bootstrap and wiring: the store from the URL's state, the panel's parts (today's route and the challenges among them), the served regions (`GET /regions`) and the map fitted to all their bboxes, the build each trip runs on (`RegionPicker`), the basemap the server names (`GET /config`) with the land outside the contiguous US flat (`public/conus.json`), a stored route when the URL names one (or today's, for `?daily`; or else the challenge `?challenge=` names set up, once its day's are known), the day's challenges asked for at start and graded on every route shown, a toast for each one completed and for a run of the one set up that ends short of its bar, clicks on the map, the cancel on `pagehide`; the theme the settings name, on the page, the map's layers, the basemap and the land outside the US (**Themes**) |
| `state.ts` | `Store`, the app's state on an `EventTarget`; `Settings`, `Stop`, `RunView`, `targetOf`, `takesTarget` (the targets a mode takes), `certifies` (whether the settings ask for a certified route), `modeOf` (the request's objective, item, brand and constraints), `vehicleOf`, `departureOf`; the API's limits (tiers, budget, leash, targets), the vehicle's, and the modes and items it plans |
| `api/types.ts` | the API's shapes, written by hand from `api.md` |
| `api/client.ts` | the fetch wrappers (`getConfig`, `pickRegion`, `getBrands`, `geocode`, `getDaily` and `getChallenges` among them), `ApiError`, `errorBody`, `subscribe` (a job's events over `EventSource`) |
| `map/basemap.ts` | `basemapParts` (the basemap `/config` names, as sources and layers, in a variant), `styleUrl` (OpenFreeMap's style for a variant), `fetchStyle` (OpenFreeMap's style), `addBasemap`, `Basemap` (the basemap drawn, its variant changed in place, with what its `extend` adds to its parts), `EMPTY_STYLE` (**Basemap**, **Themes**) |
| `map/outside.ts` | the land outside the contiguous US drawn flat (**Basemap**): `fetchOutline` (`public/conus.json`, its polygons' own holes filled), `maskOf` (the mask, the world with the outline's polygons as holes), `withOutside` (over Protomaps' earth and land cover, the earth again in the outside tone and the US in the land's colour; else the mask, just below the first water layer; the labels drawing a point only within the outline), `paintOutside` (the outside tone from the theme) |
| `map/filters.ts` | `asExpression`: a layer's filter in expression syntax, a legacy one (Protomaps') converted as the style spec's migration converter converts it, so `withOutside` can AND a clause onto it (**Basemap**) |
| `map/map.ts` | the MapLibre map, its controls, MapLibre's worker and the `pmtiles://` protocol, `fitTo` |
| `map/cities.ts` | `CITY_LAYERS` (the basemap's layers that draw the labels of cities, towns and villages, by its kind), `CITY_HIT_PX`, `cityAt` (the city whose label a click is on; no DOM) (**Stops**) |
| `map/bounds.ts` | `US_BOUNDS`, the box around the contiguous US the map's camera is held to; `fitZoom`, the zoom with the whole box in view; `holdToUs`, a camera held to the box (**Stops**) |
| `map/routeLayer.ts` | `spliceLine` and `RouteLayer`: the baseline, the growing route, the flash (**Live growth**), and the highlight of a step of the directions; `paintRoute`, their colours and widths from the theme (**Themes**) |
| `map/marksLayer.ts` | `MarksLayer`: the route's marks, the brand locations it visits and the items it collects (the document's `marks`), circles over the route named on hover; `markName`, `marksOf`; `paintMarks`, their colours from the theme (**Live growth**, **Themes**) |
| `map/stopsLayer.ts` | the stop markers and the snapped-position ghosts |
| `map/drawLayer.ts` | `DrawLayer`: a route drawn by hand, its next moves, their names on hover, the move a click takes (**Drawing**); `paintDrawing`, their colours from the theme |
| `stops/stopList.ts` | how a click, a drag or a removal changes the stops (`placeStop`: a click's, a city's label's with its name, and a place search's pick); `stopLabel`, a stop's label in the request; `stopErrored`, the stops an error names; `busy`, whether a job or a drawing locks them; the list in the panel |
| `stops/pick.ts` | `RegionPicker` (the build a trip runs on, asked of `POST /regions/pick` as the stops change, no DOM), `pickLine` (the stop list's header), `regionLabel`, `regionName`, `roadsOf`, `ROADS` (each tier's roads by name, the settings' Roads options too) (**Stops**) |
| `stops/search.ts` | `PlaceSearch` (the place search, no DOM, so the tests drive it), `searchRegion` (the region it asks in) and `mountPlaceSearch`, its field, button and results |
| `settings/panel.ts` | the request's settings (the region pinned, the mode with its item or brand, the turns, hard mode, the worst order and Certified, the vehicle and the departure among them; `regionOptions`, the region picker's; `loadBrands` and `brandOptions`, the brand picker's), the row an error's `field` names, and the units |
| `settings/themeRow.ts` | the settings' Theme row, `themeOptions` (**Themes**) |
| `theme/theme.ts` | `Theme` and its parts (`ThemeColors`, `ThemeFonts`, `ThemeShape`), `ThemeId`, `Scheme`, `BasemapVariant` (**Themes**) |
| `theme/presets/` | one `Theme` a file (`studio.ts`, `night.ts`, `transit.ts`, `topo.ts`, `arcade.ts`, `dashboard.ts`, `atlas.ts`, `sign.ts`, `ledger.ts`, `classic.ts`), and `THEMES` (`index.ts`), the settings' order |
| `theme/apply.ts` | `themeProperties` (a theme as CSS custom properties) and `applyTheme` (on `<html>`, its fonts loaded) |
| `theme/resolve.ts` | `DEFAULT_THEME`, `DARK` (the system's scheme as `matchMedia` asks it), `resolveTheme` (a setting, `auto` by the system's scheme), `storedTheme` and `followTheme` (the setting kept in the browser's storage, followed as it or the scheme changes) |
| `run/controls.ts` | `JobRunner`, a job's lifecycle (no DOM, so the tests drive it), and the retries without strict and without hard mode; `mountRunControls`, Go, Cancel, Reroll ("Try another"), Make it worse, Draw it and the stat line; `statLines`, `countsTitle` and `routeLines`, its rows; `awaitingBrand`, which holds the buttons under Brand until the brands are known, and `worseRefused`, which turns Make it worse off for an alphabet route and a certified one |
| `directions/panel.ts` | `StepLocator` (where the steps lie along the route's geometry), `stepRows`, `DirectionsPanel` (the list's rows, a hover's highlight and a click's pan, no DOM) and `mountDirections` (**Directions**) |
| `toasts.ts` | `Toasts`, the rotation and the notices shown once each (`notify`; no DOM), and `mountToasts`, the strip under the map (**Toasts**) |
| `share/share.ts` | the URL state, `UrlSync`, `openRoute`, `shareImageUrl` and `followShareImage` (the share image in the theme shown), `geojsonUrl`, the share section (**Links**) |
| `draw/drawing.ts` | `Drawing` (a route drawn by hand, its steps and its par; no DOM), `drawRequest`, `finalMove`, `nearestMove`, `moveLabel`, `verdictOf` (who has won), and the panel's lines (`progressLine`, `youLine`, `parLine`, `verdictLine`) (**Drawing**) |
| `draw/panel.ts` | `mountDraw`, the drawing's section (**Drawing**) |
| `daily/panel.ts` | `DailyRoute` (today's route, `GET /daily`, and its plan followed; no DOM), `dailyLine`, `openDailyAtStart` (`?daily`), `mountDaily`, the section and the header's link (**Today's route**) |
| `challenges/list.ts` | `Challenge` (one of the server's, with its day and region), `challengesOf` (a day's answer's), `dateOf` (the day an id ends in) (**Challenges**) |
| `challenges/progress.ts` | `sameTrip` and `tripOf` (whether a route is a challenge's trip), `reached` (its goal), `progressLine` (how the route shown or the run going stands against a challenge's bar, on its row), `shortLine` and "short" (a run of the challenge set up that ends short of its bar, for its toast), `ChallengeProgress` (the completions, graded on every route shown and again when the list changes, kept in the browser's storage by dated id; no DOM), `completedLine`, the toast (**Challenges**) |
| `challenges/panel.ts` | `ChallengePanel` (today's challenges asked for, a link's of another day, those listed, Set it up, whether one is still set up, the collapse; no DOM), `challengeSettings`, `stillSetUp`, `setUpLine`, `doneLine` (how many are done), `HINT` (the line under the list), `mountChallenges`, the section and the header's link (**Challenges**) |
| `banner.ts` | the error banner, an error's message as a sentence (`sentence`) and its code in the tooltip, with "Try without strict" or "Try without "No road twice, ever"" when the refusal says either is why (`retryOf`, on the code or the message's stable ending) |
| `format.ts` | distances (mi/km), durations, ratios, counts, items collected; fuel and its cost, percentages, states by their codes; times of day (`isoWithOffset`, `parseIsoTime`, `formatClock`); the daily route's day (`formatDay`) |
| `util/geo.ts`, `util/dom.ts` | rounding and bounds; building DOM with text, never HTML |
| `public/notices.html` | "Data sources and notices", the page the footer links to (**Basemap**) |
| `public/conus.json` | the contiguous US's outline, as `tools/desktop_data.py conus` writes it, which the map draws the land outside flat around (**Basemap**) |

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

`dist/` expects `/api` to reach the API, as the dev server and `preview` arrange, and so does
`worst-route serve --frontend dist`, which serves the build and the API from one process, as the
container does (`docs/reference/api.md`, **Running it**; `docs/reference/hosting.md`) and the
desktop app does in its window (`docs/reference/desktop.md`). The bundle is about 1.2 MB minified (MapLibre
is most of it), 330 kB gzipped, and MapLibre's worker a module of its own, 0.5 MB, in
`dist/assets/`: MapLibre 6 looks for its worker beside its own module, which a bundle (or Vite's
pre-bundled dependencies in the dev server) does not keep, so `map.ts` imports it as an asset
(`maplibre-gl/dist/maplibre-gl-worker.mjs?url`) and names it with `setWorkerUrl`. A theme's
fonts (fontsource packages, SIL OFL 1.1) are CSS chunks of their own and their `woff2` and `woff`
files in `dist/assets/` too, imported only when the theme is chosen (**Themes**): the first load
carries none, and no font is fetched from anywhere but the app's own origin.

## Using it

- **Today's route** (`daily/panel.ts`). At the top of the panel, under the title: one trip a day,
  the same for everyone on the server (`GET /daily`, `docs/reference/api.md`, **The daily route**),
  asked for at start and shown once the server has answered. It reads "Today's route", the day
  ("Saturday, October 31", the UTC date the server plans it for), the two towns ("Sageville, IA →
  Acworth, GA"), then how it stands: while the server plans it, "Planning…" and the ratio so far
  ("Planning… 7.2× so far"), from its job's `snapshot` and `progress` events, followed with the
  stream a run uses (`subscribe`), nothing drawn on the map and the job never cancelled (it is the
  server's, which refuses a client's cancel, `DAILY_JOB`: not on `pagehide` either); once done (the
  stream's `done`, after which it asks `GET /daily` again), the route's ratio, "6.6× longer than it
  had to be", its share image (the server's PNG, `shareImageUrl`, the existing card)
  and an "Open it" button, which opens the route exactly as a `?route=` link does (`openRoute`),
  disabled while a job runs; failed, or a server too busy to plan it (`GET /daily`'s 429
  `TOO_MANY_JOBS`), "Today's route could not be planned." and a "Try again" button, which asks again
  (and so has the server plan it again). The section collapses when the user starts a route of their
  own (Go, Reroll or Make it worse: the run starting; or Draw it), and a "Today's route" link under the tagline
  shows it again; opening today's route does not collapse it. A server with no daily route (`GET
  /daily` refused, 404: a region without a places index, or without two towns that fit) shows
  neither the section nor the link.
- **Challenges** (`challenges/`). Under today's route, the day's challenges, the server's (`GET
  /challenges`, `docs/reference/api.md`, **The daily challenges**): at most eight a day, in this
  order, each type left out on a day without a fitting trip (the section shows what comes): the
  long way (A to B at N× or worse), the slow way (A to B taking at least H hours), no left turns
  (A to B with none, at the ratio), bridges (at least K crossed between A and B), a pilgrimage (at
  least K of a brand's locations), hard mode (A to B at N×), the worst order (four towns at N×),
  and today's route made worse once. Each has an id that carries its day (`long-way-2026-11-02`),
  a title that names the trip and carries its bar ("Losantville, IN to Wallace, IN at 15×", "Cross
  600 bridges between Hanover, OH and Milford, OH", "Josephville, MO, West City, IL, Cambria, IL and
  Stewardson, IL in the worst order at 11×"), a blurb, a
  setup (the stops, labelled with their towns; the mode with its item or brand; the turn
  constraints and hard mode; the worst order; as bad as possible; on the answer's region at the
  automatic tier and leash; none for today's route made worse) and a goal measured on a finished
  route: a ratio (`{kind: "ratio", min}`), a drive time with its delays (`duration`, `min_s`), no
  left turn at a ratio (`no_left`, `min_ratio`), a count of an item (`items`), a brand's visits
  (`brand`), a hard-mode route's ratio (`hard`), or today's route made worse once (`daily_worse`).
  The section asks for today's at start (`GET /challenges`, the day being the server's, the UTC
  date of today's route) and says "Loading today's challenges…" meanwhile: a server makes today's
  as it starts (about a minute on `us-t3`), so a page opened later is answered at once, and one
  opened sooner, or a link's of another day not made yet, waits it out. A server without them (404: no
  places index) shows no section; any other failure (the server out of reach, a proxy's 502) shows
  "Couldn't load today's challenges." and "Try again", which asks again. Each row has its badge (a
  green tick once completed, "Completed Saturday, October 31" in its tooltip), its title, its
  blurb, its standing (below) and "Set it up". **Set it up** pins the answer's region, puts in the stops with their
  towns' names (each shown under its role, as a city's clicked on the map is, **Stops**) and gives
  the settings: the mode with its item, the turns (Avoid left turns and strict for no left turns),
  hard mode, the worst order, as bad as possible, the automatic tier and leash, Certified unticked
  (the seed, the budget, the vehicle, the departure and the units stay the panel's); its brand is
  picked once the region's brands are known, by its wikidata id, else its name (a brand's id is
  its build's). It forgets the route shown and a drawing (as an edit of the stops does), scrolls
  the stop list into view and fits the map to the stops, and the row says "Set up. Press Go."
  Today's route made worse instead opens today's route as "Open it" does ("Set up. Press Make it
  worse."; its button disabled until the day's route is done), and another day's is refused ("Only
  today's route can be made worse."). A challenge stays set up (the store's `challenge`, the row
  highlighted, `?challenge=` in the URL) while its region, stops and settings are the store's (a
  run, its seed and the snaps change none of them), and today's route made worse while that route
  is shown. **Grading** (`challenges/progress.ts`): every route the store shows (a run's, a
  drawing's, one opened by link; a certified route's like any other) is graded against each
  challenge listed and not yet completed whose trip it is (`sameTrip`: the same region; the same
  objective, with the same item, or the same brand by its wikidata id through the loaded brands
  when they are the route's region's, else by its name; the worst order as the challenge sets it
  (the request's `reorder`); and as many stops each within 500 m of its own, under the worst order
  the start and the end in place and the stops between in any order, since a worst-order route's
  request lists them in the order it visits them; for today's route made worse, the longest route
  on the region of today's route, and the challenge today's, its day that of `GET /daily`), by its
  goal (`reached`: the document's `ratio` at least the goal's, none for a round trip; `time_s` and
  `delay_s` together at least `min_s`; `turns.left` 0 and the ratio; `items` of the item;
  `brand.visits`; a request under hard mode at the ratio; a request whose `base_route_id` is
  today's route at least at today's ratio, a continuation toward a target below it shrinking); and
  the route shown is graded again when the list comes, since it may be shown first (a `?route=`
  link). A challenge newly completed is recorded with the day (the browser's, YYYY-MM-DD) in the
  browser's `localStorage` under `worst-route.challenges` (a JSON object from id to day; what is
  not an id ending in a date with a string is ignored, so the fixed list's badges of old no longer
  count and go at the next write), every read and write in a try, so the page works without it (a
  private window, storage blocked: the badges then last as long as the page); its toast,
  "Challenge done. Cross 600 bridges between Hanover, OH and Milford, OH.", is shown once
  (**Toasts**), and its badge turns on. Past
  days' badges keep, as their ids are their days': under the list, "3 challenges done" counts
  every day's, and "Reset badges" forgets them all; a route still shown that meets a challenge (a
  reload of a page whose URL still names it, `?route=`) completes it again. **Short of the bar**:
  a run that starts with a challenge set up and ends on a route of its trip short of its bar
  (Go, Try another or Make it worse, one that found nothing worse or was cancelled too) says so
  once, a toast (`shortLine`, **Toasts**) in the standing's terms: "12.3×, the challenge needs 15×.
  More time, another run or Make it worse may get there." ("41 h, the challenge needs 50 h.", "412
  bridges, the challenge needs 600.", "7 Dollar General, the challenge needs 11."); nothing for a
  run that fails, one with nothing set up, a challenge done already (its badge holder gets no
  nag), or today's route made worse. The storage is the
  page's origin's: the badges are a browser's and a site's, and the desktop app keeps its own
  between starts on its own port (`docs/reference/desktop.md`). **The standing**
  (`progressLine`): while the route shown, or the run going once it has numbers, is a challenge's
  trip (`sameTrip`; a run's from its request's mode, the store's region and stops), the
  challenge's row says how it stands against the bar, in the app's voice: "12.3× so far · needs
  15×" (a ratio: the long way, the worst order), "41 h of 50 h" (the drive time with its delays),
  "412 of 600 bridges", "7 of 11 Dollar General", "no lefts · 1.3× of 1.4×" (under strict no-left
  only) and "hard mode · 1.6× of 2×" (under hard mode only), as nothing else can reach those two;
  a ratio so far to the tenth below and the hours to the hour below, so neither reads as a bar it
  has not reached. It follows the run's numbers live (a `progress` event's), though the section is
  collapsed while a run goes until "Challenges" shows it again. A row done says "Done" instead,
  beside its badge; today's route made worse says nothing, its bar being the route a run
  continues. Under the list, a hint: "Set it up, then Go. Short of the bar? More time, another run,
  or Make it worse." The section collapses when a run
  or a drawing of the user's own starts, as today's route does, and a "Challenges" link under the
  tagline shows it again.
- **Stops.** On load the map fits the bboxes of every region the server serves (`GET /regions`,
  one or several builds: a state's all roads beside the national one). Its camera is held to a
  box around the contiguous US (`US_BOUNDS`, `map/bounds.ts`: the national build's extent with
  about 2° of margin west and east and 1.5° south and north), on which it starts, in place of
  MapLibre's own constrain (`transformConstrain`, `holdToUs`): on an axis where the view is smaller
  than the box the view never shows beyond it, on one where the view is larger the whole box is
  always in view, and the map zooms out until the whole box is in view and no further (`fitZoom`,
  the zoom `fitBounds` picks for the box, worked out again as the map's size changes). Zoomed out
  furthest, the whole contiguous US is in view, with margin only on the axis the window has to
  spare, and a fit of anything within the box (the national build's bbox, a cross-country route)
  is shown whole. The hold is flat and north-up, so the map's rotation and pitch (by drag, touch
  or key) are disabled. Every build served lies within the contiguous US and beyond it there is nothing
  to show (the desktop app's basemap is cut to it), so the box is a constant, not the served
  regions' extent. The first click sets the
  start, the second the end; later clicks add stops along the
  way, inserted before the end. A marker is numbered and coloured by its role (the theme's start,
  via and end colours: green, blue and red under classic) and dragged to move; the list shows each stop's coordinates and a remove button.
  **A city's label** places a stop too (`map/cities.ts`): a click on the label of a city, a town
  or a village the basemap draws (its text or its dot), or within 12 px (`CITY_HIT_PX`) of its
  point, puts the stop at that point, labelled with the place's name: a label under the click
  first, else the nearest point within 12 px, for a dot whose text sits beside it; of several, the
  nearest point (`cityAt`: MapLibre's `queryRenderedFeatures` at the click, then in a box of 12 px
  round it, on the layers that draw them,
  `CITY_LAYERS`: Protomaps' `places_locality` under `pmtiles`, named by `name:en`; Positron's
  `label_city`, `label_city_capital`, `label_town` and `label_village` under `openfreemap`, named
  by `name_en`, its Dark, a recoloured Positron, taken to share them, unchecked offline; none
  under `raster`). A neighbourhood, a hamlet, a state or a country is no city, nor is a label
  the map does not draw (beyond its layer's zooms, hidden by another, or a `pmtiles` basemap
  without labels); a click anywhere else places a stop where it is, as before. The list shows the
  name on its own line under the stop's role, the coordinates below it, and Go sends it as the
  stop's `label` whatever its role (**Run**). The point is the label's as its tile carries it
  (Portland's 15 m apart at zooms 7 and 10), snapped to a node by the job as any stop is.
  Dragging the marker makes it a plain stop, its name gone; editing or removing the other stops
  keeps it. The URL keeps the coordinates only (**Limitations**).
  A stop keeps its coordinates at 6 decimals, so the request and the link that reproduces it
  carry the same numbers. **Searching for a place** is the other way to place a stop: a field
  and a Search button under the list's heading. Nothing is asked while typing (Nominatim's
  policy forbids type-ahead); the button or Enter sends the text to `GET /geocode` (the server's
  proxy, cached and limited to a request a second, `docs/reference/api.md`, **Geocoding**), and
  up to five places in the region are listed under the field by their names ("No places found."
  when there are none; a place's OSM tag is its tooltip). Picking one places the next stop
  exactly as a click there would (`placeStop`: the start, the end, then stops along the way, at
  6 decimals), and clears the field; the place's name is not kept with the stop
  (**Limitations**). It asks in the region pinned in the settings, else in the served region with the largest
  bbox (`searchRegion`: the national one, which holds the others when it is served), not the one
  the stops so far are picked onto, since a stop searched for may take the trip beyond it (a
  search for Chicago with a start in Portland). The search is locked with the stops while a job
  runs, and until `/regions` has answered. Once a job has snapped them (its `snapped` event, or a stored route's
  stops), the list shows each stop's node, how far it moved and the API's note, and the map a
  dashed ghost dot where it went (the note as its tooltip). When the route visits the stops in
  another order than the list's (the worst order), the list keeps its numbering, as the markers
  do, and each stop's line says the place it is visited in, "visited 3rd" (`visitLine`, from
  the run's `order`); a stored route opened by link lists its stops in the order its route visits
  them, so it says nothing. Editing the stops forgets the route shown and the snaps, and so the
  places.
- **The build a trip runs on** (`stops/pick.ts`). The server picks it: whenever the stops'
  coordinates change (a stop placed, moved or removed; 300 ms after the last change, never for a
  job's snaps), `RegionPicker` sends them to `POST /regions/pick` (`docs/reference/api.md`,
  **Picking a build**: the smallest served region that holds every stop), and every request then
  names the region picked; an answer for stops since moved is dropped. Under the list's heading
  the region by its name and roads, the roads read from `/regions`' tiers (the first tier holding
  all of a build's edges, named as the Roads setting names them, `ROADS`: "all roads", "main
  roads"), with the server's reason under it in grey: "Oregon · all roads" / "all stops in Oregon",
  "United States · main roads" / "stops in Oregon and Illinois"; "Finding the map…" while it
  asks. Until it answers Go and Reroll are disabled. A refusal (`STOP_OUTSIDE_REGION`, a stop no
  served region holds with the
  others) is in the banner with that stop highlighted, and the header empty. Any other failure
  (`NETWORK`, a proxy's 502, a public server's `RATE_LIMITED`) is asked again a second later, up
  to three times (`PICK_RETRY_MS`, `PICK_RETRIES`); then the banner says "Could not ask the
  server which map these stops are on. Move a stop to retry." (the failure's code in its tooltip),
  and the next change of the stops asks again. A region pinned in
  the settings (**Settings**) skips the pick: the header names it, "set in the settings". Stops and settings are locked while a job runs. Under the alphabet route
  a second hint says "The alphabet route takes a start and an end, no stops between." (the
  API refuses a stop along the way).
- **Settings.** The seed (empty: random, picked by the server); under it, "Runs at once", the
  seeds a job runs at once (a portfolio, `docs/reference/api.md`, **Portfolios**): a list from 1,
  the default, to the most the server runs (`GET /config`'s `limits.portfolio_max`: 4, 2 on a public
  server, never
  more than its workers), the row hidden until the server has said it runs more than one (always,
  on a server older than portfolios), a setting above it (a link's, a route's opened by link)
  lowered to it once it has said, and a note under it from two ("runs that many searches and
  keeps the best; the map shows the one ahead"); disabled under Alphabet, whatever the number,
  with the note "the alphabet route runs one search", the number kept for another mode. A refusal
  naming `field: "portfolio"`, or `PORTFOLIO_TOO_LARGE`, highlights the row, and
  `PORTFOLIO_TOO_LARGE` lowers the setting to its `max` (**Limitations**). A row the panel hides (the
  seeds, the region with one region served) is `display: none` by `.field[hidden]`, a field's
  `display: grid` otherwise showing it: the Region row showed with one region served until then. The
  time budget (1–120 s), "Roads", the tier (`auto`, then `T0`–`T4` by the roads they keep:
  "highways", "main roads", "main and secondary roads", "most roads", "all roads", each option's
  tooltip its code and road classes, "T1: primary roads and up"), and "Corridor (km)", the leash
  (empty: auto, else up to 5,000 km), with the note "how far off the direct line it may wander";
  once a job has said, what `auto` became ("auto was all roads", "how far off the direct line it
  may wander; auto was 3.6 km"; to two decimals under a kilometre, "auto was 0.42 km", a certified
  route's). When the server serves several regions,
  a Region row: "Picked from the stops" (the default, with the note "the smallest map that holds
  every stop") or one of them by its name and roads ("United States · main roads"), which pins
  every request to it. The inefficiency target, the "Goal" row, is "As bad as
  possible", "a ratio" to the sensible route (× it, more than 1; 20 by default) or "a drive
  time" (9 h by default), typed as `9h`, `90m`, `2h30m`, `45s` or a bare number of minutes, at
  most 7 days; typing a number picks its kind, and each kind keeps its number while another is
  picked. A ratio target for a round trip is the API's `INVALID_REQUEST` (`field: "target"`),
  in the banner. The units (miles, kilometres) change what the panel shows; the API's numbers
  are metric. Last, the Theme row: "Auto (studio by day, night by night)", the default, then each
  theme by its name, never locked, the browser's to keep (**Themes**).
- **Vehicle.** Two numbers under the target: miles per gallon (24 by default, 5–100) and the gas
  price in dollars a gallon (3.89 by default, 0.50–20); a value outside them is put back. Every
  request carries them as its `vehicle` (`{mpg, gas_price_usd}`), so every stored route has its
  extra fuel and its cost. An `INVALID_REQUEST` naming `field: "vehicle"` highlights the row.
- **Departure.** A date and time (`datetime-local`, the browser's local time) under the
  vehicle, empty by default ("empty means now"). Every request carries it as its `departure`, an ISO
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
  "Alphabet" (`alphabet`: named roads whose letters run A, B, C, ...); a line under the row says
  what the mode picked does ("the longest legal route", "the slowest, counting delays", "passes as
  many of one thing as it can", "visits as many of a chain's locations as it can", "named roads in
  A, B, C order"). The brands come from
  `GET /brands?region=`, asked for the region trips run on, again whenever it changes
  (`followBrands`; an answer for a region since left is dropped, and after a failure the next
  change asks again, to the same region too), and kept in the store, in the API's order (the most
  located first), each named with its locations ("Starbucks (126)"); the first is picked unless
  the settings already name a brand the region has (a link's, or a stored route's). A brand's id
  is its build's, so when the region changes the brand picked is kept by its wikidata id, else
  its name (Starbucks, 369 on Oregon's build, is 1991 on the national one), else the first is
  picked; and the pick is kept while another mode is chosen; a region without brands has the
  Brand mode disabled. Until they have answered, the picker is disabled and reads "loading…"
  ("brands unavailable" when the request fails, its error in the banner), and under Brand Go,
  Reroll and Make it worse stay disabled (`awaitingBrand`: until the brand set is one of the
  region's), so no request goes without a brand; the other modes run meanwhile. A
  collect or brand run takes no ratio target (the API refuses one: it counts items or visits):
  under Collect or Brand the ratio is disabled, with the hint "a collect run takes a drive-time
  goal only" or "a brand run counts visits, so a drive-time goal only". The alphabet route
  takes no target at all: under Alphabet the whole target is disabled, with the hint "the
  alphabet route takes no goal". Picking a mode that refuses the target chosen makes it "as
  bad as possible" (`takesTarget` says which targets a mode takes, for the panel and for links).
  The Turns row is "Any" (the default), "Avoid left
  turns" (`no_left`: each left turn, and U-turn, costs the route a kilometre's worth of its
  score, or 90 s under the slowest time) or "Avoid right turns" (`no_right`), and a "strict"
  checkbox (none at all), enabled only when a side is chosen, with the hint "strict rarely
  finds a route on a street grid"; picking "Any" unticks it. Under them hard mode's checkbox, "No
  road twice, ever" (`hard`: its tooltip, and a note under it while ticked, "each road in one
  stretch; once left, never again"), independent of the side and of strict,
  and beside it "Worst order of stops" (`reorder`: the stops between the start and the end in
  whatever order is worst, up to 7 stops, 5 on a state's or the national map, and the ratio
  compares against the sensible route in the chosen order; its tooltip and a note under it
  while ticked say "visits the stops between in the worst order; 7 at most, 5 on a big map"),
  disabled under Alphabet, which keeps its tick for
  another mode; too many stops is the API's `TOO_MANY_STOPS_TO_REORDER`, in the banner. Beside
  them Certified's checkbox, "Prove it" (`certified`: the provably worst route in a small area, its
  tooltip and the note under it while ticked "proves there is no longer legal route in this small
  area; keep the corridor short to keep the area small"), enabled under Longest only and keeping
  its tick under another mode. Ticked under Longest it disables what a certified route does not
  take, each keeping its value
  for when it is unticked: the target kinds but "As bad as possible" (the hint "a proof finds the
  longest, so it takes no goal"), the turn sides, strict, hard mode, the worst order and the seeds
  (the hint "a proof runs one search"). An area too large to certify is the API's
  `AREA_TOO_LARGE_TO_CERTIFY` on `field: "leash"`, which highlights the Corridor row (the control
  takes 0.1 km and up), the counts and the caps in the banner.
- **Run.** Go posts the stops, a city's (**Stops**) with its name as its `label` whatever its
  role (which the directions say: "Arrive at Portland; continue", "Arrive at Portland"), each other
  stop along the way with the `label` its marker's number gives it ("Stop 2", "Stop 3", …:
  "Arrive at Stop 2; continue"; the other starts and ends go without one, so the API keeps its
  own "Arrive at your destination" and "Arrive back where you started"), and the settings (the target as
  `{kind: "ratio", value}` or `{kind: "duration", seconds}`; the `objective`; the `item` only
  under collect; the `brand`, a brand's id, only under brand; the `constraints` `{no_left,
  no_right, strict}` only when a side is avoided or hard mode is on, with `hard: true` in them
  only when it is on and `strict` only with a side, the key left out otherwise, as the API's own
  serializer does; `reorder: true` only when Worst order is ticked and the mode is not Alphabet;
  `portfolio` only when the seeds are more than one and the mode is not Alphabet; `certified:
  true` when Certified is ticked under Longest, with the target as bad as possible and no
  `constraints`, `reorder` or `portfolio`; the `vehicle`
  and the `departure`), and its 202's seed goes into the settings (a portfolio's first). Once the
  route is stored, a seed the user gave stays the settings' and the stat line's (for a portfolio
  the line names the winner's), and "best of K runs" shows only when the run asked for K: a
  route's id is its content's and the store keeps the first record, so a seed's route may come
  back with an earlier portfolio's document; a Reroll's seed, the server's, follows the document.
  Under the worst
  order the stat line says "Trying every order…" until the `snapped` event comes, which the
  server sends once it has tried them (`docs/reference/api.md`, **Jobs**). Reroll (its button
  reads "Try another") posts the same without a seed, so the server picks one. Make it worse
  posts the shown route as `base_route_id` to the route's own region (its build), with the route's
  own objective and its item or brand, from the
  route's recorded request, never the panel's (a brand's id is its build's, and the panel's are
  those of the region trips now run on, which may be another build's: Starbucks is 229 on
  Portland's build and 1991 on the national one), and with the budget, turns and hard mode,
  target, vehicle and departure from the panel (a route opened by link has put its own in the
  panel first; a target the route's objective refuses goes as as bad as possible), leaving the
  stops, tier and leash to the base route (the API's rule, which continues a worst-order route in
  the order it visits its stops, without `reorder`), and the seed after the last one tried
  on that route (its own seed at first). The Mode row is the next Go's. An alphabet route cannot
  be made worse: Make it worse is disabled while one is shown, its tooltip "The alphabet route
  cannot be made worse." (`worseRefused`), the API continuing no route under the alphabet route.
  Nor is a certified route: its tooltip says "A proven route is already the worst its area
  allows." when proven, else "Plan it again with a longer time budget." (the API would continue
  one under the
  loop within its stored leash).
  The settings' seed follows the route shown: when the
  job's `done` names the base route (nothing worse was found), the route and its seed stay, and
  the next press tries the seed after; with a target, the base route may already meet it ("Already
  at the goal."), or no route nearer it was found ("No route nearer the goal found. Try
  another."). A target below the base route's value
  shrinks it (the API's rule). Cancel sends `DELETE`; the stream then ends with `done`
  (the route so far, stored as `cancelled`) or `error`. Leaving the page sends `DELETE` with
  `keepalive` for a running job, and for one whose POST is in flight as soon as the POST is
  answered (no stream is opened for it).
- **Drawing** (`draw/drawing.ts`, `draw/panel.ts`, `map/drawLayer.ts`): a route drawn by hand. "Draw
  it", under the run's buttons once there are two stops and a region, starts drawing a route on the
  stops, against the engine's: the request is Go's (the stops, labelled as Go labels them, the
  tier, the leash, the turns and hard mode, the budget, the seed, the vehicle and the departure) as
  the longest route with no target, whatever the Mode row and the target say (`drawRequest`: a
  drawing takes no other, `docs/reference/api.md`, **Drawing a route**). The route shown goes, the
  run is `drawing` (the stops, the settings, Go, Reroll and Make it worse locked as while a job
  runs, the stat line "Drawing by hand. Click a highlighted road to take it."), today's route
  collapses, and the server's first answer (`POST /draw`, no edge yet) puts where the stops snapped
  in the list and on the map. The map then shows the drawing (`wr-drawn`, the theme's draw colour
  over its casing: violet over white under classic), the node it is at (`wr-at`) and the legal next
  moves (`wr-choices`: each move's whole edge, in the theme's par colour (green under classic) and
  dashed, a dot where it leads); hovering one names it in a popup, "Northwest Couch
  Street · 158 ft · straight" (its road, else "unnamed road", its length in the units set, the turn
  into it). A click on the map takes the move whose line passes nearest it within 8 pixels
  (`nearestMove`, on the screen, `PICK_PX`; a tie the first; the plan's 6 widened to a better
  target), never a stop; one request at a time, each answer the drawing so far with its next moves,
  and the map follows the drawing's end when it nears the edge. Undo or Backspace takes the last
  move back without asking the server (the answer before); Esc or Leave leaves drawing mode, the
  drawing kept, drawn, without its moves, and "Draw it" goes on with it. A path the server refuses
  (`ILLEGAL_PATH`, a server that has moved on) is cut back to its first illegal edge, its message in
  the banner. Finish stores the drawing (`POST /draw/finish`) once it has reached the end, or when
  the end is one move away taking the longest such move (`finalMove`), and opens the stored route as
  a link does (`openRoute`): the stat line then reads "N moves · drawn by hand · seed S", and
  directions, toasts, share image and Make it worse are a route's. The section "Drawn by hand",
  under the run's, says what to do next ("Click a highlighted road to take it.", "Head for stop 2.",
  "Head for the end.", "The end is one move away. Finish takes the longest.", "Dead end. Undo
  (Backspace) and try another way.", "Arrived. Finish stores your route.", and once left "Left
  drawing mode. Draw it picks up where you left off."), the drawing's length and
  stops ("you: 0.6 mi so far · 1 of 2 stops"), its ratio once done ("you: 14.3×"; a round trip's
  length), and the par. **The par** is the engine's route for the same request aiming at 10×
  (`DRAW_PAR_RATIO`): a normal job (`POST /jobs`, the client's share of the jobs on a public server)
  with that ratio target, started with the drawing and followed for its ratio only, nothing of it
  drawn ("the engine: aiming for 10×…", "the engine: 7.2× so far, aiming for 10×", "the engine:
  9.9×", its ratio as well where the engine stops short of the target, its budget spent or
  converged, or "the engine could not plan it: …", "the engine lost the connection to the server" when
  its stream is lost). On the short in-town trips people draw by hand the engine's full run reaches 200–320× while a
  patient drawing lands around 10–15×, so nobody beat
  it; 10× the engine reaches within ±2 % in a second or two, a bar the player sees before drawing
  much and can beat with patience. A round trip, whose ratio target the API refuses (its baseline is
  0 m when it has no stop between), keeps the full run as its par ("the engine: planning…", then its
  length, or for one with a stop between its ratios). Once both are done, the verdict, by length
  (one baseline): "You 14.3× · the engine 9.9×. You beat the engine.", "… The engine wins." or
  "… A tie.", and "Your route" and "The engine's route" open either. The drawing is forgotten, and
  its
  par cancelled if it still plans, once the stops or the settings it was drawn on (the region, the
  tier, the leash, the turns, strict, hard mode) are no longer the store's, whatever changed them
  (an edit, a route opened by link or from today's route; the store's watch, not the edit), and when
  Go, Reroll or Make it worse starts a run; the seed, the budget, the mode, the target, the vehicle,
  the departure and the units change nothing in a drawing and keep it. Leaving the page cancels the
  par too.
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
  target, the goal while the run goes ("goal 9 h") and what it reached once done ("goal 9 h,
  reached 8 h 50 min, within ±2 %", from `done`'s `metrics.target`, or the document's for
  a route opened by link); in the same row for a certified route, while the run goes, how much
  longer the worst possible may be by the solver's bound ("the worst possible is at most 0.5 %
  longer", a tenth of a percent below 10 %, from each `progress` event's `certified`), and once
  stored its certificate (`run.certified`): "no longer legal route in this area · 182
  junctions" when proven, else "longest found · the worst possible is at most 12 % longer"
  ("longest found · no bound yet" when the solver ended without one: its time ran out before it took
  in the seed), the run row then saying "proven"; the tries, the engine's attempts ("Try 12", the
  attempt of the
  last splice, while running; "12 tries", the route's attempts, once stored, "N moves" for a route
  drawn by hand; the alphabet route's count its restarts), the elapsed time,
  while the run goes and the best route so far scores above the current one (`best_score` above
  `score`), the best's ratio ("best 14.1×", from the event's `best_metrics`, shown even where it
  rounds like the current ratio or lies below it: under the slowest time the current route can be
  longer but faster than the best; since engine version 6 a max run that converges reheats and
  takes shorter splices too, so the route drawn and its ratio can fall for a while, and the
  stored route, the best, comes as the snapshot before `done`; such a run usually goes on to the
  end of its budget, where it used to converge sooner), "reconnecting…"
  while the stream is down, how the run ended in words (nothing for `budget`, the usual end;
  "nothing worse found" for `converged`, "goal reached", "cancelled", "timed out", "drawn by
  hand", "proven"; "failed"), for a portfolio "best of 4 runs", the seed (a
  portfolio's leader's while it runs, from each `progress` event's `portfolio.leader`, then its
  winner's, the stored route's `seed`; "… · best of 4 runs · seed 42" for a route opened by link
  too, from its `run.portfolio`), and, when Make it worse found nothing,
  "seed N found nothing worse". The rows follow the run's own mode (what it
  asked, a Make it
  worse its route's own, even when it found nothing and shows the base route), or a route opened
  by link's request. Under them, once the route is stored, five rows from its document (`routeLines`): when it
  arrives, the day and the minute at the departure's offset ("arrives Sat 14:57", the whole time in
  the tooltip; `formatClock`); the towns it passes through, the first two and how many more ("towns:
  Beaverton, West Slope +14", every name in the row's tooltip); the states by their USPS codes
  ("states: OR, WA", the names in the tooltip); the fuel it burns beyond the sensible route's and
  its cost ("fuel: +6.8 gal · $28.83"); and its share of the roads within reach ("roads used: 29 %
  of those within reach", "under 1 %"). A row the document has nothing for (a
  server without the places index has no towns or states; one stored without a departure has no
  arrival) is left empty, and all five are while a job runs.
- **Directions** (`directions/panel.ts`). A collapsible section under the run's, empty (with
  the hint "The directions come once a route is stored.") until the stored route's document is
  fetched, on `done` or by a link; it lists the document's `directions` with no other request,
  in a list that scrolls (45 % of the window's height). A driving step reads "Turn left onto X
  · 0.3 mi · 2 min", its distance in the units set and its time as the stat line writes times;
  a stop ("Arrive at Stop 2; continue") and the arrival are bold, a joke is in italics, muted,
  without a number of its own: the numbers are the document's, so they skip where a joke is.
  Hovering a driving step, or focusing it, draws its edges over the route in the theme's
  highlight colour (blue under classic; a `directions-highlight` line layer on top of the route
  layer's); a stop, the arrival and a
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
  "Finding a worse way.", "Skipping the shortcut.", "Still going." and the live ratio, "Currently
  17.2× worse", four slots in a fixed cycle, round again. The ratio is the run's own, from its
  snapshots and `progress` (its number follows them while
  shown; a run starting shows none of the last one's), and its slot is skipped while the run has
  none (before its first snapshot, or a round trip), so the line shown never jumps when the
  ratio comes. Once the
  route is stored (or opened by a link), its document's `toasts`, from the first, in order,
  looping. A notice, a challenge completed ("Challenge done. Make today's route worse.") or a run
  of the one set up ending short of its bar ("12.3×, the challenge needs 15×. More time, another
  run or Make it worse may get there.", **Challenges**), is shown once, for one period, before the
  lines go on where they were (several notices in turn). The pointer over the strip holds the line shown; a click shows the next one at
  once and starts the 4 s again. Nothing is shown while no job runs, no route is shown and no
  notice waits.
- **Errors.** Every API error shows its `message` in a banner over the map, as a sentence (its
  first letter a capital, a full stop at its end when it has none; its `code` when it has no
  message), its `code` in the banner's tooltip, closed by its × or the next action (a place
  search's too: `RATE_LIMITED`, "Place search is busy. Try again in a few seconds.", when the
  server's slots are taken; a search closes the banner first); an error with a `stop_index`
  (`STOP_OUTSIDE_REGION`,
  `NO_NODE_NEAR`, `STOP_UNREACHABLE_WITHOUT_REPEAT`, `INFEASIBLE_TURN_CONSTRAINT`) highlights
  that stop in the list and on the map, one whose `field` is `stops` (the alphabet route's
  refusal of a stop along the way, "the alphabet route takes a start and an end") every stop
  along the way, and an error whose `field` is `target`, `item`, `brand`, `constraints` or
  `vehicle` (`INVALID_REQUEST`) highlights the Goal, Mode, Turns or Vehicle row. A strict turn
  constraint under which the trip cannot be driven (`INFEASIBLE_TURN_CONSTRAINT`, refused by
  the `POST`) shows the server's message ("No route from stop 1 to stop 2 without repeating a road and with
  no left turns. Try without strict.") and a "Try without strict" button, which unticks strict and sends the refused
  request again without it: the same Go, Reroll or Make it worse, with its seed (none for a
  Reroll, so the server picks one) and its base route (so a refused Make it worse continues the
  route shown), hard mode kept. A refusal hard mode is why gets a "Try without "No road twice, ever"" button
  the same way (`retryOf` picks the button): a leg the seed finds no way to build under it
  (`STOP_UNREACHABLE_WITHOUT_REPEAT`, its message ending "Try without it.", refused by the
  `POST`: "No route from stop 1 to stop 2 without repeating a road and with no road twice. Try
  without it."; the API's error has no field that says hard mode is why),
  or a Make it worse whose base route comes back to a road (`BASE_ROUTE_MISMATCH`, "the base
  route comes back to OR 99E at edge 40, which hard mode refuses"). It unticks hard mode and
  sends the refused request again without `hard`, its seed and base route kept, its turn
  constraint kept and its `constraints` left out when no side is avoided. The client adds codes
  of its own: `NETWORK` (the server cannot be reached: "Cannot reach the server. Try again
  shortly."), `HTTP_<status>` (a refusal without the API's body, as a proxy's 502), `BAD_RESPONSE`
  (a
  success that is not JSON, as a page where the API should be; the message is its first 80
  characters), `STREAM_LOST` (the event stream closed and the browser gave up reconnecting:
  "Lost the connection to the server. Try again.") and
  `CLIENT_ERROR` (an error of the app's own, with its message). Every error ends the run with
  the controls unlocked: a request refused before its job exists keeps what was shown, and an
  error while following a job closes its stream (and, for `CLIENT_ERROR`, cancels the job).

## State

`Store` holds `{regions, region, pick, stops, settings, run, route, error, brands, challenge}`:
`regions`, the served ones, null until `GET /regions` has answered; `region`, the one trips run on
(the pinned one, else the pick's; null until known); `pick`, the server's pick for the stops,
`"picking"` while it is asked; `brands`, the region's, null until `GET /brands` has answered;
`challenge`, the id of the challenge set up while what it set is still the store's (**Challenges**),
else null; `run.order`, the list's index of each stop the run's route visits, in that order (null
when it is the list's own: always for a stored route opened by link, whose stops come in its
order), from the `snapped` event (for a continuation, through the base route's order) and kept
with the route stored. A module renders from it with
`watch(keys, listener)` (called at once, then after every `update` that touches one of the
keys) and changes it with `update(patch)`, `updateRun` or `updateSettings`. `run` changes up to
ten times a second while a job runs, so the stop list, the markers and the settings compare what
they show before rebuilding. The route's geometry is not in the store: it lives in
`RouteLayer`. Nor is today's route: `DailyRoute` keeps its own (the server's answer, where it
stands, its ratio so far, whether it is collapsed) and tells its section when it changes, as
`PlaceSearch` does; it reads the store's `run` to collapse when a run starts. Nor are the
challenges: `ChallengePanel` keeps the day's and a link's of another day, and says when they
change ("listed"); nor their badges: `ChallengeProgress` keeps them, and the browser's storage.

## Live growth

`RouteLayer` has four GeoJSON sources: the baseline (dashed), the route (with
`lineMetrics: true`, drawn by a `line-gradient` on `line-progress` through the theme's route
colours from the start to the end, over a casing), the flash, and on top the directions'
highlight (`highlight(line)`, **Directions**); their colours and the route's widths are the
theme's (`paintRoute`, **Themes**), under classic a grey baseline, yellow to dark red over white,
a cyan flash and a blue highlight. `line-gradient` cannot be combined with a dash pattern, hence
the separate baseline layer.

**The marks.** A route's marks, the brand locations it visits and the items it collects (the
document's `marks`, `api.md`, **The route document**), are a circle layer over these
(`MarksLayer`, `wr-marks`): each in the theme's start colour ringed in its casing colour
(`paintMarks`; under classic green over white), named in a popup on hover (text, never HTML): its
label, a brand's name, else the item in words ("toll booth", "level crossing"). They follow the
route the store shows: drawn once it is stored (on `done`, or opened by a link), and gone with it
(a run starting, an edit of the stops, a drawing); a route of another objective has none, and so
does one from a server older than them. The stat line is the same with them as without.

A route drawn by hand has its own layers over these and the marks (`DrawLayer`, **Drawing**): `wr-drawn` (the
drawing, in the theme's draw colour over its casing), `wr-choices` (the next moves, in its par
colour and dashed, with a dot where each leads, and a wide transparent line under them for the
pointer) and `wr-at` (the node
the drawing is at). Each answer of the server replaces them whole; nothing of them is spliced.

The route is kept as the stream sends it (`api.md`, **Applying the stream**): its `[lon, lat]`
points and `index`, the position among them of each route node. `subscribe` calls, per event:

- `snapped`: each stop's snap, onto the stop its `order` names (the stops come in the order the
  route visits them), the run's `order`, and the resolved tier and leash;
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

**A portfolio** grows as one route, its leader's (`api.md`, **Portfolios**): when another of its
searches takes the lead, its route comes as a `snapshot`, which replaces the line (nothing
flashed), then a `progress` with its numbers, no splice and the new `portfolio.leader`, whose seed
the stat line names; the splices after it are the new leader's. Nothing else differs from one
search, so a server's portfolio needs no other drawing.

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
answered (and for `openfreemap` its style has loaded), `addBasemap` adds the basemap's sources
and layers below them (before the first layer the map has), its fonts (`setGlyphs`) and icons
(`setSprite`) when it has labels, in the variant the theme names (`Basemap.show`; **Themes**). A
change of variant takes the last one's layers and sources away and adds the new one's in their
place, below the route's layers, which keep their data. If `/config` fails the banner shows the error and the map has
no basemap: it never falls back to another, and a `pmtiles` answer without its file, or an
`openfreemap` style that does not load (offline), draws none either (and logs an error on the
console).

- **`raster`** (the server's default): OpenStreetMap's raster tiles
  (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`, desaturated so the route stands out) with
  the attribution "© OpenStreetMap contributors", at hobby volume, as the tile usage policy
  allows locally; the browser's own `User-Agent` and `Referer` are what the policy asks for.
  MapLibre GL JS 6 fetches only the tiles in view (it has no prefetch option) and nothing is
  cached offline. The policy rules these tiles out for a public site. One look, whatever the
  theme's variant.
- **`pmtiles`**: Protomaps' vector tiles from one file the server holds, read through
  `pmtiles://` (the `pmtiles` package's `Protocol`, registered in `map.ts` with `addProtocol`)
  by HTTP byte ranges at `/api/basemap/<region>.pmtiles`; drawn by `@protomaps/basemaps` in the
  flavor of the theme's variant: `grayscale` (classic's and studio's), muted so the route stands
  out, `light` or `dark`, the last two without their points of interest, whose icons the server's
  sprite (grayscale's) lacks, and with their land cover; with labels (`layers(…, {lang: "en"})`) when
  the server has their fonts and icons (`glyphs` and `sprite`) and without them otherwise, so a
  deployment without `fetch_basemap.sh --assets` still has a basemap. The labels ask for four
  fonts, the ones the script fetches (`basemap.test.ts` holds the style to them). The source's
  attribution is "Protomaps © OpenStreetMap", linked, the app's own string: never a tileset's
  metadata, whose HTML the attribution control would render.
- **`openfreemap`**: OpenFreeMap's vector tiles (the OpenMapTiles schema) from its public
  instance, no key and no limits, drawn in the style the server names (`style`: OpenFreeMap's
  Positron, light grey, so the route stands out), or for the dark variant OpenFreeMap's Dark (the
  same URL, `/styles/dark` for `/styles/positron`), which `fetchStyle` fetches the first time the
  variant is drawn and `Basemap` keeps for the next (one that did not load is asked for again):
  its layers as they are, the sources they read (not the Natural Earth relief it declares and never draws), its
  fonts and its icons from OpenFreeMap. The source's attribution is the app's own string,
  "OpenFreeMap © OpenMapTiles Data from OpenStreetMap", linked; Positron's design is CC BY 4.0,
  credited on the notices page. MapLibre fetches only the tiles in view, as with `raster`.

**The land outside the contiguous US** is drawn flat (`map/outside.ts`): the land of Canada,
Mexico and the rest of the world but the 48 states and DC takes one flat tone, the theme's
`outside` colour (**Themes**), so it reads as not part of the app, while the water and the states
keep the basemap's look. The outline is `public/conus.json`, the contiguous US as
`tools/desktop_data.py conus` writes it (the 48 states and DC from the places index's states,
buffered 10 km and simplified, about 22 kB), committed as the command writes it (Prettier leaves it
alone) and written again with `uv run python tools/desktop_data.py conus frontend/public/conus.json`
when the Census outline changes; the page fetches it beside itself (`conus.json`, relative), with
`/config`, and keeps each polygon's outer ring, so Lake Michigan and the bays the Census coast
leaves out stay the US's. `withOutside` adds to the basemap's parts by its make:

- **`pmtiles`**: the file is cut to the outline (`tools/fetch_basemap.sh --region`), so a tile or so
  beyond it there are no tiles and the sea there is only the basemap's background, which any fill
  over the world would paint. Two land fills go instead just above Protomaps' `earth` layer (the
  land) and, in the light and dark flavors, the `landcover` right after it: `earth-outside`, the
  earth layer again (its source, source layer and filter) in the outside tone, so all land is flat,
  and `us-land`, the outline from a GeoJSON source of its own (`us-land`) filled in the flavor's
  land colour, read from the earth layer's `fill-color` (`#cccccc` grayscale, `#e2dfda` light,
  `#1f1f1f` dark), so the states are land again. The land's uses, the water, the roads, the
  boundaries and the labels draw over both as they did; the sea is left as it was, water where the
  file has tiles and the background past them. The land cover goes under the fills, the US's with
  the rest: drawn over them it would cover the land outside again until zoom 7, where it fades out,
  so in the light and dark flavors the US shows its land colour where it showed its cover below
  zoom 7.
- **`openfreemap`** (OpenFreeMap's tiles cover the world) **and `raster`**: the mask, a GeoJSON
  source of its own (`outside`) holding a ring round the world, counterclockwise, with each of the
  outline's polygons as a hole, clockwise (MapLibre rewinds the rings itself; the world's ring first
  is what makes the rest holes), and a fill layer (`outside`) at opacity 1 that `withOutside` puts
  just below the basemap's first water layer (by its source layer: OpenMapTiles' `water`,
  `waterway` or `water_name`): over the land's layers, under the water and everything the basemap
  draws after it, so the seas, the Great Lakes and the rivers draw as they do everywhere and
  nothing inside the outline changes. The raster basemap (dev only) has no vector layers: the mask
  goes over its tiles, and with no vector water the sea outside the outline is flat too. A basemap
  that draws nothing (a `pmtiles` answer without its file, an OpenFreeMap style that did not load)
  gets the mask alone, the US's shape on the flat tone.

Either way the basemap's labels but the water's (its `symbol` layers) draw a point only within the
outline: `["any", ["!=", ["geometry-type"], "Point"], ["within", <the outline>]]` ANDed onto each
layer's own filter, a legacy one converted to an expression first (`map/filters.ts`, as the style
spec's migration converter does, since a legacy filter inside an expression is misread), so the
names of places, regions, countries and islands beyond it are not drawn, nor taken by a click
(**Stops**). Labels along a line (road names, shields) and every line are drawn as they are:
`within` takes a feature whole, and at low zooms the basemap merges a tile's roads or boundaries of
one kind into a few features, so a filter on lines dropped the US's with Canada's (**Measured**).

`Basemap` takes the fills or the mask with the basemap's parts (its `extend`), so a change of
variant swaps them with the rest, the land colour read again from the new flavor, always under the
route's layers and removed before their sources; `paintOutside` recolours the outside tone when the
theme changes and the variant does not. An outline that does not load draws neither (an error on the
console) and the basemap as it is. The border effects (**Limitations**): the outline runs 10 km out
from the borders and the coast, so a strip of Canada and Mexico along the border is drawn as the
basemap has it (Windsor across from Detroit, Tijuana's edge, the towns facing the Great Lakes' US
shore); islands the Census outline lacks (the Farallon Islands off San Francisco) take the outside
tone; where a `pmtiles` file has no tiles, land and sea alike show the background, painted the
flavor's water colour so the sea past the edge stays sea; the
roads, the boundaries, the road names and the shields outside the outline are drawn over the flat
tone; and OpenFreeMap's Positron draws some land cover after its water (residential areas, woods,
buildings), which shows over the flat tone outside.

The URLs are made absolute under the page's origin and `/api` (MapLibre needs absolute ones,
and the templates' braces must stay unencoded). Below the panel a footer credits the data: "Road
data © OpenStreetMap contributors (ODbL) · Towns and states: U.S. Census Bureau · Data sources",
linked, the last to `notices.html` (relative, in a new tab): "Data sources and notices", every
source with its licence and a link (`docs/reference/hosting.md`, **Attribution**), then under
a Fonts heading the themes' typefaces with their copyright notices and the SIL Open Font License
1.1 once, a plain page in `public/` that Vite copies into `dist/` as it is, the app's colours
inline. The basemap's own
credit is in the map's attribution control, its "OpenStreetMap" linked to
openstreetmap.org/copyright. Agents verifying the app block the
tile server (**Seeing it**), so their screenshots show the route on a blank background with
`raster`, or on a basemap of their own making with `pmtiles` (**Measured**).

## Themes

The app's look is a theme (`theme/`): every colour, font, radius and marker shape of the panel,
the route's colours and widths on the map and the basemap's variant come from one `Theme`
(`theme/theme.ts`):

- `id`, `name` (the settings' option), `scheme` (`light` or `dark`: the page's `color-scheme`,
  which native controls and scrollbars follow) and `basemap`, the tiles' variant (`grayscale`,
  `light` or `dark`, **Basemap**);
- `colors`: the panel's (`panel`, `ink`, `muted`, `line`, `hover`), `accent` and `accentInk` (Go,
  the big ratio), `link` (links, the challenge set up, the focus ring; optional, the accent when
  left out), `mapBg` (behind the tiles while they load), `outside` (the land outside the contiguous US, one
  flat tone over the basemap's land, **Basemap**), the markers' (`start`, `via`, `end`,
  `markerInk`; `start` the route's marks too), the map's (`route`, one to four colours spread
  evenly from the start to the end, one a flat line; `routeCasing`, under the route and the drawing
  and round a marker and a mark; `baseline`;
  `flash`; `highlight`, a step of the directions; `draw`, a drawing; `par`, its next moves), the
  toasts' strip (`toast`, `toastInk`) and the banner's (`errorBg`, `errorInk`);
- `fonts`: three CSS font stacks, `display` (the brand, the section headings and Go), `body` (the
  rest) and `numbers` (the big ratio), and `load`, which imports the theme's fontsource CSS
  (`import("@fontsource/manrope/800.css")`): each is a chunk of its own, fetched when the theme is
  first chosen, never before; classic's system stack loads nothing;
- `shape`: `radius` (sections, buttons, fields; a step's at most 4 px) and `markerRadius` (stop
  markers, their badges in the list and the challenges' badges: `50%` round), `routeWidth` and
  `casingWidth` (the route's line widths, 4 and 7 under classic), `uppercase` (the brand and Go in
  capitals; the section headings always are) and `glow` (the casing drawn in the route's first
  colour, wide, translucent and blurred).

**The presets** are `theme/presets/<id>.ts`, one `Theme` a file and nothing else, and `THEMES` in
`presets/index.ts`, the settings' order. Built: `studio`, the default (white, ink and grey on
Protomaps' grayscale flavor, one wrong-way orange for the route, Go and the ratio, its teal start
also the flash and a step's highlight, which must read against the orange route; Manrope), and
`classic`, the app's first look (the yellow-to-red gradient, the system's fonts, the grayscale
flavor, blue links, the page's white behind the tiles), kept exactly but for MapLibre's zoom buttons
and attribution and the share section's two links, which the themes colour (below), and eight more,
each with its one bold thing named in its file's comment: night drive (navy, a glowing magenta
route, Sora, dark tiles), transit (black on white, a signal-orange metro line with no casing,
station circles, Archivo), topo (paper and pine, a trail-orange route, Zilla Slab and Nunito Sans;
its contour lines later), arcade (a navy nav unit, a yellow route, square corners and markers, Press
Start 2P and VT323, dark tiles), dashboard (an amber readout on a dark panel, square chips, IBM Plex
Mono and Sans, dark tiles), road atlas (atlas blue over a white casing, highway red for Go, Barlow
Condensed and Source Sans 3), highway sign (a green panel, a warning-yellow route over a black
casing, Overpass) and ledger (black type on white, one stamp-red route, square markers, Courier
Prime). Their fonts are Fontsource's packages, a stylesheet per weight imported dynamically by
`load`, so a theme's are fetched only when it is chosen. A preset changes values only: it adds no
CSS, no selector and no layer, and what the interface does not name it cannot set (the buttons' and
badges' background, `--field`, is white under a light scheme and the panel under a dark one; native
inputs follow `color-scheme`; the shadows are black).

**Putting one on the page** (`theme/apply.ts`): `applyTheme` sets `data-theme="<id>"` on
`<html>` and writes `themeProperties(theme)` there as inline custom properties (`--panel`,
`--ink`, `--muted`, `--line`, `--hover`, `--field`, `--accent`, `--accent-ink`, `--link`,
`--map-bg`, `--start`, `--via`, `--end`, `--marker-ink`, `--route`, `--route-casing`, `--ghost`,
`--attribution`, `--attribution-ink`, `--baseline`, `--flash`, `--highlight`, `--draw`, `--par`,
`--toast`, `--toast-ink`, `--error-bg`, `--error-ink`, `--font-display`, `--font-body`,
`--font-numbers`, `--radius`, `--marker-radius`, `--brand-case`) with `color-scheme`, then loads
its fonts and resolves once they are in (`document.fonts.load` of each stack at the weight the
page asks of it: 700, 400, 800). Fonts are at best: a font's chunk or file that does not load (an
offline start, a hashed file replaced by a newer build) is said once on the console's debug level
and the page keeps the stack's fallbacks; `applyTheme` never rejects. The translucent colours are
derived as `rgb()` with an alpha, which every web view takes, where `color-mix()` is too new for
an older macOS's WKWebView: `--ghost`, a snapped stop's dot (the casing at 70 %), and
`--attribution` and `--attribution-ink`, the attribution's strip (`--field` at 50 %) and links
(`--ink` at 75 %). `style.css` styles through these variables only, with no colour, font or
radius of its own (but its two shadows) and no selector for a theme; `data-theme` tells a look
apart, nothing styles by it. Its `:root` holds studio's values, the default theme's, so the page
is styled before the script has run (a test holds them to `themeProperties(studio)`); the theme
chosen is written over them. The variables cover MapLibre's controls too (`#map`'s rules outrank
`maplibre-gl.css`, which the bundle puts after `style.css`): the zoom buttons on `--field` with
`--line` between them, their icons in `--ink` (MapLibre's own shapes as CSS masks), and the
attribution's strip and links; and the share section's two links, in `--link`.
The root forbids a synthesized bold (`font-synthesis: style`): each theme loads the weights it
uses, and a face that exists at one weight only (arcade's pixel faces) must not be smeared into a
fake bold; a slant may still be synthesized, since no theme loads an italic face and the
directions' jokes are in italics. The map's layers take theirs from the theme too: `paintRoute` (the baseline, the casing,
the route's gradient and width, the flash, the highlight), `paintMarks` (the route's marks),
`paintDrawing` (the drawing, its next moves, the node it is at) and `paintOutside` (the land outside the US, **Basemap**), as the
layers are added and again by `setPaintProperty` whenever the theme changes; and `Basemap.show(theme.basemap)` swaps the basemap's layers and sources in
place when the variant changes (**Basemap**), not `setStyle`, which would drop the route's layers
and their data.

**The setting** (`settings.theme`, `theme/resolve.ts`) is a theme's id or `auto`, the default:
studio while the system is light and night while it is dark (`prefers-color-scheme`, followed as it
changes). An id no theme has gives the default. It is the device's own, kept in the browser's
`localStorage` under `worst-route.theme`, read before anything is drawn and written when the row
changes it, every read and write in a try (a private window, storage blocked: the setting lasts as
long as the page). It is not in the URL, unlike the request's settings (**Links**), so a link
carries no theme and opens in the receiver's own; nor is it in a request. The row
(`settings/themeRow.ts`), last in the settings, lists "Auto (studio by day, night by night)", then
each theme by its name; it is never locked, a theme changing nothing a job asks.

## Links

The URL carries the request:
`?seed=&seeds=&budget=&tier=&leash=&target=&mode=&turns=&strict=&hard=&reorder=&certified=&vehicle=&departure=&region=&stops=lat,lon;lat,lon;…`,
the target as `max`, `ratio:<x>` or `duration:<seconds>`, the mode as `distance`, `time`,
`collect:<item>` (`collect:bridge`), `brand:<id>` (`brand:229`, a brand's id, digits only) or
`alphabet`, the turns as `left` or `right` and `strict=1` (read only with a side), hard mode as
`hard=1`, the worst order as `reorder=1`, Certified as `certified=1`, a portfolio's seeds as `seeds=2` to `seeds=4` (written
only above one; any other value ignored), the vehicle as `<mpg>,<gas price>` (`vehicle=30,4.25`, both within the panel's limits), the
departure as an ISO 8601 time with its offset, encoded (`departure=2026-10-31T09%3A00%3A00-05%3A00`;
a day the month lacks is not one), the region pinned as its id (`region=us-t1`, only when pinned:
otherwise the server picks it from the stops again, the same for the same regions served; a
region the server does not serve is unpinned); the mode, the turns and the vehicle are written
only when not the default (Longest, Any, 24 mpg at $3.89; a pilgrimage only once its brand is
known), hard mode, the worst order, Certified and the departure only when set, so a distance
link reads as before. A certified route opened by `?route=` ticks Certified, its request's leash
(`auto` when it was asked so) into the panel, so Go plans the same area again. A worst-order route opened by `?route=` gives its stops in the order its route visits
them, Worst order ticked (planning them again picks the same order). A portfolio's route opened by
`?route=` gives its seeds (lowered to the server's most) and its request's seed, the first, so Go
plans the same searches. A
`?route=` link leaves the pin as the link has it: the route's stops are picked like any others,
and Make it worse continues the route on its own build, in its own objective with its own item
or brand (**Using it**, **Run**). A target the mode refuses reads
as "as bad as possible": a ratio beside `mode=collect:…` or `mode=brand:…`, any beside
`mode=alphabet`. A brand id the region's brands lack is replaced by the first of them once
`/brands` has answered. The URL is rewritten (`history.replaceState`, by
`UrlSync`) whenever the stops or settings change, so a link reproduces the request (Go with the
same seed plans the same route on the same build). A value that does not parse, or lies outside
the API's limits, is ignored and the default kept. Once a route is shown, the URL
also carries `route=<id>`; the share section offers `…/?route=<id>` with a copy button, and an
"Open share image" link to `/api/routes/<id>/share.png?theme=<id>` (`shareImageUrl`), the server's
PNG of the route in the theme the page shows (`resolveTheme`: the setting, `auto` by the system's
scheme; `followShareImage` changes the link as the setting or the scheme changes, **Themes**;
`docs/reference/api.md`, **Share image**), opening in a new tab (`rel="noopener"`), and a
"Download GeoJSON" link to `/api/routes/<id>/geojson` (`geojsonUrl`), the route, its baseline
and its stops as a FeatureCollection, saved as `worst-route-<id>.geojson` (the `download`
attribute, the proxy keeping it on the page's origin).

`?daily` opens today's route at start (`decodeUrlState`'s `daily`, `openDailyAtStart`): once `GET
/daily` says it is done (after its plan, if the server is planning it), the route opens as
`?route=<id>` would, and the URL, left as it came until then, is written whole (`route=<id>` and
the rest); a day that is failed, or a server without a daily route, opens nothing and keeps
`?daily`. It opens only while the user has done nothing since the page loaded: a run started (Go,
Reroll) or a route opened ("Open it") before the day's route is done ends the wait there, the
user's own run and route left alone and the URL following the app from then on. The app's own
state never writes `daily`, and beside `route=` it is ignored.

`?challenge=<id>` sets that challenge up once `/regions` has answered and the challenge has come, as
its Set it up does. Its id's day decides where it comes from. Today's (the day of today's challenges
once the server has answered them, before that today's route's, `GET /daily`, else the browser's UTC
date) waits for today's challenges, through a failure and Try again. Another day's within the
server's window (today and the 30 days before) is asked of the server at once, beside today's (`GET
/challenges?date=`), and listed first, "From Sunday, November 1" above its title (the server lists
today's route made worse in today's only, and Set it up refuses another day's should a link forge
one); one that today's turn out to hold after all is listed once, as today's, and one of today's
that today's lack (a clock that differs from the server's) is asked for by its day after them. An id
that does not end in a day (as the fixed list's of old), a day the server does not answer (outside
its window, or on a public server a past day it has not kept: a 422, today's challenges listed
still), or an id its day lacks sets nothing up, and nor does a link once the user has started a run
(one that failed too) or opened a route while the challenge came. Today's route made worse
(`?challenge=daily-worse-2026-11-02`) waits for today's route to be done (after its plan, while the
server plans it), then opens it, unless the user has started a run or opened a route meanwhile; a
day that could not be planned, or a server without a daily route, sets nothing up. The URL carries
`challenge=<id>` for as long as the challenge stays set up (**Challenges**); beside `route=` or
`daily` it is ignored.

`?route=<id>` opens a stored route without running anything (`openRoute`): `GET /routes/{id}`
and `GET /routes/{id}/geojson` (for the baseline), the stops and settings from the route's own
request (its seed, budget, tier, leash, target, objective, item, brand, turn constraints, hard
mode, vehicle and departure; the panel keeps its item and its brand under another objective, its
vehicle when the request has none or one outside the panel's limits, as the URL's is, and its
departure when the request has none;
for a "make it worse" route, the base route's stops),
the snaps from its stops, the route drawn whole, the camera fitted to it, its numbers in the
stat line, its directions and its toasts, from which Make it worse and Reroll work as after a
run (an alphabet route's link sets Alphabet, and Make it worse is off for it). The URL is left as it
came until the route has opened; then it is written whole. A link that does not open (a route
not stored, a build gone) keeps its `route=` and shows the API's error in the banner; the URL
follows the app again from the next change. The units and the theme are not in the URL.

## Tests

`npm test` (vitest, in Node; the gate runs it after the build):

| Test file | What it covers |
|---|---|
| `map/routeLayer.test.ts` | `spliceLine` on hand-made lines (a longer and a shorter detour, either end, the input left alone); each recording replayed: it ends with the stored route's node count, every node where the job's final snapshot has it (whose stop positions are the route's), every drawn point a point of the stored geometry in order with the same ends; and at every snapshot after splices (the max-mode recording's after its 50th `progress`), the replayed nodes equal the snapshot's (the alphabet and certified recordings' snapshots follow none; the certified one's straight streets are drawn unsimplified); the alphabet recording through a `RouteLayer` on a stand-in map: each snapshot drawn whole, no flash ever, the stored geometry drawn at the end; `paintRoute`: classic's paint the first look's (yellow to dark red in four even stops, the route 4 wide over a white casing 7 wide, the grey baseline, the cyan flash, the blue highlight), one colour a flat gradient, a glow in the route's colour, wide and blurred |
| `theme/apply.test.ts` | `themeProperties`: classic's the stylesheet's first values (the panel, the hover, white buttons, blue links, white behind the tiles, the ghost's and the attribution's translucent colours, 6 px, round markers, no capitals, the system's fonts); links in the accent unless a theme names them, a dark scheme's buttons and attribution on its panel, a bare 0 radius written 0px, capitals, a three-digit casing made translucent; `style.css`'s `:root` holding studio's values; every variable `style.css` reads written, for every theme; `applyTheme` on a stand-in root: `data-theme` and every property, then the fonts loaded and asked for at the three weights; a font chunk and a font file that fail, the properties set all the same, one debug line each, no error |
| `map/marksLayer.test.ts` | `MarksLayer` on a stand-in map over a real store: the shown document's marks each at its place, a brand's by its name and an item's in words; none without a route, a new route's own, none for a route with an empty list or none (an older server's); nothing drawn again while the route is the same; the popup on hover and closed when the pointer leaves; `paintMarks`: classic's start green over its white casing at creation, another theme's on a repaint |
| `map/drawLayer.test.ts` | `paintDrawing`: classic's paint the first look's (violet over white, the next moves green), another theme's draw, par and casing colours |
| `theme/resolve.test.ts` | auto studio by day and night by night, an id its theme, an unknown id the default; `storedTheme`: an id or auto read back, anything else auto, and auto when the storage throws; `followTheme`: the theme shown at once, again when the system's scheme flips under auto and when the setting changes (kept in the storage), not for another setting nor a flip under an id; a storage that throws survived |
| `settings/themeRow.test.ts` | the picker's options: auto first, then every theme by its name, in their order |
| `api/types.test.ts` | each type's runtime spec, which the compiler holds to the type's keys; the specs against `api.md` (its JSON request example, and the field lists of the events, `done`'s metrics, `/regions` (its `name` optional: the recordings predate it), `/regions/pick`, the 202, the job status, the route document, `/routes/{id}/directions`, `/brands`, `/geocode`, `/config` `/daily`, with its example, `/challenges`, with its example's whole challenges and the tests' day (an unknown goal and constraints not all named refused), `/draw` and `/draw/finish`) and against every payload of the eight recordings and the recorded drawing's answers, finish and refusal; the ratio recording's target reached in `done` and the document, its round-trip refusal; the slowest-time recording's time ratio, delays and turns in `done` and the document, its refusal of both turn constraints; the collect recording's bridges counted in every `progress`, in `done` and the document, its refusal of a ratio target; the directions recording's vehicle, fuel and cost, states and towns, its steps numbered from 1 with the driving steps' ranges covering the route's edges in order and adding up to its length, its first toast, its refusal of a vehicle of 0 mpg; the brand recording's visits never falling in its `progress` events (8, 13, 14 of 19), `done` and the document agreeing, its refusal of a ratio target; the alphabet recording's better routes, each a snapshot then a `progress` with no splice, the same metrics and a higher score, from "A" to "ABCDEFGHIJKLMNO", `done` and the document agreeing, its refusal of a stop along the way (`field: "stops"`); the route document's marks (a brand's with its name, an item's without; an empty list; a kind not among them refused); a payload that does not fit is caught; the request's `reorder` and the `order` of `snapped` and the route document (optional: the recordings predate them) |
| `map/basemap.test.ts` | `basemapParts`: OSM's raster tiles for `raster`; nothing (an error on the console, never OSM's tiles) for `pmtiles` without its file; Protomaps' tiles from the server for `pmtiles`, its `pmtiles://` URL absolute under `/api`, the attribution the app's own, no symbol layer without the server's fonts; with them the fonts' and icons' URLs absolute with the template's braces kept, and the labels asking for no font but the four `tools/fetch_basemap.sh` fetches; `addBasemap` on a stand-in map: the fonts, icons and sources set, then every layer before the first layer the map already had (the route's), or on top of an empty map; the light and dark flavors with their own background, without points of interest, on the grayscale sprite and the four fonts, grayscale the default; `styleUrl`: OpenFreeMap's Dark for dark, Positron otherwise; `Basemap` on a stand-in map: a new variant's layers and sources swapped in under the route's, nothing redone for the same look, OpenFreeMap's style fetched once for each look (Positron once for grayscale and light) and kept, a style that did not load asked for again, raster one look |
| `map/cities.test.ts` | `cityAt` on a stand-in map: a label drawn under the click taken however far its point (20 px), before a nearer point beside it, the nearer point of two under the click, and no box asked then; else the nearest point within 12 px wins, at its point with its name; none when no label is under the click and every point lies beyond 12 px though its label meets the box, one at 12 px exactly taken; the queries the click's point, then a box of 12 px round it, on the layers the style has, and none when it has none (raster, a pmtiles basemap without labels or none yet, another kind's layers); each basemap's name (`name:en`, `name_en`, then `name`), a feature without one or not a point skipped; `CITY_LAYERS` per kind, and Protomaps' `places_locality` the one locality layer among its four place layers in each flavor drawn, absent without labels |
| `map/outside.test.ts` | `public/conus.json`: a GeoJSON MultiPolygon of closed rings within the contiguous US's box (−125, 24.4, −66.8, 49.5) and under 100 kB; `fetchOutline`: the outline fetched beside the page (`conus.json`) with its polygons' own holes filled, null and an error on the console when it does not load; `maskOf`: a ring round the world, counterclockwise, with each polygon's outer ring a hole, clockwise, whichever way it was wound, the committed outline's every polygon cut out; `withOutside` on a stand-in map that keeps its layers in order: on Protomaps' grayscale flavor `background`, `earth`, `earth-outside` (the earth layer again in the theme's tone), `us-land` (the outline from a GeoJSON source of its own in `#cccccc`), then the land's uses and the rest, the route's layers above everything, and no mask; on the light and dark flavors the two fills above `landcover`, `us-land` in each flavor's land colour; OpenFreeMap's mask just below its first water layer, over its land's layers, from a GeoJSON source of its own; the mask on top of the raster tiles, and alone when the basemap draws nothing; the basemap as it is when the outline did not load; through a change of variant (grayscale to dark) the fills over the new flavor's earth and land cover, `us-land` in the dark land colour and the outside tone the theme then shown, the last basemap's layers removed before their sources; every flavor's labels but the water's drawing a point only within the outline, their own filters kept (a legacy one converted), every other layer as it is; OpenFreeMap's city labels filtered and its lines and water names not; the labels still filtered after a change of variant; `paintOutside` colouring the mask, or Protomaps' `earth-outside`, once drawn, nothing before |
| `map/filters.test.ts` | `asExpression` against the style spec's migration converter on each legacy form: comparisons on a property, the geometry's type and the id, against null; `in` and `!in` as a `match` over sorted unique values, mixed types as `any`/`all` of comparisons, no values; `has` and `!has`; `all`, `any` with each term's type checked first, `none`; a filter in expression syntax, or one that reads the same in both, returned as it is; no legacy node left in any of Protomaps' flavors' filters once converted |
| `map/bounds.test.ts` | `US_BOUNDS` runs west to east and south to north, and holds the national build's extent and Portland's bbox; `fitZoom` against the zooms MapLibre gave for the box filling a map of three sizes, the lesser of its two axes'; `holdToUs`: a view smaller than the box kept inside it (a centre outside put half a view from its edge), the whole box kept in a view larger than it on that axis (taller and wider maps at the fit zoom), a zoom below the fit raised to it, a camera already held unchanged |
| `run/controls.test.ts` | `JobRunner` with `fetch` and `EventSource` stubbed: the stat line while drawing and for a route drawn by hand ("25 moves · drawn by hand · seed 1", "1 move"); how a run ended, in words ("1 try · 2.0 s · nothing worse found · seed 1", nothing for a spent budget); "goal 25.0×, reached 25.0×, within ±2 %" and "goal reached"; a TypeError from the client and an event the app cannot handle end the run as `CLIENT_ERROR` with the controls unlocked (the job cancelled), the server's `error` closes the stream; Make it worse keeps the route and its seed when nothing worse is found and tries the next seed, follows a worse route's seed; Go and Reroll send the settings' seed, then none; leaving the page cancels a job whose POST is in flight once it is answered, a running one at once; a ratio and a drive-time target sent, shown while running and with what was achieved (`target reached`), and Make it worse whose base already meets its target; the objective, the item only under collect and the constraints only with a side sent (and not by default), the brand only under brand, the alphabet route with no item nor brand, Brand waiting for the region's brands with the brand set among them (none, a link's before they answer, one they lack, an empty list) and no other mode waiting, hard mode's `hard: true` alone or beside a side (the key left out when off, strict sent only with a side), Make it worse in its route's own objective with its own item or brand, whatever the panel's mode and brand (a pilgrimage under hard mode, its whole body, the panel's brand another build's id), with the panel's turns and target (as bad as possible where the route's objective refuses it); the slowest-time recording replayed (the lengths' ratio while running, then the time ratio, the drive and delays, the turns) and the collect recording's bridges counted live, then the stored route's; the route's own mode shown when Make it worse, the panel in another, finds nothing; "no lefts" and "no rights" (not with a U-turn); `INFEASIBLE_TURN_CONSTRAINT` as the error, and the refused request sent again without strict: a Go with its seed, a Make it worse on the same route with its seed, a Reroll with none, hard mode kept; the counter row's "17 of 19 Starbucks" and "letters: 23 (A–W)", "letters: 1 (A)", "letters: 0", the laps past Z for 26, 27 and 29 letters ("(A–Z)", "(A–Z, A)", "(A–Z, A–C)"), the roads one a line in its tooltip, nothing under another mode; Make it worse off for an alphabet route with its reason, whatever the mode set, and for no other route; the brand recording's visits counted live (1, 8, 13, 14 of 19), then the stored route's; the alphabet recording replayed: every snapshot drawn, no splice, the ratio and letters following each better route (1.1× and 1, 1.0× and 2, ...), the stored route drawn, its fifteen roads in the tooltip; a refusal under hard mode sent again without it: a Go with its seed and no constraints left, a Make it worse with its base and seed and its strict side kept, a Reroll with none, and a Make it worse whose base hard mode refuses (`BASE_ROUTE_MISMATCH`) on the same route with the same seed; each stop along the way labelled as its marker is numbered ("Stop 2", "Stop 3"), the start and the end without a label, and a city's stop by its name whatever its role; the vehicle set sent by Make it worse, Go and Reroll (the default in Make it worse's whole body); the departure set sent, or, empty, the time Go is pressed at the browser's offset (on a fake clock); "arrives Sat 17:42" from a document's arrival at its own offset, nothing without one; the directions recording replayed: no route rows while it runs, then its towns, states, fuel and share of the roads; the first two towns and how many more with every name in the tooltip, two states by their codes, no fuel without a vehicle, "under 1 %", and nothing from a document without them; the worst order sent only when ticked and never under the alphabet route, "Trying every order…" until `snapped`, its stops snapped onto the list in the order it names and the run's order kept with the stored route, Make it worse's stops through the base route's order, and no order for a run in the list's own, nor for a `snapped` without one (an older server's), whose stops say no place; the best's ratio beside the current one while the best route scores above it ("best 14.1×"), "reconnecting…" while the stream is down, also when the two round alike and when the best's is the lower (a faster but longer current route under the slowest time), not when the current route is the best nor from a stream without the best's numbers; a certified route sent with as bad as possible and no constraints, worst order or portfolio, whatever the panel holds (kept), and Certified counting for nothing under another mode; the certified recording replayed: the bound live on the target row ("at most 6368 % longer" at first, "0.5 %" while proving), no splice, then "certified: no legal route in this area is longer · 182 nodes" and "proven"; a stored route's certificate when not proven, with its gap and without one ("longest found · no bound yet"), and Make it worse off for a certified route with the reason for each; a portfolio sent only above one seed and never under the alphabet route, the stat line's "best of 4 runs" with its leader's seed from each `progress` event (after the leader's "best 3.2×" while its best scores above its current route), then the stored route's winner, the settings keeping the first seed, and Make it worse from the winner's next seed with no portfolio; `PORTFOLIO_TOO_LARGE` lowering the seeds to its `max`, the next Go passing; a route an earlier portfolio stored coming back to a Go of one seed (the user's seed kept on the panel and the stat line, no "best of") and to a portfolio's Go (its winner's seed shown, its first kept) |
| `directions/panel.test.ts` | on the directions recording: a row per step classed by its kind, numbered as the document numbers them (none for the 40 jokes, the numbers skipping them), a driving step's distance and time in miles and kilometres, the arrival's and a stop's rows with none; `StepLocator`: the driving steps' lines ending on their first and last nodes (more than 99 % exactly, all within a metre; the job's last snapshot gives where the nodes are) and covering the geometry end to end, a step given at least one segment however short its distance, no line for a step that drives nothing, every step starting where it is; `DirectionsPanel` with a stand-in map: the rows of the route shown and none without one, the units followed, a hover highlighting the right edges, from node to node (once for the same step), a joke and none highlighting nothing, a click panning to the step's start, the highlight forgotten when the route changes, a document without directions listing nothing |
| `settings/panel.test.ts` | the brand picker with `fetch` stubbed: one `GET /api/brands?region=`, the options in the API's order named with their locations ("Starbucks (126)", "(1,234)"), the first picked when the settings name none, the list put in the store, a brand the region has kept and one it lacks replaced, a region without brands leaving the settings alone; the brand picked kept across a change of region by its wikidata id, else its name, else the first; the brands of a region since left dropped; the seeds picker's options, 1 up to the server's most and never above 4, a setting above it lowered to it, then and whenever raised later (`followPortfolioMax`), and the stylesheet's `.field[hidden]` rule, without which a hidden row shows (no DOM here); the region picker's options, the server's pick first, then each region by its name and roads; `followBrands`: one request per region it changes to, a region's answer dropped once it is left, and after a failure (the region null meanwhile) the same region asked again |
| `banner.test.ts` | `sentence`: a message with its first letter a capital and a full stop at its end, one it already has kept; `retryOf`: "Try without strict" for `INFEASIBLE_TURN_CONSTRAINT`, "Try without hard mode" for `STOP_UNREACHABLE_WITHOUT_REPEAT` under hard mode and `BASE_ROUTE_MISMATCH` naming hard mode, nothing for the same codes without it (a base route a strict constraint refuses) nor for another refusal |
| `stops/pick.test.ts` | `RegionPicker` with `fetch` stubbed on fake timers over a real store: no request without stops, one `POST /api/regions/pick` after the stops stop changing (for the last of them), no region meanwhile (so Go waits) and "Finding the map…" in the header, then the region picked and the header "Oregon · all roads" with its reason, then "United States · main roads" for a stop in Chicago; an answer for stops since moved dropped; a job's snaps asking nothing; a refusal in the banner with its stop, asked no more; any other failure asked again a second later, up to three times, then the banner saying the server could not be asked, until the stops change; a pinned region taken without asking, "set in the settings", and the pick asked again once unpinned; nothing before `/regions`, and a pin the server does not serve dropped; `roadsOf` and `regionLabel` from the real builds' tiers |
| `stops/stopList.test.ts` | `stopErrored`: the stop of an error's `stop_index`, every stop along the way for `field: "stops"`, none otherwise; `visitLine`: "visited 1st" to "visited 5th" for a route visiting the list 0, 1, 3, 2, 4, nothing in the list's own order or for a stop moved since; a city's stop keeping its name through the edits of the other stops and losing it once dragged; `stopLabel`: a city's name whatever its role, else "Stop 2" along the way and none for the ends |
| `toasts.test.ts` | `Toasts` on fake timers: nothing and no timer while idle; the three waiting lines and the live ratio every 4 s while a job starts and runs (the ratio following `progress` while shown, round again), the ratio's slot skipped without one, and the line shown kept when the ratio comes; none of the last run's ratio while the next one's POST is in flight (and the route's back when it is refused); the stored route's toasts from the first, in order, every 4 s, looping (ten toasts, so ten periods come round); held under the pointer, a click moving on at once and starting the period again; two notices, each once for a period, then the route's toasts from the first, a click moving on from a notice too; a notice with nothing else to show, then nothing and no timer; nothing once the route is forgotten, nor for a document without toasts |
| `api/client.test.ts` | the API's error bodies, `BAD_RESPONSE` for an HTML success, `HTTP_<status>`, `NETWORK`, `CLIENT_ERROR` |
| `stops/search.test.ts` | `PlaceSearch` with `fetch` stubbed and a real store: nothing asked while typing, one `GET /api/geocode?q=&region=` on Search, nothing for a blank text or before `/regions` has answered; the region asked in, the pinned one, else the served region with the largest bbox (Chicago searched for with a start in Portland asks the national one); the candidates listed, an empty answer said; a pick placing the stop exactly as `placeStop` (a click) does, as the start and as a stop before the end, and clearing the field; nothing placed or asked while a job runs; a 429 in the banner, and a search closing it |
| `share/share.test.ts` | the URL state round trip (every field, a pinned region and none, values it ignores, `auto`, seeds 0 and 2⁵³ − 1, clicked coordinates exactly), values it ignores, the app's state to the URL and back; a `?route=` link at startup: the URL untouched until the route opens, then written from its request, and kept, with the banner, when it does not open; targets in the URL, values ignored, and a ratio-target route opened by link; every mode and turn setting round-tripped, values ignored (strict without a side among them), a ratio beside a collect mode read as max, the defaults left out, the settings given back; a brand (`brand:229`, `brand:0`), the alphabet route and hard mode round-tripped with every turn setting, malformed brands ignored (none, a name, negative, a fraction, an exponent, a space, beyond 2⁵³ − 1), `alphabet:1` and a `hard` other than 1 ignored, a ratio beside a brand and any target beside the alphabet route read as max, a pilgrimage whose brand is not known yet written without a mode, the brand kept under another mode; a slowest-time and a collecting route opened by link (mode, item and turns into the settings, the URL and the stat line, the panel's item kept), the recorded pilgrimage and alphabet route (`mode=brand:229` and "14 of 19 Starbucks", `mode=alphabet` and "letters: 15 (A–O)" with its roads); a pilgrimage, an alphabet route and a hard-mode route (alone and beside a side) opened by link, hard mode unticked by a route without it; the share image's URL, with a theme and without; its link in the theme shown, followed through `auto` by day and by night, a theme set and classic, none without a route; the vehicle round-tripped, at its limits, the default left out, values ignored; a route opened by link putting its request's vehicle into the settings and the URL (the default not written), and one without a vehicle, or with one outside the panel's limits, keeping the panel's; the departure round-tripped with its offset (encoded), what is not an ISO time with an offset ignored, a route's link putting its request's departure into the settings and the URL, and one without keeping the panel's; the GeoJSON's URL; `?daily` read (and written back alone), never written from the app's state; the worst order round-tripped as `reorder=1` only when on, values other than 1 ignored, and a worst-order route opened by link ticking it, its stops in its route's order and no "visited" places, Worst order unticked by a route without it; `?challenge=` read and written back (a day's ids among them), an id that is not one ignored, written from the app's state only while a challenge is set up, and dropped from the URL once it is not; a portfolio's seeds round-tripped as `seeds=4` only above one, other values ignored, and a portfolio's route opened by link putting its seeds and first seed into the settings and the URL, its winner's seed on the stat line, lowered to a server's most of two, a route of one search setting the seeds back to one; Certified round-tripped as `certified=1` only when on, other values ignored, a certified route opened by link ticking it and a route without unticking it |
| `notices.test.ts` | the footer's "Data sources" link (relative, `notices.html`, in a new tab) beside the two credits; the notices page's title and its sources, each with its licence and link, in order (OR-Tools, its three Python dependencies and the libraries bundled in its native library among them); its Fonts section, each theme's typeface with its copyright notice as the font's name table gives it, in order, every Fontsource package the app depends on among them, and the SIL Open Font License's text once; a build (Vite's own, into a temporary directory) holding `notices.html` as it is |
| `draw/drawing.test.ts` | `Drawing` with `fetch` and `EventSource` stubbed over a real store, answered from a drawing recorded on Portland (`portland-draw.json`): the request (the longest route with no target, whatever the panel's mode and target; the turns and hard mode, the labels, a city's stop by its name whatever its role, the seed); the start (one `POST /api/draw` with no edge, the run `drawing` and locked, the stops snapped, the route shown cleared) and the par (`POST /api/jobs` with the same request and a ratio target of 10, a round trip's as it is, its stream followed); moves taken only when offered, taken back without a request; a refused path cut back to its first illegal edge with the error in the banner; the drawing forgotten, its par cancelled, once the stops change (a stop moved; another route opened over a drawing left with Esc) or a setting it was drawn on (the tier, the leash, the turns, strict, hard mode, the region), kept when one it does not depend on changes (the seed, the budget, the units, the mode, the vehicle, the departure, the target) and by the stops' snaps; Finish taking the longest move to the end, the stored route opened, the drawing then neither active nor drawn; the par's line as it plans (its aim, then its ratio so far) and where it stopped short, its failure, and the verdict both ways once both are done, either route opened; leaving drawing mode and resuming without asking again; the drawing forgotten and its par cancelled when a run starts; an answer for a drawing since forgotten dropped; a start the server refuses giving the run back with no par; `nearestMove` (the nearer line, nothing beyond the pixels, a tie to the first), `moveLabel`, `finalMove`, the panel's lines at the end, at a dead end and for a round trip |
| `challenges/list.test.ts` | a day's answer's challenges, in its order, each with its day and region (a day lacking a type too); the day an id ends in, and none for an id without one (the fixed list's of old among them) |
| `challenges/progress.test.ts` | grading the recordings' stored routes moved to the challenge's region and stops (`__fixtures__/challenges.ts`, a day's answer as the API gives it): the max-mode recording's 78× and the ratio recording's 25× at 16×, not at 30×, nor on Portland's region and stops, another region, another mode, the worst order or a round trip; the time recording's 6.1 h with its delays reaching 6 h and not 7, nor 4.1 h without its delays, nor under the longest; no left turn (the directions recording's 259 left turns not, with none at 14.5× yes, at 15× not, nor a route without turns); the collect recording's 18 bridges short of 20 and reaching 18, not under another item; the brand recording's 14 of 19 Starbucks by name (no brands loaded) and by wikidata id, not when the build's brand 229 is another, nor against McDonald's; hard mode's ratio only under hard mode; today's route made worse (its base and at least its ratio, on the region of today's route, `us-t1` too; not shrunk below it, not a continuation of another route, not without a day, not a forged yesterday's challenge, not on another region); a stop 489 m away the challenge's and one 512 m not, nor another number of stops; the worst order's stops between in any order, the start and the end in place, and not without the worst order; the completions recorded once for the routes the store shows, with the toast's line, in the storage; the route shown graded again when the list comes; past days' badges kept and the fixed list's of old dropped at the next write, read back by another page, reset (storage cleared); junk in the storage ignored; a storage that throws, or none, survived for as long as the page; the standing on a row: "Done" once done, whatever is shown; a ratio so far to the tenth below against its bar ("12.3× so far · needs 16×", "15.9×" for 15.99×), the worst order's too; a drive time in whole hours, a count of an item, a brand's visits ("6 h of 50 h", "18 of 20 bridges", "14 of 19 Starbucks"); no left turns under strict no-left only and hard mode under hard mode only; nothing off the trip, with no route, nor for today's route made worse; the run going on the trip once it has numbers ("7 of 20 bridges", "8 of 19 Starbucks"), nothing before them, while it starts or under another item; a run of the challenge set up ending short of the bar said once ("12.3×, the challenge needs 16×. More time, another run or Make it worse may get there."), again for the next run (Make it worse finding nothing worse), not when the same route is shown again, nor for a run that reaches the bar (completed instead), fails, or had nothing set up, nor for a challenge done already, nor when its POST was refused and a route opened after; each goal kind's words ("6 h, the challenge needs 50 h.", "18 bridges, the challenge needs 20.", "14 Starbucks, the challenge needs 19.", "1.3×, the challenge needs 1.4×.", "1.6×, the challenge needs 2×."), none for today's route made worse |
| `challenges/panel.test.ts` | `ChallengePanel` over a real store, `fetch` stubbed with a day's answer and today's route stubbed: today's asked for (`/api/challenges`) and listed once answered, with their day and region; none on a server without them (404); failed when the server is out of reach or a proxy answers 502, and asked for again (Try again), loading meanwhile; a day lacking a type; Set it up refused while a run goes, today's route made worse until its route is done; Set it up pinning the region, putting in the stops with their towns and the settings, the rest of those their defaults (the worst order and Certified unticked, the automatic tier and leash) and the seed, budget and vehicle the panel's, the route shown forgotten, the challenge in the store, the map fitted to the stops; each type's mode, turns, strict, hard mode and worst order; the brand picked once the region's brands are known, by wikidata id over the one `loadBrands` carried, else by name when already loaded, and the challenge no longer set up once another brand is picked; set up through a run, its seed, the budget and the snaps, and no longer once the item, the worst order, the leash, the tier, the region or a stop changes; today's route made worse opening today's route, set up while it is shown; a link's challenge, on a fixed clock: today's set up once they have come, and through a 504 and Try again, listed once; another day's asked for its day (`?date=`) at once while today's are still to come, listed and set up, then first once today's come, and the same through a 504 and Try again; today's day from today's route over the browser's clock, the browser's a day ahead without it (today's asked for by its day too, listed once as today's) and a day behind (yesterday's asked for after today's); not set up once a run has started meanwhile, one that failed too, nor once a route was opened; nothing for an id without a day, a day the server does not answer (422; today's still listed), a type its day lacks or today's lacks; the collapse when a run or a drawing starts; `doneLine`; the hint |
| `daily/panel.test.ts` | `DailyRoute` with `fetch` and `EventSource` stubbed over a real store: a busy server's 429 a day to try again; planning, its job's stream followed (the ratio so far from its `snapshot` and `progress`), then on `done` the stream closed and `GET /daily` asked again, done, with the route's id for `?daily` (`route`), and never a `DELETE`; a route done at once, no stream opened; failed (no route for `?daily`), asked again, and asked again when the stream ends in an `error` or is lost; none on a server without a daily route; collapsed when a run of the user's own starts, not when a route is opened, shown again from the link, not collapsed again by the same run; `dailyLine` in each state; `openDailyAtStart`: today's route opened once done while the user has done nothing, never once a run has started (the wait ended at once, the run's job left as it is) or a route has been opened, nor for a failed day |
| `format.test.ts` | distances in both units and their thresholds, durations, ratios, counts, items one and many; drive times typed as a target, read and written back; fuel and its cost, percentages ("under 1 %"), states by their codes and a name not known; a time written at the browser's offset to the second and read back, ISO times with an offset read and others (a day the month lacks among them) refused, a time shown as a clock at its own offset; a UTC day named as the daily route names it; places in an order (1st, 2nd, 3rd, 4th, 11th to 13th, 21st, 101st, 111th) |
| `theme/presets/presets.test.ts` | `THEMES` in the order the settings list them, each id once; every preset's colours in hex, its route one to four of them; every preset's `outside` colour, darker than its `mapBg` under a light scheme and lighter under a dark one; each preset's `load` resolving (the fontsource stylesheets it imports found, so a weight a package lacks fails here) |

The drawn points are not the stored geometry point for point: a snapshot is simplified
(`api.md`, **Applying the stream**) and a splice is not, so the replay has fewer points; it is
compared node by node, and as an in-order subsequence of the stored geometry once consecutive
duplicates are dropped (the document's geometry drops them, a stream's points keep them).

**The recorded drawing**, `portland-draw.json` (23 kB), is a route drawn through `POST /draw` on
Portland's build 0b72253e5e8a (pipeline version 8, OSM's turn restrictions among its turn tables)
from a lane's `serve`: Powell's → Pioneer Courthouse Square, seed 1, drawn by a script that took
the offered move nearest a few waypoints round the blocks to the north and east and backed up from
dead ends, 115 edges to one move before the end. It holds the request, the path, the answers with
no edge, one and two edges, the answer one move before the end (whose moves are one on and one to
the end), the answer at the end (14.3×), `POST /draw/finish`'s answer, and a path refused
(`ILLEGAL_PATH`, `BROKEN_CHAIN` at edge 2). To record it again (after an API change, or on another
build): run `serve`, draw so with `httpx` and write the same keys.

`src/__fixtures__/http.ts` holds the stand-ins: `stubFetch`, `FakeEventSource` and a route view
that records what it is asked to draw; `src/__fixtures__/challenges.ts` a day's challenges as `GET
/challenges` answers them (`dayOf`, a date's on `us-t3`, today's route made worse only in
today's; `ANSWER`, 2026-11-02's eight).

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
`portland-certified-stream.json` (0.13 MB) is a certified route along NE Alberta Street from 15th
to 30th (45.559, −122.65 → 45.559, −122.64), seed 1, the automatic leash (0.42 km: 182 nodes,
578 edges), recorded from a lane's `serve` on build 0b72253e5e8a: 20 better routes, each a
snapshot then a `progress` without splices, every `progress` with the solver's bound and gap, a
first one alone before any better route (the seed against the presolve's bound, at most 6,368 %
longer) and one between the last better route and the proof, proven in 4.1 s at 21.5×; its
refusal is the area within a 1.5 km leash (`AREA_TOO_LARGE_TO_CERTIFY`, `field: "leash"`). Its
straight grid streets leave its snapshots nothing to simplify. To record one again
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
front of `npm run preview`. Block the tile server for the whole browser, not per DevTools session:
a block set with `Network.setBlockedURLs` ends with its session, and a page left open afterwards
(a run that ends and fits the camera, say) fetches OSM's raster tiles. Launch Chrome with
`--host-resolver-rules="MAP tile.openstreetmap.org ~NOTFOUND"` (or behind a proxy that refuses
it), and close the pages before the driver lets go.

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
  (`strict=1`; the recording predates engine version 4, under which these clicked stops still
  refuse on build 3a4a3df6269f, while Powell's → Reed at the tests' points plans): refused by the
  `POST` with `INFEASIBLE_TURN_CONSTRAINT` ("no route from stop 0 to stop 1 without repeating a
  road, after moving stop 1 up to 10 times, under the strict no-left constraint; try without
  strict"), the end stop highlighted and the "Try without
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
- **Two builds** (2026-10-31, a lane's `serve --region oregon --region us-t1 --geocoder none`,
  builds ea3b18096e8b and c4a85b3d36d0 with both places indexes, the dev server proxying to it,
  headless Chrome with the tile server blocked): the map opened on the contiguous US, the Region
  row listing "Picked from the stops", "Oregon · all roads" and "United States · primary roads
  and up". A link with stops at Powell's and Bend asked `POST /api/regions/pick` once and the
  header read "Oregon · all roads" over "all stops in Oregon"; one ending in Chicago, "United
  States · primary roads and up" over "stops in Oregon and Illinois"; with `region=us-t1`, no pick
  asked and "set in the settings". Go (seed 1, 6 s) posted `region: "oregon"` for Bend, 10.2×,
  1,618 mi, states OR, and `region: "us-t1"` for Chicago, 7.0×, 14,653 mi through 17 states, the
  stats and toasts as on one build. Under Brand with Starbucks picked on Oregon's build
  (`mode=brand:369`), removing the end and clicking near Chicago asked the pick again, then
  `GET /api/brands?region=us-t1`, and kept Starbucks (`mode=brand:1991`, "Starbucks (3,721)").
- **Today's route** (2026-10-31, a lane's `serve --region us-t3 --region oregon --region portland`
  on builds 281a455e3d15, ea3b18096e8b and a453e105582b with their places indexes, the dev server
  proxying to it, headless Chrome with the tile server blocked): the first page of the day read
  "Today's route", "Saturday, October 31", "Sageville, IA → Acworth, GA" and "planning…", then
  "planning… 4.7× so far" from the job's stream, and once the server's job was done "6.6× less
  efficient than the sensible route", the share image and Open it. Open it drew the route
  (`a076702bbc25`, 5,344 mi through 8 states) and wrote `?route=a076702bbc25&seed=20261031&…`;
  Go collapsed the section to the header's "Today's route", which showed it again. `?daily`
  opened the same route at start, the URL rewritten the same way.

- **Challenges** (2026-10-31): the first fixed list, ten challenges, was measured through a lane's
  server then, and removed on 2026-11-02 for the day's (below).
- **The day's challenges** (the evening of 2026-11-01 CST, 2026-11-02 UTC; a lane's `serve
  --region us-t3 --geocoder none` on build 6adae6917be1, the dev server proxying to it, headless
  Chrome at 1400 × 1000 with the tile hosts unresolvable). First with `GET /challenges` stubbed as
  the contract writes it, answering after 8 s: "Loading today's challenges…" under the heading
  while today's route said "Planning… 1.0× so far", then the eight in the server's order, today's
  route made worse disabled until its route was done. Set it up on "Four towns in the worst order
  at 5.5×" listed Yakima, Ellensburg, Wenatchee and Moses Lake under Start, Stop, Stop and End,
  ticked Worst order, pinned `us-t3` ("United States · most roads, set in the settings"), fitted
  the map to the four and wrote
  `?challenge=worst-order-2026-11-02&…&reorder=1&region=us-t3&stops=…`.
  `?challenge=hard-mode-2026-11-01` listed that day's first under "From Sunday, November 1",
  highlighted, and set it up (hard mode ticked, Boise to Twin Falls). Today's route made worse,
  set up once the day's route was done (Butler, AL → Burkittsville, MD, 4.7×), the budget lowered
  to 5 s: Make it worse came to 5.8× (2,168 tries), the toast "Challenge done. Make today's route
  worse.", `{"daily-worse-2026-11-02":"2026-11-01"}` in the storage, and once the section was
  shown again its badge and "1 challenge done" above Reset badges. With today's first call
  answered 502, `?challenge=hard-mode-2026-11-01` showed "Couldn't load today's challenges." and
  Try again, which said "Loading today's challenges…" and then listed the eight under the linked
  one, set up. `?challenge=slow-way-2026-10-30` asked for both days in the same second and was set
  up 8.3 s after the page loaded, one answer's wait, not two. Then against the server's endpoint
  (its series on 861a674 with its bars at 80 % of a plan's, `serve --region us-t3` started fresh,
  load averages of 3–5): "Loading today's challenges…" for 42.0 s, the day's first call, then the
  eight of 2026-11-02 as `docs/reference/api.md` shows them, from "Losantville, IN to Wallace, IN
  at 15×" to "Make today's route worse". Set it up on "Hard mode: Bondurant, IA to Brayton, IA at
  2×" listed the two towns, ticked "No road twice, ever" and wrote
  `?challenge=hard-mode-2026-11-02&…&hard=1&region=us-t3`; Go, on a seed the server picked: 4.2×
  in 20.0 s (11,960 tries), the toast "Challenge done. Hard mode: Bondurant, IA to Brayton, IA at
  2×.", the badge, "1 challenge done" and `{"hard-mode-2026-11-02":"2026-11-01"}` in the storage.
  A new page at `?challenge=worst-order-2026-10-31` listed today's eight from the server's cache
  in 0.3 s, the badge kept, and asked for October 31 at once; the server, making one date at a
  time, made it after today's, and "Four towns in the worst order at 7.5×" (Fisher, MN, Kathryn,
  ND, Bejou, MN and Amenia, ND under Start, Stop, Stop and End, Worst order ticked) was listed
  first under "From Saturday, October 31" and set up 48.5 s after the page loaded (81.8 s on a
  fresh server, which made today's first).
- **The standing, the short toast and the marks** (2026-11-02 UTC; a lane's `serve --region
  us-t3 --geocoder none` on build 6adae6917be1 with Protomaps' basemap from its file, behind a stub
  of the lane's that answered `GET /daily` 404, `GET /challenges` with a day of three and added
  marks made up along the route to `GET /routes/{id}`, the server's series not landed; the dev
  server proxying to the stub, headless Chrome at 1400 × 1000 with the tile hosts unresolvable).
  `?challenge=pilgrimage-2026-11-02` set up "Visit 40 Dollar General between Hustonville, KY and
  Ravenna, KY" with the hint under the list, Dollar General picked (`mode=brand:858`); Go, the
  section shown again six seconds in: "9 of 40 Dollar General" on the row; the run ended on its
  20 s budget at 13.1× (786 mi, 13,792 tries, 13 of the 14 within reach): "13 of 40 Dollar General"
  and the toast "13 Dollar General, the challenge needs 40. More time, another run or Make it
  worse may get there." The 13 marks were drawn over the route in night's cyan ringed in its navy
  casing, and in studio's teal ringed in white once the Theme row changed it, no reload; the
  pointer over one showed "Dollar General", gone once it left. Then on the server's series, its
  marks and its titles (the two series together, `serve` started fresh: today's eight made at start
  in 49.3 s, `GET /challenges` answered at once afterwards, no stub):
  `?challenge=pilgrimage-2026-11-02` set up "Visit 11 Dollar General between Glenfield, ND and
  Webster, SD"; Go, the section shown again five seconds in: "14 of 11 Dollar General" on the row,
  past the bar before the route was stored; the run ended on its 20 s budget at 21.3× (4,059 mi,
  9,192 tries, 14 of the 15 within reach): the document's 14 marks, each a `brand` labelled "Dollar
  General", drawn at the stores along the route (Jamestown, Fargo, Aberdeen, Brookings among them),
  the row "Done" beside its badge, and the toast "Challenge done. Visit 11 Dollar General between
  Glenfield, ND and Webster, SD."
- **The seeds** (2026-11-01, headless Chrome on the lane's `npm run dev` and `serve --region
  portland --region us-t3`, ten workers, the tile hosts unresolvable, load 9–15): a link with
  `seed=40&seeds=4&budget=20` and Powell's → Reed showed the Seeds row at 4 with its note; Go ran
  "attempt 6,963 · 4.6 s · getting worse… · best of 4 seeds · seed 43", the seed changing with the
  leader, and ended "28,905 attempts · 20.0 s · budget · best of 4 seeds · seed 42", 58.3×, the
  Seed field still 40. Under Alphabet (`mode=alphabet&seeds=3`) the row was disabled at 3 with
  "the alphabet route runs one search", and with the seeds at 1 the same. On a server of Portland
  alone with one worker (`--workers 1`), a link with `seeds=4` showed neither the Seeds row nor the
  Region row.
- **Themes** (2026-11-01, headless Chrome at 1400×900 on the lane's `npm run dev` and `serve
  --region portland --geocoder none --basemap-name us`, `WORST_ROUTE_BASEMAP=pmtiles`, the main
  tree's `us.pmtiles`, fonts and sprite read, the tile hosts unresolvable; a stored Powell's → Reed
  route, seed 1, 56.6×): four views of the page at classic (the top of the panel, the route
  opened, the run section with the directions, the stops and settings) were the same pixel for
  pixel before the change and after it but for the toasts' strip, whose line rotates, and for a
  challenge's badge completed between the two shots; so was the route opened again after
  switching at classic → a dark stand-in → studio → classic with the row. Studio: the route a flat
  orange line over white, Manrope (500 and 800 loaded, `document.fonts.check` true), the teal
  start, the toasts' strip tinted. Then, themed after review, MapLibre's controls and the share
  links (on main 7e7fd77, the same route): at classic the route view differed from the first
  shots only in the zoom buttons (148 pixels, no channel by more than 20: the icon `--ink` for
  MapLibre's #333, the divider `--line` for its #ddd), the attribution's links (912 pixels, at most
  30: `--ink` at 75 % for black at 75 %), the share links (classic's blue for the browser's), and
  in today's route, a new day's; studio on the grayscale flavor, a hover over the longest step
  (1.7 mi on Southeast McLoughlin Boulevard) adding 1,336 teal pixels on the map; the dark
  stand-in's zoom buttons on its panel (11, 16, 32) with icons in its ink (230, 233, 255), the
  attribution a dark strip with light text. A stand-in dark theme
  (night's palette, a glow, never committed) under auto with `prefers-color-scheme: dark`: the dark
  flavor, the panel dark, buttons on it with light text. No error or warning on the console in any
  of them; the grayscale sprite under the light and dark flavors asked for no missing icon. Drawing
  mode took the next moves' colour from each theme (green under classic, as before).
- **A city's label** (2026-11-01, headless Chrome at 1400×900 on the lane's `serve --region
  portland --geocoder none --basemap-name us --frontend dist`, `WORST_ROUTE_BASEMAP=pmtiles`, the
  main tree's `us.pmtiles`, fonts and sprite read, build 0b72253e5e8a, the tile hosts and
  Nominatim unresolvable): at zoom 10 over Portland the map drew 24 localities' labels; a click on
  the point of Portland's put the start there, listed "Start", "Portland", "45.520240,
  −122.674198"; a click 30 px east of it, on the label's text, its point 30 px away, put the end on
  Portland's point too, named (a round trip); a click 30 px north of the point, no label under it,
  a plain stop between them at 45.534912, −122.674245; Go sent `[{"lat": 45.52024, "lon":
  -122.674198, "label": "Portland"}, {"lat": 45.534912, "lon": -122.674245, "label": "Stop 2"},
  {"lat": 45.52024, "lon": -122.674198, "label": "Portland"}]`. Before a label under the click
  counted (the 12 px rule alone), the click on the text had placed a plain end; with it, Go's
  route was 123.8× (the start snapped 68 ft), dragging the start's marker 40 px listed it by its
  coordinates alone, and with a plain start and Portland as the end the request labelled the end
  "Portland" and the stored route's directions ended "Arrive at Portland". No request left the
  page's origin.
- **The land outside the contiguous US** (2026-11-02, headless Chrome at 1400×900 on the lane's
  `serve --region portland --region us-t3 --geocoder none --basemap-name us --frontend dist`,
  `WORST_ROUTE_BASEMAP=pmtiles`, the main tree's `us.pmtiles`, cut to the outline, fonts and sprite
  read, the tile hosts unresolvable; a stored Portland → Chicago route on `us-t3`, seed 1, 5.1×): at
  the national fit (zoom 3.59) under studio, Canada and Mexico one flat #dfe2e6 with their roads and
  boundaries on it and none of their places named; the Pacific, the Gulf, Hudson Bay and the Great
  Lakes the grayscale flavor's grey; the states as before, their labels whole ("San Francisco",
  "Miami", "Washington"), the route over all. Florida at zoom 6 and San Diego at 7, where the file's
  tiles end offshore, show sea all the way, studio's and night's (a mask over the world painted
  29–40 % of those views flat); past the tiles, Mexico's land south of Ensenada shows the
  background, as before. The layers: `background`, `earth`, `earth-outside`, `us-land`,
  `landuse_park`, ..., `places_country`, then `wr-baseline` and the route's; under night (a change
  of variant) `background`, `earth`, `landcover`, `earth-outside` (#141a30), `us-land` (#1f1f1f),
  and under classic (paint only) `earth-outside` #e2e2df. At Detroit, zoom 10, Ontario flat beyond
  the 10 km margin and Windsor's shore within it drawn as the basemap has it; over Portland at zoom
  14 the Willamette's bridges drawn. Under atlas and night the land cover is under the fills: with
  the fills right above `earth` instead, the national fit showed Canada and Mexico in the same green
  cover as the US. Each light theme's tone was set on screen against the light flavor's land
  (#e2dfda), a clear step darker: the first tones tried (atlas #e6e3da, ledger #e6e6e4, transit
  #e3e3e3) sat within a few levels of it and the edge barely showed at Detroit; arcade's first,
  #0f2a78, read as sea. Copies of the water drawn over a fill above every basemap layer were tried
  first and dropped: they covered every bridge (the Willamette's at zoom 14) and the parts of labels
  over water ("San Francisco" read "Francisco", "Miami" "Mia"). The labels' filter, measured over
  the first stacking (a mask below the water, which it does not depend on): the national fit left
  México, Ciudad de México, Puebla, Chihuahua, Cancún, Cuba, the Bahamas, Jamaica, the Dominican
  Republic, Bermuda, Vancouver, Calgary, Edmonton, Winnipeg, Toronto, Ottawa and six provinces
  unnamed, Detroit at zoom 10 Lakeshore, Essex, McGregor, Cottam and Belle River, while Tecumseh,
  LaSalle and Amherstburg, within the margin, kept their names and the 401 its shields; every road,
  boundary and US place name drawn without it was drawn with it, from the national fit to zoom 10
  (Washington, Ohio and Kansas at 6, Seattle at 8, Chicago at 9, Portland, Buffalo and Detroit at
  10). Its cost, the time from the basemap's parts made to the map idle, five loads with it and five
  without, alternated: at the national fit a median of 406 ms against 393, after a jump to Portland
  at zoom 12 646 ms against 736, so none to measure. The same `within` on every line and label but
  the water's, tried first, cost none either (455 against 479 ms, 635 against 635) but drew 2 of the
  national fit's 21 road and boundary features, the interstates of Washington and Oregon gone at
  zoom 6 (51 of 72), and was dropped.

## Limitations

- In the light and dark Protomaps flavors (eight themes) the states lose their land-cover tint at
  zoom 7 and below: the flat tone's land fills sit above `landcover`, so Canada and Mexico stay flat
  there at the cost of the US showing the flavor's plain land colour (**Basemap**).
- **A challenge is completed by any route shown that meets it**, a route opened by someone else's
  link included: there are no accounts, and the badges are only this browser's.
- **Making today's route worse for its challenge takes that route's budget**: opening a route puts
  its request's budget in the settings, the server's largest for today's route (120 s, 60 s on a
  public server), so Make it worse runs that long unless the budget is changed first.
- **A badge's day is the browser's**, a challenge's the server's (UTC): November 2's made worse on
  the evening of November 1 in Chicago reads "Completed Sunday, November 1".

- **A stream through the dev proxy whose upstream drops is not ended**: Vite's proxy keeps the
  browser's response open, so if the API dies mid-run the stat line stays at its last try.
  A connection dropped between the browser and the server is reconnected as above.
- **Seeds above 2⁵³ − 1** cannot be typed: the API allows up to 2⁶³ − 1, but a JavaScript number
  is exact only up to 2⁵³. A stored route with a larger seed (from the CLI or a script) shows it
  rounded.
- **Under the slowest time the large ratio changes kind when the run ends**: the stat line shows
  the experienced time's ratio ("× slower") only once the route is stored (from the document's
  `time_ratio`), and the lengths' ratio while the run goes; the drive and delays row below it is
  live from `progress`'s `delay_s` and settles on the stored route's.
- **Seeds before the server has said how many it runs**: until `GET /config` answers, the Runs
  at once row is hidden and a link's `seeds=4` is kept, so a Go in that window on a server that
  runs fewer is refused with `PORTFOLIO_TOO_LARGE`, shown in the banner as any refusal, and the
  setting is
  lowered to the error's `max`, so the next Go passes; the row appears once `/config` answers.
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
- **The land outside the contiguous US is flat only beyond its 10 km margin**, and the basemap's
  roads, boundaries, road names and shields are drawn there over the flat tone (**Basemap**):
  Windsor, across the river from Detroit, is drawn as the basemap has it, as is Canada's shore
  facing the Great Lakes' US one, and Ontario's 401 keeps its shields; the places beyond the margin
  go unnamed. Islands the Census outline lacks, beyond the margin, take the outside tone (the
  Farallon Islands off San Francisco). On the `pmtiles` basemap the light and dark flavors' land
  cover is under the flat tone, the US's too, so below zoom 7 the states show the flavor's plain
  land colour; and past the file's tiles land and sea alike show the background, which is the
  flavor's water colour, so Baja and Vancouver Island's far side read as sea.
- **A city's name is kept only until the page reloads**: the URL keeps the stops' coordinates
  alone, so a reload, a link or a stored route opened lists a city's stop by its coordinates
  (a stored route's request and directions keep the name it was sent with).
- **A searched place keeps no name** (a follow-up): a place picked from the search is placed by
  its point only, listed by its coordinates and labelled in the request as a click's, unlike a
  city's label clicked on the map.
- **The fuel is in US gallons and dollars** whatever the units, as the vehicle is set in mpg and
  dollars a gallon.
