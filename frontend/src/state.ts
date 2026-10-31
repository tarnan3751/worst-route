// The app's state (docs/reference/frontend.md, **State**): one typed store on EventTarget. A
// module renders from it with `watch(keys, listener)` and changes it with `update(patch)`,
// which tells the watchers of the keys the patch touches. The route's geometry is not here: it
// changes ten times a second and lives in the route layer (map/routeLayer.ts).
import type {
  CollectItem,
  ErrorBody,
  Metrics,
  Region,
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

/** Where the API put a stop: from a job's `snapped` event or a stored route's stops. */
export interface SnapInfo {
  node: number;
  lat: number;
  lon: number;
  moved_m: number;
  note: string | null;
}

/** A stop as clicked (6 decimals), and where it was snapped once a job has said. */
export interface Stop {
  lat: number;
  lon: number;
  snapped: SnapInfo | null;
}

// The API's limits (docs/reference/api.md, **Requests and errors**).
export const TIERS: readonly Tier[] = ["auto", "T0", "T1", "T2", "T3", "T4"];
export const BUDGET_MAX_S = 120; // BUDGET_TOO_LARGE above
export const LEASH_MAX_KM = 150;
export const TARGET_TOLERANCE = 0.02; // a target is reached within ±2 %
export const TARGET_MAX_DURATION_S = 7 * 24 * 3600;
export const MODES = ["distance", "time", "collect"] as const; // the objectives built
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

/** The objective: the longest, the slowest, or the most of an item. */
export type Mode = (typeof MODES)[number];
/** The turn constraint: none, or left (right) turns avoided, at a kilometre each or, `strict`,
 * none at all. */
export type TurnSetting = "any" | "no_left" | "no_right";

/** The request's settings; null seed is a random one, picked by the server. The target is
 * `target`, with `ratio` (×, more than 1) or `durationS` as its number; each keeps its value
 * while another kind is chosen. `item` is what `collect` collects, kept while another objective
 * is chosen; `strict` counts only with a side to avoid. `mpg` and `gasPriceUsd` are the
 * vehicle, for the route's extra fuel and its cost; `departure` an ISO 8601 time with its
 * offset, null for now. */
export interface Settings {
  seed: number | null;
  budgetS: number;
  tier: Tier;
  leashKm: number | "auto";
  target: TargetKind;
  ratio: number;
  durationS: number;
  objective: Mode;
  item: CollectItem;
  turns: TurnSetting;
  strict: boolean;
  mpg: number;
  gasPriceUsd: number;
  departure: string | null;
  units: Units;
}

export const DEFAULT_SETTINGS: Settings = {
  seed: null,
  budgetS: 20,
  tier: "auto",
  leashKm: "auto",
  target: "max",
  ratio: 20,
  durationS: 9 * 3600,
  objective: "distance",
  item: "bridge",
  turns: "any",
  strict: false,
  mpg: 24,
  gasPriceUsd: 3.89,
  departure: null,
  units: "mi",
};

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

/** A request's objective, item and turn constraints. */
export type RequestMode = Pick<RouteRequest, "objective" | "item" | "constraints">;

/** The request's mode for `settings`: the item only under collect, the constraints only when a
 * side is avoided (the API's own request leaves them out then). */
export function modeOf(settings: Settings): RequestMode {
  const mode: RequestMode = { objective: settings.objective };
  if (settings.objective === "collect") mode.item = settings.item;
  if (settings.turns !== "any") {
    mode.constraints = {
      no_left: settings.turns === "no_left",
      no_right: settings.turns === "no_right",
      strict: settings.strict,
    };
  }
  return mode;
}

export type Phase = "idle" | "starting" | "running" | "done" | "failed";

/** A route's numbers as the stat line shows them: while it grows (a collecting route's `items`
 * among them), and once stored its experienced time's ratio, delays and turns. */
export type RunMetrics = Pick<Metrics, "length_m" | "time_s" | "ratio" | "items"> & {
  time_ratio?: number | null;
  delay_s?: number;
  turns?: Turns;
};

/** The job being run, or the last one, as the stat line shows it. */
export interface RunView {
  phase: Phase;
  jobId: string | null;
  seed: number | null; // the job's, then the shown route's
  iteration: number; // the attempt of the last splice; a finished route's attempts
  elapsedS: number;
  metrics: RunMetrics | null;
  stoppedBecause: StopReason | null;
  resolved: { tier: string; leashKm: number } | null; // what `auto` became, from `snapped`
  goal: Target | null; // the run's target, null for max
  outcome: TargetMetrics | null; // what it achieved, once done
  mode: RequestMode; // the run's objective, item and turn constraints
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
  stoppedBecause: null,
  resolved: null,
  goal: null,
  outcome: null,
  mode: {},
  connection: "open",
  note: null,
};

export interface AppState {
  region: Region | null;
  stops: Stop[];
  settings: Settings;
  run: RunView;
  route: RouteDocument | null; // the route shown, once stored
  error: ErrorBody | null; // the banner's
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
