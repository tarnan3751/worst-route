// Which challenges are done (docs/reference/frontend.md, **Challenges**): every finished route the
// store shows (its `route`, once stored: a run's, a drawing's, one opened by link) is graded
// against each challenge not yet done whose trip it is (`sameTrip`: the region, the mode with its
// item or brand, the worst order as the challenge sets it, its leash when it sets one, and the
// stops within NEAR_M of the challenge's when it fixes them) by the challenge's goal (`reached`); a
// drawing's verdict is graded against the drawing challenges (`drawn`). A challenge newly done is
// recorded with the day under STORAGE_KEY in the browser's storage, every read and write of which
// may fail (a private window, storage blocked), so the page works without it and the badges then
// last as long as the page; "completed" says which, for its toast (`completedLine`). `reset`
// forgets them all. No DOM, so the tests drive it.
import type { BrandEntry, Daily, RouteDocument } from "../api/types";
import { verdictOf, type Drawing } from "../draw/drawing";
import { isoWithOffset } from "../format";
import type { AppState, Store } from "../state";
import { CHALLENGES, type Challenge, type ChallengeBrand, type Goal } from "./list";

export const STORAGE_KEY = "worst-route.challenges";
export const NEAR_M = 500; // a trip's stop counts as the challenge's within this
const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

/** Where the completions are kept: the browser's localStorage, or a stand-in. */
export type ChallengeStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** A finished route as a challenge sees it: its region, objective, item and brand, whether in
 * the worst order, its leash, and stops. */
export interface Trip {
  region: string;
  objective: string;
  item: string | null;
  brand: { wikidata: string | null; name: string } | null;
  reorder: boolean;
  leashKm: number | "auto";
  stops: { lat: number; lon: number }[];
}

/** The distance in metres between two points (haversine, on a sphere of the Earth's mean
 * radius: close enough for "within 500 m"). */
function metresBetween(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6_371_008.8 * Math.asin(Math.sqrt(h));
}

/** Whether `brand` is the challenge's: by its wikidata id when it has one, else by its name. */
function sameBrand(brand: Trip["brand"], wanted: ChallengeBrand): boolean {
  if (brand === null) return false;
  return brand.wikidata !== null ? brand.wikidata === wanted.wikidata : brand.name === wanted.name;
}

/** Whether `trip` is one `challenge` grades: its region (for today's challenge, today's route's,
 * from `today`, null while it is not done), its mode and item or brand, its worst order or none as
 * the challenge sets, its leash when the challenge sets one (that leash exactly: "auto" or any
 * other is not it), and, when the challenge fixes its stops, as many stops each within NEAR_M of
 * the challenge's. */
export function sameTrip(challenge: Challenge, trip: Trip, today: Daily | null = null): boolean {
  const { objective = "distance", item, brand, reorder = false, leashKm } = challenge.settings;
  const region = challenge.region ?? today?.region;
  if (trip.region !== region || trip.objective !== objective) return false;
  if (trip.reorder !== reorder) return false;
  if (leashKm !== undefined && trip.leashKm !== leashKm) return false;
  if (objective === "collect" && trip.item !== item) return false;
  if (objective === "brand" && (brand === undefined || !sameBrand(trip.brand, brand))) return false;
  const { stops } = challenge;
  if (stops === null) return true;
  return (
    trip.stops.length === stops.length &&
    stops.every((stop, k) => metresBetween(stop, trip.stops[k]) <= NEAR_M)
  );
}

/** A stored route as a challenge sees it; its brand's wikidata id from `brands` when they are its
 * region's (a brand's id is its build's), else none, so its name decides. */
export function tripOf(route: RouteDocument, state: Readonly<AppState>): Trip {
  const { request, metrics, region } = route;
  const visited = metrics.brand ?? null;
  const brands: BrandEntry[] = state.region?.region === region ? (state.brands ?? []) : [];
  const listed = visited && brands.find(({ id }) => id === visited.id);
  return {
    region,
    objective: request.objective ?? "distance",
    item: request.item ?? null,
    brand: visited && { wikidata: listed?.wikidata ?? null, name: visited.name },
    reorder: request.reorder === true,
    leashKm: request.leash_km ?? "auto",
    stops: request.stops ?? route.stops,
  };
}

/** Whether stored route `route` reaches `goal`; `today` is today's route (for the goal of making
 * it worse), null while there is none done. A drawing's goal is judged on its verdict, never
 * here. */
export function reached(goal: Goal, route: RouteDocument, today: Daily | null): boolean {
  const { metrics, request } = route;
  const ratio = metrics.ratio ?? 0; // a round trip has none
  switch (goal.kind) {
    case "ratio":
      return ratio >= goal.min;
    case "items":
      return (metrics.items?.[goal.item] ?? 0) >= goal.min;
    case "brand":
      return (metrics.brand?.visits ?? 0) >= goal.min_visits;
    case "alphabet":
      return metrics.alphabet?.sequence.startsWith(ALPHABET.slice(0, goal.letters)) ?? false;
    case "hard":
      return request.constraints?.hard === true && ratio >= goal.min_ratio;
    case "daily_worse":
      return (
        today?.route_id != null &&
        request.base_route_id === today.route_id &&
        ratio >= (today.ratio ?? Infinity)
      );
    case "drawn":
      return false;
  }
}

/** The toast a challenge completed shows. */
export function completedLine(challenge: Challenge): string {
  return `Challenge done. ${challenge.title}.`;
}

/** Which challenges are done, each with the day it was (YYYY-MM-DD, the browser's): "change" when
 * that changes, and "completed" (a CustomEvent with the challenge) for each one newly done. */
export class ChallengeProgress extends EventTarget {
  done: Record<string, string>;
  readonly #store: Store;
  readonly #storage: () => ChallengeStorage;
  readonly #today: () => Daily | null;
  readonly #challenges: readonly Challenge[];

  /** Grades every route `store` shows; `today` gives today's route once done, `storage` the
   * storage (reaching it may throw too). */
  constructor(
    store: Store,
    today: () => Daily | null,
    storage: () => ChallengeStorage = () => localStorage,
    challenges: readonly Challenge[] = CHALLENGES,
  ) {
    super();
    this.#store = store;
    this.#today = today;
    this.#storage = storage;
    this.#challenges = challenges;
    this.done = this.#read();
    store.watch(["route"], ({ route }) => {
      if (route !== null) this.route(route);
    });
  }

  /** Grade stored route `route`. */
  route(route: RouteDocument): void {
    const trip = tripOf(route, this.#store.state);
    for (const challenge of this.#challenges) {
      const today = this.#today();
      if (sameTrip(challenge, trip, today) && reached(challenge.goal, route, today)) {
        this.#record(challenge);
      }
    }
  }

  /** Grade a drawing once it has its verdict: a drawing that beat the engine completes the
   * drawing challenges whose trip it is (a drawing is the longest route). */
  drawn(drawing: Pick<Drawing, "request" | "finished" | "par">): void {
    const { request } = drawing;
    if (request === null || verdictOf(drawing, this.#store.state.settings.units) !== "you") return;
    const trip: Trip = {
      region: request.region,
      objective: "distance",
      item: null,
      brand: null,
      reorder: false, // a drawing takes the stops in their order
      leashKm: request.leash_km ?? "auto",
      stops: request.stops ?? [],
    };
    for (const challenge of this.#challenges) {
      if (challenge.goal.kind === "drawn" && sameTrip(challenge, trip)) this.#record(challenge);
    }
  }

  /** Forget every completion. */
  reset(): void {
    this.done = {};
    try {
      this.#storage().removeItem(STORAGE_KEY);
    } catch {
      // the badges were only the page's
    }
    this.dispatchEvent(new Event("change"));
  }

  #record(challenge: Challenge): void {
    if (challenge.id in this.done) return;
    this.done = { ...this.done, [challenge.id]: isoWithOffset(new Date()).slice(0, 10) };
    try {
      this.#storage().setItem(STORAGE_KEY, JSON.stringify(this.done));
    } catch {
      // kept for the page only
    }
    this.dispatchEvent(new CustomEvent<Challenge>("completed", { detail: challenge }));
    this.dispatchEvent(new Event("change"));
  }

  /** The completions stored, the challenges' only (none when there is no storage). */
  #read(): Record<string, string> {
    let stored: unknown;
    try {
      stored = JSON.parse(this.#storage().getItem(STORAGE_KEY) ?? "{}");
    } catch {
      return {};
    }
    const done: Record<string, string> = {};
    if (typeof stored !== "object" || stored === null) return done;
    for (const { id } of this.#challenges) {
      const day = (stored as Record<string, unknown>)[id];
      if (typeof day === "string") done[id] = day;
    }
    return done;
  }
}
