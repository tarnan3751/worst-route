// Grading without a DOM: each goal kind on the recordings' stored routes, moved to the challenge's
// region and stops (the max-mode recording's 78× and the ratio recording's 25×, the time
// recording's 6.1 h with its delays, the directions recording's left turns, the collect
// recording's 18 bridges, the brand recording's 14 of 19 Starbucks); what makes a route a
// challenge's trip (the stops within 500 m, the worst order's in any order between); today's route
// made worse only on today's challenge; and the completions kept in a stand-in storage (once each,
// graded again when the list comes, past days' kept, the fixed list's of old dropped, reset, junk
// ignored, a throwing storage survived), with the toast's line.
import { describe, expect, it } from "vitest";
import { ANSWER } from "../__fixtures__/challenges";
import { loadStream } from "../__fixtures__/stream";
import type { BrandEntry, Daily, RouteDocument, RouteRequest } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
import { challengesOf, type Challenge } from "./list";
import {
  ChallengeProgress,
  STORAGE_KEY,
  completedLine,
  reached,
  sameTrip,
  tripOf,
  type ChallengeSource,
  type ChallengeStorage,
} from "./progress";

const CHALLENGES = challengesOf(ANSWER);
const challenge = (type: string): Challenge => CHALLENGES.find((c) => c.type === type)!;
const max = loadStream("portland-stream").route; // 78.0×, three stops, no turns recorded
const ratio25 = loadStream("portland-ratio-stream").route;
const slow = loadStream("portland-time-stream").route;
const turning = loadStream("portland-directions-stream").route;
const collect = loadStream("portland-collect-stream").route;
const brand = loadStream("portland-brand-stream").route;
const US = { ...loadStream().regions[0], region: "us-t3" };
const STARBUCKS: BrandEntry = { id: 229, name: "Starbucks", wikidata: "Q37158", locations: 19 };
const TODAY: Daily = {
  date: "2026-11-02",
  region: "us-t3",
  build_id: "6adae6917be1",
  stops: [],
  seed: 20261102,
  max_iterations: 5000,
  status: "done",
  route_id: "a076702bbc25",
  events_url: null,
  ratio: 6.58,
  stopped_because: "budget",
  attempts: 5000,
};

function storeWith(brands: BrandEntry[] | null = null): Store {
  return new Store({
    regions: [US],
    region: US,
    pick: null,
    stops: [],
    settings: DEFAULT_SETTINGS,
    run: IDLE_RUN,
    route: null,
    error: null,
    brands,
    challenge: null,
  });
}

/** A storage in memory; `fail` makes every call throw. */
function memory(fail = false): ChallengeStorage & { items: Map<string, string> } {
  const items = new Map<string, string>();
  const check = () => {
    if (fail) throw new DOMException("blocked", "SecurityError");
  };
  return {
    items,
    getItem: (key) => (check(), items.get(key) ?? null),
    setItem: (key, value) => (check(), void items.set(key, value)),
    removeItem: (key) => (check(), void items.delete(key)),
  };
}

/** A list the panel would hold: "listed" once `list` is called. */
function source(listed: Challenge[] = CHALLENGES) {
  const target = Object.assign(new EventTarget(), { listed }) satisfies ChallengeSource;
  const list = (challenges: Challenge[]) => {
    target.listed = challenges;
    target.dispatchEvent(new Event("listed"));
  };
  return Object.assign(target, { list });
}

/** `route` driven as `c`'s trip: on its region, from its stops, its request and metrics patched. */
function on(
  c: Challenge,
  route: RouteDocument,
  request: Partial<RouteRequest> = {},
  metrics: Partial<RouteDocument["metrics"]> = {},
): RouteDocument {
  const stops = c.setup?.stops.map(({ lat, lon, label }) => ({ lat, lon, label }));
  return {
    ...route,
    region: c.region,
    request: { ...route.request, stops: stops ?? route.request.stops, ...request },
    metrics: { ...route.metrics, ...metrics },
  };
}

/** `c` with another goal. */
const aiming = (c: Challenge, goal: Challenge["goal"]): Challenge => ({ ...c, goal });

const graded = (
  c: Challenge,
  route: RouteDocument,
  store = storeWith(),
  today: Daily | null = TODAY,
) => sameTrip(c, tripOf(route, store.state), today) && reached(c.goal, route, today);

describe("grading a route", () => {
  it("takes a ratio on the challenge's trip, in its mode, on its region", () => {
    const long = challenge("long-way"); // at 16×
    expect(graded(long, on(long, max))).toBe(true); // 78.0×
    expect(graded(long, on(long, ratio25))).toBe(true); // 25.0×
    expect(graded(aiming(long, { kind: "ratio", min: 30 }), on(long, ratio25))).toBe(false);
    expect(graded(long, max)).toBe(false); // Portland's region and stops
    expect(graded(long, { ...on(long, max), region: "us-t1" })).toBe(false);
    expect(graded(long, on(long, max, { objective: "time" }))).toBe(false);
    expect(graded(long, on(long, max, { reorder: true }))).toBe(false); // the worst order
    expect(graded(long, on(long, max, {}, { ratio: null }))).toBe(false); // a round trip
  });

  it("takes a drive time with its delays", () => {
    const hours = (h: number) =>
      aiming(challenge("slow-way"), { kind: "duration", min_s: h * 3600 });
    expect(slow.metrics.time_s + (slow.metrics.delay_s ?? 0)).toBeCloseTo(22_036, 0); // 6.1 h
    expect(graded(hours(6), on(hours(6), slow))).toBe(true);
    expect(graded(hours(7), on(hours(7), slow))).toBe(false);
    expect(graded(hours(6), on(hours(6), slow, {}, { delay_s: undefined }))).toBe(false); // 4.1 h
    expect(graded(hours(6), on(hours(6), slow, { objective: "distance" }))).toBe(false);
  });

  it("takes no left turn at the ratio", () => {
    const right = challenge("no-left"); // at 1×
    const turns = { ...turning.metrics.turns!, left: 0 };
    expect(turning.metrics.turns?.left).toBe(259);
    expect(graded(right, on(right, turning))).toBe(false);
    expect(graded(right, on(right, turning, {}, { turns }))).toBe(true); // 14.5×
    const longer = aiming(right, { kind: "no_left", min_ratio: 15 });
    expect(graded(longer, on(right, turning, {}, { turns }))).toBe(false);
    expect(graded(right, on(right, max))).toBe(false); // no turns recorded
  });

  it("counts the collect recording's bridges", () => {
    const bridges = challenge("bridges"); // 20
    expect(collect.metrics.items?.bridge).toBe(18);
    expect(graded(bridges, on(bridges, collect))).toBe(false);
    const low = aiming(bridges, { kind: "items", item: "bridge", min: 18 });
    expect(graded(low, on(low, collect))).toBe(true);
    expect(graded(low, on(low, collect, { item: "roundabout" }))).toBe(false); // another item
  });

  it("counts the brand recording's visits, its brand by wikidata id, else by name", () => {
    const mcdonalds = challenge("pilgrimage");
    const starbucks = (min_visits: number): Challenge => ({
      ...mcdonalds,
      setup: { ...mcdonalds.setup!, brand: { id: 1, name: "Starbucks", wikidata: "Q37158" } },
      goal: { kind: "brand", min_visits },
    });
    expect(brand.metrics.brand).toEqual({ id: 229, name: "Starbucks", visits: 14, of: 19 });
    const route = on(mcdonalds, brand);
    expect(graded(starbucks(14), route)).toBe(true); // by name: no brands known
    expect(graded(starbucks(15), route)).toBe(false);
    expect(graded(starbucks(14), route, storeWith([STARBUCKS]))).toBe(true);
    const other = [{ ...STARBUCKS, wikidata: "Q1" }]; // id 229 another brand on this build
    expect(graded(starbucks(14), route, storeWith(other))).toBe(false);
    expect(graded(aiming(mcdonalds, { kind: "brand", min_visits: 1 }), route)).toBe(false);
  });

  it("takes hard mode's ratio only under hard mode", () => {
    const hard = challenge("hard-mode"); // at 3×
    const constraints = { no_left: false, no_right: false, strict: false };
    expect(graded(hard, on(hard, max, { constraints: { ...constraints, hard: true } }))).toBe(true);
    expect(graded(hard, on(hard, max, { constraints }))).toBe(false);
    const short = on(hard, max, { constraints: { ...constraints, hard: true } }, { ratio: 2.9 });
    expect(graded(hard, short)).toBe(false);
  });

  it("takes today's route made worse once, on today's challenge only", () => {
    const worse = challenge("daily-worse");
    const route = (base: string | null, ratio: number, region = "us-t3") => ({
      ...on(worse, max, { stops: [], base_route_id: base }, { ratio }),
      region,
    });
    expect(graded(worse, route("a076702bbc25", 7.1))).toBe(true);
    expect(graded(worse, route("a076702bbc25", 6.0))).toBe(false); // shrunk toward a target
    expect(graded(worse, route("5ea844bffd40", 7.1))).toBe(false); // made worse twice
    expect(graded(worse, route(null, 7.1))).toBe(false);
    expect(graded(worse, route("a076702bbc25", 7.1), storeWith(), null)).toBe(false); // no day
    const yesterday = { ...worse, id: "daily-worse-2026-11-01", date: "2026-11-01" }; // forged
    expect(graded(yesterday, route("a076702bbc25", 7.1))).toBe(false);
    const elsewhere = { ...TODAY, region: "us-t1" }; // the day's route on another build
    expect(graded(worse, route("a076702bbc25", 7.1), storeWith(), elsewhere)).toBe(false);
    expect(graded(worse, route("a076702bbc25", 7.1, "us-t1"), storeWith(), elsewhere)).toBe(true);
  });

  it("takes a trip's stops within 500 m of the challenge's, as many of them", () => {
    const long = challenge("long-way");
    const trip = tripOf(on(long, max), storeWith().state);
    const [start, end] = trip.stops;
    const moved = (dLat: number) => ({ ...trip, stops: [start, { ...end, lat: end.lat + dLat }] });
    expect(sameTrip(long, moved(0.0044))).toBe(true); // 489 m north of Wallace
    expect(sameTrip(long, moved(0.0046))).toBe(false); // 512 m
    expect(sameTrip(long, { ...trip, stops: [start, end, end] })).toBe(false);
  });

  it("takes the worst order's stops between in any order, the start and the end in place", () => {
    const order = challenge("worst-order");
    const trip = tripOf(on(order, max, { reorder: true }), storeWith().state);
    const [a, b, c, d] = trip.stops;
    expect(sameTrip(order, trip)).toBe(true);
    expect(sameTrip(order, { ...trip, stops: [a, c, b, d] })).toBe(true); // as the route visits them
    expect(sameTrip(order, { ...trip, stops: [d, b, c, a] })).toBe(false);
    expect(sameTrip(order, { ...trip, stops: [a, b, b, d] })).toBe(false);
    expect(sameTrip(order, { ...trip, reorder: false })).toBe(false);
  });
});

describe("the completions", () => {
  it("are recorded once, for the routes the store shows, with a toast's line each", () => {
    const store = storeWith();
    const storage = memory();
    const progress = new ChallengeProgress(
      store,
      source(),
      () => TODAY,
      () => storage,
    );
    const completed: string[] = [];
    progress.addEventListener("completed", (event) => {
      completed.push(completedLine((event as CustomEvent<Challenge>).detail));
    });
    const long = challenge("long-way");
    store.update({ route: on(long, max, {}, { ratio: 12 }) });
    expect(completed).toEqual([]);
    store.update({ route: on(long, max) });
    expect(completed).toEqual(["Challenge done. Losantville, IN to Wallace, IN at 16×."]);
    expect(Object.keys(progress.done)).toEqual(["long-way-2026-11-02"]);
    expect(progress.done["long-way-2026-11-02"]).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    store.update({ route: null });
    store.update({ route: on(long, max) });
    expect(completed).toHaveLength(1); // done already
    expect(JSON.parse(storage.items.get(STORAGE_KEY) as string)).toEqual(progress.done);
  });

  it("grade the route shown again when the list comes", () => {
    const store = storeWith();
    const list = source([]);
    const progress = new ChallengeProgress(
      store,
      list,
      () => TODAY,
      () => memory(),
    );
    store.update({ route: on(challenge("long-way"), max) });
    expect(progress.done).toEqual({});
    list.list(CHALLENGES);
    expect(Object.keys(progress.done)).toEqual(["long-way-2026-11-02"]);
  });

  it("keep past days', drop the fixed list's of old, and are reset", () => {
    const storage = memory();
    const old = { "portland-bridges": "2026-10-31", "daily-worse": "2026-10-31" };
    const past = { "bridges-2026-10-20": "2026-10-20", "daily-worse-2026-10-21": "2026-10-21" };
    storage.items.set(STORAGE_KEY, JSON.stringify({ ...old, ...past }));
    const store = storeWith();
    const progress = new ChallengeProgress(
      store,
      source(),
      () => TODAY,
      () => storage,
    );
    expect(progress.done).toEqual(past); // the old ones not counted
    store.update({ route: on(challenge("long-way"), max) });
    const written = JSON.parse(storage.items.get(STORAGE_KEY) as string) as object;
    expect(Object.keys(written).sort()).toEqual(
      [...Object.keys(past), "long-way-2026-11-02"].sort(),
    );
    const again = new ChallengeProgress(
      storeWith(),
      source(),
      () => TODAY,
      () => storage,
    );
    expect(again.done).toEqual(written);
    let changes = 0;
    again.addEventListener("change", () => changes++);
    again.reset();
    expect(again.done).toEqual({});
    expect(changes).toBe(1);
    expect(storage.items.has(STORAGE_KEY)).toBe(false);
  });

  it("ignore junk in the storage", () => {
    const storage = memory();
    for (const junk of [
      "not json",
      "[1]",
      "null",
      '"x"',
      '{"nope": "2026-10-31", "bridges-2026-10-20": 5}',
    ]) {
      storage.items.set(STORAGE_KEY, junk);
      expect(
        new ChallengeProgress(
          storeWith(),
          source(),
          () => TODAY,
          () => storage,
        ).done,
      ).toEqual({});
    }
  });

  it("survive a storage that throws, or none at all, for as long as the page", () => {
    for (const storage of [
      () => memory(true),
      (): ChallengeStorage => {
        throw new Error("none");
      },
    ]) {
      const store = storeWith();
      const progress = new ChallengeProgress(store, source(), () => TODAY, storage);
      expect(progress.done).toEqual({});
      let completed = 0;
      progress.addEventListener("completed", () => completed++);
      store.update({ route: on(challenge("long-way"), max) });
      expect(Object.keys(progress.done)).toEqual(["long-way-2026-11-02"]);
      expect(completed).toBe(1);
      progress.reset();
      expect(progress.done).toEqual({});
    }
  });
});
