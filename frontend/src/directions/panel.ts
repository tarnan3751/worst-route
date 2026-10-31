// The directions (docs/reference/frontend.md, **Directions**): the stored route's turn-by-turn
// steps, from its document's `directions`, listed under the run's stat line; empty until a route
// is stored. A driving step reads "Turn left onto X · 0.3 mi · 2 min" (in the units the settings
// show), a stop and the arrival are bold, and a joke is in italics with no number of its own (the
// numbers are the document's). Hovering or focusing a driving step highlights its edges on the
// map; clicking a step, or Enter, pans the map to where it starts.
//
// The document gives no offsets of its edges in its geometry, so `StepLocator` places the steps
// along it by distance: the driving steps cover the route's edges in order and their distances
// add up to its length, so each step ends at the point of the geometry nearest its distance past
// the point where the one before it ended (measured on the WGS84 ellipsoid, scaled to the steps'
// total). Starting each step at a point, not at a sum from the route's start, keeps the error
// from growing along a long route.
//
// `DirectionsPanel` holds the logic and no DOM, so the tests drive it; `mountDirections` draws it
// with listeners on the list, none per step (a long route has 1,500 of them), and one step in
// the tab order at a time: Tab enters the list, the arrow keys move along it.
import type { DirectionStep, LineString, LonLat, StepKind } from "../api/types";
import { formatDistance, formatDuration, type Units } from "../format";
import type { Store } from "../state";
import { el } from "../util/dom";

const RADIANS = Math.PI / 180;
const WGS84_A = 6378137; // the semi-major axis, in metres
const WGS84_E2 = 6.69437999014e-3; // the first eccentricity, squared

/** The length of segment p–q in metres, on the WGS84 ellipsoid at its middle latitude: exact
 * enough for a segment of a road. */
function segmentM([lon1, lat1]: LonLat, [lon2, lat2]: LonLat): number {
  const lat = ((lat1 + lat2) / 2) * RADIANS;
  const w = 1 - WGS84_E2 * Math.sin(lat) ** 2;
  const east = (lon2 - lon1) * RADIANS * (WGS84_A / Math.sqrt(w)) * Math.cos(lat);
  const north = (lat2 - lat1) * RADIANS * ((WGS84_A * (1 - WGS84_E2)) / w ** 1.5);
  return Math.hypot(east, north);
}

/** The point of `along` nearest `d`, from point `from` on. */
function nearest(along: number[], d: number, from: number): number {
  let lo = from;
  let hi = along.length - 1; // the first point at or past d, or the last
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (along[mid] < d) lo = mid + 1;
    else hi = mid;
  }
  return hi > from && d - along[hi - 1] < along[hi] - d ? hi - 1 : hi;
}

/** Where a route's steps lie along its geometry: each step's first and last point. */
export class StepLocator {
  readonly #points: LonLat[];
  readonly #spans: [number, number][];

  constructor(geometry: LineString, steps: DirectionStep[]) {
    const points = geometry.coordinates;
    const along = [0]; // how far along the line each point is
    for (let k = 1; k < points.length; k++) {
      along.push(along[k - 1] + segmentM(points[k - 1], points[k]));
    }
    const scale = along[along.length - 1] / steps.reduce((sum, step) => sum + step.distance_m, 0);
    let at = 0; // the point the next step starts at
    this.#spans = steps.map(({ distance_m, edge_range: [a, b] }) => {
      const from = at;
      if (b > a) at = nearest(along, along[from] + distance_m * scale, from + 1);
      return [from, at];
    });
    this.#points = points;
  }

  /** The stretch step `k` drives, from its first node to its last; null for a step that drives
   * nothing (a stop, the arrival, a joke). */
  line(k: number): LonLat[] | null {
    const [from, to] = this.#spans[k];
    return to > from ? this.#points.slice(from, to + 1) : null;
  }

  /** Where step `k` starts. */
  start(k: number): LonLat {
    return this.#points[this.#spans[k][0]];
  }
}

/** A step as the list shows it: its classes, its number (none for a joke), its text, and for a
 * driving step its distance and time. */
export interface StepRow {
  kind: StepKind;
  className: string;
  number: number | null;
  text: string;
  detail: string;
}

/** The list's rows for `steps`, their distances in `units`. */
export function stepRows(steps: DirectionStep[], units: Units): StepRow[] {
  return steps.map(({ kind, step, text, distance_m, time_s, edge_range: [a, b] }) => ({
    kind,
    className: `step step-${kind}`,
    number: kind === "joke" ? null : step,
    text,
    detail: b > a ? `${formatDistance(distance_m, units)} · ${formatDuration(time_s)}` : "",
  }));
}

/** What the panel asks of the map: a stretch of the route highlighted (or none), and a pan. */
export interface DirectionsView {
  highlight(line: LonLat[] | null): void;
  panTo(point: LonLat): void;
}

/** The directions of the route shown; "change" when its rows do. */
export class DirectionsPanel extends EventTarget {
  rows: StepRow[] = [];
  readonly #view: DirectionsView;
  #locator: StepLocator | null = null;
  #lit: number | null = null;

  constructor(store: Store, view: DirectionsView) {
    super();
    this.#view = view;
    let shown: unknown[] = [];
    store.watch(["route", "settings"], ({ route, settings }) => {
      if (route === shown[0] && settings.units === shown[1]) return;
      const steps = route?.directions ?? [];
      if (route !== shown[0]) {
        this.#locator = route && steps.length > 0 ? new StepLocator(route.geometry, steps) : null;
        this.#lit = null;
        view.highlight(null);
      }
      shown = [route, settings.units];
      this.rows = stepRows(steps, settings.units);
      this.dispatchEvent(new Event("change"));
    });
  }

  /** Highlight on the map the stretch step `k` drives; nothing for null, or a step that drives
   * nothing. */
  hover(k: number | null): void {
    if (k === this.#lit) return;
    this.#lit = k;
    this.#view.highlight(k === null ? null : (this.#locator?.line(k) ?? null));
  }

  /** Pan the map to where step `k` starts. */
  select(k: number): void {
    if (this.#locator) this.#view.panTo(this.#locator.start(k));
  }
}

/** The directions section in `root`: a collapsible list of `panel`'s rows. */
export function mountDirections(root: HTMLElement, panel: DirectionsPanel): void {
  const details = el("details");
  details.open = true;
  const summary = el("summary");
  summary.append(el("h2", "", "Directions"));
  const hint = el("p", "hint", "The directions come once a route is stored.");
  const list = el("ol", "directions");
  details.append(summary, hint, list);
  root.replaceChildren(details);

  // A step of the list (not a joke) and its index among the rows.
  const itemOf = (target: EventTarget | null): HTMLElement | null =>
    target instanceof Element ? target.closest<HTMLElement>("li[data-k]") : null;
  const indexOf = (item: HTMLElement | null): number | null =>
    item === null ? null : Number(item.dataset.k);
  const step = (item: HTMLElement, forward: boolean): HTMLElement | null => {
    let next = forward ? item.nextElementSibling : item.previousElementSibling;
    while (next !== null && !(next instanceof HTMLElement && "k" in next.dataset)) {
      next = forward ? next.nextElementSibling : next.previousElementSibling;
    }
    return next;
  };

  list.addEventListener("mouseover", (event) => panel.hover(indexOf(itemOf(event.target))));
  list.addEventListener("mouseleave", () => panel.hover(null));
  list.addEventListener("focusin", (event) => {
    const item = itemOf(event.target);
    if (item === null) return;
    for (const other of list.querySelectorAll<HTMLElement>('li[tabindex="0"]')) other.tabIndex = -1;
    item.tabIndex = 0; // where Tab comes back to
    panel.hover(indexOf(item));
  });
  list.addEventListener("focusout", (event) => {
    if (!list.contains(event.relatedTarget as Node | null)) panel.hover(null);
  });
  list.addEventListener("click", (event) => {
    const k = indexOf(itemOf(event.target));
    if (k !== null) panel.select(k);
  });
  list.addEventListener("keydown", (event) => {
    const item = itemOf(event.target);
    if (item === null) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      step(item, event.key === "ArrowDown")?.focus();
    } else if (event.key === "Enter" || event.key === " ") {
      panel.select(Number(item.dataset.k));
    } else {
      return;
    }
    event.preventDefault();
  });

  const render = () => {
    hint.hidden = panel.rows.length > 0;
    const items = document.createDocumentFragment();
    panel.rows.forEach((row, k) => {
      const item = el("li", row.className);
      if (row.number !== null) {
        item.dataset.k = String(k);
        item.tabIndex = -1;
        item.append(el("span", "num", `${row.number}.`));
      }
      item.append(el("span", "text", row.text));
      if (row.detail) item.append(el("span", "detail", ` · ${row.detail}`));
      items.append(item);
    });
    list.replaceChildren(items);
    const first = list.querySelector<HTMLElement>("li[data-k]");
    if (first) first.tabIndex = 0;
  };
  panel.addEventListener("change", render);
  render();
}
