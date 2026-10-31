import { describe, expect, it, vi } from "vitest";
import { loadStream } from "../__fixtures__/stream";
import type { DirectionStep, LineString, LonLat, RouteDocument, StepKind } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
import { DirectionsPanel, StepLocator, stepRows, type DirectionsView } from "./panel";

const fixture = loadStream("portland-directions-stream");
const { route, status } = fixture;
const steps = route.directions!;

// Where each route node lies: the job's last snapshot is the stored route, every node a vertex
// at its original coordinates (docs/reference/api.md, **Applying the stream**).
const nodes = status.snapshot!.node_index.map((k) => status.snapshot!.geometry.coordinates[k]);

/** Metres between two nearby points. */
function metres([lon1, lat1]: LonLat, [lon2, lat2]: LonLat): number {
  const lat = (((lat1 + lat2) / 2) * Math.PI) / 180;
  return Math.hypot((lon2 - lon1) * 111320 * Math.cos(lat), (lat2 - lat1) * 110540);
}

function store(): Store {
  return new Store({
    region: fixture.regions[0],
    stops: [],
    settings: DEFAULT_SETTINGS,
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
    regions: null,
    pick: null,
  });
}

function fakeView() {
  return { highlight: vi.fn(), panTo: vi.fn() } satisfies DirectionsView;
}

const driving = steps.flatMap((step, k) => (step.edge_range[1] > step.edge_range[0] ? [k] : []));

describe("the list's rows", () => {
  it("has a row per step, classed by its kind, numbered as the document numbers them", () => {
    const rows = stepRows(steps, "mi");
    expect(rows).toHaveLength(steps.length);
    expect(rows.map((row) => row.className)).toEqual(steps.map((s) => `step step-${s.kind}`));
    expect(rows.map((row) => row.number)).toEqual(
      steps.map((s) => (s.kind === "joke" ? null : s.step)),
    );
    expect(rows.filter((row) => row.number === null)).toHaveLength(40); // the jokes
    expect(rows[11]).toMatchObject({
      className: "step step-joke",
      number: null,
      text: "Keep calm. Keep driving. Keep not arriving.",
      detail: "",
    });
    expect(rows[12].number).toBe(13); // the joke's number is not given to the next step
  });

  it("gives a driving step its distance and time, in the units shown", () => {
    const [first, second] = stepRows(steps, "mi");
    expect([first.text, first.detail]).toEqual([
      "Head east on West Burnside Street",
      "312 ft · 11 s",
    ]);
    expect(`${second.text} · ${second.detail}`).toBe(
      "Turn right onto Southwest 5th Avenue · 0.2 mi · 3 min",
    );
    expect(stepRows(steps, "km")[1].detail).toBe("284 m · 3 min");
    expect(stepRows(steps, "mi").at(-1)).toMatchObject({
      className: "step step-arrive",
      number: 586,
      text: "Arrive at your destination",
      detail: "",
    });
  });

  it("makes a stop between the ends a step of its own, with no distance", () => {
    const stop: DirectionStep = {
      step: 9,
      text: "Arrive at stop 1; continue",
      distance_m: 0,
      time_s: 0,
      edge_range: [12, 12],
      kind: "stop",
    };
    expect(stepRows([stop], "mi")).toEqual([
      { kind: "stop", className: "step step-stop", number: 9, text: stop.text, detail: "" },
    ]);
  });
});

describe("where the steps lie on the map", () => {
  const locator = new StepLocator(route.geometry, steps);

  it("draws each driving step from its first node to its last", () => {
    const ends = driving.flatMap((k) => {
      const [a, b] = steps[k].edge_range;
      const line = locator.line(k)!;
      return [
        [line[0], nodes[a]],
        [line.at(-1)!, nodes[b]],
      ];
    });
    const exact = ends.filter(([drawn, node]) => drawn.join() === node.join());
    expect(exact.length / ends.length).toBeGreaterThan(0.99); // 1,086 of 1,090
    expect(Math.max(...ends.map(([drawn, node]) => metres(drawn, node)))).toBeLessThan(1);
  });

  it("covers the geometry with the driving steps, end to end", () => {
    const lines = driving.map((k) => locator.line(k)!);
    const joined = [lines[0][0], ...lines.flatMap((line) => line.slice(1))];
    expect(joined).toEqual(route.geometry.coordinates);
  });

  it("gives a step at least one segment of the line, however short", () => {
    const line: LineString = {
      type: "LineString",
      coordinates: [
        [-122.6, 45.5],
        [-122.599, 45.5],
        [-122.598, 45.5],
        [-122.597, 45.5],
      ],
    };
    const step = (step: number, kind: StepKind, a: number, b: number, m: number) => ({
      step,
      text: kind,
      distance_m: m,
      time_s: m / 10,
      edge_range: [a, b] as [number, number],
      kind,
    });
    const three = new StepLocator(line, [
      step(1, "depart", 0, 1, 78), // one segment each, the second much shorter than measured
      step(2, "joke", 1, 1, 0),
      step(3, "turn", 1, 2, 1),
      step(4, "turn", 2, 3, 78),
      step(5, "arrive", 3, 3, 0),
    ]);
    expect([0, 1, 2, 3, 4].map((k) => three.line(k))).toEqual([
      line.coordinates.slice(0, 2),
      null,
      line.coordinates.slice(1, 3),
      line.coordinates.slice(2, 4),
      null,
    ]);
  });

  it("draws nothing for a step that drives nothing, and starts every step where it is", () => {
    for (const [k, step] of steps.entries()) {
      if (!driving.includes(k)) expect(locator.line(k)).toBeNull();
      expect(metres(locator.start(k), nodes[step.edge_range[0]])).toBeLessThan(1);
    }
  });
});

describe("the panel", () => {
  it("lists the route shown, and nothing while none is", () => {
    const s = store();
    const panel = new DirectionsPanel(s, fakeView());
    expect(panel.rows).toEqual([]);
    s.update({ route });
    expect(panel.rows).toHaveLength(steps.length);
    s.updateSettings({ units: "km" });
    expect(panel.rows[1].detail).toBe("284 m · 3 min");
    s.update({ route: null });
    expect(panel.rows).toEqual([]);
  });

  it("highlights the edges of the step hovered, and nothing for a joke or none", () => {
    const s = store();
    const view = fakeView();
    const panel = new DirectionsPanel(s, view);
    s.update({ route });
    view.highlight.mockClear();
    panel.hover(1); // Turn right onto Southwest 5th Avenue, edges [1, 5)
    const line: LonLat[] = view.highlight.mock.lastCall![0];
    expect(line[0]).toEqual(nodes[1]);
    expect(line.at(-1)).toEqual(nodes[5]);
    panel.hover(1); // the mouse moving over the same step
    expect(view.highlight).toHaveBeenCalledTimes(1);
    panel.hover(11); // a joke
    expect(view.highlight).toHaveBeenLastCalledWith(null);
    panel.hover(2);
    panel.hover(null);
    expect(view.highlight).toHaveBeenLastCalledWith(null);
    expect(view.highlight).toHaveBeenCalledTimes(4);
  });

  it("pans to the step clicked", () => {
    const s = store();
    const view = fakeView();
    const panel = new DirectionsPanel(s, view);
    panel.select(1); // no route
    expect(view.panTo).not.toHaveBeenCalled();
    s.update({ route });
    panel.select(steps.length - 1); // the arrival
    expect(view.panTo.mock.lastCall![0]).toEqual(nodes.at(-1));
  });

  it("forgets the highlight when the route changes", () => {
    const s = store();
    const view = fakeView();
    const panel = new DirectionsPanel(s, view);
    s.update({ route });
    panel.hover(1);
    s.update({ route: null });
    expect(view.highlight).toHaveBeenLastCalledWith(null);
    const old: RouteDocument = { ...route, directions: undefined }; // a server older than them
    s.update({ route: old });
    expect(panel.rows).toEqual([]);
    panel.hover(1);
    expect(view.highlight).toHaveBeenLastCalledWith(null);
  });
});
