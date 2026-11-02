// Which challenges are done (docs/reference/frontend.md, **Challenges**): every finished route the
// store shows (its `route`, once stored: a run's, a drawing's, one opened by link) is graded
// against each challenge listed and not yet done whose trip it is (`sameTrip`: the region, the mode
// with its item or brand, the worst order as the challenge sets it, and the stops within NEAR_M of
// the challenge's; today's route made worse only on today's challenge) by the challenge's goal
// (`reached`), and graded again when the list changes, since a route may be shown before the list
// comes. A challenge newly done is recorded with the day under STORAGE_KEY in the browser's
// storage, by its id, which carries its day, so past days' badges keep (what is not a dated id,
// as the fixed list's of old, is not counted and goes at the next write); every read and write
// may fail (a private window, storage blocked), so the page works without it and the badges then
// last as long as the page; "completed" says which, for its toast (`completedLine`). `reset`
// forgets them all. A run of the challenge set up that ends short of its bar says so once, "short",
// for its toast (`shortLine`), unless the challenge is done already. While the route shown or the
// run going is a challenge's trip, the challenge's row says how it stands against the bar
// (`progressLine`). No DOM, so the tests drive it.
import type {
  BrandEntry,
  BrandVisits,
  ChallengeBrand,
  ChallengeGoal,
  Constraints,
  Daily,
  DocumentMetrics,
  RouteDocument,
} from "../api/types";
import { formatCount, formatItems, formatRatio, isoWithOffset } from "../format";
import type { AppState, Store } from "../state";
import { dateOf, type Challenge } from "./list";

export const STORAGE_KEY = "worst-route.challenges";
export const NEAR_M = 500; // a trip's stop counts as the challenge's within this

/** Where the completions are kept: the browser's localStorage, or a stand-in. */
export type ChallengeStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** The challenges to grade (the panel's list): "listed" when they change. */
export interface ChallengeSource extends EventTarget {
  readonly listed: readonly Challenge[];
}

/** A finished route as a challenge sees it: its region, objective, item and brand, whether in
 * the worst order, and its stops. */
export interface Trip {
  region: string;
  objective: string;
  item: string | null;
  brand: { wikidata: string | null; name: string } | null;
  reorder: boolean;
  stops: { lat: number; lon: number }[];
}

type Point = { lat: number; lon: number };

/** The distance in metres between two points (haversine, on a sphere of the Earth's mean
 * radius: close enough for "within 500 m"). */
function metresBetween(a: Point, b: Point): number {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6_371_008.8 * Math.asin(Math.sqrt(h));
}

const near = (a: Point, b: Point): boolean => metresBetween(a, b) <= NEAR_M;

/** Whether `stops` are `wanted`, as many, each within NEAR_M of its own; in the worst order
 * (`anyOrder`) the start and the end in place and the stops between in any order, as a route's
 * request lists them in the order it visits them. */
function sameStops(wanted: Point[], stops: Point[], anyOrder: boolean): boolean {
  if (stops.length !== wanted.length) return false;
  if (!anyOrder) return wanted.every((stop, k) => near(stop, stops[k]));
  const last = wanted.length - 1;
  if (!near(wanted[0], stops[0]) || !near(wanted[last], stops[last])) return false;
  const between = stops.slice(1, last);
  return wanted.slice(1, last).every((stop) => {
    const k = between.findIndex((other) => near(stop, other));
    if (k < 0) return false;
    between.splice(k, 1); // one of the challenge's each
    return true;
  });
}

/** Whether `brand` is the challenge's: by its wikidata id when it has one, else by its name. */
function sameBrand(brand: Trip["brand"], wanted: ChallengeBrand): boolean {
  if (brand === null) return false;
  return brand.wikidata !== null ? brand.wikidata === wanted.wikidata : brand.name === wanted.name;
}

/** Whether `trip` is one `challenge` grades: its region, its mode and item or brand, the worst
 * order or none as the challenge sets, and its stops (`sameStops`); for today's route made worse
 * (no setup), the longest route on the region of today's route (`today`, null while it is not
 * done), the challenge today's. */
export function sameTrip(challenge: Challenge, trip: Trip, today: Daily | null = null): boolean {
  const { setup } = challenge;
  if (setup === null) {
    return (
      challenge.date === today?.date &&
      trip.region === today.region &&
      trip.objective === "distance" &&
      !trip.reorder
    );
  }
  if (trip.region !== challenge.region || trip.objective !== setup.objective) return false;
  if (trip.reorder !== setup.reorder) return false;
  if (setup.objective === "collect" && trip.item !== setup.item) return false;
  if (setup.objective === "brand" && (setup.brand === null || !sameBrand(trip.brand, setup.brand)))
    return false;
  return sameStops(setup.stops, trip.stops, setup.reorder);
}

/** The brand a route on `region` visits as a challenge sees it; its wikidata id from `brands`
 * when they are its region's (a brand's id is its build's), else none, so its name decides. */
function brandSeen(
  visited: BrandVisits | null,
  region: string,
  state: Readonly<AppState>,
): Trip["brand"] {
  const brands: BrandEntry[] = state.region?.region === region ? (state.brands ?? []) : [];
  const listed = visited && brands.find(({ id }) => id === visited.id);
  return visited && { wikidata: listed?.wikidata ?? null, name: visited.name };
}

/** A stored route as a challenge sees it. */
export function tripOf(route: RouteDocument, state: Readonly<AppState>): Trip {
  const { request, metrics, region } = route;
  return {
    region,
    objective: request.objective ?? "distance",
    item: request.item ?? null,
    brand: brandSeen(metrics.brand ?? null, region, state),
    reorder: request.reorder === true,
    stops: request.stops ?? route.stops,
  };
}

/** Whether stored route `route` reaches `goal`; `today` is today's route (for the goal of making
 * it worse), null while there is none done. */
export function reached(goal: ChallengeGoal, route: RouteDocument, today: Daily | null): boolean {
  const { metrics, request } = route;
  const ratio = metrics.ratio ?? 0; // a round trip has none
  switch (goal.kind) {
    case "ratio":
      return ratio >= goal.min;
    case "duration":
      return metrics.time_s + (metrics.delay_s ?? 0) >= goal.min_s;
    case "no_left":
      return metrics.turns?.left === 0 && ratio >= goal.min_ratio;
    case "items":
      return (metrics.items?.[goal.item] ?? 0) >= goal.min;
    case "brand":
      return (metrics.brand?.visits ?? 0) >= goal.min_visits;
    case "hard":
      return request.constraints?.hard === true && ratio >= goal.min_ratio;
    case "daily_worse":
      return (
        today?.route_id != null &&
        request.base_route_id === today.route_id &&
        ratio >= (today.ratio ?? Infinity)
      );
  }
}

/** The toast a challenge completed shows. */
export function completedLine(challenge: Challenge): string {
  return `Challenge done. ${challenge.title}.`;
}

/** A route's numbers as a challenge's standing reads them: a stored route's, or a run's so far. */
type Seen = Pick<DocumentMetrics, "ratio" | "time_s" | "delay_s" | "items" | "brand">;

const compact = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

/** A bar's ratio, as the titles write it: `15×`, `1.4×`. */
const barRatio = (ratio: number): string => `${compact.format(ratio)}×`;

/** A ratio so far, to the tenth below, so it never reads as a bar it has not reached. */
const ratioSoFar = (ratio: number): string => formatRatio(Math.floor(ratio * 10 + 1e-9) / 10);

/** Whole hours driven with the delays, the hour below. */
const hoursOf = ({ time_s, delay_s }: Seen): string =>
  formatCount(Math.floor((time_s + (delay_s ?? 0)) / 3600));

/** The route shown, or the run going once it has numbers, as a challenge sees it: its trip, its
 * numbers and its constraints; null for neither. */
function shownOf(
  state: Readonly<AppState>,
): { trip: Trip; seen: Seen; constraints: Constraints } | null {
  const { route, run, region } = state;
  if (route !== null) {
    const constraints = route.request.constraints ?? {};
    return { trip: tripOf(route, state), seen: route.metrics, constraints };
  }
  if (run.phase !== "running" || run.metrics === null || region === null) return null;
  const trip: Trip = {
    region: region.region,
    objective: run.mode.objective ?? "distance",
    item: run.mode.item ?? null,
    brand: brandSeen(run.metrics.brand ?? null, region.region, state),
    reorder: run.reorder,
    stops: state.stops,
  };
  return { trip, seen: run.metrics, constraints: run.mode.constraints ?? {} };
}

/** What `challenge`'s row says of its standing: "Done" once `done`; else, while the route shown
 * (or the run going) is its trip, how it stands against the bar ("12.3× so far · needs 15×",
 * "41 h of 50 h", "412 of 600 bridges", "7 of 11 Dollar General", "no lefts · 1.3× of 1.4×",
 * "hard mode · 1.6× of 2×"), no left turns only under strict no-left and hard mode's only under
 * hard mode, as nothing else can reach them; else null, and always for today's route made worse,
 * whose bar is the route a run continues. */
export function progressLine(
  challenge: Challenge,
  state: Readonly<AppState>,
  done: boolean,
): string | null {
  if (done) return "Done";
  const shown = shownOf(state);
  if (challenge.setup === null || shown === null || !sameTrip(challenge, shown.trip)) return null;
  const { seen, constraints } = shown;
  const { goal } = challenge;
  const ratio = ratioSoFar(seen.ratio ?? 0);
  switch (goal.kind) {
    case "ratio":
      return `${ratio} so far · needs ${barRatio(goal.min)}`;
    case "duration":
      return `${hoursOf(seen)} h of ${formatCount(goal.min_s / 3600)} h`;
    case "items":
      return `${formatCount(seen.items?.[goal.item] ?? 0)} of ${formatItems(goal.min, goal.item)}`;
    case "brand": {
      const brand = challenge.setup.brand; // a pilgrimage's, whose trip has it
      const visits = formatCount(seen.brand?.visits ?? 0);
      return brand && `${visits} of ${formatCount(goal.min_visits)} ${brand.name}`;
    }
    case "no_left":
      if (!constraints.no_left || !constraints.strict) return null;
      return `no lefts · ${ratio} of ${barRatio(goal.min_ratio)}`;
    case "hard":
      return constraints.hard ? `hard mode · ${ratio} of ${barRatio(goal.min_ratio)}` : null;
    case "daily_worse":
      return null;
  }
}

const SHORT_ADVICE = "More time, another run or Make it worse may get there.";

/** The toast a run of `challenge` shows when its route ends short of the bar: "12.3×, the
 * challenge needs 15×. More time, another run or Make it worse may get there."; null for today's
 * route made worse. */
export function shortLine(challenge: Challenge, route: RouteDocument): string | null {
  const { goal, setup } = challenge;
  const seen = route.metrics;
  let have: string;
  let needs: string;
  switch (goal.kind) {
    case "ratio":
      [have, needs] = [ratioSoFar(seen.ratio ?? 0), barRatio(goal.min)];
      break;
    case "duration":
      [have, needs] = [`${hoursOf(seen)} h`, `${formatCount(goal.min_s / 3600)} h`];
      break;
    case "items":
      [have, needs] = [formatItems(seen.items?.[goal.item] ?? 0, goal.item), formatCount(goal.min)];
      break;
    case "brand":
      if (!setup?.brand) return null; // a pilgrimage's always has one
      have = `${formatCount(seen.brand?.visits ?? 0)} ${setup.brand.name}`;
      needs = formatCount(goal.min_visits);
      break;
    case "no_left":
    case "hard":
      [have, needs] = [ratioSoFar(seen.ratio ?? 0), barRatio(goal.min_ratio)];
      break;
    case "daily_worse":
      return null;
  }
  return `${have}, the challenge needs ${needs}. ${SHORT_ADVICE}`;
}

/** Which challenges are done, each with the day it was (YYYY-MM-DD, the browser's): "change" when
 * that changes, and "completed" (a CustomEvent with the challenge) for each one newly done; and
 * "short" (a CustomEvent with its toast's line) when a run of the challenge set up, not done yet,
 * ends short. */
export class ChallengeProgress extends EventTarget {
  done: Record<string, string>;
  readonly #store: Store;
  readonly #source: ChallengeSource;
  readonly #storage: () => ChallengeStorage;
  readonly #today: () => Daily | null;
  #pending: string | null = null; // the challenge set up when the run going started

  /** Grades every route `store` shows against the challenges `source` lists, and the route shown
   * again whenever they change; `today` gives today's route once done, `storage` the storage
   * (reaching it may throw too). */
  constructor(
    store: Store,
    source: ChallengeSource,
    today: () => Daily | null,
    storage: () => ChallengeStorage = () => localStorage,
  ) {
    super();
    this.#store = store;
    this.#source = source;
    this.#today = today;
    this.#storage = storage;
    this.done = this.#read();
    store.watch(["route"], () => this.#grade());
    source.addEventListener("listed", () => this.#grade());
    let phase = store.state.run.phase;
    let shown = store.state.route;
    store.watch(["run", "route"], ({ run, route, challenge }) => {
      if (run.phase === "starting" && phase !== "starting") this.#pending = challenge;
      else if (route !== shown && route !== null) this.#ended(route);
      else if (run.phase !== "starting" && run.phase !== "running") this.#pending = null;
      phase = run.phase;
      shown = route;
    });
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

  /** Grade the route shown, if any. */
  #grade(): void {
    const { route } = this.#store.state;
    if (route === null) return;
    const trip = tripOf(route, this.#store.state);
    const today = this.#today();
    for (const challenge of this.#source.listed) {
      if (sameTrip(challenge, trip, today) && reached(challenge.goal, route, today)) {
        this.#record(challenge);
      }
    }
  }

  /** A run has ended on `route`: "short", once, when the challenge set up as it started, not done
   * already (its badge holder gets no nag), is its trip and the route short of its bar. */
  #ended(route: RouteDocument): void {
    const id = this.#pending;
    this.#pending = null;
    const challenge = this.#source.listed.find((c) => c.id === id);
    if (challenge === undefined || challenge.id in this.done) return;
    const today = this.#today();
    const trip = tripOf(route, this.#store.state);
    if (!sameTrip(challenge, trip, today) || reached(challenge.goal, route, today)) return;
    const line = shortLine(challenge, route);
    if (line !== null) this.dispatchEvent(new CustomEvent<string>("short", { detail: line }));
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

  /** The completions stored, of dated ids only (none when there is no storage). */
  #read(): Record<string, string> {
    let stored: unknown;
    try {
      stored = JSON.parse(this.#storage().getItem(STORAGE_KEY) ?? "{}");
    } catch {
      return {};
    }
    const done: Record<string, string> = {};
    if (typeof stored !== "object" || stored === null) return done;
    for (const [id, day] of Object.entries(stored)) {
      if (dateOf(id) !== null && typeof day === "string") done[id] = day;
    }
    return done;
  }
}
