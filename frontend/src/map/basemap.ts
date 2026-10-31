// The basemap (docs/reference/frontend.md, **Basemap**), the one the server's GET /config names.
//
// `raster`: OpenStreetMap's raster tiles, desaturated so the route stands out, at hobby volume and
// with their attribution, as the tile usage policy allows for local use
// (https://operations.osmfoundation.org/policies/tiles/); MapLibre fetches only the tiles in view.
// `pmtiles`: Protomaps' vector tiles from a file the server holds, read by byte ranges through
// the `pmtiles://` protocol (registered in map.ts), drawn in Protomaps' grayscale flavor, muted
// for the same reason; with the labels when the server has their fonts and icons, without them
// otherwise. A `pmtiles` basemap without its file draws nothing (and says so on the console):
// never OSM's tiles in its place. `openfreemap`: OpenFreeMap's vector tiles from its public
// instance (https://openfreemap.org), drawn in the style the server names (Positron, muted),
// which `fetchStyle` fetches first: its layers, the sources they read, its fonts and its icons; a
// style that does not load draws nothing either. The attribution strings are the app's own,
// never a tileset's.
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
const OPENFREEMAP_ATTRIBUTION =
  `<a href="https://openfreemap.org" ${LINK}>OpenFreeMap</a> ` +
  `<a href="https://www.openmaptiles.org/" ${LINK}>© OpenMapTiles</a> Data from ${OSM_COPYRIGHT}`;
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
 * /api: MapLibre needs absolute URLs, and its templates' braces must stay as they are); for
 * `openfreemap`, those of `style`, its style as `fetchStyle` loaded it (none: nothing drawn). */
export function basemapParts(
  basemap: BasemapConfig,
  apiRoot: string,
  style: StyleSpecification | null = null,
): BasemapParts {
  if (basemap.kind === "openfreemap") return style === null ? NO_BASEMAP : styleParts(style);
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

/** The parts of a whole MapLibre style: its layers, the sources they read (each with the app's
 * attribution), its fonts, and its icons when they are one sprite. */
function styleParts(style: StyleSpecification): BasemapParts {
  const read = new Set(style.layers.map((layer) => ("source" in layer ? layer.source : null)));
  const sources = Object.fromEntries(
    Object.entries(style.sources)
      .filter(([id]) => read.has(id))
      .map(([id, source]) => [id, { ...source, attribution: OPENFREEMAP_ATTRIBUTION }]),
  ) as Record<string, SourceSpecification>;
  return {
    sources,
    layers: style.layers as AddLayerObject[],
    glyphs: style.glyphs ?? null,
    sprite: typeof style.sprite === "string" ? style.sprite : null,
  };
}

/** The style at `url`, or null (said on the console) when it does not load: offline, say. */
export async function fetchStyle(url: string | null): Promise<StyleSpecification | null> {
  try {
    if (url === null) throw new Error("the server names no style");
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return (await response.json()) as StyleSpecification;
  } catch (error) {
    console.error(`the basemap's style did not load (${String(error)}): no basemap drawn`);
    return null;
  }
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
