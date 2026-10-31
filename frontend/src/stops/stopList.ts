// The stops (docs/reference/frontend.md, **Stops**): how a click, a drag or a removal changes
// them, and the list in the panel.
//
// The first click is the start, the second the end, and later ones are stops along the way,
// inserted before the end (`placeStop`, which a place search's pick goes through too). A stop
// keeps its coordinates at 6 decimals (util/geo.ts, round6). An edit forgets where the last job
// snapped the stops; once a job has run, the list shows each stop's node, how far it moved and
// the API's note. The stop an error names (`stop_index`) is highlighted, here and on the map, and
// every stop along the way for an error on `field: "stops"` (the alphabet route takes a start and
// an end only, which the list's hint says under it). The place search (search.ts) sits under the
// list's heading.
import { formatDistance, formatLatLon, type Units } from "../format";
import type { Stop, Store } from "../state";
import { el } from "../util/dom";
import { round6 } from "../util/geo";

export type Role = "start" | "via" | "end";

/** What a stop is, by its position among `count`. */
export function roleOf(index: number, count: number): Role {
  if (index === 0) return "start";
  return index === count - 1 ? "end" : "via";
}

const forget = (stops: Stop[]): Stop[] => stops.map((stop) => ({ ...stop, snapped: null }));

/** The stops after a click at (lat, lon). */
export function withStopAdded(stops: Stop[], lat: number, lon: number): Stop[] {
  const stop: Stop = { lat, lon, snapped: null };
  const kept = forget(stops);
  if (kept.length < 2) return [...kept, stop];
  return [...kept.slice(0, -1), stop, kept[kept.length - 1]];
}

/** The stops after stop `index` was dragged to (lat, lon). */
export function withStopMoved(stops: Stop[], index: number, lat: number, lon: number): Stop[] {
  return forget(stops).map((stop, k) => (k === index ? { ...stop, lat, lon } : stop));
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

/** Whether a job is starting or running (stops and settings are then locked). */
export function busy(store: Store): boolean {
  const { phase } = store.state.run;
  return phase === "starting" || phase === "running";
}

/** Place the next stop at (lat, lon), as a click on the map does, through `edit`; nothing while
 * a job runs. Whether it was placed. */
export function placeStop(
  store: Store,
  edit: (stops: Stop[]) => void,
  lat: number,
  lon: number,
): boolean {
  if (busy(store)) return false;
  edit(withStopAdded(store.state.stops, round6(lat), round6(lon)));
  return true;
}

const ROLE_NAMES: Record<Role, string> = { start: "Start", via: "Stop", end: "End" };

/** The request's label for stop `index`: a stop along the way named as its marker numbers it,
 * "Stop 2", which the directions say ("Arrive at Stop 2; continue"); none for the start and the
 * end, whose words are the API's own ("Arrive at your destination", "Arrive back where you
 * started"). */
export function stopLabel(index: number, count: number): string | null {
  return roleOf(index, count) === "via" ? `${ROLE_NAMES.via} ${index + 1}` : null;
}

function snapLine(stop: Stop, units: Units): string | null {
  if (stop.snapped === null) return null;
  const { node, moved_m, note } = stop.snapped;
  const parts = [`node ${node}`, `moved ${formatDistance(moved_m, units)}`];
  if (note) parts.push(note);
  return parts.join(" · ");
}

/** The stop list in `root`, the place search (`search`) under its heading; `edit` applies a
 * change to the stops. */
export function mountStopList(
  root: HTMLElement,
  store: Store,
  edit: (stops: Stop[]) => void,
  search: HTMLElement,
): void {
  const hint =
    "Click the map: first the start, then the end; more clicks add stops along the way. " +
    "Drag a marker to move it, or search for a place.";
  const list = el("ol", "stop-list");
  const ends = el(
    "p",
    "hint",
    "start and end only: the alphabet route takes no stops along the way",
  );
  root.replaceChildren(el("h2", "", "Stops"), search, el("p", "hint", hint), ends, list);

  let shown: unknown[] = [];
  store.watch(["stops", "error", "run", "settings"], (state) => {
    const locked = busy(store);
    const { units, objective } = state.settings;
    const key = [state.stops, state.error, locked, units, objective];
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
        body.append(title, el("div", "coords", formatLatLon(stop.lat, stop.lon)));
        const snapped = snapLine(stop, state.settings.units);
        if (snapped) body.append(el("div", "snap", snapped));
        item.append(el("span", "badge", String(k + 1)), body);
        return item;
      }),
    );
  });
}
