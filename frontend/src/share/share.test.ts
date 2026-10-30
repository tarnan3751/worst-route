import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeView, json, stubFetch } from "../__fixtures__/http";
import { loadStream } from "../__fixtures__/stream";
import { JobRunner } from "../run/controls";
import { DEFAULT_SETTINGS, IDLE_RUN, Store, type AppState } from "../state";
import { round6 } from "../util/geo";
import {
  UrlSync,
  decodeUrlState,
  encodeUrlState,
  openRoute,
  settingsFrom,
  shareImageUrl,
  urlStateOf,
  type UrlState,
} from "./share";

const full: UrlState = {
  route: "5ea844bffd40",
  seed: 12345,
  budgetS: 15,
  tier: "T2",
  leashKm: 6.5,
  target: { kind: "duration", seconds: 32400 },
  stops: [
    { lat: 45.5231, lon: -122.6765 },
    { lat: 45.519368, lon: -122.662765 },
    { lat: 45.4817, lon: -122.6263 },
  ],
};

const nothing: UrlState = {
  route: null,
  seed: null,
  budgetS: null,
  tier: null,
  leashKm: null,
  target: null,
  stops: null,
};

describe("the URL state", () => {
  it("round-trips every field", () => {
    const query = encodeUrlState(full);
    expect(query).toBe(
      "?route=5ea844bffd40&seed=12345&budget=15&tier=T2&leash=6.5&target=duration:32400" +
        "&stops=45.5231,-122.6765;45.519368,-122.662765;45.4817,-122.6263",
    );
    expect(decodeUrlState(query)).toEqual(full);
  });

  it("round-trips auto, a seed of 0, the largest safe seed, and nothing", () => {
    const some = { ...nothing, seed: 0, leashKm: "auto" as const, tier: "auto" as const };
    expect(decodeUrlState(encodeUrlState(some))).toEqual(some);
    for (const target of [{ kind: "max" }, { kind: "ratio", value: 12.5 }] as const) {
      const state = { ...nothing, target };
      expect(decodeUrlState(encodeUrlState(state))).toEqual(state);
    }
    const big = { ...nothing, seed: Number.MAX_SAFE_INTEGER };
    expect(decodeUrlState(encodeUrlState(big))).toEqual(big);
    expect(encodeUrlState(nothing)).toBe("");
    expect(decodeUrlState("")).toEqual(nothing);
  });

  it("keeps clicked coordinates exactly", () => {
    // Coordinates as a click gives them, rounded as the stop picker rounds them.
    const clicks = [
      [45.52347512345678, -122.67665398765432],
      [45.250000499, -123.0499995],
      [44.0000004, -121.9999996],
      [45.7, -122.35],
    ].map(([lat, lon]) => ({ lat: round6(lat), lon: round6(lon) }));
    const state = { ...nothing, stops: clicks };
    expect(decodeUrlState(encodeUrlState(state)).stops).toEqual(clicks);
  });

  it("ignores what it cannot use", () => {
    const query = "?route=../x&seed=-1&budget=500&tier=T9&leash=0&stops=45.5,-122.6;nonsense";
    expect(decodeUrlState(query)).toEqual(nothing);
    expect(decodeUrlState("?seed=1.5&budget=abc&leash=151&stops=91,0").seed).toBeNull();
    expect(decodeUrlState("?seed=&budget=").budgetS).toBeNull();
    expect(decodeUrlState("?seed=9007199254740993").seed).toBeNull();
    expect(decodeUrlState("?stops=45.5,-122.6,3").stops).toBeNull();
    for (const target of [
      "ratio:1",
      "ratio:",
      "duration:0",
      "duration:604801",
      "speed:3",
      "max:1",
    ]) {
      expect(decodeUrlState(`?target=${target}`).target).toBeNull();
    }
  });

  it("is what the app shows, and gives the settings back", () => {
    const state: AppState = {
      region: null,
      stops: full.stops!.map((stop) => ({ ...stop, snapped: null })),
      settings: {
        ...DEFAULT_SETTINGS,
        seed: 12345,
        budgetS: 15,
        tier: "T2",
        leashKm: 6.5,
        target: "duration",
        durationS: 32400,
      },
      run: IDLE_RUN,
      route: null,
      error: null,
    };
    const url = decodeUrlState(encodeUrlState(urlStateOf(state)));
    expect(url).toEqual({ ...full, route: null });
    expect(settingsFrom(url, { ...DEFAULT_SETTINGS, units: "km" })).toEqual({
      ...state.settings,
      units: "km",
    });
    expect(settingsFrom(nothing, DEFAULT_SETTINGS)).toEqual(DEFAULT_SETTINGS);
  });
});

describe("opening a ?route= link at startup", () => {
  const route = loadStream().route;
  const query = `?route=${route.route_id}&seed=9&budget=5`;

  /** The app as main.ts starts it on `query`: the store, and the URL's writes. */
  function start() {
    const url = decodeUrlState(query);
    const store = new Store({
      region: null,
      stops: [],
      settings: settingsFrom(url, DEFAULT_SETTINGS),
      run: IDLE_RUN,
      route: null,
      error: null,
    });
    const writes: string[] = [];
    const sync = new UrlSync(store, (q) => writes.push(q));
    return { url, store, writes, sync, runner: new JobRunner(store, fakeView(), () => {}) };
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps the URL until the route has opened, then writes the route's request", async () => {
    const baseline = { type: "LineString", coordinates: route.geometry.coordinates.slice(0, 2) };
    stubFetch((call) =>
      call.url.endsWith("/geojson")
        ? json({
            type: "FeatureCollection",
            features: [{ type: "Feature", geometry: baseline, properties: { kind: "baseline" } }],
          })
        : json(route),
    );
    const { url, store, writes, sync, runner } = start();
    const opened = await openRoute(url.route!, store, runner);
    expect(opened).toBe(true);
    expect(writes).toEqual([]);
    sync.start(opened);
    expect(writes).toEqual([
      `?route=${route.route_id}&seed=1&budget=6&tier=auto&leash=auto&target=max` +
        "&stops=45.5231,-122.6765;45.5195,-122.6625;45.5317,-122.654",
    ]);
    expect(decodeUrlState(writes[0]).route).toBe(route.route_id);
  });

  it("keeps the parameter of a link that does not open, and says why", async () => {
    stubFetch(() => json({ code: "NOT_FOUND", message: `no route ${route.route_id}` }, 404));
    const { url, store, writes, sync, runner } = start();
    const opened = await openRoute(url.route!, store, runner);
    expect(opened).toBe(false);
    sync.start(opened);
    expect(writes).toEqual([]);
    expect(store.state.error?.code).toBe("NOT_FOUND");
    store.updateSettings({ budgetS: 7 }); // the user moves on: the URL follows
    expect(writes).toEqual(["?seed=9&budget=7&tier=auto&leash=auto&target=max"]);
  });
});

describe("a ratio-target route opened by its link", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("takes its target into the settings and the URL", async () => {
    const route = loadStream("portland-ratio-stream").route;
    stubFetch((call) =>
      call.url.endsWith("/geojson")
        ? json({ type: "FeatureCollection", features: [] })
        : json(route),
    );
    const store = new Store({
      region: null,
      stops: [],
      settings: DEFAULT_SETTINGS,
      run: IDLE_RUN,
      route: null,
      error: null,
    });
    expect(await openRoute(route.route_id, store, new JobRunner(store, fakeView(), () => {}))).toBe(
      true,
    );
    expect(store.state.settings).toMatchObject({ target: "ratio", ratio: 25 });
    expect(store.state.run.outcome).toMatchObject({ kind: "ratio", within_tolerance: true });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&target=ratio:25&");
  });
});

describe("the share image", () => {
  it("is the API's share.png of the route, through the /api proxy", () => {
    expect(shareImageUrl("5ea844bffd40")).toBe("/api/routes/5ea844bffd40/share.png");
  });
});
