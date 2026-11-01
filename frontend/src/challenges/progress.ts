// Grading the challenges (docs/reference/frontend.md, **Challenges**): whether a finished route is
// a challenge's trip (`sameTrip`: the region, the mode with its item or brand, the worst order as
// the challenge sets it, and the stops within NEAR_M of the challenge's when it fixes them;
// `tripOf` reads it from a stored route), and whether it reaches the challenge's goal (`reached`).
// No DOM, so the tests drive it.
import type { BrandEntry, Daily, RouteDocument } from "../api/types";
import type { AppState } from "../state";
import type { Challenge, ChallengeBrand, Goal } from "./list";

export const NEAR_M = 500; // a trip's stop counts as the challenge's within this
const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

/** A finished route as a challenge sees it: its region, objective, item and brand, whether in
 * the worst order, and stops. */
export interface Trip {
  region: string;
  objective: string;
  item: string | null;
  brand: { wikidata: string | null; name: string } | null;
  reorder: boolean;
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
 * the challenge sets, and, when the challenge fixes its stops, as many stops each within NEAR_M of
 * the challenge's. */
export function sameTrip(challenge: Challenge, trip: Trip, today: Daily | null = null): boolean {
  const { objective = "distance", item, brand, reorder = false } = challenge.settings;
  const region = challenge.region ?? today?.region;
  if (trip.region !== region || trip.objective !== objective) return false;
  if (trip.reorder !== reorder) return false;
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
