// The MapLibre map (docs/reference/frontend.md): the basemap, its attribution and the
// navigation control. Layers are stacked in the order they are added: the route's (routeLayer.ts:
// baseline, casing, route, flash, the directions' highlight) above the basemap; the stops are DOM
// markers above the canvas.
import { Map as MapLibreMap, NavigationControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Bounds } from "../util/geo";
import { basemapStyle } from "./basemap";

/** The map in `container`, showing the contiguous US until a region is known. */
export function createMap(container: HTMLElement): MapLibreMap {
  const map = new MapLibreMap({
    container,
    style: basemapStyle(),
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
