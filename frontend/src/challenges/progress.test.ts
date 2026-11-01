// Grading without a DOM: each goal kind on the recordings' stored routes (the collect recording's
// 18 bridges, the alphabet recording's A–O, the brand recording's 14 of 19 Starbucks, the max-mode
// recording's 78×), and what makes a route a challenge's trip.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { loadStream } from "../__fixtures__/stream";
import type { BrandEntry, Daily, RouteDocument, RouteRequest } from "../api/types";
import { DEFAULT_SETTINGS, IDLE_RUN, Store } from "../state";
import { CHALLENGES, type Challenge } from "./list";
import { reached, sameTrip, tripOf } from "./progress";

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
  });
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

  it("never completes a drawing's challenge from a stored route", () => {
    const route = variant(max, { stops: drawn.request.stops }, { ratio: 1000 });
    expect(reached(challenge("portland-draw").goal, route, TODAY)).toBe(false);
  });
});
