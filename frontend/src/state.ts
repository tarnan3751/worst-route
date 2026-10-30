// The app's state (docs/reference/frontend.md, **State**): one typed store on EventTarget. A
// module renders from it with `watch(keys, listener)` and changes it with `update(patch)`,
// which tells the watchers of the keys the patch touches. The route's geometry is not here: it
// changes ten times a second and lives in the route layer (map/routeLayer.ts).
import type {
  ErrorBody,
  Metrics,
  Objective,
  Region,
  RouteDocument,
  StopReason,
  TargetKind,
  Tier,
} from "./api/types";
import type { Units } from "./format";

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

/** The request's settings; null seed is a random one, picked by the server. */
export interface Settings {
  seed: number | null;
  budgetS: number;
  tier: Tier;
  leashKm: number | "auto";
  target: TargetKind;
  objective: Objective;
  units: Units;
}

export const DEFAULT_SETTINGS: Settings = {
  seed: null,
  budgetS: 20,
  tier: "auto",
  leashKm: "auto",
  target: "max",
  objective: "distance",
  units: "mi",
};

export type Phase = "idle" | "starting" | "running" | "done" | "failed";

/** The job being run, or the last one, as the stat line shows it. */
export interface RunView {
  phase: Phase;
  jobId: string | null;
  iteration: number; // the attempt of the last splice; a finished route's attempts
  elapsedS: number;
  metrics: Pick<Metrics, "length_m" | "time_s" | "ratio"> | null;
  stoppedBecause: StopReason | null;
  resolved: { tier: string; leashKm: number } | null; // what `auto` became, from `snapped`
  connection: "open" | "reconnecting" | "lost";
  note: string | null;
}

export const IDLE_RUN: RunView = {
  phase: "idle",
  jobId: null,
  iteration: 0,
  elapsedS: 0,
  metrics: null,
  stoppedBecause: null,
  resolved: null,
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
