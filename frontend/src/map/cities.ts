// The cities on the basemap (docs/reference/frontend.md, **Stops**): a click on or very near the
// label of a city, a town or a village the basemap draws places the stop at that place's point,
// labelled with its name. The labels are the rendered basemap's own, read with MapLibre's
// `queryRenderedFeatures` on the layers that draw them (`CITY_LAYERS`, by the basemap's kind): a
// label drawn under the click (its text or its dot) first, else the nearest whose point lies
// within `CITY_HIT_PX` of the click (in a box of that size around it), for a dot whose text sits
// beside it. A label the map does not draw (beyond its layer's zooms, hidden by another, or on a
// raster basemap or a pmtiles one without labels) is no city.
//
// `cityAt` takes the map's three calls it uses, so the tests stub them (cities.test.ts).
import type { Map as MapLibreMap, PointLike } from "maplibre-gl";
import type { BasemapConfig } from "../api/types";

/** How near a city's point a click must be, in screen pixels. */
export const CITY_HIT_PX = 12;

/** The layers that draw a city's, a town's or a village's label, by the basemap's kind. */
export const CITY_LAYERS: Record<BasemapConfig["kind"], readonly string[]> = {
  raster: [], // no vector features
  // @protomaps/basemaps' `layers()` (package 5.7.2): `kind` locality, cities, towns and villages
  // alike; not places_subplace (neighbourhoods), places_region or places_country.
  pmtiles: ["places_locality"],
  // OpenFreeMap's Positron (https://tiles.openfreemap.org/styles/positron, read 2026-11-01): `class`
  // city (a capital on a layer of its own), town and village; not label_other (suburbs, hamlets,
  // ...), label_state or label_country_1/2/3. Its Dark is a recoloured Positron, taken to have the
  // same ids: unverified offline.
  openfreemap: ["label_city", "label_city_capital", "label_town", "label_village"],
};

/** A city as a stop takes it: its label's point and name. */
export interface City {
  lat: number;
  lon: number;
  name: string;
}

/** What `cityAt` asks of the map. */
export type CityMap = Pick<MapLibreMap, "getLayer" | "queryRenderedFeatures" | "project">;

/** The name a city's label shows, as the layers' text-field picks it: Protomaps' `name:en` (its
 * labels asked for in English), OpenFreeMap's `name_en`, else `name`. */
function nameOf(properties: { [name: string]: unknown }): string | null {
  const name = properties["name:en"] ?? properties.name_en ?? properties.name;
  return typeof name === "string" && name !== "" ? name : null;
}

/** The city among `layers` (`CITY_LAYERS`' for the basemap drawn) whose label is drawn at the
 * screen point (`x`, `y`), else whose point lies within `CITY_HIT_PX` of it; of several, the one
 * whose point is nearest; null when none. */
export function cityAt(
  map: CityMap,
  { x, y }: { x: number; y: number },
  layers: readonly string[],
): City | null {
  // MapLibre fires an error for a layer the style lacks: before the basemap, or another kind's.
  const drawn = layers.filter((id) => map.getLayer(id) !== undefined);
  if (drawn.length === 0) return null;
  const nearest = (query: PointLike | [PointLike, PointLike], withinPx: number): City | null => {
    let city: City | null = null;
    let cityPx = Infinity;
    for (const { geometry, properties } of map.queryRenderedFeatures(query, { layers: drawn })) {
      const name = nameOf(properties);
      if (geometry.type !== "Point" || name === null) continue;
      const [lon, lat] = geometry.coordinates;
      const at = map.project([lon, lat]);
      const px = Math.hypot(at.x - x, at.y - y);
      if (px <= withinPx && px < cityPx) {
        city = { lat, lon, name };
        cityPx = px;
      }
    }
    return city;
  };
  const box: [PointLike, PointLike] = [
    [x - CITY_HIT_PX, y - CITY_HIT_PX],
    [x + CITY_HIT_PX, y + CITY_HIT_PX],
  ];
  return nearest([x, y], Infinity) ?? nearest(box, CITY_HIT_PX);
}
