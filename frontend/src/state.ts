// The app's state (docs/reference/frontend.md, **State**): one typed store on EventTarget. A
// module renders from it with `watch(keys, listener)` and changes it with `update(patch)`,
// which tells the watchers of the keys the patch touches. The route's geometry is not here: it
// changes ten times a second and lives in the route layer (map/routeLayer.ts).
import type {
  AlphabetLetters,
  BrandEntry,
  BrandVisits,
  CertifiedProgress,
  CertifiedRun,
  CollectItem,
  ErrorBody,
  Metrics,
  Region,
  RegionPick,
  RouteDocument,
  RouteRequest,
  StopReason,
  Target,
  TargetKind,
  TargetMetrics,
  Tier,
  Turns,
  Vehicle,
} from "./api/types";
import { isoWithOffset, type Units } from "./format";
import type { ThemeSetting } from "./theme/resolve";

/** Where the API put a stop: from a job's `snapped` event or a stored route's stops. */
export interface SnapInfo {
  node: number;
  lat: number;
  lon: number;
  moved_m: number;
  note: string | null;
}

/** A stop as clicked (6 decimals), the name of the city whose label was clicked (map/cities.ts),
 * and where it was snapped once a job has said. */
export interface Stop {
  lat: number;
  lon: number;
  label?: string;
  snapped: SnapInfo | null;
}

// The API's limits (docs/reference/api.md, **Requests and errors**).
export const TIERS: readonly Tier[] = ["auto", "T0", "T1", "T2", "T3", "T4"];
export const BUDGET_MAX_S = 120; // BUDGET_TOO_LARGE above
export const LEASH_MAX_KM = 5000;
export const TARGET_TOLERANCE = 0.02; // a target is reached within ±2 %
export const TARGET_MAX_DURATION_S = 7 * 24 * 3600;
export const MAX_PORTFOLIO = 4; // the most seeds a job runs on any server (GET /config: this one's)
export const MODES = ["distance", "time", "collect", "brand", "alphabet"] as const; // the objectives built
export const ITEMS: readonly CollectItem[] = [
  "toll_booth",
  "level_crossing",
  "roundabout",
  "bridge",
];
// The vehicle the panel takes (the API takes any mpg above 0 and any price from 0).
export const MPG_MIN = 5;
export const MPG_MAX = 100;
export const GAS_PRICE_MIN = 0.5;
export const GAS_PRICE_MAX = 20;

/** The objective: the longest, the slowest, the most of an item, the most locations of a brand,
 * or the most letters of the alphabet. */
export type Mode = (typeof MODES)[number];
/** The turn constraint: none, or left (right) turns avoided, at a kilometre each (about half as
 * many a km as without, but about as many as the other side's: a route that never repeats a node
 * tends to turn both ways alike, a tendency rather than a law, since a route can spiral), or,
 * `strict`, none at all. */
export type TurnSetting = "any" | "no_left" | "no_right";

/** The request's settings; null seed is a random one, picked by the server, and null region the one
 * the server picks for the stops (`POST /regions/pick`), else the served region pinned. The target
 * is `target`, with `ratio` (×, more than 1) or `durationS` as its number; each keeps its value
 * while another kind is chosen. `item` is what `collect` collects, kept while another objective is
 * chosen, and `brand` (a brand's id from GET /brands, null until the region's brands are known)
 * what `brand` visits, kept the same way; `strict` counts only with a side to avoid, and `hard`
 * (hard mode: each road in one stretch) with or without one. `reorder` visits the stops between
 * the start and the end in the worst order (not under the alphabet route), and `portfolio` runs
 * that many seeds at once and keeps the best (not under the alphabet route either). `certified`
 * asks for the provably longest route in a small area, under the longest only, which then takes
 * no target, turn constraint, hard mode, worst order or second seed (`certifies`). `mpg` and
 * `gasPriceUsd` are the vehicle, for the route's extra fuel and its cost; `departure` an ISO 8601
 * time with its offset, null for now. `theme` is the look, a theme's id or "auto" (theme/resolve.ts),
 * the device's own: never in a request or a link. */
export interface Settings {
  seed: number | null;
  region: string | null;
  budgetS: number;
  tier: Tier;
  leashKm: number | "auto";
  target: TargetKind;
  ratio: number;
  durationS: number;
  objective: Mode;
  item: CollectItem;
  brand: number | null;
  turns: TurnSetting;
  strict: boolean;
  hard: boolean;
  reorder: boolean;
  certified: boolean;
  portfolio: number;
  mpg: number;
  gasPriceUsd: number;
  departure: string | null;
  units: Units;
  theme: ThemeSetting;
}

export const DEFAULT_SETTINGS: Settings = {
  seed: null,
  region: null,
  budgetS: 20,
  tier: "auto",
  leashKm: "auto",
  target: "max",
  ratio: 20,
  durationS: 9 * 3600,
  objective: "distance",
  item: "bridge",
  brand: null,
  turns: "any",
  strict: false,
  hard: false,
  reorder: false,
  certified: false,
  portfolio: 1,
  mpg: 24,
  gasPriceUsd: 3.89,
  departure: null,
  units: "mi",
  theme: "auto",
};

/** Whether `objective` takes a target of `kind`: the alphabet route none but as bad as possible,
 * a collect or brand run no ratio (it counts items or visits, not metres), as the API refuses. */
export function takesTarget(objective: Mode, kind: TargetKind): boolean {
  if (objective === "alphabet") return kind === "max";
  return kind !== "ratio" || (objective !== "collect" && objective !== "brand");
}

/** Whether `settings` ask for a certified route: Certified ticked under the longest (the box keeps
 * its tick under another mode, where it counts for nothing). */
export function certifies(settings: Settings): boolean {
  return settings.certified && settings.objective === "distance";
}

/** The request's target for `settings`. */
export function targetOf(settings: Settings): Target {
  if (settings.target === "ratio") return { kind: "ratio", value: settings.ratio };
  if (settings.target === "duration") return { kind: "duration", seconds: settings.durationS };
  return { kind: "max" };
}

/** The request's vehicle for `settings`. */
export function vehicleOf(settings: Settings): Vehicle {
  return { mpg: settings.mpg, gas_price_usd: settings.gasPriceUsd };
}

/** The request's departure for `settings`: the time set, else now, at the browser's offset. */
export function departureOf(settings: Settings): string {
  return settings.departure ?? isoWithOffset(new Date());
}

/** A request's objective, item, brand and constraints. */
export type RequestMode = Pick<RouteRequest, "objective" | "item" | "brand" | "constraints">;

/** The request's mode for `settings`: the item only under collect, the brand only under brand,
 * the constraints only when a side is avoided or hard mode is on, and `hard` in them only when
 * on (the API's own request leaves them out then). */
export function modeOf(settings: Settings): RequestMode {
  const mode: RequestMode = { objective: settings.objective };
  if (settings.objective === "collect") mode.item = settings.item;
  if (settings.objective === "brand") mode.brand = settings.brand;
  if (settings.turns !== "any" || settings.hard) {
    mode.constraints = {
      no_left: settings.turns === "no_left",
      no_right: settings.turns === "no_right",
      strict: settings.turns !== "any" && settings.strict,
    };
    if (settings.hard) mode.constraints.hard = true;
  }
  return mode;
}

/** Where the run stands; `drawing` while the user draws a route by hand (draw/drawing.ts). */
export type Phase = "idle" | "starting" | "running" | "done" | "failed" | "drawing";

/** A route's numbers as the stat line shows them: while it grows (a collecting route's `items`,
 * a pilgrimage's `brand` and an alphabet route's `alphabet` among them), and once stored its
 * experienced time's ratio, delays and turns. */
export type RunMetrics = Pick<Metrics, "length_m" | "time_s" | "ratio" | "items"> & {
  time_ratio?: number | null;
  delay_s?: number;
  turns?: Turns;
  brand?: BrandVisits | null;
  alphabet?: AlphabetLetters | null;
};

/** The job being run, or the last one, as the stat line shows it. */
export interface RunView {
  phase: Phase;
  jobId: string | null;
  seed: number | null; // the job's (a portfolio's first), then the shown route's
  iteration: number; // the attempt of the last splice; a finished route's attempts
  elapsedS: number;
  metrics: RunMetrics | null;
  bestRatio: number | null; // the best route's ratio while it scores above the current route
  stoppedBecause: StopReason | null;
  resolved: { tier: string; leashKm: number } | null; // what `auto` became, from `snapped`
  goal: Target | null; // the run's target, null for max
  outcome: TargetMetrics | null; // what it achieved, once done
  mode: RequestMode; // the run's objective, item, brand and constraints
  reorder: boolean; // the run asked for the worst order
  portfolio: number; // the seeds it runs, or the shown route's portfolio ran; 1 for one search
  leader: number; // the index of a portfolio's leading search, whose seed is `seed` plus it
  certified: CertifiedProgress | CertifiedRun | null; // a certified run's bound, or a route's
  order: number[] | null; // the list's index of each stop, in the order visited; null: the list's
  connection: "open" | "reconnecting" | "lost";
  note: string | null;
}

export const IDLE_RUN: RunView = {
  phase: "idle",
  jobId: null,
  seed: null,
  iteration: 0,
  elapsedS: 0,
  metrics: null,
  bestRatio: null,
  stoppedBecause: null,
  resolved: null,
  goal: null,
  outcome: null,
  mode: {},
  reorder: false,
  portfolio: 1,
  leader: 0,
  certified: null,
  order: null,
  connection: "open",
  note: null,
};

export interface AppState {
  regions: Region[] | null; // the served ones, once GET /regions has answered, in its order
  region: Region | null; // the one trips plan on: the pinned one, else the pick's; null till known
  pick: RegionPick | "picking" | null; // the server's pick for the stops, or being asked
  stops: Stop[];
  settings: Settings;
  run: RunView;
  route: RouteDocument | null; // the route shown, once stored
  error: ErrorBody | null; // the banner's
  brands: BrandEntry[] | null; // the region's, once GET /brands has answered
  challenge: string | null; // the challenge set up, while what it set is unchanged (challenges/)
}

export type StateKey = keyof AppState;

export class Store extends EventTarget {
  #state: AppState;

  constructor(initial: AppState) {
    super();
    this.#state = initial;
  }

  get state(): Readonly<AppState> {
    return this.#state;
  }

  update(patch: Partial<AppState>): void {
    this.#state = { ...this.#state, ...patch };
    const keys = Object.keys(patch) as StateKey[];
    this.dispatchEvent(new CustomEvent<StateKey[]>("change", { detail: keys }));
  }

  updateRun(patch: Partial<RunView>): void {
    this.update({ run: { ...this.#state.run, ...patch } });
  }

  updateSettings(patch: Partial<Settings>): void {
    this.update({ settings: { ...this.#state.settings, ...patch } });
  }

  /** Call `listener` now, and after every update that touches one of `keys`. */
  watch(keys: StateKey[], listener: (state: Readonly<AppState>) => void): void {
    listener(this.#state);
    this.addEventListener("change", (event) => {
      if ((event as CustomEvent<StateKey[]>).detail.some((key) => keys.includes(key))) {
        listener(this.#state);
      }
    });
  }
}
