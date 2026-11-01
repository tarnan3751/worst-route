// The box the map's view is held to (docs/reference/frontend.md, **Stops**), and how a camera is
// held to it: MapLibre's transformConstrain, set in map.ts.
import type { LonLat } from "../api/types";
import type { Bounds } from "../util/geo";

/** A box around the contiguous US, `[[west, south], [east, north]]`: the national build's extent
 * (W −124.72, S 24.55, E −66.97, N 49.22) with about 2° of margin west and east and 1.5° south
 * and north, so the coasts and the borders are not flush against its edges. The map's camera is
 * held to it (`holdToUs`): on an axis where the view is smaller than the box the view never shows
 * beyond it, and on one where the view is larger the whole box is always in view; the map zooms
 * out no further than the whole box in view (`fitZoom`), so there the whole contiguous US is in
 * view, with margin only on the axis the window has to spare. The hold is flat and north-up, so the
 * map's rotation and pitch are disabled (map.ts). A constant, not the served regions'
 * extent: every build served lies within the contiguous US, and the desktop app's basemap is cut
 * to it, so beyond the box there is nothing to show; a server serving only Portland keeps the same
 * box, a stop outside any build being refused by the build picker. */
export const US_BOUNDS: Bounds = [
  [-127, 23],
  [-65, 51],
];

/** MapLibre's tiles: a world 512 · 2^zoom pixels across. */
const TILE_SIZE = 512;

/** Web Mercator's y of `lat`, 0 at the top of the world and 1 at the bottom (as MapLibre's). */
function mercatorY(lat: number): number {
  return (180 - (180 / Math.PI) * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360))) / 360;
}

/** The latitude of Web Mercator's `y`, `mercatorY`'s inverse. */
function latOfMercatorY(y: number): number {
  return (360 / Math.PI) * Math.atan(Math.exp(((180 - y * 360) * Math.PI) / 180)) - 90;
}

/** `value` held between `a` and `b`, whichever is the lower. */
function between(value: number, a: number, b: number): number {
  return Math.min(Math.max(value, Math.min(a, b)), Math.max(a, b));
}

/** The zoom at which `US_BOUNDS` just fits a `width` × `height` pixel viewport: the lesser of
 * its two axes' fit zooms, the zoom `fitBounds(US_BOUNDS)` without padding picks. */
export function fitZoom(width: number, height: number): number {
  const [[west, south], [east, north]] = US_BOUNDS;
  const across = ((east - west) / 360) * TILE_SIZE;
  const down = (mercatorY(south) - mercatorY(north)) * TILE_SIZE;
  return Math.min(Math.log2(width / across), Math.log2(height / down));
}

/** A camera at `center` and `zoom` in a `width` × `height` pixel viewport, held to the box: its
 * zoom no lower than `fitZoom`, and its centre, on each axis, half the view from the box's edges,
 * inside them where the view is the smaller (the view within the box) and outside them where it
 * is the larger (the box within the view). A camera already so is returned as it is. */
export function holdToUs(
  center: LonLat,
  zoom: number,
  width: number,
  height: number,
): { center: LonLat; zoom: number } {
  const [[west, south], [east, north]] = US_BOUNDS;
  const held = Math.max(zoom, fitZoom(width, height));
  const world = TILE_SIZE * 2 ** held; // the world's width in pixels at that zoom
  const halfLon = (width / world) * 180; // half the view in degrees of longitude
  const halfY = height / world / 2; // and in Mercator's y
  const [lon, lat] = center;
  const y = mercatorY(lat);
  const heldY = between(y, mercatorY(north) + halfY, mercatorY(south) - halfY);
  return {
    center: [
      between(lon, west + halfLon, east - halfLon),
      heldY === y ? lat : latOfMercatorY(heldY),
    ],
    zoom: held,
  };
}
