// Links (docs/reference/frontend.md, **Links**).
//
// The request's settings and stops live in the URL
// (`?seed=&budget=&tier=&leash=&target=&mode=&turns=&strict=&vehicle=&stops=`, the target as
// `max`, `ratio:<x>` or `duration:<seconds>`, the mode as `distance`, `time` or `collect:<item>`
// and the turns as `left` or `right`, with `strict=1` (both written only when not the default),
// the vehicle as `<mpg>,<gas price>` (only when not the default), the stops as
// `lat,lon;lat,lon;...` at 6 decimals, as they were clicked), so a link reproduces a request;
// `?route=<id>` opens a stored route without running anything, its own request then giving the
// stops and settings. `UrlSync` keeps the URL in step with what the app shows once it is
// started, which is after a `?route=` link has been opened, or has failed to open (the parameter
// then stays). The share section copies a link to the route shown, and opens its share image
// (`GET /routes/{id}/share.png`) in a new tab.
import { API_BASE, errorBody, getRoute, getRouteGeojson } from "../api/client";
import type { CollectItem, LineString, Target, Tier, Vehicle } from "../api/types";
import type { JobRunner } from "../run/controls";
import {
  BUDGET_MAX_S,
  DEFAULT_SETTINGS,
  GAS_PRICE_MAX,
  GAS_PRICE_MIN,
  ITEMS,
  LEASH_MAX_KM,
  MODES,
  MPG_MAX,
  MPG_MIN,
  TARGET_MAX_DURATION_S,
  TIERS,
  targetOf,
  vehicleOf,
  type AppState,
  type Mode,
  type Settings,
  type Store,
  type TurnSetting,
} from "../state";
import { el } from "../util/dom";
import { round6 } from "../util/geo";

/** The objective a URL names, with the item under collect. */
export type UrlMode =
  { objective: Exclude<Mode, "collect"> } | { objective: "collect"; item: CollectItem };

/** What a URL can carry; null where it says nothing (or nothing valid). `strict` only with a
 * side to avoid; no ratio target under collect; the vehicle only within the panel's limits. */
export interface UrlState {
  route: string | null;
  seed: number | null;
  budgetS: number | null;
  tier: Tier | null;
  leashKm: number | "auto" | null;
  target: Target | null;
  mode: UrlMode | null;
  turns: Exclude<TurnSetting, "any"> | null;
  strict: true | null;
  vehicle: Vehicle | null;
  stops: { lat: number; lon: number }[] | null;
}

const SIDES = { left: "no_left", right: "no_right" } as const;

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

/** `vehicle` when the panel takes it, else null. */
function withinLimits(vehicle: Vehicle | null | undefined): Vehicle | null {
  if (!vehicle) return null;
  const { mpg, gas_price_usd: price } = vehicle;
  const ok = mpg >= MPG_MIN && mpg <= MPG_MAX && price >= GAS_PRICE_MIN && price <= GAS_PRICE_MAX;
  return ok ? vehicle : null;
}

function parseVehicle(text: string | null): Vehicle | null {
  const [mpg, price, ...rest] = (text ?? "").split(",").map(Number); // "" is 0, out of range
  return rest.length === 0 ? withinLimits({ mpg, gas_price_usd: price }) : null;
}

function parseMode(text: string | null): UrlMode | null {
  if (text === "distance" || text === "time") return { objective: text };
  const [kind, item, ...rest] = (text ?? "").split(":");
  const collected = ITEMS.find((known) => known === item);
  return kind === "collect" && collected && rest.length === 0
    ? { objective: kind, item: collected }
    : null;
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
  const side = params.get("turns");
  const turns = side === "left" || side === "right" ? SIDES[side] : null;
  const mode = parseMode(params.get("mode"));
  const target = parseTarget(params.get("target"));
  return {
    route: route !== null && /^[0-9a-f]+$/.test(route) ? route : null,
    seed: Number.isSafeInteger(seed) && seed >= 0 ? seed : null,
    budgetS: inRange(budget, 0, BUDGET_MAX_S) ? budget : null,
    tier: TIERS.find((tier) => tier === params.get("tier")) ?? null,
    leashKm:
      params.get("leash") === "auto" ? "auto" : inRange(leash, 0, LEASH_MAX_KM) ? leash : null,
    // Collect takes no ratio: one in a link reads as as bad as possible.
    target: mode?.objective === "collect" && target?.kind === "ratio" ? { kind: "max" } : target,
    mode,
    turns,
    strict: turns !== null && params.get("strict") === "1" ? true : null,
    vehicle: parseVehicle(params.get("vehicle")),
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
  if (state.mode !== null) {
    const { mode } = state;
    params.push(`mode=${mode.objective}${mode.objective === "collect" ? `:${mode.item}` : ""}`);
  }
  if (state.turns !== null) params.push(`turns=${state.turns === "no_left" ? "left" : "right"}`);
  if (state.strict !== null) params.push("strict=1");
  if (state.vehicle !== null) {
    params.push(`vehicle=${state.vehicle.mpg},${state.vehicle.gas_price_usd}`);
  }
  if (state.stops !== null && state.stops.length > 0) {
    params.push(`stops=${state.stops.map(({ lat, lon }) => `${lat},${lon}`).join(";")}`);
  }
  return params.length ? `?${params.join("&")}` : "";
}

/** What the app shows, as a URL carries it: the mode, the turns and the vehicle only when not
 * the default. */
export function urlStateOf(state: Readonly<AppState>): UrlState {
  const { settings } = state;
  const { objective, item, turns, mpg, gasPriceUsd } = settings;
  const usual = mpg === DEFAULT_SETTINGS.mpg && gasPriceUsd === DEFAULT_SETTINGS.gasPriceUsd;
  return {
    route: state.route?.route_id ?? null,
    seed: settings.seed,
    budgetS: settings.budgetS,
    tier: settings.tier,
    leashKm: settings.leashKm,
    target: targetOf(settings),
    mode:
      objective === "distance"
        ? null
        : objective === "collect"
          ? { objective, item }
          : { objective },
    turns: turns === "any" ? null : turns,
    strict: turns !== "any" && settings.strict ? true : null,
    vehicle: usual ? null : vehicleOf(settings),
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
    objective: url.mode?.objective ?? defaults.objective,
    item: url.mode?.objective === "collect" ? url.mode.item : defaults.item,
    turns: url.turns ?? defaults.turns,
    strict: url.strict ?? defaults.strict,
    mpg: url.vehicle?.mpg ?? defaults.mpg,
    gasPriceUsd: url.vehicle?.gas_price_usd ?? defaults.gasPriceUsd,
  };
}

/** Open stored route `routeId`: its stops and settings (its mode, turns and vehicle among them;
 * the panel's vehicle stays when it has none, or one outside the panel's limits), the route and
 * its baseline. Whether it opened; when not, the banner says why. */
export async function openRoute(
  routeId: string,
  store: Store,
  runner: Pick<JobRunner, "showRoute">,
): Promise<boolean> {
  try {
    const [route, geojson] = await Promise.all([getRoute(routeId), getRouteGeojson(routeId)]);
    const { request } = route;
    const { objective, item, constraints } = request;
    const vehicle = withinLimits(request.vehicle);
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
      objective: MODES.find((mode) => mode === objective) ?? "distance",
      item: item ?? store.state.settings.item,
      turns: constraints?.no_left ? "no_left" : constraints?.no_right ? "no_right" : "any",
      strict: constraints?.strict ?? false,
      ...(vehicle ? { mpg: vehicle.mpg, gasPriceUsd: vehicle.gas_price_usd } : {}),
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
