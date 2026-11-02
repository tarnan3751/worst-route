import { describe, expect, it } from "vitest";
import type { Stop } from "../state";
import {
  stopErrored,
  stopLabel,
  visitLine,
  withStopAdded,
  withStopMoved,
  withStopRemoved,
} from "./stopList";

describe("the stops an error names", () => {
  const named = (error: { [field: string]: unknown } | null, count: number) =>
    Array.from({ length: count }, (_, k) => k).filter((k) => stopErrored(error, k, count));

  it("are the stop of its stop_index, or every stop along the way on field stops", () => {
    expect(named({ code: "STOP_OUTSIDE_REGION", message: "", stop_index: 1 }, 3)).toEqual([1]);
    expect(named({ code: "NO_NODE_NEAR", message: "", stop_index: null }, 3)).toEqual([]);
    // The alphabet route takes a start and an end only: its refusal names the stops along the way.
    const vias = { code: "INVALID_REQUEST", message: "", field: "stops" };
    expect(named(vias, 4)).toEqual([1, 2]);
    expect(named(vias, 2)).toEqual([]);
    expect(named({ code: "INVALID_REQUEST", message: "", field: "target" }, 4)).toEqual([]);
    expect(named(null, 3)).toEqual([]);
  });
});

describe("the place a stop is visited in", () => {
  const snapped = { node: 1, lat: 45.5, lon: -122.6, moved_m: 0, note: null };
  const stop: Stop = { lat: 45.5, lon: -122.6, snapped };

  it("is said when the route visits the stops in another order than the list's", () => {
    const order = [0, 1, 3, 2, 4]; // the list's index of each stop visited
    const lines = [0, 1, 2, 3, 4].map((k) => visitLine(stop, k, order));
    expect(lines).toEqual([
      "visited 1st",
      "visited 2nd",
      "visited 4th",
      "visited 3rd",
      "visited 5th",
    ]);
  });

  it("is not said in the list's own order, or for a stop moved since", () => {
    expect(visitLine(stop, 2, null)).toBeNull();
    expect(visitLine({ ...stop, snapped: null }, 2, [0, 2, 1, 3])).toBeNull();
  });
});

describe("a city's stop", () => {
  const snapped = { node: 1, lat: 45.5, lon: -122.6, moved_m: 0, note: null };
  const start: Stop = { lat: 45.5231, lon: -122.6765, snapped };
  const end: Stop = { lat: 45.4817, lon: -122.6263, snapped };

  it("keeps its city's name, through the edits of the other stops", () => {
    const stops = withStopAdded([start, end], 45.523064, -122.676483, "Portland");
    expect(stops[1]).toEqual({
      lat: 45.523064,
      lon: -122.676483,
      label: "Portland",
      snapped: null,
    });
    expect(withStopAdded([start, end], 45.52, -122.67)[1]).toEqual({
      lat: 45.52,
      lon: -122.67,
      snapped: null,
    });
    expect(withStopMoved(stops, 0, 45.51, -122.66)[1].label).toBe("Portland");
    expect(withStopRemoved(stops, 2)[1].label).toBe("Portland");
  });

  it("is no longer its city once dragged", () => {
    const stops = withStopAdded([start], 45.523064, -122.676483, "Portland");
    expect(withStopMoved(stops, 1, 45.53, -122.68)).toEqual([
      { ...start, snapped: null },
      { lat: 45.53, lon: -122.68, snapped: null },
    ]);
  });

  it("is labelled with its city's name whatever its role, the others as before", () => {
    const city: Stop = { ...start, label: "Portland" };
    expect([0, 1, 2].map((k) => stopLabel(city, k, 3))).toEqual([
      "Portland",
      "Portland",
      "Portland",
    ]);
    expect([0, 1, 2].map((k) => stopLabel(start, k, 3))).toEqual([null, "Stop 2", null]);
  });
});
