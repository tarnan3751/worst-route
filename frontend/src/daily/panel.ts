// The daily route (docs/reference/frontend.md, **Today's route**): one trip a day, the same for
// everyone on the server (GET /daily), at the top of the panel as "Today's route": its date and
// its two towns; while the server plans it, "Planning…" with the ratio so far, from its job's
// events (the stream a run follows, nothing drawn on the map and the job never cancelled: it is
// the server's); once done, its ratio, its share image and "Open it", which opens it as a
// ?route= link does; failed (or the server too busy to plan it, a 429), "Try again", which asks
// again (and so plans it again). The section collapses once the user starts a route of their own
// (Go, Reroll, Make it worse, Draw it), and "Today's route" in the header shows it again. Neither
// shows until the server has answered, nor on a server with no daily route (GET /daily refused).
// `?daily` opens today's route at start once it is done, unless the user has started a run or
// opened a route meanwhile (`openDailyAtStart`).
//
// `DailyRoute` holds the logic and no DOM, so the tests drive it (panel.test.ts); `mountDaily`
// draws it.
import { ApiError, getDaily, subscribe, type Subscription } from "../api/client";
import type { Daily, Metrics } from "../api/types";
import { formatDay, formatRatio } from "../format";
import { shareImageUrl } from "../share/share";
import type { Store } from "../state";
import { busy } from "../stops/stopList";
import { el } from "../util/dom";

/** Where today's route stands: being asked for, planned, done, failed, or none on this server. */
export type DailyPhase = "loading" | "planning" | "done" | "failed" | "none";

export class DailyRoute extends EventTarget {
  daily: Daily | null = null; // the server's last answer
  phase: DailyPhase = "loading";
  ratio: number | null = null; // so far while it is planned, the route's once done
  collapsed = false;
  #subscription: Subscription | null = null;

  /** Collapsed whenever a run of the user's own starts in `store`. */
  constructor(store: Store) {
    super();
    let phase = store.state.run.phase;
    store.watch(["run"], ({ run }) => {
      const own = run.phase === "starting" || run.phase === "drawing";
      if (own && phase !== run.phase) this.setCollapsed(true);
      phase = run.phase;
    });
  }

  /** Ask the server for today's route, and follow its plan while it is planned. */
  async load(): Promise<void> {
    this.#subscription?.close();
    this.#subscription = null;
    try {
      this.daily = await getDaily();
    } catch (error) {
      this.daily = null; // a busy server (TOO_MANY_JOBS) is a day to try again, not none
      this.phase = error instanceof ApiError && error.status === 429 ? "failed" : "none";
      this.#changed();
      return;
    }
    const { status, ratio, events_url: eventsUrl } = this.daily;
    this.phase = status;
    this.ratio = ratio;
    this.#changed();
    if (status === "planning" && eventsUrl !== null) this.#follow(eventsUrl);
  }

  /** Today's route's id once it is done (after its plan, while it is planned), or null when
   * there is none to open. */
  route(): Promise<string | null> {
    return new Promise((resolve) => {
      const settled = (): boolean => {
        if (this.phase === "loading" || this.phase === "planning") return false;
        resolve(this.phase === "done" ? (this.daily?.route_id ?? null) : null);
        return true;
      };
      if (settled()) return;
      const listener = () => {
        if (settled()) this.removeEventListener("change", listener);
      };
      this.addEventListener("change", listener);
    });
  }

  setCollapsed(collapsed: boolean): void {
    if (this.collapsed === collapsed) return;
    this.collapsed = collapsed;
    this.#changed();
  }

  /** The job's ratio as it grows; at its end, the server asked again for how it ended. */
  #follow(eventsUrl: string): void {
    const live = ({ metrics }: { metrics: Metrics }) => {
      this.ratio = metrics.ratio;
      this.#changed();
    };
    const ended = () => void this.load();
    this.#subscription = subscribe(eventsUrl, {
      snapped: () => undefined,
      baseline: () => undefined,
      snapshot: live,
      progress: live,
      done: ended,
      error: ended,
      connection: (state) => {
        if (state === "lost") ended();
      },
    });
  }

  #changed(): void {
    this.dispatchEvent(new Event("change"));
  }
}

/** Open today's route for `?daily` once it is done, with `open` (whether it opened), only while
 * no run has started and no route is shown since the page loaded: a run started or a route opened
 * first ends the wait, and the user's own stays. Whether it opened. */
export function openDailyAtStart(
  daily: DailyRoute,
  store: Store,
  open: (routeId: string) => Promise<boolean>,
): Promise<boolean> {
  const untouched = () => store.state.run.phase === "idle" && store.state.route === null;
  return new Promise((resolve) => {
    let waiting = true;
    store.watch(["run", "route"], () => {
      if (waiting && !untouched()) {
        waiting = false;
        resolve(false);
      }
    });
    void daily.route().then(async (id) => {
      if (!waiting) return;
      waiting = false;
      resolve(id !== null && untouched() ? await open(id) : false);
    });
  });
}

/** What the section says of today's route as it stands: "Planning… 7.2× so far", its ratio
 * ("12.3× longer than it had to be"), or that it could not be planned. */
export function dailyLine(daily: DailyRoute): string {
  const { phase, ratio } = daily;
  if (phase === "planning")
    return ratio === null ? "Planning…" : `Planning… ${formatRatio(ratio)} so far`;
  if (phase === "done") return `${formatRatio(ratio)} longer than it had to be`;
  if (phase === "failed") return "Today's route could not be planned.";
  return "";
}

/** The daily route's section in `root`, and its link in `header`, which shows it again once
 * collapsed; "Open it" calls `open` with the route's id (not while a run of `store` is under
 * way). */
export function mountDaily(
  root: HTMLElement,
  header: HTMLElement,
  daily: DailyRoute,
  store: Store,
  open: (routeId: string) => void,
): void {
  const show = el("button", "daily-show", "Today's route");
  show.type = "button";
  header.append(show);
  const day = el("p", "daily-day");
  const towns = el("p", "daily-trip");
  const line = el("p", "daily-line");
  const image = el("img", "daily-image");
  image.alt = "";
  const openIt = el("button", "primary", "Open it");
  const again = el("button", "", "Try again");
  const buttons = el("div", "buttons");
  buttons.append(openIt, again);
  root.replaceChildren(el("h2", "", "Today's route"), day, towns, line, image, buttons);

  show.addEventListener("click", () => daily.setCollapsed(false));
  again.addEventListener("click", () => void daily.load());
  openIt.addEventListener("click", () => {
    const id = daily.daily?.route_id;
    if (id) open(id);
  });

  const render = (): void => {
    const { daily: answer, phase, collapsed } = daily;
    const absent = phase === "loading" || phase === "none"; // shown once the server answers
    root.hidden = absent || collapsed;
    show.hidden = absent || !collapsed;
    day.textContent = answer ? formatDay(answer.date) : "";
    towns.textContent = answer ? answer.stops.map(({ label }) => label).join(" → ") : "";
    line.textContent = dailyLine(daily);
    const id = phase === "done" ? answer?.route_id : null;
    image.hidden = !id;
    if (id && image.getAttribute("src") !== shareImageUrl(id)) {
      image.src = shareImageUrl(id);
      image.alt = `Today's route: ${towns.textContent}, ${line.textContent}`;
    }
    openIt.hidden = !id;
    openIt.disabled = busy(store);
    again.hidden = phase !== "failed";
  };
  daily.addEventListener("change", render);
  store.watch(["run"], render);
}
