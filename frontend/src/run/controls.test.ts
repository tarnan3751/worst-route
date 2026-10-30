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
import type { RouteDocument } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
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
