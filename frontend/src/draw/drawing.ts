// A route drawn by hand, against the engine (docs/reference/frontend.md, **Drawing**): `Drawing`,
// the drawing's state and steps, which the tests drive without a DOM, and what the panel and the
// map say of it.
//
// "Draw it" starts a drawing on the stops and settings Go would send, as the longest route with no
// target (what a drawing takes: `drawRequest`), and the run is then `drawing`, which locks the
// stops and the settings. The server answers each step (POST /draw) with the drawing so far and its
// legal next moves, which the map shows (map/drawLayer.ts): a click near one takes it (`take`;
// `nearestMove` finds it), Undo or Backspace takes the last back (`undo`: the answer before, no
// request), Esc leaves drawing mode (`leave`: the drawing kept, drawn, until the stops change, and
// "Draw it" resumes it), and Finish stores it (POST /draw/finish) once it has reached the end, or
// when the end is one move away taking the longest such move (`finalMove`), then opens the stored
// route like a link. An illegal path (ILLEGAL_PATH: a server that moved on) is cut back to its
// first illegal edge. The engine's route for the same request aiming at DRAW_PAR_RATIO is the par:
// a normal job with that ratio target (a round trip's, whose ratio target the API refuses, the full
// run), started with the drawing and followed for its ratio only (nothing of it drawn); once both
// are done, the verdict, by length, and the engine's route to open. The drawing is forgotten, its
// par cancelled, once the stops or the settings it was drawn on (the region, the tier, the leash,
// the turns, hard mode) are no longer the store's, whatever changed them (an edit, a route opened
// by link or from today's route), and when a run starts; leaving the page cancels the par.
import {
  cancelJob,
  cancelJobOnLeave,
  createJob,
  draw,
  errorBody,
  finishDraw,
  subscribe,
  type Subscription,
} from "../api/client";
import type { DrawAnswer, DrawMove, LonLat, Metrics, RouteRequest } from "../api/types";
import { formatDistance, formatRatio, type Units } from "../format";
import { IDLE_RUN, departureOf, modeOf, vehicleOf, type AppState, type Store } from "../state";
import { stopLabel } from "../stops/stopList";

/** What draws the drawing: its line and, in drawing mode, its next moves; nothing for null. */
export interface DrawView {
  show(answer: DrawAnswer | null, active: boolean): void;
}

/** What a drawing asks of the rest of the app: to clear the route shown when it starts, and to
 * open a stored route (the finished drawing, or the par's) as a link does. */
export interface DrawHooks {
  clearRoute(): void;
  open(routeId: string): Promise<boolean>;
}

/** The par's ratio target. On the short in-town trips people draw by hand the engine's full run
 * reaches 200–320× while a patient drawing lands around 10–15×, so nobody would ever beat it; it
 * aims at 10× instead, which it reaches within ±2 % in a second or two: a bar the player sees
 * before drawing much and can beat with patience. */
export const DRAW_PAR_RATIO = 10;

/** The engine's route for the same request, aiming at `target` (DRAW_PAR_RATIO; null for a round
 * trip's full run): planning (its job's ratio so far, once it has one), done (its stored route) or
 * failed (why). */
export interface Par {
  phase: "planning" | "done" | "failed";
  target: number | null;
  jobId: string | null;
  ratio: number | null;
  lengthM: number | null;
  routeId: string | null;
  note: string | null;
}

/** A drawing stored as a route: its id, ratio (null for a round trip) and length. */
export interface Finished {
  routeId: string;
  ratio: number | null;
  lengthM: number;
}

/** The request a drawing makes: the stops (each along the way labelled as Go labels it) and the
 * settings Go sends, as the longest route with no target; the turns and hard mode kept. */
export function drawRequest(state: Readonly<AppState>): RouteRequest {
  const { settings, stops } = state;
  const { constraints } = modeOf(settings);
  const request: RouteRequest = {
    region: state.region?.region ?? "",
    stops: stops.map(({ lat, lon }, k) => {
      const label = stopLabel(k, stops.length);
      return label === null ? { lat, lon } : { lat, lon, label };
    }),
    objective: "distance",
    tier: settings.tier,
    leash_km: settings.leashKm,
    budget_s: settings.budgetS,
    target: { kind: "max" },
    vehicle: vehicleOf(settings),
    departure: departureOf(settings),
  };
  if (constraints !== undefined) request.constraints = constraints;
  if (settings.seed !== null) request.seed = settings.seed;
  return request;
}

/** What a drawing's request is drawn on: its region, stops, tier, leash and constraints (not its
 * seed, budget, vehicle or departure, which change nothing in it). */
function drawnOn(request: RouteRequest): string {
  const stops = (request.stops ?? []).map(({ lat, lon }) => [lat, lon]);
  const { region, tier, leash_km, constraints } = request;
  return JSON.stringify([region, stops, tier, leash_km, constraints ?? null]);
}

/** The move that ends the drawing, the longest of them, or null when none does. */
export function finalMove(answer: DrawAnswer | null): DrawMove | null {
  if (answer === null) return null;
  const last = answer.stops.length - 1;
  const final = answer.next.filter((move) => move.stop === last);
  return final.reduce<DrawMove | null>((a, b) => (a && a.length_m >= b.length_m ? a : b), null);
}

/** The move whose line passes nearest `point` on the screen, within `tolerance` pixels, or
 * null; `project` puts a [lon, lat] on the screen. A tie goes to the first. */
export function nearestMove(
  point: { x: number; y: number },
  moves: DrawMove[],
  project: (at: LonLat) => { x: number; y: number },
  tolerance = 8,
): DrawMove | null {
  let best: DrawMove | null = null;
  let nearest = tolerance;
  for (const move of moves) {
    const line = move.coords.map(project);
    for (let k = 1; k < line.length; k++) {
      const d = toSegment(point, line[k - 1], line[k]);
      if (d < nearest || (d === nearest && best === null)) {
        nearest = d;
        best = move;
      }
    }
  }
  return best;
}

function toSegment(
  p: { x: number; y: number },
  a: { x: number; y: number },
  b: { x: number; y: number },
): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length2 = dx * dx + dy * dy;
  const t = length2 === 0 ? 0 : ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2;
  const u = Math.min(1, Math.max(0, t));
  return Math.hypot(p.x - (a.x + u * dx), p.y - (a.y + u * dy));
}

/** A move as its tooltip names it: "Southwest Alder Street · 0.1 mi · left". */
export function moveLabel(move: Pick<DrawMove, "name" | "length_m" | "turn">, units: Units) {
  const turn = move.turn === null ? [] : [move.turn === "u_turn" ? "U-turn" : move.turn];
  return [move.name ?? "unnamed road", formatDistance(move.length_m, units), ...turn].join(" · ");
}

export class Drawing extends EventTarget {
  request: RouteRequest | null = null; // the drawing's; null when there is none
  path: number[] = []; // its edges
  answers: DrawAnswer[] = []; // the server's answer to each prefix of the path, the empty first
  active = false; // drawing mode: the map's clicks take moves
  pending = false; // a request in flight
  par: Par | null = null;
  finished: Finished | null = null;
  showing: "mine" | "engine" = "mine"; // the route shown once finished
  readonly #store: Store;
  readonly #view: DrawView;
  readonly #hooks: DrawHooks;
  #generation = 0; // a drawing's; answers for an earlier one are dropped
  #subscription: Subscription | null = null;

  constructor(store: Store, view: DrawView, hooks: DrawHooks) {
    super();
    this.#store = store;
    this.#view = view;
    this.#hooks = hooks;
    store.watch(["run"], ({ run }) => {
      if (run.phase === "starting") this.discard(); // a run of the user's own
    });
    store.watch(["region", "stops", "settings"], (state) => {
      const request = this.request;
      if (request !== null && drawnOn(drawRequest(state)) !== drawnOn(request)) this.discard();
    });
  }

  /** The answer for the path drawn, or null before the first. */
  get answer(): DrawAnswer | null {
    return this.answers.at(-1) ?? null;
  }

  /** Whether the last edge can be taken back. */
  get canUndo(): boolean {
    return this.active && !this.pending && this.path.length > 0;
  }

  /** Whether Finish stores the drawing: it has reached the end, or the end is one move away. */
  get canFinish(): boolean {
    const answer = this.answer;
    return this.active && !this.pending && (answer?.done === true || finalMove(answer) !== null);
  }

  /** Start drawing on the stops and settings, or resume the drawing left with Esc. */
  async start(): Promise<void> {
    const store = this.#store;
    const resume = this.request !== null && this.finished === null;
    if (!resume) this.discard();
    this.#hooks.clearRoute();
    store.update({ error: null, route: null, run: { ...IDLE_RUN, phase: "drawing" } });
    this.active = true;
    if (resume) {
      this.#changed();
      return;
    }
    const generation = this.#generation;
    const request = drawRequest(store.state);
    this.request = request;
    this.pending = true;
    this.#changed();
    let first: DrawAnswer;
    try {
      first = await draw({ request, path: [] });
    } catch (error) {
      if (generation !== this.#generation) return;
      this.discard();
      store.update({ error: errorBody(error), run: IDLE_RUN });
      return;
    }
    if (generation !== this.#generation) return;
    this.answers = [first];
    this.pending = false;
    store.update({
      stops: store.state.stops.map((stop, k) => {
        const { node, lat, lon, moved_m, note } = first.stops[k];
        return { ...stop, snapped: { node, lat, lon, moved_m, note } };
      }),
    });
    this.#changed();
    await this.#startPar(request, generation);
  }

  /** Take `edge`, one of the next moves; nothing while a step is under way. */
  take(edge: number): Promise<void> {
    const answer = this.answer;
    if (!this.active || this.pending || !answer?.next.some((move) => move.edge === edge)) {
      return Promise.resolve();
    }
    return this.#step([...this.path, edge]);
  }

  /** Take the last edge back: the answer before, no request. */
  undo(): void {
    if (!this.canUndo) return;
    this.path = this.path.slice(0, -1);
    this.answers = this.answers.slice(0, -1);
    this.#store.update({ error: null });
    this.#changed();
  }

  /** Leave drawing mode; the drawing stays, drawn, until the stops change. */
  leave(): void {
    if (!this.active) return;
    this.active = false;
    if (this.#store.state.run.phase === "drawing") this.#store.update({ run: IDLE_RUN });
    this.#changed();
  }

  /** Store the drawing, with the longest move to the end if it has not reached it, and open it. */
  async finish(): Promise<void> {
    if (!this.canFinish || this.request === null) return;
    const answer = this.answer as DrawAnswer;
    const final = finalMove(answer);
    const path = answer.done || final === null ? this.path : [...this.path, final.edge];
    const generation = this.#generation;
    this.pending = true;
    this.#changed();
    try {
      const finished = await finishDraw({ request: this.request, path });
      if (generation !== this.#generation) return;
      const lengthM = answer.length_m + (answer.done || final === null ? 0 : final.length_m);
      this.finished = { routeId: finished.route_id, ratio: finished.ratio, lengthM };
      this.showing = "mine";
      await this.#hooks.open(finished.route_id); // the panel settles before the verdict shows
      this.active = false;
      this.pending = false;
      this.#changed();
    } catch (error) {
      if (generation !== this.#generation) return;
      this.pending = false;
      this.#store.update({ error: errorBody(error) });
      this.#changed();
    }
  }

  /** Once finished, show the engine's route, or the drawing again. */
  async show(which: "mine" | "engine"): Promise<void> {
    const id = which === "mine" ? this.finished?.routeId : this.par?.routeId;
    if (!id || this.finished === null) return;
    this.showing = which;
    this.#changed();
    await this.#hooks.open(id);
  }

  /** Forget the drawing, and cancel its par if it is still planned. */
  discard(): void {
    this.#generation += 1;
    this.#subscription?.close();
    this.#subscription = null;
    const jobId = this.par?.phase === "planning" ? this.par.jobId : null;
    if (jobId !== null) cancelJob(jobId).catch(() => undefined);
    this.request = null;
    this.path = [];
    this.answers = [];
    this.active = false;
    this.pending = false;
    this.par = null;
    this.finished = null;
    this.#changed();
  }

  /** The page is going away: cancel the par if it is still planned. */
  leavePage(): void {
    const jobId = this.par?.phase === "planning" ? this.par.jobId : null;
    if (jobId !== null) cancelJobOnLeave(jobId);
  }

  async #step(path: number[]): Promise<void> {
    const generation = this.#generation;
    this.pending = true;
    this.#changed();
    try {
      const answer = await draw({ request: this.request as RouteRequest, path });
      if (generation !== this.#generation) return;
      this.path = path;
      this.answers = [...this.answers, answer];
      this.#store.update({ error: null });
    } catch (error) {
      if (generation !== this.#generation) return;
      const body = errorBody(error);
      if (body.code === "ILLEGAL_PATH" && typeof body.index === "number") {
        const kept = Math.min(body.index, this.path.length);
        this.path = this.path.slice(0, kept);
        this.answers = this.answers.slice(0, kept + 1);
      }
      this.#store.update({ error: body });
    }
    this.pending = false;
    this.#changed();
  }

  /** Plan the engine's route for `request` aiming at DRAW_PAR_RATIO, or for a round trip, whose
   * ratio target the API refuses (its baseline is 0 m when it has no stop between), the request as
   * it is; follow its ratio. */
  async #startPar(request: RouteRequest, generation: number): Promise<void> {
    const stops = request.stops ?? [];
    const [start, end] = [stops[0], stops[stops.length - 1]];
    const roundTrip = stops.length >= 2 && start.lat === end.lat && start.lon === end.lon;
    const target = roundTrip ? null : DRAW_PAR_RATIO;
    const planning: Par = {
      phase: "planning",
      target,
      jobId: null,
      ratio: null,
      lengthM: null,
      routeId: null,
      note: null,
    };
    this.par = planning;
    this.#changed();
    const update = (patch: Partial<Par>) => {
      if (generation !== this.#generation || this.par === null) return;
      this.par = { ...this.par, ...patch };
      this.#changed();
    };
    let created;
    try {
      created = await createJob(
        target === null ? request : { ...request, target: { kind: "ratio", value: target } },
      );
    } catch (error) {
      update({ phase: "failed", note: errorBody(error).message });
      return;
    }
    if (generation !== this.#generation) {
      cancelJob(created.job_id).catch(() => undefined);
      return;
    }
    update({ jobId: created.job_id });
    const live = ({ metrics }: { metrics: Metrics }) =>
      update({ ratio: metrics.ratio, lengthM: metrics.length_m });
    this.#subscription = subscribe(created.events_url, {
      snapped: () => undefined,
      baseline: () => undefined,
      snapshot: live,
      progress: live,
      done: ({ route_id, metrics }) =>
        update({
          phase: "done",
          ratio: metrics.ratio,
          lengthM: metrics.length_m,
          routeId: route_id,
        }),
      error: (body) => update({ phase: "failed", note: body.message }),
      connection: (state) => {
        if (state === "lost") update({ phase: "failed", note: "its event stream was lost" });
      },
    });
  }

  #changed(): void {
    this.#view.show(this.finished === null ? this.answer : null, this.active);
    this.dispatchEvent(new Event("change"));
  }
}

/** Where the drawing stands, for the panel: what to do next. */
export function progressLine(drawing: Drawing): string {
  const answer = drawing.answer;
  if (drawing.finished !== null) return "Your route is stored.";
  if (answer === null) return drawing.pending ? "Asking for the start…" : "";
  if (!drawing.active) return "Left drawing mode: Draw it goes on with it.";
  if (answer.done) return "Arrived: Finish stores your route.";
  if (finalMove(answer) !== null) return "The end is one move away: Finish takes the longest.";
  if (answer.next.length === 0) return "Dead end: Undo (Backspace) and try another way.";
  if (drawing.path.length === 0) return "Click a highlighted road to take it.";
  const next = answer.reached.length;
  const last = answer.stops.length - 1;
  return next === last ? "Next: the end." : `Next: stop ${next + 1}.`;
}

/** The drawing's numbers: its length and the stops reached as it goes, its ratio once done ("you:
 * 7.2×"; the length for a round trip). */
export function youLine(drawing: Drawing, units: Units): string {
  const { finished, answer } = drawing;
  if (finished !== null) return `you: ${measure(finished.ratio, finished.lengthM, units)}`;
  if (answer === null) return "";
  if (answer.done) return `you: ${measure(answer.ratio, answer.length_m, units)}`;
  const stops = `${answer.reached.length} of ${answer.stops.length} stops`;
  return `you: ${formatDistance(answer.length_m, units)} so far · ${stops}`;
}

/** The par as it stands: "the engine: aiming for 10×…", "the engine: 7.2× so far, aiming for
 * 10×", "the engine: 10.1×" (where it stopped, short of the target or not), or that it could not
 * plan the route; a round trip's "the engine: planning…", then its ratio, or its length when it
 * has no stop between (no ratio). */
export function parLine(par: Par | null, units: Units): string {
  if (par === null) return "";
  if (par.phase === "failed") return `the engine could not plan it: ${par.note ?? "it failed"}`;
  const aim = par.target === null ? "" : `aiming for ${par.target}×`;
  if (par.lengthM === null) return `the engine: ${aim || "planning"}…`;
  const value = measure(par.ratio, par.lengthM, units);
  if (par.phase === "done") return `the engine: ${value}`;
  return aim ? `the engine: ${value} so far, ${aim}` : `the engine: ${value} so far`;
}

/** Who has won once the drawing is stored and the par done, by length (the same baseline): a tie
 * when the two read the same; null before. */
export function verdictOf(
  drawing: Pick<Drawing, "finished" | "par">,
  units: Units,
): "you" | "engine" | "tie" | null {
  const { finished, par } = drawing;
  if (finished === null || par?.phase !== "done" || par.lengthM === null) return null;
  const you = measure(finished.ratio, finished.lengthM, units);
  if (you === measure(par.ratio, par.lengthM, units)) return "tie";
  return finished.lengthM > par.lengthM ? "you" : "engine";
}

const OUTCOMES = { you: "you beat the engine.", engine: "the engine wins.", tie: "a tie." };

/** The verdict once the drawing is stored and the par done (`verdictOf`): "You 7.2× · the engine
 * 31×: the engine wins." */
export function verdictLine(drawing: Drawing, units: Units): string {
  const { finished, par } = drawing;
  const verdict = verdictOf(drawing, units);
  if (verdict === null || finished === null || par?.lengthM == null) return "";
  const you = measure(finished.ratio, finished.lengthM, units);
  const engine = measure(par.ratio, par.lengthM, units);
  return `You ${you} · the engine ${engine}: ${OUTCOMES[verdict]}`;
}

/** A ratio, or for a round trip (no ratio) the length. */
function measure(ratio: number | null, lengthM: number, units: Units): string {
  return ratio === null ? formatDistance(lengthM, units) : formatRatio(ratio);
}
