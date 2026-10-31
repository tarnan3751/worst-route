// Running a job (docs/reference/frontend.md, **Run**): `JobRunner`, a job's lifecycle, which the
// tests drive without a DOM, and `mountRunControls`, the Go, Cancel, Reroll and Make it worse
// buttons and the live stat line (ratio, length, drive time, attempts, elapsed; the drive and
// the delays under the slowest time, the items collected, the turns; once stored, the towns and
// states the route passes through, its extra fuel and its share of the roads within reach).
//
// Go posts the stops (each stop along the way labelled as its marker is numbered, "Stop 2") and
// settings (the target, the objective with its item, the turn constraints and the vehicle among
// them), and Reroll the same without a seed,
// so the server picks one; the 202's seed goes into the settings. Make it worse posts the shown route as `base_route_id`
// with the seed after the last one tried on it (its own at first), the base route giving the
// stops, tier and leash; the settings' seed follows the route shown, so it changes only when a
// worse route is found. Then the job's events: `snapped` says where the stops went, `baseline`
// draws the sensible route, each `snapshot` replaces the route and each `progress` applies its
// splices in order; `done` shows the stored route whole, and for a ratio or duration target
// what it achieved; `error` shows its code and message. The stat line follows the run's own
// objective and constraints, or a stored route's request.
// Cancel sends DELETE, and the stream then ends with `done` (the route so far, stored) or
// `error`. Every error ends the run with its code in the banner and the controls unlocked: the
// API's, or CLIENT_ERROR for one of the app's own (a running job is then cancelled). Leaving the
// page cancels a running job, or a job whose POST is in flight as soon as it is answered.
import {
  cancelJob,
  cancelJobOnLeave,
  createJob,
  errorBody,
  getRoute,
  subscribe,
} from "../api/client";
import type {
  Done,
  ErrorBody,
  LineString,
  RouteDocument,
  RouteRequest,
  StopReason,
  Target,
  TargetMetrics,
  Turns,
} from "../api/types";
import {
  formatCount,
  formatDistance,
  formatDuration,
  formatElapsed,
  formatFuel,
  formatItems,
  formatPercent,
  formatRatio,
  formatStates,
} from "../format";
import type { RouteLayer } from "../map/routeLayer";
import {
  IDLE_RUN,
  TARGET_TOLERANCE,
  modeOf,
  targetOf,
  vehicleOf,
  type AppState,
  type RequestMode,
  type Store,
} from "../state";
import { busy, stopLabel } from "../stops/stopList";
import { el } from "../util/dom";
import { lineBounds, type Bounds } from "../util/geo";

/** What the runner draws on: the route layer, or a test's stand-in. */
export type RouteView = Pick<
  RouteLayer,
  "clear" | "setBaseline" | "setSnapshot" | "applySplice" | "setFinal"
>;

/** The request Go makes, with `seed` (null: the server picks one). */
function jobRequest(state: Readonly<AppState>, seed: number | null): RouteRequest {
  const { settings, stops } = state;
  const request: RouteRequest = {
    region: state.region?.region ?? "",
    stops: stops.map(({ lat, lon }, k) => {
      const label = stopLabel(k, stops.length);
      return label === null ? { lat, lon } : { lat, lon, label };
    }),
    ...modeOf(settings),
    tier: settings.tier,
    leash_km: settings.leashKm,
    budget_s: settings.budgetS,
    target: targetOf(settings),
    vehicle: vehicleOf(settings),
  };
  if (seed !== null) request.seed = seed;
  return request;
}

/** The request Make it worse makes from the shown route, with `seed`. */
function worseRequest(state: Readonly<AppState>, route: RouteDocument, seed: number): RouteRequest {
  const { settings } = state;
  return {
    region: route.region,
    base_route_id: route.route_id,
    seed,
    ...modeOf(settings),
    budget_s: settings.budgetS,
    target: targetOf(settings),
    vehicle: vehicleOf(settings),
  };
}

/** A target worth showing: null for max (or none). */
function goalOf(target: Target | undefined): Target | null {
  return target === undefined || target.kind === "max" ? null : target;
}

/** A request's objective, item and turn constraints. */
function modeOfRequest({ objective, item, constraints }: RouteRequest): RequestMode {
  return { objective, item, constraints };
}

/** What is shown with a route: its baseline, a note, the run's target and what it achieved,
 * and its mode (by default the route's own request's). */
interface Shown {
  baseline?: LineString | null;
  note?: string | null;
  goal?: Target | null;
  outcome?: TargetMetrics | null;
  mode?: RequestMode;
}

export class JobRunner {
  readonly #store: Store;
  readonly #view: RouteView;
  readonly #fit: (bounds: Bounds) => void;
  readonly #tried = new Map<string, number>(); // the last seed Make it worse tried, by route
  #sent: { request: RouteRequest; base: RouteDocument | null } | null = null; // the last request
  #leaving = false;

  constructor(store: Store, view: RouteView, fit: (bounds: Bounds) => void) {
    this.#store = store;
    this.#view = view;
    this.#fit = fit;
  }

  go(): Promise<void> {
    return this.#start(jobRequest(this.#store.state, this.#store.state.settings.seed), null);
  }

  reroll(): Promise<void> {
    return this.#start(jobRequest(this.#store.state, null), null);
  }

  /** Untick strict and send the last request again without it (a Go, a Reroll or a Make it
   * worse, with its seed or none, and its base route): the banner's answer to
   * INFEASIBLE_TURN_CONSTRAINT. */
  withoutStrict(): Promise<void> {
    if (this.#sent === null) return Promise.resolve();
    const { request, base } = this.#sent;
    this.#store.updateSettings({ strict: false });
    return this.#start(
      { ...request, constraints: { ...request.constraints, strict: false } },
      base,
    );
  }

  worse(): Promise<void> {
    const { route } = this.#store.state;
    if (route === null) return Promise.resolve();
    const seed = (this.#tried.get(route.route_id) ?? route.seed) + 1;
    return this.#start(worseRequest(this.#store.state, route, seed), route);
  }

  async cancel(): Promise<void> {
    const { jobId } = this.#store.state.run;
    if (jobId === null) return;
    try {
      await cancelJob(jobId);
    } catch (error) {
      this.#store.update({ error: errorBody(error) });
    }
  }

  /** The page is going away: cancel the job, now or once its POST is answered. */
  leave(): void {
    const { phase, jobId } = this.#store.state.run;
    if (phase === "running" && jobId !== null) cancelJobOnLeave(jobId);
    if (phase === "starting") this.#leaving = true;
  }

  /** Show a stored route: its whole geometry, its numbers, and the baseline when given. */
  showRoute(route: RouteDocument, shown: Shown = {}): void {
    const { baseline, note = null } = shown;
    if (baseline !== undefined) this.#view.setBaseline(baseline);
    this.#view.setFinal(route.geometry);
    const { length_m, time_s, ratio, items, time_ratio, delay_s, turns } = route.metrics;
    this.#store.updateSettings({ seed: route.seed });
    this.#store.update({
      route,
      run: {
        ...IDLE_RUN,
        phase: "done",
        seed: route.seed,
        iteration: route.run.attempts,
        elapsedS: route.run.elapsed_s,
        metrics: { length_m, time_s, ratio, items, time_ratio, delay_s, turns },
        stoppedBecause: route.run.stopped_because,
        resolved: this.#store.state.run.resolved,
        goal: shown.goal !== undefined ? shown.goal : goalOf(route.request.target),
        outcome: shown.outcome !== undefined ? shown.outcome : (route.metrics.target ?? null),
        mode: shown.mode ?? modeOfRequest(route.request),
        note,
      },
    });
    const bounds = lineBounds(route.geometry.coordinates);
    if (bounds) this.#fit(bounds);
  }

  async #start(request: RouteRequest, base: RouteDocument | null): Promise<void> {
    const store = this.#store;
    const before = store.state.run;
    this.#sent = { request, base };
    // The last run's numbers are not this one's (the toasts' live ratio reads them).
    store.update({ error: null, run: { ...before, phase: "starting", metrics: null } });
    let created;
    try {
      created = await createJob(request);
    } catch (error) {
      store.update({ error: errorBody(error), run: before });
      return;
    }
    const { job_id: jobId, seed } = created;
    if (this.#leaving) {
      cancelJobOnLeave(jobId);
      return;
    }
    this.#view.clear();
    if (base === null) store.updateSettings({ seed });
    const goal = goalOf(request.target);
    const mode = modeOfRequest(request);
    store.update({ route: null, run: { ...IDLE_RUN, phase: "running", jobId, seed, goal, mode } });
    // An error of the app's own while following the job ends the run, and the job with it.
    const guard =
      <T>(handle: (data: T) => void) =>
      (data: T): void => {
        try {
          handle(data);
        } catch (error) {
          subscription.close();
          cancelJob(jobId).catch(() => undefined);
          this.#fail(errorBody(error));
        }
      };
    const subscription = subscribe(created.events_url, {
      snapped: guard((data) => {
        const stops = store.state.stops.map((stop, k) => {
          const { node, lat, lon, moved_m, note } = data.stops[k];
          return { ...stop, snapped: { node, lat, lon, moved_m, note } };
        });
        store.update({ stops });
        store.updateRun({ resolved: { tier: data.tier, leashKm: data.leash_km } });
      }),
      baseline: guard((data) => this.#view.setBaseline(data.geometry)),
      snapshot: guard((data) => {
        this.#view.setSnapshot(data.geometry, data.node_index);
        store.updateRun({
          iteration: data.iteration,
          elapsedS: data.elapsed_s,
          metrics: data.metrics,
        });
      }),
      progress: guard((data) => {
        for (const s of data.splices) {
          this.#view.applySplice(s.i, s.j, s.coords, s.node_index);
        }
        store.updateRun({
          iteration: data.iteration,
          elapsedS: data.elapsed_s,
          metrics: data.metrics,
        });
      }),
      done: guard((data) => void this.#finish(data, base, seed, { goal, mode })),
      error: guard((data) => this.#fail(data)),
      connection: guard((state) => {
        store.updateRun({ connection: state });
        if (state === "lost") {
          this.#fail({
            code: "STREAM_LOST",
            message: "the job's event stream closed and could not reconnect",
          });
        }
      }),
    });
  }

  async #finish(
    done: Done,
    base: RouteDocument | null,
    seed: number,
    { goal, mode }: { goal: Target | null; mode: RequestMode },
  ): Promise<void> {
    const outcome = done.metrics.target ?? null;
    try {
      if (base !== null && done.route_id === base.route_id) {
        this.#tried.set(base.route_id, seed);
        let note = `no worse route found with seed ${seed}; try again or Reroll`;
        if (goal !== null) {
          note = outcome?.within_tolerance
            ? "already within the target"
            : `no route nearer the target found with seed ${seed}; try again or Reroll`;
        }
        this.showRoute(base, { note, goal, outcome, mode });
      } else {
        this.showRoute(await getRoute(done.route_id), { goal, outcome, mode });
      }
    } catch (error) {
      this.#fail(errorBody(error));
    }
  }

  #fail(body: ErrorBody): void {
    this.#store.update({ error: body });
    this.#store.updateRun({ phase: "failed" });
  }
}

/** The run section in `root`: the buttons, which drive `runner`, and the stat line. */
export function mountRunControls(root: HTMLElement, store: Store, runner: JobRunner): void {
  const button = (text: string, className: string, action: () => Promise<void>) => {
    const b = el("button", className, text);
    b.addEventListener("click", () => void action());
    return b;
  };
  const go = button("Go", "primary", () => runner.go());
  const cancel = button("Cancel", "", () => runner.cancel());
  const reroll = button("Reroll", "", () => runner.reroll());
  const worse = button("Make it worse", "", () => runner.worse());
  const buttons = el("div", "buttons");
  buttons.append(go, cancel, reroll, worse);
  const names = ["ratio", "line", "target", "note", "time", "items", "turns"];
  const rows = names.map((name) => el("div", `stat-${name}`));
  const [ratio, line, target, note, time, items, turns] = rows;
  root.replaceChildren(el("h2", "", "Run"), buttons, ratio, line, time, items, turns, target, note);
  const routeRows = ["towns", "states", "fuel", "roads"].map((name) => el("div", `stat-${name}`));
  root.append(...routeRows);

  store.watch(["region", "stops", "route", "run", "settings"], (state) => {
    const locked = busy(store);
    const ready = state.region !== null && state.stops.length >= 2 && !locked;
    go.disabled = !ready;
    reroll.disabled = !ready;
    worse.disabled = state.region === null || state.route === null || locked;
    cancel.disabled = state.run.phase !== "running";
    statLines(state).forEach((text, k) => (rows[k].textContent = text));
    routeLines(state).forEach(({ text, title }, k) => {
      routeRows[k].textContent = text;
      routeRows[k].title = title;
    });
  });
}

// How a run ended, where the API's word needs one.
const STOPPED: Partial<Record<StopReason, string>> = { target: "target reached" };

/** A target's number: × for a ratio, a drive time for a duration. */
function targetValue(kind: TargetMetrics["kind"], value: number): string {
  return kind === "ratio" ? formatRatio(value) : formatDuration(value);
}

/** The target row: the run's target, and once done what it achieved. */
function targetLine(goal: Target | null, outcome: TargetMetrics | null): string {
  if (outcome !== null) {
    const band = outcome.within_tolerance ? "within" : "outside";
    return (
      `target ${targetValue(outcome.kind, outcome.value)}, ` +
      `achieved ${targetValue(outcome.kind, outcome.achieved)}, ` +
      `${band} ±${TARGET_TOLERANCE * 100} %`
    );
  }
  if (goal === null || goal.kind === "max") return "";
  const value = goal.kind === "ratio" ? goal.value : goal.seconds;
  return value == null ? "" : `target ${targetValue(goal.kind, value)}`;
}

/** The turns row: the left and right turns, "no lefts" (rights) when the side avoided has none
 * and no U-turn either, as the constraint counts them. */
function turnsLine({ left, right, u_turn }: Turns, mode: RequestMode): string {
  const none = (n: number, avoided?: boolean) => n === 0 && u_turn === 0 && avoided === true;
  const lefts = none(left, mode.constraints?.no_left) ? "no lefts" : `${formatCount(left)} left`;
  const rights = none(right, mode.constraints?.no_right)
    ? "no rights"
    : `${formatCount(right)} right`;
  return `${lefts} · ${rights}`;
}

type StatLines = [string, string, string, string, string, string, string];

/** The stat line's rows: the ratio (under the slowest time, of experienced times once stored);
 * the length and drive time; the target; the run; and, shown between the second and the third,
 * the drive and the delays (the slowest time, once stored), the items collected (live), and the
 * turns (once stored). */
export function statLines(state: Readonly<AppState>): StatLines {
  const { run, settings } = state;
  const only = (line: string): StatLines => ["", line, "", "", "", "", ""];
  if (run.phase === "starting") return only("Starting…");
  if (run.metrics === null) {
    if (run.phase === "running") return only("Planning the sensible route…");
    if (run.phase === "failed") return only("No route.");
    return only(
      state.stops.length < 2 ? "Click the map to set a start and an end." : "Ready when you are.",
    );
  }
  const { length_m, time_s, ratio, items, time_ratio, delay_s, turns } = run.metrics;
  const { objective, item } = run.mode;
  const slowest = objective === "time";
  const numbers = `${formatDistance(length_m, settings.units)} · ${formatDuration(time_s)}`;
  const attempts =
    run.phase === "done"
      ? `${formatCount(run.iteration)} attempts`
      : `attempt ${formatCount(run.iteration)}`;
  const parts = [attempts, formatElapsed(run.elapsedS)];
  if (run.phase === "running") {
    parts.push(run.connection === "open" ? "getting worse…" : "reconnecting…");
  }
  if (run.phase === "failed") parts.push("failed");
  if (run.stoppedBecause) parts.push(STOPPED[run.stoppedBecause] ?? run.stoppedBecause);
  if (run.seed !== null) parts.push(`seed ${run.seed}`);
  if (run.note) parts.push(run.note);
  return [
    slowest && typeof time_ratio === "number"
      ? `${formatRatio(time_ratio)} slower`
      : formatRatio(ratio),
    numbers,
    targetLine(run.goal, run.outcome),
    parts.join(" · "),
    slowest && delay_s !== undefined
      ? `drive ${formatDuration(time_s)} + delays ${formatDuration(delay_s)}`
      : "",
    objective === "collect" && item && items ? formatItems(items[item], item) : "",
    turns ? turnsLine(turns, run.mode) : "",
  ];
}

/** A row drawn from the stored route: its text, and the tooltip that spells it out. */
export interface RouteLine {
  text: string;
  title: string;
}

/** The stored route's rows, after the run's: the towns it passes through (the first two and how
 * many more; all of them in the tooltip), the states (by their codes), the fuel it burns beyond
 * the sensible route's and its cost (with a vehicle), and its share of the roads within reach.
 * Empty where the document says nothing, and while no stored route is shown. */
export function routeLines(
  state: Readonly<AppState>,
): [RouteLine, RouteLine, RouteLine, RouteLine] {
  const none = { text: "", title: "" };
  const { route, run } = state;
  if (route === null || run.phase !== "done") return [none, none, none, none];
  const { towns, states, extra_fuel_gal: fuel, extra_cost_usd: cost } = route.metrics;
  const fraction = route.metrics.network_fraction;
  const more = towns && towns.length > 2 ? ` +${towns.length - 2}` : "";
  return [
    towns?.length
      ? { text: `towns: ${towns.slice(0, 2).join(", ")}${more}`, title: towns.join(", ") }
      : none,
    states?.length ? { text: `states: ${formatStates(states)}`, title: states.join(", ") } : none,
    fuel != null && cost != null ? { text: `fuel: ${formatFuel(fuel, cost)}`, title: "" } : none,
    fraction !== undefined
      ? { text: `roads used: ${formatPercent(fraction)} of those within reach`, title: "" }
      : none,
  ];
}
