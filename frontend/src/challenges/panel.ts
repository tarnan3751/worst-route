// The challenges' section (docs/reference/frontend.md, **Challenges**), after today's route: the
// day's challenges, asked of the server at start (GET /challenges: "Loading today's challenges…"
// meanwhile, its first answer of a day taking about half a minute; "Couldn't load today's
// challenges." and "Try again" when it fails, and no section on a server without them, a 404), and
// first the challenge of another day a link names, "From <day>", asked for at once; each with its
// badge (progress.ts), title and blurb, and "Set it up", which pins its region, puts in its stops
// with their towns' names, and gives its mode, constraints and worst order (the other settings a
// request takes of those its defaults; its brand once the region's brands are known), forgetting
// the route shown and a drawing; then the stops are shown and the map fitted to them, ready for Go.
// Today's route made worse opens today's route instead, for Make it worse, and another day's is
// refused. The challenge stays set up (the store's `challenge`, which the link carries as
// `?challenge=`) while what it set is the store's, and today's route made worse while its route is
// shown. Under the list, how many challenges are done, all days', and "Reset badges", which forgets
// them. The section collapses once a run or a drawing of the user's own starts, and "Challenges" in
// the header shows it again.
//
// `ChallengePanel` holds the logic and no DOM, so the tests drive it (panel.test.ts);
// `mountChallenges` draws it.
import { ApiError, getChallenges } from "../api/client";
import type { BrandEntry, ChallengeBrand, ChallengeSetup, Daily } from "../api/types";
import type { DailyRoute } from "../daily/panel";
import { formatCount, formatDay } from "../format";
import { targetSettings } from "../share/share";
import type { AppState, Settings, Stop, Store } from "../state";
import { busy } from "../stops/stopList";
import { el } from "../util/dom";
import { lineBounds, round6, type Bounds } from "../util/geo";
import { challengesOf, dateOf, type Challenge } from "./list";
import type { ChallengeProgress } from "./progress";

/** What setting a challenge up asks of the rest of the app: to change the stops as an edit does
 * (forgetting the route shown and a drawing), to open a stored route as a link does, and to fit the
 * map to bounds. */
export interface SetupHooks {
  edit(stops: Stop[]): void;
  open(routeId: string): Promise<boolean>;
  fit(bounds: Bounds): void;
}

/** Where today's challenges stand: being asked for, listed, failed (to ask again), or none on
 * this server (a 404: no places index). */
export type ChallengesPhase = "loading" | "done" | "failed" | "none";

/** The settings `setup` gives over `settings`: `region` pinned, its mode with its item, its turns,
 * strict, hard mode, worst order and target, the automatic tier and leash, and Certified unticked;
 * the brand, the seed, the budget, the vehicle, the departure and the units left. */
export function challengeSettings(
  region: string,
  setup: ChallengeSetup,
  settings: Settings,
): Settings {
  const { constraints } = setup;
  return {
    ...settings,
    region,
    objective: setup.objective,
    item: setup.item ?? settings.item,
    tier: "auto",
    leashKm: "auto",
    turns: constraints.no_left ? "no_left" : constraints.no_right ? "no_right" : "any",
    strict: constraints.strict,
    hard: constraints.hard,
    reorder: setup.reorder,
    certified: false,
    ...targetSettings(setup.target),
  };
}

/** The stops `setup` puts in, each named for its town. */
function stopsOf(setup: ChallengeSetup): Stop[] {
  return setup.stops.map(({ lat, lon, label }) => ({
    lat: round6(lat),
    lon: round6(lon),
    label,
    snapped: null,
  }));
}

/** Whether what `challenge` set up is still `state`'s: its stops, region and settings (its brand
 * unless still to be picked: `brandPending`), or for today's route made worse that route shown. */
export function stillSetUp(
  challenge: Challenge,
  state: Readonly<AppState>,
  today: Daily | null,
  brandPending: boolean,
): boolean {
  const { setup } = challenge;
  if (setup === null) return state.route !== null && state.route.route_id === today?.route_id;
  const { settings, stops, brands } = state;
  const wanted = challengeSettings(challenge.region, setup, settings);
  const keys = Object.keys(wanted) as (keyof Settings)[];
  if (keys.some((key) => wanted[key] !== settings[key])) return false;
  const fixed = stopsOf(setup);
  const same = (stop: Stop, k: number) => stop.lat === fixed[k].lat && stop.lon === fixed[k].lon;
  if (stops.length !== fixed.length || !stops.every(same)) return false;
  if (setup.brand === null || brandPending || brands === null) return true;
  return brandOf(brands, setup.brand)?.id === settings.brand;
}

/** The brand of `brands` that `wanted` names: by its wikidata id, else by its name. */
function brandOf(brands: BrandEntry[], wanted: ChallengeBrand): BrandEntry | undefined {
  return (
    brands.find(({ wikidata }) => wikidata === wanted.wikidata) ??
    brands.find(({ name }) => name === wanted.name)
  );
}

/** The challenges of `date`, or null when the server does not answer (a day outside its window). */
function dayOf(date: string): Promise<Challenge[] | null> {
  return getChallenges(date).then(challengesOf, () => null);
}

export class ChallengePanel extends EventTarget {
  collapsed = false;
  phase: ChallengesPhase = "loading";
  readonly #store: Store;
  readonly #daily: DailyRoute;
  readonly #hooks: SetupHooks;
  #today: Challenge[] = []; // today's, once the server has answered
  #date: string | null = null; // their day, once the server has answered
  #other: Challenge | null = null; // another day's, a link's
  #brand: ChallengeBrand | null = null; // the brand to pick once the region's brands are known

  /** Collapsed whenever a run of the user's own starts in `store`. */
  constructor(store: Store, daily: DailyRoute, hooks: SetupHooks) {
    super();
    this.#store = store;
    this.#daily = daily;
    this.#hooks = hooks;
    let phase = store.state.run.phase;
    store.watch(["run"], ({ run }) => {
      const own = run.phase === "starting" || run.phase === "drawing";
      if (own && phase !== run.phase) this.setCollapsed(true);
      phase = run.phase;
    });
    store.watch(["brands"], () => this.#pickBrand());
    store.watch(["route", "stops", "settings", "brands"], (state) => {
      const challenge = this.#current();
      if (challenge === null) return;
      if (!stillSetUp(challenge, state, this.#todayRoute(), this.#brand !== null)) {
        this.#brand = null;
        store.update({ challenge: null });
      }
    });
    daily.addEventListener("change", () => this.#changed());
  }

  /** Ask the server for today's challenges (again, after a failure). */
  async load(): Promise<void> {
    this.phase = "loading";
    this.#changed();
    try {
      const answer = await getChallenges();
      this.#today = challengesOf(answer);
      this.#date = answer.date;
      const other = this.#other;
      if (other !== null && this.#today.some(({ id }) => id === other.id)) this.#other = null;
      this.phase = "done";
    } catch (error) {
      this.phase = error instanceof ApiError && error.status === 404 ? "none" : "failed";
    }
    this.#listed();
  }

  /** The challenges listed: another day's a link named first, then today's. */
  get listed(): Challenge[] {
    return this.#other === null ? this.#today : [this.#other, ...this.#today];
  }

  /** Whether `challenge` is another day's than today's (a link's). */
  otherDay(challenge: Challenge): boolean {
    return challenge === this.#other;
  }

  /** Why "Set it up" cannot be pressed for `challenge` now, or null. */
  refusal(challenge: Challenge): string | null {
    if (busy(this.#store)) return "A route is being planned or drawn.";
    if (challenge.setup !== null) return null;
    if (this.otherDay(challenge)) return "Only today's route can be made worse.";
    if (this.#daily.phase === "done") return null;
    return this.#daily.phase === "failed"
      ? "Today's route could not be planned."
      : "Today's route is not planned yet.";
  }

  /** Set `challenge` up: its region, stops and settings, or for today's route made worse that
   * route opened (once it is done, unless the user has started a run or opened a route
   * meanwhile). Nothing while a run or a drawing goes, nor for another day's route made worse. */
  setUp(challenge: Challenge): void {
    const store = this.#store;
    if (busy(store)) return;
    const { setup } = challenge;
    if (setup === null) {
      if (!this.otherDay(challenge)) void this.#openToday(challenge);
      return;
    }
    const stops = stopsOf(setup);
    this.#hooks.edit(stops);
    this.#brand = setup.brand;
    store.update({
      settings: challengeSettings(challenge.region, setup, store.state.settings),
      challenge: challenge.id,
    });
    if (store.state.brands !== null) this.#pickBrand(); // the region's already
    const line = lineBounds(stops.map(({ lat, lon }) => [lon, lat]));
    if (line !== null) this.#hooks.fit(line);
  }

  /** Set up the challenge a link names (`?challenge=<id>`), as its Set it up does, once it has
   * come (`#find`), unless the user has started a run (a failed one too) or opened a route
   * meanwhile. Nothing for an id without a day. */
  async setUpLink(id: string): Promise<void> {
    const date = dateOf(id);
    if (date === null) return;
    const shown = this.#store.state.route;
    let moved = false;
    this.#store.watch(["run", "route"], ({ run, route }) => {
      moved ||= run.phase === "starting" || run.phase === "drawing" || route !== shown;
    });
    const challenge = await this.#find(id, date);
    if (challenge !== undefined && !moved) this.setUp(challenge);
  }

  setCollapsed(collapsed: boolean): void {
    if (this.collapsed === collapsed) return;
    this.collapsed = collapsed;
    this.#changed();
  }

  /** The day today's challenges are of: the server's answer's, else today's route's, else the
   * browser's UTC date. */
  #todayDate(): string {
    return this.#date ?? this.#daily.daily?.date ?? new Date().toISOString().slice(0, 10);
  }

  /** Today's challenges once the server has answered with them (after a Try again, when it
   * failed), or null on a server without them. */
  #todays(): Promise<Challenge[] | null> {
    return new Promise((resolve) => {
      const settled = (): boolean => {
        if (this.phase === "loading" || this.phase === "failed") return false;
        resolve(this.phase === "done" ? this.#today : null);
        return true;
      };
      if (settled()) return;
      const listener = () => {
        if (settled()) this.removeEventListener("listed", listener);
      };
      this.addEventListener("listed", listener);
    });
  }

  /** The challenge `id` of `date`: of today's, once they have come, when `date` is today; else
   * (or when today's turn out to be of another day) of its day's, asked of the server at once,
   * beside today's, and listed first, unless today's hold it after all. Undefined for a day the
   * server does not answer (outside its window) or an id its day lacks. */
  async #find(id: string, date: string): Promise<Challenge | undefined> {
    if (date === this.#todayDate()) {
      const today = await this.#todays();
      const found = today?.find((c) => c.id === id);
      if (found !== undefined || today === null || this.#date === date) return found;
    }
    const day = await dayOf(date);
    const own = this.#today.find((c) => c.id === id);
    if (own !== undefined) return own;
    const found = day?.find((c) => c.id === id);
    if (found !== undefined) {
      this.#other = found;
      this.#listed();
    }
    return found;
  }

  #current(): Challenge | null {
    const id = this.#store.state.challenge;
    return this.listed.find((challenge) => challenge.id === id) ?? null;
  }

  #todayRoute(): Daily | null {
    return this.#daily.phase === "done" ? this.#daily.daily : null;
  }

  /** The brand the challenge set up visits, by its wikidata id, else its name, once the brands
   * of the region it pins are known (the brands the store holds are always the region's). */
  #pickBrand(): void {
    const wanted = this.#brand;
    const { brands, region, settings } = this.#store.state;
    if (wanted === null || brands === null || region?.region !== this.#current()?.region) return;
    this.#brand = null;
    const brand = brandOf(brands, wanted);
    if (brand !== undefined && brand.id !== settings.brand) {
      this.#store.updateSettings({ brand: brand.id });
    }
  }

  async #openToday(challenge: Challenge): Promise<void> {
    const store = this.#store;
    const shown = store.state.route;
    const id = await this.#daily.route();
    if (id === null || busy(store) || store.state.route !== shown) return; // the user moved on
    if (await this.#hooks.open(id)) store.update({ challenge: challenge.id });
  }

  /** The list has changed: "listed" (progress.ts grades the route shown again), then "change". */
  #listed(): void {
    this.dispatchEvent(new Event("listed"));
    this.#changed();
  }

  #changed(): void {
    this.dispatchEvent(new Event("change"));
  }
}

/** What to do once `challenge` is set up, under its title. */
export function setUpLine(challenge: Challenge): string {
  return challenge.setup === null ? "Set up. Press Make it worse." : "Set up. Press Go.";
}

/** How many challenges are done, all days': "1 challenge done", "12 challenges done". */
export function doneLine(count: number): string {
  return `${formatCount(count)} challenge${count === 1 ? "" : "s"} done`;
}

/** The challenges' section in `root`, and its link in `header`, which shows it again once
 * collapsed; `reveal` shows what a challenge set up needs next (the stops, or Make it worse). */
export function mountChallenges(
  root: HTMLElement,
  header: HTMLElement,
  panel: ChallengePanel,
  progress: ChallengeProgress,
  store: Store,
  reveal: (challenge: Challenge) => void,
): void {
  const show = el("button", "challenges-show", "Challenges");
  show.type = "button";
  header.append(show);
  const loading = el("p", "hint", "Loading today's challenges…");
  const again = el("button", "", "Try again");
  again.type = "button";
  const buttons = el("div", "buttons");
  buttons.append(again);
  const failed = el("div");
  failed.append(el("p", "hint", "Couldn't load today's challenges."), buttons);
  const list = el("ul", "challenge-list");
  const count = el("p", "hint");
  const reset = el("button", "challenges-reset", "Reset badges");
  reset.type = "button";
  root.replaceChildren(el("h2", "", "Challenges"), loading, failed, list, count, reset);
  show.addEventListener("click", () => panel.setCollapsed(false));
  again.addEventListener("click", () => void panel.load());
  reset.addEventListener("click", () => progress.reset());

  let shown = "";
  const render = (): void => {
    const state = store.state;
    const listed = panel.listed;
    const absent = listed.length === 0 && panel.phase === "none";
    root.hidden = absent || panel.collapsed;
    show.hidden = absent || !panel.collapsed;
    loading.hidden = panel.phase !== "loading";
    failed.hidden = panel.phase !== "failed";
    const done = Object.keys(progress.done).length;
    count.textContent = doneLine(done);
    count.hidden = done === 0;
    reset.hidden = done === 0;
    const refusals = listed.map((challenge) => panel.refusal(challenge));
    const key = JSON.stringify([
      listed.map(({ id }) => id),
      progress.done,
      refusals,
      state.challenge,
    ]);
    if (key === shown) return;
    shown = key;
    list.replaceChildren(
      ...listed.map((challenge, k) => {
        const day = progress.done[challenge.id];
        const current = state.challenge === challenge.id;
        const row = el("li", `challenge${day ? " done" : ""}${current ? " current" : ""}`);
        const badge = el("span", "challenge-badge", day ? "✓" : "");
        badge.title = day ? `Completed ${formatDay(day)}` : "Not completed yet";
        const body = el("div", "body");
        if (panel.otherDay(challenge)) {
          body.append(el("p", "hint", `From ${formatDay(challenge.date)}`));
        }
        body.append(el("strong", "", challenge.title), el("p", "hint", challenge.blurb));
        if (current) body.append(el("p", "challenge-next", setUpLine(challenge)));
        const button = el("button", "", "Set it up");
        button.type = "button";
        button.disabled = refusals[k] !== null;
        button.title = refusals[k] ?? "";
        button.addEventListener("click", () => {
          panel.setUp(challenge);
          reveal(challenge);
        });
        row.append(badge, body, button);
        return row;
      }),
    );
  };
  panel.addEventListener("change", render);
  progress.addEventListener("change", render);
  store.watch(["run", "challenge"], render);
}
