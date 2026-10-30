// The stops (docs/reference/frontend.md, **Stops**): how a click, a drag or a removal changes
// them, and the list in the panel.
//
// The first click is the start, the second the end, and later ones are stops along the way,
// inserted before the end. A stop keeps its coordinates at 6 decimals (util/geo.ts, round6). An
// edit forgets where the last job snapped the stops; once a job has run, the list shows each
// stop's node, how far it moved and the API's note. The stop an error names (`stop_index`) is
// highlighted, here and on the map.
import { formatDistance, formatLatLon, type Units } from "../format";
import type { Stop, Store } from "../state";
import { el } from "../util/dom";

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

/** The index of the stop an error names, if any. */
export function erroredStop(error: { [field: string]: unknown } | null): number | null {
  const index = error?.stop_index;
  return typeof index === "number" ? index : null;
}

/** Whether a job is starting or running (stops and settings are then locked). */
export function busy(store: Store): boolean {
  const { phase } = store.state.run;
  return phase === "starting" || phase === "running";
}

const ROLE_NAMES: Record<Role, string> = { start: "Start", via: "Stop", end: "End" };

function snapLine(stop: Stop, units: Units): string | null {
  if (stop.snapped === null) return null;
  const { node, moved_m, note } = stop.snapped;
  const parts = [`node ${node}`, `moved ${formatDistance(moved_m, units)}`];
  if (note) parts.push(note);
  return parts.join(" · ");
}

/** The stop list in `root`; `edit` applies a change to the stops. */
export function mountStopList(
  root: HTMLElement,
  store: Store,
  edit: (stops: Stop[]) => void,
): void {
  const hint =
    "Click the map: first the start, then the end; more clicks add stops along the way. " +
    "Drag a marker to move it.";
  const list = el("ol", "stop-list");
  root.replaceChildren(el("h2", "", "Stops"), el("p", "hint", hint), list);

  let shown: unknown[] = [];
  store.watch(["stops", "error", "run", "settings"], (state) => {
    const locked = busy(store);
    const key = [state.stops, state.error, locked, state.settings.units];
    if (key.every((value, k) => value === shown[k])) return;
    shown = key;
    const errored = erroredStop(state.error);
    list.replaceChildren(
      ...state.stops.map((stop, k) => {
        const role = roleOf(k, state.stops.length);
        const item = el("li", `stop role-${role}${k === errored ? " has-error" : ""}`);
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
