// Bootstrap and wiring (docs/reference/frontend.md): the store, from the URL's state; the map
// and the panel's parts (today's route at the top, the directions, which highlight their steps
// on the route layer, among them); the toasts under the map; the served regions from /regions,
// the map fitted to all their bboxes, and the build each trip runs on picked from its stops
// (RegionPicker); a stored route when the URL names one, or today's once it is done for
// `?daily` (unless a run of the user's own has started first), the URL left alone until then;
// clicks on the map and a place search's picks add stops, the same way (placeStop), but in
// drawing mode, where a click takes the move nearest it (draw/drawing.ts), Backspace takes the last
// one back and Esc leaves; changing the stops forgets a drawing; leaving the page cancels a running
// job, and a drawing's par.
// The basemap the server names (GET /config) goes under the route's layers once the map has
// loaded (OpenFreeMap's once its style has).
import "./style.css";
import { API_BASE, errorBody, getConfig, getRegions } from "./api/client";
import { mountBanner } from "./banner";
import { DailyRoute, mountDaily, openDailyAtStart } from "./daily/panel";
import { DirectionsPanel, mountDirections } from "./directions/panel";
import { Drawing } from "./draw/drawing";
import { mountDraw } from "./draw/panel";
import { addBasemap, basemapParts, fetchStyle } from "./map/basemap";
import { DrawLayer } from "./map/drawLayer";
import { createMap, fitTo } from "./map/map";
import { RouteLayer } from "./map/routeLayer";
import { StopsLayer } from "./map/stopsLayer";
import { JobRunner, mountRunControls } from "./run/controls";
import { mountSettings } from "./settings/panel";
import { UrlSync, decodeUrlState, mountShare, openRoute, settingsFrom } from "./share/share";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Stop } from "./state";
import { RegionPicker } from "./stops/pick";
import { PlaceSearch, mountPlaceSearch } from "./stops/search";
import { mountStopList, placeStop } from "./stops/stopList";
import { Toasts, mountToasts } from "./toasts";
import { bboxBounds, unionBbox } from "./util/geo";

const byId = (id: string): HTMLElement => document.getElementById(id) as HTMLElement;

const url = decodeUrlState(location.search);
const store = new Store({
  regions: null,
  region: null,
  pick: null,
  stops: (url.stops ?? []).map(({ lat, lon }) => ({ lat, lon, snapped: null })),
  settings: settingsFrom(url, DEFAULT_SETTINGS),
  run: IDLE_RUN,
  route: null,
  error: null,
  brands: null,
});

const map = createMap(byId("map"));
const config = getConfig();
let routeLayer: RouteLayer | null = null;
let drawLayer: DrawLayer | null = null;
let runner: JobRunner | null = null;
let drawing: Drawing | null = null;
let ready: (runner: JobRunner) => void = () => undefined;
const loaded = new Promise<JobRunner>((resolve) => (ready = resolve)); // the runner, on the map

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
mountSettings(byId("settings"), store);
mountShare(byId("share"), store);
mountToasts(byId("toasts"), new Toasts(store));
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
  routeLayer = new RouteLayer(map);
  runner = new JobRunner(store, routeLayer, (bounds) => fitTo(map, bounds));
  drawLayer = new DrawLayer(map, store);
  const shown = runner;
  const layer = routeLayer;
  drawing = new Drawing(store, drawLayer, {
    clearRoute: () => layer.clear(),
    open: (id) => openRoute(id, store, shown),
  });
  mountDraw(byId("draw"), drawing, store);
  const drawn = drawing;
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
    .then(async ({ basemap }) => {
      const style = basemap.kind === "openfreemap" ? await fetchStyle(basemap.style) : null;
      addBasemap(map, basemapParts(basemap, location.origin + API_BASE, style));
    })
    .catch((error: unknown) => store.update({ error: errorBody(error) }));
});

getRegions()
  .then((regions) => {
    store.update({ regions });
    const all = unionBbox(regions.map(({ bbox }) => bbox));
    if (store.state.route === null && all) fitTo(map, bboxBounds(all)); // a route fits itself
  })
  .catch((error: unknown) => store.update({ error: errorBody(error) }));

addEventListener("pagehide", () => {
  runner?.leave();
  drawing?.leavePage();
});
