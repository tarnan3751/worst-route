// The MapLibre map (docs/reference/frontend.md): its attribution and navigation controls, and
// the `pmtiles://` protocol the vector basemap is read through. It starts with nothing drawn; the
// route's layers (routeLayer.ts: baseline, casing, route, flash, the directions' highlight) come
// once it has loaded and the basemap (basemap.ts) below them once the server has named it; the
// stops are DOM markers above the canvas.
//
// MapLibre 6 finds its worker beside its own module, which a bundle does not keep: the worker is
// imported as an asset (Vite's `?url`, emitted into dist/assets/) and named with setWorkerUrl.
import { Map as MapLibreMap, NavigationControl, addProtocol, setWorkerUrl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { Protocol } from "pmtiles";
import type { Bounds } from "../util/geo";
import { EMPTY_STYLE } from "./basemap";

/** The map in `container`, showing the contiguous US until a region is known. */
export function createMap(container: HTMLElement): MapLibreMap {
  setWorkerUrl(workerUrl);
  addProtocol("pmtiles", new Protocol().tile);
  const map = new MapLibreMap({
    container,
    style: EMPTY_STYLE,
    center: [-98.5, 39.8],
    zoom: 3.5,
    attributionControl: { compact: false },
  });
  map.addControl(new NavigationControl({ showCompass: false }), "top-right");
  return map;
}

/** Show `bounds` with room around them, at once. */
export function fitTo(map: MapLibreMap, bounds: Bounds): void {
  map.fitBounds(bounds, { padding: 48, duration: 0 });
}
