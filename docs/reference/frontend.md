# Frontend

The single-page app under `frontend/`: a map of the served region on which the user clicks a
start, an end and stops along the way, starts a job, watches the worst route grow live, cancels,
rerolls, makes the route worse, and opens a stored route by link. Vite + TypeScript (strict), no
framework, MapLibre GL JS 5. The design it implements is `docs/PLAN.md` §5; the conventions are
`CLAUDE.md`. It talks to the backend through the API only (`docs/reference/api.md`), and changes
nothing there.

| Module | What it holds |
|---|---|
| `main.ts` | bootstrap and wiring: the store from the URL's state, the panel's parts, the region's bbox, a stored route when the URL names one, clicks on the map, the cancel on `pagehide` |
| `state.ts` | `Store`, the app's state on an `EventTarget`; `Settings`, `Stop`, `RunView`, `targetOf`; the API's limits (tiers, budget, leash, targets) |
| `api/types.ts` | the API's shapes, written by hand from `api.md` |
| `api/client.ts` | the fetch wrappers, `ApiError`, `errorBody`, `subscribe` (a job's events over `EventSource`) |
| `map/basemap.ts` | the basemap and its switch (**Basemap**) |
| `map/map.ts` | the MapLibre map, its controls, `fitTo` |
| `map/routeLayer.ts` | `spliceLine` and `RouteLayer`: the baseline, the growing route, the flash (**Live growth**) |
| `map/stopsLayer.ts` | the stop markers and the snapped-position ghosts |
| `stops/stopList.ts` | how a click, a drag or a removal changes the stops; the list in the panel |
| `settings/panel.ts` | the request's settings and the units |
| `run/controls.ts` | `JobRunner`, a job's lifecycle (no DOM, so the tests drive it); `mountRunControls`, Go, Cancel, Reroll, Make it worse and the stat line |
| `share/share.ts` | the URL state, `UrlSync`, `openRoute`, the share section (**Links**) |
| `banner.ts` | the error banner |
| `format.ts` | distances (mi/km), durations, ratios, counts |
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
WORST_ROUTE_DATA=<main tree>/data uv run worst-route serve --region portland --port <api> --store <lane>/data/routes.sqlite
cd frontend && WORST_ROUTE_API=http://127.0.0.1:<api> npm run dev -- --port <web> --strictPort
```

Prettier formats everything under `frontend/` but the recordings (`.prettierignore`); the
gate runs its check as part of `typecheck`.

`dist/` expects `/api` to reach the API, as the dev server and `preview` arrange; public
hosting needs the same from its web server (M5). The bundle is about 1 MB minified (MapLibre
is most of it), 290 kB gzipped.

## Using it

- **Stops.** On load the map fits the region's bbox (`GET /regions`; the server serves one
  region). The first click sets the start, the second the end; later clicks add stops along the
  way, inserted before the end. A marker is numbered and coloured by its role (start green, via
  blue, end red) and dragged to move; the list shows each stop's coordinates and a remove button.
  A stop keeps its coordinates at 6 decimals, so the request and the link that reproduces it
  carry the same numbers. Once a job has snapped them (its `snapped` event, or a stored route's
  stops), the list shows each stop's node, how far it moved and the API's note, and the map a
  dashed ghost dot where it went (the note as its tooltip). Editing the stops forgets the route
  shown and the snaps. Stops and settings are locked while a job runs.
- **Settings.** The seed (empty: random, picked by the server), the time budget (1–120 s), the
  tier (`auto`, `T0`–`T4`) and the leash (empty: auto, else up to 150 km); once a job has said,
  what `auto` became ("auto was T4", "auto was 3.6 km"). The inefficiency target is "as bad as
  possible", "a ratio" to the sensible route (× it, more than 1; 20 by default) or "a drive
  time" (9 h by default), typed as `9h`, `90m`, `2h30m`, `45s` or a bare number of minutes, at
  most 7 days; typing a number picks its kind, and each kind keeps its number while another is
  picked. A ratio target for a round trip is the API's `INVALID_REQUEST` (`field: "target"`),
  in the banner. The objective shows the five modes, only the distance enabled. The units
  (miles, kilometres) change what the panel shows; the API's numbers are metric.
- **Run.** Go posts the stops and settings (the target as `{kind: "ratio", value}` or
  `{kind: "duration", seconds}`), and its 202's seed goes into the settings. Reroll
  posts the same without a seed, so the server picks one. Make it worse posts the shown route as
  `base_route_id`, with the budget, objective and target from the panel, leaving the stops,
  tier and leash to the base route (the API's rule), and the seed after the last one tried on
  that route (its own seed at first). The settings' seed follows the route shown: when the
  job's `done` names the base route (nothing worse was found), the route and its seed stay, and
  the next press tries the seed after; with a target, the base route may already meet it ("already
  within the target"), or no route nearer it was found. A target below the base route's value
  shrinks it (the API's rule). Cancel sends `DELETE`; the stream then ends with `done`
  (the route so far, stored as `cancelled`) or `error`. Leaving the page sends `DELETE` with
  `keepalive` for a running job, and for one whose POST is in flight as soon as the POST is
  answered (no stream is opened for it).
- **The stat line**: the ratio, large; the length and drive time; for a ratio or duration
  target, the target while the run goes ("target 9 h") and what it achieved once done ("target
  9 h, achieved 8 h 50 min, within ±2 %", from `done`'s `metrics.target`, or the document's for
  a route opened by link); the attempts (the attempt of the last splice while running, the
  route's attempts once stored), the elapsed time, how the run is going or ended
  (`getting worse…`, `reconnecting…`, `budget`, `converged`, `target reached`, `cancelled`,
  `timeout`, `failed`), the seed, and, when Make it worse found nothing, "no worse route found
  with seed N; try again or Reroll".
- **Errors.** Every API error shows its `code` and `message` in a banner over the map, closed by
  its × or the next action; an error with a `stop_index` (`STOP_OUTSIDE_REGION`,
  `NO_NODE_NEAR`, `STOP_UNREACHABLE_WITHOUT_REPEAT`) highlights that stop in the list and on
  the map. The client adds codes of its own: `NETWORK` (the server cannot be reached),
  `HTTP_<status>` (a refusal without the API's body, as a proxy's 502), `BAD_RESPONSE` (a
  success that is not JSON, as a page where the API should be; the message is its first 80
  characters), `STREAM_LOST` (the event stream closed and the browser gave up reconnecting) and
  `CLIENT_ERROR` (an error of the app's own, with its message). Every error ends the run with
  the controls unlocked: a request refused before its job exists keeps what was shown, and an
  error while following a job closes its stream (and, for `CLIENT_ERROR`, cancels the job).

## State

`Store` holds `{region, stops, settings, run, route, error}`. A module renders from it with
`watch(keys, listener)` (called at once, then after every `update` that touches one of the
keys) and changes it with `update(patch)`, `updateRun` or `updateSettings`. `run` changes up to
ten times a second while a job runs, so the stop list, the markers and the settings compare what
they show before rebuilding. The route's geometry is not in the store: it lives in
`RouteLayer`.

## Live growth

`RouteLayer` has three GeoJSON sources: the baseline (grey, dashed), the route (with
`lineMetrics: true`, drawn by a `line-gradient` on `line-progress` from yellow at the start to
dark red at the end, over a white casing) and the flash (cyan). `line-gradient` cannot be
combined with a dash pattern, hence the separate baseline layer.

The route is kept as the stream sends it (`api.md`, **Applying the stream**): its `[lon, lat]`
points and `index`, the position among them of each route node. `subscribe` calls, per event:

- `snapped`: each stop's snap, and the resolved tier and leash;
- `baseline`: `setBaseline(geometry)` (none for a round trip);
- `snapshot`: `setSnapshot(geometry, node_index)`, which replaces points and index;
- `progress`: `applySplice(i, j, coords, node_index)` for each splice, in order, then the
  numbers; each splice is flashed for 400 ms;
- `done`: `GET /routes/{id}`, then `setFinal(geometry)`, the document's full geometry (the
  drawn line until then is partly simplified, as snapshots are), the final numbers, the URL's
  `?route=`, and the camera fitted to the route;
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

`map/basemap.ts` is the one place it is configured. Locally the map shows OpenStreetMap's
raster tiles (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`, desaturated so the route stands
out) with the attribution "© OpenStreetMap contributors", at hobby volume, as the tile usage
policy allows; the browser's own `User-Agent` and `Referer` are what the policy asks for. MapLibre
GL JS 5 fetches only the tiles in view (it has no prefetch option) and nothing is cached
offline. The policy rules these tiles out for a public site: `VECTOR_STYLE_URL`, marked as the
switch, takes a vector style URL (Protomaps PMTiles, MapTiler, Stadia), and the map then uses it
instead. Agents verifying the app block the tile server (**Seeing it**), so their screenshots
show the route on a blank background.

## Links

The URL carries the request: `?seed=&budget=&tier=&leash=&target=&stops=lat,lon;lat,lon;…`, the
target as `max`, `ratio:<x>` or `duration:<seconds>`, rewritten (`history.replaceState`, by
`UrlSync`) whenever the stops or settings change, so a link reproduces the request (Go with the
same seed plans the same route on the same build). A value that does not parse, or lies outside
the API's limits, is ignored and the default kept. Once a route is shown, the URL
also carries `route=<id>`; the share section offers `…/?route=<id>` with a copy button.

`?route=<id>` opens a stored route without running anything (`openRoute`): `GET /routes/{id}`
and `GET /routes/{id}/geojson` (for the baseline), the stops and settings from the route's own
request (its seed, budget, tier, leash and target; for a "make it worse" route, the base
route's stops),
the snaps from its stops, the route drawn whole, the camera fitted to it, and its numbers in
the stat line, from which Make it worse and Reroll work as after a run. The URL is left as it
came until the route has opened; then it is written whole. A link that does not open (a route
not stored, a build gone) keeps its `route=` and shows the API's error in the banner; the URL
follows the app again from the next change. The units are not in the URL.

## Tests

`npm test` (vitest, in Node; the gate runs it after the build):

| Test file | What it covers |
|---|---|
| `map/routeLayer.test.ts` | `spliceLine` on hand-made lines (a longer and a shorter detour, either end, the input left alone); each recording replayed: it ends with the stored route's node count, every node where the job's final snapshot has it (whose stop positions are the route's), every drawn point a point of the stored geometry in order with the same ends; and at every snapshot after splices (the max-mode recording's after its 50th `progress`), the replayed nodes equal the snapshot's |
| `api/types.test.ts` | each type's runtime spec, which the compiler holds to the type's keys; the specs against `api.md` (its JSON request example, and the field lists of the events, `done`'s metrics, `/regions`, the 202, the job status and the route document) and against every payload of both recordings; the ratio recording's target reached in `done` and the document, its round-trip refusal; a payload that does not fit is caught |
| `run/controls.test.ts` | `JobRunner` with `fetch` and `EventSource` stubbed: a TypeError from the client and an event the app cannot handle end the run as `CLIENT_ERROR` with the controls unlocked (the job cancelled), the server's `error` closes the stream; Make it worse keeps the route and its seed when nothing worse is found and tries the next seed, follows a worse route's seed; Go and Reroll send the settings' seed, then none; leaving the page cancels a job whose POST is in flight once it is answered, a running one at once; a ratio and a drive-time target sent, shown while running and with what was achieved (`target reached`), and Make it worse whose base already meets its target |
| `api/client.test.ts` | the API's error bodies, `BAD_RESPONSE` for an HTML success, `HTTP_<status>`, `NETWORK`, `CLIENT_ERROR` |
| `share/share.test.ts` | the URL state round trip (every field, `auto`, seeds 0 and 2⁵³ − 1, clicked coordinates exactly), values it ignores, the app's state to the URL and back; a `?route=` link at startup: the URL untouched until the route opens, then written from its request, and kept, with the banner, when it does not open; targets in the URL, values ignored, and a ratio-target route opened by link |
| `format.test.ts` | distances in both units and their thresholds, durations, ratios, counts; drive times typed as a target, read and written back |

The drawn points are not the stored geometry point for point: a snapshot is simplified
(`api.md`, **Applying the stream**) and a splice is not, so the replay has fewer points; it is
compared node by node, and as an in-order subsequence of the stored geometry once consecutive
duplicates are dropped (the document's geometry drops them, a stream's points keep them).

`src/__fixtures__/http.ts` holds the stand-ins: `stubFetch`, `FakeEventSource` and a route view
that records what it is asked to draw.

**The recordings** (`src/__fixtures__/stream.ts` loads them) are jobs on the Portland build
e4b3b4ad90db as `worst-route serve` streamed them. `portland-stream.json` (about 1 MB), recorded
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
any snapshot. To record one again (after an API change): run `serve` on a build, `POST /jobs`
with its request, read `/jobs/{id}/events` to its end, then fetch the status, the route and
the refusal, and write the same keys, one top-level value (and one event) per line; a script
of a few lines with `httpx` does it. The max-mode run needs at least 50 `progress` events before
its end, so a mid-run snapshot exists, and stays near 1 MB.

## Seeing it

Agents run the app in their own clone (**Running it**) and drive it with headless Chrome over
the DevTools protocol, the tile server blocked (`Network.setBlockedURLs`), writing screenshots
to their scratch directory; a Playwright tool under `tools/` is a later item. Killing the dev
server mid-run reloads the page (Vite's client does), so a dropped stream is tested through a
relay in front of `npm run preview`.

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

## Limitations

- **A stream through the dev proxy whose upstream drops is not ended**: Vite's proxy keeps the
  browser's response open, so if the API dies mid-run the stat line stays at `getting worse…`.
  A connection dropped between the browser and the server is reconnected as above.
- **MapLibre GL JS 5**, as decided, carries GHSA-jrc7-96c5-q579 (its HTML sanitizer; `npm audit`
  lists every version up to 6.4.0). In 5.x the sanitizer renders the attribution control's
  strings only, which here are the app's own; a vector style from a third party for public
  hosting brings its own attribution, so the upgrade to 6.x comes before that (M5).
- **Seeds above 2⁵³ − 1** cannot be typed: the API allows up to 2⁶³ − 1, but a JavaScript number
  is exact only up to 2⁵³. A stored route with a larger seed (from the CLI or a script) shows it
  rounded.
- **Desktop layout only**; directions, toasts, the share image, geocoding search and the
  other objectives arrive with their milestones.
