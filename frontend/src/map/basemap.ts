// The basemap (docs/reference/frontend.md, **Basemap**), the one the server's GET /config names.
//
// `raster`: OpenStreetMap's raster tiles, desaturated so the route stands out, at hobby volume and
// with their attribution, as the tile usage policy allows for local use
// (https://operations.osmfoundation.org/policies/tiles/); MapLibre fetches only the tiles in view.
// `pmtiles`: Protomaps' vector tiles from a file the server holds, read by byte ranges through
// the `pmtiles://` protocol (registered in map.ts), drawn in Protomaps' grayscale flavor, muted
// for the same reason; with the labels when the server has their fonts and icons, without them
// otherwise. A `pmtiles` basemap without its file draws nothing (and says so on the console):
// never OSM's tiles in its place. The attribution strings are the app's own, never a tileset's.
//
// `basemapParts` is pure and imports MapLibre's types only, so the tests load it in Node;
// `addBasemap` puts the parts under whatever the map draws already (the route's layers).
import { layers, namedFlavor } from "@protomaps/basemaps";
import type {
  AddLayerObject,
  Map as MapLibreMap,
  SourceSpecification,
  StyleSpecification,
} from "maplibre-gl";
import type { BasemapConfig } from "../api/types";

const LINK = 'target="_blank" rel="noopener"';
const OSM_COPYRIGHT = `<a href="https://www.openstreetmap.org/copyright" ${LINK}>OpenStreetMap</a>`;
const OSM_ATTRIBUTION = `© ${OSM_COPYRIGHT} contributors`;
const PROTOMAPS_ATTRIBUTION = `<a href="https://protomaps.com" ${LINK}>Protomaps</a> © ${OSM_COPYRIGHT}`;
const SOURCE = "protomaps";
const FLAVOR = "grayscale"; // the sprite the server offers is this flavor's (basemap_api.py)

/** The map's style before the basemap comes: nothing drawn. */
export const EMPTY_STYLE: StyleSpecification = { version: 8, sources: {}, layers: [] };

/** A basemap as the map takes it. */
export interface BasemapParts {
  sources: Record<string, SourceSpecification>;
  layers: AddLayerObject[];
  glyphs: string | null;
  sprite: string | null;
}

const NO_BASEMAP: BasemapParts = { sources: {}, layers: [], glyphs: null, sprite: null };

/** The parts of `basemap`, its server paths made absolute under `apiRoot` (the page's origin and
 * /api: MapLibre needs absolute URLs, and its templates' braces must stay as they are). */
export function basemapParts(basemap: BasemapConfig, apiRoot: string): BasemapParts {
  if (basemap.kind === "raster") {
    const osm: SourceSpecification = {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: OSM_ATTRIBUTION,
    };
    const raster: AddLayerObject = {
      id: "osm",
      type: "raster",
      source: "osm",
      paint: { "raster-saturation": -0.7 },
    };
    return { sources: { osm }, layers: [raster], glyphs: null, sprite: null };
  }
  if (basemap.tiles === null) {
    console.error("the server names a pmtiles basemap without its file: no basemap drawn");
    return NO_BASEMAP;
  }
  const labelled = basemap.glyphs !== null && basemap.sprite !== null;
  const flavor = namedFlavor(FLAVOR);
  const source: SourceSpecification = {
    type: "vector",
    url: `pmtiles://${apiRoot}${basemap.tiles}`,
    attribution: PROTOMAPS_ATTRIBUTION,
  };
  return {
    sources: { [SOURCE]: source },
    layers: (labelled
      ? layers(SOURCE, flavor, { lang: "en" })
      : layers(SOURCE, flavor)) as AddLayerObject[],
    glyphs: labelled ? `${apiRoot}${basemap.glyphs}` : null,
    sprite: labelled ? `${apiRoot}${basemap.sprite}` : null,
  };
}

/** What `addBasemap` asks of the map. */
export type BasemapMap = Pick<
  MapLibreMap,
  "addSource" | "addLayer" | "setGlyphs" | "setSprite" | "getLayersOrder"
>;

/** Add the basemap below the layers the map has already; its style must be loaded. */
export function addBasemap(map: BasemapMap, parts: BasemapParts): void {
  if (parts.glyphs !== null) map.setGlyphs(parts.glyphs);
  if (parts.sprite !== null) map.setSprite(parts.sprite);
  for (const [id, source] of Object.entries(parts.sources)) map.addSource(id, source);
  const below = map.getLayersOrder()[0];
  for (const layer of parts.layers) map.addLayer(layer, below);
}
