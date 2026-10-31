import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeView, json, stubFetch } from "../__fixtures__/http";
import { loadStream } from "../__fixtures__/stream";
import type { RouteDocument } from "../api/types";
import { JobRunner, statLines } from "../run/controls";
import { DEFAULT_SETTINGS, IDLE_RUN, ITEMS, Store, type AppState, type Settings } from "../state";
import { round6 } from "../util/geo";
import {
  UrlSync,
  decodeUrlState,
  encodeUrlState,
  openRoute,
  settingsFrom,
  geojsonUrl,
  shareImageUrl,
  urlStateOf,
  type UrlMode,
  type UrlState,
} from "./share";

const full: UrlState = {
  route: "5ea844bffd40",
  seed: 12345,
  budgetS: 15,
  tier: "T2",
  leashKm: 6.5,
  target: { kind: "duration", seconds: 32400 },
  mode: null,
  turns: null,
  strict: null,
  vehicle: null,
  departure: null,
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
  mode: null,
  turns: null,
  strict: null,
  vehicle: null,
  departure: null,
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

describe("the mode and the turns in the URL", () => {
  const modes: UrlMode[] = [
    { objective: "distance" },
    { objective: "time" },
    ...ITEMS.map((item): UrlMode => ({ objective: "collect", item })),
  ];

  it("round-trips every mode and turn setting", () => {
    for (const mode of modes) {
      for (const turns of [null, "no_left", "no_right"] as const) {
        for (const strict of turns === null ? [null] : [null, true as const]) {
          const state = { ...nothing, mode, turns, strict };
          expect(decodeUrlState(encodeUrlState(state))).toEqual(state);
        }
      }
    }
    const collecting = { objective: "collect", item: "toll_booth" } as const;
    expect(encodeUrlState({ ...nothing, mode: collecting, turns: "no_right", strict: true })).toBe(
      "?mode=collect:toll_booth&turns=right&strict=1",
    );
    expect(encodeUrlState({ ...nothing, mode: { objective: "time" }, turns: "no_left" })).toBe(
      "?mode=time&turns=left",
    );
  });

  it("ignores what it cannot use", () => {
    for (const mode of [
      "collect",
      "collect:",
      "collect:bridges",
      "collect:bridge:2",
      "time:bridge",
      "distance:",
      "brand",
      "alphabet",
      "slowest",
      "",
    ]) {
      expect(decodeUrlState(`?mode=${mode}`).mode).toBeNull();
    }
    for (const turns of ["no_left", "Left", "both", "", "1"]) {
      expect(decodeUrlState(`?turns=${turns}`).turns).toBeNull();
    }
    expect(decodeUrlState("?strict=1").strict).toBeNull(); // strict needs a side
    for (const strict of ["true", "0", "yes", ""]) {
      expect(decodeUrlState(`?turns=left&strict=${strict}`).strict).toBeNull();
    }
  });

  it("reads a ratio target beside a collect mode as as bad as possible", () => {
    const url = decodeUrlState("?target=ratio:20&mode=collect:bridge");
    expect(url).toMatchObject({ target: { kind: "max" }, mode: { objective: "collect" } });
    expect(settingsFrom(url, DEFAULT_SETTINGS)).toMatchObject({
      objective: "collect",
      target: "max",
    });
    const ratio = { kind: "ratio", value: 20 };
    expect(decodeUrlState("?target=ratio:20&mode=time").target).toEqual(ratio);
    expect(decodeUrlState("?target=ratio:20&mode=collect").target).toEqual(ratio); // no mode
    expect(decodeUrlState("?target=duration:3600&mode=collect:bridge").target).toEqual({
      kind: "duration",
      seconds: 3600,
    });
  });

  it("is what the app shows, without the defaults, and gives the settings back", () => {
    const shown = (patch: Partial<Settings>): AppState => ({
      region: null,
      stops: [],
      settings: { ...DEFAULT_SETTINGS, ...patch },
      run: IDLE_RUN,
      route: null,
      error: null,
    });
    const settings: Settings = {
      ...DEFAULT_SETTINGS,
      objective: "collect",
      item: "roundabout",
      turns: "no_right",
      strict: true,
    };
    const query = encodeUrlState(urlStateOf(shown(settings)));
    expect(query).toContain("&target=max&mode=collect:roundabout&turns=right&strict=1");
    expect(settingsFrom(decodeUrlState(query), DEFAULT_SETTINGS)).toEqual(settings);
    const slowest = { ...DEFAULT_SETTINGS, objective: "time", turns: "no_left" } as const;
    const back = settingsFrom(decodeUrlState(encodeUrlState(urlStateOf(shown(slowest)))), {
      ...DEFAULT_SETTINGS,
      item: "toll_booth", // the URL leaves the item alone under another mode
    });
    expect(back).toEqual({ ...slowest, item: "toll_booth" });
    expect(urlStateOf(shown({}))).toMatchObject({ mode: null, turns: null, strict: null });
    expect(urlStateOf(shown({ strict: true })).strict).toBeNull(); // no side, no strict
  });
});

describe("a route of another mode opened by its link", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** Open `route` by its link, from settings of another mode; the store. */
  async function open(route: RouteDocument): Promise<Store> {
    stubFetch((call) =>
      call.url.endsWith("/geojson")
        ? json({ type: "FeatureCollection", features: [] })
        : json(route),
    );
    const store = new Store({
      region: null,
      stops: [],
      settings: { ...DEFAULT_SETTINGS, objective: "collect", item: "roundabout", turns: "no_left" },
      run: IDLE_RUN,
      route: null,
      error: null,
    });
    const runner = new JobRunner(store, fakeView(), () => {});
    expect(await openRoute(route.route_id, store, runner)).toBe(true);
    return store;
  }

  it("takes a slowest-time route's mode into the settings, the URL and the stat line", async () => {
    const store = await open(loadStream("portland-time-stream").route);
    expect(store.state.settings).toMatchObject({
      objective: "time",
      item: "roundabout", // kept for the next collect run
      turns: "any",
      strict: false,
    });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&target=max&mode=time&stops=");
    const [ratio, , , , drive, items, turns] = statLines(store.state);
    expect([ratio, drive, items, turns]).toEqual([
      "16.9× slower",
      "drive 4 h 8 min + delays 1 h 59 min",
      "",
      "277 left · 282 right",
    ]);
  });

  it("takes a collecting route's item, and a route's turn constraint", async () => {
    const { route } = loadStream("portland-collect-stream");
    const store = await open(route);
    expect(store.state.settings).toMatchObject({ objective: "collect", item: "bridge" });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&mode=collect:bridge&stops=");
    expect(statLines(store.state)[5]).toBe("18 bridges");
    const constraints = { no_left: false, no_right: true, strict: true };
    const strict = await open({ ...route, request: { ...route.request, constraints } });
    expect(strict.state.settings).toMatchObject({ turns: "no_right", strict: true });
    expect(encodeUrlState(urlStateOf(strict.state))).toContain("&turns=right&strict=1&");
  });
});

describe("the vehicle in the URL", () => {
  const shown = (patch: Partial<Settings>): AppState => ({
    region: null,
    stops: [],
    settings: { ...DEFAULT_SETTINGS, ...patch },
    run: IDLE_RUN,
    route: null,
    error: null,
  });

  it("round-trips a vehicle, and leaves the default out", () => {
    const state = { ...nothing, vehicle: { mpg: 31, gas_price_usd: 4.25 } };
    expect(encodeUrlState(state)).toBe("?vehicle=31,4.25");
    expect(decodeUrlState(encodeUrlState(state))).toEqual(state);
    for (const limits of ["5,0.5", "100,20"]) {
      expect(decodeUrlState(`?vehicle=${limits}`).vehicle).not.toBeNull();
    }
    expect(urlStateOf(shown({})).vehicle).toBeNull();
    const priced = shown({ gasPriceUsd: 4.25 });
    expect(encodeUrlState(urlStateOf(priced))).toContain("&vehicle=24,4.25");
    expect(
      settingsFrom(decodeUrlState(encodeUrlState(urlStateOf(priced))), DEFAULT_SETTINGS),
    ).toEqual(priced.settings);
  });

  it("ignores what it cannot use", () => {
    for (const vehicle of [
      "4,3",
      "101,3",
      "24,0.4",
      "24,21",
      "24",
      "24,3.89,1",
      "abc,3",
      ",3.89",
      "",
    ]) {
      expect(decodeUrlState(`?vehicle=${vehicle}`).vehicle).toBeNull();
    }
    expect(settingsFrom(decodeUrlState("?vehicle=4,3"), DEFAULT_SETTINGS)).toEqual(
      DEFAULT_SETTINGS,
    );
  });
});

describe("a route's vehicle opened by its link", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function open(route: RouteDocument): Promise<Store> {
    stubFetch((call) =>
      call.url.endsWith("/geojson")
        ? json({ type: "FeatureCollection", features: [] })
        : json(route),
    );
    const store = new Store({
      region: null,
      stops: [],
      settings: { ...DEFAULT_SETTINGS, mpg: 40, gasPriceUsd: 5 },
      run: IDLE_RUN,
      route: null,
      error: null,
    });
    expect(await openRoute(route.route_id, store, new JobRunner(store, fakeView(), () => {}))).toBe(
      true,
    );
    return store;
  }

  it("takes the stored request's vehicle into the settings and the URL", async () => {
    const { route } = loadStream("portland-directions-stream");
    const vehicle = { mpg: 31, gas_price_usd: 4.25 };
    const store = await open({ ...route, request: { ...route.request, vehicle } });
    expect(store.state.settings).toMatchObject({ mpg: 31, gasPriceUsd: 4.25 });
    expect(encodeUrlState(urlStateOf(store.state))).toContain("&vehicle=31,4.25&");
    const recorded = await open(route); // 24 mpg at $3.89, the default: left out of the URL
    expect(recorded.state.settings).toMatchObject({ mpg: 24, gasPriceUsd: 3.89 });
    expect(encodeUrlState(urlStateOf(recorded.state))).not.toContain("vehicle=");
  });

  it("keeps the panel's vehicle for a route whose request has none, or one it does not take", async () => {
    const store = await open(loadStream("portland-time-stream").route);
    expect(store.state.settings).toMatchObject({ mpg: 40, gasPriceUsd: 5 });
    const { route } = loadStream("portland-directions-stream");
    for (const vehicle of [
      { mpg: 3, gas_price_usd: 3.89 },
      { mpg: 24, gas_price_usd: 0.1 },
    ]) {
      const scripted = await open({ ...route, request: { ...route.request, vehicle } });
      expect(scripted.state.settings).toMatchObject({ mpg: 40, gasPriceUsd: 5 });
      expect(encodeUrlState(urlStateOf(scripted.state))).toContain("&vehicle=40,5&");
    }
  });
});

describe("the departure in the URL and in a route's link", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("round-trips a departure with its offset, and ignores what is not one", () => {
    for (const departure of [
      "2026-10-31T09:00:00-07:00",
      "2026-10-31T09:00+05:30",
      "2026-11-01T02:00:00Z",
    ]) {
      const state = { ...nothing, departure };
      expect(decodeUrlState(encodeUrlState(state))).toEqual(state);
    }
    expect(encodeUrlState({ ...nothing, departure: "2026-10-31T09:00:00+05:30" })).toBe(
      "?departure=2026-10-31T09%3A00%3A00%2B05%3A30",
    );
    for (const text of ["2026-10-31T09:00", "2026-10-31", "tomorrow", "2026-13-31T09:00:00Z", ""]) {
      expect(decodeUrlState(`?departure=${encodeURIComponent(text)}`).departure).toBeNull();
    }
    const url = decodeUrlState("?departure=2026-10-31T09%3A00%3A00-07%3A00");
    expect(settingsFrom(url, DEFAULT_SETTINGS).departure).toBe("2026-10-31T09:00:00-07:00");
    expect(urlStateOf({ ...emptyApp(), settings: DEFAULT_SETTINGS }).departure).toBeNull(); // now
  });

  it("takes a stored request's departure into the settings, and keeps the panel's without one", async () => {
    const { route } = loadStream("portland-directions-stream");
    const open = async (departure: string | null) => {
      stubFetch((call) =>
        call.url.endsWith("/geojson")
          ? json({ type: "FeatureCollection", features: [] })
          : json({ ...route, request: { ...route.request, departure } }),
      );
      const store = new Store({
        ...emptyApp(),
        settings: { ...DEFAULT_SETTINGS, departure: "2026-11-02T08:00:00-08:00" },
      });
      expect(
        await openRoute(route.route_id, store, new JobRunner(store, fakeView(), () => {})),
      ).toBe(true);
      return store;
    };
    const stored = await open("2026-10-31T09:00:00-07:00");
    expect(stored.state.settings.departure).toBe("2026-10-31T09:00:00-07:00");
    expect(encodeUrlState(urlStateOf(stored.state))).toContain(
      "&departure=2026-10-31T09%3A00%3A00-07%3A00&",
    );
    const none = await open(null);
    expect(none.state.settings.departure).toBe("2026-11-02T08:00:00-08:00");
  });

  it("downloads the route's GeoJSON from the API, through the /api proxy", () => {
    expect(geojsonUrl("5ea844bffd40")).toBe("/api/routes/5ea844bffd40/geojson");
  });
});

/** An app with nothing in it. */
function emptyApp(): AppState {
  return {
    region: null,
    stops: [],
    settings: DEFAULT_SETTINGS,
    run: IDLE_RUN,
    route: null,
    error: null,
  };
}
