import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fakeView,
  json,
  settle,
  stubEventSource,
  stubFetch,
  type Call,
} from "../__fixtures__/http";
import { loadStream } from "../__fixtures__/stream";
import type { RouteDocument, RouteRequest, StopReason } from "../api/types";
import { isoWithOffset } from "../format";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Settings } from "../state";
import { busy, visitLine } from "../stops/stopList";
import {
  JobRunner,
  awaitingBrand,
  countsTitle,
  routeLines,
  statLines,
  worseRefused,
} from "./controls";

const fixture = loadStream();

function store(route: RouteDocument | null = null): Store {
  return new Store({
    region: fixture.regions[0],
    stops: [
      { lat: 45.5231, lon: -122.6765, snapped: null },
      { lat: 45.4817, lon: -122.6263, snapped: null },
    ],
    settings: { ...DEFAULT_SETTINGS, seed: route?.seed ?? null },
    run: IDLE_RUN,
    route,
    error: null,
    brands: null,
    challenge: null,
    regions: null,
    pick: null,
  });
}

/** The 202 for a POST of `call`, with its seed (or 77). */
const created = (call: Call, id: string): Response => {
  const seed = (call.body as { seed?: number }).seed ?? 77;
  return json({ job_id: id, events_url: `/jobs/${id}/events`, seed }, 202);
};

let sources: ReturnType<typeof stubEventSource>;
beforeEach(() => {
  sources = stubEventSource();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("errors end the run with the controls unlocked", () => {
  it("shows a TypeError from the client as CLIENT_ERROR and gives the controls back", async () => {
    stubFetch(
      () =>
        ({
          ok: true,
          status: 202,
          text: () => {
            throw new TypeError("body stream already read");
          },
        }) as unknown as Response,
    );
    const s = store();
    await new JobRunner(s, fakeView(), () => {}).go();
    expect(s.state.error).toEqual({ code: "CLIENT_ERROR", message: "body stream already read" });
    expect(s.state.run.phase).toBe("idle");
    expect(busy(s)).toBe(false);
    expect(sources).toHaveLength(0);
  });

  it("ends a job whose events the app cannot handle, cancelling it", async () => {
    const calls = stubFetch((call) => (call.method === "POST" ? created(call, "j1") : json({})));
    const s = store();
    await new JobRunner(s, fakeView(), () => {}).go();
    expect(s.state.run.phase).toBe("running");
    sources[0].send("progress", { iteration: 1, elapsed_s: 0.1, score: 1, metrics: {} });
    await settle();
    expect(s.state.error?.code).toBe("CLIENT_ERROR");
    expect(busy(s)).toBe(false);
    expect(sources[0].readyState).toBe(EventSource.CLOSED);
    expect(calls.at(-1)).toMatchObject({ method: "DELETE", url: "/api/jobs/j1" });
  });

  it("shows the server's error event and closes the stream", async () => {
    stubFetch((call) => created(call, "j2"));
    const s = store();
    await new JobRunner(s, fakeView(), () => {}).go();
    sources[0].send("error", { code: "WORKER_FAILED", message: "the worker died" });
    expect(s.state.error).toEqual({ code: "WORKER_FAILED", message: "the worker died" });
    expect(busy(s)).toBe(false);
    expect(sources[0].readyState).toBe(EventSource.CLOSED);
  });
});

describe("the best ratio so far", () => {
  const metrics = (ratio: number) => ({ length_m: ratio * 1000, time_s: ratio * 60, ratio });
  const progress = (ratio: number, best?: number) => ({
    iteration: 7,
    elapsed_s: 0.5,
    score: ratio * 1000,
    metrics: metrics(ratio),
    splices: [],
    ...(best === undefined ? {} : { best_score: best * 1000, best_metrics: metrics(best) }),
  });

  it("shows beside the current ratio while an annealing run's best scores above it", async () => {
    stubFetch((call) => created(call, "jb"));
    const s = store();
    await new JobRunner(s, fakeView(), () => {}).go();
    const running = (rest: string) => `Try 7 · 0.5 s · ${rest}seed 77`;
    sources[0].send("progress", progress(12.3, 14.1));
    expect(statLines(s.state)[0]).toBe("12.3×");
    expect(statLines(s.state)[3]).toBe(running("best 14.1× · "));
    s.updateRun({ connection: "reconnecting" }); // a dropped stream, which the browser retries
    expect(statLines(s.state)[3]).toBe(running("best 14.1× · reconnecting… · "));
    s.updateRun({ connection: "open" });
    sources[0].send("progress", progress(14.1, 14.1)); // the current route is the best
    expect(statLines(s.state)[3]).toBe(running(""));
    // a best that scores higher with a ratio that rounds alike, and one with a lower ratio (a
    // faster but longer route under the slowest time): the best's own ratio, either way
    sources[0].send("progress", { ...progress(14.1, 14.1), score: 14_090 });
    expect(statLines(s.state)[3]).toBe(running("best 14.1× · "));
    sources[0].send("progress", { ...progress(15.0, 14.8), score: 900, best_score: 950 });
    expect(statLines(s.state)[0]).toBe("15.0×");
    expect(statLines(s.state)[3]).toBe(running("best 14.8× · "));
    sources[0].send("progress", progress(15.0)); // a stream without the best route's numbers
    expect(statLines(s.state)[3]).toBe(running(""));
  });
});

describe("make it worse", () => {
  it("keeps the route and its seed when nothing worse is found, and tries the next seed", async () => {
    const base = fixture.route; // seed 1
    const worse = { ...base, route_id: "0123456789ab", seed: 3 };
    const calls = stubFetch((call) =>
      call.method === "POST" ? created(call, `j${calls.length}`) : json(worse),
    );
    const s = store(base);
    const runner = new JobRunner(s, fakeView(), () => {});

    await runner.worse();
    expect(calls[0].body).toMatchObject({ base_route_id: base.route_id, seed: 2 });
    expect(s.state.settings.seed).toBe(1); // not advanced while it runs
    sources[0].send("done", { route_id: base.route_id, stopped_because: "converged", metrics: {} });
    await settle();
    expect(s.state.route).toBe(base);
    expect(s.state.settings.seed).toBe(1);
    expect(s.state.run.seed).toBe(1);
    expect(statLines(s.state)[3]).toMatch(/ s · seed 1 · seed 2 found nothing worse$/);
    expect(calls).toHaveLength(1); // the base route is not fetched again

    await runner.worse();
    expect(calls[1].body).toMatchObject({ base_route_id: base.route_id, seed: 3 });
    sources[1].send("done", {
      route_id: worse.route_id,
      stopped_because: "converged",
      metrics: {},
    });
    await settle();
    expect(calls[2]).toMatchObject({ method: "GET", url: `/api/routes/${worse.route_id}` });
    expect(s.state.route?.route_id).toBe(worse.route_id);
    expect(s.state.settings.seed).toBe(3);
    expect(s.state.run.note).toBeNull();

    await runner.worse();
    expect(calls[3].body).toMatchObject({ base_route_id: worse.route_id, seed: 4 });
  });

  it("goes and rerolls with the settings' seed, then none", async () => {
    const calls = stubFetch((call) => created(call, "j"));
    const s = store();
    s.updateSettings({ seed: 41 });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    expect(calls[0].body).toMatchObject({ seed: 41, stops: [{ lat: 45.5231 }, { lat: 45.4817 }] });
    s.updateRun({ phase: "idle" });
    await runner.reroll();
    expect(calls[1].body).not.toHaveProperty("seed");
    expect(s.state.settings.seed).toBe(77); // the server's
  });
});

describe("targets", () => {
  const ratio = loadStream("portland-ratio-stream");

  it("sends the target and shows it, then what was achieved", async () => {
    const calls = stubFetch((call) =>
      call.method === "POST" ? created(call, "j7") : json(ratio.route),
    );
    const s = store();
    s.updateSettings({ target: "ratio", ratio: 25 });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    expect(calls[0].body).toMatchObject({ target: { kind: "ratio", value: 25 } });
    expect(statLines(s.state)[2]).toBe("");
    for (const event of ratio.events.slice(2, -1)) sources[0].send(event.event, event.data);
    expect(statLines(s.state)[2]).toBe("goal 25.0×");
    const done = ratio.events.at(-1)!;
    sources[0].send(done.event, done.data);
    await settle();
    const [, , target, run] = statLines(s.state);
    expect(target).toBe("goal 25.0×, reached 25.0×, within ±2 %");
    expect(run).toContain("goal reached");
  });

  it("sends a drive time in seconds, and shows it in hours and minutes", async () => {
    const calls = stubFetch((call) => created(call, "j8"));
    const s = store();
    s.updateSettings({ target: "duration", durationS: 9 * 3600 });
    await new JobRunner(s, fakeView(), () => {}).go();
    expect(calls[0].body).toMatchObject({ target: { kind: "duration", seconds: 32400 } });
    expect(statLines(s.state)[2]).toBe("");
    sources[0].send("snapshot", ratio.events[2].data);
    expect(statLines(s.state)[2]).toBe("goal 9 h");
    s.updateRun({
      phase: "done",
      outcome: { kind: "duration", value: 32400, achieved: 31800, within_tolerance: false },
    });
    expect(statLines(s.state)[2]).toBe("goal 9 h, reached 8 h 50 min, outside ±2 %");
  });

  it("says so when the base route already meets the target", async () => {
    stubFetch((call) => created(call, "j9"));
    const s = store(ratio.route);
    s.updateSettings({ target: "ratio", ratio: 25 });
    await new JobRunner(s, fakeView(), () => {}).worse();
    const done = ratio.events.at(-1)!;
    sources[0].send("done", done.data); // names the base route
    await settle();
    expect(s.state.route).toBe(ratio.route);
    expect(statLines(s.state)[3]).toContain("Already at the goal.");
  });
});

describe("modes and turn constraints", () => {
  const time = loadStream("portland-time-stream");
  const collect = loadStream("portland-collect-stream");
  const pilgrimage = loadStream("portland-brand-stream");
  const letters = loadStream("portland-alphabet-stream");

  it("sends the objective, its item and the turn constraints as set, no more", async () => {
    const calls = stubFetch((call) => created(call, "j"));
    const s = store();
    const runner = new JobRunner(s, fakeView(), () => {});
    const sent = async (patch: Partial<Settings>): Promise<RouteRequest> => {
      s.update({ run: IDLE_RUN });
      s.updateSettings(patch);
      await runner.go();
      return calls.at(-1)!.body as RouteRequest;
    };
    const plain = await sent({});
    expect(plain.objective).toBe("distance");
    expect(plain).not.toHaveProperty("item");
    expect(plain).not.toHaveProperty("brand");
    expect(plain).not.toHaveProperty("constraints");
    const slowest = await sent({ objective: "time", item: "roundabout" });
    expect(slowest.objective).toBe("time");
    expect(slowest).not.toHaveProperty("item"); // only collect takes one
    const collecting = await sent({ objective: "collect", item: "level_crossing" });
    expect(collecting).toMatchObject({ objective: "collect", item: "level_crossing" });
    expect(collecting).not.toHaveProperty("constraints");
    const lefts = await sent({ turns: "no_left" });
    expect(lefts.constraints).toEqual({ no_left: true, no_right: false, strict: false });
    const rights = await sent({ objective: "distance", turns: "no_right", strict: true });
    expect(rights.constraints).toEqual({ no_left: false, no_right: true, strict: true });
    expect(rights).not.toHaveProperty("item");
    const any = await sent({ turns: "any" }); // strict left ticked: it needs a side
    expect(any).not.toHaveProperty("constraints");
  });

  it("sends a pilgrimage's brand, the alphabet route and hard mode as set, no more", async () => {
    const calls = stubFetch((call) => created(call, "j"));
    const s = store();
    const runner = new JobRunner(s, fakeView(), () => {});
    const sent = async (patch: Partial<Settings>): Promise<RouteRequest> => {
      s.update({ run: IDLE_RUN });
      s.updateSettings(patch);
      await runner.go();
      return calls.at(-1)!.body as RouteRequest;
    };
    const pilgrimage = await sent({ objective: "brand", brand: 229, item: "roundabout" });
    expect(pilgrimage).toMatchObject({ objective: "brand", brand: 229 });
    expect(pilgrimage).not.toHaveProperty("item");
    expect(pilgrimage).not.toHaveProperty("constraints");
    const alphabet = await sent({ objective: "alphabet" });
    expect(alphabet).toMatchObject({ objective: "alphabet", target: { kind: "max" } });
    expect(alphabet).not.toHaveProperty("brand"); // only brand takes one
    expect(alphabet).not.toHaveProperty("item");
    const collecting = await sent({ objective: "collect", item: "bridge" });
    expect(collecting).not.toHaveProperty("brand");
    const hard = await sent({ objective: "distance", hard: true });
    expect(hard.constraints).toEqual({
      no_left: false,
      no_right: false,
      strict: false,
      hard: true,
    });
    const strict = await sent({ turns: "no_right", strict: true });
    expect(strict.constraints).toEqual({
      no_left: false,
      no_right: true,
      strict: true,
      hard: true,
    });
    const soft = await sent({ turns: "no_left", strict: false, hard: false });
    expect(soft.constraints).toEqual({ no_left: true, no_right: false, strict: false }); // no hard
    const any = await sent({ turns: "any", strict: true, hard: true }); // strict needs a side
    expect(any.constraints).toEqual({ no_left: false, no_right: false, strict: false, hard: true });
  });

  it("waits under Brand for the region's brands, with the brand set among them", () => {
    const s = store();
    const brands = [{ id: 229, name: "Starbucks", wikidata: "Q37158", locations: 126 }];
    s.updateSettings({ objective: "brand", brand: null });
    expect(awaitingBrand(s.state)).toBe(true); // GET /brands has not answered (or failed)
    s.updateSettings({ brand: 229 }); // a link's, before the brands are known
    expect(awaitingBrand(s.state)).toBe(true);
    s.update({ brands });
    expect(awaitingBrand(s.state)).toBe(false);
    s.updateSettings({ brand: 9999 });
    expect(awaitingBrand(s.state)).toBe(true);
    s.update({ brands: [] }); // a region without brands
    expect(awaitingBrand(s.state)).toBe(true);
    s.update({ brands: null });
    s.updateSettings({ objective: "distance" });
    expect(awaitingBrand(s.state)).toBe(false); // only Brand waits
  });

  it("makes no alphabet route worse, whatever the mode set, and says why", () => {
    const s = store(letters.route);
    for (const objective of ["distance", "time", "collect", "brand", "alphabet"] as const) {
      s.updateSettings({ objective }); // a continuation keeps its route's objective
      expect(worseRefused(s.state)).toBe("The alphabet route cannot be made worse.");
    }
    s.update({ route: collect.route });
    s.updateSettings({ objective: "alphabet" });
    expect(worseRefused(s.state)).toBeNull(); // the panel's mode is the next Go's
    expect(worseRefused({ ...s.state, route: null })).toBeNull();
  });

  it("makes a pilgrimage worse with its own brand, on its own build, under hard mode", async () => {
    // The region trips run on has changed since: the panel's brand is another build's id.
    const calls = stubFetch((call) => created(call, "jb"));
    const s = store(pilgrimage.route);
    s.update({ brands: [{ id: 1991, name: "Starbucks", wikidata: "Q37158", locations: 3721 }] });
    s.updateSettings({ objective: "brand", brand: 1991, hard: true });
    await new JobRunner(s, fakeView(), () => {}).worse();
    expect(calls[0].body).toEqual({
      region: "portland",
      base_route_id: pilgrimage.route.route_id,
      seed: 2,
      objective: "brand",
      brand: 229, // the route's own, Starbucks on Portland's build
      constraints: { no_left: false, no_right: false, strict: false, hard: true },
      budget_s: 20,
      target: { kind: "max" },
      vehicle: { mpg: 24, gas_price_usd: 3.89 },
      departure: expect.any(String),
    });
  });

  it("makes a route worse in its own mode, with the turns and target set", async () => {
    const calls = stubFetch((call) => created(call, "jw"));
    const s = store(collect.route);
    s.updateSettings({ objective: "brand", brand: 1991, turns: "no_left", target: "duration" });
    await new JobRunner(s, fakeView(), () => {}).worse();
    expect(calls[0].body).toEqual({
      region: "portland",
      base_route_id: collect.route.route_id,
      seed: 2,
      objective: "collect", // the route's, with its item; no brand
      item: "bridge",
      constraints: { no_left: true, no_right: false, strict: false },
      budget_s: 20,
      target: { kind: "duration", seconds: 9 * 3600 },
      vehicle: { mpg: 24, gas_price_usd: 3.89 },
      departure: expect.any(String),
    });
    const ratio = store(collect.route); // a collect run takes no ratio: as bad as possible
    ratio.updateSettings({ target: "ratio" });
    await new JobRunner(ratio, fakeView(), () => {}).worse();
    expect(calls[1].body).toMatchObject({ objective: "collect", target: { kind: "max" } });
    const plain = store(time.route); // a slowest-time route: no item, no brand
    plain.updateSettings({ objective: "collect", item: "roundabout" });
    await new JobRunner(plain, fakeView(), () => {}).worse();
    expect(calls[2].body).not.toHaveProperty("item");
    expect(calls[2].body).toMatchObject({ objective: "time", target: { kind: "max" } });
  });

  it("shows a slowest-time route's time ratio, its drive and delays, and its turns", async () => {
    stubFetch((call) => (call.method === "POST" ? created(call, "jt") : json(time.route)));
    const s = store();
    s.updateSettings({ objective: "time" });
    await new JobRunner(s, fakeView(), () => {}).go();
    for (const event of time.events.slice(0, -1)) sources[0].send(event.event, event.data);
    const running = statLines(s.state);
    expect(running[0]).toBe("17.2×"); // the lengths' ratio until the route is stored
    expect(running.slice(4)).toEqual(["", "", ""]);
    sources[0].send("done", time.events.at(-1)!.data);
    await settle();
    const [ratio, numbers, target, run, drive, items, turns] = statLines(s.state);
    expect(ratio).toBe("16.9× slower");
    expect(numbers).toBe("82.5 mi · 4 h 8 min");
    expect(drive).toBe("drive 4 h 8 min + delays 1 h 59 min");
    expect(items).toBe("");
    expect(turns).toBe("277 left · 282 right");
    expect(target).toBe("");
    expect(run).toBe("552 tries · 4.0 s · seed 1");
  });

  it("counts a collecting run's items live, then the stored route's", async () => {
    stubFetch((call) => (call.method === "POST" ? created(call, "jc") : json(collect.route)));
    const s = store();
    s.updateSettings({ objective: "collect", item: "bridge" });
    await new JobRunner(s, fakeView(), () => {}).go();
    const live: string[] = [];
    for (const event of collect.events.slice(0, -1)) {
      sources[0].send(event.event, event.data);
      if (event.event === "progress") live.push(statLines(s.state)[5]);
    }
    const counts = collect.events.flatMap((e) =>
      e.event === "progress" ? [`${e.data.metrics.items!.bridge} bridges`] : [],
    );
    expect(live).toEqual(counts);
    expect([live[0], live.at(-1)]).toEqual(["5 bridges", "18 bridges"]);
    sources[0].send("done", collect.events.at(-1)!.data);
    await settle();
    const [ratio, , , , drive, items, turns] = statLines(s.state);
    expect([ratio, drive, items, turns]).toEqual([
      "14.9×",
      "",
      "18 bridges",
      "237 left · 239 right",
    ]);
  });

  it("shows the route's own mode when Make it worse, the panel in another, finds nothing", async () => {
    stubFetch((call) => created(call, "jn"));
    const s = store(time.route); // a slowest-time route, with 10 bridges
    s.updateSettings({ objective: "collect", item: "bridge" });
    await new JobRunner(s, fakeView(), () => {}).worse();
    sources[0].send("done", { ...time.events.at(-1)!.data, route_id: time.route.route_id });
    await settle();
    expect(s.state.route).toBe(time.route);
    const [ratio, , , , drive, items] = statLines(s.state);
    expect([ratio, drive, items]).toEqual([
      "16.9× slower",
      "drive 4 h 8 min + delays 1 h 59 min",
      "",
    ]);
  });

  it("says no lefts (or rights) when the side avoided has none", () => {
    const s = store();
    const metrics = { length_m: 2000, time_s: 200, ratio: 2 };
    s.updateRun({
      phase: "done",
      metrics: { ...metrics, turns: { left: 0, right: 12, straight: 30, u_turn: 0 } },
      mode: { objective: "distance", constraints: { no_left: true } },
    });
    expect(statLines(s.state)[6]).toBe("no lefts · 12 right");
    s.updateRun({ mode: { objective: "distance", constraints: { no_right: true } } });
    expect(statLines(s.state)[6]).toBe("0 left · 12 right");
    s.updateRun({
      metrics: { ...metrics, turns: { left: 1211, right: 0, straight: 1, u_turn: 0 } },
    });
    expect(statLines(s.state)[6]).toBe("1,211 left · no rights");
    s.updateRun({ mode: {} });
    expect(statLines(s.state)[6]).toBe("1,211 left · 0 right");
    // Either constraint counts U-turns too: a route with one has not avoided the side.
    s.updateRun({
      metrics: { ...metrics, turns: { left: 0, right: 0, straight: 9, u_turn: 1 } },
      mode: { objective: "distance", constraints: { no_left: true } },
    });
    expect(statLines(s.state)[6]).toBe("0 left · 0 right");
    s.updateRun({ mode: { objective: "distance", constraints: { no_right: true } } });
    expect(statLines(s.state)[6]).toBe("0 left · 0 right");
  });

  // As the server refused a strict no-left Powell's → Reed on build 506f747d7458.
  const refusal = {
    code: "INFEASIBLE_TURN_CONSTRAINT",
    message:
      "no route from stop 0 to stop 1 without repeating a road, after moving stop 1 up to 10 " +
      "times, under the strict no-left constraint; try without strict",
    leg: [0, 1],
    stop_index: 1,
    constraint: "no_left",
  };
  const soft = { no_left: true, no_right: false, strict: false };

  it("tries again without strict when strict finds no route", async () => {
    const calls = stubFetch((call) =>
      calls.length === 1 ? json(refusal, 422) : created(call, "js"),
    );
    const s = store();
    s.updateSettings({ seed: 1, turns: "no_left", strict: true });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    expect(calls[0].body).toMatchObject({ constraints: { no_left: true, strict: true } });
    expect(s.state.error).toEqual(refusal);
    expect(busy(s)).toBe(false);
    await runner.withoutStrict();
    expect(s.state.settings).toMatchObject({ turns: "no_left", strict: false });
    expect(calls[1].body).toEqual({ ...(calls[0].body as RouteRequest), constraints: soft });
    expect(calls[1].body).toMatchObject({ seed: 1 });
    expect(s.state.error).toBeNull();
    expect(s.state.run.phase).toBe("running");
  });

  it("makes the same route worse without strict when a strict Make it worse is refused", async () => {
    const calls = stubFetch((call) =>
      calls.length === 1 ? json(refusal, 422) : created(call, "jsw"),
    );
    const s = store(time.route); // seed 1
    s.updateSettings({ seed: 41, turns: "no_left", strict: true });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.worse();
    expect(s.state.error?.code).toBe("INFEASIBLE_TURN_CONSTRAINT");
    expect(s.state.route).toBe(time.route); // still shown
    await runner.withoutStrict();
    const refused = calls[0].body as RouteRequest;
    expect(refused).toMatchObject({ base_route_id: time.route.route_id, seed: 2 });
    expect(calls[1].body).toEqual({ ...refused, constraints: soft });
    expect(s.state.settings).toMatchObject({ seed: 41, strict: false }); // a worse route sets it
  });

  it("rerolls without strict when a strict Reroll is refused", async () => {
    const calls = stubFetch((call) =>
      calls.length === 1 ? json(refusal, 422) : created(call, "jsr"),
    );
    const s = store();
    s.updateSettings({ seed: 41, turns: "no_left", strict: true });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.reroll();
    await runner.withoutStrict();
    expect(calls[1].body).toEqual({ ...(calls[0].body as RouteRequest), constraints: soft });
    expect(calls[1].body).not.toHaveProperty("seed"); // the server picks one, not seed 41
    expect(s.state.settings.seed).toBe(77);
  });
});

describe("a pilgrimage's visits, an alphabet route's letters and hard mode's refusal", () => {
  const metrics = { length_m: 2000, time_s: 200, ratio: 2 };
  const brand = { id: 229, name: "Starbucks", visits: 17, of: 19 };
  const alphabet = (sequence: string) => ({
    letters: sequence.length,
    sequence,
    roads: [...sequence].map((letter) => `${letter} Street`),
    alpha_from: 3,
    free_from: 40,
  });

  it("counts a pilgrimage's visits of those within reach, and an alphabet route's letters", () => {
    const s = store();
    const counts = (objective: RouteRequest["objective"], patch: object) => {
      s.updateRun({ phase: "running", mode: { objective }, metrics: { ...metrics, ...patch } });
      return [statLines(s.state)[5], countsTitle(s.state)];
    };
    expect(counts("brand", { brand })).toEqual(["17 of 19 Starbucks", ""]);
    expect(counts("brand", { brand: null })).toEqual(["", ""]); // a document's for another mode
    expect(counts("distance", { brand })).toEqual(["", ""]); // the run's own mode
    const letters = "ABCDEFGHIJKLMNOPQRSTUVW";
    expect(counts("alphabet", { alphabet: alphabet(letters) })).toEqual([
      "letters: 23 (A–W)",
      [...letters].map((letter) => `${letter}: ${letter} Street`).join("\n"), // one a line
    ]);
    expect(counts("alphabet", { alphabet: alphabet("A") })).toEqual([
      "letters: 1 (A)",
      "A: A Street",
    ]);
    expect(counts("alphabet", { alphabet: alphabet("") })).toEqual(["letters: 0", ""]);
    // Past Z the letters wrap to A: each lap of the alphabet, as the CLI writes them.
    const laps = (n: number) => {
      const sequence = Array.from({ length: n }, (_, k) => String.fromCharCode(65 + (k % 26)));
      return counts("alphabet", { alphabet: alphabet(sequence.join("")) })[0];
    };
    expect([1, 15, 26, 27, 29].map(laps)).toEqual([
      "letters: 1 (A)",
      "letters: 15 (A–O)",
      "letters: 26 (A–Z)",
      "letters: 27 (A–Z, A)",
      "letters: 29 (A–Z, A–C)",
    ]);
    expect(counts("time", { alphabet: alphabet("AB") })).toEqual(["", ""]);
  });

  it("counts the recorded pilgrimage's visits live, then the stored route's", async () => {
    const pilgrimage = loadStream("portland-brand-stream");
    stubFetch((call) => (call.method === "POST" ? created(call, "jb") : json(pilgrimage.route)));
    const s = store();
    s.updateSettings({ objective: "brand", brand: 229 });
    await new JobRunner(s, fakeView(), () => {}).go();
    const live: string[] = [];
    for (const event of pilgrimage.events.slice(0, -1)) {
      sources[0].send(event.event, event.data);
      if (event.event !== "snapped" && event.event !== "baseline") live.push(statLines(s.state)[5]);
    }
    expect(live).toEqual([
      "1 of 19 Starbucks", // the seed's snapshot
      "8 of 19 Starbucks",
      "13 of 19 Starbucks",
      "14 of 19 Starbucks",
    ]);
    sources[0].send("done", pilgrimage.events.at(-1)!.data);
    await settle();
    const [ratio, , , run, , visits] = statLines(s.state);
    expect([ratio, visits]).toEqual(["13.2×", "14 of 19 Starbucks"]);
    expect(run).toBe("450 tries · 0.3 s · seed 1");
  });

  it("follows the recorded alphabet route's better routes, each replacing the last", async () => {
    const alphabet = loadStream("portland-alphabet-stream");
    stubFetch((call) => (call.method === "POST" ? created(call, "ja") : json(alphabet.route)));
    const s = store();
    s.updateSettings({ objective: "alphabet" });
    const view = fakeView();
    await new JobRunner(s, view, () => {}).go();
    const shown: string[][] = [];
    for (const event of alphabet.events.slice(0, -1)) {
      sources[0].send(event.event, event.data);
      if (event.event === "progress") {
        const [ratio, , , , , letters] = statLines(s.state);
        shown.push([ratio, letters]);
        expect(s.state.run.metrics?.ratio).toBe(event.data.metrics.ratio); // the toasts' too
      }
    }
    const snapshots = alphabet.events.filter((e) => e.event === "snapshot");
    expect(view.setSnapshot).toHaveBeenCalledTimes(snapshots.length);
    expect(view.applySplice).not.toHaveBeenCalled();
    expect(shown.slice(0, 3)).toEqual([
      ["1.1×", "letters: 1 (A)"],
      ["1.0×", "letters: 2 (A–B)"], // a better route may be shorter: more letters
      ["1.1×", "letters: 3 (A–C)"],
    ]);
    expect(shown.at(-1)).toEqual(["1.9×", "letters: 15 (A–O)"]);
    sources[0].send("done", alphabet.events.at(-1)!.data);
    await settle();
    expect(view.setFinal).toHaveBeenCalledWith(alphabet.route.geometry);
    const [ratio, , , run, , letters] = statLines(s.state);
    expect([ratio, letters]).toEqual(["1.9×", "letters: 15 (A–O)"]);
    expect(run).toMatch(/^150 tries · 4\.5 s · seed 1$/);
    const roads = countsTitle(s.state).split("\n");
    expect(roads).toHaveLength(15);
    expect([roads[0], roads.at(-1)]).toEqual([
      "A: Southwest Alder Street",
      "O: Southwest Oak Street",
    ]);
  });

  // As the API refuses a leg the seed cannot build under hard mode (tests/api/test_routes_api.py).
  const refusal = {
    code: "STOP_UNREACHABLE_WITHOUT_REPEAT",
    message:
      "the seed found no way from stop 0 to stop 1 under hard mode, after moving stop 1 up to 10 " +
      "times; try without hard",
    leg: [0, 1],
    stop_index: 1,
  };

  it("tries again without hard mode when the seed finds no way under it", async () => {
    const calls = stubFetch((call) =>
      calls.length === 1 ? json(refusal, 422) : created(call, "jh"),
    );
    const s = store();
    s.updateSettings({ seed: 1, hard: true });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    const refused = calls[0].body as RouteRequest;
    expect(refused.constraints).toEqual({
      no_left: false,
      no_right: false,
      strict: false,
      hard: true,
    });
    expect(s.state.error).toEqual(refusal);
    expect(busy(s)).toBe(false);
    await runner.withoutHard();
    expect(s.state.settings.hard).toBe(false);
    const { constraints: _, ...plain } = refused; // no side: no constraints left
    expect(calls[1].body).toEqual(plain);
    expect(calls[1].body).toMatchObject({ seed: 1 });
    expect(s.state.error).toBeNull();
    expect(s.state.run.phase).toBe("running");
  });

  it("keeps the turn constraint, the base route and the seed it was refused with", async () => {
    const time = loadStream("portland-time-stream");
    const calls = stubFetch((call) =>
      calls.length % 2 === 1 ? json(refusal, 422) : created(call, `jh${calls.length}`),
    );
    const s = store(time.route); // seed 1
    s.updateSettings({ seed: 41, turns: "no_left", strict: true, hard: true });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.worse();
    await runner.withoutHard();
    const refused = calls[0].body as RouteRequest;
    expect(refused).toMatchObject({ base_route_id: time.route.route_id, seed: 2 });
    const strict = { no_left: true, no_right: false, strict: true };
    expect(calls[1].body).toEqual({ ...refused, constraints: strict });
    s.update({ run: IDLE_RUN });
    s.updateSettings({ hard: true });
    await runner.reroll();
    await runner.withoutHard();
    expect(calls[3].body).toEqual({ ...(calls[2].body as RouteRequest), constraints: strict });
    expect(calls[3].body).not.toHaveProperty("seed"); // a Reroll: the server picks one
  });

  it("makes the same route worse without hard mode when hard mode refuses it as a base", async () => {
    const time = loadStream("portland-time-stream");
    // As the API refuses a base route that comes back to a road (tests/api/test_routes_api.py).
    const mismatch = {
      code: "BASE_ROUTE_MISMATCH",
      message:
        "the base route comes back to northeast multnomah street at edge 59, which hard mode refuses",
      field: "route",
    };
    const calls = stubFetch((call) =>
      calls.length === 1 ? json(mismatch, 422) : created(call, "jhb"),
    );
    const s = store(time.route); // seed 1
    s.updateSettings({ hard: true });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.worse();
    expect(s.state.error).toEqual(mismatch);
    expect(s.state.route).toBe(time.route); // still shown
    await runner.withoutHard();
    const { constraints: _, ...plain } = calls[0].body as RouteRequest;
    expect(plain).toMatchObject({ base_route_id: time.route.route_id, seed: 2 });
    expect(calls[1].body).toEqual(plain);
    expect(s.state.settings.hard).toBe(false);
  });

  it("keeps hard mode when it tries again without strict", async () => {
    const infeasible = {
      code: "INFEASIBLE_TURN_CONSTRAINT",
      message:
        "no route from stop 0 to stop 1 ..., under hard mode and the strict no-left constraint; try without strict",
      leg: [0, 1],
      stop_index: 1,
      constraint: "no_left",
    };
    const calls = stubFetch((call) =>
      calls.length === 1 ? json(infeasible, 422) : created(call, "jhs"),
    );
    const s = store();
    s.updateSettings({ turns: "no_left", strict: true, hard: true });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    await runner.withoutStrict();
    expect((calls[1].body as RouteRequest).constraints).toEqual({
      no_left: true,
      no_right: false,
      strict: false,
      hard: true,
    });
    expect(s.state.settings).toMatchObject({ strict: false, hard: true });
  });
});

describe("the vehicle and the stored route's rows", () => {
  const directions = loadStream("portland-directions-stream");
  const none = { text: "", title: "" };

  it("labels each stop along the way as its marker is numbered, and not the ends", async () => {
    const calls = stubFetch((call) => created(call, "jl"));
    const s = store();
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    const labels = () => (calls.at(-1)!.body as RouteRequest).stops!.map((stop) => stop.label);
    expect(labels()).toEqual([undefined, undefined]); // the API's own words for the ends
    s.update({
      run: IDLE_RUN,
      stops: [
        { lat: 45.5231, lon: -122.6765, snapped: null },
        { lat: 45.5195, lon: -122.6625, snapped: null },
        { lat: 45.5317, lon: -122.654, snapped: null },
        { lat: 45.4817, lon: -122.6263, snapped: null },
      ],
    });
    await runner.reroll();
    expect(labels()).toEqual([undefined, "Stop 2", "Stop 3", undefined]);
    const via = (calls.at(-1)!.body as RouteRequest).stops![1];
    expect(via).toEqual({ lat: 45.5195, lon: -122.6625, label: "Stop 2" });
    expect((calls.at(-1)!.body as RouteRequest).stops![0]).toEqual({
      lat: 45.5231,
      lon: -122.6765,
    });
  });

  it("labels a city's stop with its name whatever its role, the clicked ones as before", async () => {
    const calls = stubFetch((call) => created(call, "jc"));
    const s = store();
    const runner = new JobRunner(s, fakeView(), () => {});
    s.update({
      stops: [
        { lat: 45.523064, lon: -122.676483, label: "Portland", snapped: null },
        { lat: 45.5195, lon: -122.6625, snapped: null },
        { lat: 45.498674, lon: -122.431481, label: "Gresham", snapped: null },
        { lat: 45.4817, lon: -122.6263, snapped: null },
      ],
    });
    await runner.go();
    const sent = () => (calls.at(-1)!.body as RouteRequest).stops!;
    expect(sent().map((stop) => stop.label)).toEqual(["Portland", "Stop 2", "Gresham", undefined]);
    expect(sent()[0]).toEqual({ lat: 45.523064, lon: -122.676483, label: "Portland" });
    s.update({
      run: IDLE_RUN,
      stops: [
        { lat: 45.5231, lon: -122.6765, snapped: null },
        { lat: 45.4462, lon: -122.6393, label: "Milwaukie", snapped: null },
      ],
    });
    await runner.reroll();
    expect(sent()).toEqual([
      { lat: 45.5231, lon: -122.6765 },
      { lat: 45.4462, lon: -122.6393, label: "Milwaukie" },
    ]);
  });

  it("sends the departure set, or now at the browser's offset", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-31T16:00:30Z"));
    const calls = stubFetch((call) => created(call, "jd"));
    const s = store(directions.route);
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    const now = (calls[0].body as RouteRequest).departure!;
    expect(now).toBe(isoWithOffset(new Date()));
    expect(Date.parse(now)).toBe(Date.parse("2026-10-31T16:00:30Z"));
    s.update({ run: IDLE_RUN, route: directions.route });
    s.updateSettings({ departure: "2026-11-02T08:15:00-08:00" });
    await runner.worse();
    expect(calls[1].body).toMatchObject({ departure: "2026-11-02T08:15:00-08:00" });
    vi.useRealTimers();
  });

  it("says when a route with a departure arrives, at the departure's offset", () => {
    const arrival = "2026-10-31T17:42:13.25-07:00";
    const s = store({ ...directions.route, metrics: { ...directions.route.metrics, arrival } });
    s.updateRun({ phase: "done" });
    expect(routeLines(s.state)[0]).toEqual({ text: "arrives Sat 17:42", title: arrival });
    s.update({ route: directions.route }); // recorded without a departure
    expect(routeLines(s.state)[0]).toEqual(none);
  });

  it("sends the vehicle set with Make it worse, Go and Reroll", async () => {
    const calls = stubFetch((call) => created(call, "jv"));
    const s = store(directions.route);
    s.updateSettings({ mpg: 31, gasPriceUsd: 4.5 });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.worse();
    s.updateRun({ phase: "idle" });
    await runner.go();
    s.updateRun({ phase: "idle" });
    await runner.reroll();
    const vehicle = { mpg: 31, gas_price_usd: 4.5 };
    expect(calls.map((call) => (call.body as RouteRequest).vehicle)).toEqual([
      vehicle,
      vehicle,
      vehicle,
    ]);
  });

  it("shows the recorded route's rows once stored, none while it runs", async () => {
    stubFetch((call) => (call.method === "POST" ? created(call, "jd") : json(directions.route)));
    const s = store();
    await new JobRunner(s, fakeView(), () => {}).go();
    for (const event of directions.events.slice(0, -1)) sources[0].send(event.event, event.data);
    expect(routeLines(s.state)).toEqual([none, none, none, none, none]);
    sources[0].send("done", directions.events.at(-1)!.data);
    await settle();
    expect(statLines(s.state)[0]).toBe("14.5×");
    expect(routeLines(s.state)).toEqual([
      none, // recorded without a departure
      { text: "towns: Portland", title: "Portland" },
      { text: "states: OR", title: "Oregon" },
      { text: "fuel: +2.7 gal · $10.50", title: "" },
      { text: "roads used: 10 % of those within reach", title: "" },
    ]);
  });

  it("names the first two towns and how many more, all of them in the tooltip", () => {
    const shown = (metrics: Partial<RouteDocument["metrics"]>): Store => {
      const route = { ...directions.route, metrics: { ...directions.route.metrics, ...metrics } };
      const s = store(route);
      s.updateRun({ phase: "done" });
      return s;
    };
    const towns = ["Portland", "Beaverton", "Tigard", "Lake Oswego", "Milwaukie"];
    const many = shown({
      towns,
      states: ["Oregon", "Washington"],
      extra_fuel_gal: null,
      extra_cost_usd: null,
      network_fraction: 0.004,
    });
    expect(routeLines(many.state).slice(1)).toEqual([
      { text: "towns: Portland, Beaverton +3", title: towns.join(", ") },
      { text: "states: OR, WA", title: "Oregon, Washington" },
      none, // no vehicle
      { text: "roads used: under 1 % of those within reach", title: "" },
    ]);
    const two = shown({ towns: towns.slice(0, 2) });
    expect(routeLines(two.state)[1].text).toBe("towns: Portland, Beaverton");
    const nowhere = shown({ towns: [], states: null });
    expect(routeLines(nowhere.state).slice(1, 3)).toEqual([none, none]);
    const {
      extra_fuel_gal,
      extra_cost_usd,
      states,
      towns: _,
      towns_n,
      network_fraction,
      ...old
    } = directions.route.metrics; // a server older than them
    const s = store({ ...directions.route, metrics: old });
    s.updateRun({ phase: "done" });
    expect(routeLines(s.state)).toEqual([none, none, none, none, none]);
  });
});

describe("leaving the page", () => {
  it("cancels a job whose POST was in flight, once the POST is answered", async () => {
    let answer: (response: Response) => void = () => {};
    const calls = stubFetch((call) =>
      call.method === "POST" ? new Promise<Response>((resolve) => (answer = resolve)) : json({}),
    );
    const s = store();
    const runner = new JobRunner(s, fakeView(), () => {});
    const going = runner.go();
    await settle();
    expect(s.state.run.phase).toBe("starting");
    runner.leave();
    expect(calls.map((c) => c.method)).toEqual(["POST"]);
    answer(json({ job_id: "j9", events_url: "/jobs/j9/events", seed: 5 }, 202));
    await going;
    expect(calls.map((c) => [c.method, c.url, c.keepalive])).toEqual([
      ["POST", "/api/jobs", false],
      ["DELETE", "/api/jobs/j9", true],
    ]);
    expect(sources).toHaveLength(0); // no stream is opened for it
  });

  it("cancels a running job at once", async () => {
    const calls = stubFetch((call) => (call.method === "POST" ? created(call, "j3") : json({})));
    const s = store();
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    runner.leave();
    expect(calls.at(-1)).toMatchObject({ method: "DELETE", url: "/api/jobs/j3", keepalive: true });
  });
});

describe("how a run ended", () => {
  it("is said in words, and not at all for a spent time budget, the usual end", () => {
    const s = store();
    const ended = (stopped_because: StopReason) => {
      const run = { ...fixture.route.run, attempts: 1, elapsed_s: 2, stopped_because };
      new JobRunner(s, fakeView(), () => {}).showRoute({ ...fixture.route, seed: 1, run });
      return statLines(s.state)[3];
    };
    expect(ended("budget")).toBe("1 try · 2.0 s · seed 1");
    expect(ended("converged")).toBe("1 try · 2.0 s · nothing worse found · seed 1");
    expect(ended("cancelled")).toBe("1 try · 2.0 s · cancelled · seed 1");
    expect(ended("timeout")).toBe("1 try · 2.0 s · timed out · seed 1");
  });
});

describe("a route drawn by hand", () => {
  it("says the run is a drawing, and a drawn route's moves", () => {
    const s = store();
    s.update({ run: { ...IDLE_RUN, phase: "drawing" } });
    expect(busy(s)).toBe(true);
    expect(statLines(s.state)[1]).toBe("Drawing by hand. Click a highlighted road to take it.");
    const run = { ...fixture.route.run, attempts: 25, accepted: 25, elapsed_s: 0 };
    const drawn = { ...fixture.route, run: { ...run, stopped_because: "drawn" as const } };
    new JobRunner(s, fakeView(), () => {}).showRoute({ ...drawn, seed: 1 });
    expect(statLines(s.state)[3]).toBe("25 moves · drawn by hand · seed 1");
    new JobRunner(s, fakeView(), () => {}).showRoute({
      ...drawn,
      run: { ...drawn.run, attempts: 1 },
    });
    expect(statLines(s.state)[3]).toBe("1 move · drawn by hand · seed 1");
  });
});

describe("a portfolio", () => {
  it("is sent above one seed, never under the alphabet route", async () => {
    const calls = stubFetch((call) => created(call, "jp"));
    const s = store();
    const runner = new JobRunner(s, fakeView(), () => {});
    const sent = async (patch: Partial<Settings>): Promise<RouteRequest> => {
      s.update({ run: IDLE_RUN });
      s.updateSettings(patch);
      await runner.go();
      return calls.at(-1)!.body as RouteRequest;
    };
    expect(await sent({})).not.toHaveProperty("portfolio");
    expect(await sent({ portfolio: 3 })).toMatchObject({ portfolio: 3 });
    expect(await sent({ objective: "alphabet" })).not.toHaveProperty("portfolio");
  });

  it("lowers the seeds to the server's most when it runs fewer, for the next Go", async () => {
    const refusal = {
      code: "PORTFOLIO_TOO_LARGE",
      message: "a portfolio of 4 seeds is over the 2 this server runs",
      max: 2,
    };
    const calls = stubFetch((call) =>
      call.body && (call.body as RouteRequest).portfolio === 4
        ? json(refusal, 422)
        : created(call, "jm"),
    );
    const s = store();
    s.updateSettings({ portfolio: 4 }); // a link's, before GET /config answered
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    expect(s.state.error).toEqual(refusal);
    expect(s.state.settings.portfolio).toBe(2);
    expect(s.state.run.phase).toBe("idle");
    await runner.go();
    expect(calls.at(-1)!.body).toMatchObject({ portfolio: 2 });
    expect(s.state.run.phase).toBe("running");
  });

  it("names its leader's seed while it runs, then its winner's, the panel keeping the first", async () => {
    const won: RouteDocument = {
      ...fixture.route,
      route_id: "0123456789ab",
      seed: 42,
      request: { ...fixture.route.request, seed: 40, portfolio: 4 },
      run: { ...fixture.route.run, portfolio: { k: 4, seeds: [40, 41, 42, 43], winner: 2 } },
    };
    const calls = stubFetch((call) => (call.method === "POST" ? created(call, "jq") : json(won)));
    const s = store();
    s.updateSettings({ seed: 40, portfolio: 4 });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    const metrics = { length_m: 9000, time_s: 900, delay_s: 0, ratio: 3 };
    const progress = (leader: number) => ({
      iteration: 12,
      elapsed_s: 1.5,
      score: 9000,
      metrics,
      splices: [],
      portfolio: { k: 4, leader, best_ratios: [2.5, 2.8, 3, 2.9] },
    });
    sources[0].send("progress", progress(0));
    expect(statLines(s.state)[3]).toMatch(/^Try 12 · 1\.5 s · best of 4 runs · seed 40$/);
    sources[0].send("progress", progress(2));
    expect(statLines(s.state)[3]).toMatch(/ · best of 4 runs · seed 42$/);
    // the leader's best above its current route: its ratio first, then the portfolio's parts
    const annealing = {
      ...progress(2),
      best_score: 9500,
      best_metrics: { ...metrics, ratio: 3.2 },
    };
    sources[0].send("progress", annealing);
    expect(statLines(s.state)[3]).toMatch(
      /^Try 12 · 1\.5 s · best 3\.2× · best of 4 runs · seed 42$/,
    );
    sources[0].send("done", { route_id: won.route_id, stopped_because: "budget", metrics: {} });
    await settle();
    expect(calls.at(-1)).toMatchObject({ method: "GET", url: `/api/routes/${won.route_id}` });
    expect(statLines(s.state)[3]).toMatch(/ s · best of 4 runs · seed 42$/);
    expect(s.state.settings.seed).toBe(40); // Go plans the same four seeds again
    await runner.worse(); // a continuation is one search, from the winner's seed
    expect(calls.at(-1)!.body).toMatchObject({ base_route_id: won.route_id, seed: 43 });
    expect(calls.at(-1)!.body).not.toHaveProperty("portfolio");
  });
});

describe("a route an earlier run stored", () => {
  // A route's id is its content's, and the store keeps its first record: the route a seed gives
  // alone may come back with the document of a portfolio that found it first.
  const portfolios: RouteDocument = {
    ...fixture.route,
    route_id: "0123456789ab",
    seed: 12,
    request: { ...fixture.route.request, seed: 11, portfolio: 2 },
    run: { ...fixture.route.run, portfolio: { k: 2, seeds: [11, 12], winner: 1 } },
  };

  it("keeps a Go's own seed and shows no portfolio it did not ask for", async () => {
    stubFetch((call) => (call.method === "POST" ? created(call, "jc") : json(portfolios)));
    const s = store();
    s.updateSettings({ seed: 12 });
    await new JobRunner(s, fakeView(), () => {}).go();
    sources[0].send("done", {
      route_id: portfolios.route_id,
      stopped_because: "budget",
      metrics: {},
    });
    await settle();
    expect(s.state.route?.route_id).toBe(portfolios.route_id);
    expect(s.state.settings.seed).toBe(12);
    expect(statLines(s.state)[3]).toMatch(/ s · seed 12$/);
  });

  it("shows a portfolio's own seeds and keeps its first seed in the panel", async () => {
    stubFetch((call) => (call.method === "POST" ? created(call, "jd") : json(portfolios)));
    const s = store();
    s.updateSettings({ seed: 11, portfolio: 2 });
    await new JobRunner(s, fakeView(), () => {}).go();
    sources[0].send("done", {
      route_id: portfolios.route_id,
      stopped_because: "budget",
      metrics: {},
    });
    await settle();
    expect(s.state.settings.seed).toBe(11);
    expect(statLines(s.state)[3]).toMatch(/ s · best of 2 runs · seed 12$/);
  });
});

describe("the worst order", () => {
  const four = [
    { lat: 45.5231, lon: -122.6765, snapped: null },
    { lat: 45.5195, lon: -122.6625, snapped: null },
    { lat: 45.5317, lon: -122.654, snapped: null },
    { lat: 45.4817, lon: -122.6263, snapped: null },
  ];
  const at = (node: number) => ({
    input: { lat: 45.5, lon: -122.6 },
    label: null,
    node,
    lat: 45.5,
    lon: -122.6,
    moved_m: 0,
    note: null,
  });
  const scale = { tier: "T4", leash_km: 3, active_nodes: 100 };

  it("is sent when ticked, never under the alphabet route", async () => {
    const calls = stubFetch((call) => created(call, "jr"));
    const s = store();
    const runner = new JobRunner(s, fakeView(), () => {});
    const sent = async (patch: Partial<Settings>): Promise<RouteRequest> => {
      s.update({ run: IDLE_RUN });
      s.updateSettings(patch);
      await runner.go();
      return calls.at(-1)!.body as RouteRequest;
    };
    expect(await sent({})).not.toHaveProperty("reorder");
    expect(await sent({ reorder: true })).toMatchObject({ reorder: true });
    expect(await sent({ objective: "alphabet" })).not.toHaveProperty("reorder");
  });

  it("says it tries every order, then where each stop went, kept with the stored route", async () => {
    const stored = { ...fixture.route, route_id: "0123456789ab" };
    const calls = stubFetch((call) =>
      call.method === "POST" ? created(call, `j${calls.length}`) : json(stored),
    );
    const s = store();
    s.update({ stops: four });
    s.updateSettings({ reorder: true });
    const runner = new JobRunner(s, fakeView(), () => {});
    await runner.go();
    expect(statLines(s.state)[1]).toBe("Trying every order…");
    // visited 0, 2, 1, 3: the snapped stops come in that order
    sources[0].send("snapped", { stops: [10, 30, 20, 40].map(at), order: [0, 2, 1, 3], ...scale });
    expect(s.state.stops.map((stop) => stop.snapped?.node)).toEqual([10, 20, 30, 40]);
    expect(s.state.run.order).toEqual([0, 2, 1, 3]);
    expect(statLines(s.state)[1]).toBe("Planning the sensible route…");
    sources[0].send("done", { route_id: stored.route_id, stopped_because: "budget", metrics: {} });
    await settle();
    expect(s.state.route?.route_id).toBe(stored.route_id);
    expect(s.state.run.order).toEqual([0, 2, 1, 3]);

    // Make it worse: the base's stops in the order it visits them, the order the identity
    await runner.worse();
    expect(calls.at(-1)!.body).not.toHaveProperty("reorder");
    sources[1].send("snapped", { stops: [11, 31, 21, 41].map(at), order: [0, 1, 2, 3], ...scale });
    expect(s.state.stops.map((stop) => stop.snapped?.node)).toEqual([11, 21, 31, 41]);
    expect(s.state.run.order).toEqual([0, 2, 1, 3]);

    // a run in the list's own order says nothing of it
    s.updateSettings({ reorder: false });
    await settle();
    s.update({ run: IDLE_RUN });
    await runner.go();
    sources[2].send("snapped", { stops: [10, 20, 30, 40].map(at), order: [0, 1, 2, 3], ...scale });
    expect(s.state.run.order).toBeNull();
  });

  it("reads a snapped event without an order, an older server's, as the list's own", async () => {
    stubFetch((call) => created(call, "jo"));
    const s = store();
    s.update({ stops: four });
    await new JobRunner(s, fakeView(), () => {}).go();
    sources[0].send("snapped", { stops: [10, 20, 30, 40].map(at), ...scale });
    expect(s.state.stops.map((stop) => stop.snapped?.node)).toEqual([10, 20, 30, 40]);
    expect(s.state.run.order).toBeNull();
    const lines = s.state.stops.map((stop, k) => visitLine(stop, k, s.state.run.order));
    expect(lines).toEqual([null, null, null, null]); // no "visited" places
  });
});

describe("a certified route", () => {
  const certified = loadStream("portland-certified-stream");

  it("is sent with as bad as possible and none of what it does not take", async () => {
    const calls = stubFetch((call) => created(call, "jc"));
    const s = store();
    s.updateSettings({
      certified: true,
      target: "ratio",
      turns: "no_left",
      strict: true,
      hard: true,
      reorder: true,
      portfolio: 4,
      leashKm: 0.4,
    });
    await new JobRunner(s, fakeView(), () => {}).go();
    const sent = calls[0].body as RouteRequest;
    expect(sent).toMatchObject({ certified: true, target: { kind: "max" }, leash_km: 0.4 });
    for (const key of ["constraints", "reorder", "portfolio"]) expect(sent).not.toHaveProperty(key);
    expect(s.state.settings).toMatchObject({ target: "ratio", hard: true, portfolio: 4 }); // kept
    // under another mode the box counts for nothing
    s.update({ run: IDLE_RUN });
    s.updateSettings({ objective: "time" });
    await new JobRunner(s, fakeView(), () => {}).go();
    const plain = calls[1].body as RouteRequest;
    expect(plain).not.toHaveProperty("certified");
    expect(plain).toMatchObject({ objective: "time", reorder: true, portfolio: 4 });
  });

  it("shows the solver's bound live, then its certificate", async () => {
    stubFetch((call) => (call.method === "POST" ? created(call, "jc") : json(certified.route)));
    const s = store();
    s.updateSettings({ certified: true });
    const view = fakeView();
    await new JobRunner(s, view, () => {}).go();
    const live: string[] = [];
    for (const event of certified.events.slice(0, -1)) {
      sources[0].send(event.event, event.data);
      if (event.event === "progress") live.push(statLines(s.state)[2]);
    }
    expect(live[0]).toBe("the worst possible is at most 6368 % longer"); // the seed, at first
    expect(live.at(-1)).toBe("the worst possible is at most 0.5 % longer"); // proving
    expect(view.applySplice).not.toHaveBeenCalled();
    sources[0].send("done", certified.events.at(-1)!.data);
    await settle();
    const [ratio, , target, run] = statLines(s.state);
    expect(ratio).toBe("21.5×");
    expect(target).toBe("no longer legal route in this area · 182 junctions");
    expect(run).toMatch(/ · proven · seed 1$/);
  });

  it("reads the longest found and its gap when not proven, and is not made worse", () => {
    const proven = certified.route;
    const s = store(proven);
    new JobRunner(s, fakeView(), () => {}).showRoute(proven);
    expect(statLines(s.state)[2]).toBe("no longer legal route in this area · 182 junctions");
    expect(worseRefused(s.state)).toBe("A proven route is already the worst its area allows.");
    const run = proven.run;
    const feasible: RouteDocument = {
      ...proven,
      run: {
        ...run,
        stopped_because: "budget",
        certified: { ...run.certified!, status: "feasible", gap: 0.124 },
      },
    };
    new JobRunner(s, fakeView(), () => {}).showRoute(feasible);
    expect(statLines(s.state)[2]).toBe("longest found · the worst possible is at most 12 % longer");
    expect(worseRefused(s.state)).toBe("Plan it again with a longer time budget.");
    const unbounded: RouteDocument = {
      ...feasible,
      run: { ...feasible.run, certified: { ...feasible.run.certified!, bound_m: null, gap: null } },
    };
    new JobRunner(s, fakeView(), () => {}).showRoute(unbounded);
    expect(statLines(s.state)[2]).toBe("longest found · no bound yet");
  });
});
