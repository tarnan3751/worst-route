// Bootstrap and wiring (docs/reference/frontend.md): the store, the map, the panel's parts;
// the region's bbox from /regions; clicks on the map add stops.
import "./style.css";
import { errorBody, getRegions } from "./api/client";
import { mountBanner } from "./banner";
import { createMap, fitTo } from "./map/map";
import { StopsLayer } from "./map/stopsLayer";
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

/** Change the stops. */
function edit(stops: Stop[]): void {
  store.update({ stops, error: null });
}

const map = createMap(byId("map"));
mountBanner(byId("banner"), store);
mountStopList(byId("stops"), store, edit);
new StopsLayer(map, store, edit);

map.on("click", (event) => {
  if (busy(store)) return;
  const { lat, lng } = event.lngLat;
  edit(withStopAdded(store.state.stops, round6(lat), round6(lng)));
});

getRegions()
  .then(([region]) => {
    store.update({ region });
    fitTo(map, bboxBounds(region.bbox));
  })
  .catch((error: unknown) => store.update({ error: errorBody(error) }));
