// Which build a trip runs on (docs/reference/frontend.md, **Stops**). A server may serve several
// builds (a state's all roads beside the national one); the server picks the one a trip runs on
// from its stops (`POST /regions/pick`: the smallest served region holding them all), and Go, the
// brand picker and the request follow it (`AppState.region`). `RegionPicker` asks again whenever
// the stops' coordinates change (after `PICK_DELAY_MS` without another change; never for a job's
// `snapped`, which moves no stop), drops an answer the stops have moved on from, and puts a
// refusal (STOP_OUTSIDE_REGION, naming the stop) in the banner; any other failure (the server
// unreachable, busy or failing) it asks again `PICK_RETRY_MS` later, up to `PICK_RETRIES` times,
// then says in the banner that the server could not be asked, until the stops change again.
// Until it answers there is no region, so Go waits. A region pinned in the settings skips the pick, and one the server does
// not serve is unpinned once /regions has answered. `pickLine` is the stop list's header: the
// region by its name, with its roads (from its tiers), and why ("Oregon · all roads", "all
// stops in Oregon").
import { ApiError, errorBody, pickRegion } from "../api/client";
import type { Region } from "../api/types";
import type { AppState, Store } from "../state";

export const PICK_DELAY_MS = 300;
export const PICK_RETRY_MS = 1000;
export const PICK_RETRIES = 3;

// A build's roads by the first tier holding all of its edges (T0..T4).
const ROADS = [
  "motorways and trunks",
  "primary roads and up",
  "secondary roads and up",
  "tertiary roads and up",
  "all roads",
];

/** The region as the app names it: the server's name, else its id. */
export function regionName(region: Region): string {
  return region.name ?? region.region;
}

/** A region's roads, by the first of its tiers that holds all of its edges: "all roads" for a
 * state's build, "primary roads and up" for the national T1 build; "" without tiers. */
export function roadsOf(region: Region): string {
  const all = region.tiers[region.tiers.length - 1]?.edges;
  const first = region.tiers.findIndex(({ edges }) => edges === all);
  return ROADS[first] ?? "";
}

/** A region by its name and roads: "Oregon · all roads". */
export function regionLabel(region: Region): string {
  const roads = roadsOf(region);
  return roads ? `${regionName(region)} · ${roads}` : regionName(region);
}

/** The stop list's header: the region trips run on and its roads, and why; empty while there
 * are no stops, or the pick failed (the banner says why). */
export function pickLine({ region, pick, settings }: Readonly<AppState>): {
  text: string;
  reason: string;
} {
  if (pick === "picking") return { text: "Finding the build…", reason: "" };
  if (region === null) return { text: "", reason: "" };
  const text = regionLabel(region);
  if (settings.region !== null) return { text, reason: "set in the settings" };
  return { text, reason: pick?.reason ?? "" };
}

/** Asks the server which build the stops run on whenever they change, into the store. */
export class RegionPicker {
  readonly #store: Store;
  readonly #delayMs: number;
  #key: string | null = null; // what the region was last worked out for
  #asked = 0; // counts the changes, so an answer to an earlier one is dropped
  #timer: ReturnType<typeof setTimeout> | undefined;

  constructor(store: Store, delayMs = PICK_DELAY_MS) {
    this.#store = store;
    this.#delayMs = delayMs;
    store.watch(["regions", "stops", "settings"], () => this.#changed());
  }

  #changed(): void {
    const store = this.#store;
    const { regions, stops, settings } = store.state;
    if (regions === null) return;
    if (settings.region !== null && !regions.some(({ region }) => region === settings.region)) {
      store.updateSettings({ region: null }); // a pin the server does not serve
      return;
    }
    const at = stops.map(({ lat, lon }) => `${lat},${lon}`).join(";");
    const key = settings.region === null ? at : `pinned:${settings.region}`;
    if (key === this.#key) return;
    this.#key = key;
    clearTimeout(this.#timer);
    const asked = ++this.#asked;
    if (settings.region !== null) {
      const region = regions.find((r) => r.region === settings.region) ?? null;
      store.update({ region, pick: null });
      return;
    }
    store.update({ region: null, pick: stops.length > 0 ? "picking" : null });
    if (stops.length === 0) return;
    const points = stops.map(({ lat, lon }) => ({ lat, lon }));
    this.#timer = setTimeout(() => void this.#ask(asked, points), this.#delayMs);
  }

  async #ask(asked: number, stops: { lat: number; lon: number }[], tries = 0): Promise<void> {
    const store = this.#store;
    try {
      const pick = await pickRegion(stops);
      if (asked !== this.#asked) return;
      const region = store.state.regions?.find((r) => r.region === pick.region) ?? null;
      store.update({ region, pick });
    } catch (error) {
      if (asked !== this.#asked) return;
      const refused = error instanceof ApiError && error.status === 422;
      if (!refused && tries < PICK_RETRIES) {
        const again = () => void this.#ask(asked, stops, tries + 1);
        this.#timer = setTimeout(again, PICK_RETRY_MS);
        return;
      }
      const body = errorBody(error);
      const message = `could not ask the server which build these stops run on: ${body.message}`;
      store.update({ pick: null, error: refused ? body : { ...body, message } });
    }
  }
}
