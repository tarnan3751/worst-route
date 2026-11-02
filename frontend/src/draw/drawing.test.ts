// A route drawn by hand without a DOM: `Drawing` over a real Store with `fetch` and `EventSource`
// stubbed, answered from a drawing recorded on Portland's build (portland-draw.json: Powell's →
// Pioneer Courthouse Square, its first steps, the answer one move before the end, the end, the
// finish and a refusal): the request it makes, the start and the par (aiming at 10×, a round
// trip's the full run), steps, undo, an illegal path cut back, Finish taking the longest move to
// the end, the par's ratio and the verdict, leaving and resuming, the drawing forgotten when the
// stops change or a run starts; and what the panel and the map say (`progressLine`, `youLine`,
// `parLine`, `verdictLine`, `nearestMove`, `moveLabel`).
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { json, settle, stubEventSource, stubFetch, type Call } from "../__fixtures__/http";
import { loadStream } from "../__fixtures__/stream";
import type { DrawAnswer, DrawFinished, DrawMove, RouteRequest } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Settings } from "../state";
import { busy } from "../stops/stopList";
import {
  DRAW_PAR_RATIO,
  Drawing,
  drawRequest,
  finalMove,
  moveLabel,
  nearestMove,
  parLine,
  progressLine,
  verdictLine,
  youLine,
} from "./drawing";

interface DrawFixture {
  request: RouteRequest;
  path: number[];
  steps: DrawAnswer[];
  near_end: DrawAnswer;
  done: DrawAnswer;
  finished: DrawFinished;
  refused: { path: number[]; status: number; body: { code: string; index: number } };
}

const fixture = JSON.parse(
  readFileSync(new URL("../__fixtures__/portland-draw.json", import.meta.url), "utf8"),
) as DrawFixture;
const REGION = loadStream().regions[0];
const [START, NEXT] = fixture.steps;
const DEPARTURE = "2026-10-31T09:00:00-07:00";
const PAR = { job_id: "par1", events_url: "/jobs/par1/events", seed: 7 };

function storeWith(settings: Partial<Settings> = {}): Store {
  return new Store({
    regions: null,
    region: REGION,
    pick: null,
    stops: (fixture.request.stops ?? []).map(({ lat, lon }) => ({ lat, lon, snapped: null })),
    settings: { ...DEFAULT_SETTINGS, budgetS: 4, departure: DEPARTURE, ...settings },
    run: IDLE_RUN,
    route: null,
    error: null,
    brands: null,
    challenge: null,
  });
}

/** The recording's answer for a path: the start, the first step, else the move before the end. */
function recorded(path: number[]): DrawAnswer {
  if (path.length === 0) return START;
  return path.length === 1 ? NEXT : fixture.near_end;
}

type Answer = (path: number[], call: Call) => Response | Promise<Response>;

function setup(draw: Answer = (path) => json(recorded(path))) {
  const store = storeWith();
  const calls = stubFetch((call) => {
    if (call.url === "/api/draw") return draw((call.body as { path: number[] }).path, call);
    if (call.url === "/api/draw/finish") return json(fixture.finished);
    if (call.url === "/api/jobs") return json(PAR, 202);
    return json({ job_id: "par1", status: "done" });
  });
  const sources = stubEventSource();
  const shown: [DrawAnswer | null, boolean][] = [];
  const hooks = { clearRoute: vi.fn(), open: vi.fn(async () => true) };
  const view = {
    show: (answer: DrawAnswer | null, active: boolean) => shown.push([answer, active]),
  };
  return { store, calls, sources, shown, hooks, drawing: new Drawing(store, view, hooks) };
}

const posted = (calls: Call[]) => calls.map(({ method, url }) => `${method} ${url}`);
const metrics = (length_m: number, ratio: number | null) => ({
  length_m,
  time_s: 1,
  delay_s: 0,
  ratio,
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a route drawn by hand", () => {
  it("asks for the longest route with no target, on the stops and the turns set", () => {
    const store = storeWith({ objective: "collect", target: "ratio", seed: 4 });
    store.updateSettings({ turns: "no_left", strict: true, hard: true });
    const stops = store.state.stops;
    store.update({ stops: [stops[0], { ...stops[0], lat: 45.52 }, stops[1]] });
    const request = drawRequest(store.state);
    expect(request).toEqual({
      region: "portland",
      stops: [
        { lat: 45.5231, lon: -122.6765 },
        { lat: 45.52, lon: -122.6765, label: "Stop 2" },
        { lat: 45.5189, lon: -122.6793 },
      ],
      objective: "distance",
      tier: "auto",
      leash_km: "auto",
      budget_s: 4,
      target: { kind: "max" },
      vehicle: { mpg: 24, gas_price_usd: 3.89 },
      departure: DEPARTURE,
      constraints: { no_left: true, no_right: false, strict: true, hard: true },
      seed: 4,
    });
  });

  it("labels a city's stop with its name whatever its role, as Go does", () => {
    const store = storeWith();
    const [start, end] = store.state.stops;
    const via = { lat: 45.52024, lon: -122.674198, snapped: null };
    store.update({ stops: [{ ...start, label: "Portland" }, via, end] });
    expect(drawRequest(store.state).stops).toEqual([
      { lat: 45.5231, lon: -122.6765, label: "Portland" },
      { lat: 45.52024, lon: -122.674198, label: "Stop 2" },
      { lat: 45.5189, lon: -122.6793 },
    ]);
    store.update({ stops: [start, { ...via, label: "Portland" }, { ...end, label: "Gresham" }] });
    expect(drawRequest(store.state).stops).toEqual([
      { lat: 45.5231, lon: -122.6765 },
      { lat: 45.52024, lon: -122.674198, label: "Portland" },
      { lat: 45.5189, lon: -122.6793, label: "Gresham" },
    ]);
  });

  it("starts at the start, locks the run like a job, and plans the par aiming at 10×", async () => {
    const { store, calls, sources, shown, hooks, drawing } = setup();
    await drawing.start();
    expect(posted(calls)).toEqual(["POST /api/draw", "POST /api/jobs"]);
    const request = drawRequest(store.state);
    expect(calls[0].body).toEqual({ request, path: [] });
    expect(calls[1].body).toEqual({ ...request, target: { kind: "ratio", value: 10 } });
    expect(DRAW_PAR_RATIO).toBe(10);
    expect(hooks.clearRoute).toHaveBeenCalledOnce();
    expect(store.state.run.phase).toBe("drawing");
    expect(busy(store)).toBe(true);
    expect(store.state.stops.map((s) => s.snapped?.node)).toEqual(START.stops.map((s) => s.node));
    expect(drawing.answer).toEqual(START);
    expect(shown.at(-1)).toEqual([START, true]);
    expect(sources.map((s) => s.url)).toEqual(["/api/jobs/par1/events"]);
    expect(progressLine(drawing)).toBe("Click a highlighted road to take it.");
    expect(youLine(drawing, "mi")).toBe("you: 0 ft so far · 1 of 2 stops");
    expect(parLine(drawing.par, "mi")).toBe("the engine: aiming for 10×…");
    expect([drawing.canUndo, drawing.canFinish]).toEqual([false, false]);
  });

  it("plans a round trip's par as the full run: the API refuses its ratio target", async () => {
    const { store, calls, sources, drawing } = setup();
    const [start] = store.state.stops;
    store.update({ stops: [start, { ...start }] });
    await drawing.start();
    const request = drawRequest(store.state);
    expect(request.target).toEqual({ kind: "max" });
    expect(calls[1]).toMatchObject({ url: "/api/jobs", body: request });
    expect(drawing.par?.target).toBeNull();
    expect(parLine(drawing.par, "mi")).toBe("the engine: planning…");
    sources[0].send("progress", { metrics: metrics(4000, null), splices: [] });
    expect(parLine(drawing.par, "mi")).toBe("the engine: 2.5 mi so far");
  });

  it("takes offered moves, takes them back without asking, and ignores the rest", async () => {
    const { calls, drawing } = setup();
    await drawing.start();
    await drawing.take(-1); // not offered
    expect(calls).toHaveLength(2);
    const first = fixture.path[0];
    await drawing.take(first);
    expect(calls[2].body).toEqual({ request: drawing.request, path: [first] });
    expect([drawing.path, drawing.answer]).toEqual([[first], NEXT]);
    expect(drawing.canUndo).toBe(true);
    expect(progressLine(drawing)).toBe("Head for the end.");
    drawing.undo();
    expect([drawing.path, drawing.answer, calls.length]).toEqual([[], START, 3]);
  });

  it("cuts a path the server refuses back to its first illegal edge", async () => {
    const refused = { code: "ILLEGAL_PATH", message: "no", refusal: "NODE_REUSED", index: 1 };
    const { store, drawing } = setup((path) =>
      path.length === 3 ? json(refused, 422) : json(recorded(path)),
    );
    await drawing.start();
    await drawing.take(fixture.path[0]);
    await drawing.take(NEXT.next[0].edge);
    expect(drawing.path).toEqual([fixture.path[0], NEXT.next[0].edge]);
    await drawing.take(fixture.near_end.next[0].edge);
    expect(drawing.path).toEqual([fixture.path[0]]);
    expect(drawing.answers).toEqual([START, NEXT]);
    expect(store.state.error).toEqual(refused);
  });

  it("finishes with the longest move to the end and opens the stored route", async () => {
    const { calls, hooks, drawing, shown } = setup();
    await drawing.start();
    await drawing.take(fixture.path[0]);
    await drawing.take(NEXT.next[0].edge);
    expect(drawing.answer).toEqual(fixture.near_end);
    const final = finalMove(fixture.near_end) as DrawMove;
    expect(final.stop).toBe(1);
    expect(progressLine(drawing)).toBe("The end is one move away. Finish takes the longest.");
    expect(drawing.canFinish).toBe(true);
    await drawing.finish();
    const path = [fixture.path[0], NEXT.next[0].edge, final.edge];
    expect(calls.at(-1)).toMatchObject({ url: "/api/draw/finish", body: { path } });
    expect(hooks.open).toHaveBeenCalledWith(fixture.finished.route_id);
    expect(drawing.finished).toEqual({
      routeId: fixture.finished.route_id,
      ratio: fixture.finished.ratio,
      lengthM: fixture.near_end.length_m + final.length_m,
    });
    expect([drawing.active, drawing.canFinish]).toEqual([false, false]);
    expect(shown.at(-1)).toEqual([null, false]); // the stored route is the route layer's
    expect(progressLine(drawing)).toBe("Your route is stored.");
    expect(youLine(drawing, "mi")).toBe("you: 14.3×");
  });

  it("follows the par's ratio and gives the verdict once both are done", async () => {
    const { sources, hooks, drawing } = setup();
    await drawing.start();
    const [source] = sources;
    source.send("snapshot", { metrics: metrics(700, 1.2) });
    source.send("progress", { metrics: metrics(4900, 7.2), splices: [] });
    expect(parLine(drawing.par, "mi")).toBe("the engine: 7.2× so far, aiming for 10×");
    await drawing.take(fixture.path[0]);
    await drawing.take(NEXT.next[0].edge);
    await drawing.finish();
    expect(verdictLine(drawing, "mi")).toBe(""); // the par still plans
    source.send("done", { route_id: "engine1", metrics: metrics(6880, 10.1) });
    expect(parLine(drawing.par, "mi")).toBe("the engine: 10.1×");
    expect(verdictLine(drawing, "mi")).toBe("You 14.3× · the engine 10.1×. You beat the engine.");
    await drawing.show("engine");
    expect(hooks.open).toHaveBeenLastCalledWith("engine1");
    expect(drawing.showing).toBe("engine");
    await drawing.show("mine");
    expect(hooks.open).toHaveBeenLastCalledWith(fixture.finished.route_id);
    drawing.finished = { ...drawing.finished!, lengthM: 4900, ratio: 7.2 };
    expect(verdictLine(drawing, "mi")).toBe("You 7.2× · the engine 10.1×. The engine wins.");
  });

  it("gives the par's ratio where it stopped short of its target", async () => {
    const { sources, drawing } = setup();
    await drawing.start();
    sources[0].send("done", { route_id: "engine1", metrics: metrics(5720, 8.4) });
    expect(parLine(drawing.par, "mi")).toBe("the engine: 8.4×");
  });

  it("says when the par fails", async () => {
    const { sources, drawing } = setup();
    await drawing.start();
    sources[0].send("error", { code: "WORKER_FAILED", message: "the worker died" });
    expect(parLine(drawing.par, "mi")).toBe("the engine could not plan it: the worker died");
    const lost = setup();
    await lost.drawing.start();
    lost.sources[0].readyState = 2;
    lost.sources[0].dispatchEvent(new Event("error")); // the browser gave up reconnecting
    expect(parLine(lost.drawing.par, "mi")).toBe("the engine lost the connection to the server");
  });

  it("leaves drawing mode keeping the drawing, and resumes it without asking again", async () => {
    const { store, calls, shown, drawing } = setup();
    await drawing.start();
    await drawing.take(fixture.path[0]);
    drawing.leave();
    expect([drawing.active, store.state.run.phase, busy(store)]).toEqual([false, "idle", false]);
    expect(shown.at(-1)).toEqual([NEXT, false]); // still drawn, without its moves
    expect(progressLine(drawing)).toBe("Left drawing mode. Draw it picks up where you left off.");
    await drawing.start();
    expect(calls).toHaveLength(3);
    expect([drawing.active, drawing.path, store.state.run.phase]).toEqual([
      true,
      [fixture.path[0]],
      "drawing",
    ]);
  });

  it("forgets the drawing and cancels its par when the stops change or a run starts", async () => {
    const { store, calls, shown, drawing } = setup();
    await drawing.start();
    expect(drawing.request).not.toBeNull(); // the stops' snaps are no change
    drawing.leave();
    const [start, end] = store.state.stops;
    store.update({ stops: [start, { ...end, lat: 45.52 }] }); // a stop moved
    expect(posted(calls).at(-1)).toBe("DELETE /api/jobs/par1");
    expect([drawing.request, drawing.answer, drawing.par]).toEqual([null, null, null]);
    expect(shown.at(-1)).toEqual([null, false]);
    store.update({ stops: [start, end] });
    await drawing.start();
    store.update({ run: { ...IDLE_RUN, phase: "starting" } }); // Go
    expect(drawing.request).toBeNull();
    expect(posted(calls).filter((c) => c === "DELETE /api/jobs/par1")).toHaveLength(2);
  });

  it("forgets a drawing left with Esc when another route is opened over it", async () => {
    const { store, calls, shown, drawing } = setup();
    await drawing.start();
    await drawing.take(fixture.path[0]);
    drawing.leave();
    expect(shown.at(-1)).toEqual([NEXT, false]);
    // what openRoute (a link, today's route's "Open it") does: the route's stops and settings
    const route = loadStream().route;
    store.update({
      stops: (route.request.stops ?? []).map(({ lat, lon }) => ({ lat, lon, snapped: null })),
    });
    store.updateSettings({ tier: route.request.tier ?? "auto", seed: route.seed });
    expect([drawing.request, drawing.par, drawing.path]).toEqual([null, null, []]);
    expect(shown.at(-1)).toEqual([null, false]);
    expect(posted(calls).at(-1)).toBe("DELETE /api/jobs/par1");
  });

  it.each([
    { tier: "T3" },
    { leashKm: 5 },
    { turns: "no_left" },
    { turns: "no_right", strict: true },
    { hard: true },
    { region: "us-t3" },
  ] as Partial<Settings>[])("forgets a drawing left with Esc once %o changes", async (patch) => {
    const { store, calls, drawing } = setup();
    await drawing.start();
    drawing.leave();
    if (patch.region != null) {
      store.update({ region: { ...REGION, region: patch.region } });
    } else {
      store.updateSettings(patch);
    }
    expect(drawing.request).toBeNull();
    expect(posted(calls).at(-1)).toBe("DELETE /api/jobs/par1");
  });

  it("keeps a drawing left with Esc when a setting it does not depend on changes", async () => {
    const { store, calls, drawing } = setup();
    await drawing.start();
    drawing.leave();
    store.updateSettings({ seed: 9, budgetS: 30, units: "km", objective: "collect", mpg: 40 });
    store.updateSettings({ departure: "2026-11-01T08:00:00-07:00", target: "ratio" });
    expect(drawing.request).not.toBeNull();
    expect(posted(calls)).not.toContain("DELETE /api/jobs/par1");
    await drawing.start();
    expect(drawing.active).toBe(true);
  });

  it("drops an answer for a drawing since forgotten", async () => {
    let answer: (response: Response) => void = () => undefined;
    const { drawing } = setup(() => new Promise<Response>((resolve) => (answer = resolve)));
    const started = drawing.start();
    await settle();
    drawing.discard();
    answer(json(START));
    await started;
    expect([drawing.answer, drawing.par]).toEqual([null, null]);
  });

  it("gives the run back when the server refuses the start", async () => {
    const unreachable = { code: "STOP_UNREACHABLE_WITHOUT_REPEAT", message: "no", stop_index: 1 };
    const { store, calls, drawing } = setup(() => json(unreachable, 422));
    await drawing.start();
    expect(posted(calls)).toEqual(["POST /api/draw"]); // no par
    expect([store.state.run.phase, store.state.error, drawing.request]).toEqual([
      "idle",
      unreachable,
      null,
    ]);
  });
});

describe("what the panel and the map say", () => {
  const move = (edge: number, coords: [number, number][], extra: Partial<DrawMove> = {}) =>
    ({
      edge,
      node: edge,
      lat: 0,
      lon: 0,
      name: null,
      length_m: 100,
      turn: null,
      stop: null,
      coords,
      ...extra,
    }) as DrawMove;

  it("finds the move nearest a click, within a few pixels", () => {
    const east = move(1, [
      [0, 0],
      [100, 0],
    ]);
    const north = move(2, [
      [0, 0],
      [0, 100],
    ]);
    const project = ([x, y]: [number, number]) => ({ x, y });
    expect(nearestMove({ x: 60, y: 5 }, [east, north], project)?.edge).toBe(1);
    expect(nearestMove({ x: 3, y: 50 }, [east, north], project)?.edge).toBe(2);
    expect(nearestMove({ x: 60, y: 30 }, [east, north], project)).toBeNull();
    expect(nearestMove({ x: 0, y: 0 }, [east, north], project)?.edge).toBe(1); // a tie: the first
    expect(nearestMove({ x: 120, y: 0 }, [east], project, 25)?.edge).toBe(1);
  });

  it("names a move by its road, its length and its turn", () => {
    expect(moveLabel(NEXT.next[0], "mi")).toBe("Northwest 6th Avenue · 260 ft · straight");
    expect(moveLabel({ name: null, length_m: 1609.344, turn: "u_turn" }, "km")).toBe(
      "unnamed road · 1.6 km · U-turn",
    );
  });

  it("takes the longest of the moves to the end", () => {
    const end = (edge: number, length_m: number) => move(edge, [], { stop: 1, length_m });
    const answer = { ...START, next: [end(1, 50), move(2, []), end(3, 80), end(4, 80)] };
    expect(finalMove(answer)?.edge).toBe(3);
    expect(finalMove({ ...START, next: [move(2, [])] })).toBeNull();
  });

  it("says when the drawing has reached the end, or nowhere to go", () => {
    const hooks = { clearRoute: () => undefined, open: async () => true };
    const drawing = new Drawing(storeWith(), { show: () => undefined }, hooks);
    Object.assign(drawing, { answers: [fixture.done], active: true, path: [1] });
    expect(progressLine(drawing)).toBe("Arrived. Finish stores your route.");
    expect(youLine(drawing, "mi")).toBe("you: 14.3×");
    Object.assign(drawing, { answers: [{ ...NEXT, next: [] }] });
    expect(progressLine(drawing)).toBe("Dead end. Undo (Backspace) and try another way.");
  });

  it("measures a round trip by its length", () => {
    expect(
      parLine(
        {
          phase: "done",
          target: null,
          jobId: null,
          ratio: null,
          lengthM: 16093.44,
          routeId: "r",
          note: null,
        },
        "mi",
      ),
    ).toBe("the engine: 10.0 mi");
  });
});
