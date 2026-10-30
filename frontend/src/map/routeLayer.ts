// The route on the map (docs/reference/frontend.md, **Live growth**).
//
// Three GeoJSON sources: the baseline (the sensible route, grey and dashed), the worst route
// (with `lineMetrics`, so a `line-gradient` colours it from warm at the start to hot at the end,
// over a white casing), and the flash (each new splice, bright, for 400 ms).
//
// The route is kept as the stream sends it (docs/reference/api.md, **Applying the stream**): its
// [lon, lat] points, and the index among them of each route node (consecutive edges share their
// junction point). A snapshot replaces both; a splice replaces route nodes i..j by its own
// points (`spliceLine`, the pure function the tests replay a recorded stream through). Updates
// are drawn at most once per animation frame. Once the route is stored, `setFinal` draws the
// document's full geometry, which no splice follows.
//
// maplibre-gl is imported for its types only, so the tests load this module in Node.
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import type { LineString, LonLat } from "../api/types";

/** A route as drawn: its points, and the index among them of each route node. */
export interface RouteLine {
  points: LonLat[];
  index: number[];
}

/** `line` with route nodes i..j replaced by a detour drawn by `coords`, whose node k is
 * `coords[nodeIndex[k]]`. */
export function spliceLine(
  line: RouteLine,
  i: number,
  j: number,
  coords: LonLat[],
  nodeIndex: number[],
): RouteLine {
  const a = line.index[i];
  const b = line.index[j];
  const shift = coords.length - (b - a + 1);
  return {
    points: line.points.slice(0, a).concat(coords, line.points.slice(b + 1)),
    index: line.index.slice(0, i).concat(
      nodeIndex.map((k) => a + k),
      line.index.slice(j + 1).map((k) => k + shift),
    ),
  };
}

const BASELINE = "wr-baseline";
const ROUTE = "wr-route";
const FLASH = "wr-flash";
const FLASH_MS = 400;
const GRADIENT = ["#fdd049", "#f98e2b", "#e8452c", "#b10026"];

function lines(coordinates: LonLat[][]) {
  return {
    type: "FeatureCollection" as const,
    features: coordinates
      .filter((line) => line.length > 1)
      .map((line) => ({
        type: "Feature" as const,
        properties: {},
        geometry: { type: "LineString" as const, coordinates: line },
      })),
  };
}

export class RouteLayer {
  readonly #map: MapLibreMap;
  #line: RouteLine = { points: [], index: [] };
  #flashes: { coords: LonLat[]; until: number }[] = [];
  #frame = 0;
  #routeDirty = false;
  #flashTimer = 0;

  /** Adds the sources and layers; the map's style must be loaded. */
  constructor(map: MapLibreMap) {
    this.#map = map;
    for (const id of [BASELINE, FLASH]) map.addSource(id, { type: "geojson", data: lines([]) });
    map.addSource(ROUTE, { type: "geojson", data: lines([]), lineMetrics: true });
    const round = { "line-join": "round", "line-cap": "round" } as const;
    map.addLayer({
      id: BASELINE,
      type: "line",
      source: BASELINE,
      layout: round,
      paint: { "line-color": "#4b5563", "line-width": 3, "line-dasharray": [2, 1.5] },
    });
    map.addLayer({
      id: `${ROUTE}-casing`,
      type: "line",
      source: ROUTE,
      layout: round,
      paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": 0.85 },
    });
    const stops = GRADIENT.flatMap((color, k) => [k / (GRADIENT.length - 1), color]);
    map.addLayer({
      id: ROUTE,
      type: "line",
      source: ROUTE,
      layout: round,
      paint: {
        "line-width": 4,
        "line-gradient": ["interpolate", ["linear"], ["line-progress"], ...stops],
      },
    });
    map.addLayer({
      id: FLASH,
      type: "line",
      source: FLASH,
      layout: round,
      paint: { "line-color": "#00e5ff", "line-width": 7, "line-blur": 1 },
    });
  }

  /** The points drawn now. */
  get points(): LonLat[] {
    return this.#line.points;
  }

  setBaseline(geometry: LineString | null): void {
    this.#source(BASELINE).setData(lines(geometry ? [geometry.coordinates] : []));
  }

  /** The whole route, from a `snapshot` event. */
  setSnapshot(geometry: LineString, nodeIndex: number[]): void {
    this.#line = { points: geometry.coordinates, index: nodeIndex };
    this.#draw(true);
  }

  /** One splice of a `progress` event, flashed. */
  applySplice(i: number, j: number, coords: LonLat[], nodeIndex: number[]): void {
    this.#line = spliceLine(this.#line, i, j, coords, nodeIndex);
    this.#flashes.push({ coords, until: performance.now() + FLASH_MS });
    this.#draw(true);
  }

  /** The stored route's geometry, every point of it; no splice follows. */
  setFinal(geometry: LineString): void {
    this.#line = { points: geometry.coordinates, index: [] };
    this.#draw(true);
  }

  clear(): void {
    this.#line = { points: [], index: [] };
    this.#flashes = [];
    this.setBaseline(null);
    this.#draw(true);
  }

  #source(id: string): GeoJSONSource {
    return this.#map.getSource(id) as GeoJSONSource;
  }

  /** Draw at the next frame: the route if it changed, and the flashes still lit. */
  #draw(routeChanged: boolean): void {
    this.#routeDirty ||= routeChanged;
    if (this.#frame !== 0) return;
    this.#frame = requestAnimationFrame(() => {
      this.#frame = 0;
      if (this.#routeDirty) this.#source(ROUTE).setData(lines([this.#line.points]));
      this.#routeDirty = false;
      const now = performance.now();
      this.#flashes = this.#flashes.filter((flash) => flash.until > now);
      this.#source(FLASH).setData(lines(this.#flashes.map((flash) => flash.coords)));
      clearTimeout(this.#flashTimer);
      if (this.#flashes.length > 0) {
        const next = this.#flashes[0].until - now; // the oldest, as they are added in order
        this.#flashTimer = window.setTimeout(() => this.#draw(false), next);
      }
    });
  }
}
