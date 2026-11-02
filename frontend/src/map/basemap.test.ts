// The basemap the server's GET /config names: OSM's raster tiles, Protomaps' vector tiles from
// the server with or without labels, its URLs absolute under /api, or OpenFreeMap's tiles in the
// style the server names, fetched first; the attribution the app's own; addBasemap putting it
// under the layers the map already draws; and the theme's variant of it (grayscale, light or
// dark), changed in place by Basemap.
import { namedFlavor } from "@protomaps/basemaps";
import type { StyleSpecification } from "maplibre-gl";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BasemapConfig } from "../api/types";
import {
  type BasemapMap,
  Basemap,
  addBasemap,
  basemapParts,
  fetchStyle,
  styleUrl,
} from "./basemap";

const ROOT = "http://test/api";
const NONE = { tiles: null, glyphs: null, sprite: null, style: null };
const RASTER: BasemapConfig = { ...NONE, kind: "raster" };
const VECTOR: BasemapConfig = { ...NONE, kind: "pmtiles", tiles: "/basemap/portland.pmtiles" };
const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const ONLINE: BasemapConfig = { ...NONE, kind: "openfreemap", style: STYLE_URL };
// The shape of OpenFreeMap's Positron, by hand: a source no layer reads, and one the layers do.
const POSITRON: StyleSpecification = {
  version: 8,
  sources: {
    ne2_shaded: {
      type: "raster",
      tiles: ["https://tiles.openfreemap.org/natural_earth/ne2sr/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 6,
    },
    openmaptiles: { type: "vector", url: "https://tiles.openfreemap.org/planet" },
  },
  sprite: "https://tiles.openfreemap.org/sprites/ofm_f384/ofm",
  glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#f2f3f0" } },
    { id: "water", type: "fill", source: "openmaptiles", "source-layer": "water" },
    {
      id: "label_city",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      layout: { "text-field": "{name}", "text-font": ["Noto Sans Regular"] },
    },
  ],
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
    for (const variant of ["grayscale", "light", "dark"] as const) {
      const background = basemapParts(VECTOR, ROOT, null, variant).layers[0];
      expect(background).toMatchObject({
        paint: { "background-color": namedFlavor(variant).water },
      }); // past the file's tiles the sea stays sea
    }
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

  it("draws OpenFreeMap's style: the sources its layers read, with the app's attribution", () => {
    const parts = basemapParts(ONLINE, ROOT, POSITRON);
    expect(Object.keys(parts.sources)).toEqual(["openmaptiles"]);
    const source = parts.sources.openmaptiles as { type: string; url: string; attribution: string };
    expect(source.type).toBe("vector");
    expect(source.url).toBe("https://tiles.openfreemap.org/planet");
    expect(source.attribution).toContain('href="https://openfreemap.org"');
    expect(source.attribution).toContain("© OpenMapTiles");
    expect(source.attribution).toContain("www.openstreetmap.org/copyright");
    expect(parts.layers).toEqual(POSITRON.layers);
    expect(parts.glyphs).toBe("https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf");
    expect(parts.sprite).toBe("https://tiles.openfreemap.org/sprites/ofm_f384/ofm");
  });

  it("draws nothing, never OSM's tiles, for OpenFreeMap without its style", () => {
    expect(basemapParts(ONLINE, ROOT, null)).toEqual({
      sources: {},
      layers: [],
      glyphs: null,
      sprite: null,
    });
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

describe("the basemap's variants", () => {
  it("draws Protomaps' light and dark flavors, without points of interest, in the same fonts", () => {
    for (const variant of ["light", "dark"] as const) {
      const parts = basemapParts(LABELLED, ROOT, null, variant);
      expect(parts.layers[0]).toMatchObject({
        id: "background",
        paint: { "background-color": namedFlavor(variant).water }, // the sea past the tiles
      });
      expect(parts.layers.map((l) => l.id)).not.toContain("pois");
      expect(parts.sprite).toBe("http://test/api/basemap/sprites/grayscale");
      const symbols = parts.layers.filter((l) => l.type === "symbol");
      const fonts = fontsOf(symbols.map((l) => (l as { layout?: Record<string, unknown> }).layout));
      expect([...fonts].sort()).toEqual([...FETCHED_FONTS].sort());
    }
    expect(basemapParts(LABELLED, ROOT, null, "grayscale")).toEqual(basemapParts(LABELLED, ROOT));
  });

  it("names OpenFreeMap's Dark for the dark variant, Positron otherwise", () => {
    expect(styleUrl(STYLE_URL, "dark")).toBe("https://tiles.openfreemap.org/styles/dark");
    expect(styleUrl(STYLE_URL, "light")).toBe(STYLE_URL);
    expect(styleUrl(STYLE_URL, "grayscale")).toBe(STYLE_URL);
    expect(styleUrl(null, "dark")).toBeNull();
  });
});

describe("fetchStyle", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("loads the style the server names", async () => {
    const fetched = vi.fn(async () => new Response(JSON.stringify(POSITRON)));
    vi.stubGlobal("fetch", fetched);
    expect(await fetchStyle(STYLE_URL)).toEqual(POSITRON);
    expect(fetched).toHaveBeenCalledWith(STYLE_URL);
  });

  it("is null, said on the console, when the style does not load", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal("fetch", async () => new Response("gone", { status: 404 }));
    expect(await fetchStyle(STYLE_URL)).toBeNull();
    vi.stubGlobal("fetch", async () => Promise.reject(new TypeError("offline")));
    expect(await fetchStyle(STYLE_URL)).toBeNull();
    expect(await fetchStyle(null)).toBeNull();
    expect(logged).toHaveBeenCalledTimes(3);
    expect(String(logged.mock.calls[1][0])).toContain("offline");
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

describe("Basemap", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubMap() {
    const calls: string[] = [];
    const map = {
      addSource: (id: string) => void calls.push(`add source ${id}`),
      addLayer: (layer: { id: string }, before?: string) =>
        void calls.push(`add ${layer.id} before ${before}`),
      removeLayer: (id: string) => void calls.push(`remove ${id}`),
      removeSource: (id: string) => void calls.push(`remove source ${id}`),
      setGlyphs: () => undefined,
      setSprite: () => undefined,
      getLayersOrder: () => ["wr-baseline"],
    } as unknown as BasemapMap;
    return { map, calls };
  }

  it("swaps the variant's layers and sources under the route's, once per look", async () => {
    const { map, calls } = stubMap();
    const basemap = new Basemap(map, VECTOR, ROOT);
    await basemap.show("grayscale");
    const grayscale = basemapParts(VECTOR, ROOT).layers.map((l) => l.id);
    expect(calls.filter((c) => c.startsWith("add "))).toHaveLength(grayscale.length + 1);
    calls.length = 0;
    await basemap.show("grayscale");
    expect(calls).toEqual([]);
    await basemap.show("dark");
    const dark = basemapParts(VECTOR, ROOT, null, "dark").layers.map((l) => l.id);
    expect(calls).toEqual([
      ...grayscale.map((id) => `remove ${id}`),
      "remove source protomaps",
      "add source protomaps",
      ...dark.map((id) => `add ${id} before wr-baseline`),
    ]);
  });

  it("fetches OpenFreeMap's style of the variant, and keeps one raster look", async () => {
    const fetched: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      fetched.push(url);
      return new Response(JSON.stringify(POSITRON));
    });
    const online = stubMap();
    const basemap = new Basemap(online.map, ONLINE, ROOT);
    await basemap.show("grayscale");
    await basemap.show("light"); // Positron as well
    await basemap.show("dark");
    await basemap.show("grayscale"); // each style fetched once, then kept
    expect(fetched).toEqual([STYLE_URL, "https://tiles.openfreemap.org/styles/dark"]);
    expect(online.calls.filter((c) => c.startsWith("remove "))).toHaveLength(8);
    const raster = stubMap();
    const osm = new Basemap(raster.map, RASTER, ROOT);
    await osm.show("grayscale");
    await osm.show("dark");
    expect(raster.calls).toEqual(["add source osm", "add osm before wr-baseline"]);
  });

  it("asks again for a style that did not load", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    let online = false;
    const fetched = vi.fn(async () =>
      online ? new Response(JSON.stringify(POSITRON)) : Promise.reject(new TypeError("offline")),
    );
    vi.stubGlobal("fetch", fetched);
    const basemap = new Basemap(stubMap().map, ONLINE, ROOT);
    await basemap.show("dark"); // nothing drawn
    await basemap.show("grayscale");
    online = true;
    await basemap.show("dark");
    await basemap.show("grayscale");
    expect(fetched).toHaveBeenCalledTimes(4);
    expect(logged).toHaveBeenCalledTimes(2);
    logged.mockRestore();
  });
});
