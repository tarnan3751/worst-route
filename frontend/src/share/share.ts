// Links (docs/reference/frontend.md, **Links**).
//
// The request's settings and stops live in the URL
// (`?seed=&budget=&tier=&leash=&target=&stops=`, the target as `max`, `ratio:<x>` or
// `duration:<seconds>`, the stops as `lat,lon;lat,lon;...` at 6 decimals, as they were
// clicked), so a link reproduces a request; `?route=<id>` opens a stored route without running
// anything, its own request then giving the stops and settings. `UrlSync` keeps the URL in step
// with what the app shows once it is started, which is after a `?route=` link has been opened,
// or has failed to open (the parameter then stays). The share section copies a link to the
// route shown, and opens its share image (`GET /routes/{id}/share.png`) in a new tab.
import { API_BASE, errorBody, getRoute, getRouteGeojson } from "../api/client";
import type { LineString, Target, Tier } from "../api/types";
import type { JobRunner } from "../run/controls";
import {
  BUDGET_MAX_S,
  LEASH_MAX_KM,
  TARGET_MAX_DURATION_S,
  TIERS,
  targetOf,
  type AppState,
  type Settings,
  type Store,
} from "../state";
import { el } from "../util/dom";
import { round6 } from "../util/geo";

/** What a URL can carry; null where it says nothing (or nothing valid). */
export interface UrlState {
  route: string | null;
  seed: number | null;
  budgetS: number | null;
  tier: Tier | null;
  leashKm: number | "auto" | null;
  target: Target | null;
  stops: { lat: number; lon: number }[] | null;
}

const inRange = (value: number, min: number, max: number) =>
  Number.isFinite(value) && value > min && value <= max;

function parseStops(text: string): { lat: number; lon: number }[] | null {
  const stops = text.split(";").map((pair) => {
    const [lat, lon, ...rest] = pair.split(",").map((part) => (part.trim() ? Number(part) : NaN));
    return rest.length === 0 && Math.abs(lat) <= 90 && Math.abs(lon) <= 180
      ? { lat: round6(lat), lon: round6(lon) }
      : null;
  });
  return stops.every((stop) => stop !== null) ? stops : null;
}

function parseTarget(text: string | null): Target | null {
  if (text === "max") return { kind: "max" };
  const [kind, number, ...rest] = (text ?? "").split(":");
  const value = number?.trim() ? Number(number) : NaN;
  if (rest.length > 0 || !Number.isFinite(value)) return null;
  if (kind === "ratio" && value > 1) return { kind: "ratio", value };
  if (kind === "duration" && inRange(value, 0, TARGET_MAX_DURATION_S)) {
    return { kind: "duration", seconds: value };
  }
  return null;
}

/** The state a query string (`location.search`) carries. */
export function decodeUrlState(search: string): UrlState {
  const params = new URLSearchParams(search);
  const number = (name: string) => {
    const text = params.get(name)?.trim();
    return text ? Number(text) : NaN;
  };
  const route = params.get("route");
  const seed = number("seed");
  const budget = number("budget");
  const leash = number("leash");
  const stops = params.get("stops");
  return {
    route: route !== null && /^[0-9a-f]+$/.test(route) ? route : null,
    seed: Number.isSafeInteger(seed) && seed >= 0 ? seed : null,
    budgetS: inRange(budget, 0, BUDGET_MAX_S) ? budget : null,
    tier: TIERS.find((tier) => tier === params.get("tier")) ?? null,
    leashKm:
      params.get("leash") === "auto" ? "auto" : inRange(leash, 0, LEASH_MAX_KM) ? leash : null,
    target: parseTarget(params.get("target")),
    stops: stops ? parseStops(stops) : null,
  };
}

/** The query string for `state` ("" when it says nothing). */
export function encodeUrlState(state: UrlState): string {
  const params: string[] = [];
  if (state.route !== null) params.push(`route=${state.route}`);
  if (state.seed !== null) params.push(`seed=${state.seed}`);
  if (state.budgetS !== null) params.push(`budget=${state.budgetS}`);
  if (state.tier !== null) params.push(`tier=${state.tier}`);
  if (state.leashKm !== null) params.push(`leash=${state.leashKm}`);
  if (state.target !== null) {
    const { kind, value, seconds } = state.target;
    params.push(`target=${kind}${kind === "max" ? "" : `:${kind === "ratio" ? value : seconds}`}`);
  }
  if (state.stops !== null && state.stops.length > 0) {
    params.push(`stops=${state.stops.map(({ lat, lon }) => `${lat},${lon}`).join(";")}`);
  }
  return params.length ? `?${params.join("&")}` : "";
}

/** What the app shows, as a URL carries it. */
export function urlStateOf(state: Readonly<AppState>): UrlState {
  const { settings } = state;
  return {
    route: state.route?.route_id ?? null,
    seed: settings.seed,
    budgetS: settings.budgetS,
    tier: settings.tier,
    leashKm: settings.leashKm,
    target: targetOf(settings),
    stops: state.stops.map(({ lat, lon }) => ({ lat, lon })),
  };
}

/** The settings that give `target`. */
function targetSettings(target: Target): Partial<Settings> {
  if (target.kind === "ratio" && target.value != null) {
    return { target: "ratio", ratio: target.value };
  }
  if (target.kind === "duration" && target.seconds != null) {
    return { target: "duration", durationS: target.seconds };
  }
  return { target: "max" };
}

/** The settings a URL gives, over `defaults`. */
export function settingsFrom(url: UrlState, defaults: Settings): Settings {
  return {
    ...defaults,
    seed: url.seed ?? defaults.seed,
    budgetS: url.budgetS ?? defaults.budgetS,
    tier: url.tier ?? defaults.tier,
    leashKm: url.leashKm ?? defaults.leashKm,
    ...(url.target ? targetSettings(url.target) : {}),
  };
}

/** Open stored route `routeId`: its stops and settings, the route and its baseline. Whether it
 * opened; when not, the banner says why. */
export async function openRoute(
  routeId: string,
  store: Store,
  runner: Pick<JobRunner, "showRoute">,
): Promise<boolean> {
  try {
    const [route, geojson] = await Promise.all([getRoute(routeId), getRouteGeojson(routeId)]);
    const { request } = route;
    store.update({
      stops: (request.stops ?? []).map(({ lat, lon }, k) => {
        const { node, lat: snappedLat, lon: snappedLon, moved_m, note } = route.stops[k];
        return { lat, lon, snapped: { node, lat: snappedLat, lon: snappedLon, moved_m, note } };
      }),
    });
    store.updateSettings({
      budgetS: request.budget_s ?? store.state.settings.budgetS,
      tier: request.tier ?? "auto",
      leashKm: request.leash_km ?? "auto",
      ...targetSettings(request.target ?? { kind: "max" }),
    });
    const baseline = geojson.features.find((feature) => feature.properties.kind === "baseline");
    const geometry = baseline?.geometry?.type === "LineString" ? baseline.geometry : null;
    runner.showRoute(route, { baseline: geometry as LineString | null });
    return true;
  } catch (error) {
    store.update({ error: errorBody(error) });
    return false;
  }
}

/** The URL, kept in step with the route, stops and settings shown once started. */
export class UrlSync {
  readonly #store: Store;
  readonly #write: (query: string) => void;
  #started = false;

  constructor(store: Store, write: (query: string) => void) {
    this.#store = store;
    this.#write = write;
    store.watch(["route", "stops", "settings"], (state) => {
      if (this.#started) write(encodeUrlState(urlStateOf(state)));
    });
  }

  /** Follow the app from now on; with `now`, write the URL at once. */
  start(now: boolean): void {
    this.#started = true;
    if (now) this.#write(encodeUrlState(urlStateOf(this.#store.state)));
  }
}

/** The share image of stored route `routeId`, as the API renders it. */
export function shareImageUrl(routeId: string): string {
  return `${API_BASE}/routes/${routeId}/share.png`;
}

/** The share section: a link to the route shown, a button that copies it, and its share
 * image. */
export function mountShare(root: HTMLElement, store: Store): void {
  const link = el("input", "link");
  link.readOnly = true;
  const copy = el("button", "", "Copy link");
  const hint = el("p", "hint", "Run a route to get a link to it.");
  const image = el("a", "", "Open share image");
  image.target = "_blank";
  image.rel = "noopener";
  const imageLine = el("p");
  imageLine.append(image);
  root.replaceChildren(el("h2", "", "Share"), hint, link, copy, imageLine);
  copy.addEventListener("click", async () => {
    link.select();
    try {
      await navigator.clipboard.writeText(link.value);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Select and copy";
    }
    setTimeout(() => (copy.textContent = "Copy link"), 1500);
  });

  store.watch(["route"], (state) => {
    const id = state.route?.route_id;
    hint.hidden = id !== undefined;
    link.hidden = copy.hidden = imageLine.hidden = id === undefined;
    if (id !== undefined) {
      link.value = `${location.origin}${location.pathname}?route=${id}`;
      image.href = shareImageUrl(id);
    }
  });
}
