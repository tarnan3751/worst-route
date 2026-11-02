// The route's marks: drawn from the document the store shows (a brand's by its name, an item's in
// words), gone with the route and replaced by the next route's, named on hover, and painted from
// the theme, as the route's are (routeLayer.test.ts).
import type { Map as MapLibreMap } from "maplibre-gl";
import { describe, expect, it, vi } from "vitest";
import { loadStream } from "../__fixtures__/stream";
import type { Mark, RouteDocument } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
import { classic } from "../theme/presets/classic";
import { night } from "../theme/presets/night";
import type { Theme } from "../theme/theme";
import { MarksLayer, markName, marksOf, paintMarks } from "./marksLayer";

// The popup a hover opens, as text: what it was last given, and whether it is open.
const popup = vi.hoisted(() => ({ text: null as string | null, open: false }));
vi.mock("maplibre-gl", () => ({
  Popup: class {
    setLngLat() {
      return this;
    }
    setText(text: string) {
      popup.text = text;
      return this;
    }
    addTo() {
      popup.open = true;
      return this;
    }
    remove() {
      popup.open = false;
      return this;
    }
  },
}));

const BRAND = loadStream("portland-brand-stream").route;
const MARKS: Mark[] = [
  { kind: "brand", lat: 45.5231, lon: -122.6765, label: "Starbucks" },
  { kind: "brand", lat: 45.5122, lon: -122.6587, label: "Starbucks" },
];
const ITEMS: Mark[] = [
  { kind: "toll_booth", lat: 45.6, lon: -122.7, label: null },
  { kind: "level_crossing", lat: 45.61, lon: -122.71, label: null },
];

type Data = ReturnType<typeof marksOf>;

/** A stand-in map that keeps what the marks' source is given and its paint, and the layer's
 * hover listeners; and a store whose route it shows. */
function setup(theme: Theme = classic) {
  const data: Data[] = [];
  const paint: Record<string, unknown> = {};
  const listeners: Record<string, (event: unknown) => void> = {};
  const map = {
    addSource: (_id: string, source: { data: Data }) => data.push(source.data),
    addLayer: vi.fn(),
    setPaintProperty: (id: string, name: string, value: unknown) => {
      paint[`${id} ${name}`] = value;
    },
    getSource: () => ({ setData: (next: Data) => data.push(next) }),
    on: (type: string, _layer: string, listener: (event: unknown) => void) => {
      listeners[type] = listener;
    },
  } as unknown as MapLibreMap;
  const store = new Store({
    regions: null,
    region: null,
    pick: null,
    stops: [],
    settings: DEFAULT_SETTINGS,
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
    challenge: null,
  });
  new MarksLayer(map, store, theme);
  const shown = () => data.at(-1)!.features;
  return { map, store, data, paint, listeners, shown };
}

const withMarks = (route: RouteDocument, marks: Mark[] | undefined): RouteDocument => ({
  ...route,
  marks,
});

describe("the route's marks", () => {
  it("are the shown document's, each at its place with its name", () => {
    const { store, shown } = setup();
    expect(shown()).toEqual([]);
    store.update({ route: withMarks(BRAND, [...MARKS, ...ITEMS]) });
    expect(shown().map((f) => f.geometry.coordinates)).toEqual([
      [-122.6765, 45.5231],
      [-122.6587, 45.5122],
      [-122.7, 45.6],
      [-122.71, 45.61],
    ]);
    expect(shown().map((f) => f.properties.name)).toEqual([
      "Starbucks",
      "Starbucks",
      "toll booth",
      "level crossing",
    ]);
  });

  it("go with the route, and a new route shows its own or none", () => {
    const { store, shown, data } = setup();
    store.update({ route: withMarks(BRAND, MARKS) });
    expect(shown()).toHaveLength(2);
    store.update({ route: null }); // a run starting, an edit of the stops, a drawing
    expect(shown()).toEqual([]);
    store.update({ route: withMarks(BRAND, ITEMS) });
    expect(shown().map((f) => f.properties.name)).toEqual(["toll booth", "level crossing"]);
    store.update({ route: withMarks(BRAND, []) }); // another objective's
    expect(shown()).toEqual([]);
    store.update({ route: withMarks(BRAND, MARKS) });
    store.update({ route: withMarks(BRAND, undefined) }); // a server older than them
    expect(shown()).toEqual([]);
    const drawn = data.length;
    store.update({ error: null }); // nothing of the route changed
    expect(data).toHaveLength(drawn);
  });

  it("name themselves on hover, and the popup closes when the pointer leaves", () => {
    const { listeners } = setup();
    const at = { lngLat: { lng: -122.7, lat: 45.6 } };
    listeners.mousemove({ ...at, features: [{ properties: { name: "toll booth" } }] });
    expect(popup).toEqual({ text: "toll booth", open: true });
    listeners.mouseleave({});
    expect(popup.open).toBe(false);
  });

  it("are named by their label, else by the item in words", () => {
    expect(markName(MARKS[0])).toBe("Starbucks");
    expect(markName({ kind: "bridge", lat: 0, lon: 0, label: null })).toBe("bridge");
    expect(markName({ kind: "roundabout", lat: 0, lon: 0, label: null })).toBe("roundabout");
    expect(markName({ kind: "brand", lat: 0, lon: 0, label: null })).toBe("brand");
  });

  it("are painted in the theme's start colour, ringed in its casing colour", () => {
    const { paint } = setup(classic);
    expect(paint).toEqual({
      "wr-marks circle-color": "#1a9850",
      "wr-marks circle-stroke-color": "#ffffff",
    });
    const repainted: Record<string, unknown> = {};
    const map = {
      setPaintProperty: (id: string, name: string, value: unknown) => {
        repainted[`${id} ${name}`] = value;
      },
    } as unknown as MapLibreMap;
    paintMarks(map, night); // the theme changed
    expect(repainted).toEqual({
      "wr-marks circle-color": night.colors.start,
      "wr-marks circle-stroke-color": night.colors.routeCasing,
    });
    expect(night.colors.start).not.toBe(classic.colors.start);
  });
});
