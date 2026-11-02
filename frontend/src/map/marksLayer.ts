// The route's marks on the map (docs/reference/frontend.md, **Live growth**): the brand locations
// a route visits and the items it collects, from the route document's `marks`, small circles over
// the route in the theme's start colour ringed in its casing colour (`paintMarks`, at creation and
// again when the theme changes), each named in a popup on hover (text, never HTML): its label, or
// the item in words ("toll booth"). They follow the route the store shows, so they go with it (an
// edit of the stops, a run starting, a drawing).
import { Popup, type GeoJSONSource, type Map as MapLibreMap } from "maplibre-gl";
import type { Mark, RouteDocument } from "../api/types";
import { ITEM_NAMES } from "../format";
import type { Store } from "../state";
import type { Theme } from "../theme/theme";
import type { PaintName } from "./routeLayer";

const MARKS = "wr-marks";

/** What a mark's popup says: its label (a brand's name), else the item in words. */
export function markName(mark: Mark): string {
  return mark.label ?? (mark.kind === "brand" ? "brand" : ITEM_NAMES[mark.kind][0]);
}

/** The marks of `route` (none without one) as points, each with its name. */
export function marksOf(route: RouteDocument | null) {
  return {
    type: "FeatureCollection" as const,
    features: (route?.marks ?? []).map((mark) => ({
      type: "Feature" as const,
      properties: { name: markName(mark) },
      geometry: { type: "Point" as const, coordinates: [mark.lon, mark.lat] },
    })),
  };
}

/** Colour the marks as `theme` says: its start colour, ringed in its casing colour. */
export function paintMarks(map: Pick<MapLibreMap, "setPaintProperty">, theme: Theme): void {
  const { start, routeCasing } = theme.colors;
  const paint: [PaintName, string][] = [
    ["circle-color", start],
    ["circle-stroke-color", routeCasing],
  ];
  for (const [name, value] of paint) map.setPaintProperty(MARKS, name, value);
}

export class MarksLayer {
  readonly #popup = new Popup({ closeButton: false, closeOnClick: false, offset: 8 });

  /** Adds the source and the layer over the route's, painted as `theme` says, and draws the marks
   * of the route `store` shows; the map's style must be loaded. */
  constructor(map: MapLibreMap, store: Store, theme: Theme) {
    map.addSource(MARKS, { type: "geojson", data: marksOf(null) });
    map.addLayer({
      id: MARKS,
      type: "circle",
      source: MARKS,
      paint: { "circle-radius": 5, "circle-stroke-width": 2 },
    });
    paintMarks(map, theme);
    map.on("mousemove", MARKS, (event) => {
      const name = event.features?.[0]?.properties?.name;
      if (typeof name === "string") this.#popup.setLngLat(event.lngLat).setText(name).addTo(map);
    });
    map.on("mouseleave", MARKS, () => this.#popup.remove());
    let shown: RouteDocument | null | undefined;
    store.watch(["route"], ({ route }) => {
      if (route === shown) return;
      shown = route;
      this.#popup.remove();
      (map.getSource(MARKS) as GeoJSONSource).setData(marksOf(route));
    });
  }
}
