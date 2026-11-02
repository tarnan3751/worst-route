// Bootstrap and wiring (docs/reference/frontend.md): the store, from the URL's state; the map
// and the panel's parts (today's route at the top, then the challenges, graded on every route
// shown and every drawing's verdict, a toast for each one completed; the directions, which
// highlight their steps on the route layer, among them); the toasts under the map; the served
// regions from /regions, the map fitted to all their bboxes, and the build each trip runs on
// picked from its stops (RegionPicker); a stored route when the URL names one, or today's once it
// is done for `?daily` (unless a run of the user's own has started first), the URL left alone
// until then, or else the challenge `?challenge=` names set up once the regions are known;
// clicks on the map and a place search's picks add stops, the same way (placeStop), but in
// drawing mode, where a click takes the move nearest it (draw/drawing.ts), Backspace takes the last
// one back and Esc leaves; changing the stops forgets a drawing; leaving the page cancels a running
// job, and a drawing's par.
// The basemap the server names (GET /config) goes under the route's layers once the map has
// loaded (OpenFreeMap's once its style has); the most seeds a job runs there goes to the settings.
// The theme the settings name (the device's, from the browser's storage; auto by default) is put
// on the page at once and on the map's layers and basemap once they are there, and again
// whenever it changes (theme/, docs/reference/frontend.md, **Themes**).
import "./style.css";
import { API_BASE, errorBody, getConfig, getRegions } from "./api/client";
import { mountBanner } from "./banner";
import type { Challenge } from "./challenges/list";
import { ChallengePanel, mountChallenges } from "./challenges/panel";
import { ChallengeProgress, completedLine } from "./challenges/progress";
import { DailyRoute, mountDaily, openDailyAtStart } from "./daily/panel";
import { DirectionsPanel, mountDirections } from "./directions/panel";
import { Drawing } from "./draw/drawing";
import { mountDraw } from "./draw/panel";
import { Basemap } from "./map/basemap";
import { DrawLayer, paintDrawing } from "./map/drawLayer";
import { createMap, fitTo } from "./map/map";
import { RouteLayer, paintRoute } from "./map/routeLayer";
import { StopsLayer } from "./map/stopsLayer";
import { JobRunner, mountRunControls } from "./run/controls";
import { mountSettings } from "./settings/panel";
import { UrlSync, decodeUrlState, mountShare, openRoute, settingsFrom } from "./share/share";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Stop } from "./state";
import { RegionPicker } from "./stops/pick";
import { PlaceSearch, mountPlaceSearch } from "./stops/search";
import { mountStopList, placeStop } from "./stops/stopList";
import { applyTheme } from "./theme/apply";
import { followTheme, storedTheme } from "./theme/resolve";
import type { Theme } from "./theme/theme";
import { Toasts, mountToasts } from "./toasts";
import { bboxBounds, unionBbox } from "./util/geo";

const byId = (id: string): HTMLElement => document.getElementById(id) as HTMLElement;

const url = decodeUrlState(location.search);
const store = new Store({
  regions: null,
  region: null,
  pick: null,
  stops: (url.stops ?? []).map(({ lat, lon }) => ({ lat, lon, snapped: null })),
  settings: settingsFrom(url, { ...DEFAULT_SETTINGS, theme: storedTheme() }),
  run: IDLE_RUN,
  route: null,
  error: null,
  brands: null,
  challenge: null,
});

const map = createMap(byId("map"));
const config = getConfig();
let routeLayer: RouteLayer | null = null;
let drawLayer: DrawLayer | null = null;
let runner: JobRunner | null = null;
let drawing: Drawing | null = null;
let basemap: Basemap | null = null;
let theme!: Theme; // the theme shown, from followTheme's first call below
let ready: (runner: JobRunner) => void = () => undefined;
const loaded = new Promise<JobRunner>((resolve) => (ready = resolve)); // the runner, on the map

followTheme(store, (next) => {
  theme = next;
  void applyTheme(next);
  if (routeLayer === null) return; // the map's layers take it once it has loaded
  paintRoute(map, next);
  paintDrawing(map, next);
  basemap?.show(next.basemap).catch((error: unknown) => store.update({ error: errorBody(error) }));
});

/** Change the stops; the route shown no longer goes through them, nor a drawing. */
function edit(stops: Stop[]): void {
  routeLayer?.clear();
  drawing?.discard();
  store.update({ stops, error: null, route: null, run: IDLE_RUN });
}

mountBanner(byId("banner"), store, {
  strict: () => void runner?.withoutStrict(),
  hard: () => void runner?.withoutHard(),
});
const placeSearch = document.createElement("div");
const place = (lat: number, lon: number): boolean => placeStop(store, edit, lat, lon);
mountPlaceSearch(placeSearch, store, new PlaceSearch(store, place));
mountStopList(byId("stops"), store, edit, placeSearch);
// the most seeds a job runs here (none above 1 from a server older than portfolios)
const portfolioMax = config.then(({ limits }) => limits.portfolio_max ?? 1).catch(() => 1);
mountSettings(byId("settings"), store, portfolioMax);
mountShare(byId("share"), store);
const toasts = new Toasts(store);
mountToasts(byId("toasts"), toasts);
new StopsLayer(map, store, edit);
new RegionPicker(store);
const urlSync = new UrlSync(store, (query) => {
  if (query !== location.search) history.replaceState(null, "", location.pathname + query);
});
const daily = new DailyRoute(store);
const openDaily = async (id: string) => openRoute(id, store, await loaded);
const header = document.querySelector("#panel header") as HTMLElement;
mountDaily(byId("daily"), header, daily, store, (id) => void openDaily(id));
void daily.load();
const challenges = new ChallengePanel(store, daily, {
  edit,
  open: openDaily,
  fit: (bounds) => fitTo(map, bounds),
});
const progress = new ChallengeProgress(store, () => (daily.phase === "done" ? daily.daily : null));
progress.addEventListener("completed", (event) => {
  toasts.notify(completedLine((event as CustomEvent<Challenge>).detail));
});
mountChallenges(byId("challenges"), header, challenges, progress, store, ({ goal }) => {
  byId(goal.kind === "daily_worse" ? "run" : "stops").scrollIntoView({ block: "start" });
});
if (url.route === null && url.daily === null) urlSync.start(false);
if (url.route === null && url.daily !== null) {
  void openDailyAtStart(daily, store, openDaily).then((opened) => urlSync.start(opened));
}

map.on("click", (event) => {
  if (drawing?.active) {
    const move = drawLayer?.pick(event.point);
    if (move) void drawing.take(move.edge);
    return;
  }
  place(event.lngLat.lat, event.lngLat.lng);
});

addEventListener("keydown", (event) => {
  const typing =
    event.target instanceof HTMLElement && event.target.closest("input, select, textarea");
  if (!drawing?.active || typing) return;
  if (event.key === "Backspace") {
    event.preventDefault();
    drawing.undo();
  } else if (event.key === "Escape") {
    drawing.leave();
  }
});

map.once("load", () => {
  routeLayer = new RouteLayer(map, theme);
  runner = new JobRunner(store, routeLayer, (bounds) => fitTo(map, bounds));
  drawLayer = new DrawLayer(map, store, theme);
  const shown = runner;
  const layer = routeLayer;
  drawing = new Drawing(store, drawLayer, {
    clearRoute: () => layer.clear(),
    open: (id) => openRoute(id, store, shown),
  });
  mountDraw(byId("draw"), drawing, store);
  const drawn = drawing;
  drawn.addEventListener("change", () => progress.drawn(drawn));
  mountRunControls(byId("run"), store, runner, () => drawn.start());
  const directions = new DirectionsPanel(store, {
    highlight: (line) => layer.highlight(line),
    panTo: (at) => map.panTo(at),
  });
  mountDirections(byId("directions"), directions);
  ready(runner);
  if (url.route !== null) {
    void openRoute(url.route, store, runner).then((opened) => urlSync.start(opened));
  }
  config
    .then(async (answer) => {
      basemap = new Basemap(map, answer.basemap, location.origin + API_BASE);
      await basemap.show(theme.basemap);
    })
    .catch((error: unknown) => store.update({ error: errorBody(error) }));
});

getRegions()
  .then((regions) => {
    store.update({ regions });
    const all = unionBbox(regions.map(({ bbox }) => bbox));
    if (store.state.route === null && all) fitTo(map, bboxBounds(all)); // a route fits itself
    if (url.challenge !== null && url.route === null && url.daily === null) {
      challenges.setUpLink(url.challenge);
    }
  })
  .catch((error: unknown) => store.update({ error: errorBody(error) }));

addEventListener("pagehide", () => {
  runner?.leave();
  drawing?.leavePage();
});
