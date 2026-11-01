// A route drawn by hand on the map (docs/reference/frontend.md, **Drawing**): the line drawn so
// far (`wr-drawn`, violet over a white casing) and, in drawing mode, the node it is at (`wr-at`)
// and the legal next moves (`wr-choices`: each move's whole edge, green and dashed, a dot where it
// leads), over the route's layers. Hovering a move names its road in a popup (text, never HTML),
// its length and the turn into it; `pick` is the move a click at a point takes, the nearest within
// a few pixels (draw/drawing.ts, `nearestMove`). The map follows the drawing when its end nears
// the map's edge.
import { Popup, type GeoJSONSource, type Map as MapLibreMap } from "maplibre-gl";
import type { DrawAnswer, DrawMove, LonLat } from "../api/types";
import { moveLabel, nearestMove, type DrawView } from "../draw/drawing";
import type { Store } from "../state";

const DRAWN = "wr-drawn";
const CHOICES = "wr-choices";
const ENDS = "wr-choices-ends";
const AT = "wr-at";
const HIT = "wr-choices-hit"; // a wide transparent line, so hovering needs no precision
const PICK_PX = 8;
const EDGE_PX = 60; // the drawing's end this near the map's edge brings it back into view
const EMPTY = { type: "FeatureCollection" as const, features: [] };

export class DrawLayer implements DrawView {
  readonly #map: MapLibreMap;
  readonly #popup = new Popup({ closeButton: false, closeOnClick: false, offset: 10 });
  #moves: DrawMove[] = [];

  /** Adds the sources and layers, over the route's; the map's style must be loaded. */
  constructor(map: MapLibreMap, store: Store) {
    this.#map = map;
    for (const id of [DRAWN, CHOICES, AT]) map.addSource(id, { type: "geojson", data: EMPTY });
    const round = { "line-join": "round", "line-cap": "round" } as const;
    map.addLayer({
      id: `${DRAWN}-casing`,
      type: "line",
      source: DRAWN,
      layout: round,
      paint: { "line-color": "#ffffff", "line-width": 8, "line-opacity": 0.9 },
    });
    map.addLayer({
      id: DRAWN,
      type: "line",
      source: DRAWN,
      layout: round,
      paint: { "line-color": "#7c3aed", "line-width": 5 },
    });
    map.addLayer({
      id: HIT,
      type: "line",
      source: CHOICES,
      filter: ["==", ["geometry-type"], "LineString"],
      paint: { "line-color": "#000000", "line-width": 18, "line-opacity": 0 },
    });
    map.addLayer({
      id: CHOICES,
      type: "line",
      source: CHOICES,
      filter: ["==", ["geometry-type"], "LineString"],
      layout: { "line-cap": "round" },
      paint: { "line-color": "#059669", "line-width": 5, "line-dasharray": [1.5, 1] },
    });
    map.addLayer({
      id: ENDS,
      type: "circle",
      source: CHOICES,
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-radius": 4,
        "circle-color": "#059669",
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.5,
      },
    });
    map.addLayer({
      id: AT,
      type: "circle",
      source: AT,
      paint: {
        "circle-radius": 7,
        "circle-color": "#7c3aed",
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 2,
      },
    });
    map.on("mousemove", HIT, (event) => {
      const edge = event.features?.[0]?.properties?.edge;
      const move = this.#moves.find((m) => m.edge === edge);
      if (move === undefined) return;
      map.getCanvas().style.cursor = "pointer";
      this.#popup
        .setLngLat(event.lngLat)
        .setText(moveLabel(move, store.state.settings.units))
        .addTo(map);
    });
    map.on("mouseleave", HIT, () => {
      map.getCanvas().style.cursor = "";
      this.#popup.remove();
    });
  }

  show(answer: DrawAnswer | null, active: boolean): void {
    this.#moves = active && answer !== null ? answer.next : [];
    const drawn = answer?.geometry ? [line(answer.geometry.coordinates, {})] : [];
    this.#source(DRAWN).setData({ type: "FeatureCollection", features: drawn });
    const at = active && answer !== null ? [point([answer.at.lon, answer.at.lat], {})] : [];
    this.#source(AT).setData({ type: "FeatureCollection", features: at });
    const choices = this.#moves.flatMap((move) => [
      line(move.coords, { edge: move.edge }),
      point([move.lon, move.lat], { edge: move.edge }),
    ]);
    this.#source(CHOICES).setData({ type: "FeatureCollection", features: choices });
    if (this.#moves.length === 0) this.#popup.remove();
    if (active && answer !== null) this.#follow([answer.at.lon, answer.at.lat]);
  }

  /** The move a click at `point` (in pixels) takes, or null. */
  pick(at: { x: number; y: number }): DrawMove | null {
    return nearestMove(at, this.#moves, (lonLat) => this.#map.project(lonLat), PICK_PX);
  }

  /** Bring `at` back into view when the drawing's end nears the map's edge. */
  #follow(at: LonLat): void {
    const { x, y } = this.#map.project(at);
    const { clientWidth: width, clientHeight: height } = this.#map.getContainer();
    const out = x < EDGE_PX || y < EDGE_PX || x > width - EDGE_PX || y > height - EDGE_PX;
    if (out) this.#map.easeTo({ center: at });
  }

  #source(id: string): GeoJSONSource {
    return this.#map.getSource(id) as GeoJSONSource;
  }
}

function line(coordinates: LonLat[], properties: Record<string, number>) {
  return {
    type: "Feature" as const,
    properties,
    geometry: { type: "LineString" as const, coordinates },
  };
}

function point(coordinates: LonLat, properties: Record<string, number>) {
  return {
    type: "Feature" as const,
    properties,
    geometry: { type: "Point" as const, coordinates },
  };
}
