// The land outside the contiguous US drawn flat (map/outside.ts): the outline it is cut around,
// public/conus.json, as tools/desktop_data.py conus writes it (a GeoJSON MultiPolygon within the
// contiguous US's box, small enough to fetch at every start), fetched beside the page with its
// polygons' own holes filled; over Protomaps' earth and land cover the earth again in the outside
// tone and the outline in the flavor's land colour, the rest of the basemap and the route's layers
// above them, the land colour following the flavor through a change of variant; else the mask, a
// ring round the world with each polygon a hole, just below the basemap's first water layer or on
// top of the raster tiles; the basemap's labels but the water's drawing a point only within the
// outline, their own filters kept, its lines as they are; the outside tone the theme's.
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { namedFlavor } from "@protomaps/basemaps";
import type { AddLayerObject, FilterSpecification, StyleSpecification } from "maplibre-gl";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BasemapConfig, LonLat } from "../api/types";
import { night } from "../theme/presets/night";
import { studio } from "../theme/presets/studio";
import type { Theme } from "../theme/theme";
import { type BasemapMap, Basemap, addBasemap, basemapParts } from "./basemap";
import { asExpression } from "./filters";
import {
  EARTH_OUTSIDE,
  MASK,
  type MultiPolygon,
  US_LAND,
  fetchOutline,
  maskOf,
  paintOutside,
  withOutside,
} from "./outside";

const root = fileURLToPath(new URL("../..", import.meta.url));
const OUTLINE = join(root, "public/conus.json");
const [WEST, SOUTH, EAST, NORTH] = [-125, 24.4, -66.8, 49.5];

const ROOT = "http://test/api";
const NONE = { tiles: null, glyphs: null, sprite: null, style: null };
const RASTER: BasemapConfig = { ...NONE, kind: "raster" };
const VECTOR: BasemapConfig = {
  ...NONE,
  kind: "pmtiles",
  tiles: "/basemap/us.pmtiles",
  glyphs: "/basemap/fonts/{fontstack}/{range}.pbf",
  sprite: "/basemap/sprites/grayscale",
};
const ONLINE: BasemapConfig = { ...NONE, kind: "openfreemap", style: "https://ofm/positron" };
const ROUTE_LAYERS = ["wr-baseline", "wr-route-casing", "wr-route", "wr-flash"];

/** Twice a ring's signed area, positive counterclockwise. */
function area(ring: LonLat[]): number {
  let sum = 0;
  for (let k = 0; k + 1 < ring.length; k++) {
    sum += ring[k][0] * ring[k + 1][1] - ring[k + 1][0] * ring[k][1];
  }
  return sum;
}

const square = (west: number, south: number, size: number): LonLat[] => [
  [west, south],
  [west + size, south],
  [west + size, south + size],
  [west, south + size],
  [west, south],
];

// Two polygons, one wound each way, as the outline is once fetched (its holes filled).
const US: MultiPolygon = {
  type: "MultiPolygon",
  coordinates: [[square(-100, 30, 10)], [[...square(-80, 40, 2)].reverse()]],
};
const POINTS = ["any", ["!=", ["geometry-type"], "Point"], ["within", US]];

const filterOf = (layer: AddLayerObject | undefined): FilterSpecification | undefined =>
  (layer as { filter?: FilterSpecification } | undefined)?.filter;

/** A stand-in map keeping its layers in order, which refuses to remove a source a layer reads (as
 * MapLibre does). */
function orderedMap(existing: string[]) {
  const order = [...existing];
  const layers = new Map<string, AddLayerObject>();
  const sources = new Set<string>();
  const paint: string[] = [];
  const map = {
    addSource: (id: string) => void sources.add(id),
    removeSource: (id: string) => {
      const reader = [...layers.values()].find((layer) => "source" in layer && layer.source === id);
      if (reader) throw new Error(`source ${id} removed while ${reader.id} reads it`);
      sources.delete(id);
    },
    addLayer: (layer: AddLayerObject, before?: string) => {
      const at = before === undefined ? -1 : order.indexOf(before);
      order.splice(at < 0 ? order.length : at, 0, layer.id);
      layers.set(layer.id, layer);
    },
    removeLayer: (id: string) => {
      order.splice(order.indexOf(id), 1);
      layers.delete(id);
    },
    getLayersOrder: () => [...order],
    getLayer: (id: string) => layers.get(id),
    setPaintProperty: (id: string, name: string, value: string) =>
      void paint.push(`${id} ${name} ${value}`),
    setGlyphs: () => undefined,
    setSprite: () => undefined,
  };
  return { map: map as unknown as BasemapMap, order, layers, sources, paint };
}

describe("the contiguous US's outline", () => {
  it("is a GeoJSON MultiPolygon within the contiguous US's box, under 100 kB", () => {
    expect(statSync(OUTLINE).size).toBeLessThan(100_000);
    const outline = JSON.parse(readFileSync(OUTLINE, "utf8")) as {
      type: string;
      coordinates: unknown;
    };
    expect(outline.type).toBe("MultiPolygon");
    const polygons = outline.coordinates as number[][][][];
    expect(polygons.length).toBeGreaterThan(0);
    for (const polygon of polygons) {
      expect(polygon.length).toBeGreaterThan(0);
      for (const ring of polygon) {
        expect(ring.length).toBeGreaterThanOrEqual(4);
        expect(ring[ring.length - 1]).toEqual(ring[0]); // closed
        for (const position of ring) {
          expect(position).toHaveLength(2);
          const [lon, lat] = position;
          expect(lon).toBeGreaterThanOrEqual(WEST);
          expect(lon).toBeLessThanOrEqual(EAST);
          expect(lat).toBeGreaterThanOrEqual(SOUTH);
          expect(lat).toBeLessThanOrEqual(NORTH);
        }
      }
    }
  });
});

describe("fetchOutline", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("fetches the outline beside the page, its polygons' own holes filled", async () => {
    const lake = [...square(-96, 34, 2)].reverse();
    const outline = { ...US, coordinates: [[square(-100, 30, 10), lake], US.coordinates[1]] };
    const fetched = vi.fn(async () => new Response(JSON.stringify(outline)));
    vi.stubGlobal("fetch", fetched);
    expect(await fetchOutline()).toEqual(US);
    expect(fetched).toHaveBeenCalledWith("conus.json");
  });

  it("is null, said on the console, when the outline does not load", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal("fetch", async () => new Response("gone", { status: 404 }));
    expect(await fetchOutline()).toBeNull();
    vi.stubGlobal("fetch", async () => Promise.reject(new TypeError("offline")));
    expect(await fetchOutline()).toBeNull();
    expect(logged).toHaveBeenCalledTimes(2);
    expect(String(logged.mock.calls[1][0])).toContain("offline");
  });
});

describe("maskOf", () => {
  it("is a ring round the world with each polygon's outer ring a hole, wound against it", () => {
    const mask = maskOf(US);
    expect(mask.type).toBe("Polygon");
    const [world, ...holes] = mask.coordinates;
    expect(world).toEqual([
      [-180, -85],
      [180, -85],
      [180, 85],
      [-180, 85],
      [-180, -85],
    ]);
    expect(area(world)).toBeGreaterThan(0); // counterclockwise
    expect(holes).toHaveLength(2);
    for (const hole of holes) expect(area(hole)).toBeLessThan(0); // clockwise
    expect(holes[0]).toEqual([...square(-100, 30, 10)].reverse()); // wound again
    expect(holes[1]).toEqual([...square(-80, 40, 2)].reverse()); // as it was
    expect(US.coordinates[0][0]).toEqual(square(-100, 30, 10)); // the outline left alone
  });

  it("cuts the committed outline's every polygon out, each clockwise", () => {
    const outline = JSON.parse(readFileSync(OUTLINE, "utf8")) as MultiPolygon;
    const [world, ...holes] = maskOf(outline).coordinates;
    expect(area(world)).toBeGreaterThan(0);
    expect(holes).toHaveLength(outline.coordinates.length);
    for (const hole of holes) expect(area(hole)).toBeLessThan(0);
  });
});

describe("withOutside on Protomaps' basemap", () => {
  it("puts the earth again in the outside tone and the US in the land's colour over the earth", () => {
    const { map, order, layers } = orderedMap(ROUTE_LAYERS);
    const parts = basemapParts(VECTOR, ROOT);
    addBasemap(map, withOutside(parts, US, studio));
    const ids = parts.layers.map((l) => l.id);
    expect(ids.slice(0, 3)).toEqual(["background", "earth", "landuse_park"]);
    expect(order).toEqual([
      "background",
      "earth",
      EARTH_OUTSIDE,
      US_LAND,
      ...ids.slice(2),
      ...ROUTE_LAYERS,
    ]);
    const earth = parts.layers[1];
    expect(layers.get(EARTH_OUTSIDE)).toEqual({
      ...earth,
      id: EARTH_OUTSIDE,
      paint: { "fill-color": "#dfe2e6" },
    });
    expect(layers.get(US_LAND)).toEqual({
      id: US_LAND,
      type: "fill",
      source: US_LAND,
      paint: { "fill-color": "#cccccc" },
    });
  });

  it("puts them over the light and dark flavors' land cover too, in each flavor's land colour", () => {
    for (const variant of ["light", "dark"] as const) {
      const parts = basemapParts(VECTOR, ROOT, null, variant);
      const ids = parts.layers.map((l) => l.id);
      expect(ids.slice(0, 4), variant).toEqual([
        "background",
        "earth",
        "landcover",
        "landuse_park",
      ]);
      const drawn = withOutside(parts, US, night).layers;
      expect(drawn.map((l) => l.id)).toEqual([
        "background",
        "earth",
        "landcover",
        EARTH_OUTSIDE,
        US_LAND,
        ...ids.slice(3),
      ]);
      expect(drawn[4]).toMatchObject({ paint: { "fill-color": namedFlavor(variant).earth } });
      expect(drawn[3]).toMatchObject({ source: "protomaps", "source-layer": "earth" });
      expect(drawn[3]).toMatchObject({ paint: { "fill-color": "#141a30" } });
    }
  });

  it("draws the US from a GeoJSON source of the outline, and no mask", () => {
    const parts = withOutside(basemapParts(VECTOR, ROOT), US, studio);
    expect(Object.keys(parts.sources)).toEqual(["protomaps", US_LAND]);
    expect(parts.sources[US_LAND]).toEqual({
      type: "geojson",
      data: { type: "Feature", properties: {}, geometry: US },
    });
    expect(parts.layers.map((l) => l.id)).not.toContain(MASK);
  });
});

describe("withOutside's labels", () => {
  it("draws Protomaps' labels but the water's only within the outline, its lines as they are", () => {
    for (const variant of ["grayscale", "light", "dark"] as const) {
      const parts = basemapParts(VECTOR, ROOT, null, variant);
      const drawn = withOutside(parts, US, studio).layers.filter(
        (l) => l.id !== EARTH_OUTSIDE && l.id !== US_LAND,
      );
      expect(drawn.map((l) => l.id)).toEqual(parts.layers.map((l) => l.id));
      let filtered = 0;
      parts.layers.forEach((layer, k) => {
        const water = "source-layer" in layer && layer["source-layer"] === "water";
        if (layer.type === "symbol" && !water) {
          const own = filterOf(layer);
          const points = own === undefined ? POINTS : ["all", asExpression(own), POINTS];
          expect(drawn[k], layer.id).toEqual({ ...layer, filter: points });
          filtered += 1;
        } else {
          expect(drawn[k], layer.id).toBe(layer); // fills, lines, the water's labels
        }
      });
      expect(filtered, variant).toBeGreaterThan(8);
    }
    const locality = withOutside(basemapParts(VECTOR, ROOT), US, studio).layers.find(
      (l) => l.id === "places_locality",
    );
    expect(filterOf(locality)).toEqual(["all", ["==", ["get", "kind"], "locality"], POINTS]);
  });

  it("filters OpenFreeMap's city labels, not its lines or its water's names", () => {
    const style: StyleSpecification = {
      version: 8,
      sources: { openmaptiles: { type: "vector", url: "https://ofm/planet" } },
      layers: [
        { id: "water", type: "fill", source: "openmaptiles", "source-layer": "water" },
        {
          id: "highway",
          type: "line",
          source: "openmaptiles",
          "source-layer": "transportation",
          filter: ["==", ["get", "class"], "motorway"],
        },
        { id: "water_name", type: "symbol", source: "openmaptiles", "source-layer": "water_name" },
        { id: "label_city", type: "symbol", source: "openmaptiles", "source-layer": "place" },
      ],
    };
    const parts = withOutside(basemapParts(ONLINE, ROOT, style), US, studio);
    const filters = Object.fromEntries(parts.layers.map((l) => [l.id, filterOf(l)]));
    expect(filters.label_city).toEqual(POINTS);
    expect(filters.highway).toEqual(["==", ["get", "class"], "motorway"]);
    expect(filters.water_name).toBeUndefined();
  });
});

describe("withOutside on another basemap", () => {
  it("puts OpenFreeMap's mask just below its first water layer, over its land's", () => {
    const style: StyleSpecification = {
      version: 8,
      sources: { openmaptiles: { type: "vector", url: "https://ofm/planet" } },
      layers: [
        { id: "background", type: "background" },
        { id: "park", type: "fill", source: "openmaptiles", "source-layer": "park" },
        { id: "water", type: "fill", source: "openmaptiles", "source-layer": "water" },
        { id: "waterway", type: "line", source: "openmaptiles", "source-layer": "waterway" },
        { id: "label_city", type: "symbol", source: "openmaptiles", "source-layer": "place" },
      ],
    };
    const { map, order, layers } = orderedMap(ROUTE_LAYERS);
    const parts = withOutside(basemapParts(ONLINE, ROOT, style), US, studio);
    addBasemap(map, parts);
    expect(order).toEqual([
      "background",
      "park",
      MASK,
      "water",
      "waterway",
      "label_city",
      ...ROUTE_LAYERS,
    ]);
    expect(layers.get(MASK)).toEqual({
      id: MASK,
      type: "fill",
      source: MASK,
      paint: { "fill-color": "#dfe2e6" },
    });
    expect(parts.sources[MASK]).toEqual({
      type: "geojson",
      data: { type: "Feature", properties: {}, geometry: maskOf(US) },
    });
  });

  it("covers the raster basemap with the mask on top, which has no vector layers", () => {
    const parts = withOutside(basemapParts(RASTER, ROOT), US, night);
    expect(parts.layers.map((l) => l.id)).toEqual(["osm", MASK]);
    expect(parts.layers[1]).toMatchObject({ paint: { "fill-color": "#141a30" } });
  });

  it("draws the mask alone when the basemap draws nothing (its style did not load)", () => {
    const parts = withOutside(basemapParts(ONLINE, ROOT, null), US, studio);
    expect(parts.layers.map((l) => l.id)).toEqual([MASK]);
    expect(Object.keys(parts.sources)).toEqual([MASK]);
  });

  it("leaves the basemap as it is when the outline did not load", () => {
    const parts = basemapParts(VECTOR, ROOT);
    expect(withOutside(parts, null, studio)).toBe(parts);
  });
});

describe("the land outside through a change of variant", () => {
  it("stays over the new flavor's earth, the US in its land colour, the tone the theme's", async () => {
    const { map, order, layers, sources } = orderedMap(ROUTE_LAYERS);
    let theme: Theme = studio;
    const basemap = new Basemap(map, VECTOR, ROOT, (parts) => withOutside(parts, US, theme));
    await basemap.show("grayscale");
    expect(layers.get(US_LAND)).toMatchObject({ paint: { "fill-color": "#cccccc" } });
    theme = night;
    await basemap.show("dark"); // the last one's layers gone before their sources
    const dark = basemapParts(VECTOR, ROOT, null, "dark").layers.map((l) => l.id);
    expect(order).toEqual([
      ...dark.slice(0, 3),
      EARTH_OUTSIDE,
      US_LAND,
      ...dark.slice(3),
      ...ROUTE_LAYERS,
    ]);
    expect(layers.get(EARTH_OUTSIDE)).toMatchObject({ paint: { "fill-color": "#141a30" } });
    expect(layers.get(US_LAND)).toMatchObject({ paint: { "fill-color": "#1f1f1f" } });
    expect(filterOf(layers.get("places_locality"))).toEqual([
      "all",
      ["==", ["get", "kind"], "locality"],
      POINTS,
    ]);
    expect([...sources]).toEqual(["protomaps", US_LAND]);
  });
});

describe("paintOutside", () => {
  it("colours the outside tone as the theme says once it is drawn, and does nothing before", () => {
    const painted = (existing: string[], parts: Parameters<typeof addBasemap>[1]) => {
      const { map, paint } = orderedMap(existing);
      const stub = map as unknown as Parameters<typeof paintOutside>[0];
      paintOutside(stub, night);
      expect(paint).toEqual([]);
      addBasemap(map, parts);
      paintOutside(stub, night);
      return paint;
    };
    const raster = withOutside(basemapParts(RASTER, ROOT), US, studio);
    expect(painted([], raster)).toEqual(["outside fill-color #141a30"]);
    const vector = withOutside(basemapParts(VECTOR, ROOT), US, studio);
    expect(painted([], vector)).toEqual(["earth-outside fill-color #141a30"]);
  });
});
