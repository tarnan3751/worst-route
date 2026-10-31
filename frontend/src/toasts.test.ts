import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeView, json, stubFetch } from "./__fixtures__/http";
import { loadStream } from "./__fixtures__/stream";
import { JobRunner } from "./run/controls";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "./state";
import { Toasts, WAITING } from "./toasts";

const TOAST_MS = 4000; // every 4 s
const { route } = loadStream("portland-directions-stream");
const toasts = route.toasts!;
const metrics = (ratio: number | null) => ({ length_m: 1000, time_s: 100, ratio });

function store(): Store {
  return new Store({
    region: null,
    stops: [],
    settings: DEFAULT_SETTINGS,
    run: IDLE_RUN,
    route: null,
    error: null,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("the toasts", () => {
  it("show nothing while idle, and keep no timer", () => {
    const t = new Toasts(store());
    expect(t.text).toBeNull();
    vi.advanceTimersByTime(10 * TOAST_MS);
    expect(t.text).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("go through the waiting lines and the live ratio while a run goes, every 4 s", () => {
    const s = store();
    const t = new Toasts(s);
    expect(WAITING).toEqual([
      "Finding the worst way. Please hold.",
      "Rejecting good ideas…",
      "Still not repeating a road.",
    ]);
    s.updateRun({ phase: "starting" });
    expect(t.text).toBe(WAITING[0]);
    s.updateRun({ phase: "running", metrics: metrics(17.2) });
    vi.advanceTimersByTime(TOAST_MS - 1);
    expect(t.text).toBe(WAITING[0]);
    vi.advanceTimersByTime(1);
    expect(t.text).toBe(WAITING[1]);
    vi.advanceTimersByTime(TOAST_MS);
    expect(t.text).toBe(WAITING[2]);
    vi.advanceTimersByTime(TOAST_MS);
    expect(t.text).toBe("Currently 17.2× worse");
    s.updateRun({ metrics: metrics(18.04) }); // the ratio is live
    expect(t.text).toBe("Currently 18.0× worse");
    vi.advanceTimersByTime(TOAST_MS);
    expect(t.text).toBe(WAITING[0]); // and round again
  });

  it("leave the ratio out while there is none", () => {
    const s = store();
    const t = new Toasts(s);
    s.updateRun({ phase: "running", metrics: metrics(null) }); // a round trip
    const seen = [t.text];
    for (let k = 0; k < 3; k++) {
      vi.advanceTimersByTime(TOAST_MS);
      seen.push(t.text);
    }
    expect(seen).toEqual([...WAITING, WAITING[0]]);
  });

  it("keep the line shown when the ratio comes, its slot skipped until then", () => {
    const s = store();
    const t = new Toasts(s);
    s.updateRun({ phase: "running", metrics: null });
    vi.advanceTimersByTime(3 * TOAST_MS); // past the ratio's slot, which has nothing yet
    expect(t.text).toBe(WAITING[0]);
    s.updateRun({ metrics: metrics(3.2) });
    expect(t.text).toBe(WAITING[0]); // not the ratio, which comes in its own slot
    vi.advanceTimersByTime(TOAST_MS);
    expect(t.text).toBe(WAITING[1]);
    vi.advanceTimersByTime(2 * TOAST_MS);
    expect(t.text).toBe("Currently 3.2× worse");
  });

  it("show no ratio of the last run while the next one's POST is in flight", async () => {
    let answer: (response: Response) => void = () => {};
    stubFetch(() => new Promise<Response>((resolve) => (answer = resolve)));
    const s = store();
    s.update({ route, run: { ...IDLE_RUN, phase: "done", metrics: metrics(14.5) } });
    const t = new Toasts(s);
    const going = new JobRunner(s, fakeView(), () => {}).worse();
    expect(s.state.run).toMatchObject({ phase: "starting", metrics: null });
    const seen = [t.text];
    for (let k = 0; k < 4; k++) {
      vi.advanceTimersByTime(TOAST_MS);
      seen.push(t.text);
    }
    expect(seen).toEqual([...WAITING, WAITING[0], WAITING[1]]);
    answer(json({ code: "TOO_MANY_JOBS", message: "every worker is busy" }, 429));
    await going;
    expect(s.state.run.metrics?.ratio).toBe(14.5); // a refused POST gives the route's back
    vi.unstubAllGlobals();
  });

  it("go through the stored route's toasts in order, from the first, and loop", () => {
    const s = store();
    const t = new Toasts(s);
    s.updateRun({ phase: "running", metrics: metrics(5) });
    vi.advanceTimersByTime(2 * TOAST_MS);
    s.update({ route, run: { ...IDLE_RUN, phase: "done" } });
    const seen = [t.text];
    for (let k = 0; k < toasts.length; k++) {
      vi.advanceTimersByTime(TOAST_MS);
      seen.push(t.text);
    }
    expect(toasts).toHaveLength(10);
    expect(seen).toEqual([...toasts, toasts[0]]);
    expect(toasts[0]).toBe("14.5× less efficient than the sensible route.");
  });

  it("hold the line while the pointer is over it, and move on a click", () => {
    const s = store();
    const t = new Toasts(s);
    s.update({ route, run: { ...IDLE_RUN, phase: "done" } });
    vi.advanceTimersByTime(TOAST_MS);
    expect(t.text).toBe(toasts[1]);
    t.hold(true);
    vi.advanceTimersByTime(3 * TOAST_MS);
    expect(t.text).toBe(toasts[1]);
    t.next(); // a click, held or not
    expect(t.text).toBe(toasts[2]);
    vi.advanceTimersByTime(3 * TOAST_MS);
    expect(t.text).toBe(toasts[2]);
    t.hold(false);
    vi.advanceTimersByTime(TOAST_MS - 1);
    expect(t.text).toBe(toasts[2]); // a whole period after the pointer leaves
    vi.advanceTimersByTime(1);
    expect(t.text).toBe(toasts[3]);
    t.next();
    vi.advanceTimersByTime(TOAST_MS - 1);
    expect(t.text).toBe(toasts[4]); // a click starts the period again
  });

  it("stop when the route is forgotten, and say nothing for a route without toasts", () => {
    const s = store();
    const t = new Toasts(s);
    s.update({ route, run: { ...IDLE_RUN, phase: "done" } });
    s.update({ route: null, run: IDLE_RUN }); // the stops were edited
    expect(t.text).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    s.update({ route: { ...route, toasts: undefined }, run: { ...IDLE_RUN, phase: "done" } });
    expect(t.text).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});
