// The route on the map (docs/reference/frontend.md, **Live growth**).
//
// Four GeoJSON sources: the baseline (the sensible route, dashed), the worst route (with
// `lineMetrics`, so a `line-gradient` colours it from the start to the end, over a casing), the
// flash (each new splice, bright, for 400 ms), and on top the stretch of a step the directions
// panel points at (`highlight`). Their colours and widths are the theme's (`paintRoute`, at
// creation and again when the theme changes; docs/reference/frontend.md, **Themes**).
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
import type { Theme } from "../theme/theme";

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
const CASING = `${ROUTE}-casing`;
const FLASH = "wr-flash";
const HIGHLIGHT = "directions-highlight";
const FLASH_MS = 400;

/** A paint property of a layer, and its value, as `setPaintProperty` takes them. */
export type PaintName = Parameters<MapLibreMap["setPaintProperty"]>[1];
type PaintValue = Parameters<MapLibreMap["setPaintProperty"]>[2];

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

/** Colour the route's layers, and size the route and its casing, as `theme` says: the route's
 * colours spread evenly from its start to its end (one colour, a flat line); a glowing casing in
 * the route's first colour, wide, translucent and blurred. */
export function paintRoute(map: Pick<MapLibreMap, "setPaintProperty">, theme: Theme): void {
  const { colors, shape } = theme;
  const route = colors.route.length > 1 ? colors.route : [colors.route[0], colors.route[0]];
  const stops = route.flatMap((color, k) => [k / (route.length - 1), color]);
  const paint: [string, PaintName, PaintValue][] = [
    [BASELINE, "line-color", colors.baseline],
    [CASING, "line-color", shape.glow ? colors.route[0] : colors.routeCasing],
    [CASING, "line-width", shape.casingWidth],
    [CASING, "line-opacity", shape.glow ? 0.35 : 0.85],
    [CASING, "line-blur", shape.glow ? shape.casingWidth / 2 : 0],
    [ROUTE, "line-width", shape.routeWidth],
    [ROUTE, "line-gradient", ["interpolate", ["linear"], ["line-progress"], ...stops]],
    [FLASH, "line-color", colors.flash],
    [HIGHLIGHT, "line-color", colors.highlight],
  ];
  for (const [layer, name, value] of paint) map.setPaintProperty(layer, name, value);
}

export class RouteLayer {
  readonly #map: MapLibreMap;
  #line: RouteLine = { points: [], index: [] };
  #flashes: { coords: LonLat[]; until: number }[] = [];
  #frame = 0;
  #routeDirty = false;
  #flashTimer = 0;

  /** Adds the sources and layers, painted as `theme` says; the map's style must be loaded. */
  constructor(map: MapLibreMap, theme: Theme) {
    this.#map = map;
    for (const id of [BASELINE, FLASH, HIGHLIGHT]) {
      map.addSource(id, { type: "geojson", data: lines([]) });
    }
    map.addSource(ROUTE, { type: "geojson", data: lines([]), lineMetrics: true });
    const round = { "line-join": "round", "line-cap": "round" } as const;
    map.addLayer({
      id: BASELINE,
      type: "line",
      source: BASELINE,
      layout: round,
      paint: { "line-width": 3, "line-dasharray": [2, 1.5] },
    });
    map.addLayer({ id: CASING, type: "line", source: ROUTE, layout: round });
    map.addLayer({ id: ROUTE, type: "line", source: ROUTE, layout: round });
    map.addLayer({
      id: FLASH,
      type: "line",
      source: FLASH,
      layout: round,
      paint: { "line-width": 7, "line-blur": 1 },
    });
    map.addLayer({
      id: HIGHLIGHT,
      type: "line",
      source: HIGHLIGHT,
      layout: round,
      paint: { "line-width": 8, "line-opacity": 0.9 },
    });
    paintRoute(map, theme);
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

  /** A stretch of the route drawn over it, or none. */
  highlight(line: LonLat[] | null): void {
    this.#source(HIGHLIGHT).setData(lines(line ? [line] : []));
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
