// Bootstrap and wiring (docs/reference/frontend.md): the store, from the URL's state; the map
// and the panel's parts; the region's bbox from /regions; a stored route when the URL names one,
// the URL left alone until it has opened; clicks on the map and a place search's picks add stops,
// the same way (placeStop); leaving the page cancels a running job.
import "./style.css";
import { errorBody, getRegions } from "./api/client";
import { mountBanner } from "./banner";
import { createMap, fitTo } from "./map/map";
import { RouteLayer } from "./map/routeLayer";
import { StopsLayer } from "./map/stopsLayer";
import { JobRunner, mountRunControls } from "./run/controls";
import { mountSettings } from "./settings/panel";
import { UrlSync, decodeUrlState, mountShare, openRoute, settingsFrom } from "./share/share";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Stop } from "./state";
import { PlaceSearch, mountPlaceSearch } from "./stops/search";
import { mountStopList, placeStop } from "./stops/stopList";
import { bboxBounds } from "./util/geo";

const byId = (id: string): HTMLElement => document.getElementById(id) as HTMLElement;

const url = decodeUrlState(location.search);
const store = new Store({
  region: null,
  stops: (url.stops ?? []).map(({ lat, lon }) => ({ lat, lon, snapped: null })),
  settings: settingsFrom(url, DEFAULT_SETTINGS),
  run: IDLE_RUN,
  route: null,
  error: null,
});

const map = createMap(byId("map"));
let routeLayer: RouteLayer | null = null;
let runner: JobRunner | null = null;

/** Change the stops; the route shown no longer goes through them. */
function edit(stops: Stop[]): void {
  routeLayer?.clear();
  store.update({ stops, error: null, route: null, run: IDLE_RUN });
}

mountBanner(byId("banner"), store);
const placeSearch = document.createElement("div");
const place = (lat: number, lon: number): boolean => placeStop(store, edit, lat, lon);
mountPlaceSearch(placeSearch, store, new PlaceSearch(store, place));
mountStopList(byId("stops"), store, edit, placeSearch);
mountSettings(byId("settings"), store);
mountShare(byId("share"), store);
new StopsLayer(map, store, edit);
const urlSync = new UrlSync(store, (query) => {
  if (query !== location.search) history.replaceState(null, "", location.pathname + query);
});
if (url.route === null) urlSync.start(false);

map.on("click", (event) => place(event.lngLat.lat, event.lngLat.lng));

map.once("load", () => {
  routeLayer = new RouteLayer(map);
  runner = new JobRunner(store, routeLayer, (bounds) => fitTo(map, bounds));
  mountRunControls(byId("run"), store, runner);
  if (url.route !== null) {
    void openRoute(url.route, store, runner).then((opened) => urlSync.start(opened));
  }
});

getRegions()
  .then(([region]) => {
    store.update({ region });
    if (store.state.route === null) fitTo(map, bboxBounds(region.bbox)); // a route fits itself
  })
  .catch((error: unknown) => store.update({ error: errorBody(error) }));

addEventListener("pagehide", () => runner?.leave());
