// The cities a click snaps to: the basemap's locality labels, a label drawn under the click first,
// else the nearest point within CITY_HIT_PX of it; the layers that draw them, by the basemap's kind.
import { layers, namedFlavor } from "@protomaps/basemaps";
import type { MapGeoJSONFeature } from "maplibre-gl";
import { describe, expect, it } from "vitest";
import { CITY_HIT_PX, CITY_LAYERS, type CityMap, cityAt } from "./cities";

// A map 1,024 px to the degree (exact in floating point), (-123, 46) at its top left.
const project = ([lon, lat]: [number, number]) => ({ x: (lon + 123) * 1024, y: (46 - lat) * 1024 });
const at = (x: number, y: number): [number, number] => [x / 1024 - 123, 46 - y / 1024];

function feature(lonLat: [number, number], properties: object, type = "Point"): MapGeoJSONFeature {
  const geometry = type === "Point" ? { type, coordinates: lonLat } : { type, coordinates: [] };
  return { geometry, properties } as unknown as MapGeoJSONFeature;
}

/** A map drawing `drawn` layers, whose labels `atClick` are drawn under the click and `nearby`
 * meet the box around it only (a box's query answers both, as MapLibre's does). */
function stubMap(
  { atClick = [], nearby = [] }: { atClick?: MapGeoJSONFeature[]; nearby?: MapGeoJSONFeature[] },
  drawn = ["places_locality"],
) {
  const queries: { query: unknown; layers: unknown }[] = [];
  const map = {
    getLayer: (id: string) => (drawn.includes(id) ? { id } : undefined),
    queryRenderedFeatures: (query: unknown[], options: { layers: string[] }) => {
      queries.push({ query, layers: options.layers });
      return typeof query[0] === "number" ? atClick : [...atClick, ...nearby];
    },
    project,
  } as unknown as CityMap;
  return { map, queries };
}

const CLICK = { x: 300, y: 500 };
const BOX = [
  [300 - CITY_HIT_PX, 500 - CITY_HIT_PX],
  [300 + CITY_HIT_PX, 500 + CITY_HIT_PX],
];
const city = (stub: ReturnType<typeof stubMap>) => cityAt(stub.map, CLICK, CITY_LAYERS.pmtiles);

describe("the city a click snaps to", () => {
  it("is one whose label is drawn under the click, its point however far, before any nearby", () => {
    const portland = feature(at(320, 500), { "name:en": "Portland", name: "Portland" }); // 20 px
    const gresham = feature(at(305, 503), { name: "Gresham" }); // 5.8 px, its label beside
    const stub = stubMap({ atClick: [portland], nearby: [gresham] });
    const [lon, lat] = at(320, 500);
    expect(city(stub)).toEqual({ lat, lon, name: "Portland" });
    // the click's point, on the layers the style has, and no box once a label is under it
    expect(stub.queries).toEqual([{ query: [300, 500], layers: ["places_locality"] }]);
    // two labels under the click: the one whose point is nearer
    const vancouver = feature(at(300, 515), { name: "Vancouver" }); // 15 px
    expect(city(stubMap({ atClick: [portland, vancouver] }))?.name).toBe("Vancouver");
  });

  it("is else the nearest whose point lies within 12 px, at its point with its name", () => {
    const portland = feature(at(305, 503), { "name:en": "Portland", name: "Portland" }); // 5.8 px
    const gresham = feature(at(291, 507), { name: "Gresham" }); // 11.4 px
    const stub = stubMap({ nearby: [gresham, portland] });
    const [lon, lat] = at(305, 503);
    expect(city(stub)).toEqual({ lat, lon, name: "Portland" });
    // the click's point first, then the box around it
    expect(stub.queries).toEqual([
      { query: [300, 500], layers: ["places_locality"] },
      { query: BOX, layers: ["places_locality"] },
    ]);
    expect(city(stubMap({ nearby: [gresham] }))?.name).toBe("Gresham");
  });

  it("is none when no label is under the click and every point lies beyond 12 px", () => {
    const corner = feature(at(309, 509), { name: "Troutdale" }); // 12.7 px, its label in the box
    const stub = stubMap({ nearby: [corner] });
    expect(city(stub)).toBeNull();
    expect(stub.queries).toHaveLength(2);
    const edge = feature(at(312, 500), { name: "Fairview" }); // 12 px exactly
    expect(city(stubMap({ nearby: [edge] }))?.name).toBe("Fairview");
  });

  it("reads each basemap's name, and skips a feature without one or not a point", () => {
    const near = at(302, 500);
    const named = (properties: object) =>
      city(stubMap({ atClick: [feature(near, properties)] }))?.name ?? null;
    expect(named({ "name:en": "Saint Helens", name: "St. Helens" })).toBe("Saint Helens");
    expect(named({ name_en: "Hood River", name: "Hood River (OR)" })).toBe("Hood River");
    expect(named({ name: "Sandy" })).toBe("Sandy");
    expect(named({})).toBeNull();
    expect(named({ name: "" })).toBeNull();
    const line = feature(near, { name: "Columbia River" }, "LineString");
    const far = feature(at(300, 511), { name: "Maywood Park" });
    expect(city(stubMap({ atClick: [line], nearby: [far] }))?.name).toBe("Maywood Park");
  });

  it("is none, without a query, when the map draws none of the layers", () => {
    const portland = feature(at(300, 500), { name: "Portland" });
    for (const [layersOf, drawn] of [
      [CITY_LAYERS.raster, ["osm"]],
      [CITY_LAYERS.pmtiles, []], // a pmtiles basemap without labels, or none yet
      [CITY_LAYERS.openfreemap, ["places_locality"]], // another kind's
    ] as const) {
      const { map, queries } = stubMap({ atClick: [portland] }, [...drawn]);
      expect(cityAt(map, CLICK, layersOf)).toBeNull();
      expect(queries).toEqual([]);
    }
  });

  it("asks only the layers the style has", () => {
    const { map, queries } = stubMap({}, ["label_town", "label_city"]);
    expect(cityAt(map, CLICK, CITY_LAYERS.openfreemap)).toBeNull();
    expect(queries.map(({ layers }) => layers)).toEqual([
      ["label_city", "label_town"],
      ["label_city", "label_town"],
    ]);
  });
});

describe("the layers that draw cities", () => {
  it("are none on the raster basemap, Protomaps' localities, Positron's cities, towns and villages", () => {
    expect(CITY_LAYERS).toEqual({
      raster: [],
      pmtiles: ["places_locality"],
      openfreemap: ["label_city", "label_city_capital", "label_town", "label_village"],
    });
  });

  it("are Protomaps' locality labels in every flavor the app draws, and none without labels", () => {
    for (const flavor of ["grayscale", "light", "dark"]) {
      const labelled = layers("protomaps", namedFlavor(flavor), { lang: "en" });
      const places = labelled.filter(
        (layer) => "source-layer" in layer && layer["source-layer"] === "places",
      );
      const cities = places.filter(({ id }) => CITY_LAYERS.pmtiles.includes(id));
      expect(cities.map(({ id, type }) => [id, type])).toEqual([["places_locality", "symbol"]]);
      expect(cities[0]).toMatchObject({ filter: ["==", "kind", "locality"] });
      // the neighbourhoods, the states and the countries are not cities
      expect(places.map(({ id }) => id).sort()).toEqual([
        "places_country",
        "places_locality",
        "places_region",
        "places_subplace",
      ]);
      const bare = layers("protomaps", namedFlavor(flavor)).map(({ id }) => id);
      expect(bare.filter((id) => CITY_LAYERS.pmtiles.includes(id))).toEqual([]);
    }
  });
});
