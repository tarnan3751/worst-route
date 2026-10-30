// Bootstrap and wiring (docs/reference/frontend.md): the store, the map, the panel's parts;
// the region's bbox from /regions; clicks on the map add stops; leaving the page cancels a
// running job.
import "./style.css";
import { errorBody, getRegions } from "./api/client";
import { mountBanner } from "./banner";
import { createMap, fitTo } from "./map/map";
import { RouteLayer } from "./map/routeLayer";
import { StopsLayer } from "./map/stopsLayer";
import { JobRunner, mountRunControls } from "./run/controls";
import { mountSettings } from "./settings/panel";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Stop } from "./state";
import { busy, mountStopList, withStopAdded } from "./stops/stopList";
import { bboxBounds, round6 } from "./util/geo";

const byId = (id: string): HTMLElement => document.getElementById(id) as HTMLElement;

const store = new Store({
  region: null,
  stops: [],
  settings: DEFAULT_SETTINGS,
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
mountStopList(byId("stops"), store, edit);
mountSettings(byId("settings"), store);
new StopsLayer(map, store, edit);

map.on("click", (event) => {
  if (busy(store)) return;
  const { lat, lng } = event.lngLat;
  edit(withStopAdded(store.state.stops, round6(lat), round6(lng)));
});

map.once("load", () => {
  routeLayer = new RouteLayer(map);
  runner = new JobRunner(store, routeLayer, (bounds) => fitTo(map, bounds));
  mountRunControls(byId("run"), store, runner);
});

getRegions()
  .then(([region]) => {
    store.update({ region });
    fitTo(map, bboxBounds(region.bbox));
  })
  .catch((error: unknown) => store.update({ error: errorBody(error) }));

addEventListener("pagehide", () => runner?.leave());
