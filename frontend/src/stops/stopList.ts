// The stops (docs/reference/frontend.md, **Stops**): how a click, a drag or a removal changes
// them, and the list in the panel.
//
// The first click is the start, the second the end, and later ones are stops along the way,
// inserted before the end (`placeStop`, which a place search's pick goes through too). A click on
// a city's label places the stop at the city's point with its name (map/cities.ts), which the
// list shows and the request sends as the stop's label, until the stop is dragged. A stop
// keeps its coordinates at 6 decimals (util/geo.ts, round6). An edit forgets where the last job
// snapped the stops; once a job has run, the list shows each stop's node, how far it moved and
// the API's note, and when its route visits them in another order than the list's (the worst
// order), the place each is visited in ("visited 3rd"), the list keeping its numbering. The stop
// an error names (`stop_index`) is highlighted, here and on the map, and
// every stop along the way for an error on `field: "stops"` (the alphabet route takes a start and
// an end only, which the list's hint says under it). Under the list's heading, the build the stops
// run on (pick.ts, `pickLine`: "Oregon · all roads", and why), then the place search (search.ts).
import { formatDistance, formatLatLon, formatOrdinal, type Units } from "../format";
import type { Stop, Store } from "../state";
import { el } from "../util/dom";
import { round6 } from "../util/geo";
import { pickLine } from "./pick";

export type Role = "start" | "via" | "end";

/** What a stop is, by its position among `count`. */
export function roleOf(index: number, count: number): Role {
  if (index === 0) return "start";
  return index === count - 1 ? "end" : "via";
}

const forget = (stops: Stop[]): Stop[] => stops.map((stop) => ({ ...stop, snapped: null }));

/** The stops after a click at (lat, lon), on the label of the city `label` names, if any. */
export function withStopAdded(stops: Stop[], lat: number, lon: number, label?: string): Stop[] {
  const stop: Stop =
    label === undefined ? { lat, lon, snapped: null } : { lat, lon, label, snapped: null };
  const kept = forget(stops);
  if (kept.length < 2) return [...kept, stop];
  return [...kept.slice(0, -1), stop, kept[kept.length - 1]];
}

/** The stops after stop `index` was dragged to (lat, lon), where it is no longer its city. */
export function withStopMoved(stops: Stop[], index: number, lat: number, lon: number): Stop[] {
  return forget(stops).map((stop, k) => (k === index ? { lat, lon, snapped: null } : stop));
}

/** The stops without stop `index`. */
export function withStopRemoved(stops: Stop[], index: number): Stop[] {
  return forget(stops).filter((_, k) => k !== index);
}

/** Whether `error` names stop `index` of `count`: by its `stop_index`, or, on `field: "stops"`
 * (a stop along the way the alphabet route refuses), as a stop along the way. */
export function stopErrored(
  error: { [field: string]: unknown } | null,
  index: number,
  count: number,
): boolean {
  if (error?.stop_index === index) return true;
  return error?.field === "stops" && roleOf(index, count) === "via";
}

/** Whether a job is starting or running, or a route is being drawn (stops and settings are then
 * locked). */
export function busy(store: Store): boolean {
  const { phase } = store.state.run;
  return phase === "starting" || phase === "running" || phase === "drawing";
}

/** Place the next stop at (lat, lon), as a click on the map does, through `edit`, labelled with
 * the name of the city whose label was clicked (`label`); nothing while a job runs. Whether it was
 * placed. */
export function placeStop(
  store: Store,
  edit: (stops: Stop[]) => void,
  lat: number,
  lon: number,
  label?: string,
): boolean {
  if (busy(store)) return false;
  edit(withStopAdded(store.state.stops, round6(lat), round6(lon), label));
  return true;
}

const ROLE_NAMES: Record<Role, string> = { start: "Start", via: "Stop", end: "End" };

/** The request's label for `stop`, at `index` of `count`: its city's name whatever its role, which
 * the directions say ("Arrive at Portland; continue", "Arrive at Portland"); else a stop along the
 * way named as its marker numbers it, "Stop 2" ("Arrive at Stop 2; continue"), and none for the
 * start and the end, whose words are then the API's own ("Arrive at your destination", "Arrive
 * back where you started"). */
export function stopLabel(stop: Stop, index: number, count: number): string | null {
  if (stop.label !== undefined) return stop.label;
  return roleOf(index, count) === "via" ? `${ROLE_NAMES.via} ${index + 1}` : null;
}

/** The place stop `index` is visited in, "visited 3rd", when the route visits the stops in
 * another order than the list's (`order`: the list's index of each stop visited) and the stop
 * has not moved since; else null. */
export function visitLine(stop: Stop, index: number, order: number[] | null): string | null {
  const place = order?.indexOf(index) ?? -1;
  return stop.snapped === null || place < 0 ? null : `visited ${formatOrdinal(place + 1)}`;
}

function snapLine(stop: Stop, units: Units): string | null {
  if (stop.snapped === null) return null;
  const { node, moved_m, note } = stop.snapped;
  const parts = [`node ${node}`, `moved ${formatDistance(moved_m, units)}`];
  if (note) parts.push(note);
  return parts.join(" · ");
}

/** The stop list in `root`, the build the stops run on and the place search (`search`) under its
 * heading; `edit` applies a change to the stops. */
export function mountStopList(
  root: HTMLElement,
  store: Store,
  edit: (stops: Stop[]) => void,
  search: HTMLElement,
): void {
  const hint =
    "Click the map for a start, then an end. More clicks add stops along the way. " +
    "Drag a marker to move it, or search for a place.";
  const list = el("ol", "stop-list");
  const ends = el("p", "hint", "The alphabet route takes a start and an end, no stops between.");
  const build = el("p", "region-pick");
  const why = el("span", "hint");
  root.replaceChildren(el("h2", "", "Stops"), build, search, el("p", "hint", hint), ends, list);

  store.watch(["region", "pick", "settings"], (state) => {
    const { text, reason } = pickLine(state);
    why.textContent = reason;
    build.replaceChildren(text, ...(reason ? [el("br"), why] : []));
    build.hidden = text === "";
  });

  let shown: unknown[] = [];
  store.watch(["stops", "error", "run", "settings"], (state) => {
    const locked = busy(store);
    const { units, objective } = state.settings;
    const { order } = state.run;
    const key = [state.stops, state.error, locked, units, objective, order];
    if (key.every((value, k) => value === shown[k])) return;
    shown = key;
    ends.hidden = objective !== "alphabet";
    const count = state.stops.length;
    list.replaceChildren(
      ...state.stops.map((stop, k) => {
        const role = roleOf(k, count);
        const errored = stopErrored(state.error, k, count);
        const item = el("li", `stop role-${role}${errored ? " has-error" : ""}`);
        const remove = el("button", "remove", "×");
        remove.title = "Remove this stop";
        remove.disabled = locked;
        remove.addEventListener("click", () => edit(withStopRemoved(store.state.stops, k)));
        const title = el("div", "title");
        title.append(el("span", "", ROLE_NAMES[role]), remove);
        const body = el("div", "body");
        body.append(title);
        if (stop.label !== undefined) body.append(el("div", "city", stop.label));
        body.append(el("div", "coords", formatLatLon(stop.lat, stop.lon)));
        const snapped = snapLine(stop, state.settings.units);
        if (snapped) body.append(el("div", "snap", snapped));
        const visited = visitLine(stop, k, order);
        if (visited) body.append(el("div", "visited", visited));
        item.append(el("span", "badge", String(k + 1)), body);
        return item;
      }),
    );
  });
}
