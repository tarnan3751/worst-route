// The toasts (docs/reference/frontend.md, **Toasts**): one line at a time in a strip under the
// map, the next every 4 s. While a run goes, three lines about waiting and the live ratio
// ("Currently 17.2× worse"), four slots in a fixed cycle, the ratio's skipped while the run has
// none, so no line jumps when it comes; once a route is stored, its document's toasts, in order,
// looping. A notice (a challenge completed: `notify`) is shown once, for a period, before the
// rotation goes on where it was. The pointer over the strip holds the line shown; a click shows
// the next. Nothing while no run goes, no route is shown and no notice waits.
//
// `Toasts` holds the rotation and no DOM, so the tests drive it on fake timers; `mountToasts`
// draws it.
import type { RouteDocument } from "./api/types";
import { formatRatio } from "./format";
import type { AppState, Store } from "./state";
import { el } from "./util/dom";

const TOAST_MS = 4000;
export const WAITING = ["Finding a worse way.", "Skipping the shortcut.", "Still going."];

/** Where the lines come from: the run going, the route shown, or nowhere. */
type Source = "run" | RouteDocument | null;

function sourceOf({ run, route }: Readonly<AppState>): Source {
  if (run.phase === "starting" || run.phase === "running") return "run";
  return route?.toasts?.length ? route : null;
}

/** The line shown; "change" when it does. */
export class Toasts extends EventTarget {
  text: string | null = null;
  readonly #store: Store;
  #source: Source = null;
  #k = 0; // the line shown, counted from the first since the source changed
  #notices: string[] = []; // lines to show once each, the first shown now
  #held = false;
  #timer: ReturnType<typeof setTimeout> | undefined;

  constructor(store: Store) {
    super();
    this.#store = store;
    store.watch(["run", "route"], (state) => {
      const source = sourceOf(state);
      if (source !== this.#source) {
        this.#source = source;
        this.#k = 0;
        this.#arm();
      }
      this.#show(); // the live ratio changes with every progress
    });
  }

  /** Show the next line now. */
  next(): void {
    if (this.#notices.length > 0) this.#notices.shift();
    else this.#k++;
    this.#arm();
    this.#show();
  }

  /** Show `line` once, for a period, after the notices before it, then go on with the lines. */
  notify(line: string): void {
    this.#notices.push(line);
    if (this.#notices.length > 1) return;
    this.#arm();
    this.#show();
  }

  /** Hold the line shown (the pointer is over it), or let it go on. */
  hold(held: boolean): void {
    this.#held = held;
    this.#arm();
  }

  /** The lines it goes through now; null for the ratio's slot while there is none. */
  #lines(): (string | null)[] {
    const source = this.#source;
    if (source === null) return [];
    if (source !== "run") return source.toasts ?? [];
    const ratio = this.#store.state.run.metrics?.ratio;
    return [...WAITING, ratio == null ? null : `Currently ${formatRatio(ratio)} worse`];
  }

  /** The next line in TOAST_MS, unless held or there is none. */
  #arm(): void {
    clearTimeout(this.#timer);
    if ((this.#source !== null || this.#notices.length > 0) && !this.#held) {
      this.#timer = setTimeout(() => this.next(), TOAST_MS);
    }
  }

  #show(): void {
    const lines = this.#lines();
    for (let n = 0; n < lines.length && lines[this.#k % lines.length] === null; n++) this.#k++;
    const text = this.#notices[0] ?? (lines.length > 0 ? lines[this.#k % lines.length] : null);
    if (text === this.text) return;
    this.text = text;
    this.dispatchEvent(new Event("change"));
  }
}

/** The toast strip in `root`: `toasts`' line; the pointer over it holds it, a click moves on. */
export function mountToasts(root: HTMLElement, toasts: Toasts): void {
  const line = el("p", "toast");
  root.replaceChildren(line);
  root.addEventListener("mouseenter", () => toasts.hold(true));
  root.addEventListener("mouseleave", () => toasts.hold(false));
  root.addEventListener("click", () => toasts.next());
  const render = () => {
    line.textContent = toasts.text ?? "";
    root.classList.toggle("idle", toasts.text === null);
  };
  toasts.addEventListener("change", render);
  render();
}
