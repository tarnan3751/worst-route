// The stops on the map (docs/reference/frontend.md, **Stops**): a numbered marker per stop,
// coloured by its role and draggable while no job runs, and, once a job has snapped them, a
// ghost dot where each stop was snapped, its tooltip the API's note (or how far it moved). A
// click on either stays with it: it would otherwise reach the map and add a stop there.
import { Marker, type Map as MapLibreMap } from "maplibre-gl";
import { formatDistance } from "../format";
import type { Stop, Store } from "../state";
import { busy, roleOf, stopErrored, withStopMoved } from "../stops/stopList";
import { el } from "../util/dom";
import { round6 } from "../util/geo";

/** A marker's element, whose clicks do not reach the map. */
function markerElement(className: string, text?: string): HTMLElement {
  const element = el("div", className, text);
  element.addEventListener("click", (event) => event.stopPropagation());
  return element;
}

export class StopsLayer {
  #markers: Marker[] = [];
  #shown: unknown[] = [];

  constructor(map: MapLibreMap, store: Store, edit: (stops: Stop[]) => void) {
    store.watch(["stops", "error", "run", "settings"], (state) => {
      const locked = busy(store);
      const key = [state.stops, state.error, locked, state.settings.units];
      if (key.every((value, k) => value === this.#shown[k])) return;
      this.#shown = key;
      for (const marker of this.#markers) marker.remove();
      this.#markers = [];
      const count = state.stops.length;
      for (const { snapped } of state.stops) {
        if (snapped === null) continue;
        const ghost = markerElement("stop-ghost");
        const moved = `snapped ${formatDistance(snapped.moved_m, state.settings.units)} away`;
        ghost.title = snapped.note ?? moved;
        const at: [number, number] = [snapped.lon, snapped.lat];
        this.#markers.push(new Marker({ element: ghost }).setLngLat(at).addTo(map));
      }
      state.stops.forEach((stop, k) => {
        const role = roleOf(k, count);
        const element = markerElement(`stop-marker role-${role}`, String(k + 1));
        if (stopErrored(state.error, k, count)) element.classList.add("has-error");
        const marker = new Marker({ element, draggable: !locked })
          .setLngLat([stop.lon, stop.lat])
          .addTo(map);
        marker.on("dragend", () => {
          const { lng, lat } = marker.getLngLat();
          edit(withStopMoved(store.state.stops, k, round6(lat), round6(lng)));
        });
        this.#markers.push(marker);
      });
    });
  }
}
