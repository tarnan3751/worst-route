// The MapLibre map (docs/reference/frontend.md): its attribution and navigation controls, its
// camera held to the box around the contiguous US (bounds.ts: on each axis the view within the box
// or the box within the view, zoomed out no further than the whole box in view; flat and north-up,
// rotation and pitch disabled), and the
// `pmtiles://` protocol the vector basemap is read through. It starts with nothing drawn; the
// route's layers (routeLayer.ts: baseline, casing, route, flash, the directions' highlight) come
// once it has loaded and the basemap (basemap.ts) below them once the server has named it; the
// stops are DOM markers above the canvas.
//
// MapLibre 6 finds its worker beside its own module, which a bundle does not keep: the worker is
// imported as an asset (Vite's `?url`, emitted into dist/assets/) and named with setWorkerUrl.
import {
  LngLat,
  Map as MapLibreMap,
  NavigationControl,
  addProtocol,
  setWorkerUrl,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { Protocol } from "pmtiles";
import type { Bounds } from "../util/geo";
import { EMPTY_STYLE } from "./basemap";
import { US_BOUNDS, holdToUs } from "./bounds";

/** The map in `container`, showing the contiguous US until a region is known. */
export function createMap(container: HTMLElement): MapLibreMap {
  setWorkerUrl(workerUrl);
  addProtocol("pmtiles", new Protocol().tile);
  const map = new MapLibreMap({
    container,
    style: EMPTY_STYLE,
    bounds: US_BOUNDS,
    attributionControl: { compact: false },
    // Flat and north-up, which the hold below relies on: no rotation or pitch by drag, touch or key.
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
  });
  map.keyboard.disableRotation();
  // MapLibre's own constrain, replaced, kept the zoom within minZoom and maxZoom: so does this.
  map.setTransformConstrain((center, zoom) => {
    const within = Math.min(Math.max(zoom, map.getMinZoom()), map.getMaxZoom());
    const held = holdToUs(center.toArray(), within, container.clientWidth, container.clientHeight);
    return { center: new LngLat(...held.center), zoom: held.zoom };
  });
  map.addControl(new NavigationControl({ showCompass: false }), "top-right");
  return map;
}

/** Show `bounds` with room around them, at once. */
export function fitTo(map: MapLibreMap, bounds: Bounds): void {
  map.fitBounds(bounds, { padding: 48, duration: 0 });
}
