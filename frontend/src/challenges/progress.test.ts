// Grading without a DOM: each goal kind on the recordings' stored routes (the collect recording's
// 18 bridges, the alphabet recording's A–O, the brand recording's 14 of 19 Starbucks, the max-mode
// recording's 78×) and on a drawing's verdict; what makes a route a challenge's trip; the
// completions kept in a stand-in storage (persisted, reset, junk ignored, a throwing storage
// survived); and the toast line.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { loadStream } from "../__fixtures__/stream";
import type { BrandEntry, Daily, RouteDocument, RouteRequest } from "../api/types";
import type { Par } from "../draw/drawing";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
import { CHALLENGES, type Challenge } from "./list";
import {
  ChallengeProgress,
  STORAGE_KEY,
  completedLine,
  reached,
  sameTrip,
  tripOf,
  type ChallengeStorage,
} from "./progress";

const challenge = (id: string): Challenge => CHALLENGES.find((c) => c.id === id) as Challenge;
const collect = loadStream("portland-collect-stream").route;
const alphabet = loadStream("portland-alphabet-stream").route;
const brand = loadStream("portland-brand-stream").route;
const max = loadStream("portland-stream").route;
const ratio25 = loadStream("portland-ratio-stream").route;
const REGION = loadStream().regions[0]; // Portland's
const drawn = JSON.parse(
  readFileSync(new URL("../__fixtures__/portland-draw.json", import.meta.url), "utf8"),
) as { request: RouteRequest };
const STARBUCKS: BrandEntry = { id: 229, name: "Starbucks", wikidata: "Q37158", locations: 19 };
const TODAY: Daily = {
  date: "2026-10-31",
  region: "us-t3",
  build_id: "6adae6917be1",
  stops: [],
  seed: 20261031,
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
    regions: [REGION],
    region: REGION,
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

/** `route` as another route: its request and metrics patched. */
function variant(
  route: RouteDocument,
  request: Partial<RouteRequest>,
  metrics: Partial<RouteDocument["metrics"]> = {},
  region = route.region,
): RouteDocument {
  return {
    ...route,
    region,
    request: { ...route.request, ...request },
    metrics: { ...route.metrics, ...metrics },
  };
}

const graded = (
  c: Challenge,
  route: RouteDocument,
  store = storeWith(),
  today: Daily | null = TODAY,
) => sameTrip(c, tripOf(route, store.state), today) && reached(c.goal, route, today);

describe("grading a route", () => {
  it("counts the collect recording's bridges against the threshold", () => {
    const bridges = challenge("portland-bridges"); // Powell's → Reed, 20 bridges
    expect(collect.metrics.items?.bridge).toBe(18);
    expect(sameTrip(bridges, tripOf(collect, storeWith().state))).toBe(true); // within 500 m
    expect(graded(bridges, collect)).toBe(false);
    const low = { ...bridges, goal: { kind: "items", item: "bridge", min: 18 } } as Challenge;
    expect(graded(low, collect)).toBe(true);
    const roundabouts = { ...low, settings: { objective: "collect", item: "roundabout" } };
    expect(graded(roundabouts as Challenge, collect)).toBe(false); // another item
  });

  it("does not complete the alphabet on the alphabet recording's A–O", () => {
    const letters = challenge("portland-alphabet");
    expect(alphabet.metrics.alphabet?.sequence).toBe("ABCDEFGHIJKLMNO");
    expect(graded(letters, alphabet)).toBe(false);
    const fifteen = { ...letters, goal: { kind: "alphabet", letters: 15 } } as Challenge;
    expect(graded(fifteen, alphabet)).toBe(true);
    const sixteen = { ...letters, goal: { kind: "alphabet", letters: 16 } } as Challenge;
    expect(graded(sixteen, alphabet)).toBe(false);
  });

  it("counts the brand recording's visits, its brand by wikidata id, else by name", () => {
    const pilgrimage = { ...challenge("oregon-starbucks"), region: "portland" };
    expect(brand.metrics.brand).toEqual({ id: 229, name: "Starbucks", visits: 14, of: 19 });
    const at = (min_visits: number) => ({ ...pilgrimage, goal: { kind: "brand", min_visits } });
    expect(graded(at(14) as Challenge, brand)).toBe(true); // by name: no brands known
    expect(graded(at(15) as Challenge, brand)).toBe(false);
    expect(graded(at(14) as Challenge, brand, storeWith([STARBUCKS]))).toBe(true);
    const other = [{ ...STARBUCKS, wikidata: "Q1" }]; // id 229 another brand on this build
    expect(graded(at(14) as Challenge, brand, storeWith(other))).toBe(false);
    expect(graded(challenge("oregon-starbucks"), brand)).toBe(false); // Portland's build
  });

  it("grades a ratio on any trip in the region, in the longest mode", () => {
    const town = challenge("portland-30x");
    expect(graded(town, max)).toBe(true); // 78.0×, three stops
    expect(graded(town, ratio25)).toBe(false); // 25.0×
    expect(graded(town, variant(max, { objective: "time" }))).toBe(false);
    expect(graded(town, variant(max, {}, {}, "oregon"))).toBe(false);
    expect(graded(town, variant(max, {}, { ratio: null }))).toBe(false); // a round trip
    expect(graded(town, variant(max, { reorder: true }))).toBe(false); // the worst order
    const reordered = { ...town, settings: { reorder: true } };
    expect(graded(reordered, variant(max, { reorder: true }))).toBe(true);
    expect(graded(reordered, max)).toBe(false);
  });

  it("grades hard mode's ratio only under hard mode", () => {
    const hard = challenge("portland-hard");
    const constraints = { no_left: false, no_right: false, strict: false };
    expect(graded(hard, variant(max, { constraints: { ...constraints, hard: true } }))).toBe(true);
    expect(graded(hard, variant(max, { constraints }))).toBe(false);
    const slow = variant(max, { constraints: { ...constraints, hard: true } }, { ratio: 7.9 });
    expect(graded(hard, slow)).toBe(false);
  });

  it("grades today's route made worse once, on its own region, at least the day's ratio", () => {
    const worse = challenge("daily-worse");
    const route = (base: string | null, ratio: number) =>
      variant(max, { stops: [], base_route_id: base }, { ratio }, "us-t3");
    expect(graded(worse, route("a076702bbc25", 7.1))).toBe(true);
    expect(graded(worse, route("a076702bbc25", 6.0))).toBe(false); // shrunk toward a target
    expect(graded(worse, route("5ea844bffd40", 7.1))).toBe(false); // made worse twice
    expect(graded(worse, route(null, 7.1))).toBe(false);
    expect(graded(worse, route("a076702bbc25", 7.1), storeWith(), null)).toBe(false); // no day
    const elsewhere = { ...TODAY, region: "us-t1" }; // the day's route on another build
    expect(graded(worse, route("a076702bbc25", 7.1), storeWith(), elsewhere)).toBe(false);
    const there = variant(
      max,
      { stops: [], base_route_id: "a076702bbc25" },
      { ratio: 7.1 },
      "us-t1",
    );
    expect(graded(worse, there, storeWith(), elsewhere)).toBe(true);
  });

  it("takes a trip's stops within 500 m of the challenge's, as many of them", () => {
    const bridges = challenge("portland-bridges");
    const [start, end] = bridges.stops ?? [];
    const trip = tripOf(collect, storeWith().state);
    const moved = (dLat: number) => ({ ...trip, stops: [start, { ...end, lat: end.lat + dLat }] });
    expect(sameTrip(bridges, moved(0.0044))).toBe(true); // 489 m north of Reed
    expect(sameTrip(bridges, moved(0.0046))).toBe(false); // 512 m
    expect(sameTrip(bridges, { ...trip, stops: [...trip.stops, end] })).toBe(false);
  });

  it("takes a trip on the challenge's leash only when it sets one", () => {
    const draw = challenge("portland-draw"); // a 300 m leash
    const onLeash = variant(max, { stops: drawn.request.stops, leash_km: 0.3 });
    const trip = tripOf(onLeash, storeWith().state);
    expect(trip.leashKm).toBe(0.3);
    expect(sameTrip(draw, trip)).toBe(true);
    expect(sameTrip(draw, { ...trip, leashKm: "auto" })).toBe(false);
    expect(sameTrip(draw, { ...trip, leashKm: 0.5 })).toBe(false);
    const unset = tripOf(variant(max, { leash_km: undefined }), storeWith().state);
    expect(unset.leashKm).toBe("auto"); // the API's default
    const town = challenge("portland-30x"); // no leash
    expect(graded(town, variant(max, { leash_km: 5 }))).toBe(true);
    expect(graded(town, variant(max, { leash_km: "auto" }))).toBe(true);
  });

  it("never completes a drawing's challenge from a stored route", () => {
    const route = variant(max, { stops: drawn.request.stops }, { ratio: 1000 });
    expect(reached(challenge("portland-draw").goal, route, TODAY)).toBe(false);
  });
});

describe("the completions", () => {
  it("are recorded once, for the routes the store shows, with a toast's line each", () => {
    const store = storeWith();
    const storage = memory();
    const progress = new ChallengeProgress(
      store,
      () => TODAY,
      () => storage,
    );
    const completed: string[] = [];
    progress.addEventListener("completed", (event) => {
      completed.push(completedLine((event as CustomEvent<Challenge>).detail));
    });
    store.update({ route: ratio25 });
    expect(completed).toEqual([]);
    store.update({ route: max });
    expect(completed).toEqual(["Challenge completed: A 30× route in town"]);
    expect(Object.keys(progress.done)).toEqual(["portland-30x"]);
    expect(progress.done["portland-30x"]).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    store.update({ route: null });
    store.update({ route: max });
    expect(completed).toHaveLength(1); // done already
    expect(JSON.parse(storage.items.get(STORAGE_KEY) as string)).toEqual(progress.done);
  });

  it("persist, are reset, and ignore what is not theirs", () => {
    const storage = memory();
    const store = storeWith();
    new ChallengeProgress(
      store,
      () => TODAY,
      () => storage,
    ).route(max);
    const again = new ChallengeProgress(
      storeWith(),
      () => TODAY,
      () => storage,
    );
    expect(Object.keys(again.done)).toEqual(["portland-30x"]);
    let changes = 0;
    again.addEventListener("change", () => changes++);
    again.reset();
    expect(again.done).toEqual({});
    expect(changes).toBe(1);
    expect(storage.items.has(STORAGE_KEY)).toBe(false);
    for (const junk of ["not json", "[1]", "null", '{"nope": "2026-10-31", "us-chicago": 5}']) {
      storage.items.set(STORAGE_KEY, junk);
      expect(
        new ChallengeProgress(
          storeWith(),
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
      const progress = new ChallengeProgress(storeWith(), () => TODAY, storage);
      expect(progress.done).toEqual({});
      let completed = 0;
      progress.addEventListener("completed", () => completed++);
      progress.route(max);
      expect(Object.keys(progress.done)).toEqual(["portland-30x"]);
      expect(completed).toBe(1);
      progress.reset();
      expect(progress.done).toEqual({});
    }
  });

  it("complete the drawing's challenge when a drawing on its leash beats the engine, and only then", () => {
    const progress = new ChallengeProgress(
      storeWith(),
      () => TODAY,
      () => memory(),
    );
    const par = (lengthM: number): Par => ({
      phase: "done",
      target: 10,
      jobId: "par1",
      ratio: lengthM / 680,
      lengthM,
      routeId: "e1",
      note: null,
    });
    const finished = { routeId: "d1", ratio: 16.9, lengthM: 11_492 };
    // Powell's → Pioneer Courthouse Square, Portland's build, on the challenge's 300 m leash
    const request = { ...drawn.request, leash_km: 0.3 };
    progress.drawn({ request, finished: null, par: par(5000) }); // not finished
    progress.drawn({ request, finished, par: { ...par(5000), phase: "planning" } });
    progress.drawn({ request, finished, par: par(11_500) }); // a tie: 16.9× each
    progress.drawn({ request, finished, par: par(12_000) }); // the engine wins
    progress.drawn({ request: { ...request, region: "oregon" }, finished, par: par(5000) });
    progress.drawn({ request: { ...request, leash_km: "auto" }, finished, par: par(5000) });
    progress.drawn({ request: { ...request, leash_km: 1 }, finished, par: par(5000) });
    expect(progress.done).toEqual({});
    progress.drawn({ request, finished, par: par(10_000) });
    expect(Object.keys(progress.done)).toEqual(["portland-draw"]);
  });
});
