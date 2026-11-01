// The challenges' section (docs/reference/frontend.md, **Challenges**), after today's route: the
// challenges of the regions the server serves (list.ts; today's once GET /daily answers with one),
// each with its badge (progress.ts), title and blurb, and "Set it up", which pins its region, puts
// in its stops (or, for any trip in the region, keeps the stops when they all lie in its bbox, else
// none), and gives the settings it fixes (the rest of those their defaults; its brand once the
// region's brands are known), forgetting the route shown and a drawing; then the stops are shown
// and the map fitted to them (or to the region), ready for Go, or Draw it. Today's challenge opens
// today's route instead, for Make it worse. The challenge stays set up (the store's `challenge`,
// which the link carries as `?challenge=`) while what it set is the store's, and today's while its
// route is shown. The section collapses once a run or a drawing of the user's own starts, and
// "Challenges" in the header shows it again; "Reset badges" forgets every completion.
//
// `ChallengePanel` holds the logic and no DOM, so the tests drive it (panel.test.ts);
// `mountChallenges` draws it.
import type { Bbox, BrandEntry, Daily } from "../api/types";
import type { DailyRoute } from "../daily/panel";
import { formatDay } from "../format";
import { targetSettings } from "../share/share";
import type { AppState, Settings, Stop, Store } from "../state";
import { busy } from "../stops/stopList";
import { el } from "../util/dom";
import { bboxBounds, lineBounds, round6, type Bounds } from "../util/geo";
import { CHALLENGES, type Challenge, type ChallengeBrand } from "./list";
import type { ChallengeProgress } from "./progress";

/** What setting a challenge up asks of the rest of the app: to change the stops as an edit does
 * (forgetting the route shown and a drawing), to open a stored route as a link does, and to fit the
 * map to bounds. */
export interface SetupHooks {
  edit(stops: Stop[]): void;
  open(routeId: string): Promise<boolean>;
  fit(bounds: Bounds): void;
}

/** The settings a challenge sets up over `settings`: its region pinned and the settings it fixes,
 * the rest of those (the mode, the tier, the leash, the turns, hard mode, the worst order, the
 * target) at their defaults, and Certified unticked; the brand, the seed, the budget, the vehicle,
 * the departure and the units left. */
export function challengeSettings(challenge: Challenge, settings: Settings): Settings {
  const fixed = challenge.settings;
  return {
    ...settings,
    region: challenge.region,
    objective: fixed.objective ?? "distance",
    item: fixed.item ?? settings.item,
    tier: fixed.tier ?? "auto",
    leashKm: fixed.leashKm ?? "auto",
    turns: fixed.turns ?? "any",
    strict: fixed.strict ?? false,
    hard: fixed.hard ?? false,
    reorder: fixed.reorder ?? false,
    certified: false,
    ...targetSettings(fixed.target ?? { kind: "max" }),
  };
}

const inside = ([west, south, east, north]: Bbox, { lat, lon }: Stop): boolean =>
  lon >= west && lon <= east && lat >= south && lat <= north;

/** Whether what `challenge` set up is still `state`'s: its stops, region and settings (its brand
 * unless still to be picked: `brandPending`), or for today's challenge today's route shown. */
export function stillSetUp(
  challenge: Challenge,
  state: Readonly<AppState>,
  today: Daily | null,
  brandPending: boolean,
): boolean {
  if (challenge.goal.kind === "daily_worse") {
    return state.route !== null && state.route.route_id === today?.route_id;
  }
  const { settings, stops, brands } = state;
  const wanted = challengeSettings(challenge, settings);
  const keys = Object.keys(wanted) as (keyof Settings)[];
  if (keys.some((key) => wanted[key] !== settings[key])) return false;
  const fixed = challenge.stops;
  const same = (stop: Stop, k: number) =>
    fixed !== null && stop.lat === round6(fixed[k].lat) && stop.lon === round6(fixed[k].lon);
  if (fixed !== null && (stops.length !== fixed.length || !stops.every(same))) return false;
  const brand = challenge.settings.brand;
  if (brand === undefined || brandPending || brands === null) return true;
  return brandOf(brands, brand)?.id === settings.brand;
}

/** The brand of `brands` that `wanted` names: by its wikidata id, else by its name. */
function brandOf(brands: BrandEntry[], wanted: ChallengeBrand): BrandEntry | undefined {
  return (
    brands.find(({ wikidata }) => wikidata === wanted.wikidata) ??
    brands.find(({ name }) => name === wanted.name)
  );
}

export class ChallengePanel extends EventTarget {
  collapsed = false;
  readonly #store: Store;
  readonly #daily: DailyRoute;
  readonly #hooks: SetupHooks;
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
      if (!stillSetUp(challenge, state, this.#today(), this.#brand !== null)) {
        this.#brand = null;
        store.update({ challenge: null });
      }
    });
    daily.addEventListener("change", () => this.#changed());
  }

  /** The challenges listed: those of the regions the server serves (none until it has said), and
   * today's challenge once GET /daily has answered with a day, whichever region it runs on. */
  get listed(): Challenge[] {
    const { regions } = this.#store.state;
    if (regions === null) return [];
    const { phase } = this.#daily;
    const today = phase === "planning" || phase === "done" || phase === "failed";
    return CHALLENGES.filter(({ region }) =>
      region === null ? today : regions.some((r) => r.region === region),
    );
  }

  /** Why "Set it up" cannot be pressed for `challenge` now, or null. */
  refusal(challenge: Challenge): string | null {
    if (busy(this.#store)) return "a route is being planned or drawn";
    if (challenge.goal.kind === "daily_worse" && this.#daily.phase !== "done") {
      return this.#daily.phase === "failed"
        ? "today's route could not be planned"
        : "today's route is not planned yet";
    }
    return null;
  }

  /** Set `challenge` up: its region, stops and settings, or today's route opened (once it is
   * done, unless the user has started a run or opened a route meanwhile). Nothing while a run or
   * a drawing goes, nor for a region the server does not serve. */
  setUp(challenge: Challenge): void {
    const store = this.#store;
    if (busy(store)) return;
    if (challenge.goal.kind === "daily_worse") {
      void this.#openToday(challenge);
      return;
    }
    const region = store.state.regions?.find((r) => r.region === challenge.region);
    if (region === undefined) return;
    const { stops } = store.state;
    const fixed = challenge.stops?.map(({ lat, lon }) => ({ lat, lon, snapped: null }));
    this.#hooks.edit(fixed ?? (stops.every((stop) => inside(region.bbox, stop)) ? stops : []));
    this.#brand = challenge.settings.brand ?? null;
    store.update({
      settings: challengeSettings(challenge, store.state.settings),
      challenge: challenge.id,
    });
    if (store.state.brands !== null) this.#pickBrand(); // the region's already
    const line = fixed && lineBounds(fixed.map(({ lat, lon }) => [lon, lat]));
    this.#hooks.fit(line ?? bboxBounds(region.bbox));
  }

  /** Set up the challenge a link names (`?challenge=<id>`), as its Set it up does: today's waits
   * for today's route (`setUp`); an id the list lacks sets nothing up. */
  setUpLink(id: string): void {
    const challenge = CHALLENGES.find((c) => c.id === id);
    if (challenge !== undefined) this.setUp(challenge);
  }

  setCollapsed(collapsed: boolean): void {
    if (this.collapsed === collapsed) return;
    this.collapsed = collapsed;
    this.#changed();
  }

  #current(): Challenge | null {
    const id = this.#store.state.challenge;
    return CHALLENGES.find((challenge) => challenge.id === id) ?? null;
  }

  #today(): Daily | null {
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

  #changed(): void {
    this.dispatchEvent(new Event("change"));
  }
}

/** What to do once `challenge` is set up, under its title. */
export function setUpLine(challenge: Challenge, state: Readonly<AppState>): string {
  if (challenge.goal.kind === "daily_worse") return "Set up: press Make it worse.";
  if (challenge.goal.kind === "drawn") return "Set up: press Draw it.";
  if (state.stops.length >= 2) return "Set up: press Go.";
  return "Set up: place a start and an end, then press Go.";
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
  const list = el("ul", "challenge-list");
  const reset = el("button", "challenges-reset", "Reset badges");
  reset.type = "button";
  root.replaceChildren(el("h2", "", "Challenges"), list, reset);
  show.addEventListener("click", () => panel.setCollapsed(false));
  reset.addEventListener("click", () => progress.reset());

  let shown = "";
  const render = (): void => {
    const state = store.state;
    const listed = panel.listed;
    root.hidden = listed.length === 0 || panel.collapsed;
    show.hidden = listed.length === 0 || !panel.collapsed;
    reset.hidden = Object.keys(progress.done).length === 0;
    const refusals = listed.map((challenge) => panel.refusal(challenge));
    const key = JSON.stringify([
      listed.map(({ id }) => id),
      progress.done,
      refusals,
      state.challenge,
      state.stops.length,
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
        body.append(el("strong", "", challenge.title), el("p", "hint", challenge.blurb));
        if (current) body.append(el("p", "challenge-next", setUpLine(challenge, state)));
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
  store.watch(["regions", "run", "stops", "challenge"], render);
}
