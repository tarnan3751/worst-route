// Running a job (docs/reference/frontend.md, **Run**): `JobRunner`, a job's lifecycle, which the
// tests drive without a DOM, and `mountRunControls`, the Go, Cancel, Reroll and Make it worse
// buttons and the live stat line (ratio, length, drive time, attempts, elapsed; the drive and
// the delays under the slowest time, the items collected, a pilgrimage's visits, an alphabet
// route's letters, the turns; once stored, its arrival,
// the towns and states it passes through, its extra fuel and its share of the roads within
// reach).
//
// Go posts the stops (each stop along the way labelled as its marker is numbered, "Stop 2") and
// settings (the target, the objective with its item or brand, the turn constraints and hard mode,
// the worst order when ticked but not under the alphabet route, the vehicle and the departure, now
// when none is set, among them) to the region trips run on (the server's pick for the stops, or the
// one pinned: stops/pick.ts; Go and Reroll wait for it), and Reroll the same without a seed, so the
// server picks one; the 202's seed goes into the settings. Make it worse posts the shown route as
// `base_route_id` to the route's own region, with the seed after the last one tried on it (its own
// at first), the base route giving the stops, tier and leash and its recorded request the objective
// with its item or brand (a brand's id is its build's), the panel the rest; the settings' seed
// follows the route shown, so it changes only when a worse route is found. Then the job's events:
// `snapped` says where the stops went and in what order the route visits them (its `order`, through
// the base route's for a continuation: the run's `order` is the list's index of each stop visited,
// and the stop list says "visited 3rd"; under the worst order it comes once the server has tried
// every order, the stat line saying "Trying every order…" until then), `baseline` draws the
// sensible route, each `snapshot` replaces the route and each `progress` applies its splices in
// order (the alphabet route sends each better route as a snapshot, then a progress without
// splices), and while a run's best route so far scores above its current one (an annealing run
// takes shorter splices too) the stat line gives the best's ratio beside it, "best 31.0×"; `done`
// shows the stored route whole (the best), and for a ratio or duration target what it achieved;
// `error` shows its code and message. The stat line follows the run's own objective and
// constraints, or a stored route's request. A refusal under strict, or under hard mode, can be
// sent again without it.
// Cancel sends DELETE, and the stream then ends with `done` (the route so far, stored) or
// `error`. Every error ends the run with its code in the banner and the controls unlocked: the
// API's, or CLIENT_ERROR for one of the app's own (a running job is then cancelled). Leaving the
// page cancels a running job, or a job whose POST is in flight as soon as it is answered. Under
// Brand, Go, Reroll and Make it worse wait for the region's brands (`awaitingBrand`); an alphabet
// route cannot be made worse (`worseRefused`). Draw it starts drawing a route by hand on the same
// stops (draw/drawing.ts), which locks the run like a job; a route drawn by hand reads "drawn by
// hand" and its moves.
import {
  cancelJob,
  cancelJobOnLeave,
  createJob,
  errorBody,
  getRoute,
  subscribe,
} from "../api/client";
import type {
  AlphabetLetters,
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
  formatClock,
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
  departureOf,
  modeOf,
  takesTarget,
  targetOf,
  vehicleOf,
  type AppState,
  type RequestMode,
  type RunMetrics,
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
    departure: departureOf(settings),
  };
  if (settings.reorder && settings.objective !== "alphabet") request.reorder = true;
  if (seed !== null) request.seed = seed;
  return request;
}

/** The request Make it worse makes from the shown route, with `seed`, on the route's own build: a
 * continuation keeps its route's objective, with its item or brand, from its recorded request (a
 * brand's id is its build's, and the panel's are the region's trips now run on); the turns and
 * hard mode, the budget, the target (as bad as possible when the route's objective refuses the
 * one set), the vehicle and the departure are the panel's. */
function worseRequest(state: Readonly<AppState>, route: RouteDocument, seed: number): RouteRequest {
  const { settings } = state;
  const objective = route.request.objective ?? "distance";
  const { constraints } = modeOf(settings);
  const target = targetOf(settings);
  const request: RouteRequest = {
    region: route.region,
    base_route_id: route.route_id,
    seed,
    objective,
    budget_s: settings.budgetS,
    target: takesTarget(objective, target.kind) ? target : { kind: "max" },
    vehicle: vehicleOf(settings),
    departure: departureOf(settings),
  };
  if (objective === "collect") request.item = route.request.item;
  if (objective === "brand") request.brand = route.request.brand;
  if (constraints !== undefined) request.constraints = constraints;
  return request;
}

/** A target worth showing: null for max (or none). */
function goalOf(target: Target | undefined): Target | null {
  return target === undefined || target.kind === "max" ? null : target;
}

/** A request's objective, item, brand and constraints. */
function modeOfRequest({ objective, item, brand, constraints }: RouteRequest): RequestMode {
  return { objective, item, brand, constraints };
}

/** What is shown with a route: its baseline, a note, the run's target and what it achieved,
 * its mode (by default the route's own request's), and the list's index of each stop it visits,
 * in that order (by default the list's own: a stored route opens with its stops in its order). */
interface Shown {
  baseline?: LineString | null;
  note?: string | null;
  goal?: Target | null;
  outcome?: TargetMetrics | null;
  mode?: RequestMode;
  order?: number[] | null;
}

/** `order`, or null when it is the list's own. */
function visitOrder(order: number[]): number[] | null {
  return order.every((at, k) => at === k) ? null : order;
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

  /** Untick hard mode and send the last request again without it, as `withoutStrict` does (its
   * turn constraint kept; no constraints left when it had no side): the banner's answer to a leg
   * the seed cannot build under hard mode, or to a base route that comes back to a road. */
  withoutHard(): Promise<void> {
    if (this.#sent === null) return Promise.resolve();
    const { request, base } = this.#sent;
    this.#store.updateSettings({ hard: false });
    const { constraints: given, ...rest } = request;
    const { hard: _, ...constraints } = given ?? {};
    const sided = constraints.no_left === true || constraints.no_right === true;
    return this.#start(sided ? { ...rest, constraints } : rest, base);
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
    const { length_m, time_s, ratio, items, time_ratio, delay_s, turns, brand, alphabet } =
      route.metrics;
    this.#store.updateSettings({ seed: route.seed });
    this.#store.update({
      route,
      run: {
        ...IDLE_RUN,
        phase: "done",
        seed: route.seed,
        iteration: route.run.attempts,
        elapsedS: route.run.elapsed_s,
        metrics: { length_m, time_s, ratio, items, time_ratio, delay_s, turns, brand, alphabet },
        stoppedBecause: route.run.stopped_because,
        resolved: this.#store.state.run.resolved,
        goal: shown.goal !== undefined ? shown.goal : goalOf(route.request.target),
        outcome: shown.outcome !== undefined ? shown.outcome : (route.metrics.target ?? null),
        mode: shown.mode ?? modeOfRequest(route.request),
        order: shown.order ?? null,
        note,
      },
    });
    const bounds = lineBounds(route.geometry.coordinates);
    if (bounds) this.#fit(bounds);
  }

  async #start(request: RouteRequest, base: RouteDocument | null): Promise<void> {
    const store = this.#store;
    const before = store.state.run;
    // A continuation's stops are its base route's, in the order it visits them: the list's index
    // of each is the base's.
    const listed = base !== null ? before.order : null;
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
    const reorder = request.reorder === true;
    store.update({
      route: null,
      run: { ...IDLE_RUN, phase: "running", jobId, seed, goal, mode, reorder },
    });
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
        const asked = data.order ?? data.stops.map((_, k) => k); // among the job's stops
        const order = asked.map((k) => (listed ? listed[k] : k)); // among the list's
        const stops = [...store.state.stops];
        data.stops.forEach(({ node, lat, lon, moved_m, note }, k) => {
          const stop = stops[order[k]];
          if (stop) stops[order[k]] = { ...stop, snapped: { node, lat, lon, moved_m, note } };
        });
        store.update({ stops });
        store.updateRun({
          resolved: { tier: data.tier, leashKm: data.leash_km },
          order: visitOrder(order),
        });
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
          // the best route's ratio while it scores above the current route: an annealing
          // run takes shorter splices too (under the slowest time a faster one may be longer)
          bestRatio:
            data.best_score !== undefined && data.best_score > data.score
              ? (data.best_metrics?.ratio ?? null)
              : null,
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
    const { order } = this.#store.state.run;
    try {
      if (base !== null && done.route_id === base.route_id) {
        this.#tried.set(base.route_id, seed);
        let note = `no worse route found with seed ${seed}; try again or Reroll`;
        if (goal !== null) {
          note = outcome?.within_tolerance
            ? "already within the target"
            : `no route nearer the target found with seed ${seed}; try again or Reroll`;
        }
        this.showRoute(base, { note, goal, outcome, mode, order });
      } else {
        this.showRoute(await getRoute(done.route_id), { goal, outcome, mode, order });
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

/** Whether a pilgrimage has no brand to send yet: under Brand until GET /brands has answered with
 * the brand set among the region's (never, when it failed), so no request goes without one. */
export function awaitingBrand({ settings, brands }: Readonly<AppState>): boolean {
  return settings.objective === "brand" && !brands?.some(({ id }) => id === settings.brand);
}

/** Why the route shown cannot be made worse, or null: a continuation keeps its route's objective,
 * and the API continues no route under the alphabet route. */
export function worseRefused({ route }: Readonly<AppState>): string | null {
  return route?.request.objective === "alphabet" ? "the alphabet route cannot be continued" : null;
}

/** The run section in `root`: the buttons, which drive `runner` (Draw it, `draw`), and the stat
 * line. */
export function mountRunControls(
  root: HTMLElement,
  store: Store,
  runner: JobRunner,
  draw: () => Promise<void>,
): void {
  const button = (text: string, className: string, action: () => Promise<void>) => {
    const b = el("button", className, text);
    b.addEventListener("click", () => void action());
    return b;
  };
  const go = button("Go", "primary", () => runner.go());
  const cancel = button("Cancel", "", () => runner.cancel());
  const reroll = button("Reroll", "", () => runner.reroll());
  const worse = button("Make it worse", "", () => runner.worse());
  const drawIt = button("Draw it", "draw-it", draw);
  drawIt.title = "Draw your own worst route, junction by junction, against the engine's";
  const buttons = el("div", "buttons");
  buttons.append(go, cancel, reroll, worse, drawIt);
  const names = ["ratio", "line", "target", "note", "time", "items", "turns"];
  const rows = names.map((name) => el("div", `stat-${name}`));
  const [ratio, line, target, note, time, items, turns] = rows;
  root.replaceChildren(el("h2", "", "Run"), buttons, ratio, line, time, items, turns, target, note);
  const routeNames = ["arrives", "towns", "states", "fuel", "roads"];
  const routeRows = routeNames.map((name) => el("div", `stat-${name}`));
  root.append(...routeRows);

  store.watch(["region", "stops", "route", "run", "settings", "brands"], (state) => {
    const locked = busy(store) || awaitingBrand(state);
    const ready = state.region !== null && state.stops.length >= 2 && !locked;
    go.disabled = !ready;
    reroll.disabled = !ready;
    const refused = worseRefused(state);
    worse.disabled = state.region === null || state.route === null || locked || refused !== null;
    worse.title = refused ?? "";
    cancel.disabled = state.run.phase !== "running";
    drawIt.disabled = state.region === null || state.stops.length < 2 || busy(store);
    statLines(state).forEach((text, k) => (rows[k].textContent = text));
    items.title = countsTitle(state);
    routeLines(state).forEach(({ text, title }, k) => {
      routeRows[k].textContent = text;
      routeRows[k].title = title;
    });
  });
}

// How a run ended, where the API's word needs one.
const STOPPED: Partial<Record<StopReason, string>> = {
  target: "target reached",
  drawn: "drawn by hand",
};

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

/** An alphabet route's letters: how many, and the laps of the alphabet they run, as the CLI
 * writes them: "letters: 23 (A–W)", one letter "(A)", and past Z "(A–Z, A–C)" or "(A–Z, A)". */
function lettersLine({ letters, sequence }: AlphabetLetters): string {
  if (letters === 0) return "letters: 0";
  const last = sequence.at(-1);
  const laps = Array<string>(Math.floor((letters - 1) / 26)).fill("A–Z");
  laps.push(last === "A" ? "A" : `A–${last}`);
  return `letters: ${formatCount(letters)} (${laps.join(", ")})`;
}

/** The objective's counter: the items a collecting route collects, the locations a pilgrimage
 * visits of those within reach ("17 of 19 Starbucks"), an alphabet route's letters. */
function countsLine(metrics: RunMetrics, { objective, item }: RequestMode): string {
  const { items, brand, alphabet } = metrics;
  if (objective === "collect" && item && items) return formatItems(items[item], item);
  if (objective === "brand" && brand) {
    return `${formatCount(brand.visits)} of ${formatCount(brand.of)} ${brand.name}`;
  }
  if (objective === "alphabet" && alphabet) return lettersLine(alphabet);
  return "";
}

/** The counter row's tooltip: an alphabet route's first road of each letter, one a line
 * ("A: Southwest Alder Street"); none for the other objectives. */
export function countsTitle({ run }: Readonly<AppState>): string {
  const alphabet = run.mode.objective === "alphabet" ? run.metrics?.alphabet : null;
  if (!alphabet) return "";
  return alphabet.roads.map((road, k) => `${alphabet.sequence[k]}: ${road}`).join("\n");
}

type StatLines = [string, string, string, string, string, string, string];

/** The stat line's rows: the ratio (under the slowest time, of experienced times once stored);
 * the length and drive time; the target; the run; and, shown between the second and the third,
 * the drive and the delays (the slowest time, live from each progress event, then the stored
 * route's), the objective's counter (live: the items collected, a pilgrimage's visits, an
 * alphabet route's letters), and the turns (once stored). */
export function statLines(state: Readonly<AppState>): StatLines {
  const { run, settings } = state;
  const only = (line: string): StatLines => ["", line, "", "", "", "", ""];
  if (run.phase === "starting") return only("Starting…");
  if (run.phase === "drawing") return only("Drawing by hand: click a highlighted road to take it.");
  if (run.metrics === null) {
    if (run.phase === "running") {
      // under the worst order, the server tries every order before it says where the stops went
      return only(
        run.reorder && run.resolved === null
          ? "Trying every order…"
          : "Planning the sensible route…",
      );
    }
    if (run.phase === "failed") return only("No route.");
    return only(
      state.stops.length < 2 ? "Click the map to set a start and an end." : "Ready when you are.",
    );
  }
  const { length_m, time_s, ratio, time_ratio, delay_s, turns } = run.metrics;
  const slowest = run.mode.objective === "time";
  const numbers = `${formatDistance(length_m, settings.units)} · ${formatDuration(time_s)}`;
  const drawn = run.stoppedBecause === "drawn";
  const attempts =
    run.phase === "done"
      ? `${formatCount(run.iteration)} ${drawn ? "moves" : "attempts"}`
      : `attempt ${formatCount(run.iteration)}`;
  const parts = drawn ? [attempts] : [attempts, formatElapsed(run.elapsedS)];
  if (run.phase === "running") {
    if (run.bestRatio !== null) parts.push(`best ${formatRatio(run.bestRatio)}`);
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
    countsLine(run.metrics, run.mode),
    turns ? turnsLine(turns, run.mode) : "",
  ];
}

/** A row drawn from the stored route: its text, and the tooltip that spells it out. */
export interface RouteLine {
  text: string;
  title: string;
}

/** The stored route's rows, after the run's: when it arrives (with a departure; the day and the
 * minute at the departure's offset, the whole time in the tooltip), the towns it passes through
 * (the first two and how many more; all of them in the tooltip), the states (by their codes), the
 * fuel it burns beyond the sensible route's and its cost (with a vehicle), and its share of the
 * roads within reach. Empty where the document says nothing, and while no stored route is
 * shown. */
export function routeLines(state: Readonly<AppState>): RouteLine[] {
  const none = { text: "", title: "" };
  const { route, run } = state;
  if (route === null || run.phase !== "done") return [none, none, none, none, none];
  const { arrival, towns, states, extra_fuel_gal: fuel, extra_cost_usd: cost } = route.metrics;
  const fraction = route.metrics.network_fraction;
  const more = towns && towns.length > 2 ? ` +${towns.length - 2}` : "";
  return [
    arrival ? { text: `arrives ${formatClock(arrival)}`, title: arrival } : none,
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
