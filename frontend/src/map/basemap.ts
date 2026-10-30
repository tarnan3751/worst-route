// The basemap (docs/reference/frontend.md, **Basemap**), configured here and nowhere else.
//
// Locally the map shows OpenStreetMap's raster tiles, at hobby volume and with their attribution,
// as the tile usage policy asks (https://operations.osmfoundation.org/policies/tiles/); the
// browser's own User-Agent and Referer are what it requires, and MapLibre fetches only the tiles
// in view (no prefetching, no offline cache). That policy rules the OSM tiles out for a public
// site: before hosting publicly, set VECTOR_STYLE_URL to a vector style (Protomaps PMTiles,
// MapTiler, Stadia) and the map takes it instead.
import type { StyleSpecification } from "maplibre-gl";

// >>> The switch for public hosting: a vector style URL, or null for OSM's raster tiles. <<<
export const VECTOR_STYLE_URL: string | null = null;

const OSM_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">' +
  "OpenStreetMap</a> contributors";

const OSM_RASTER: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: OSM_ATTRIBUTION,
    },
  },
  // Muted, so the route stands out.
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.7 } }],
};

/** The style the map starts with. */
export function basemapStyle(): StyleSpecification | string {
  return VECTOR_STYLE_URL ?? OSM_RASTER;
}
