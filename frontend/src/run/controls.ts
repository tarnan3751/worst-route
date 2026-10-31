// Running a job (docs/reference/frontend.md, **Run**): `JobRunner`, a job's lifecycle, which the
// tests drive without a DOM, and `mountRunControls`, the Go, Cancel, Reroll and Make it worse
// buttons and the live stat line (ratio, length, drive time, attempts, elapsed).
//
// Go posts the stops and settings (the target, the objective with its item, and the turn
// constraints among them), and Reroll the same without a seed,
// so the server picks one; the 202's seed goes into the settings. Make it worse posts the shown route as `base_route_id`
// with the seed after the last one tried on it (its own at first), the base route giving the
// stops, tier and leash; the settings' seed follows the route shown, so it changes only when a
// worse route is found. Then the job's events: `snapped` says where the stops went, `baseline`
// draws the sensible route, each `snapshot` replaces the route and each `progress` applies its
// splices in order; `done` shows the stored route whole, and for a ratio or duration target
// what it achieved; `error` shows its code and message.
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
} from "../api/types";
import { formatCount, formatDistance, formatDuration, formatElapsed, formatRatio } from "../format";
import type { RouteLayer } from "../map/routeLayer";
import { IDLE_RUN, TARGET_TOLERANCE, modeOf, targetOf, type AppState, type Store } from "../state";
import { busy } from "../stops/stopList";
import { el } from "../util/dom";
import { lineBounds, type Bounds } from "../util/geo";

/** What the runner draws on: the route layer, or a test's stand-in. */
export type RouteView = Pick<
  RouteLayer,
  "clear" | "setBaseline" | "setSnapshot" | "applySplice" | "setFinal"
>;

/** The request Go makes, with `seed` (null: the server picks one). */
function jobRequest(state: Readonly<AppState>, seed: number | null): RouteRequest {
  const { settings } = state;
  const request: RouteRequest = {
    region: state.region?.region ?? "",
    stops: state.stops.map(({ lat, lon }) => ({ lat, lon })),
    ...modeOf(settings),
    tier: settings.tier,
    leash_km: settings.leashKm,
    budget_s: settings.budgetS,
    target: targetOf(settings),
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
  };
}

/** A target worth showing: null for max (or none). */
function goalOf(target: Target | undefined): Target | null {
  return target === undefined || target.kind === "max" ? null : target;
}

/** What is shown with a route: its baseline, a note, the run's target and what it achieved
 * (by default the route's own request's). */
interface Shown {
  baseline?: LineString | null;
  note?: string | null;
  goal?: Target | null;
  outcome?: TargetMetrics | null;
}

export class JobRunner {
  readonly #store: Store;
  readonly #view: RouteView;
  readonly #fit: (bounds: Bounds) => void;
  readonly #tried = new Map<string, number>(); // the last seed Make it worse tried, by route
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
    const { length_m, time_s, ratio } = route.metrics;
    this.#store.updateSettings({ seed: route.seed });
    this.#store.update({
      route,
      run: {
        ...IDLE_RUN,
        phase: "done",
        seed: route.seed,
        iteration: route.run.attempts,
        elapsedS: route.run.elapsed_s,
        metrics: { length_m, time_s, ratio },
        stoppedBecause: route.run.stopped_because,
        resolved: this.#store.state.run.resolved,
        goal: shown.goal !== undefined ? shown.goal : goalOf(route.request.target),
        outcome: shown.outcome !== undefined ? shown.outcome : (route.metrics.target ?? null),
        note,
      },
    });
    const bounds = lineBounds(route.geometry.coordinates);
    if (bounds) this.#fit(bounds);
  }

  async #start(request: RouteRequest, base: RouteDocument | null): Promise<void> {
    const store = this.#store;
    const before = store.state.run;
    store.update({ error: null, run: { ...before, phase: "starting" } });
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
    store.update({ route: null, run: { ...IDLE_RUN, phase: "running", jobId, seed, goal } });
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
      done: guard((data) => void this.#finish(data, base, seed, goal)),
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
    goal: Target | null,
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
        this.showRoute(base, { note, goal, outcome });
      } else {
        this.showRoute(await getRoute(done.route_id), { goal, outcome });
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
  const rows = ["stat-ratio", "stat-line", "stat-target", "stat-note"].map((name) =>
    el("div", name),
  );
  root.replaceChildren(el("h2", "", "Run"), buttons, ...rows);

  store.watch(["region", "stops", "route", "run", "settings"], (state) => {
    const locked = busy(store);
    const ready = state.region !== null && state.stops.length >= 2 && !locked;
    go.disabled = !ready;
    reroll.disabled = !ready;
    worse.disabled = state.region === null || state.route === null || locked;
    cancel.disabled = state.run.phase !== "running";
    statLines(state).forEach((text, k) => (rows[k].textContent = text));
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

/** The stat line's four rows: the ratio; the length and drive time; the target; and the run. */
export function statLines(state: Readonly<AppState>): [string, string, string, string] {
  const { run, settings } = state;
  if (run.phase === "starting") return ["", "Starting…", "", ""];
  if (run.metrics === null) {
    if (run.phase === "running") return ["", "Planning the sensible route…", "", ""];
    if (run.phase === "failed") return ["", "No route.", "", ""];
    const prompt =
      state.stops.length < 2 ? "Click the map to set a start and an end." : "Ready when you are.";
    return ["", prompt, "", ""];
  }
  const { length_m, time_s, ratio } = run.metrics;
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
  return [formatRatio(ratio), numbers, targetLine(run.goal, run.outcome), parts.join(" · ")];
}
