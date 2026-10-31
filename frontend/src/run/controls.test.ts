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
import type { RouteDocument, RouteRequest } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type Settings } from "../state";
import { busy } from "../stops/stopList";
import { JobRunner, statLines } from "./controls";

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
    expect(statLines(s.state)[3]).toContain(
      "no worse route found with seed 2; try again or Reroll",
    );
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
    expect(statLines(s.state)[2]).toBe("target 25.0×");
    const done = ratio.events.at(-1)!;
    sources[0].send(done.event, done.data);
    await settle();
    const [, , target, run] = statLines(s.state);
    expect(target).toBe("target 25.0×, achieved 25.0×, within ±2 %");
    expect(run).toContain("target reached");
  });

  it("sends a drive time in seconds, and shows it in hours and minutes", async () => {
    const calls = stubFetch((call) => created(call, "j8"));
    const s = store();
    s.updateSettings({ target: "duration", durationS: 9 * 3600 });
    await new JobRunner(s, fakeView(), () => {}).go();
    expect(calls[0].body).toMatchObject({ target: { kind: "duration", seconds: 32400 } });
    expect(statLines(s.state)[2]).toBe("");
    sources[0].send("snapshot", ratio.events[2].data);
    expect(statLines(s.state)[2]).toBe("target 9 h");
    s.updateRun({
      phase: "done",
      outcome: { kind: "duration", value: 32400, achieved: 31800, within_tolerance: false },
    });
    expect(statLines(s.state)[2]).toBe("target 9 h, achieved 8 h 50 min, outside ±2 %");
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
    expect(statLines(s.state)[3]).toContain("already within the target");
  });
});

describe("modes and turn constraints", () => {
  const time = loadStream("portland-time-stream");
  const collect = loadStream("portland-collect-stream");

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

  it("makes a route worse in the mode set", async () => {
    const calls = stubFetch((call) => created(call, "jw"));
    const s = store(collect.route);
    s.updateSettings({ objective: "collect", item: "bridge", turns: "no_left" });
    await new JobRunner(s, fakeView(), () => {}).worse();
    expect(calls[0].body).toEqual({
      region: "portland",
      base_route_id: collect.route.route_id,
      seed: 2,
      objective: "collect",
      item: "bridge",
      constraints: { no_left: true, no_right: false, strict: false },
      budget_s: 20,
      target: { kind: "max" },
    });
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
    expect(run).toBe("552 attempts · 4.0 s · budget · seed 1");
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

  it("shows the run's mode when Make it worse in another finds nothing", async () => {
    stubFetch((call) => created(call, "jn"));
    const s = store(time.route); // a slowest-time route, with 10 bridges
    s.updateSettings({ objective: "collect", item: "bridge" });
    await new JobRunner(s, fakeView(), () => {}).worse();
    sources[0].send("done", { ...time.events.at(-1)!.data, route_id: time.route.route_id });
    await settle();
    expect(s.state.route).toBe(time.route);
    const [ratio, , , , drive, items] = statLines(s.state);
    expect([ratio, drive, items]).toEqual(["17.2×", "", "10 bridges"]);
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
