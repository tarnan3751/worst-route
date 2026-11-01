// Today's route without a DOM: `DailyRoute` with `fetch` and `EventSource` stubbed over a real
// Store, through each state (planning with its ratio so far, done, failed and asked again, a busy
// server, none), what the section says of it (`dailyLine`), when it collapses, `route`, and
// `openDailyAtStart` for `?daily`, which never takes over a run of the user's own.
import { afterEach, describe, expect, it, vi } from "vitest";
import { json, settle, stubEventSource, stubFetch, type Call } from "../__fixtures__/http";
import type { Daily, RouteDocument } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
import { DailyRoute, dailyLine, openDailyAtStart } from "./panel";

const PLANNING: Daily = {
  date: "2026-10-31",
  region: "us-t3",
  build_id: "281a455e3d15",
  stops: [
    { lat: 37.123456, lon: -93.654321, label: "Vista, MO" },
    { lat: 43.444444, lon: -71.666666, label: "Franklin, NH" },
  ],
  seed: 20261031,
  max_iterations: 5000,
  status: "planning",
  route_id: null,
  events_url: "/jobs/abc123/events",
  ratio: null,
  stopped_because: null,
  attempts: null,
};
const DONE: Daily = {
  ...PLANNING,
  status: "done",
  route_id: "5ea844bffd40",
  events_url: null,
  ratio: 11.04,
  stopped_because: "budget",
  attempts: 5000,
};
const FAILED: Daily = { ...PLANNING, status: "failed", events_url: null };

const metrics = (ratio: number) => ({ length_m: 1, time_s: 1, delay_s: 0, ratio });

function setup(...answers: (Daily | number)[]) {
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
  const calls: Call[] = stubFetch(() => {
    const next = answers.length > 1 ? answers.shift() : answers[0];
    if (typeof next === "number") {
      return json({ code: next === 429 ? "TOO_MANY_JOBS" : "NOT_FOUND", message: "no" }, next);
    }
    return json(next);
  });
  const sources = stubEventSource();
  return { store, daily: new DailyRoute(store), calls, sources };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("today's route", () => {
  it("follows the server's plan, then shows the route it made", async () => {
    const { daily, calls, sources } = setup(PLANNING, DONE);
    expect(daily.phase).toBe("loading");
    const opened = daily.route();
    await daily.load();
    expect(daily.phase).toBe("planning");
    expect(dailyLine(daily)).toBe("planning…");
    expect(sources.map((s) => s.url)).toEqual(["/api/jobs/abc123/events"]);
    const [source] = sources;
    source.send("snapshot", { metrics: metrics(1.2) });
    expect(dailyLine(daily)).toBe("planning… 1.2× so far");
    source.send("progress", { metrics: metrics(7.25), splices: [] });
    expect(daily.ratio).toBe(7.25);
    source.send("done", { route_id: "5ea844bffd40", stopped_because: "budget", metrics: {} });
    await settle();
    expect(source.readyState).toBe(2); // closed: the job is done
    expect(daily.phase).toBe("done");
    expect(daily.daily?.route_id).toBe("5ea844bffd40");
    expect(dailyLine(daily)).toBe("11.0× less efficient than the sensible route");
    await expect(opened).resolves.toBe("5ea844bffd40");
    // GET /daily twice, and never a DELETE: the job is the server's
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(["GET /api/daily", "GET /api/daily"]);
  });

  it("shows a route already done at once, its ratio the server's", async () => {
    const { daily, sources } = setup(DONE);
    await daily.load();
    expect(daily.phase).toBe("done");
    expect(daily.ratio).toBe(11.04);
    expect(sources).toEqual([]);
    await expect(daily.route()).resolves.toBe("5ea844bffd40");
  });

  it("asks again after a failed plan, and once the stream is lost or ends in an error", async () => {
    const { daily, sources } = setup(FAILED, PLANNING, PLANNING, DONE);
    await daily.load();
    expect(daily.phase).toBe("failed");
    expect(dailyLine(daily)).toBe("Today's route could not be planned.");
    await expect(daily.route()).resolves.toBeNull();
    await daily.load(); // Try again
    expect(daily.phase).toBe("planning");
    sources[0].send("error", { code: "WORKER_FAILED", message: "boom" });
    await settle();
    expect(sources[0].readyState).toBe(2);
    expect(sources).toHaveLength(2); // planned again, followed again
    sources[1].readyState = 2;
    sources[1].dispatchEvent(new Event("error")); // the browser gave up reconnecting
    await settle();
    expect(daily.phase).toBe("done");
  });

  it("is a day to try again when the server is too busy to plan it", async () => {
    const { daily } = setup(429, DONE);
    await daily.load();
    expect(daily.phase).toBe("failed");
    expect(dailyLine(daily)).toBe("Today's route could not be planned.");
    await daily.load(); // Try again
    expect(daily.phase).toBe("done");
  });

  it("is none where the server has no daily route", async () => {
    const { daily } = setup(404);
    await daily.load();
    expect(daily.phase).toBe("none");
    expect(daily.daily).toBeNull();
    expect(dailyLine(daily)).toBe("");
    await expect(daily.route()).resolves.toBeNull();
  });

  it("collapses when a run of the user's own starts, and shows again when asked", async () => {
    const { store, daily } = setup(DONE);
    await daily.load();
    const changes = vi.fn();
    daily.addEventListener("change", changes);
    store.updateRun({ phase: "done" }); // a route opened, as Open it does
    expect(daily.collapsed).toBe(false);
    store.updateRun({ phase: "starting" }); // Go
    expect(daily.collapsed).toBe(true);
    store.updateRun({ phase: "running" });
    daily.setCollapsed(false); // the header's link
    expect(daily.collapsed).toBe(false);
    store.updateRun({ phase: "running" });
    expect(daily.collapsed).toBe(false); // the same run: not again
    expect(changes).toHaveBeenCalledTimes(2);
  });
});

describe("?daily at start", () => {
  it("opens today's route once it is done, while the user has done nothing", async () => {
    const { store, daily, sources } = setup(PLANNING, DONE);
    const open = vi.fn(async () => true);
    const opened = openDailyAtStart(daily, store, open);
    await daily.load();
    sources[0].send("done", { route_id: "5ea844bffd40", stopped_because: "budget", metrics: {} });
    await expect(opened).resolves.toBe(true);
    expect(open).toHaveBeenCalledWith("5ea844bffd40");
  });

  it("leaves a run the user started meanwhile alone", async () => {
    const { store, daily, sources } = setup(PLANNING, DONE);
    const open = vi.fn(async () => true);
    const opened = openDailyAtStart(daily, store, open);
    await daily.load();
    store.updateRun({ phase: "starting" }); // Go, while the server plans today's route
    await expect(opened).resolves.toBe(false); // at once: the URL follows the app from now
    store.updateRun({ phase: "running", jobId: "mine" });
    sources[0].send("done", { route_id: "5ea844bffd40", stopped_because: "budget", metrics: {} });
    await settle();
    expect(daily.phase).toBe("done");
    expect(open).not.toHaveBeenCalled();
    expect(store.state.run.jobId).toBe("mine");
  });

  it("leaves a route the user opened meanwhile alone, and opens nothing for a failed day", async () => {
    const { store, daily } = setup(DONE);
    const open = vi.fn(async () => true);
    const opened = openDailyAtStart(daily, store, open);
    store.update({ route: { route_id: "abc" } as RouteDocument }); // a route opened first
    await daily.load();
    await expect(opened).resolves.toBe(false);
    expect(open).not.toHaveBeenCalled();
    const failed = setup(FAILED);
    const none = openDailyAtStart(failed.daily, failed.store, open);
    await failed.daily.load();
    await expect(none).resolves.toBe(false);
    expect(open).not.toHaveBeenCalled();
  });
});
