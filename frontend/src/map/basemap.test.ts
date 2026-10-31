// The basemap the server's GET /config names: OSM's raster tiles, or Protomaps' vector tiles
// from the server with or without labels, its URLs absolute under /api, the attribution the app's
// own; and addBasemap putting it under the layers the map already draws.
import { describe, expect, it, vi } from "vitest";
import type { BasemapConfig } from "../api/types";
import { type BasemapMap, addBasemap, basemapParts } from "./basemap";

const ROOT = "http://test/api";
const RASTER: BasemapConfig = { kind: "raster", tiles: null, glyphs: null, sprite: null };
const VECTOR: BasemapConfig = {
  kind: "pmtiles",
  tiles: "/basemap/portland.pmtiles",
  glyphs: null,
  sprite: null,
};
const LABELLED: BasemapConfig = {
  ...VECTOR,
  glyphs: "/basemap/fonts/{fontstack}/{range}.pbf",
  sprite: "/basemap/sprites/grayscale",
};
// The fonts tools/fetch_basemap.sh fetches: the labels must ask for no other.
const FETCHED_FONTS = [
  "Noto Sans Regular",
  "Noto Sans Medium",
  "Noto Sans Italic",
  "Noto Sans Devanagari Regular v1",
];

/** The font names anywhere in `value` (a layer's layout, its expressions included). */
function fontsOf(value: unknown, into = new Set<string>()): Set<string> {
  if (typeof value === "string" && value.startsWith("Noto")) into.add(value);
  else if (typeof value === "object" && value !== null) {
    Object.values(value).forEach((v) => fontsOf(v, into));
  }
  return into;
}

describe("basemapParts", () => {
  it("draws OpenStreetMap's raster tiles, muted, for a raster basemap", () => {
    const parts = basemapParts(RASTER, ROOT);
    expect(parts.sources).toEqual({
      osm: expect.objectContaining({
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        attribution: expect.stringContaining("www.openstreetmap.org/copyright"),
      }),
    });
    expect(parts.layers).toEqual([
      { id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.7 } },
    ]);
    expect([parts.glyphs, parts.sprite]).toEqual([null, null]);
  });

  it("reads Protomaps' tiles from the server, without labels when it has no fonts", () => {
    const parts = basemapParts(VECTOR, ROOT);
    const source = parts.sources.protomaps as { type: string; url: string; attribution: string };
    expect(source.type).toBe("vector");
    expect(source.url).toBe("pmtiles://http://test/api/basemap/portland.pmtiles");
    expect(source.attribution).toContain(">Protomaps</a>");
    expect(source.attribution).toContain("www.openstreetmap.org/copyright");
    expect(parts.layers.length).toBeGreaterThan(40);
    expect(parts.layers.filter((l) => l.type === "symbol")).toEqual([]);
    expect(parts.layers[0]).toMatchObject({ id: "background", type: "background" });
    for (const layer of parts.layers.slice(1)) {
      expect(layer).toMatchObject({ source: "protomaps" });
    }
    expect([parts.glyphs, parts.sprite]).toEqual([null, null]);
  });

  it("draws nothing, never OSM's tiles, for a pmtiles basemap without its file", () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const parts = basemapParts({ ...VECTOR, tiles: null }, ROOT);
      expect(parts).toEqual({ sources: {}, layers: [], glyphs: null, sprite: null });
      expect(logged).toHaveBeenCalledOnce();
    } finally {
      logged.mockRestore();
    }
  });

  it("labels it with the server's fonts and icons, the template's braces kept", () => {
    const parts = basemapParts(LABELLED, ROOT);
    expect(parts.glyphs).toBe("http://test/api/basemap/fonts/{fontstack}/{range}.pbf");
    expect(parts.sprite).toBe("http://test/api/basemap/sprites/grayscale");
    const symbols = parts.layers.filter((l) => l.type === "symbol");
    expect(symbols.length).toBeGreaterThan(5);
    const fonts = fontsOf(symbols.map((l) => (l as { layout?: Record<string, unknown> }).layout));
    expect([...fonts].sort()).toEqual([...FETCHED_FONTS].sort());
  });
});

describe("addBasemap", () => {
  function stubMap(existing: string[]) {
    const calls: string[] = [];
    const map: BasemapMap = {
      addSource: (id: string) => void calls.push(`source ${id}`),
      addLayer: (layer: { id: string }, before?: string) =>
        void calls.push(`layer ${layer.id} before ${before ?? "-"}`),
      setGlyphs: (url: string | null | undefined) => void calls.push(`glyphs ${url}`),
      setSprite: (url: string | null) => void calls.push(`sprite ${url}`),
      getLayersOrder: () => existing,
    } as unknown as BasemapMap;
    return { map, calls };
  }

  it("puts the basemap below the route's layers, in its own order", () => {
    const { map, calls } = stubMap(["wr-baseline", "wr-route"]);
    const parts = basemapParts(LABELLED, ROOT);
    addBasemap(map, parts);
    expect(calls.slice(0, 3)).toEqual([
      "glyphs http://test/api/basemap/fonts/{fontstack}/{range}.pbf",
      "sprite http://test/api/basemap/sprites/grayscale",
      "source protomaps",
    ]);
    expect(calls.slice(3)).toEqual(parts.layers.map((l) => `layer ${l.id} before wr-baseline`));
  });

  it("sets no fonts or icons for a basemap without labels, and stacks on an empty map", () => {
    const { map, calls } = stubMap([]);
    addBasemap(map, basemapParts(RASTER, ROOT));
    expect(calls).toEqual(["source osm", "layer osm before -"]);
  });
});
