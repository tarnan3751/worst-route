// The land outside the contiguous US, drawn flat (docs/reference/frontend.md, **Basemap**): the
// land of Canada, Mexico and the rest of the world but the 48 states and DC takes one flat tone,
// the theme's `outside` colour, so it reads as not part of the app, while the water, the 48 states
// and DC keep the basemap's look.
//
// The outline is public/conus.json (as tools/desktop_data.py conus writes it, 10 km out from the
// coast and the borders), fetched relative to the page, its polygons' own holes filled (Lake
// Michigan and the bays the Census coast leaves out are the US's). `withOutside` adds to the
// basemap's parts by its make:
// - Protomaps' (`pmtiles`), whose file may end a tile or so beyond the outline, the sea past it
//   only the background: two land fills just above its `earth` layer (the land) and, in the light
//   and dark flavors, the `landcover` right after it (drawn up to zoom 7), `earth-outside`, the
//   earth again in the outside tone, and `us-land`, the outline filled in the flavor's own land
//   colour (the earth layer's), so all land goes flat and then the states are land again, the sea
//   left as it was; the land's uses, the water, the roads and the labels draw over both. The land
//   cover is under them, the US's too: drawn over them it would cover the land outside as well.
// - OpenFreeMap's (the whole world's) and the raster's: the mask, a polygon round the world (its
//   ring first) with each of the outline's polygons as a hole, just below the basemap's first water
//   layer (over the land, under the water and all after it), or over the raster tiles, which have
//   no vector water, the sea outside the outline flat there too; a basemap that draws nothing gets
//   the mask alone.
// map/basemap.ts `Basemap` draws them with the basemap's parts and swaps them with each variant,
// the land colour read again from the new flavor; `paintOutside` recolours the outside tone.
//
// maplibre-gl is imported for its types only, so the tests load this module in Node.
import type {
  AddLayerObject,
  FillLayerSpecification,
  Map as MapLibreMap,
  SourceSpecification,
} from "maplibre-gl";
import type { LonLat } from "../api/types";
import type { Theme } from "../theme/theme";
import type { BasemapParts } from "./basemap";

type Ring = LonLat[];
export interface Polygon {
  type: "Polygon";
  coordinates: Ring[];
}
export interface MultiPolygon {
  type: "MultiPolygon";
  coordinates: Ring[][];
}

export const MASK = "outside"; // the mask's source and layer (OpenFreeMap's basemap, the raster)
export const EARTH_OUTSIDE = "earth-outside"; // Protomaps': its earth again, in the outside tone
export const US_LAND = "us-land"; // Protomaps': the outline in the land colour, source and layer
const OUTLINE_URL = "conus.json"; // public/, beside the page
// The source layers of the basemaps' water: Protomaps' `water` (its seas, lakes and rivers, as
// fills, lines and labels); OpenMapTiles' `water`, `waterway` (rivers as lines) and `water_name`.
const WATER = new Set(["water", "waterway", "water_name"]);
// Round the world, counterclockwise (Web Mercator stops short of the poles).
const WORLD: Ring = [
  [-180, -85],
  [180, -85],
  [180, 85],
  [-180, 85],
  [-180, -85],
];

/** Twice the signed area of a closed ring, positive when it winds counterclockwise. */
function signedArea(ring: Ring): number {
  let sum = 0;
  for (let k = 0; k + 1 < ring.length; k++) {
    sum += ring[k][0] * ring[k + 1][1] - ring[k + 1][0] * ring[k][1];
  }
  return sum;
}

/** The mask, the world but `us`: a ring round the world, counterclockwise, with the outer ring of
 * each of `us`'s polygons as a hole, clockwise. */
export function maskOf(us: MultiPolygon): Polygon {
  const holes = us.coordinates.map(([outer]) =>
    signedArea(outer) < 0 ? outer : [...outer].reverse(),
  );
  return { type: "Polygon", coordinates: [WORLD, ...holes] };
}

/** The outline at `url` (public/conus.json, relative to the page), its polygons' own holes filled,
 * or null (said on the console) when it does not load. */
export async function fetchOutline(url: string = OUTLINE_URL): Promise<MultiPolygon | null> {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const outline = (await response.json()) as MultiPolygon;
    return { type: "MultiPolygon", coordinates: outline.coordinates.map(([outer]) => [outer]) };
  } catch (error) {
    console.error(`the contiguous US's outline did not load (${String(error)}): none drawn`);
    return null;
  }
}

const shape = (geometry: Polygon | MultiPolygon): SourceSpecification => ({
  type: "geojson",
  data: { type: "Feature", properties: {}, geometry },
});
const sourceLayer = (layer: AddLayerObject | undefined): string | undefined =>
  layer !== undefined && "source-layer" in layer ? layer["source-layer"] : undefined;
const isEarth = (layer: AddLayerObject): boolean =>
  layer.type === "fill" && sourceLayer(layer) === "earth";
const isWater = (layer: AddLayerObject): boolean => WATER.has(sourceLayer(layer) ?? "");

/** `parts` with the land outside `us` in `theme`'s outside tone: over Protomaps' earth and land
 * cover, the earth again in that tone and `us` in the earth's colour; else the mask, just below
 * their first water layer (over them all when they have none). `parts` themselves when the outline
 * did not load. */
export function withOutside(
  parts: BasemapParts,
  us: MultiPolygon | null,
  theme: Theme,
): BasemapParts {
  if (us === null) return parts;
  const layers = [...parts.layers];
  const earth = layers.findIndex(isEarth);
  if (earth >= 0) {
    const ground = layers[earth] as FillLayerSpecification;
    const outside = { ...ground, id: EARTH_OUTSIDE, paint: { "fill-color": theme.colors.outside } };
    const land: AddLayerObject = {
      id: US_LAND,
      type: "fill",
      source: US_LAND,
      paint: { "fill-color": ground.paint?.["fill-color"] },
    };
    let above = earth + 1;
    while (sourceLayer(layers[above]) === "landcover") above += 1;
    layers.splice(above, 0, outside, land);
    return { ...parts, sources: { ...parts.sources, [US_LAND]: shape(us) }, layers };
  }
  const mask: AddLayerObject = {
    id: MASK,
    type: "fill",
    source: MASK,
    paint: { "fill-color": theme.colors.outside },
  };
  const water = layers.findIndex(isWater);
  layers.splice(water < 0 ? layers.length : water, 0, mask);
  return { ...parts, sources: { ...parts.sources, [MASK]: shape(maskOf(us)) }, layers };
}

/** Colour the outside tone (the mask, or Protomaps' earth outside) as `theme` says, once drawn. */
export function paintOutside(
  map: Pick<MapLibreMap, "getLayer" | "setPaintProperty">,
  theme: Theme,
): void {
  for (const id of [MASK, EARTH_OUTSIDE]) {
    if (map.getLayer(id)) map.setPaintProperty(id, "fill-color", theme.colors.outside);
  }
}
